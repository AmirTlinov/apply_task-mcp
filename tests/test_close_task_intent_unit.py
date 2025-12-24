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
    current = manager.load_task("TASK-001", skip_sync=True)
    assert current is not None
    expected_revision = int(getattr(current, "revision", 0) or 0)

    resp = process_intent(manager, {"intent": "close_task", "task": "TASK-001"})
    assert resp.success is True
    assert resp.result.get("dry_run") is True
    runway = resp.result.get("runway") or {}
    assert runway.get("open") is False
    recipe = runway.get("recipe") or {}
    assert recipe.get("intent") == "patch"
    assert recipe.get("strict_targeting") is True
    assert recipe.get("expected_target_id") == "TASK-001"
    assert recipe.get("expected_kind") == "task"
    assert recipe.get("expected_revision") == expected_revision
    diff = resp.result.get("diff") or {}
    patches = diff.get("patches") or []
    assert len(patches) == 1
    assert (diff.get("patch_results") or []) == []
    assert patches[0].get("kind") == "task_detail"
    assert patches[0].get("strict_targeting") is True
    assert patches[0].get("expected_target_id") == "TASK-001"
    assert patches[0].get("expected_kind") == "task"
    assert patches[0].get("expected_revision") == expected_revision
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


def test_close_task_dry_run_includes_apply_package_when_runway_open(manager: TaskManager):
    step = Step.new("Ready step title long enough 12345", criteria=["c"], tests=["pytest -q"])
    assert step is not None
    step.completed = True
    step.criteria_confirmed = True
    step.tests_confirmed = True

    task = TaskDetail(id="TASK-001", title="Task", status="ACTIVE", steps=[step], success_criteria=["done"])
    manager.save_task(task, skip_sync=True)
    current = manager.load_task("TASK-001", skip_sync=True)
    assert current is not None
    expected_revision = int(getattr(current, "revision", 0) or 0)

    resp = process_intent(manager, {"intent": "close_task", "task": "TASK-001"})
    assert resp.success is True
    assert resp.result.get("dry_run") is True
    diff = resp.result.get("diff") or {}
    apply_pkg = diff.get("apply") or {}
    assert apply_pkg.get("atomic") is True
    assert apply_pkg.get("task") == "TASK-001"
    assert apply_pkg.get("strict_targeting") is True
    assert apply_pkg.get("expected_target_id") == "TASK-001"
    assert apply_pkg.get("expected_kind") == "task"
    assert apply_pkg.get("expected_revision") == expected_revision

    ops = apply_pkg.get("operations") or []
    assert ops and ops[-1].get("intent") == "complete"


def test_close_task_apply_blocks_when_runway_closed(manager: TaskManager):
    step = Step.new("Pending step title long enough 12345", criteria=["c"], tests=["t"])
    assert step is not None
    task = TaskDetail(id="TASK-001", title="Task", status="ACTIVE", steps=[step], success_criteria=["done"])
    manager.save_task(task, skip_sync=True)

    resp = process_intent(manager, {"intent": "close_task", "task": "TASK-001", "apply": True})
    assert resp.success is False
    assert resp.error_code == "RUNWAY_CLOSED"
    # Atomic safety: no noisy payloads, only a single executable recipe suggestion.
    assert "diff" not in (resp.result or {})
    assert "runway" not in (resp.result or {})
    assert "lint" not in (resp.result or {})
    assert resp.suggestions and len(resp.suggestions) == 1
    sug = resp.suggestions[0]
    assert sug.validated is True
    assert (sug.params or {}).get("strict_targeting") is True
    assert (sug.params or {}).get("expected_target_id") == "TASK-001"
    assert isinstance((sug.params or {}).get("expected_revision"), int)


def test_close_task_apply_autolands_even_with_template_recipe(manager: TaskManager):
    step = Step.new("Ready step title long enough 12345", criteria=["c"], tests=["t"])
    assert step is not None
    step.completed = True
    step.criteria_confirmed = True
    step.tests_confirmed = True

    task = TaskDetail(id="TASK-001", title="Task", status="ACTIVE", steps=[step], success_criteria=[])
    manager.save_task(task, skip_sync=True)

    resp = process_intent(manager, {"intent": "close_task", "task": "TASK-001", "apply": True})
    assert resp.success is True
    reloaded = manager.load_task("TASK-001", skip_sync=True)
    assert reloaded is not None
    assert str(getattr(reloaded, "status", "") or "").upper() == "DONE"
    assert "<definition of done>" in list(getattr(reloaded, "success_criteria", []) or [])


