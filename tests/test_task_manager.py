from pathlib import Path

import pytest

from core.desktop.devtools.application import task_manager
from core.desktop.devtools.application.task_manager import TaskManager
from infrastructure.file_repository import FileTaskRepository
from core import TaskDetail, SubTask


class DummySync:
    def __init__(self, enabled=True, workers=1):
        self.enabled = enabled
        self.config = type("Cfg", (), {"workers": workers})
        self.calls = []
        self.last_push = None

    def sync_task(self, task):
        self.calls.append(task.id)
        # emulate project fields population
        task.project_item_id = task.project_item_id or "item"
        task.project_issue_number = task.project_issue_number or 1
        return True

    def clone(self):
        return self


def _write_task(path: Path, task_id: str):
    content = f"""---
id: {task_id}
title: Demo {task_id}
status: FAIL
domain:
created: 2025-01-01 00:00
updated: 2025-01-01 00:00
---
# Demo
"""
    path.write_text(content, encoding="utf-8")


def test_auto_sync_all_writes_back(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    task_file = tasks_dir / "TASK-001.task"
    _write_task(task_file, "TASK-001")

    dummy = DummySync(enabled=True, workers=1)
    manager = TaskManager(tasks_dir=tasks_dir, sync_service=dummy)

    # auto_sync runs in __init__, ensure call recorded and file updated
    assert dummy.calls == ["TASK-001"]
    saved = task_file.read_text()
    assert "project_item_id" in saved
    assert "project_issue_number" in saved
    assert manager.auto_sync_message


def test_update_task_status_validates_and_sets_ok(tmp_path):
    manager = TaskManager(tasks_dir=tmp_path / ".tasks", sync_service=DummySync(enabled=False))
    task = TaskDetail(
        id="TASK-010",
        title="Demo",
        status="WARN",
        domain="",
        created="2025-01-01 00:00",
        updated="2025-01-01 00:00",
    )
    # add subtask with checkpoints to satisfy validation
    sub = SubTask(
        completed=True,
        title="Sub with checkpoints",
        success_criteria=["c"],
        tests=["t"],
        blockers=["b"],
        criteria_confirmed=True,
        tests_confirmed=True,
        blockers_resolved=True,
    )
    task.subtasks.append(sub)
    task.success_criteria = ["sc"]
    task.tests = ["tt"]
    manager.repo.save(task)

    ok, err = manager.update_task_status("TASK-010", "OK")
    assert ok is True
    assert err is None
    reloaded = manager.load_task("TASK-010")
    assert reloaded.status == "OK"


def test_update_task_status_not_found(tmp_path):
    manager = TaskManager(tasks_dir=tmp_path / ".tasks", sync_service=DummySync(enabled=False))
    ok, err = manager.update_task_status("NOPE", "OK")
    assert ok is False
    assert err and err["code"] == "not_found"


def test_update_task_status_warn_recalculates_progress(tmp_path):
    manager = TaskManager(tasks_dir=tmp_path / ".tasks", sync_service=DummySync(enabled=False))
    task = TaskDetail(
        id="TASK-011",
        title="Progress",
        status="FAIL",
        created="2025-01-01 00:00",
        updated="2025-01-01 00:00",
    )
    task.subtasks.append(SubTask(completed=True, title="done", success_criteria=["c"], tests=["t"], blockers=["b"]))
    task.subtasks.append(SubTask(completed=False, title="todo", success_criteria=["c"], tests=["t"], blockers=["b"]))
    manager.repo.save(task)

    ok, err = manager.update_task_status("TASK-011", "WARN")
    assert ok and err is None
    updated = manager.load_task("TASK-011")
    assert updated.status == "WARN"
    assert updated.progress == 50


def test_update_task_status_validation_failure(tmp_path):
    manager = TaskManager(tasks_dir=tmp_path / ".tasks", sync_service=DummySync(enabled=False))
    task = TaskDetail(
        id="TASK-012",
        title="Invalid",
        status="FAIL",
        created="2025-01-01 00:00",
        updated="2025-01-01 00:00",
    )
    task.subtasks.append(SubTask(completed=True, title="child", success_criteria=[], tests=[], blockers=["b"]))
    manager.repo.save(task)

    ok, err = manager.update_task_status("TASK-012", "OK")
    assert ok is False
    assert err and err["code"] == "validation"


def test_add_subtask_missing_fields_and_path(tmp_path):
    manager = TaskManager(tasks_dir=tmp_path / ".tasks", sync_service=DummySync(enabled=False))
    base = TaskDetail(
        id="TASK-013",
        title="Base",
        status="FAIL",
        created="2025-01-01 00:00",
        updated="2025-01-01 00:00",
    )
    manager.repo.save(base)

    ok, err = manager.add_subtask("TASK-013", "No fields")
    assert ok is False and err == "missing_fields"

    ok, err = manager.add_subtask(
        "TASK-013",
        "Wrong path",
        criteria=["c"],
        tests=["t"],
        blockers=["b"],
        parent_path="9",
    )
    assert ok is False and err == "path"


def test_add_subtask_success_nested(tmp_path):
    manager = TaskManager(tasks_dir=tmp_path / ".tasks", sync_service=DummySync(enabled=False))
    base = TaskDetail(
        id="TASK-014",
        title="Base",
        status="FAIL",
        created="2025-01-01 00:00",
        updated="2025-01-01 00:00",
    )
    parent = SubTask(False, "parent", ["c"], ["t"], ["b"])
    base.subtasks.append(parent)
    manager.repo.save(base)

    ok, err = manager.add_subtask(
        "TASK-014",
        "Child",
        criteria=["c1"],
        tests=["t1"],
        blockers=["b1"],
        parent_path="0",
    )
    assert ok and err is None
    reloaded = manager.load_task("TASK-014")
    assert len(reloaded.subtasks[0].children) == 1


def test_add_subtask_success_root(tmp_path):
    manager = TaskManager(tasks_dir=tmp_path / ".tasks", sync_service=DummySync(enabled=False))
    base = TaskDetail(
        id="TASK-015",
        title="Base",
        status="FAIL",
        created="2025-01-01 00:00",
        updated="2025-01-01 00:00",
    )
    manager.repo.save(base)

    ok, err = manager.add_subtask(
        "TASK-015",
        "Root child",
        criteria=["c"],
        tests=["t"],
        blockers=["b"],
    )
    assert ok and err is None
    reloaded = manager.load_task("TASK-015")
    assert len(reloaded.subtasks) == 1


def test_update_task_status_subtask_missing_criteria(tmp_path):
    manager = TaskManager(tasks_dir=tmp_path / ".tasks", sync_service=DummySync(enabled=False))
    task = TaskDetail(
        id="TASK-016",
        title="Criteria check",
        status="WARN",
        created="2025-01-01 00:00",
        updated="2025-01-01 00:00",
    )
    task.success_criteria = ["goal"]
    task.subtasks.append(SubTask(completed=True, title="child", success_criteria=[], tests=["t"], blockers=["b"]))
    manager.repo.save(task)

    ok, err = manager.update_task_status("TASK-016", "OK")
    assert ok is False
    assert err and err["code"] == "validation"


def test_update_task_status_subtask_missing_tests(tmp_path):
    manager = TaskManager(tasks_dir=tmp_path / ".tasks", sync_service=DummySync(enabled=False))
    task = TaskDetail(
        id="TASK-017",
        title="Tests check",
        status="WARN",
        created="2025-01-01 00:00",
        updated="2025-01-01 00:00",
    )
    task.success_criteria = ["goal"]
    task.subtasks.append(SubTask(completed=True, title="child", success_criteria=["c"], tests=[], blockers=["b"]))
    manager.repo.save(task)

    ok, err = manager.update_task_status("TASK-017", "OK")
    assert ok is False
    assert err and err["code"] == "validation"


def test_find_subtask_by_path_invalid_returns_none():
    root = SubTask(False, "root", ["c"], ["t"], ["b"])
    child = SubTask(False, "child", ["c"], ["t"], ["b"])
    root.children.append(child)

    assert task_manager._find_subtask_by_path([root], "invalid") == (None, None, None)
    assert task_manager._find_subtask_by_path([root], "5") == (None, None, None)
    assert task_manager._find_subtask_by_path([root], "0.9") == (None, None, None)
    assert task_manager._find_subtask_by_path([root], "") == (None, None, None)


def test_update_progress_for_status_sets_progress():
    task = TaskDetail(id="TASK-018", title="p", status="FAIL", created="2025-01-01", updated="2025-01-01")
    task.subtasks.append(SubTask(completed=True, title="child", success_criteria=["c"], tests=["t"], blockers=["b"]))
    task_manager._update_progress_for_status(task, "WARN")
    assert task.progress == 100


def test_update_task_status_requires_full_progress(tmp_path):
    manager = TaskManager(tasks_dir=tmp_path / ".tasks", sync_service=DummySync(enabled=False))
    task = TaskDetail(
        id="TASK-019",
        title="Partial",
        status="WARN",
        created="2025-01-01 00:00",
        updated="2025-01-01 00:00",
    )
    task.success_criteria = ["goal"]
    task.subtasks.append(SubTask(completed=False, title="child", success_criteria=["c"], tests=["t"], blockers=["b"]))
    manager.repo.save(task)

    ok, err = manager.update_task_status("TASK-019", "OK")
    assert ok is False
    assert err and err["code"] == "validation"


def test_set_subtask_ready_and_not_ready(tmp_path):
    manager = TaskManager(tasks_dir=tmp_path / ".tasks", sync_service=DummySync(enabled=False))
    task = TaskDetail(
        id="TASK-020",
        title="Has subtasks",
        status="FAIL",
        created="2025-01-01 00:00",
        updated="2025-01-01 00:00",
    )
    task.subtasks.append(
        SubTask(
            completed=False,
            title="child",
            success_criteria=["c"],
            tests=["t"],
            blockers=["b"],
            criteria_confirmed=True,
            tests_confirmed=True,
            blockers_resolved=True,
        )
    )
    manager.repo.save(task)

    ok, err = manager.set_subtask("TASK-020", 0, True)
    assert ok and err is None

    # missing checkpoints
    task.subtasks[0].criteria_confirmed = False
    manager.repo.save(task)
    ok, err = manager.set_subtask("TASK-020", 0, True)
    assert ok is False and err


def test_set_subtask_path_invalid(tmp_path):
    manager = TaskManager(tasks_dir=tmp_path / ".tasks", sync_service=DummySync(enabled=False))
    task = TaskDetail(
        id="TASK-021",
        title="Path",
        status="FAIL",
        created="2025-01-01 00:00",
        updated="2025-01-01 00:00",
    )
    task.subtasks.append(SubTask(False, "child", ["c"], ["t"], ["b"]))
    manager.repo.save(task)
    ok, err = manager.set_subtask("TASK-021", 0, True, path="1")
    assert ok is False and err == "index"


def test_set_subtask_index_out_of_bounds(tmp_path):
    manager = TaskManager(tasks_dir=tmp_path / ".tasks", sync_service=DummySync(enabled=False))
    task = TaskDetail(id="TASK-024", title="Task", status="FAIL", created="2025", updated="2025")
    task.subtasks.append(SubTask(False, "child", ["c"], ["t"], ["b"], criteria_confirmed=True, tests_confirmed=True, blockers_resolved=True))
    manager.repo.save(task)
    ok, err = manager.set_subtask("TASK-024", 5, True)
    assert ok is False and err == "index"


def test_update_subtask_checkpoint_unknown(tmp_path):
    manager = TaskManager(tasks_dir=tmp_path / ".tasks", sync_service=DummySync(enabled=False))
    task = TaskDetail(
        id="TASK-022",
        title="Checkpoint",
        status="FAIL",
        created="2025-01-01 00:00",
        updated="2025-01-01 00:00",
    )
    task.subtasks.append(SubTask(False, "child", ["c"], ["t"], ["b"]))
    manager.repo.save(task)
    ok, err = manager.update_subtask_checkpoint("TASK-022", 0, "unknown", True)
    assert ok is False and err == "unknown_checkpoint"


def test_update_subtask_checkpoint_sets_notes_and_resets_completion(tmp_path):
    manager = TaskManager(tasks_dir=tmp_path / ".tasks", sync_service=DummySync(enabled=False))
    task = TaskDetail(
        id="TASK-023",
        title="Checkpoint ok",
        status="FAIL",
        created="2025-01-01 00:00",
        updated="2025-01-01 00:00",
    )
    st = SubTask(True, "child", ["c"], ["t"], ["b"], criteria_confirmed=True, tests_confirmed=True, blockers_resolved=True)
    task.subtasks.append(st)
    manager.repo.save(task)
    ok, err = manager.update_subtask_checkpoint("TASK-023", 0, "criteria", False, note="note")
    assert ok and err is None
    updated = manager.load_task("TASK-023")
    assert updated.subtasks[0].criteria_notes == ["note"]
    assert updated.subtasks[0].completed is False


def test_clean_tasks_fallback(monkeypatch):
    class FakeRepo:
        def __init__(self):
            self.storage = {}

        def next_id(self):
            return "TASK-100"

        def save(self, task):
            self.storage[(task.id, task.domain)] = task

        def load(self, task_id, domain):
            return self.storage.get((task_id, domain))

        def list(self, domain, skip_sync=False):
            return [t for (_, d), t in self.storage.items() if domain == "" or d == domain]

        def compute_signature(self):
            return len(self.storage)

        def delete(self, task_id, domain):
            return self.storage.pop((task_id, domain), None) is not None

        def clean_filtered(self, *args, **kwargs):
            raise NotImplementedError

    monkeypatch.setattr(task_manager.TaskManager, "load_config", staticmethod(lambda: {"auto_sync": False}))
    repo = FakeRepo()
    manager = TaskManager(tasks_dir=Path(".tasks"), repository=repo, sync_service=DummySync(enabled=False))
    t1 = TaskDetail(id="TASK-200", title="A", status="FAIL", created="2025", updated="2025")
    t1.tags = ["keep"]
    t2 = TaskDetail(id="TASK-201", title="B", status="WARN", created="2025", updated="2025")
    t2.tags = ["zap"]
    repo.save(t1)
    repo.save(t2)

    matched, removed = manager.clean_tasks(tag="zap")
    assert matched == ["TASK-201"] and removed == 1


def test_sanitize_domain_invalid():
    with pytest.raises(ValueError):
        TaskManager.sanitize_domain("../bad")


def test_next_id_fallback(tmp_path, monkeypatch):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    for name in ["TASK-001.task", "TASK-010.task"]:
        (tasks_dir / name).write_text("---\n", encoding="utf-8")
    manager = TaskManager(tasks_dir=tasks_dir, sync_service=DummySync(enabled=False))
    monkeypatch.setattr(manager.repo, "next_id", lambda: (_ for _ in ()).throw(Exception("boom")))
    assert manager._next_id() == "TASK-011"


def test_load_task_sets_ok_and_pulls_sync(monkeypatch, tmp_path):
    class Sync(DummySync):
        def __init__(self):
            super().__init__(enabled=True)
            self.pulled = False

        def pull_task_fields(self, task):
            self.pulled = True

    sync = Sync()
    manager = TaskManager(tasks_dir=tmp_path / ".tasks", sync_service=sync)
    task = TaskDetail(id="TASK-030", title="Demo", status="WARN", created="2025", updated="2025")
    sub = SubTask(True, "done", ["c"], ["t"], ["b"], criteria_confirmed=True, tests_confirmed=True, blockers_resolved=True)
    task.subtasks.append(sub)
    task.project_item_id = "pid"
    manager.repo.save(task)

    loaded = manager.load_task("TASK-030")
    assert loaded.status == "OK"
    assert sync.pulled is True


def test_list_tasks_pulls_sync(monkeypatch, tmp_path):
    class Sync(DummySync):
        def __init__(self):
            super().__init__(enabled=True)

        def pull_task_fields(self, task):
            task.pulled = True

    sync = Sync()
    manager = TaskManager(tasks_dir=tmp_path / ".tasks", sync_service=sync)
    task = TaskDetail(id="TASK-031", title="Demo", status="WARN", created="2025", updated="2025", project_item_id="1")
    manager.repo.save(task)
    tasks = manager.list_tasks()
    assert tasks[0].pulled is True


def test_set_subtask_with_path_success(tmp_path):
    manager = TaskManager(tasks_dir=tmp_path / ".tasks", sync_service=DummySync(enabled=False))
    root = SubTask(False, "root", ["c"], ["t"], ["b"], criteria_confirmed=True, tests_confirmed=True, blockers_resolved=True)
    child = SubTask(False, "child", ["c"], ["t"], ["b"], criteria_confirmed=True, tests_confirmed=True, blockers_resolved=True)
    root.children.append(child)
    task = TaskDetail(id="TASK-032", title="Task", status="FAIL", created="2025", updated="2025")
    task.subtasks.append(root)
    manager.repo.save(task)
    ok, err = manager.set_subtask("TASK-032", 0, True, path="0.0")
    assert ok and err is None


def test_update_subtask_checkpoint_with_path(tmp_path):
    manager = TaskManager(tasks_dir=tmp_path / ".tasks", sync_service=DummySync(enabled=False))
    parent = SubTask(False, "parent", ["c"], ["t"], ["b"])
    child = SubTask(False, "child", ["c"], ["t"], ["b"])
    parent.children.append(child)
    task = TaskDetail(id="TASK-033", title="Task", status="FAIL", created="2025", updated="2025")
    task.subtasks.append(parent)
    manager.repo.save(task)

    ok, err = manager.update_subtask_checkpoint("TASK-033", 0, "tests", True, path="0.0", note="n")
    assert ok and err is None
    updated = manager.load_task("TASK-033")
    assert updated.subtasks[0].children[0].tests_confirmed is True
    assert updated.subtasks[0].children[0].tests_notes == ["n"]


def test_compute_worker_count_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("APPLY_TASK_SYNC_WORKERS", "7")
    manager = TaskManager(tasks_dir=tmp_path / ".tasks", sync_service=DummySync(enabled=False))
    assert manager._compute_worker_count(3) == 7


def test_dependency_and_move_operations(monkeypatch, tmp_path):
    class Repo(FileTaskRepository):
        def __init__(self, base):
            super().__init__(base)
            self.moved = False

        def move(self, task_id, new_domain):
            self.moved = True
            return True

        def move_glob(self, pattern, new_domain):
            self.moved_glob = True
            return 2

    repo = Repo(tmp_path / ".tasks")
    manager = TaskManager(tasks_dir=tmp_path / ".tasks", repository=repo, sync_service=DummySync(enabled=False))
    manager.repo.save(TaskDetail(id="TASK-040", title="x", status="FAIL", created="2025", updated="2025"))
    assert manager.add_dependency("TASK-040", "DEP") is True
    assert manager.move_task("TASK-040", "dom") is True and repo.moved
    assert manager.move_glob("TASK-*", "dom") == 2 and repo.moved_glob


def test_auto_sync_all_short_circuits(monkeypatch, tmp_path):
    monkeypatch.setattr(task_manager.TaskManager, "load_config", staticmethod(lambda: {"auto_sync": False}))
    manager = TaskManager(tasks_dir=tmp_path / ".tasks", sync_service=DummySync(enabled=False))
    assert manager._auto_sync_all() == 0

    manager.config = {"auto_sync": True}
    manager.sync_service.enabled = False
    assert manager._auto_sync_all() == 0


def test_clean_tasks_status_and_phase(monkeypatch):
    monkeypatch.setattr(task_manager.TaskManager, "load_config", staticmethod(lambda: {"auto_sync": False}))

    class FakeRepo:
        def __init__(self):
            self.storage = {}

        def list(self, domain, skip_sync=False):
            return list(self.storage.values())

        def compute_signature(self):
            return 0

        def clean_filtered(self, *args, **kwargs):
            raise NotImplementedError

        def delete(self, task_id, domain):
            return True

        def save(self, task):
            self.storage[task.id] = task

    repo = FakeRepo()
    manager = TaskManager(tasks_dir=Path(".tasks"), repository=repo, sync_service=DummySync(enabled=False))
    t1 = TaskDetail(id="TASK-050", title="X", status="FAIL", phase="p", created="2025", updated="2025")
    t1.tags = ["a"]
    t2 = TaskDetail(id="TASK-051", title="Y", status="WARN", phase="p", created="2025", updated="2025")
    t2.tags = ["b"]
    repo.save(t1)
    repo.save(t2)
    matched, removed = manager.clean_tasks(tag="a", status="FAIL", phase="p")
    assert matched == ["TASK-050"] and removed == 1
