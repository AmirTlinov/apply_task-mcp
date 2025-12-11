"""Unit tests for cli_history module."""

import json
import time
from pathlib import Path

import pytest

from core.desktop.devtools.interface.cli_history import (
    Operation,
    OperationHistory,
    get_global_storage_dir,
    get_project_namespace,
    get_project_tasks_dir,
    migrate_to_global,
    MAX_HISTORY_SIZE,
)


class TestOperation:
    """Tests for Operation dataclass."""

    def test_to_dict(self):
        op = Operation(
            id="abc123",
            timestamp=1234567890.0,
            intent="decompose",
            task_id="TASK-001",
            data={"subtasks": []},
            snapshot_id="snap1",
            result={"created": 2},
        )
        d = op.to_dict()
        assert d["id"] == "abc123"
        assert d["intent"] == "decompose"
        assert d["task_id"] == "TASK-001"
        assert d["snapshot_id"] == "snap1"
        assert d["undone"] is False

    def test_from_dict(self):
        data = {
            "id": "xyz789",
            "timestamp": 1234567890.0,
            "intent": "verify",
            "task_id": "TASK-002",
            "data": {},
            "snapshot_id": None,
            "result": None,
            "undone": True,
        }
        op = Operation.from_dict(data)
        assert op.id == "xyz789"
        assert op.intent == "verify"
        assert op.undone is True


class TestOperationHistory:
    """Tests for OperationHistory class."""

    def test_init_creates_dirs(self, tmp_path):
        storage_dir = tmp_path / "history"
        history = OperationHistory(storage_dir)

        assert storage_dir.exists()
        assert (storage_dir / ".snapshots").exists()

    def test_record_operation(self, tmp_path):
        storage_dir = tmp_path / "history"
        history = OperationHistory(storage_dir)

        op = history.record(
            intent="decompose",
            task_id="TASK-001",
            data={"subtasks": [{"title": "Test"}]},
        )

        assert op.id is not None
        assert op.intent == "decompose"
        assert op.task_id == "TASK-001"
        assert len(history.operations) == 1
        assert history.current_index == 0

    def test_record_with_snapshot(self, tmp_path):
        storage_dir = tmp_path / "history"
        task_file = storage_dir / "TASK-001.task"
        storage_dir.mkdir(parents=True)
        task_file.write_text("task content")

        history = OperationHistory(storage_dir)
        op = history.record(
            intent="define",
            task_id="TASK-001",
            data={},
            task_file=task_file,
        )

        assert op.snapshot_id is not None
        snapshot_path = storage_dir / ".snapshots" / f"{op.snapshot_id}.task"
        assert snapshot_path.exists()
        assert snapshot_path.read_text() == "task content"

    def test_can_undo_initially_false(self, tmp_path):
        history = OperationHistory(tmp_path / "history")
        assert history.can_undo() is False

    def test_can_undo_after_record(self, tmp_path):
        history = OperationHistory(tmp_path / "history")
        history.record(intent="test", task_id=None, data={})
        assert history.can_undo() is True

    def test_can_redo_initially_false(self, tmp_path):
        history = OperationHistory(tmp_path / "history")
        assert history.can_redo() is False

    def test_undo_restores_snapshot(self, tmp_path):
        storage_dir = tmp_path / "history"
        task_file = storage_dir / "TASK-001.task"
        storage_dir.mkdir(parents=True)
        task_file.write_text("original content")

        history = OperationHistory(storage_dir)
        history.record(
            intent="modify",
            task_id="TASK-001",
            data={},
            task_file=task_file,
        )

        # Modify the file
        task_file.write_text("modified content")
        assert task_file.read_text() == "modified content"

        # Undo
        success, error, op = history.undo(storage_dir)
        assert success is True
        assert task_file.read_text() == "original content"

    def test_undo_marks_operation_undone(self, tmp_path):
        storage_dir = tmp_path / "history"
        history = OperationHistory(storage_dir)
        history.record(intent="test", task_id=None, data={})

        success, error, op = history.undo(storage_dir)
        assert success is True
        assert op.undone is True
        assert history.current_index == -1

    def test_redo_marks_operation_not_undone(self, tmp_path):
        storage_dir = tmp_path / "history"
        history = OperationHistory(storage_dir)
        history.record(intent="test", task_id=None, data={})
        history.undo(storage_dir)

        success, error, op = history.redo(storage_dir)
        assert success is True
        assert op.undone is False

    def test_list_recent(self, tmp_path):
        history = OperationHistory(tmp_path / "history")

        for i in range(5):
            history.record(intent=f"op_{i}", task_id=None, data={})

        recent = history.list_recent(3)
        assert len(recent) == 3
        assert recent[0].intent == "op_2"
        assert recent[2].intent == "op_4"

    def test_clear(self, tmp_path):
        storage_dir = tmp_path / "history"
        history = OperationHistory(storage_dir)

        history.record(intent="test", task_id=None, data={})
        assert len(history.operations) == 1

        history.clear()
        assert len(history.operations) == 0
        assert history.current_index == -1

    def test_persistence(self, tmp_path):
        storage_dir = tmp_path / "history"

        # First session
        history1 = OperationHistory(storage_dir)
        history1.record(intent="op1", task_id="TASK-001", data={"key": "value"})
        history1.record(intent="op2", task_id="TASK-002", data={})

        # Second session - should load from disk
        history2 = OperationHistory(storage_dir)
        assert len(history2.operations) == 2
        assert history2.operations[0].intent == "op1"
        assert history2.operations[1].intent == "op2"

    def test_truncates_redo_history_on_new_record(self, tmp_path):
        storage_dir = tmp_path / "history"
        history = OperationHistory(storage_dir)

        history.record(intent="op1", task_id=None, data={})
        history.record(intent="op2", task_id=None, data={})
        history.undo(storage_dir)  # op2 is now undone

        # Record new operation - should truncate op2
        history.record(intent="op3", task_id=None, data={})

        assert len(history.operations) == 2
        assert history.operations[1].intent == "op3"

    def test_max_history_size(self, tmp_path):
        storage_dir = tmp_path / "history"
        history = OperationHistory(storage_dir)

        # Record more than MAX_HISTORY_SIZE operations
        for i in range(MAX_HISTORY_SIZE + 10):
            history.record(intent=f"op_{i}", task_id=None, data={})

        assert len(history.operations) == MAX_HISTORY_SIZE


