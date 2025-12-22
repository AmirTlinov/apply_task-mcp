"""Unit tests for delta intent (agent-friendly operation deltas)."""

from core import Step, TaskDetail
from core.desktop.devtools.application.task_manager import TaskManager
from core.desktop.devtools.interface.intent_api import handle_delta, process_intent
from core.desktop.devtools.interface.operation_history import OperationHistory


def test_delta_returns_operations_after_since(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    step1 = Step(False, "Step", success_criteria=["c"], tests=["t"])
    task1 = TaskDetail(id="TASK-001", title="Example", status="TODO", steps=[step1])
    manager.save_task(task1, skip_sync=True)

    step2 = Step(False, "Step", success_criteria=["c"], tests=["t"])
    task2 = TaskDetail(id="TASK-002", title="Other", status="TODO", steps=[step2])
    manager.save_task(task2, skip_sync=True)

    r1 = process_intent(manager, {"intent": "note", "task": "TASK-001", "path": "s:0", "note": "n1"})
    assert r1.success is True
    assert r1.meta.get("operation_id")
    since_id = r1.meta["operation_id"]

    r2 = process_intent(manager, {"intent": "note", "task": "TASK-002", "path": "s:0", "note": "n2"})
    assert r2.success is True
    assert r2.meta.get("operation_id")

    r3 = process_intent(
        manager,
        {
            "intent": "verify",
            "task": "TASK-001",
            "path": "s:0",
            "checkpoints": {"criteria": {"confirmed": True}, "tests": {"confirmed": True}},
        },
    )
    assert r3.success is True
    assert r3.meta.get("operation_id")

    r4 = process_intent(manager, {"intent": "progress", "task": "TASK-001", "path": "s:0", "completed": True})
    assert r4.success is True
    assert r4.meta.get("operation_id")

    history = OperationHistory(storage_dir=tasks_dir)
    assert [op.intent for op in history.operations] == ["note", "note", "verify", "progress"]

    resp = handle_delta(manager, {"intent": "delta", "since": since_id, "task": "TASK-001", "limit": 50})
    assert resp.success is True
    assert resp.result["since"] == since_id
    assert resp.result["task"] == "TASK-001"
    assert resp.result["latest_id"] == history.operations[-1].id
    assert [op["intent"] for op in resp.result["operations"]] == ["verify", "progress"]
    assert resp.result["can_undo"] is True
    # Delta is lightweight by default (summary only).
    assert "data" not in resp.result["operations"][0]
    assert "result" not in resp.result["operations"][0]

    resp_full = handle_delta(manager, {"intent": "delta", "since": since_id, "task": "TASK-001", "limit": 50, "include_details": True})
    assert resp_full.success is True
    assert resp_full.result["include_details"] is True
    assert "data" in resp_full.result["operations"][0]
    assert "result" in resp_full.result["operations"][0]


def test_delta_errors_when_since_not_found(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    resp = handle_delta(manager, {"intent": "delta", "since": "nope"})
    assert resp.success is False
    assert resp.error_code == "SINCE_NOT_FOUND"
