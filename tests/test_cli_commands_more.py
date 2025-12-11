from types import SimpleNamespace

from core.desktop.devtools.interface import cli_commands as cmds


class DummyTask:
    def __init__(self, id, status="FAIL", priority="LOW", progress=0, phase="", component=""):
        self.id = id
        self.status = status
        self.priority = priority
        self.phase = phase
        self.component = component
        self.domain = ""

    def calculate_progress(self):
        return 10


def _deps(tasks):
    return cmds.CliDeps(
        manager_factory=lambda: SimpleNamespace(list_tasks=lambda domain, skip_sync=False: tasks, load_task=lambda tid, dom: None),
        translate=lambda key, **kwargs: key,
        derive_domain_explicit=lambda d, p, c: d or "",
        resolve_task_reference=lambda *_: (None, None),
        save_last_task=lambda *_, **__: None,
        normalize_task_id=lambda x: x,
        task_to_dict=lambda t, include_subtasks=False: {"id": t.id, "status": t.status, "priority": t.priority, "phase": t.phase, "component": t.component},
    )


def test_cmd_list_filters_by_status(capsys):
    tasks = [DummyTask("A", status="OK"), DummyTask("B", status="FAIL")]
    args = SimpleNamespace(domain="", phase=None, component=None, status="OK", progress=False)
    rc = cmds.cmd_list(args, _deps(tasks))
    assert rc == 0
    out = capsys.readouterr().out
    assert "MSG_LIST_BUILT" in out


def test_cmd_show_not_found(capsys):
    deps = _deps([])
    args = SimpleNamespace(task_id="TASK-1", domain="", phase=None, component=None)
    rc = cmds.cmd_show(args, deps)
    assert rc == 1
    assert "ERR_TASK_NOT_FOUND" in capsys.readouterr().out


def test_cmd_next_no_candidates(monkeypatch, capsys):
    deps = _deps([])
    args = SimpleNamespace(domain="", phase=None, component=None)
    rc = cmds.cmd_next(args, deps)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Нет незавершённых задач" in out


def test_cmd_suggest_no_active(monkeypatch, capsys):
    deps = _deps([])
    args = SimpleNamespace(folder="", domain="", phase=None, component=None)
    rc = cmds.cmd_suggest(args, deps)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Нет задач для рекомендации" in out
