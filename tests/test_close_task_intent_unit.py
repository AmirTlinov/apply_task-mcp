from pathlib import Path

import pytest

from core import Step, TaskDetail
from core.desktop.devtools.application.task_manager import TaskManager
from core.desktop.devtools.interface.intent_api import process_intent


@pytest.fixture
def manager(tmp_path: Path) -> TaskManager:
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    return TaskManager(tasks_dir=tasks_dir)


def test_close_task_dry_run_reports_runway_and_recipe(manager: TaskManager):
    step = Step.new("Step title long enough 12345", criteria=["c"], tests=["t"])
    assert step is not None
    task = TaskDetail(id="TASK-001", title="Task", status="ACTIVE", steps=[step])
    task.success_criteria = []
    manager.save_task(task, skip_sync=True)

    resp = process_intent(manager, {"intent": "close_task", "task": "TASK-001"})
    assert resp.success is True
    assert resp.result.get("dry_run") is True
    runway = resp.result.get("runway") or {}
    assert runway.get("open") is False
    recipe = runway.get("recipe") or {}
    assert recipe.get("intent") == "patch"
    diff = resp.result.get("diff") or {}
    patches = diff.get("patches") or []
    assert len(patches) == 1
    assert (diff.get("patch_results") or []) == []
    assert patches[0].get("kind") == "task_detail"
    ops = patches[0].get("ops") or []
    assert ops and ops[0].get("field") == "success_criteria"

    # Apply the suggested patch item via the regular patch intent (by adding the task id).
    patched = process_intent(manager, {"intent": "patch", "task": "TASK-001", **patches[0]})
    assert patched.success is True
    reloaded = manager.load_task("TASK-001", skip_sync=True)
    assert reloaded is not None
    assert reloaded.success_criteria


def test_close_task_apply_completes_when_ready(manager: TaskManager):
    step = Step.new("Ready step title long enough 12345", criteria=["c"], tests=["pytest -q"])
    assert step is not None
    step.completed = True
    step.criteria_confirmed = True
    step.tests_confirmed = True

    task = TaskDetail(id="TASK-001", title="Task", status="ACTIVE", steps=[step], success_criteria=["done"])
    manager.save_task(task, skip_sync=True)

    resp = process_intent(manager, {"intent": "close_task", "task": "TASK-001", "apply": True})
    assert resp.success is True
    reloaded = manager.load_task("TASK-001", skip_sync=True)
    assert reloaded is not None
    assert str(getattr(reloaded, "status", "") or "").upper() == "DONE"


def test_close_task_apply_blocks_when_runway_closed(manager: TaskManager):
    step = Step.new("Pending step title long enough 12345", criteria=["c"], tests=["t"])
    assert step is not None
    task = TaskDetail(id="TASK-001", title="Task", status="ACTIVE", steps=[step], success_criteria=["done"])
    manager.save_task(task, skip_sync=True)

    resp = process_intent(manager, {"intent": "close_task", "task": "TASK-001", "apply": True})
    assert resp.success is False
    assert resp.error_code == "RUNWAY_CLOSED"
