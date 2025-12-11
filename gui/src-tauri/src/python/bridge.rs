//! Python subprocess bridge
//!
//! Manages a persistent Python subprocess for JSON-RPC communication.
//! Spawns `apply_task mcp` and communicates via stdio.

use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use anyhow::{anyhow, Context, Result};
use serde_json::Value;
use tokio::sync::Mutex;

use super::protocol::{JsonRpcRequest, JsonRpcResponse};

/// Python bridge for communicating with apply_task backend
pub struct PythonBridge {
    /// Python subprocess handle
    process: Arc<Mutex<Option<BridgeProcess>>>,
    /// Request ID counter
    request_id: AtomicU64,
    /// Apply_task package root (for finding Python scripts)
    apply_task_root: PathBuf,
    /// User's working directory (for project detection in Python)
    user_cwd: PathBuf,
    /// Python executable path
    python_path: String,
    /// Whether MCP is initialized
    initialized: Arc<Mutex<bool>>,
}

struct BridgeProcess {
    child: Child,
}

/// MCP initialization request/response
#[derive(serde::Serialize)]
struct McpInitializeParams {
    #[serde(rename = "protocolVersion")]
    protocol_version: String,
    capabilities: serde_json::Value,
    #[serde(rename = "clientInfo")]
    client_info: McpClientInfo,
}

#[derive(serde::Serialize)]
struct McpClientInfo {
    name: String,
    version: String,
}

/// MCP notification (no id, no response expected)
#[derive(serde::Serialize)]
struct McpNotification {
    jsonrpc: String,
    method: String,
}

/// MCP tools/call params
#[derive(serde::Serialize)]
struct McpToolCallParams {
    name: String,
    arguments: serde_json::Value,
}

impl PythonBridge {
    /// Create a new Python bridge
    ///
    /// # Arguments
    /// * `apply_task_root` - Path to apply_task package (for finding Python scripts)
    /// * `user_cwd` - User's working directory (for project detection in Python)
    pub fn new(apply_task_root: PathBuf, user_cwd: PathBuf) -> Self {
        // Try to find Python in common locations
        let python_path = std::env::var("PYTHON_PATH")
            .or_else(|_| std::env::var("APPLY_TASK_PYTHON"))
            .unwrap_or_else(|_| "python3".to_string());

        Self {
            process: Arc::new(Mutex::new(None)),
            request_id: AtomicU64::new(1),
            apply_task_root,
            user_cwd,
            python_path,
            initialized: Arc::new(Mutex::new(false)),
        }
    }

    /// Spawn the Python subprocess if not already running
    async fn ensure_process(&self) -> Result<()> {
        let mut guard = self.process.lock().await;

        if guard.is_some() {
            return Ok(());
        }

        log::info!("Spawning Python bridge subprocess...");
        log::info!("Apply task root: {:?}", self.apply_task_root);
        log::info!("User working directory: {:?}", self.user_cwd);

        // Find apply_task entry point
        let args = self.find_apply_task()?;
        log::info!("Found apply_task args: {:?}", args);

        // Build command based on what we found
        let mut cmd = if args.first().map(|s| s.as_str()) == Some("-m") {
            // Module mode: python3 -m module
            let mut c = Command::new(&self.python_path);
            c.args(&args);
            log::info!("Running: {} {:?}", self.python_path, args);
            c
        } else {
            // Script mode: /path/to/apply_task mcp
            // The script is executable, run it directly
            let executable = args.first().ok_or_else(|| anyhow!("No executable found"))?;
            let mut c = Command::new(executable);
            c.arg("mcp");
            log::info!("Running: {} mcp", executable);
            c
        };

        // Set PYTHONPATH to apply_task package root (for imports)
        cmd.env("PYTHONPATH", &self.apply_task_root);
        // CRITICAL: Run Python in user's working directory (for project detection)
        cmd.current_dir(&self.user_cwd);
        cmd.stdin(Stdio::piped());
        cmd.stdout(Stdio::piped());
        cmd.stderr(Stdio::piped());

        let child = cmd.spawn().context("Failed to spawn Python subprocess")?;

        let mut child = child; // Make mutable to take stderr
        if let Some(stderr) = child.stderr.take() {
            std::thread::spawn(move || {
                let reader = BufReader::new(stderr);
                for line in reader.lines() {
                    if let Ok(l) = line {
                        log::error!("[Python Bridge Stderr] {}", l);
                    }
                }
            });
        }

        log::info!("Python bridge started with PID: {}", child.id());
        *guard = Some(BridgeProcess { child });

        Ok(())
    }

