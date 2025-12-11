import json
from types import SimpleNamespace
from pathlib import Path

from core.desktop.devtools.interface import cli_guided


def test_cmd_create_guided_non_interactive(monkeypatch, capsys):
    monkeypatch.setattr(cli_guided, "is_interactive", lambda: False)
    rc = cli_guided.cmd_create_guided(SimpleNamespace(domain=None, phase=None, component=None, priority="MEDIUM"))
    assert rc == 1
    out = capsys.readouterr().out
    assert "Wizard available" in out


def test_cmd_automation_task_create_validate(monkeypatch, tmp_path, capsys):
    from core.desktop.devtools.interface.tasks_app import _automation_template_payload

    payload = _automation_template_payload(3, 80, "risk", "sla")
    path = tmp_path / "subtasks.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    args = SimpleNamespace(
        parent="TASK-001",
        title="My title",
        description="desc",
        subtasks=f"@{path}",
        domain="",
        phase=None,
        component=None,
        count=3,
        coverage=80,
        risks="risk",
        sla="sla",
        tests=None,
        risks_note=None,
        note=None,
        validate_only=True,
        apply=False,
        dry_run=False,
    )
    rc = cli_guided.cmd_automation_task_create(args)
    assert rc == 0
    body = json.loads(capsys.readouterr().out)
    assert body["status"] == "OK"
    assert "automation.task-create.validate" in body["command"]


def test_cmd_automation_checkpoint_ok(monkeypatch, tmp_path, capsys):
    calls = {}

    class DummyManager:
        def __init__(self, *_, **__):
            pass

        def update_subtask_checkpoint(self, *args, **kwargs):
            calls.setdefault("chk", []).append(args)
            return True, None

        def set_subtask(self, *args, **kwargs):
            calls.setdefault("set", []).append(args)
            return True, None

        def load_task(self, *_, **__):
            return SimpleNamespace(
                id="TASK-1",
                title="T",
                status="FAIL",
                priority="LOW",
                domain="",
                phase="",
                component="",
                parent="P",
                tags=[],
                assignee=None,
                blocked=False,
                blockers=[],
                description="d",
                context="",
                success_criteria=[],
                dependencies=[],
                next_steps=[],
                problems=[],
                risks=[],
                history=[],
                subtasks=[],
                project_remote_updated=False,
                calculate_progress=lambda: 0,
            )

    monkeypatch.setattr(cli_guided, "TaskManager", DummyManager)
    args = SimpleNamespace(task_id="TASK-1", index=0, mode="ok", note=None, log=None, checkpoint="criteria", domain=None, phase=None, component=None, tasks_dir=tmp_path / ".tasks")
    rc = cli_guided.cmd_automation_checkpoint(args)
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "OK"
    assert len(calls.get("set", [])) == 1


