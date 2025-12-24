from core import Step, TaskDetail
from core.desktop.devtools.application.task_manager import TaskManager
from core.desktop.devtools.interface.intent_api import handle_radar


def test_radar_suggests_close_step_with_checkpoints_when_step_not_ready(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    step = Step.new("Step title long enough 12345", criteria=["c"], tests=["t"])
    assert step is not None
    # Not completed and checkpoints not confirmed -> radar should suggest close_step with checkpoints.
    step.completed = False
    step.criteria_confirmed = False
    step.tests_confirmed = False

    task = TaskDetail(id="TASK-001", title="Task", status="ACTIVE", steps=[step], success_criteria=["done"])
    manager.save_task(task, skip_sync=True)

    resp = handle_radar(manager, {"intent": "radar", "task": "TASK-001"})
    assert resp.success is True
    suggestions = list(resp.result.get("next") or [])
    assert len(suggestions) == 1
    assert suggestions[0].get("action") == "close_step"
    assert suggestions[0].get("validated") is True

    params = suggestions[0].get("params") or {}
    assert params.get("task") == "TASK-001"
    assert params.get("path") == "s:0"
    checkpoints = params.get("checkpoints") or {}
    assert checkpoints.get("criteria", {}).get("confirmed") is True
    assert checkpoints.get("tests", {}).get("confirmed") is True

