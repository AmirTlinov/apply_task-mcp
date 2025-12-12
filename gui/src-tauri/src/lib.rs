//! Apply Task GUI - Tauri Backend
//!
//! Desktop GUI for apply_task using Tauri 2.0 + React 19.
//! Communicates with Python backend via JSON-RPC 2.0.

mod commands;
mod python;

use std::env;
use std::path::PathBuf;
use std::sync::Arc;

use tokio::sync::Mutex;

use python::PythonBridge;

/// Application state shared across all commands
pub struct AppState {
    pub bridge: Arc<Mutex<PythonBridge>>,
    /// Path to apply_task package (for finding Python scripts)
    pub apply_task_root: PathBuf,
    /// User's working directory when GUI was launched (for project detection)
    pub user_cwd: PathBuf,
}

/// Get apply_task package root (where Python scripts are located)
fn get_apply_task_root() -> PathBuf {
    // 1. Check explicit environment variable
    if let Ok(path) = env::var("APPLY_TASK_PROJECT_ROOT") {
        let path = PathBuf::from(path);
        if path.exists() {
            return path;
        }
    }

    // 2. Use executable path to find apply_task root
    //    Binary location: apply_task/gui/src-tauri/target/debug/apply-task-gui
    //    Need to go up: debug -> target -> src-tauri -> gui -> apply_task
    if let Ok(exe_path) = env::current_exe() {
        if let Some(exe_dir) = exe_path.parent() {
            // Check if we're in target/debug or target/release
            let path_str = exe_dir.to_string_lossy();
            if path_str.contains("target/debug") || path_str.contains("target/release") {
                // Navigate up: debug/release -> target -> src-tauri -> gui -> apply_task
                if let Some(target_dir) = exe_dir.parent() {
                    if let Some(src_tauri_dir) = target_dir.parent() {
                        if let Some(gui_dir) = src_tauri_dir.parent() {
                            if let Some(apply_task_root) = gui_dir.parent() {
                                if apply_task_root.join("core").exists()
                                    || apply_task_root.join("tasks.py").exists()
                                {
                                    return apply_task_root.to_path_buf();
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    // 3. Navigate up from current working directory
    if let Ok(current) = env::current_dir() {
        // Check if we're in src-tauri
        if current.ends_with("src-tauri") {
            if let Some(gui_dir) = current.parent() {
                if let Some(project_root) = gui_dir.parent() {
                    if project_root.join("core").exists() || project_root.join("tasks.py").exists()
                    {
                        return project_root.to_path_buf();
                    }
                }
            }
        }

        // Check if we're in gui/
        if current.ends_with("gui") {
            if let Some(project_root) = current.parent() {
                if project_root.join("core").exists() || project_root.join("tasks.py").exists() {
                    return project_root.to_path_buf();
                }
            }
        }

        // Check if current dir is the project root
        if current.join("core").exists() || current.join("tasks.py").exists() {
            return current;
        }
    }

    // 4. Fallback to current directory
    env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // Initialize logging
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();

    log::info!("Starting Apply Task GUI...");

    // Capture user's working directory FIRST (before any directory changes)
    let user_cwd = env::var("APPLY_TASK_USER_CWD")
        .map(PathBuf::from)
        .unwrap_or_else(|_| env::current_dir().unwrap_or_else(|_| PathBuf::from(".")));

    let apply_task_root = get_apply_task_root();

    log::info!("Apply task root: {:?}", apply_task_root);
    log::info!("User working directory: {:?}", user_cwd);

    let bridge = PythonBridge::new(apply_task_root.clone(), user_cwd.clone());
    let state = AppState {
        bridge: Arc::new(Mutex::new(bridge)),
        apply_task_root,
        user_cwd,
    };

    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_shell::init())
        .manage(state)
        .invoke_handler(tauri::generate_handler![
            commands::tasks_list,
            commands::tasks_show,
            commands::tasks_context,
            commands::tasks_create,
            commands::tasks_update_status,
            commands::tasks_checkpoint,
            commands::tasks_ai_status,
            commands::tasks_template_subtasks,
            commands::tasks_send_signal,
            commands::tasks_storage,
            commands::tasks_delete,
            commands::ai_intent,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
