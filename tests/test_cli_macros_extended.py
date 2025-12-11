from types import SimpleNamespace

import pytest

from core.desktop.devtools.interface import cli_macros_extended as macros


def test_cmd_update_uses_last_task(monkeypatch, capsys):
    calls = {}

    class DummyManager:
        def update_task_status(self, task_id, status, domain):
            calls["args"] = (task_id, status, domain)
            return True, None

        def load_task(self, task_id, domain):
            return SimpleNamespace(id=task_id, domain=domain, subtasks=[])

    monkeypatch.setattr(macros, "TaskManager", lambda: DummyManager())
    monkeypatch.setattr(macros, "get_last_task", lambda: ("TASK-123", "dom"))
    monkeypatch.setattr(macros, "derive_domain_explicit", lambda *args, **kwargs: "")
    monkeypatch.setattr(macros, "save_last_task", lambda *args, **kwargs: None)
    monkeypatch.setattr(macros, "task_to_dict", lambda detail, include_subtasks=True: {"id": detail.id, "domain": detail.domain})
    monkeypatch.setattr(macros, "translate", lambda key, **kwargs: key)

    exit_code = macros.cmd_update(SimpleNamespace(arg1="OK", arg2=None, domain=None, phase=None, component=None))
    captured = capsys.readouterr().out
    assert exit_code == 0
    assert "TASK-123" in captured
    assert calls["args"] == ("TASK-123", "OK", "dom")


def test_cmd_note_handles_index_error(monkeypatch, capsys):
    class DummyManager:
        def update_subtask_checkpoint(self, *args, **kwargs):
            return False, "index"

    monkeypatch.setattr(macros, "TaskManager", lambda: DummyManager())
    monkeypatch.setattr(macros, "resolve_task_reference", lambda *args, **kwargs: ("TASK-1", ""))
    monkeypatch.setattr(macros, "translate", lambda key, **kwargs: key)

    exit_code = macros.cmd_note(
        SimpleNamespace(task_id="TASK-1", domain=None, phase=None, component=None, index=0, checkpoint="criteria", undo=False, note="")
    )
    captured = capsys.readouterr().out
    assert exit_code == 1
    assert "ERR_SUBTASK_INDEX" in captured


def test_cmd_ok_success_builds_payload(monkeypatch, capsys):
    checkpoints = []

    class DummyManager:
        def update_subtask_checkpoint(self, task_id, index, checkpoint, value, note, domain):
            checkpoints.append((checkpoint, value, note))
            return True, None

        def set_subtask(self, *args, **kwargs):
            return True, None

        def load_task(self, task_id, domain):
            st = SimpleNamespace(
                criteria_confirmed=True, tests_confirmed=True, blockers_resolved=True, completed=False
            )
            return SimpleNamespace(id=task_id, domain=domain, subtasks=[st])

    monkeypatch.setattr(macros, "TaskManager", lambda: DummyManager())
    monkeypatch.setattr(macros, "resolve_task_reference", lambda *args, **kwargs: ("TASK-2", "dom"))
    monkeypatch.setattr(macros, "translate", lambda key, **kwargs: key)
    monkeypatch.setattr(macros, "task_to_dict", lambda detail, include_subtasks=True: {"id": detail.id})
    monkeypatch.setattr(macros, "subtask_to_dict", lambda st, idx=0: {"completed": True})
    monkeypatch.setattr(macros, "save_last_task", lambda *args, **kwargs: None)

    exit_code = macros.cmd_ok(
        SimpleNamespace(
            task_id=None,
            domain=None,
            phase=None,
            component=None,
            indices="0",  # Changed from index to indices (string)
            all_subtasks=False,
            criteria_note="",
            tests_note="",
            blockers_note="",
        )
    )
    captured = capsys.readouterr().out
    assert exit_code == 0
    assert "subtask#0" in captured
    assert checkpoints == [("criteria", True, ""), ("tests", True, ""), ("blockers", True, "")]