class TestGlobalStorage:
    """Tests for global storage helpers."""

    def test_get_global_storage_dir(self):
        path = get_global_storage_dir()
        assert path == Path.home() / ".tasks"

    def test_get_project_namespace_from_dir_name(self, tmp_path):
        project_dir = tmp_path / "my_project"
        project_dir.mkdir()

        namespace = get_project_namespace(project_dir)
        assert namespace == "my_project"

    def test_get_project_namespace_from_git(self, tmp_path):
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        git_dir = project_dir / ".git"
        git_dir.mkdir()
        config = git_dir / "config"
        config.write_text('[remote "origin"]\n\turl = git@github.com:user/repo.git\n')

        namespace = get_project_namespace(project_dir)
        assert namespace == "user_repo"

    def test_get_project_tasks_dir_local(self, tmp_path):
        path = get_project_tasks_dir(tmp_path, use_global=False)
        assert path == tmp_path / ".tasks"

    def test_get_project_tasks_dir_global(self, tmp_path):
        project_dir = tmp_path / "test_project"
        project_dir.mkdir()

        path = get_project_tasks_dir(project_dir, use_global=True)
        assert path.parent == get_global_storage_dir()
        assert path.name == "test_project"

    def test_migrate_to_global_no_local(self, tmp_path):
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        success, message = migrate_to_global(project_dir)
        assert success is False
        assert "не найдена" in message

    def test_migrate_to_global_success(self, tmp_path, monkeypatch):
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        # Create local tasks
        local_tasks = project_dir / ".tasks"
        local_tasks.mkdir()
        (local_tasks / "TASK-001.task").write_text("task 1")
        (local_tasks / "TASK-002.task").write_text("task 2")

        # Mock global storage to be in tmp_path
        global_root = tmp_path / "global_tasks"
        monkeypatch.setattr(
            "core.desktop.devtools.interface.cli_history.get_global_storage_dir",
            lambda: global_root,
        )

        success, message = migrate_to_global(project_dir)
        assert success is True

        global_tasks = global_root / "project"
        assert global_tasks.exists()
        assert (global_tasks / "TASK-001.task").exists()
        assert (global_tasks / "TASK-002.task").exists()
