from core import Step, TaskDetail
from core.desktop.devtools.application.task_manager import TaskManager


def test_auto_status_does_not_set_done_without_root_criteria(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    step = Step(False, "Step", success_criteria=["c"], tests=["t"])
    step.criteria_confirmed = True
    step.tests_confirmed = True
    task = TaskDetail(id="TASK-001", title="Example", status="TODO", steps=[step], success_criteria=[])
    manager.save_task(task, skip_sync=True)

    ok, msg = manager.set_step_completed("TASK-001", 0, True, path="s:0")
    assert ok is True
    assert msg is None

    updated = manager.load_task("TASK-001", skip_sync=True)
    assert updated.calculate_progress() == 100
    assert updated.status != "DONE"