def test_cmd_update_requires_status(monkeypatch, capsys):
    monkeypatch.setattr(macros, "translate", lambda key, **kwargs: key)
    exit_code = macros.cmd_update(SimpleNamespace(arg1="TASK-9", arg2=None, domain=None, phase=None, component=None))
    assert exit_code == 1
    assert "ERR_STATUS_REQUIRED" in capsys.readouterr().out


def test_cmd_update_requires_task(monkeypatch, capsys):
    monkeypatch.setattr(macros, "translate", lambda key, **kwargs: key)
    monkeypatch.setattr(macros, "get_last_task", lambda: (None, None))
    exit_code = macros.cmd_update(SimpleNamespace(arg1="OK", arg2=None, domain=None, phase=None, component=None))
    assert exit_code == 1
    assert "ERR_NO_TASK_AND_LAST" in capsys.readouterr().out


def test_cmd_update_handles_not_found(monkeypatch, capsys):
    class DummyManager:
        def update_task_status(self, *args, **kwargs):
            return False, {"code": "not_found", "message": "oops"}

    monkeypatch.setattr(macros, "TaskManager", lambda: DummyManager())
    monkeypatch.setattr(macros, "translate", lambda key, **kwargs: key)
    monkeypatch.setattr(macros, "derive_domain_explicit", lambda *args, **kwargs: "")
    monkeypatch.setattr(macros, "get_last_task", lambda: (None, ""))
    exit_code = macros.cmd_update(SimpleNamespace(arg1="OK", arg2="TASK-1", domain=None, phase=None, component=None))
    assert exit_code == 1
    assert "oops" in capsys.readouterr().out


def test_cmd_ok_checkpoint_failure(monkeypatch, capsys):
    class DummyManager:
        def update_subtask_checkpoint(self, *args, **kwargs):
            return False, "index"

        def load_task(self, task_id, domain):
            st = SimpleNamespace(completed=False)
            return SimpleNamespace(id=task_id, domain=domain, subtasks=[st, st])

    monkeypatch.setattr(macros, "TaskManager", lambda: DummyManager())
    monkeypatch.setattr(macros, "resolve_task_reference", lambda *args, **kwargs: ("TASK-3", ""))
    monkeypatch.setattr(macros, "translate", lambda key, **kwargs: key)
    monkeypatch.setattr(macros, "task_to_dict", lambda detail, include_subtasks=True: {"id": detail.id})
    monkeypatch.setattr(macros, "save_last_task", lambda *args, **kwargs: None)
    exit_code = macros.cmd_ok(SimpleNamespace(task_id=None, domain=None, phase=None, component=None, indices="1", all_subtasks=False, criteria_note="", tests_note="", blockers_note=""))
    assert exit_code == 1
    # Now returns WARN with partial failure info
    out = capsys.readouterr().out
    assert "criteria: index" in out or "WARN" in out


def test_cmd_ok_set_subtask_failure(monkeypatch, capsys):
    class DummyManager:
        def update_subtask_checkpoint(self, *args, **kwargs):
            return True, None

        def set_subtask(self, *args, **kwargs):
            return False, "boom"

        def load_task(self, task_id, domain):
            st = SimpleNamespace(completed=False)
            return SimpleNamespace(id=task_id, domain=domain, subtasks=[st])

    monkeypatch.setattr(macros, "TaskManager", lambda: DummyManager())
    monkeypatch.setattr(macros, "resolve_task_reference", lambda *args, **kwargs: ("TASK-4", ""))
    monkeypatch.setattr(macros, "task_to_dict", lambda detail, include_subtasks=True: {"id": detail.id})
    monkeypatch.setattr(macros, "save_last_task", lambda *args, **kwargs: None)
    exit_code = macros.cmd_ok(SimpleNamespace(task_id=None, domain=None, phase=None, component=None, indices="0", all_subtasks=False, criteria_note="", tests_note="", blockers_note=""))
    assert exit_code == 1
    assert "boom" in capsys.readouterr().out