    /// Find the apply_task entry point
    fn find_apply_task(&self) -> Result<Vec<String>> {
        // Check APPLY_TASK_PATH environment variable
        if let Ok(path) = std::env::var("APPLY_TASK_PATH") {
            let path = PathBuf::from(&path);
            if path.exists() {
                return Ok(vec![path.to_string_lossy().to_string()]);
            }
        }

        // Check if apply_task is in PATH (installed via pip/uv)
        if let Ok(output) = Command::new("which").arg("apply_task").output() {
            if output.status.success() {
                let path = String::from_utf8_lossy(&output.stdout).trim().to_string();
                if !path.is_empty() {
                    return Ok(vec![path]);
                }
            }
        }

        // Check tasks.py in apply_task root
        let tasks_py = self.apply_task_root.join("tasks.py");
        if tasks_py.exists() {
            return Ok(vec![tasks_py.to_string_lossy().to_string()]);
        }

        // Use Python module directly: python -m core.desktop.devtools.interface.mcp_server
        // This works if project root is in PYTHONPATH
        Ok(vec![
            "-m".to_string(),
            "core.desktop.devtools.interface.mcp_server".to_string(),
        ])
    }

    /// Initialize the MCP connection (handshake)
    async fn initialize_mcp(&self) -> Result<()> {
        {
            let initialized = self.initialized.lock().await;
            if *initialized {
                return Ok(());
            }
        }

        log::info!("Initializing MCP connection...");

        // Send initialize request
        let init_params = McpInitializeParams {
            protocol_version: "2024-11-05".to_string(),
            capabilities: serde_json::json!({}),
            client_info: McpClientInfo {
                name: "apply-task-gui".to_string(),
                version: "0.1.0".to_string(),
            },
        };

        let response = self
            .call_raw("initialize", Some(serde_json::to_value(init_params)?))
            .await?;

        if response.error.is_some() {
            return Err(anyhow!("MCP initialize failed: {:?}", response.error));
        }

        log::info!("MCP initialized, sending notifications/initialized...");

        // Send initialized notification (no response expected)
        {
            let mut guard = self.process.lock().await;
            let process = guard
                .as_mut()
                .ok_or_else(|| anyhow!("Process not running"))?;

            let notification = McpNotification {
                jsonrpc: "2.0".to_string(),
                method: "notifications/initialized".to_string(),
            };

            let stdin = process
                .child
                .stdin
                .as_mut()
                .ok_or_else(|| anyhow!("Failed to get stdin"))?;

            let notification_json = serde_json::to_string(&notification)?;
            writeln!(stdin, "{}", notification_json)?;
            stdin.flush()?;
        }

        *self.initialized.lock().await = true;
        log::info!("MCP connection fully initialized");

        Ok(())
    }

