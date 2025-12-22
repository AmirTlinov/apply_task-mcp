"""Unit tests for templates_list intent (read-only templates catalog)."""

from core.desktop.devtools.application.task_manager import TaskManager
from core.desktop.devtools.interface.intent_api import process_intent


def test_templates_list_returns_deterministic_catalog(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    resp = process_intent(manager, {"intent": "templates_list"})
    assert resp.success is True
    templates = resp.result.get("templates") or []
    assert isinstance(templates, list)
    ids = [t.get("id") for t in templates]
    assert ids == sorted(ids)
    assert "bugfix" in ids
    assert "feature" in ids
    assert "refactor" in ids

    # Basic shape: id/name/description/supports
    first = templates[0]
    assert "name" in first
    assert "description" in first
    supports = first.get("supports")
    assert isinstance(supports, list)
    assert set(supports).issubset({"plan", "task"})