def test_close_task_apply_autolands_when_contract_done_can_fill_success_criteria(manager: TaskManager):
    step = Step.new("Ready step title long enough 12345", criteria=["c"], tests=["t"])
    assert step is not None
    step.completed = True
    step.criteria_confirmed = True
    step.tests_confirmed = True

    task = TaskDetail(id="TASK-001", title="Task", status="ACTIVE", steps=[step], success_criteria=[])
    task.contract_data = {"goal": "Ship", "done": ["All checks green"], "checks": ["pytest -q"]}
    manager.save_task(task, skip_sync=True)

    resp = process_intent(manager, {"intent": "close_task", "task": "TASK-001", "apply": True})
    assert resp.success is True
    reloaded = manager.load_task("TASK-001", skip_sync=True)
    assert reloaded is not None
    assert str(getattr(reloaded, "status", "") or "").upper() == "DONE"
    assert "All checks green" in list(getattr(reloaded, "success_criteria", []) or [])


def test_close_task_preview_includes_recipe_patch_even_with_user_patches(manager: TaskManager):
    step = Step.new("Ready step title long enough 12345", criteria=["c"], tests=["t"])
    assert step is not None
    step.completed = True
    step.criteria_confirmed = True
    step.tests_confirmed = True

    task = TaskDetail(id="TASK-001", title="Task", status="ACTIVE", steps=[step], success_criteria=[])
    task.contract_data = {"goal": "Ship", "done": ["All checks green"], "checks": ["pytest -q"]}
    manager.save_task(task, skip_sync=True)

    resp = process_intent(
        manager,
        {
            "intent": "close_task",
            "task": "TASK-001",
            "patches": [{"kind": "task_detail", "ops": [{"op": "append", "field": "next_steps", "value": "x"}]}],
        },
    )
    assert resp.success is True
    assert resp.result.get("dry_run") is True
    diff = resp.result.get("diff") or {}
    patches = diff.get("patches") or []
    assert len(patches) == 2
    fields = [(p.get("ops") or [{}])[0].get("field") for p in patches]
    assert fields == ["next_steps", "success_criteria"]


def test_close_task_apply_uses_computed_diff_patches_to_land(manager: TaskManager):
    step = Step.new("Ready step title long enough 12345", criteria=["c"], tests=["t"])
    assert step is not None
    step.completed = True
    step.criteria_confirmed = True
    step.tests_confirmed = True

    task = TaskDetail(id="TASK-001", title="Task", status="ACTIVE", steps=[step], success_criteria=[])
    task.contract_data = {"goal": "Ship", "done": ["All checks green"], "checks": ["pytest -q"]}
    manager.save_task(task, skip_sync=True)

    resp = process_intent(
        manager,
        {
            "intent": "close_task",
            "task": "TASK-001",
            "apply": True,
            "patches": [{"kind": "task_detail", "ops": [{"op": "append", "field": "next_steps", "value": "x"}]}],
        },
    )
    assert resp.success is True
    reloaded = manager.load_task("TASK-001", skip_sync=True)
    assert reloaded is not None
    assert str(getattr(reloaded, "status", "") or "").upper() == "DONE"
    assert "x" in list(getattr(reloaded, "next_steps", []) or [])
    assert "All checks green" in list(getattr(reloaded, "success_criteria", []) or [])


def test_close_task_apply_rejects_expected_revision_mismatch(manager: TaskManager):
    step = Step.new("Ready step title long enough 12345", criteria=["c"], tests=["t"])
    assert step is not None
    step.completed = True
    step.criteria_confirmed = True
    step.tests_confirmed = True

    task = TaskDetail(id="TASK-001", title="Task", status="ACTIVE", steps=[step], success_criteria=["done"])
    manager.save_task(task, skip_sync=True)
    current = manager.load_task("TASK-001", skip_sync=True)
    assert current is not None
    current_rev = int(getattr(current, "revision", 0) or 0)

    resp = process_intent(
        manager,
        {
            "intent": "close_task",
            "task": "TASK-001",
            "apply": True,
            "expected_revision": current_rev + 1,
            "strict_targeting": True,
            "expected_target_id": "TASK-001",
            "expected_kind": "task",
        },
    )
    assert resp.success is False
    assert resp.error_code == "REVISION_MISMATCH"
    reloaded = manager.load_task("TASK-001", skip_sync=True)
    assert reloaded is not None
    assert str(getattr(reloaded, "status", "") or "").upper() != "DONE"


def test_close_task_apply_rejects_expected_target_mismatch(manager: TaskManager):
    step = Step.new("Ready step title long enough 12345", criteria=["c"], tests=["t"])
    assert step is not None
    step.completed = True
    step.criteria_confirmed = True
    step.tests_confirmed = True

    task = TaskDetail(id="TASK-001", title="Task", status="ACTIVE", steps=[step], success_criteria=["done"])
    manager.save_task(task, skip_sync=True)

    resp = process_intent(
        manager,
        {
            "intent": "close_task",
            "task": "TASK-001",
            "apply": True,
            "strict_targeting": True,
            "expected_target_id": "TASK-999",
            "expected_kind": "task",
        },
    )
    assert resp.success is False
    assert resp.error_code == "EXPECTED_TARGET_MISMATCH"
    reloaded = manager.load_task("TASK-001", skip_sync=True)
    assert reloaded is not None
    assert str(getattr(reloaded, "status", "") or "").upper() != "DONE"
