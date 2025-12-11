"""CLI activity marker for TUI visual feedback.

When CLI modifies a task, it writes an activity marker file that TUI reads
to provide visual indication of which task was just modified.
"""

import json
import time
from pathlib import Path
from typing import Optional, Dict, Any

# Activity marker file location (relative to tasks directory)
ACTIVITY_FILE = ".cli_activity.json"

# How long the activity indicator stays visible (seconds)
ACTIVITY_TTL = 5.0


def _get_activity_path(tasks_dir: Optional[Path] = None) -> Path:
    """Get the path to the activity marker file."""
    base = tasks_dir or Path(".tasks")
    return base / ACTIVITY_FILE


def write_activity_marker(
    task_id: str,
    command: str,
    task_file: Optional[str] = None,
    subtask_path: Optional[str] = None,
    tasks_dir: Optional[Path] = None,
) -> bool:
    """Write CLI activity marker for TUI to display.

    Args:
        task_id: The task ID being modified
        command: The CLI command name (e.g., "checkpoint", "status-set")
        task_file: Optional path to the task file
        subtask_path: Optional subtask path (e.g., "0.1.2")
        tasks_dir: Optional tasks directory path

    Returns:
        True if marker was written successfully
    """
    marker = {
        "timestamp": time.time(),
        "task_id": task_id,
        "command": command,
        "task_file": task_file,
        "subtask_path": subtask_path,
    }
    try:
        path = _get_activity_path(tasks_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(marker, ensure_ascii=False), encoding="utf-8")
        return True
    except Exception:
        return False


def read_activity_marker(tasks_dir: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Read CLI activity marker if it exists and is recent.

    Args:
        tasks_dir: Optional tasks directory path

    Returns:
        Activity marker dict if valid and recent, None otherwise
    """
    try:
        path = _get_activity_path(tasks_dir)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        # Check if marker is still valid (within TTL)
        if time.time() - data.get("timestamp", 0) > ACTIVITY_TTL:
            return None
        return data
    except Exception:
        return None


def clear_activity_marker(tasks_dir: Optional[Path] = None) -> None:
    """Clear the activity marker file."""
    try:
        path = _get_activity_path(tasks_dir)
        if path.exists():
            path.unlink()
    except Exception:
        pass


__all__ = ["write_activity_marker", "read_activity_marker", "clear_activity_marker", "ACTIVITY_TTL"]
