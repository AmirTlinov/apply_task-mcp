from core import Step, TaskDetail, TaskNode
from core.desktop.devtools.application.task_manager import TaskManager
from core.desktop.devtools.interface.intent_api import handle_radar


def test_radar_now_ready_prioritizes_runway_recipe_when_runway_closed(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    step = Step.new("Step title long enough 12345", criteria=["c"], tests=["t"])
    assert step is not None
    step.completed = True
    step.criteria_confirmed = True
    step.tests_confirmed = True

    # Root DoD missing keeps the task non-DONE even at 100% progress (runway closed).
    task = TaskDetail(id="TASK-001", title="Task", status="ACTIVE", steps=[step])
    task.success_criteria = []
    manager.save_task(task, skip_sync=True)

    resp = handle_radar(manager, {"intent": "radar", "task": "TASK-001"})
    assert resp.success is True
    assert (resp.result.get("now") or {}).get("kind") == "task"
    assert (resp.result.get("now") or {}).get("queue_status") == "ready"

    runway = resp.result.get("runway") or {}
    assert runway.get("open") is False
    assert (runway.get("recipe") or {}).get("intent") == "patch"

    suggestions = list(resp.result.get("next") or [])
    assert len(suggestions) == 1
    assert suggestions[0].get("action") == "close_task"
    params = suggestions[0].get("params") or {}
    assert params.get("task") == "TASK-001"
    assert params.get("apply") is True
    assert params.get("expected_target_id") == "TASK-001"
    assert isinstance(params.get("expected_revision"), int)
    patches = params.get("patches") or []
    assert isinstance(patches, list) and len(patches) == 1
    assert patches[0].get("kind") == "task_detail"
    assert patches[0].get("strict_targeting") is True
    assert patches[0].get("expected_target_id") == "TASK-001"
    assert patches[0].get("expected_kind") == "task"
    assert isinstance(patches[0].get("expected_revision"), int)
    assert suggestions[0].get("validated") is True


def test_radar_runway_plan_current_recipe_is_schema_safe(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    plan = TaskDetail(id="PLAN-001", title="Plan", status="TODO", kind="plan")
    plan.plan_steps = ["one", "two"]
    plan.plan_current = 99
    manager.save_task(plan, skip_sync=True)

    resp = handle_radar(manager, {"intent": "radar", "plan": "PLAN-001"})
    assert resp.success is True
    runway = resp.result.get("runway") or {}
    assert runway.get("open") is False
    recipe = runway.get("recipe") or {}
    assert recipe.get("intent") == "patch"
    ops = recipe.get("ops") or []
    assert ops and isinstance(ops[0].get("value"), str)


def test_radar_treats_empty_step_tree_as_ready_and_offers_one_shot_landing(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    # No steps at all: "all steps completed" is vacuously true; runway is the only gate.
    task = TaskDetail(id="TASK-001", title="Task", status="ACTIVE", steps=[], success_criteria=[])
    manager.save_task(task, skip_sync=True)

    resp = handle_radar(manager, {"intent": "radar", "task": "TASK-001"})
    assert resp.success is True
    assert (resp.result.get("now") or {}).get("kind") == "task"
    assert (resp.result.get("now") or {}).get("queue_status") == "ready"

    runway = resp.result.get("runway") or {}
    assert runway.get("open") is False
    assert (runway.get("recipe") or {}).get("intent") == "patch"

    suggestions = list(resp.result.get("next") or [])
    assert len(suggestions) == 1
    assert suggestions[0].get("action") == "close_task"
    assert suggestions[0].get("validated") is True
    params = suggestions[0].get("params") or {}
    assert params.get("task") == "TASK-001"
    assert params.get("apply") is True
    patches = params.get("patches") or []
    assert isinstance(patches, list) and len(patches) == 1
    assert patches[0].get("kind") == "task_detail"
    ops = patches[0].get("ops") or []
    assert ops and ops[0].get("field") == "success_criteria"


def test_radar_completion_semantics_match_full_nested_step_tree(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    # Root step is (incorrectly) marked completed, but a nested child step is still open.
    child = Step.new("Child step title long enough 12345", criteria=["c"], tests=["t"])
    assert child is not None
    root = Step.new("Root step title long enough 12345", criteria=["c"], tests=["t"])
    assert root is not None
    root.completed = True
    root.plan.tasks = [TaskNode(title="Inner", steps=[child])]

    task = TaskDetail(id="TASK-001", title="Task", status="ACTIVE", steps=[root], success_criteria=["done"])
    manager.save_task(task, skip_sync=True)

    resp = handle_radar(manager, {"intent": "radar", "task": "TASK-001"})
    assert resp.success is True
    now = resp.result.get("now") or {}
    assert now.get("kind") == "step"
    assert now.get("path") == "s:0.t:0.s:0"


def test_radar_queue_status_completed_when_task_already_done(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    task = TaskDetail(id="TASK-001", title="Task", status="DONE", steps=[], success_criteria=["done"])
    manager.save_task(task, skip_sync=True)

    resp = handle_radar(manager, {"intent": "radar", "task": "TASK-001"})
    assert resp.success is True
    now = resp.result.get("now") or {}
    assert now.get("kind") == "task"
    assert now.get("queue_status") == "completed"
