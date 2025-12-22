"""Unit tests for focus fallback in process_intent for mutating intents.

These tests validate the contract:
- explicit > focus
- if id omitted, focus is used (when compatible)
- errors are actionable (suggestions + target_resolution in context)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core import Step, TaskDetail
from core.desktop.devtools.application.context import clear_last_task
from core.desktop.devtools.interface.intent_api import (
    handle_focus_set,
    process_intent,
)
from core.desktop.devtools.application.task_manager import TaskManager


@pytest.fixture
def manager(tmp_path, monkeypatch) -> TaskManager:
    # Isolate `.last` to this test run (resolve_project_root falls back to CWD outside git).
    monkeypatch.chdir(tmp_path)
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    return TaskManager(tasks_dir=tasks_dir)


@pytest.fixture
def sample_plan_and_task(manager: TaskManager) -> tuple[TaskDetail, TaskDetail]:
    plan = TaskDetail(id="PLAN-001", title="Plan", status="TODO", kind="plan")
    manager.save_task(plan, skip_sync=True)

    task = TaskDetail(
        id="TASK-001",
        title="Task",
        status="TODO",
        kind="task",
        parent="PLAN-001",
        steps=[
            Step(
                title="Step 0",
                completed=False,
                success_criteria=[],
                tests=[],
                blockers=[],
                progress_notes=[],
                started_at=None,
                blocked=False,
                block_reason="",
            )
        ],
    )
    manager.save_task(task, skip_sync=True)
    return plan, task


def test_process_intent_uses_focus_for_task_only_mutation(manager: TaskManager, sample_plan_and_task) -> None:
    _plan, _task = sample_plan_and_task
    clear_last_task()

    focus_resp = handle_focus_set(manager, {"intent": "focus_set", "task": "TASK-001"})
    assert focus_resp.success is True

    resp = process_intent(
        manager,
        {
            "intent": "note",
            "path": "s:0",
            "note": "work in progress",
        },
    )
    assert resp.success is True
    assert resp.intent == "note"
    assert resp.context["task_id"] == "TASK-001"
    assert resp.context["target_resolution"]["source"] == "focus"
    assert resp.context["target_resolution"]["task"] == "TASK-001"


def test_process_intent_derives_parent_plan_for_plan_only_mutation(manager: TaskManager, sample_plan_and_task) -> None:
    _plan, _task = sample_plan_and_task
    clear_last_task()

    focus_resp = handle_focus_set(manager, {"intent": "focus_set", "task": "TASK-001"})
    assert focus_resp.success is True

    resp = process_intent(
        manager,
        {
            "intent": "contract",
            "current": "Goal: ship it",
        },
    )
    assert resp.success is True
    assert resp.intent == "contract"
    assert resp.context["task_id"] == "PLAN-001"
    assert resp.context["target_resolution"]["source"] == "focus_task_parent"
    assert resp.context["target_resolution"]["plan"] == "PLAN-001"


def test_process_intent_rejects_incompatible_focus(manager: TaskManager, sample_plan_and_task) -> None:
    _plan, _task = sample_plan_and_task
    clear_last_task()

    # Focus on plan, then call a task-only mutation.
    focus_resp = handle_focus_set(manager, {"intent": "focus_set", "task": "PLAN-001"})
    assert focus_resp.success is True

    resp = process_intent(manager, {"intent": "note", "path": "s:0", "note": "x"})
    assert resp.success is False
    assert resp.intent == "note"
    assert resp.error_code == "FOCUS_INCOMPATIBLE"
    assert resp.context["target_resolution"]["source"] == "focus_incompatible"
    assert any(s.action == "focus_set" for s in (resp.suggestions or []))


def test_process_intent_requires_target_when_no_focus(manager: TaskManager, sample_plan_and_task) -> None:
    _plan, _task = sample_plan_and_task
    # Ensure there is no `.last`.
    clear_last_task()
    # Also remove any accidental `.last` in the tasks dir (should not exist, but keep test stable).
    Path(".last").unlink(missing_ok=True)

    resp = process_intent(manager, {"intent": "note", "path": "s:0", "note": "x"})
    assert resp.success is False
    assert resp.intent == "note"
    assert resp.error_code == "MISSING_TARGET"
    assert any(s.action in {"context", "focus_get", "focus_set"} for s in (resp.suggestions or []))