def test_cmd_create_guided_happy_flow(monkeypatch, capsys):
    # simulate interactive path with simple stubs
    monkeypatch.setattr(cli_guided, "is_interactive", lambda: True)
    monkeypatch.setattr(cli_guided, "translate", lambda key, **kwargs: key)
    seq = iter([
        "Title",
        "TASK-001",
        "Desc long enough",
    ])
    monkeypatch.setattr(cli_guided, "prompt_required", lambda *_: next(seq))
    monkeypatch.setattr(cli_guided, "prompt", lambda *_, **__: "ctx")
    monkeypatch.setattr(cli_guided, "prompt_list", lambda *_, **__: ["item"])
    monkeypatch.setattr(cli_guided, "confirm", lambda *_, **__: False)
    monkeypatch.setattr(cli_guided, "validate_flagship_subtasks", lambda subtasks: (True, []))
    monkeypatch.setattr(cli_guided, "prompt_subtask_interactive", lambda idx: SimpleNamespace(title=f"Subtask {idx}", criteria_confirmed=True, tests_confirmed=True, blockers_resolved=True))

    class DummyManager:
        def __init__(self):
            self.saved = None

        def create_task(self, *_, **__):
            task = SimpleNamespace(id="TASK-123", title="Title", domain="", parent="TASK-001", subtasks=[], update_status_from_progress=lambda: None)
            task.success_criteria = []
            task.risks = []
            task.tags = []
            task.context = ""
            return task

        def save_task(self, task):
            self.saved = task

    monkeypatch.setattr(cli_guided, "TaskManager", lambda: DummyManager())
    monkeypatch.setattr(cli_guided, "save_last_task", lambda *_, **__: None)
    rc = cli_guided.cmd_create_guided(SimpleNamespace(domain=None, phase=None, component=None, priority="MEDIUM"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "GUIDED_SUCCESS" in out


def test_cmd_create_guided_adds_extra_subtask(monkeypatch, capsys):
    monkeypatch.setattr(cli_guided, "is_interactive", lambda: True)
    monkeypatch.setattr(cli_guided, "translate", lambda key, **kwargs: key)
    seq = iter(["Title", "TASK-001", "Desc"])
    monkeypatch.setattr(cli_guided, "prompt_required", lambda *_: next(seq))
    monkeypatch.setattr(cli_guided, "prompt", lambda *_, **__: "a,b")
    monkeypatch.setattr(cli_guided, "prompt_list", lambda *_, **__: ["item"])
    confirms = iter([True, False])
    monkeypatch.setattr(cli_guided, "confirm", lambda *_, **__: next(confirms))
    monkeypatch.setattr(cli_guided, "validate_flagship_subtasks", lambda subtasks: (True, []))
    monkeypatch.setattr(cli_guided, "prompt_subtask_interactive", lambda idx: SimpleNamespace(title=f"Subtask {idx}", criteria_confirmed=True, tests_confirmed=True, blockers_resolved=True))

    class DummyManager:
        def __init__(self):
            self.saved = None

        def create_task(self, *_, **__):
            task = SimpleNamespace(id="TASK-123", title="Title", domain="", parent="TASK-001", subtasks=[], update_status_from_progress=lambda: None)
            task.success_criteria = []
            task.risks = []
            task.tags = []
            task.context = ""
            return task

        def save_task(self, task):
            self.saved = task
            self.count = len(task.subtasks)

    mgr = DummyManager()
    monkeypatch.setattr(cli_guided, "TaskManager", lambda: mgr)
    monkeypatch.setattr(cli_guided, "save_last_task", lambda *_, **__: None)
    rc = cli_guided.cmd_create_guided(SimpleNamespace(domain=None, phase=None, component=None, priority="MEDIUM"))
    assert rc == 0
    assert mgr.count == 4


def test_cmd_automation_task_create_dry_run(monkeypatch, tmp_path, capsys):
    from core.desktop.devtools.interface.tasks_app import _automation_template_payload

    tpl = _automation_template_payload(3, 70, "r", "sla")
    path = tmp_path / "tpl.json"
    path.write_text(json.dumps(tpl), encoding="utf-8")
    args = SimpleNamespace(
        parent="TASK-001",
        title="Title",
        description="desc",
        subtasks=f"@{path}",
        domain="",
        phase=None,
        component=None,
        count=3,
        coverage=70,
        risks="r",
        sla="sla",
        tests=None,
        note=None,
        validate_only=False,
        apply=False,
        dry_run=True,
    )
    rc = cli_guided.cmd_automation_task_create(args)
    assert rc == 0
    assert "DRY RUN" in capsys.readouterr().out


def test_cmd_automation_task_create_with_tags_and_deps(monkeypatch, tmp_path, capsys):
    from core.desktop.devtools.interface.tasks_app import _automation_template_payload

    tpl = _automation_template_payload(3, 70, "r", "sla")
    path = tmp_path / "tpl.json"
    path.write_text(json.dumps(tpl), encoding="utf-8")
    args = SimpleNamespace(
        parent="TASK-001",
        title="Title #tag1 @TASK-009",
        description="desc",
        subtasks=f"@{path}",
        domain="",
        phase="ph",
        component="cmp",
        count=3,
        coverage=70,
        risks="r",
        sla="sla",
        tests=None,
        note=None,
        validate_only=True,
        apply=False,
        dry_run=False,
    )
    rc = cli_guided.cmd_automation_task_create(args)
    assert rc == 0
    body = json.loads(capsys.readouterr().out)
    assert "automation.task-create.validate" in body["command"]
    # tags/deps parsed from title
    assert body["status"] == "OK"


def test_cmd_create_guided_with_tags_and_confirm(monkeypatch, capsys):
    monkeypatch.setattr(cli_guided, "is_interactive", lambda: True)
    monkeypatch.setattr(cli_guided, "translate", lambda key, **kwargs: key)
    seq = iter(["Title", "TASK-001", "Desc"])
    monkeypatch.setattr(cli_guided, "prompt_required", lambda *_: next(seq))
    # tags provided
    prompt_calls = iter(["ctx", "tag1,tag2"])
    def prompt_side(key, default=""):
        try:
            return next(prompt_calls)
        except StopIteration:
            return default
    monkeypatch.setattr(cli_guided, "prompt", prompt_side)
    monkeypatch.setattr(cli_guided, "prompt_list", lambda *_, **__: ["item"])
    confirms = iter([True, False])
    monkeypatch.setattr(cli_guided, "confirm", lambda *_, **__: next(confirms))
    monkeypatch.setattr(cli_guided, "validate_flagship_subtasks", lambda subtasks: (True, []))
    monkeypatch.setattr(cli_guided, "prompt_subtask_interactive", lambda idx: SimpleNamespace(title=f"Subtask {idx}", criteria_confirmed=True, tests_confirmed=True, blockers_resolved=True))

    class DummyManager:
        def __init__(self):
            self.saved = None

        def create_task(self, *_, **__):
            task = SimpleNamespace(id="TASK-123", title="Title", domain="", parent="TASK-001", subtasks=[], update_status_from_progress=lambda: None)
            task.success_criteria = []
            task.risks = []
            task.tags = []
            task.context = ""
            return task

        def save_task(self, task):
            self.saved = task
            self.tags = task.tags

    mgr = DummyManager()
    monkeypatch.setattr(cli_guided, "TaskManager", lambda: mgr)
    monkeypatch.setattr(cli_guided, "save_last_task", lambda *_, **__: None)
    rc = cli_guided.cmd_create_guided(SimpleNamespace(domain=None, phase=None, component=None, priority="MEDIUM"))
    assert rc == 0
    assert mgr.tags == ["tag1", "tag2"]


def test_cmd_create_guided_flagship_continue(monkeypatch, capsys):
    monkeypatch.setattr(cli_guided, "is_interactive", lambda: True)
    monkeypatch.setattr(cli_guided, "translate", lambda key, **kwargs: key)
    seq = iter(["Title", "TASK-001", "Desc"])
    monkeypatch.setattr(cli_guided, "prompt_required", lambda *_: next(seq))
    monkeypatch.setattr(cli_guided, "prompt", lambda *_, **__: "")
    monkeypatch.setattr(cli_guided, "prompt_list", lambda *_, **__: ["item"])
    confirm_iter = iter([True, False, True])
    monkeypatch.setattr(cli_guided, "confirm", lambda *_, **__: next(confirm_iter))
    monkeypatch.setattr(cli_guided, "validate_flagship_subtasks", lambda subtasks: (False, ["issue1"]))
    monkeypatch.setattr(cli_guided, "prompt_subtask_interactive", lambda idx: SimpleNamespace(title=f"Subtask {idx}", criteria_confirmed=True, tests_confirmed=True, blockers_resolved=True))

    class DummyManager:
        def __init__(self):
            self.saved = None
        def create_task(self, *_, **__):
            task = SimpleNamespace(id="TASK-123", title="Title", domain="", parent="TASK-001", subtasks=[], update_status_from_progress=lambda: None)
            task.success_criteria = []
            task.risks = []
            task.tags = []
            task.context = ""
            return task
        def save_task(self, task):
            self.saved = task
    monkeypatch.setattr(cli_guided, "TaskManager", lambda: DummyManager())
    monkeypatch.setattr(cli_guided, "save_last_task", lambda *_, **__: None)
    rc = cli_guided.cmd_create_guided(SimpleNamespace(domain="d", phase="p", component="c", priority="MEDIUM"))
    assert rc == 0


def test_cmd_create_guided_issues_path(monkeypatch, capsys):
    # Simulate flagship issues path where user declines to continue
    monkeypatch.setattr(cli_guided, "is_interactive", lambda: True)
    monkeypatch.setattr(cli_guided, "translate", lambda key, **kwargs: key)
    seq = iter(["Title", "TASK-001", "Desc long enough"])
    monkeypatch.setattr(cli_guided, "prompt_required", lambda *_: next(seq))
    monkeypatch.setattr(cli_guided, "prompt", lambda *_, **__: "ctx")
    monkeypatch.setattr(cli_guided, "prompt_list", lambda *_, **__: ["item"])
    monkeypatch.setattr(cli_guided, "confirm", lambda *_, **__: False)
    monkeypatch.setattr(cli_guided, "validate_flagship_subtasks", lambda subtasks: (False, ["issue1"]))
    monkeypatch.setattr(cli_guided, "prompt_subtask_interactive", lambda idx: SimpleNamespace(title=f"Subtask {idx}", criteria_confirmed=True, tests_confirmed=True, blockers_resolved=True))

    rc = cli_guided.cmd_create_guided(SimpleNamespace(domain=None, phase=None, component=None, priority="MEDIUM"))
    assert rc == 1
    out = capsys.readouterr().out
    assert "GUIDED_WARN_ISSUES" in out