    /// Call an MCP tool by name
    pub async fn call_tool(&self, tool_name: &str, arguments: Value) -> Result<Value> {
        self.ensure_process().await?;
        self.initialize_mcp().await?;

        let params = McpToolCallParams {
            name: tool_name.to_string(),
            arguments,
        };

        let response = self
            .call_raw("tools/call", Some(serde_json::to_value(params)?))
            .await?;

        if let Some(error) = response.error {
            return Err(anyhow!("Tool call error {}: {}", error.code, error.message));
        }

        // Extract result from MCP content format
        if let Some(result) = response.result {
            // MCP returns { content: [{ type: "json", json: {...} }], isError: false }
            if let Some(content) = result.get("content").and_then(|c| c.as_array()) {
                if let Some(first) = content.first() {
                    if let Some(json) = first.get("json") {
                        return Ok(json.clone());
                    }
                    if let Some(text) = first.get("text").and_then(|t| t.as_str()) {
                        return serde_json::from_str(text)
                            .context("Failed to parse tool response text as JSON");
                    }
                }
            }
            return Ok(result);
        }

        Err(anyhow!("Empty tool response"))
    }

    /// Send a raw JSON-RPC request and wait for response (internal)
    async fn call_raw(&self, method: &str, params: Option<Value>) -> Result<JsonRpcResponse> {
        let id = self.request_id.fetch_add(1, Ordering::SeqCst);
        let request = JsonRpcRequest::new(id, method, params);

        log::info!("call_raw: method={}, id={}", method, id);

        let mut guard = self.process.lock().await;
        let process = guard
            .as_mut()
            .ok_or_else(|| anyhow!("Process not running"))?;

        // Write request to stdin
        let stdin = process
            .child
            .stdin
            .as_mut()
            .ok_or_else(|| anyhow!("Failed to get stdin"))?;

        let request_json = serde_json::to_string(&request)?;
        log::info!("Sending request: {}", request_json);

        writeln!(stdin, "{}", request_json)?;
        stdin.flush()?;
        log::info!("Request sent, waiting for response...");

        // Read response from stdout
        let stdout = process
            .child
            .stdout
            .as_mut()
            .ok_or_else(|| anyhow!("Failed to get stdout"))?;

        let mut reader = BufReader::new(stdout);
        let mut response_line = String::new();

        log::info!("Reading response line...");
        let bytes_read = reader.read_line(&mut response_line)?;
        log::info!("Read {} bytes: {}", bytes_read, response_line.trim());

        if response_line.is_empty() {
            // Check if process is still running
            if let Some(status) = process.child.try_wait()? {
                return Err(anyhow!("Python process exited with status: {:?}", status));
            }
            return Err(anyhow!("Empty response from Python"));
        }

        let response: JsonRpcResponse =
            serde_json::from_str(&response_line).context("Failed to parse JSON-RPC response")?;

        log::info!("Parsed response id={}", response.id);

        // Verify response ID matches
        if response.id != id {
            return Err(anyhow!(
                "Response ID mismatch: expected {}, got {}",
                id,
                response.id
            ));
        }

        Ok(response)
    }

    /// Public method to call MCP tools (main API for commands)
    pub async fn call(&self, tool_name: &str, params: Option<Value>) -> Result<Value> {
        self.call_tool(tool_name, params.unwrap_or(serde_json::json!({})))
            .await
    }

    /// Call a method with simplified error handling (deprecated, use call_tool)
    pub async fn invoke(&self, method: &str, params: Option<Value>) -> Result<Value> {
        // For backwards compatibility, try as tool call
        self.call_tool(method, params.unwrap_or(serde_json::json!({})))
            .await
    }

    /// Shutdown the Python subprocess
    pub async fn shutdown(&self) -> Result<()> {
        let mut guard = self.process.lock().await;

        if let Some(mut process) = guard.take() {
            log::info!("Shutting down Python bridge...");
            let _ = process.child.kill();
            let _ = process.child.wait();
        }

        Ok(())
    }

    /// Check if the bridge is running
    pub async fn is_running(&self) -> bool {
        self.process.lock().await.is_some()
    }
}

impl Drop for BridgeProcess {
    fn drop(&mut self) {
        let _ = self.child.kill();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::env;

    #[tokio::test]
    async fn test_bridge_creation() {
        let cwd = env::current_dir().unwrap();
        let bridge = PythonBridge::new(cwd.clone(), cwd);
        assert!(!bridge.is_running().await);
    }
}
