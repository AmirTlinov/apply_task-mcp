from types import SimpleNamespace
import json

import pytest

from core.subtask import SubTask
from core.desktop.devtools.interface import cli_checkpoint
from core.desktop.devtools.interface.subtask_loader import SubtaskParseError


def test_parse_bulk_operations_happy_path():
    raw = '[{"task":"T-1","index":0,"criteria":{"done":true}}]'
    result = cli_checkpoint._parse_bulk_operations(raw)
    assert result == [{"task": "T-1", "index": 0, "criteria": {"done": True}}]


def test_parse_bulk_operations_invalid_shape():
    with pytest.raises(SubtaskParseError):
        cli_checkpoint._parse_bulk_operations('{"task":"bad"}')
    with pytest.raises(SubtaskParseError):
        cli_checkpoint._parse_bulk_operations('["bad"]')


def test_cmd_bulk_updates(monkeypatch, capsys):
    operations = '[{"task":"T-1","index":0,"criteria":{"done":true},"complete":true}]'

    class FakeManager:
        def __init__(self):
            self.updated = []
            self.completed = []

        def update_subtask_checkpoint(self, task_id, index, checkpoint, done, note, domain, path=None):
            self.updated.append((task_id, index, checkpoint, done, note, domain, path))
            return True, ""

        def set_subtask(self, task_id, index, done, domain, path=None):
            self.completed.append((task_id, index, done, domain, path))
            return True, ""

        def load_task(self, task_id, domain):
            return None

    fake_manager = FakeManager()
    monkeypatch.setattr(cli_checkpoint, "TaskManager", lambda: fake_manager)
    monkeypatch.setattr(cli_checkpoint, "resolve_task_reference", lambda *a, **k: ("T-1", "dom"))
    monkeypatch.setattr(cli_checkpoint, "derive_domain_explicit", lambda *a, **k: "dom")
    monkeypatch.setattr(cli_checkpoint, "save_last_task", lambda *a, **k: None)
    monkeypatch.setattr(cli_checkpoint, "_load_input_source", lambda *a, **k: operations)

    args = SimpleNamespace(input="inline", task=None, domain=None, phase=None, component=None)

    exit_code = cli_checkpoint.cmd_bulk(args)
    out = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert out["status"] == "OK"
    assert out["payload"]["results"][0]["status"] == "OK"
    assert fake_manager.updated and fake_manager.completed


def test_cmd_checkpoint_auto(monkeypatch, capsys):
    st = SubTask(False, "A very descriptive subtask title", ["c"], ["t"], ["b"])
    detail = SimpleNamespace(subtasks=[st])

    class FakeManager:
        def __init__(self):
            self.updated = []
            self.completed = []

        def load_task(self, task_id, domain):
            return detail

        def update_subtask_checkpoint(self, task_id, index, checkpoint, done, note, domain, path=None):
            self.updated.append((checkpoint, note))
            return True, ""

        def set_subtask(self, task_id, index, done, domain, path=None):
            self.completed.append((index, done, path))
            return True, ""

    fake_manager = FakeManager()
    monkeypatch.setattr(cli_checkpoint, "TaskManager", lambda: fake_manager)
    monkeypatch.setattr(cli_checkpoint, "resolve_task_reference", lambda *a, **k: ("T-1", "dom"))
    monkeypatch.setattr(cli_checkpoint, "save_last_task", lambda *a, **k: None)
    monkeypatch.setattr(cli_checkpoint, "_flatten_subtasks", lambda subs: [("0", subs[0])])
    monkeypatch.setattr(cli_checkpoint, "task_to_dict", lambda *a, **k: {"id": "T-1"})
    monkeypatch.setattr(cli_checkpoint, "subtask_to_dict", lambda st: {"title": st.title})
    monkeypatch.setattr(
        cli_checkpoint,
        "_find_subtask_by_path",
        lambda subs, path: (subs[0], None, None) if path == "0" else (None, None, None),
    )

    args = SimpleNamespace(
        task_id=None, domain=None, phase=None, component=None, auto=True, subtask=None, path=None, note="auto-note"
    )

    exit_code = cli_checkpoint.cmd_checkpoint(args)
    out = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert out["status"] == "OK"
    assert out["payload"]["completed"] is True
    assert out["payload"]["subtask_index"] == 0
    assert fake_manager.completed
    assert len(fake_manager.updated) == 3  # criteria/tests/blockers


