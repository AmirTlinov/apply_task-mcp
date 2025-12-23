"""Unit tests for complete intent gating with lint severity."""

from core import Step, TaskDetail
from core.desktop.devtools.application.task_manager import TaskManager
from core.desktop.devtools.interface.intent_api import handle_complete


def test_complete_blocks_on_lint_errors(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    step = Step(False, "Step", success_criteria=[], tests=[])
    task = TaskDetail(id="TASK-001", title="Example", status="TODO", steps=[step])
    manager.save_task(task, skip_sync=True)

    resp = handle_complete(manager, {"intent": "complete", "task": "TASK-001", "status": "DONE"})
    assert resp.success is False
    assert resp.error_code == "LINT_ERRORS_BLOCKING"
    assert resp.result
    assert resp.result.get("lint")


def test_complete_allows_warnings(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    step = Step(False, "Step", success_criteria=["c"], tests=["t"])
    step.completed = True
    step.criteria_confirmed = True
    step.tests_confirmed = True
    task = TaskDetail(id="TASK-001", title="Example", status="TODO", steps=[step], success_criteria=["done"])
    manager.save_task(task, skip_sync=True)

    resp = handle_complete(manager, {"intent": "complete", "task": "TASK-001", "status": "DONE"})
    assert resp.success is True
    assert resp.result.get("task")
