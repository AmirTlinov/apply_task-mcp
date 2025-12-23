"""Unit tests for context_pack intent (radar + delta cold start)."""

from core import Step, TaskDetail
from core.desktop.devtools.application.task_manager import TaskManager
from core.desktop.devtools.interface.intent_api import handle_context_pack, process_intent


def _seed_task(manager: TaskManager) -> None:
    step = Step(False, "Step", success_criteria=["c"], tests=["t"])
    task = TaskDetail(id="TASK-001", title="Example", status="TODO", steps=[step])
    manager.save_task(task, skip_sync=True)


def test_context_pack_contains_radar_and_delta(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)
    _seed_task(manager)

    resp = handle_context_pack(manager, {"intent": "context_pack", "task": "TASK-001"})
    assert resp.success is True

    result = resp.result
    assert set(["now", "why", "verify", "next", "blockers", "open_checkpoints", "delta"]).issubset(result.keys())
    assert isinstance(result.get("delta"), dict)
    assert isinstance(result["delta"].get("operations", []), list)


def test_context_pack_budget_truncates_delta(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)
    _seed_task(manager)

    for idx in range(40):
        process_intent(manager, {"intent": "note", "task": "TASK-001", "path": "s:0", "note": f"n{idx}"})

    resp = handle_context_pack(
        manager,
        {"intent": "context_pack", "task": "TASK-001", "delta_limit": 50, "max_chars": 1000},
    )
    assert resp.success is True
    budget = resp.result.get("budget", {})
    assert budget.get("used_chars", 0) <= 1000
    assert budget.get("truncated") is True
    assert len(resp.result.get("delta", {}).get("operations", [])) <= 3


def test_context_pack_since_not_found(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)
    _seed_task(manager)

    resp = handle_context_pack(manager, {"intent": "context_pack", "task": "TASK-001", "since": "missing"})
    assert resp.success is False
    assert resp.error_code == "SINCE_NOT_FOUND"