def test_cmd_bulk_invalid_json(monkeypatch, capsys):
    monkeypatch.setattr(cli_checkpoint, "_load_input_source", lambda *a, **k: "{")
    args = SimpleNamespace(input="inline", task=None, domain=None, phase=None, component=None)

    exit_code = cli_checkpoint.cmd_bulk(args)
    out = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert out["status"] == "ERROR"


def test_cmd_bulk_update_failure(monkeypatch, capsys):
    operations = '[{"task":"T-1","index":0,"criteria":{"done":true}}]'

    class FailManager:
        def update_subtask_checkpoint(self, *a, **k):
            return False, "nope"

        def set_subtask(self, *a, **k):
            return True, ""

        def load_task(self, *a, **k):
            return None

    monkeypatch.setattr(cli_checkpoint, "TaskManager", FailManager)
    monkeypatch.setattr(cli_checkpoint, "resolve_task_reference", lambda *a, **k: ("T-1", "dom"))
    monkeypatch.setattr(cli_checkpoint, "derive_domain_explicit", lambda *a, **k: "dom")
    monkeypatch.setattr(cli_checkpoint, "save_last_task", lambda *a, **k: None)
    monkeypatch.setattr(cli_checkpoint, "_load_input_source", lambda *a, **k: operations)

    args = SimpleNamespace(input="inline", task=None, domain=None, phase=None, component=None)

    exit_code = cli_checkpoint.cmd_bulk(args)
    out = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert out["payload"]["results"][0]["status"] == "ERROR"


def test_cmd_checkpoint_requires_interactive(monkeypatch, capsys):
    monkeypatch.setattr(cli_checkpoint, "is_interactive", lambda: False)
    args = SimpleNamespace(
        task_id="T-1", domain=None, phase=None, component=None, auto=False, subtask=None, path=None, note=""
    )

    exit_code = cli_checkpoint.cmd_checkpoint(args)
    out = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert out["status"] == "ERROR"


def test_cmd_checkpoint_skips_confirmed(monkeypatch, capsys):
    st = SubTask(
        False,
        "A sufficiently long title for flagship",
        ["c"],
        ["t"],
        ["b"],
        criteria_confirmed=True,
        tests_confirmed=False,
        blockers_resolved=False,
    )
    detail = SimpleNamespace(subtasks=[st])

    class FakeManager:
        def __init__(self):
            self.updated = []
            self.completed = []

        def load_task(self, task_id, domain):
            return detail

        def update_subtask_checkpoint(self, task_id, index, checkpoint, done, note, domain, path=None):
            self.updated.append(checkpoint)
            return True, ""

        def set_subtask(self, task_id, index, done, domain, path=None):
            self.completed.append(index)
            return True, ""

    monkeypatch.setattr(cli_checkpoint, "TaskManager", lambda: FakeManager())
    monkeypatch.setattr(cli_checkpoint, "resolve_task_reference", lambda *a, **k: ("T-1", "dom"))
    monkeypatch.setattr(cli_checkpoint, "save_last_task", lambda *a, **k: None)
    monkeypatch.setattr(cli_checkpoint, "task_to_dict", lambda *a, **k: {"id": "T-1"})
    monkeypatch.setattr(cli_checkpoint, "subtask_to_dict", lambda st: {"title": st.title})
    monkeypatch.setattr(cli_checkpoint, "_flatten_subtasks", lambda subs: [("0", subs[0])])
    monkeypatch.setattr(
        cli_checkpoint,
        "_find_subtask_by_path",
        lambda subs, path: (subs[0], None, None) if path == "0" else (None, None, None),
    )

    args = SimpleNamespace(
        task_id=None, domain=None, phase=None, component=None, auto=True, subtask=None, path=None, note=""
    )
    exit_code = cli_checkpoint.cmd_checkpoint(args)
    out = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    # first checkpoint already confirmed, rest updated
    assert out["payload"]["operations"][0]["state"] == "already"
    assert out["payload"]["completed"] is True


