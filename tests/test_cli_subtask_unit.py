from types import SimpleNamespace

from core.desktop.devtools.interface import cli_subtask


def _args(**kwargs):
    defaults = dict(
        task_id="TASK-001",
        domain="",
        phase=None,
        component=None,
        add=None,
        done=None,
        undo=None,
        criteria=None,
        tests=None,
        blockers=None,
        criteria_done=None,
        criteria_undo=None,
        tests_done=None,
        tests_undo=None,
        blockers_done=None,
        blockers_undo=None,
        note=None,
        path=None,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_parse_semicolon_list_empty():
    assert cli_subtask._parse_semicolon_list(None) == []


def test_subtask_requires_single_action(monkeypatch, capsys):
    exit_code = cli_subtask.cmd_subtask(_args(add="x" * 25, done=1))
    assert exit_code == 1
    assert "actions" in capsys.readouterr().out


def test_subtask_add_success_and_missing(monkeypatch, capsys):
    class DummyManager:
        def __init__(self, ok=True):
            self.ok = ok

        def load_task(self, *args, **kwargs):
            return SimpleNamespace(subtasks=[SimpleNamespace()])

        def add_subtask(self, *args, **kwargs):
            return (True, None) if self.ok else (False, "missing_fields")

    monkeypatch.setattr(cli_subtask, "TaskManager", lambda: DummyManager(ok=True))
    monkeypatch.setattr(cli_subtask, "translate", lambda key, **kwargs: key)
    monkeypatch.setattr(cli_subtask, "task_to_dict", lambda detail, include_subtasks=True: {"id": "TASK-001"})
    monkeypatch.setattr(cli_subtask, "subtask_to_dict", lambda st: {"stub": True})
    monkeypatch.setattr(cli_subtask, "_find_subtask_by_path", lambda subtasks, path: (SimpleNamespace(), None, None))
    ok_exit = cli_subtask.cmd_subtask(
        _args(add="A" * 25, criteria="c1;c2", tests="t1", blockers="b1", path="0")
    )
    assert ok_exit == 0
    capsys.readouterr()

    monkeypatch.setattr(cli_subtask, "TaskManager", lambda: DummyManager(ok=False))
    miss_exit = cli_subtask.cmd_subtask(_args(add="A" * 25, criteria="c1", tests="t1", blockers="b1"))
    assert miss_exit == 1
    assert "criteria/tests/blockers" in capsys.readouterr().out


def test_subtask_add_title_too_short(monkeypatch, capsys):
    monkeypatch.setattr(cli_subtask, "translate", lambda key, **kwargs: key)
    exit_code = cli_subtask.cmd_subtask(_args(add="short"))
    assert exit_code == 1
    assert "ERR_SUBTASK_TITLE_MIN" in capsys.readouterr().out


def test_subtask_add_not_found(monkeypatch, capsys):
    class DummyManager:
        def load_task(self, *args, **kwargs):
            return None

        def add_subtask(self, *args, **kwargs):
            return False, "not_found"

    monkeypatch.setattr(cli_subtask, "TaskManager", lambda: DummyManager())
    monkeypatch.setattr(cli_subtask, "translate", lambda key, **kwargs: key)
    exit_code = cli_subtask.cmd_subtask(_args(add="A" * 25, criteria="c1", tests="t1", blockers="b1"))
    assert exit_code == 1
    assert "ERR_TASK_NOT_FOUND" in capsys.readouterr().out


def test_subtask_done_success_and_index(monkeypatch, capsys):
    class DummyManager:
        def __init__(self, ok=True):
            self.ok = ok

        def load_task(self, *args, **kwargs):
            return SimpleNamespace(subtasks=[SimpleNamespace()]) if self.ok else None

        def set_subtask(self, *args, **kwargs):
            return (True, None) if self.ok else (False, "index")

    monkeypatch.setattr(cli_subtask, "TaskManager", lambda: DummyManager(ok=True))
    monkeypatch.setattr(cli_subtask, "subtask_to_dict", lambda st: {"idx": True})
    monkeypatch.setattr(cli_subtask, "task_to_dict", lambda detail, include_subtasks=True: {"id": "TASK-1"})
    ok_exit = cli_subtask.cmd_subtask(_args(done=0))
    assert ok_exit == 0

    monkeypatch.setattr(cli_subtask, "TaskManager", lambda: DummyManager(ok=False))
    monkeypatch.setattr(cli_subtask, "translate", lambda key, **kwargs: key)
    idx_exit = cli_subtask.cmd_subtask(_args(done=1))
    assert idx_exit == 1
    assert "ERR_SUBTASK_INDEX" in capsys.readouterr().out


def test_subtask_done_not_found(monkeypatch, capsys):
    class DummyManager:
        def load_task(self, *args, **kwargs):
            return None

        def set_subtask(self, *args, **kwargs):
            return False, "not_found"

    monkeypatch.setattr(cli_subtask, "TaskManager", lambda: DummyManager())
    exit_code = cli_subtask.cmd_subtask(_args(done=2))
    assert exit_code == 1
    assert "не найдена" in capsys.readouterr().out


def test_subtask_checkpoint_not_found(monkeypatch, capsys):
    class DummyManager:
        def update_subtask_checkpoint(self, *args, **kwargs):
            return False, "not_found"

    monkeypatch.setattr(cli_subtask, "TaskManager", lambda: DummyManager())
    monkeypatch.setattr(cli_subtask, "translate", lambda key, **kwargs: key)
    exit_code = cli_subtask.cmd_subtask(_args(criteria_done=0, note="note"))
    assert exit_code == 1
    assert "не найдена" in capsys.readouterr().out


def test_subtask_blockers_checkpoint_index(monkeypatch, capsys):
    class DummyManager:
        def update_subtask_checkpoint(self, *args, **kwargs):
            return False, "index"

    monkeypatch.setattr(cli_subtask, "TaskManager", lambda: DummyManager())
    monkeypatch.setattr(cli_subtask, "translate", lambda key, **kwargs: key)
    exit_code = cli_subtask.cmd_subtask(_args(blockers_done=0, note="x"))
    assert exit_code == 1
    assert "ERR_SUBTASK_INDEX" in capsys.readouterr().out


def test_subtask_checkpoint_success_with_note(monkeypatch, capsys):
    class DummyManager:
        def load_task(self, *args, **kwargs):
            return SimpleNamespace(subtasks=[], id="TASK-001")

        def update_subtask_checkpoint(self, *args, **kwargs):
            return True, None

    monkeypatch.setattr(cli_subtask, "TaskManager", lambda: DummyManager())
    monkeypatch.setattr(cli_subtask, "translate", lambda key, **kwargs: key)
    monkeypatch.setattr(cli_subtask, "task_to_dict", lambda detail, include_subtasks=True: {"id": detail.id})
    exit_code = cli_subtask.cmd_subtask(_args(tests_done=0, note="note"))
    assert exit_code == 0
    output = capsys.readouterr().out
    assert "note" in output
