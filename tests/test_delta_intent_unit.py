"""Unit tests for delta intent (agent-friendly operation deltas)."""

from core import Step, TaskDetail
from core.desktop.devtools.application.task_manager import TaskManager
from core.desktop.devtools.interface.intent_api import handle_delta, process_intent
from core.desktop.devtools.interface.operation_history import OperationHistory


def test_delta_returns_operations_after_since(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    step = Step(False, "Step", success_criteria=["c"], tests=["t"])
    task = TaskDetail(id="TASK-001", title="Example", status="TODO", steps=[step])
    manager.save_task(task, skip_sync=True)

    assert process_intent(manager, {"intent": "note", "task": "TASK-001", "path": "s:0", "note": "n1"}).success is True
    assert (
        process_intent(
            manager,
            {
                "intent": "verify",
                "task": "TASK-001",
                "path": "s:0",
                "checkpoints": {"criteria": {"confirmed": True}, "tests": {"confirmed": True}},
            },
        ).success
        is True
    )
    assert process_intent(manager, {"intent": "progress", "task": "TASK-001", "path": "s:0", "completed": True}).success is True

    history = OperationHistory(storage_dir=tasks_dir)
    assert [op.intent for op in history.operations] == ["note", "verify", "progress"]

    since_id = history.operations[0].id
    resp = handle_delta(manager, {"intent": "delta", "since": since_id, "limit": 50})
    assert resp.success is True
    assert resp.result["since"] == since_id
    assert resp.result["latest_id"] == history.operations[-1].id
    assert [op["intent"] for op in resp.result["operations"]] == ["verify", "progress"]
    assert resp.result["can_undo"] is True


def test_delta_errors_when_since_not_found(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    resp = handle_delta(manager, {"intent": "delta", "since": "nope"})
    assert resp.success is False
    assert resp.error_code == "SINCE_NOT_FOUND"
