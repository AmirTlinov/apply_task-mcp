"""Unit tests for CLI activity marker functionality."""

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.desktop.devtools.interface.cli_activity import (
    write_activity_marker,
    read_activity_marker,
    clear_activity_marker,
    ACTIVITY_TTL,
)


def test_write_activity_marker_creates_file(tmp_path):
    """Test that write_activity_marker creates a marker file."""
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()

    result = write_activity_marker(
        task_id="TASK-001",
        command="checkpoint",
        subtask_path="0.1",
        tasks_dir=tasks_dir,
    )

    assert result is True
    marker_file = tasks_dir / ".cli_activity.json"
    assert marker_file.exists()

    data = json.loads(marker_file.read_text())
    assert data["task_id"] == "TASK-001"
    assert data["command"] == "checkpoint"
    assert data["subtask_path"] == "0.1"
    assert "timestamp" in data


def test_read_activity_marker_returns_recent_marker(tmp_path):
    """Test that read_activity_marker returns recent markers."""
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()

    write_activity_marker(
        task_id="TASK-002",
        command="subtask-done",
        tasks_dir=tasks_dir,
    )

    marker = read_activity_marker(tasks_dir)
    assert marker is not None
    assert marker["task_id"] == "TASK-002"
    assert marker["command"] == "subtask-done"


def test_read_activity_marker_returns_none_for_expired(tmp_path):
    """Test that read_activity_marker returns None for expired markers."""
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()

    # Create marker with old timestamp
    marker_file = tasks_dir / ".cli_activity.json"
    old_marker = {
        "timestamp": time.time() - ACTIVITY_TTL - 1,
        "task_id": "OLD-TASK",
        "command": "old-command",
    }
    marker_file.write_text(json.dumps(old_marker))

    marker = read_activity_marker(tasks_dir)
    assert marker is None


def test_read_activity_marker_returns_none_for_missing_file(tmp_path):
    """Test that read_activity_marker returns None when file doesn't exist."""
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()

    marker = read_activity_marker(tasks_dir)
    assert marker is None


def test_clear_activity_marker_removes_file(tmp_path):
    """Test that clear_activity_marker removes the marker file."""
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()

    write_activity_marker(task_id="TASK-003", command="test", tasks_dir=tasks_dir)
    marker_file = tasks_dir / ".cli_activity.json"
    assert marker_file.exists()

    clear_activity_marker(tasks_dir)
    assert not marker_file.exists()


def test_write_activity_marker_handles_none_tasks_dir():
    """Test that write_activity_marker handles None tasks_dir gracefully."""
    # Should not raise, just return False or True depending on default path
    result = write_activity_marker(
        task_id="TASK-004",
        command="test",
        tasks_dir=None,
    )
    # Result depends on whether .tasks exists in cwd
    assert isinstance(result, bool)


def test_read_activity_marker_handles_none_tasks_dir():
    """Test that read_activity_marker handles None tasks_dir gracefully."""
    # Should not raise
    result = read_activity_marker(None)
    # Result depends on whether .tasks exists in cwd
    assert result is None or isinstance(result, dict)


def test_write_activity_marker_creates_parent_dir(tmp_path):
    """Test that write_activity_marker creates parent directory if needed."""
    tasks_dir = tmp_path / "new_tasks_dir"
    # Directory doesn't exist yet

    result = write_activity_marker(
        task_id="TASK-005",
        command="create",
        tasks_dir=tasks_dir,
    )

    assert result is True
    assert tasks_dir.exists()


def test_activity_marker_with_task_file(tmp_path):
    """Test that activity marker includes task_file when provided."""
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()

    write_activity_marker(
        task_id="TASK-006",
        command="status-set",
        task_file="TASK-006.task",
        tasks_dir=tasks_dir,
    )

    marker = read_activity_marker(tasks_dir)
    assert marker["task_file"] == "TASK-006.task"
