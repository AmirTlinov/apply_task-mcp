from core import Step, TaskDetail
from core.desktop.devtools.application.task_manager import TaskManager
from core.desktop.devtools.interface.intent_api import handle_radar


def test_radar_runway_surfaces_blocking_lint_and_patch_recipe(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    step = Step.new("Step title long enough 12345", criteria=["c"], tests=["t"])
    assert step is not None
    task = TaskDetail(id="TASK-001", title="Task", status="ACTIVE", steps=[step])
    # Root DoD missing -> blocking lint error.
    task.success_criteria = []
    manager.save_task(task, skip_sync=True)

    resp = handle_radar(manager, {"intent": "radar", "task": "TASK-001"})
    assert resp.success is True
    runway = resp.result.get("runway") or {}
    assert runway.get("open") is False
    blocking = runway.get("blocking") or {}
    lint_blocking = (blocking.get("lint") or {}).get("top_errors") or []
    assert any(i.get("code") == "TASK_SUCCESS_CRITERIA_MISSING" for i in lint_blocking)
    assert blocking.get("validation") is None, "validation must not duplicate lint blockers in runway payload"
    recipe = runway.get("recipe") or {}
    assert recipe.get("intent") == "patch"
    assert recipe.get("task") == "TASK-001"
    assert recipe.get("kind") == "task_detail"
    assert recipe.get("ops")


def test_radar_runway_uses_next_suggestion_when_validation_blocks(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    step = Step.new("Pending step title long enough 12345", criteria=["c"], tests=["t"])
    assert step is not None
    task = TaskDetail(id="TASK-001", title="Task", status="ACTIVE", steps=[step], success_criteria=["done"])
    manager.save_task(task, skip_sync=True)

    resp = handle_radar(manager, {"intent": "radar", "task": "TASK-001"})
    assert resp.success is True
    runway = resp.result.get("runway") or {}
    assert runway.get("open") is False
    recipe = runway.get("recipe") or {}
    # Not ready to complete; runway should suggest progressing the current step (batch close_step).
    assert recipe.get("intent") in {"batch", "close_step"}


def test_radar_runway_recipe_present_for_invalid_dependency_id(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    task = TaskDetail(id="TASK-001", title="Task", status="ACTIVE", success_criteria=["done"], depends_on=["NOT_A_TASK"])
    manager.save_task(task, skip_sync=True)

    resp = handle_radar(manager, {"intent": "radar", "task": "TASK-001"})
    assert resp.success is True
    runway = resp.result.get("runway") or {}
    assert runway.get("open") is False
    recipe = runway.get("recipe") or {}
    assert recipe.get("intent")
