"""Unit tests for resume payload size controls (compact + include_steps)."""

from core import Step, TaskDetail
from core.desktop.devtools.application.task_manager import TaskManager
from core.desktop.devtools.interface.intent_api import process_intent
from core.step_event import StepEvent


def test_resume_default_compact_returns_summary_only(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    step = Step.new("Step title long enough 12345", criteria=["c"], tests=["t"])
    assert step is not None
    task = TaskDetail(id="TASK-001", title="Task", status="ACTIVE", steps=[step], success_criteria=["done"])
    manager.save_task(task, skip_sync=True)

    resp = process_intent(manager, {"intent": "resume", "task": "TASK-001", "events_limit": 0})
    assert resp.success is True

    result = resp.result or {}
    assert "summary" in result
    assert "task" not in result
    assert "plan" not in result


def test_resume_compact_false_include_steps_false_keeps_timeline_without_steps(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    step = Step.new("Step title long enough 12345", criteria=["c"], tests=["t"])
    assert step is not None
    task = TaskDetail(id="TASK-001", title="Task", status="ACTIVE", steps=[step], success_criteria=["done"])
    task.events = [StepEvent.created()]
    manager.save_task(task, skip_sync=True)

    resp = process_intent(
        manager,
        {"intent": "resume", "task": "TASK-001", "compact": False, "include_steps": False, "events_limit": 10},
    )
    assert resp.success is True

    result = resp.result or {}
    task_payload = result.get("task") or {}
    assert "steps" not in task_payload
    assert task_payload.get("steps_count") == 1

    timeline = result.get("timeline") or []
    assert timeline and timeline[0].get("event_type") == "created"
