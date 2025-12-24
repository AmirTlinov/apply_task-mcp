from core import Step, TaskDetail
from core.desktop.devtools.application.task_manager import TaskManager
from core.desktop.devtools.interface.intent_api import handle_radar


def test_radar_suggestions_skip_completed_steps(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    plan = TaskDetail(id="PLAN-001", title="Plan", status="TODO", kind="plan")
    manager.save_task(plan, skip_sync=True)

    step1 = Step(True, "Completed step title long enough 12345", ["c1"], ["t1"], ["b1"], criteria_confirmed=True, tests_confirmed=True)
    step2 = Step(False, "Pending step title long enough 12345", ["c2"], ["t2"], ["b2"])
    task = TaskDetail(id="TASK-001", title="Example", status="ACTIVE", steps=[step1, step2], success_criteria=["done"], parent="PLAN-001")
    manager.save_task(task, skip_sync=True)

    resp = handle_radar(manager, {"intent": "radar", "task": "TASK-001"})
    assert resp.success is True
    assert resp.suggestions
    suggestion = resp.suggestions[0]
    params = suggestion.params or {}
    assert suggestion.action == "close_step"
    assert params.get("path") == "s:1"
