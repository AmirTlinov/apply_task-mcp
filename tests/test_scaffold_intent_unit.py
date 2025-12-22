"""Unit tests for scaffold intent (templates -> plans/tasks)."""

from pathlib import Path

from core.desktop.devtools.application.task_manager import TaskManager
from core.desktop.devtools.interface.intent_api import process_intent


def _task_files(tasks_dir: Path) -> list[Path]:
    return sorted([p for p in tasks_dir.rglob("*.task") if p.is_file()])


def test_scaffold_task_dry_run_does_not_write(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    plan = process_intent(manager, {"intent": "create", "kind": "plan", "title": "P"}).result["plan"]
    plan_id = plan["id"]

    before = _task_files(tasks_dir)
    resp = process_intent(
        manager,
        {"intent": "scaffold", "template": "bugfix", "kind": "task", "title": "T", "parent": plan_id},
    )
    after = _task_files(tasks_dir)

    assert resp.success is True
    assert resp.result["dry_run"] is True
    assert resp.result["kind"] == "task"
    assert resp.result["parent"] == plan_id
    assert before == after


def test_scaffold_task_write_creates_file_and_operation_id(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    plan_id = process_intent(manager, {"intent": "create", "kind": "plan", "title": "P"}).result["plan_id"]

    resp = process_intent(
        manager,
        {
            "intent": "scaffold",
            "template": "bugfix",
            "kind": "task",
            "title": "T",
            "parent": plan_id,
            "dry_run": False,
        },
    )
    assert resp.success is True
    assert resp.result["dry_run"] is False
    task_id = resp.result.get("task_id")
    assert isinstance(task_id, str) and task_id.startswith("TASK-")
    assert (tasks_dir / f"{task_id}.task").exists()
    assert resp.meta.get("operation_id")


def test_scaffold_task_missing_parent_has_actionable_suggestions(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    resp = process_intent(manager, {"intent": "scaffold", "template": "bugfix", "kind": "task", "title": "T"})
    assert resp.success is False
    assert resp.error_code == "MISSING_PARENT"
    assert resp.suggestions
    assert any(s.action in {"context", "focus_get", "focus_set"} for s in (resp.suggestions or []))

