"""Unit tests for patch(dry_run) preview semantics (trust-by-shape)."""

from core import Step, TaskDetail
from core.desktop.devtools.application.task_manager import TaskManager
from core.desktop.devtools.interface.intent_api import process_intent


def _make_completed_step() -> Step:
    step = Step(False, "Step", success_criteria=["c"], tests=["t"])
    step.completed = True
    step.criteria_confirmed = True
    step.tests_confirmed = True
    return step


def test_patch_dry_run_returns_current_and_computed_snapshots(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    task = TaskDetail(id="TASK-001", title="Example", status="ACTIVE", steps=[_make_completed_step()], success_criteria=[])
    manager.save_task(task, skip_sync=True)

    resp = process_intent(
        manager,
        {
            "intent": "patch",
            "task": "TASK-001",
            "dry_run": True,
            "ops": [{"op": "append", "field": "success_criteria", "value": "done"}],
        },
    )
    assert resp.success is True
    result = resp.result

    assert result.get("dry_run") is True
    assert result.get("kind") == "task_detail"
    assert "current" in result
    assert "computed" in result
    assert result["current"]["task"]["status"] == "ACTIVE"
    assert result["computed"]["task"]["status"] == "ACTIVE"
    diff = result.get("diff") or {}
    assert (diff.get("state") or {}) == {}
    fields = diff.get("fields") or []
    assert fields and fields[0].get("field") == "success_criteria"
    assert fields[0].get("before") == []
    assert fields[0].get("after") == ["done"]


def test_patch_dry_run_exposes_status_diff_when_blocked_changes(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    task = TaskDetail(id="TASK-001", title="Example", status="ACTIVE", steps=[_make_completed_step()], success_criteria=["ok"])
    manager.save_task(task, skip_sync=True)

    resp = process_intent(
        manager,
        {
            "intent": "patch",
            "task": "TASK-001",
            "dry_run": True,
            "ops": [{"op": "set", "field": "blocked", "value": True}],
        },
    )
    assert resp.success is True
    result = resp.result
    diff = result.get("diff") or {}
    state = diff.get("state") or {}

    assert state.get("blocked") == {"from": False, "to": True}
    assert state.get("status") == {"from": "ACTIVE", "to": "TODO"}
