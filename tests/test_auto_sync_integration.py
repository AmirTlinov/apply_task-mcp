from pathlib import Path
from types import SimpleNamespace

import tasks
from core import TaskDetail, SubTask


class DummySync:
    def __init__(self):
        self.calls_sync = []
        self.calls_pull = []
        self.enabled_flag = True
        self.config = SimpleNamespace(project_type="repository", repo="demo", workers=None)

    @property
    def enabled(self):
        return self.enabled_flag

    def sync_task(self, task):
        self.calls_sync.append(task.id)
        # emulate minimal fill
        task.project_item_id = task.project_item_id or "gh-item"
        task.project_issue_number = task.project_issue_number or 1
        return True

    def pull_task_fields(self, task):
        self.calls_pull.append(task.id)
        task.tags.append("pulled")
        return True


def build_manager(tmp_path, sync_obj):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    return tasks.TaskManager(tasks_dir=tasks_dir, sync_provider=lambda: sync_obj)


def test_save_task_pushes_when_sync_enabled(tmp_path):
    sync = DummySync()
    manager = build_manager(tmp_path, sync)
    task = manager.create_task("Demo", domain="d")
    task.subtasks = [
        SubTask(True, "Long enough subtask for ok", ["c"], ["t"], ["b"], True, True, True),
        SubTask(True, "Second long subtask ok", ["c"], ["t"], ["b"], True, True, True),
    ]
    manager.save_task(task)

    assert sync.calls_sync == [task.id]
    saved = manager.load_task(task.id, "d")
    assert saved.project_item_id == "gh-item"
    assert saved.project_issue_number == 1


def test_load_task_pulls_fields_when_has_project_id(tmp_path):
    sync = DummySync()
    manager = build_manager(tmp_path, sync)
    task = TaskDetail(id="TASK-001", title="Pull me", status="WARN", domain="")
    task.project_item_id = "gh-item"
    manager.repo.save(task)

    loaded = manager.load_task(task.id, "")
    assert loaded
    assert sync.calls_pull == [task.id]
    assert "pulled" in loaded.tags
