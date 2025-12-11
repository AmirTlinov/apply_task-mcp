#!/usr/bin/env python3
"""
tasks.py — flagship task manager (single-file CLI/TUI).

All tasks live under .tasks/ (one .task file per task).

This is now a thin facade that delegates to specialized modules.
"""

import argparse
import sys
from pathlib import Path

from core.desktop.devtools.interface.cli_parser import build_parser as build_cli_parser
from core.desktop.devtools.interface.constants import AI_HELP
from importlib.metadata import version as pkg_version, PackageNotFoundError
from core.desktop.devtools.interface.cli_automation import AUTOMATION_TMP

# Import all command functions
from .cli_commands_core import (
    cmd_list,
    cmd_show,
    cmd_create,
    cmd_smart_create,
    cmd_create_guided,
    cmd_status_set,
    cmd_analyze,
    cmd_next,
    cmd_add_subtask,
    cmd_add_dependency,
    cmd_subtask,
    cmd_bulk,
    cmd_checkpoint,
    cmd_move,
    cmd_clean,
    cmd_edit,
    cmd_lint,
)
from .cli_projects import (
    cmd_projects_auth,
    cmd_projects_webhook,
    cmd_projects_webhook_serve,
    cmd_projects_sync_cli,
    cmd_projects_status,
    cmd_projects_autosync,
    cmd_projects_workers,
)
from .cli_automation import (
    cmd_automation_task_template,
    cmd_automation_task_create,
    cmd_automation_projects_health,
    cmd_automation_health,
    cmd_automation_checkpoint,
)
from .cli_templates import cmd_template_subtasks
from .cli_ai import cmd_ai
from .mcp_server import run_stdio_server as _mcp_run
from .tui_app import cmd_tui, TaskTrackerTUI
from .tui_themes import THEMES, DEFAULT_THEME
from .tui_models import Task, CLI_DEPS, CHECKLIST_SECTIONS, InteractiveFormattedTextControl
from core.desktop.devtools.interface.cli_macros_extended import cmd_update, cmd_ok, cmd_note, cmd_suggest, cmd_quick

# Additional exports for backward compatibility and tests
from core import Status, SubTask, TaskDetail
from core.desktop.devtools.application.task_manager import TaskManager
from core.desktop.devtools.application.context import (
    derive_domain_explicit,
    derive_folder_explicit,
    save_last_task,
    get_last_task,
    resolve_task_reference,
    normalize_task_id,
)
from util.responsive import ResponsiveLayoutManager
from .cli_automation import _automation_template_payload
from infrastructure.task_file_parser import TaskFileParser
from projects_sync import get_projects_sync
from concurrent.futures import ThreadPoolExecutor, as_completed
from config import get_user_token
from .projects_integration import validate_pat_token_http

# Re-export for backward compatibility
__all__ = [
    # Commands
    "cmd_list",
    "cmd_show",
    "cmd_create",
    "cmd_smart_create",
    "cmd_create_guided",
    "cmd_status_set",
    "cmd_analyze",
    "cmd_next",
    "cmd_add_subtask",
    "cmd_add_dependency",
    "cmd_subtask",
    "cmd_bulk",
    "cmd_checkpoint",
    "cmd_move",
    "cmd_clean",
    "cmd_edit",
    "cmd_lint",
    "cmd_projects_auth",
    "cmd_projects_webhook",
    "cmd_projects_webhook_serve",
    "cmd_projects_sync_cli",
    "cmd_projects_status",
    "cmd_projects_autosync",
    "cmd_projects_workers",
    "cmd_automation_task_template",
    "cmd_automation_task_create",
    "cmd_automation_projects_health",
    "cmd_automation_health",
    "cmd_automation_checkpoint",
    "cmd_template_subtasks",
    "cmd_ai",
    "cmd_mcp",
    "cmd_gui",
    "cmd_tui",
    "cmd_update",
    "cmd_ok",
    "cmd_note",
    "cmd_suggest",
    "cmd_quick",
    # Models and constants
    "Task",
    "TaskTrackerTUI",
    "CLI_DEPS",
    "CHECKLIST_SECTIONS",
    "InteractiveFormattedTextControl",
    "THEMES",
    "DEFAULT_THEME",
    "AUTOMATION_TMP",
    # Additional exports for backward compatibility
    "Status",
    "SubTask",
    "TaskDetail",
    "TaskManager",
    "derive_domain_explicit",
    "derive_folder_explicit",
    "save_last_task",
    "get_last_task",
    "resolve_task_reference",
    "normalize_task_id",
    "ResponsiveLayoutManager",
    "_automation_template_payload",
    "TaskFileParser",
    "get_projects_sync",
    "ThreadPoolExecutor",
    "as_completed",
    "get_user_token",
    "validate_pat_token_http",
]