def test_cmd_ok_resolve_error(monkeypatch, capsys):
    monkeypatch.setattr(macros, "resolve_task_reference", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bad")))
    exit_code = macros.cmd_ok(SimpleNamespace(task_id=None, domain=None, phase=None, component=None, indices="0", all_subtasks=False, criteria_note="", tests_note="", blockers_note=""))
    assert exit_code == 1
    assert "bad" in capsys.readouterr().out


def test_cmd_note_resolve_error(monkeypatch, capsys):
    monkeypatch.setattr(macros, "resolve_task_reference", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("oops")))
    exit_code = macros.cmd_note(SimpleNamespace(task_id=None, domain=None, phase=None, component=None, index=0, checkpoint="criteria", undo=False, note=""))
    assert exit_code == 1
    assert "oops" in capsys.readouterr().out


def test_cmd_note_success_and_not_found(monkeypatch, capsys):
    class DummyManager:
        def __init__(self, ok=True):
            self.ok = ok

        def update_subtask_checkpoint(self, *args, **kwargs):
            return (True, None) if self.ok else (False, "not_found")

        def load_task(self, *args, **kwargs):
            return SimpleNamespace(id="TASK-5", domain="", subtasks=[SimpleNamespace()])

    monkeypatch.setattr(macros, "TaskManager", lambda: DummyManager(ok=True))
    monkeypatch.setattr(macros, "resolve_task_reference", lambda *args, **kwargs: ("TASK-5", ""))
    monkeypatch.setattr(macros, "task_to_dict", lambda detail, include_subtasks=True: {"id": detail.id})
    monkeypatch.setattr(macros, "subtask_to_dict", lambda st: {"stub": True})
    monkeypatch.setattr(macros, "save_last_task", lambda *args, **kwargs: None)
    monkeypatch.setattr(macros, "translate", lambda key, **kwargs: key)
    ok_exit = macros.cmd_note(SimpleNamespace(task_id="TASK-5", domain=None, phase=None, component=None, index=0, checkpoint="criteria", undo=False, note=""))
    assert ok_exit == 0
    # not_found branch
    monkeypatch.setattr(macros, "TaskManager", lambda: DummyManager(ok=False))
    nf_exit = macros.cmd_note(SimpleNamespace(task_id="TASK-5", domain=None, phase=None, component=None, index=0, checkpoint="criteria", undo=False, note=""))
    assert nf_exit == 1
    output = capsys.readouterr().out
    assert "Задача TASK-5 не найдена" in output


def test_cmd_suggest_with_results(monkeypatch, capsys):
    class DummyManager:
        def list_tasks(self, *args, **kwargs):
            return ["task"]

    monkeypatch.setattr(macros, "TaskManager", lambda: DummyManager())
    monkeypatch.setattr(macros, "derive_domain_explicit", lambda *args, **kwargs: "")
    monkeypatch.setattr(macros, "suggest_tasks", lambda tasks, filters, remember, serializer: ({"suggestions": [1, 2]}, [1]))
    exit_code = macros.cmd_suggest(SimpleNamespace(folder="", domain=None, phase=None, component=None))
    assert exit_code == 0
    assert "2 рекомендаций" in capsys.readouterr().out


def test_cmd_quick_with_results(monkeypatch, capsys):
    class DummyManager:
        def list_tasks(self, *args, **kwargs):
            return ["task"]

    monkeypatch.setattr(macros, "TaskManager", lambda: DummyManager())
    monkeypatch.setattr(macros, "derive_domain_explicit", lambda *args, **kwargs: "")
    monkeypatch.setattr(macros, "quick_overview", lambda tasks, filters, remember, serializer: ({"top": [1, 2]}, [1, 2]))
    exit_code = macros.cmd_quick(SimpleNamespace(folder="", domain=None, phase=None, component=None))
    assert exit_code == 0
    assert "Top-2 задач" in capsys.readouterr().out
