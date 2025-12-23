"""Unit tests for preview/audit stream separation (no history pollution by default)."""

from pathlib import Path

from core import Step, TaskDetail
from core.desktop.devtools.application.task_manager import TaskManager
from core.desktop.devtools.interface.intent_api import handle_delta, handle_history, process_intent
from core.desktop.devtools.interface.operation_history import OperationHistory


def test_close_task_dry_run_does_not_write_ops_history(tmp_path: Path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    step = Step.new("Step title long enough 12345", criteria=["c"], tests=["t"])
    assert step is not None
    task = TaskDetail(id="TASK-001", title="Task", status="ACTIVE", steps=[step])
    task.success_criteria = []
    manager.save_task(task, skip_sync=True)

    resp = process_intent(manager, {"intent": "close_task", "task": "TASK-001"})
    assert resp.success is True
    assert resp.result.get("dry_run") is True
    assert resp.meta.get("operation_id") in {None, ""}  # preview must be silent by default

    history = OperationHistory(storage_dir=tasks_dir)
    assert history.operations == []
    assert history.audit_operations == []


def test_close_task_dry_run_audit_records_to_audit_stream_only(tmp_path: Path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    step = Step.new("Step title long enough 12345", criteria=["c"], tests=["t"])
    assert step is not None
    task = TaskDetail(id="TASK-001", title="Task", status="ACTIVE", steps=[step])
    task.success_criteria = []
    manager.save_task(task, skip_sync=True)

    resp = process_intent(manager, {"intent": "close_task", "task": "TASK-001", "audit": True})
    assert resp.success is True
    assert resp.result.get("dry_run") is True
    assert resp.meta.get("operation_id") in {None, ""}  # still not an ops mutation
    assert resp.meta.get("audit_operation_id")

    history = OperationHistory(storage_dir=tasks_dir)
    assert history.operations == []
    assert len(history.audit_operations) == 1
    assert history.audit_operations[0].intent == "close_task"
    assert history.audit_operations[0].stream == "audit"
    assert history.audit_operations[0].effect == "read"

    delta_audit = handle_delta(manager, {"intent": "delta", "stream": "audit", "limit": 50})
    assert delta_audit.success is True
    assert [op["intent"] for op in delta_audit.result["operations"]] == ["close_task"]
    assert delta_audit.result["operations"][0]["stream"] == "audit"

    delta_ops = handle_delta(manager, {"intent": "delta", "stream": "ops", "limit": 50})
    assert delta_ops.success is True
    assert delta_ops.result["operations"] == []

    hist_audit = handle_history(manager, {"intent": "history", "stream": "audit", "limit": 10})
    assert hist_audit.success is True
    assert len(hist_audit.result["operations"]) == 1
    assert hist_audit.result["operations"][0]["stream"] == "audit"


def test_delta_filters_by_intent_and_path(tmp_path: Path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    step = Step.new("Step title long enough 12345", criteria=["c"], tests=["t"])
    assert step is not None
    task = TaskDetail(id="TASK-001", title="Task", status="ACTIVE", steps=[step], success_criteria=["done"])
    manager.save_task(task, skip_sync=True)

    r1 = process_intent(manager, {"intent": "note", "task": "TASK-001", "path": "s:0", "note": "n1"})
    assert r1.success is True
    r2 = process_intent(
        manager,
        {"intent": "verify", "task": "TASK-001", "path": "s:0", "checkpoints": {"criteria": {"confirmed": True}, "tests": {"confirmed": True}}},
    )
    assert r2.success is True

    only_note = handle_delta(manager, {"intent": "delta", "stream": "ops", "intents": ["note"], "limit": 50})
    assert only_note.success is True
    assert [op["intent"] for op in only_note.result["operations"]] == ["note"]

    only_path = handle_delta(manager, {"intent": "delta", "stream": "ops", "paths": ["s:0"], "limit": 50})
    assert only_path.success is True
    assert [op["intent"] for op in only_path.result["operations"]] == ["note", "verify"]
