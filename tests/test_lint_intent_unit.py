"""Unit tests for lint intent (read-only preflight checks)."""

from core import Step, TaskDetail
from core.desktop.devtools.application.task_manager import TaskManager
from core.desktop.devtools.interface.intent_api import process_intent


def test_lint_task_reports_errors_and_suggestions(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    step = Step(False, "Short", success_criteria=[], tests=[], blockers=[], started_at="2025-12-22 20:20")
    task = TaskDetail(id="TASK-001", title="Example", status="TODO", steps=[step], depends_on=["TASK-999"])
    manager.save_task(task, skip_sync=True)

    resp = process_intent(manager, {"intent": "lint", "task": "TASK-001"})
    assert resp.success is True
    assert resp.result["item_id"] == "TASK-001"
    assert resp.result["kind"] == "task"
    assert resp.result["summary"]["errors"] >= 1

    codes = {i["code"] for i in resp.result.get("issues", [])}
    assert "TASK_SUCCESS_CRITERIA_MISSING" in codes
    assert "STEP_SUCCESS_CRITERIA_MISSING" in codes
    assert "INVALID_DEPENDENCIES" in codes

    assert resp.result.get("links")
    assert any(s.action in {"patch", "context"} for s in (resp.suggestions or []))


def test_lint_plan_detects_plan_current_out_of_range(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    plan = TaskDetail(id="PLAN-001", title="Plan", status="TODO", kind="plan", plan_steps=["A"], plan_current=2)
    manager.save_task(plan, skip_sync=True)

    resp = process_intent(manager, {"intent": "lint", "plan": "PLAN-001"})
    assert resp.success is True
    codes = {i["code"] for i in resp.result.get("issues", [])}
    assert "PLAN_CURRENT_OUT_OF_RANGE" in codes

