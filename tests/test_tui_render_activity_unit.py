"""Unit tests for TUI rendering with CLI activity indicators."""

import time
from types import SimpleNamespace

import pytest

from core.desktop.devtools.interface.tui_render import (
    _is_task_cli_active,
    _is_subtask_cli_active,
)


def test_is_task_cli_active_returns_true_for_matching_task():
    """Test that _is_task_cli_active returns True for the active task."""
    tui = SimpleNamespace(
        _cli_activity_task_id="TASK-001",
        _cli_activity_expires=time.time() + 10,
    )
    task = SimpleNamespace(id="TASK-001")

    assert _is_task_cli_active(tui, task) is True


def test_is_task_cli_active_returns_false_for_different_task():
    """Test that _is_task_cli_active returns False for different task."""
    tui = SimpleNamespace(
        _cli_activity_task_id="TASK-001",
        _cli_activity_expires=time.time() + 10,
    )
    task = SimpleNamespace(id="TASK-002")

    assert _is_task_cli_active(tui, task) is False


def test_is_task_cli_active_returns_false_when_expired():
    """Test that _is_task_cli_active returns False when activity expired."""
    tui = SimpleNamespace(
        _cli_activity_task_id="TASK-001",
        _cli_activity_expires=time.time() - 1,  # Expired
    )
    task = SimpleNamespace(id="TASK-001")

    assert _is_task_cli_active(tui, task) is False


def test_is_task_cli_active_returns_false_when_no_activity():
    """Test that _is_task_cli_active returns False when no activity."""
    tui = SimpleNamespace(
        _cli_activity_task_id=None,
        _cli_activity_expires=0,
    )
    task = SimpleNamespace(id="TASK-001")

    assert _is_task_cli_active(tui, task) is False


def test_is_subtask_cli_active_returns_true_for_matching_path():
    """Test that _is_subtask_cli_active returns True for matching path."""
    tui = SimpleNamespace(
        _cli_activity_task_id="TASK-001",
        _cli_activity_subtask_path="0.1",
        _cli_activity_expires=time.time() + 10,
        current_task_detail=SimpleNamespace(id="TASK-001"),
    )

    assert _is_subtask_cli_active(tui, "0.1") is True


def test_is_subtask_cli_active_returns_true_for_child_path():
    """Test that _is_subtask_cli_active returns True for child path."""
    tui = SimpleNamespace(
        _cli_activity_task_id="TASK-001",
        _cli_activity_subtask_path="0",
        _cli_activity_expires=time.time() + 10,
        current_task_detail=SimpleNamespace(id="TASK-001"),
    )

    assert _is_subtask_cli_active(tui, "0.1") is True


def test_is_subtask_cli_active_returns_true_for_parent_path():
    """Test that _is_subtask_cli_active returns True for parent path."""
    tui = SimpleNamespace(
        _cli_activity_task_id="TASK-001",
        _cli_activity_subtask_path="0.1.2",
        _cli_activity_expires=time.time() + 10,
        current_task_detail=SimpleNamespace(id="TASK-001"),
    )

    assert _is_subtask_cli_active(tui, "0.1") is True


def test_is_subtask_cli_active_returns_false_for_different_task():
    """Test that _is_subtask_cli_active returns False for different task."""
    tui = SimpleNamespace(
        _cli_activity_task_id="TASK-001",
        _cli_activity_subtask_path="0.1",
        _cli_activity_expires=time.time() + 10,
        current_task_detail=SimpleNamespace(id="TASK-002"),  # Different task
    )

    assert _is_subtask_cli_active(tui, "0.1") is False


def test_is_subtask_cli_active_returns_false_when_expired():
    """Test that _is_subtask_cli_active returns False when expired."""
    tui = SimpleNamespace(
        _cli_activity_task_id="TASK-001",
        _cli_activity_subtask_path="0.1",
        _cli_activity_expires=time.time() - 1,  # Expired
        current_task_detail=SimpleNamespace(id="TASK-001"),
    )

    assert _is_subtask_cli_active(tui, "0.1") is False


def test_is_subtask_cli_active_returns_true_when_no_specific_path():
    """Test that _is_subtask_cli_active returns True when no specific path."""
    tui = SimpleNamespace(
        _cli_activity_task_id="TASK-001",
        _cli_activity_subtask_path=None,  # No specific path
        _cli_activity_expires=time.time() + 10,
        current_task_detail=SimpleNamespace(id="TASK-001"),
    )

    # All subtasks of the active task are considered active
    assert _is_subtask_cli_active(tui, "0") is True
    assert _is_subtask_cli_active(tui, "1.2.3") is True


def test_is_subtask_cli_active_returns_false_when_no_detail():
    """Test that _is_subtask_cli_active returns False when no detail view."""
    tui = SimpleNamespace(
        _cli_activity_task_id="TASK-001",
        _cli_activity_subtask_path="0.1",
        _cli_activity_expires=time.time() + 10,
        current_task_detail=None,  # No detail view
    )

    assert _is_subtask_cli_active(tui, "0.1") is False
