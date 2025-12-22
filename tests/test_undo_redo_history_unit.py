"""Unit tests for undo/redo and history snapshots (create-like + before-state)."""

from pathlib import Path

from core import Step, TaskDetail
from core.desktop.devtools.application.task_manager import TaskManager
from core.desktop.devtools.interface.intent_api import handle_redo, handle_undo, process_intent


def test_undo_redo_reverts_mutation_using_before_snapshot(tmp_path: Path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    step = Step(False, "Step", success_criteria=["c"], tests=["t"])
    task = TaskDetail(id="TASK-001", title="Example", status="TODO", steps=[step])
    manager.save_task(task, skip_sync=True)

    r1 = process_intent(manager, {"intent": "note", "task": "TASK-001", "path": "s:0", "note": "n1"})
    assert r1.success is True
    assert r1.meta.get("operation_id")
    assert manager.load_task("TASK-001", skip_sync=True).steps[0].progress_notes == ["n1"]

    u1 = handle_undo(manager, {"intent": "undo"})
    assert u1.success is True
    assert manager.load_task("TASK-001", skip_sync=True).steps[0].progress_notes == []

    r2 = handle_redo(manager, {"intent": "redo"})
    assert r2.success is True
    assert manager.load_task("TASK-001", skip_sync=True).steps[0].progress_notes == ["n1"]


def test_undo_redo_create_deletes_and_restores_file(tmp_path: Path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    created = process_intent(manager, {"intent": "create", "kind": "plan", "title": "P"})
    assert created.success is True
    assert created.meta.get("operation_id")
    plan_id = created.result["plan_id"]
    plan_file = tasks_dir / f"{plan_id}.task"
    assert plan_file.exists()

    u1 = handle_undo(manager, {"intent": "undo"})
    assert u1.success is True
    assert not plan_file.exists()

    r1 = handle_redo(manager, {"intent": "redo"})
    assert r1.success is True
    assert plan_file.exists()


def test_undo_redo_scaffold_deletes_and_restores_file(tmp_path: Path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    plan_id = process_intent(manager, {"intent": "create", "kind": "plan", "title": "P"}).result["plan_id"]
    created = process_intent(
        manager,
        {"intent": "scaffold", "template": "bugfix", "kind": "task", "title": "T", "parent": plan_id, "dry_run": False},
    )
    assert created.success is True
    assert created.meta.get("operation_id")
    task_id = created.result["task_id"]
    task_file = tasks_dir / f"{task_id}.task"
    assert task_file.exists()

    u1 = handle_undo(manager, {"intent": "undo"})
    assert u1.success is True
    assert not task_file.exists()

    r1 = handle_redo(manager, {"intent": "redo"})
    assert r1.success is True
    assert task_file.exists()

