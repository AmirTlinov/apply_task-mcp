from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tasks
import projects_sync
from core.desktop.devtools.application import task_manager as task_manager_mod


class DummyProjects(projects_sync.ProjectsSync):
    def __init__(self, config_path: Path):
        super().__init__(config_path=config_path)
        self.config = projects_sync.ProjectConfig(project_type="repository", owner="octo", repo="demo", number=1)
        self.enabled_flag = True
        self.calls = []

    @property
    def enabled(self) -> bool:
        return self.enabled_flag

    def sync_step(self, task):
        self.calls.append(task.id)
        if not task.project_item_id:
            task.project_item_id = "gh-item"
        if not task.project_issue_number:
            task.project_issue_number = 99
        return True


def _write_task(path: Path, task_id: str):
    content = f"""---
id: {task_id}
title: Demo {task_id}
status: TODO
domain:
created: 2025-01-01 00:00
updated: 2025-01-01 00:00
---
# Demo
"""
    path.write_text(content, encoding="utf-8")


def test_auto_sync_all(monkeypatch, tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    _write_task(tasks_dir / "TASK-001.task", "TASK-001")

    dummy_sync = DummyProjects(config_path=tmp_path / "dummy.yaml")
    monkeypatch.setattr(task_manager_mod, "get_projects_sync", lambda: dummy_sync)
    monkeypatch.setattr(tasks.TaskManager, "_make_parallel_sync", lambda self, base_sync: dummy_sync)
    monkeypatch.setattr(tasks.TaskManager, "load_config", staticmethod(lambda: {"auto_sync": True}))

    manager = tasks.TaskManager(tasks_dir=tasks_dir)

    assert dummy_sync.calls == ["TASK-001"]
    saved = (tasks_dir / "TASK-001.task").read_text()
    assert "project_item_id" in saved
    assert "project_issue_number" in saved
    assert manager.auto_sync_message


def test_auto_sync_disabled(monkeypatch, tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    _write_task(tasks_dir / "TASK-001.task", "TASK-001")

    dummy_sync = DummyProjects(config_path=tmp_path / "dummy.yaml")
    monkeypatch.setattr(task_manager_mod, "get_projects_sync", lambda: dummy_sync)
    monkeypatch.setattr(tasks.TaskManager, "load_config", staticmethod(lambda: {"auto_sync": False}))

    manager = tasks.TaskManager(tasks_dir=tasks_dir)

    assert dummy_sync.calls == []
    assert manager.auto_sync_message == ""


def test_pool_size_respects_config(monkeypatch, tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    _write_task(tasks_dir / "TASK-001.task", "TASK-001")
    _write_task(tasks_dir / "TASK-002.task", "TASK-002")

    dummy_sync = DummyProjects(config_path=tmp_path / "dummy.yaml")
    dummy_sync.config.workers = 1
    monkeypatch.setattr(task_manager_mod, "get_projects_sync", lambda: dummy_sync)
    monkeypatch.setattr(tasks.TaskManager, "load_config", staticmethod(lambda: {"auto_sync": True}))

    captured = {}

    class DummyPool:
        def __init__(self, max_workers):
            captured["max"] = max_workers

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def submit(self, fn, arg):
            class DummyFuture:
                def result(self_inner):
                    return fn(arg)
            return DummyFuture()

    monkeypatch.setattr(task_manager_mod, "ThreadPoolExecutor", DummyPool)
    monkeypatch.setattr(task_manager_mod, "as_completed", lambda futs: futs)

    manager = tasks.TaskManager(tasks_dir=tasks_dir)
    assert captured["max"] == 1
