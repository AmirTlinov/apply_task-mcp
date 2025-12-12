//! Task-related Tauri commands
//!
//! These commands are invoked from the React frontend via Tauri's invoke API.

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tauri::State;

use crate::AppState;

/// Task list response
#[derive(Debug, Serialize, Deserialize)]
pub struct TaskListResponse {
    pub success: bool,
    pub tasks: Vec<Value>,
    pub total: usize,
    pub error: Option<String>,
}

/// Task detail response
#[derive(Debug, Serialize, Deserialize)]
pub struct TaskResponse {
    pub success: bool,
    pub task: Option<Value>,
    pub error: Option<String>,
}

/// AI Intent response
#[derive(Debug, Serialize, Deserialize)]
pub struct AIResponse {
    pub success: bool,
    pub intent: String,
    pub result: Option<Value>,
    pub suggestions: Option<Vec<String>>,
    pub error: Option<String>,
}

/// Get task list
#[tauri::command]
pub async fn tasks_list(
    state: State<'_, AppState>,
    domain: Option<String>,
    status: Option<String>,
    compact: Option<bool>,
    namespace: Option<String>,
    all_namespaces: Option<bool>,
) -> Result<TaskListResponse, String> {
    let bridge = state.bridge.lock().await;

    let params = json!({
        "domain": domain,
        "status": status,
        "compact": compact.unwrap_or(true),
        "namespace": namespace,
        "all_namespaces": all_namespaces.unwrap_or(false)
    });

    match bridge.invoke("tasks_list", Some(params)).await {
        Ok(result) => {
            let tasks = result
                .get("tasks")
                .and_then(|t| t.as_array())
                .cloned()
                .unwrap_or_default();
            let total = tasks.len();

            Ok(TaskListResponse {
                success: true,
                tasks,
                total,
                error: None,
            })
        }
        Err(e) => Ok(TaskListResponse {
            success: false,
            tasks: vec![],
            total: 0,
            error: Some(e.to_string()),
        }),
    }
}

/// Get task details
#[tauri::command]
pub async fn tasks_show(
    state: State<'_, AppState>,
    task_id: String,
    domain: Option<String>,
    namespace: Option<String>,
) -> Result<TaskResponse, String> {
    let bridge = state.bridge.lock().await;

    let params = json!({
        "task": task_id,
        "domain": domain,
        "namespace": namespace
    });

    log::info!(
        "tasks_show called with task_id: {}, namespace: {:?}",
        task_id,
        namespace
    );

    match bridge.invoke("tasks_show", Some(params)).await {
        Ok(result) => {
            log::info!("tasks_show raw result: {}", result);
            // MCP returns {success: true, task: {...}, domain: ""}
            // Extract only the task field for frontend
            let task = result.get("task").cloned();
            let has_task = task.is_some();
            log::info!(
                "tasks_show extracted task: has_task={}, task={:?}",
                has_task,
                task.as_ref().map(|t| t.get("id"))
            );
            Ok(TaskResponse {
                success: has_task,
                task,
                error: if !has_task {
                    Some("Task not found in response".to_string())
                } else {
                    None
                },
            })
        }
        Err(e) => {
            log::error!("tasks_show error: {}", e);
            Ok(TaskResponse {
                success: false,
                task: None,
                error: Some(e.to_string()),
            })
        }
    }
}

/// Get current context (for AI session start)
#[tauri::command]
pub async fn tasks_context(
    state: State<'_, AppState>,
    task: Option<String>,
    include_all: Option<bool>,
) -> Result<AIResponse, String> {
    let bridge = state.bridge.lock().await;

    let params = json!({
        "task": task,
        "include_all": include_all.unwrap_or(false)
    });

    match bridge.invoke("tasks_context", Some(params)).await {
        Ok(result) => Ok(AIResponse {
            success: true,
            intent: "context".to_string(),
            result: Some(result),
            suggestions: None,
            error: None,
        }),
        Err(e) => Ok(AIResponse {
            success: false,
            intent: "context".to_string(),
            result: None,
            suggestions: None,
            error: Some(e.to_string()),
        }),
    }
}

