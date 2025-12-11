import json
from types import SimpleNamespace

import pytest

from core import SubTask, TaskDetail
from core.desktop.devtools.interface import cli_create
from core.desktop.devtools.interface import subtask_loader
from core.desktop.devtools.interface import subtask_validation


class DummyManager:
    def __init__(self):
        self.saved = False
        self.created = []
        self.config = {"templates": {"default": {"description": "", "tests": ""}}}

    def create_task(self, title, status, priority, parent, domain, phase, component):
        task = TaskDetail(id="T-1", title=title, status=status, priority=priority, domain=domain, phase=phase, component=component, parent=parent)
        self.created.append(task)
        return task

    def save_task(self, task):
        self.saved = True


def make_args(**kwargs):
    defaults = dict(
        title="Demo task title",
        status="FAIL",
        priority="MEDIUM",
        parent="",
        domain="",
        phase="",
        component="",
        description="Meaningful description",
        context="",
        tags="",
        subtasks=None,
        dependencies="",
        next_steps="",
        tests="Acceptance",
        risks="Risk item",
        validate_only=False,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def stub_flagship_ok(monkeypatch):
    monkeypatch.setattr(cli_create, "validate_flagship_subtasks", lambda subtasks: (True, []))


def test_cmd_create_succeeds_and_saves(monkeypatch):
    dummy = DummyManager()
    monkeypatch.setattr(cli_create, "TaskManager", lambda: dummy)
    stub_flagship_ok(monkeypatch)
    args = make_args()

    rc = cli_create.cmd_create(args)

    assert rc == 0
    assert dummy.saved
    assert dummy.created[-1].description == args.description


def test_cmd_create_validate_only_does_not_save(monkeypatch):
    dummy = DummyManager()
    monkeypatch.setattr(cli_create, "TaskManager", lambda: dummy)
    stub_flagship_ok(monkeypatch)
    args = make_args(validate_only=True)

    rc = cli_create.cmd_create(args)

    assert rc == 0
    assert dummy.saved is False  # preview only


def test_cmd_create_fails_on_missing_description(monkeypatch):
    dummy = DummyManager()
    monkeypatch.setattr(cli_create, "TaskManager", lambda: dummy)
    stub_flagship_ok(monkeypatch)
    args = make_args(description="TBD")

    rc = cli_create.cmd_create(args)

    assert rc != 0
    assert dummy.saved is False


def test_cmd_create_uses_subtasks_payload(monkeypatch, tmp_path):
    dummy = DummyManager()
    monkeypatch.setattr(cli_create, "TaskManager", lambda: dummy)
    stub_flagship_ok(monkeypatch)
    subtasks_payload = json.dumps([
        {"title": "Subtask long enough 12345", "criteria": ["c"], "tests": ["t"], "blockers": ["b"]}
    ])
    args = make_args(subtasks="@" + str(tmp_path / "subs.json"))
    (tmp_path / "subs.json").write_text(subtasks_payload, encoding="utf-8")

    rc = cli_create.cmd_create(args)

    assert rc == 0
    assert dummy.created[-1].subtasks


def test_cmd_smart_create_uses_template(monkeypatch):
    dummy = DummyManager()
    dummy.config["templates"]["default"] = {"description": "tpl desc", "tests": "tpl tests"}
    monkeypatch.setattr(cli_create, "TaskManager", lambda: dummy)
    monkeypatch.setattr(cli_create, "load_template", lambda kind, mgr: ("tpl desc", "tpl tests"))
    stub_flagship_ok(monkeypatch)
    args = make_args(parent="P-1", description="D", tests="", tags="", subtasks=None)
    args.title = "Smart input"

    rc = cli_create.cmd_smart_create(args)

    assert rc == 0
    saved = dummy.created[-1]
    assert saved.description == "D"


def test_cmd_smart_create_requires_parent(monkeypatch):
    dummy = DummyManager()
    monkeypatch.setattr(cli_create, "TaskManager", lambda: dummy)
    args = make_args(parent="")
    rc = cli_create.cmd_smart_create(args)
    assert rc != 0


def test_cmd_create_flagship_validation_fails(monkeypatch):
    dummy = DummyManager()
    monkeypatch.setattr(cli_create, "TaskManager", lambda: dummy)
    monkeypatch.setattr(cli_create, "validate_flagship_subtasks", lambda subtasks: (False, ["issue1"]))
    args = make_args()
    rc = cli_create.cmd_create(args)
    assert rc != 0
    assert dummy.saved is False


def test_cmd_create_subtasks_parse_error(monkeypatch):
    dummy = DummyManager()
    monkeypatch.setattr(cli_create, "TaskManager", lambda: dummy)
    stub_flagship_ok(monkeypatch)
    monkeypatch.setattr(cli_create, "load_subtasks_source", lambda raw: "not json")
    args = make_args(subtasks="inline")
    rc = cli_create.cmd_create(args)
    assert rc != 0


def test_cmd_create_missing_risks(monkeypatch):
    dummy = DummyManager()
    monkeypatch.setattr(cli_create, "TaskManager", lambda: dummy)
    stub_flagship_ok(monkeypatch)
    args = make_args(risks="")
    rc = cli_create.cmd_create(args)
    assert rc != 0
    assert dummy.saved is False


def test_cmd_create_validation_only_failure(monkeypatch, capsys):
    dummy = DummyManager()
    monkeypatch.setattr(cli_create, "TaskManager", lambda: dummy)
    stub_flagship_ok(monkeypatch)
    args = make_args(description="TBD", validate_only=True)

    rc = cli_create.cmd_create(args)
    out = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert out["status"] == "ERROR"


def test_cmd_smart_create_validate_only(monkeypatch, capsys):
    dummy = DummyManager()
    monkeypatch.setattr(cli_create, "TaskManager", lambda: dummy)
    stub_flagship_ok(monkeypatch)
    monkeypatch.setattr(cli_create, "load_template", lambda kind, mgr: ("desc tpl", "tests tpl"))
    args = make_args(parent="P-1", validate_only=True, description="Base desc")
    args.title = "Smart title"

    rc = cli_create.cmd_smart_create(args)
    out = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert out["status"] == "OK"
    assert out["payload"]["task"]["title"] == "Smart title"


def test_cmd_create_populates_deps_and_steps(monkeypatch):
    dummy = DummyManager()
    monkeypatch.setattr(cli_create, "TaskManager", lambda: dummy)
    stub_flagship_ok(monkeypatch)
    args = make_args(dependencies="A,B", next_steps="do1;do2")

    rc = cli_create.cmd_create(args)

    assert rc == 0
    saved = dummy.created[-1]
    assert saved.dependencies == ["A", "B"]
    assert saved.next_steps == ["do1", "do2"]


def test_parse_subtasks_flexible_rejects_invalid_json():
    with pytest.raises(subtask_loader.SubtaskParseError):
        subtask_loader.parse_subtasks_flexible("not json")


def test_validate_flagship_subtasks_requires_minimum_items():
    ok, issues = subtask_loader.validate_flagship_subtasks([])
    assert ok is False
    assert issues


def test_subtask_validation_quality_and_structure():
    subtasks = [
        SubTask(False, "Контекст: because reasons long text 12345", ["c"], ["t"], ["b"]),
        SubTask(False, "Критерии: done anchor long text 67890", ["c"], ["t"], ["b"]),
        SubTask(False, "Тесты: verify anchor long text 13579", ["c"], ["t"], ["b"]),
        SubTask(False, "Блокеры и risks: anchor long text 24680", ["c"], ["t"], ["b"]),
    ]
    ok_cov, _ = subtask_validation.validate_subtasks_coverage(subtasks)
    ok_struct, _ = subtask_validation.validate_subtasks_structure(subtasks)
    ok_quality, _ = subtask_validation.validate_subtasks_quality(subtasks)
    assert ok_cov and ok_struct
    assert ok_quality
