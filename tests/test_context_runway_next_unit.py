"""Unit tests for context suggestions (must be runway-gated like radar/resume)."""

from core import Step, TaskDetail
from core.desktop.devtools.application.task_manager import TaskManager
from core.desktop.devtools.interface.intent_api import process_intent


def test_context_prefers_runway_recipe_over_close_task_when_runway_closed(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    step = Step(False, "Step", success_criteria=["c"], tests=["t"])
    step.completed = True
    step.criteria_confirmed = True
    step.tests_confirmed = True

    # All steps completed, but runway closed due to missing root success_criteria.
    task = TaskDetail(id="TASK-001", title="Example", status="ACTIVE", steps=[step], success_criteria=[])
    manager.save_task(task, skip_sync=True)

    resp = process_intent(manager, {"intent": "context", "task": "TASK-001", "include_all": False})
    assert resp.success is True
    assert resp.suggestions, "context should provide a runway-fix suggestion"
    assert len(resp.suggestions) == 1, "when runway is closed, other suggestions must be suppressed"

    top = resp.suggestions[0]
    assert top.action == "patch"
    assert top.validated is True

    params = top.params or {}
    assert params.get("task") == "TASK-001"
    assert params.get("kind") == "task_detail"
    assert params.get("strict_targeting") is True
    assert params.get("expected_target_id") == "TASK-001"
    assert params.get("expected_kind") == "task"
    assert isinstance(params.get("expected_revision"), int)

    ops = params.get("ops") or []
    assert ops and ops[0].get("field") == "success_criteria"