/// Execute AI intent (maps intent name to MCP tool)
#[tauri::command]
pub async fn ai_intent(
    state: State<'_, AppState>,
    intent: String,
    params: Option<Value>,
) -> Result<AIResponse, String> {
    let bridge = state.bridge.lock().await;

    // Map intent names to MCP tool names
    let tool_name = match intent.as_str() {
        "context" => "tasks_context",
        "create" => "tasks_create",
        "decompose" => "tasks_decompose",
        "define" => "tasks_define",
        "verify" => "tasks_verify",
        "progress" => "tasks_progress",
        "done" => "tasks_done",
        "complete" => "tasks_complete",
        "delete" => "tasks_delete",
        "storage" => "tasks_storage",
        "undo" => "tasks_undo",
        "redo" => "tasks_redo",
        "history" => "tasks_history",
        "next" => "tasks_next",
        "suggest" => "tasks_macro_suggest",
        _ => {
            return Ok(AIResponse {
                success: false,
                intent: intent.clone(),
                result: None,
                suggestions: None,
                error: Some(format!("Unknown intent: {}", intent)),
            })
        }
    };

    let request_params = params.unwrap_or(json!({}));

    match bridge.invoke(tool_name, Some(request_params)).await {
        Ok(result) => {
            let suggestions = result
                .get("suggestions")
                .and_then(|s| s.as_array())
                .map(|arr| {
                    arr.iter()
                        .filter_map(|v| v.as_str().map(String::from))
                        .collect()
                });

            Ok(AIResponse {
                success: result
                    .get("success")
                    .and_then(|v| v.as_bool())
                    .unwrap_or(true),
                intent,
                result: Some(result),
                suggestions,
                error: None,
            })
        }
        Err(e) => Ok(AIResponse {
            success: false,
            intent,
            result: None,
            suggestions: None,
            error: Some(e.to_string()),
        }),
    }
}

/// Create a new task
#[tauri::command]
pub async fn tasks_create(
    state: State<'_, AppState>,
    title: String,
    parent: Option<String>,
    description: Option<String>,
    priority: Option<String>,
    tags: Option<Vec<String>>,
    subtasks: Option<Vec<Value>>,
    domain: Option<String>,
    phase: Option<String>,
    component: Option<String>,
    context: Option<String>,
    namespace: Option<String>,
) -> Result<AIResponse, String> {
    let bridge = state.bridge.lock().await;

    let params = json!({
        "title": title,
        "parent": parent,
        "description": description.unwrap_or_default(),
        "priority": priority.unwrap_or_else(|| "MEDIUM".to_string()),
        "tags": tags.unwrap_or_default(),
        "subtasks": subtasks.unwrap_or_default(),
        "domain": domain.unwrap_or_default(),
        "phase": phase.unwrap_or_default(),
        "component": component.unwrap_or_default(),
        "context": context.unwrap_or_default(),
        "namespace": namespace.unwrap_or_default()
    });

    match bridge.invoke("tasks_create", Some(params)).await {
        Ok(result) => Ok(AIResponse {
            success: result
                .get("success")
                .and_then(|v| v.as_bool())
                .unwrap_or(true),
            intent: "create".to_string(),
            result: Some(result),
            suggestions: None,
            error: None,
        }),
        Err(e) => Ok(AIResponse {
            success: false,
            intent: "create".to_string(),
            result: None,
            suggestions: None,
            error: Some(e.to_string()),
        }),
    }
}

/// Update task status
#[tauri::command]
pub async fn tasks_update_status(
    state: State<'_, AppState>,
    task_id: String,
    status: String,
    domain: Option<String>,
    namespace: Option<String>,
) -> Result<AIResponse, String> {
    let bridge = state.bridge.lock().await;

    let params = json!({
        "task": task_id,
        "status": status,
        "domain": domain.unwrap_or_default(),
        "namespace": namespace.unwrap_or_default()
    });

    match bridge.invoke("tasks_macro_update", Some(params)).await {
        Ok(result) => Ok(AIResponse {
            success: true,
            intent: "update".to_string(),
            result: Some(result),
            suggestions: None,
            error: None,
        }),
        Err(e) => Ok(AIResponse {
            success: false,
            intent: "update".to_string(),
            result: None,
            suggestions: None,
            error: Some(e.to_string()),
        }),
    }
}

/// Complete subtask checkpoint
#[tauri::command]
pub async fn tasks_checkpoint(
    state: State<'_, AppState>,
    task_id: String,
    path: String,
    checkpoint: String,
    note: String,
    domain: Option<String>,
    namespace: Option<String>,
) -> Result<AIResponse, String> {
    let bridge = state.bridge.lock().await;

    let mut checkpoints = json!({});
    checkpoints[&checkpoint] = json!({
        "confirmed": true,
        "note": note
    });

    let params = json!({
        "task": task_id,
        "path": path,
        "domain": domain.unwrap_or_default(),
        "namespace": namespace.unwrap_or_default(),
        "checkpoints": checkpoints
    });

    match bridge.invoke("tasks_verify", Some(params)).await {
        Ok(result) => Ok(AIResponse {
            success: result
                .get("success")
                .and_then(|v| v.as_bool())
                .unwrap_or(true),
            intent: "verify".to_string(),
            result: Some(result),
            suggestions: None,
            error: None,
        }),
        Err(e) => Ok(AIResponse {
            success: false,
            intent: "verify".to_string(),
            result: None,
            suggestions: None,
            error: Some(e.to_string()),
        }),
    }
}