def cmd_mcp(args) -> int:
    """Запустить MCP stdio сервер."""
    tasks_dir = Path(args.tasks_dir) if getattr(args, "tasks_dir", None) else None
    use_global = not getattr(args, "local", False)
    _mcp_run(tasks_dir=tasks_dir, use_global=use_global)
    return 0


def cmd_gui(args) -> int:
    """Запустить GUI приложение (Tauri)."""
    import subprocess
    import shutil

    # GUI directory relative to this file
    gui_dir = Path(__file__).resolve().parent.parent.parent.parent.parent / "gui"

    if not gui_dir.exists():
        print(f"Error: GUI directory not found at {gui_dir}", file=sys.stderr)
        return 1

    dev_mode = getattr(args, "dev", False)

    if dev_mode:
        # Development mode: run with hot-reload
        pnpm = shutil.which("pnpm")
        if not pnpm:
            print("Error: pnpm not found. Install with: npm install -g pnpm", file=sys.stderr)
            return 1
        cmd = [pnpm, "tauri", "dev"]
        print(f"Starting GUI in development mode...")
        result = subprocess.run(cmd, cwd=gui_dir)
        return result.returncode
    else:
        # Production mode: try to find and run the built binary
        # Tauri builds to src-tauri/target/release/apply-task-gui (Linux)
        # or similar paths on other platforms
        import platform

        system = platform.system().lower()
        if system == "linux":
            binary_name = "apply-task-gui"
            binary_path = gui_dir / "src-tauri" / "target" / "release" / binary_name
        elif system == "darwin":
            binary_name = "apply-task-gui.app"
            binary_path = gui_dir / "src-tauri" / "target" / "release" / "bundle" / "macos" / binary_name
        elif system == "windows":
            binary_name = "apply-task-gui.exe"
            binary_path = gui_dir / "src-tauri" / "target" / "release" / binary_name
        else:
            print(f"Error: Unsupported platform: {system}", file=sys.stderr)
            return 1

        if not binary_path.exists():
            print(f"Error: Built GUI not found at {binary_path}", file=sys.stderr)
            print("Build with: cd gui && pnpm tauri build", file=sys.stderr)
            print("Or run in dev mode: apply_task gui --dev", file=sys.stderr)
            return 1

        print(f"Starting GUI...")
        if system == "darwin":
            # macOS: open the .app bundle
            result = subprocess.run(["open", str(binary_path)])
        else:
            result = subprocess.run([str(binary_path)])
        return result.returncode


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
    parser = build_cli_parser(commands=sys.modules[__name__], themes=THEMES, default_theme=DEFAULT_THEME, automation_tmp=AUTOMATION_TMP)
    parser.add_argument("--version", action="store_true", help="Show version and exit")
    return parser


def main() -> int:
    """Main entry point."""
    parser = build_parser()
    args = parser.parse_args()
    if getattr(args, "version", False):
        try:
            print(pkg_version("apply-task"))
        except PackageNotFoundError:
            print("0.0.0")
        return 0
    if not getattr(args, "command", None):
        parser.print_help()
        return 1
    if args.command == "help":
        parser.print_help()
        print("\nКонтекст: --domain или phase/component формируют путь; .last хранит TASK@domain.")
        print("\nПравила для ИИ-агентов:\n")
        print(AI_HELP.strip())
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
