"""Unit tests for atomic close_step / done(auto_verify=true) and gating errors."""

from core import Step, TaskDetail
from core.step import TaskNode
from core.desktop.devtools.application.task_manager import TaskManager
from core.desktop.devtools.interface.intent_api import handle_close_step, handle_done, handle_progress


def test_close_step_completes_step_and_returns_snapshots(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    step = Step(False, "Step", success_criteria=["c"], tests=["t"])
    task = TaskDetail(id="TASK-001", title="Example", status="TODO", steps=[step])
    manager.save_task(task, skip_sync=True)

    resp = handle_close_step(
        manager,
        {
            "intent": "close_step",
            "task": "TASK-001",
            "path": "s:0",
            "checkpoints": {"criteria": {"confirmed": True}, "tests": {"confirmed": True}},
        },
    )
    assert resp.success is True
    assert resp.result["completed"] is True
    assert resp.result["checkpoints_before"]["criteria"]["confirmed"] is False
    assert resp.result["checkpoints_after"]["criteria"]["confirmed"] is True

    reloaded = manager.load_task("TASK-001", skip_sync=True)
    assert reloaded.steps[0].completed is True


def test_close_step_gating_fails_when_plan_tasks_not_done(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    step = Step(False, "Step", success_criteria=["c"], tests=["t"])
    nested = Step(False, "Nested", success_criteria=["c"], tests=["t"])
    step.plan.tasks = [TaskNode(title="Subtask", steps=[nested])]
    task = TaskDetail(id="TASK-001", title="Example", status="TODO", steps=[step])
    manager.save_task(task, skip_sync=True)

    resp = handle_close_step(
        manager,
        {
            "intent": "close_step",
            "task": "TASK-001",
            "path": "s:0",
            "checkpoints": {"criteria": {"confirmed": True}, "tests": {"confirmed": True}},
        },
    )
    assert resp.success is False
    assert resp.error_code == "GATING_FAILED"
    assert "plan_tasks" in (resp.result or {}).get("needs", [])

    reloaded = manager.load_task("TASK-001", skip_sync=True)
    assert reloaded.steps[0].completed is False
    # Verify is applied (criteria/tests) even if completion is gated by plan tasks.
    assert reloaded.steps[0].criteria_confirmed is True
    assert reloaded.steps[0].tests_confirmed is True


def test_done_auto_verify_is_atomic_close(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    step = Step(False, "Step", success_criteria=["c"], tests=["t"])
    task = TaskDetail(id="TASK-001", title="Example", status="TODO", steps=[step])
    manager.save_task(task, skip_sync=True)

    resp = handle_done(
        manager,
        {
            "intent": "done",
            "task": "TASK-001",
            "path": "s:0",
            "auto_verify": True,
            "checkpoints": {"criteria": {"confirmed": True}, "tests": {"confirmed": True}},
        },
    )
    assert resp.success is True
    assert resp.result["completed"] is True

    reloaded = manager.load_task("TASK-001", skip_sync=True)
    assert reloaded.steps[0].completed is True


def test_progress_returns_gating_failed_when_missing_checkpoints(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    step = Step(False, "Step", success_criteria=["c"], tests=["t"])
    task = TaskDetail(id="TASK-001", title="Example", status="TODO", steps=[step])
    manager.save_task(task, skip_sync=True)

    resp = handle_progress(
        manager,
        {"intent": "progress", "task": "TASK-001", "path": "s:0", "completed": True},
    )
    assert resp.success is False
    assert resp.error_code == "GATING_FAILED"
    assert set(resp.result["missing_checkpoints"]) == {"criteria", "tests"}