/// Get storage info
#[tauri::command]
pub async fn tasks_storage(state: State<'_, AppState>) -> Result<AIResponse, String> {
    let bridge = state.bridge.lock().await;

    match bridge.invoke("tasks_storage", None).await {
        Ok(result) => Ok(AIResponse {
            success: true,
            intent: "storage".to_string(),
            result: Some(result),
            suggestions: None,
            error: None,
        }),
        Err(e) => Ok(AIResponse {
            success: false,
            intent: "storage".to_string(),
            result: None,
            suggestions: None,
            error: Some(e.to_string()),
        }),
    }
}

/// Get AI session status (plan/current op/history)
#[tauri::command]
pub async fn tasks_ai_status(state: State<'_, AppState>) -> Result<AIResponse, String> {
    let bridge = state.bridge.lock().await;

    match bridge.invoke("tasks_ai_status", None).await {
        Ok(result) => Ok(AIResponse {
            success: true,
            intent: "ai_status".to_string(),
            result: Some(result),
            suggestions: None,
            error: None,
        }),
        Err(e) => Ok(AIResponse {
            success: false,
            intent: "ai_status".to_string(),
            result: None,
            suggestions: None,
            error: Some(e.to_string()),
        }),
    }
}

/// Generate flagship subtasks template
#[tauri::command]
pub async fn tasks_template_subtasks(
    state: State<'_, AppState>,
    count: Option<u32>,
) -> Result<AIResponse, String> {
    let bridge = state.bridge.lock().await;

    let params = json!({
        "count": count.unwrap_or(3)
    });

    match bridge.invoke("tasks_template_subtasks", Some(params)).await {
        Ok(result) => Ok(AIResponse {
            success: result
                .get("success")
                .and_then(|v| v.as_bool())
                .unwrap_or(true),
            intent: "template_subtasks".to_string(),
            result: Some(result),
            suggestions: None,
            error: None,
        }),
        Err(e) => Ok(AIResponse {
            success: false,
            intent: "template_subtasks".to_string(),
            result: None,
            suggestions: None,
            error: Some(e.to_string()),
        }),
    }
}

/// Send a user signal to AI (pause/resume/stop/skip/message)
#[tauri::command]
pub async fn tasks_send_signal(
    state: State<'_, AppState>,
    signal: String,
    message: Option<String>,
) -> Result<AIResponse, String> {
    let bridge = state.bridge.lock().await;

    let params = json!({
        "signal": signal,
        "message": message.unwrap_or_default()
    });

    match bridge.invoke("tasks_send_signal", Some(params)).await {
        Ok(result) => Ok(AIResponse {
            success: result
                .get("success")
                .and_then(|v| v.as_bool())
                .unwrap_or(true),
            intent: "send_signal".to_string(),
            result: Some(result),
            suggestions: None,
            error: None,
        }),
        Err(e) => Ok(AIResponse {
            success: false,
            intent: "send_signal".to_string(),
            result: None,
            suggestions: None,
            error: Some(e.to_string()),
        }),
    }
}

/// Delete a task
#[tauri::command]
pub async fn tasks_delete(
    state: State<'_, AppState>,
    task_id: String,
    domain: Option<String>,
    namespace: Option<String>,
) -> Result<AIResponse, String> {
    let bridge = state.bridge.lock().await;

    let params = json!({
        "task": task_id,
        "domain": domain.unwrap_or_default(),
        "namespace": namespace.unwrap_or_default()
    });

    match bridge.invoke("tasks_delete", Some(params)).await {
        Ok(result) => Ok(AIResponse {
            success: result
                .get("success")
                .and_then(|v| v.as_bool())
                .unwrap_or(true),
            intent: "delete".to_string(),
            result: Some(result),
            suggestions: None,
            error: None,
        }),
        Err(e) => Ok(AIResponse {
            success: false,
            intent: "delete".to_string(),
            result: None,
            suggestions: None,
            error: Some(e.to_string()),
        }),
    }
}