def test_cmd_bulk_resolve_task_failure(monkeypatch, capsys):
    monkeypatch.setattr(cli_checkpoint, "resolve_task_reference", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad")))
    args = SimpleNamespace(input="inline", task="oops", domain=None, phase=None, component=None)
    exit_code = cli_checkpoint.cmd_bulk(args)
    out = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert out["status"] == "ERROR"


def test_cmd_bulk_uses_default_task(monkeypatch, capsys):
    operations = '[{"index":0,"criteria":{"done":true}}]'

    class Manager:
        def __init__(self):
            self.updated = 0

        def update_subtask_checkpoint(self, *a, **k):
            self.updated += 1
            return True, ""

        def set_subtask(self, *a, **k):
            return True, ""

        def load_task(self, *a, **k):
            return None

    monkeypatch.setattr(cli_checkpoint, "TaskManager", Manager)
    monkeypatch.setattr(cli_checkpoint, "resolve_task_reference", lambda *a, **k: ("DEF", "dom"))
    monkeypatch.setattr(cli_checkpoint, "derive_domain_explicit", lambda *a, **k: "dom")
    monkeypatch.setattr(cli_checkpoint, "save_last_task", lambda *a, **k: None)
    monkeypatch.setattr(cli_checkpoint, "_load_input_source", lambda *a, **k: operations)

    args = SimpleNamespace(input="inline", task="default", domain=None, phase=None, component=None)
    exit_code = cli_checkpoint.cmd_bulk(args)
    out = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert out["payload"]["results"][0]["task"] == "DEF"


def test_cmd_bulk_set_subtask_failure(monkeypatch, capsys):
    operations = '[{"task":"T-1","index":0,"criteria":{"done":true},"complete":true}]'

    class Manager:
        def update_subtask_checkpoint(self, *a, **k):
            return True, ""

        def set_subtask(self, *a, **k):
            return False, "boom"

        def load_task(self, *a, **k):
            return None

    monkeypatch.setattr(cli_checkpoint, "TaskManager", Manager)
    monkeypatch.setattr(cli_checkpoint, "resolve_task_reference", lambda *a, **k: ("T-1", "dom"))
    monkeypatch.setattr(cli_checkpoint, "derive_domain_explicit", lambda *a, **k: "dom")
    monkeypatch.setattr(cli_checkpoint, "save_last_task", lambda *a, **k: None)
    monkeypatch.setattr(cli_checkpoint, "_load_input_source", lambda *a, **k: operations)

    args = SimpleNamespace(input="inline", task=None, domain=None, phase=None, component=None)
    exit_code = cli_checkpoint.cmd_bulk(args)
    out = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert out["payload"]["results"][0]["status"] == "ERROR"


def test_cmd_checkpoint_resolve_failure(monkeypatch, capsys):
    monkeypatch.setattr(cli_checkpoint, "resolve_task_reference", lambda *a, **k: (_ for _ in ()).throw(ValueError("nope")))
    args = SimpleNamespace(
        task_id=None, domain=None, phase=None, component=None, auto=True, subtask=None, path=None, note=""
    )
    exit_code = cli_checkpoint.cmd_checkpoint(args)
    out = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert out["status"] == "ERROR"


def test_cmd_checkpoint_missing_task(monkeypatch, capsys):
    class Manager:
        def load_task(self, *a, **k):
            return None

    monkeypatch.setattr(cli_checkpoint, "TaskManager", Manager)
    monkeypatch.setattr(cli_checkpoint, "resolve_task_reference", lambda *a, **k: ("T-1", "dom"))
    args = SimpleNamespace(
        task_id=None, domain=None, phase=None, component=None, auto=True, subtask=None, path=None, note=""
    )
    exit_code = cli_checkpoint.cmd_checkpoint(args)
    out = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert out["status"] == "ERROR"


def test_cmd_checkpoint_no_subtasks(monkeypatch, capsys):
    class Manager:
        def load_task(self, *a, **k):
            return SimpleNamespace(subtasks=[])

    monkeypatch.setattr(cli_checkpoint, "TaskManager", Manager)
    monkeypatch.setattr(cli_checkpoint, "resolve_task_reference", lambda *a, **k: ("T-1", "dom"))
    args = SimpleNamespace(
        task_id=None, domain=None, phase=None, component=None, auto=True, subtask=None, path=None, note=""
    )
    exit_code = cli_checkpoint.cmd_checkpoint(args)
    out = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert out["status"] == "ERROR"


def test_cmd_checkpoint_invalid_path(monkeypatch, capsys):
    st = SubTask(False, "Valid title for path test", ["c"], ["t"], ["b"])
    detail = SimpleNamespace(subtasks=[st])

    class Manager:
        def load_task(self, *a, **k):
            return detail

    monkeypatch.setattr(cli_checkpoint, "TaskManager", Manager)
    monkeypatch.setattr(cli_checkpoint, "resolve_task_reference", lambda *a, **k: ("T-1", "dom"))
    monkeypatch.setattr(cli_checkpoint, "_find_subtask_by_path", lambda subs, path: (None, None, None))

    args = SimpleNamespace(
        task_id=None, domain=None, phase=None, component=None, auto=True, subtask=None, path="9.9", note=""
    )
    exit_code = cli_checkpoint.cmd_checkpoint(args)
    out = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert out["status"] == "ERROR"


def test_cmd_checkpoint_set_subtask_failure(monkeypatch, capsys):
    st = SubTask(False, "Another long title for failure", ["c"], ["t"], ["b"])
    detail = SimpleNamespace(subtasks=[st])

    class Manager:
        def __init__(self):
            self.updated = 0

        def load_task(self, *a, **k):
            return detail

        def update_subtask_checkpoint(self, *a, **k):
            self.updated += 1
            return True, ""

        def set_subtask(self, *a, **k):
            return False, "fail"

    monkeypatch.setattr(cli_checkpoint, "TaskManager", Manager)
    monkeypatch.setattr(cli_checkpoint, "resolve_task_reference", lambda *a, **k: ("T-1", "dom"))
    monkeypatch.setattr(cli_checkpoint, "save_last_task", lambda *a, **k: None)
    monkeypatch.setattr(cli_checkpoint, "task_to_dict", lambda *a, **k: {"id": "T-1"})
    monkeypatch.setattr(cli_checkpoint, "subtask_to_dict", lambda st: {"title": st.title})
    monkeypatch.setattr(cli_checkpoint, "_flatten_subtasks", lambda subs: [("0", subs[0])])
    monkeypatch.setattr(
        cli_checkpoint,
        "_find_subtask_by_path",
        lambda subs, path: (subs[0], None, None) if path == "0" else (None, None, None),
    )

    args = SimpleNamespace(
        task_id=None, domain=None, phase=None, component=None, auto=True, subtask=None, path=None, note=""
    )
    exit_code = cli_checkpoint.cmd_checkpoint(args)
    out = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert out["status"] == "ERROR"


def test_cmd_bulk_missing_task(monkeypatch, capsys):
    operations = '[{"index":0,"criteria":{"done":true}}]'
    monkeypatch.setattr(cli_checkpoint, "_load_input_source", lambda *a, **k: operations)
    monkeypatch.setattr(cli_checkpoint, "derive_domain_explicit", lambda *a, **k: "dom")
    args = SimpleNamespace(input="inline", task=None, domain=None, phase=None, component=None)
    exit_code = cli_checkpoint.cmd_bulk(args)
    out = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert out["payload"]["results"][0]["status"] == "ERROR"


def test_cmd_bulk_includes_checkpoint_states(monkeypatch, capsys):
    st = SubTask(False, "long enough title for states", ["c"], ["t"], ["b"], True, True, False)

    class Manager:
        def update_subtask_checkpoint(self, *a, **k):
            return True, ""

        def set_subtask(self, *a, **k):
            return True, ""

        def load_task(self, *a, **k):
            return SimpleNamespace(subtasks=[st])

    monkeypatch.setattr(cli_checkpoint, "TaskManager", Manager)
    monkeypatch.setattr(cli_checkpoint, "resolve_task_reference", lambda *a, **k: ("T-1", "dom"))
    monkeypatch.setattr(cli_checkpoint, "derive_domain_explicit", lambda *a, **k: "dom")
    monkeypatch.setattr(cli_checkpoint, "save_last_task", lambda *a, **k: None)
    monkeypatch.setattr(cli_checkpoint, "_load_input_source", lambda *a, **k: '[{"task":"T-1","index":0,"criteria":{"done":true}}]')
    monkeypatch.setattr(cli_checkpoint, "task_to_dict", lambda *a, **k: {"id": "T-1"})
    monkeypatch.setattr(cli_checkpoint, "subtask_to_dict", lambda st: {"title": st.title})

    args = SimpleNamespace(input="inline", task=None, domain=None, phase=None, component=None)
    exit_code = cli_checkpoint.cmd_bulk(args)
    out = json.loads(capsys.readouterr().out)
    states = out["payload"]["results"][0]["checkpoint_states"]
    assert exit_code == 0
    assert states["criteria"] is True and states["tests"] is True and states["blockers"] is False


def test_cmd_checkpoint_with_subtask_index(monkeypatch, capsys):
    st = SubTask(False, "Descriptive subtask title for index", ["c"], ["t"], ["b"])
    detail = SimpleNamespace(subtasks=[st])

    class Manager:
        def __init__(self):
            self.updated = 0

        def load_task(self, *a, **k):
            return detail

        def update_subtask_checkpoint(self, *a, **k):
            self.updated += 1
            return True, ""

        def set_subtask(self, *a, **k):
            return True, ""

    monkeypatch.setattr(cli_checkpoint, "TaskManager", Manager)
    monkeypatch.setattr(cli_checkpoint, "resolve_task_reference", lambda *a, **k: ("T-1", "dom"))
    monkeypatch.setattr(cli_checkpoint, "save_last_task", lambda *a, **k: None)
    monkeypatch.setattr(cli_checkpoint, "task_to_dict", lambda *a, **k: {"id": "T-1"})
    monkeypatch.setattr(cli_checkpoint, "subtask_to_dict", lambda st: {"title": st.title})
    monkeypatch.setattr(cli_checkpoint, "_find_subtask_by_path", lambda subs, path: (subs[0], None, None))

    args = SimpleNamespace(
        task_id=None, domain=None, phase=None, component=None, auto=True, subtask=0, path=None, note="n"
    )
    exit_code = cli_checkpoint.cmd_checkpoint(args)
    out = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert out["payload"]["subtask_path"] == "0"


def test_cmd_checkpoint_interactive_skips(monkeypatch, capsys):
    st = SubTask(False, "Interactive path subtask title long", ["c"], ["t"], ["b"])
    detail = SimpleNamespace(subtasks=[st])

    class Manager:
        def load_task(self, *a, **k):
            return detail

        def update_subtask_checkpoint(self, *a, **k):
            return True, ""

        def set_subtask(self, *a, **k):
            return True, ""

    monkeypatch.setattr(cli_checkpoint, "TaskManager", Manager)
    monkeypatch.setattr(cli_checkpoint, "resolve_task_reference", lambda *a, **k: ("T-1", "dom"))
    monkeypatch.setattr(cli_checkpoint, "save_last_task", lambda *a, **k: None)
    monkeypatch.setattr(cli_checkpoint, "task_to_dict", lambda *a, **k: {"id": "T-1"})
    monkeypatch.setattr(cli_checkpoint, "subtask_to_dict", lambda st: {"title": st.title})
    monkeypatch.setattr(cli_checkpoint, "_flatten_subtasks", lambda subs: [("0", subs[0])])
    monkeypatch.setattr(cli_checkpoint, "_find_subtask_by_path", lambda subs, path: (subs[0], None, None))
    monkeypatch.setattr(cli_checkpoint, "is_interactive", lambda: True)

    prompts = iter(["0", "n", "n", "n", "n"])
    monkeypatch.setattr(cli_checkpoint, "prompt", lambda *a, **k: next(prompts))

    args = SimpleNamespace(
        task_id=None, domain=None, phase=None, component=None, auto=False, subtask=None, path=None, note=""
    )
    exit_code = cli_checkpoint.cmd_checkpoint(args)
    raw = capsys.readouterr().out
    out = json.loads(raw[raw.find("{") :])
    assert exit_code == 0
    assert out["payload"]["operations"][0]["state"] == "skip"


def test_cmd_checkpoint_update_failure(monkeypatch, capsys):
    st = SubTask(False, "Checkpoint fails update title long", ["c"], ["t"], ["b"])
    detail = SimpleNamespace(subtasks=[st])

    class Manager:
        def load_task(self, *a, **k):
            return detail

        def update_subtask_checkpoint(self, *a, **k):
            return False, "fail"

        def set_subtask(self, *a, **k):
            return True, ""

    monkeypatch.setattr(cli_checkpoint, "TaskManager", Manager)
    monkeypatch.setattr(cli_checkpoint, "resolve_task_reference", lambda *a, **k: ("T-1", "dom"))
    monkeypatch.setattr(cli_checkpoint, "save_last_task", lambda *a, **k: None)
    monkeypatch.setattr(cli_checkpoint, "task_to_dict", lambda *a, **k: {"id": "T-1"})
    monkeypatch.setattr(cli_checkpoint, "subtask_to_dict", lambda st: {"title": st.title})
    monkeypatch.setattr(cli_checkpoint, "_flatten_subtasks", lambda subs: [("0", subs[0])])
    monkeypatch.setattr(cli_checkpoint, "_find_subtask_by_path", lambda subs, path: (subs[0], None, None))

    args = SimpleNamespace(
        task_id=None, domain=None, phase=None, component=None, auto=True, subtask=None, path=None, note=""
    )
    exit_code = cli_checkpoint.cmd_checkpoint(args)
    out = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert out["status"] == "ERROR"
