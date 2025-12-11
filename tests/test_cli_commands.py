from types import SimpleNamespace

import pytest

from core.desktop.devtools.interface import cli_commands as cmds


class DummyManager:
    def __init__(self, tasks):
        self._tasks = {t.id: t for t in tasks}
        self.list_calls = 0
        self.load_calls = 0

    def list_tasks(self, domain, skip_sync=False):
        self.list_calls += 1
        return list(self._tasks.values())

    def load_task(self, task_id, domain):
        self.load_calls += 1
        return self._tasks.get(task_id)


def _task(task_id, status="FAIL", priority="MEDIUM", progress=0, blocked=False, deps=None, phase="", component="", domain=""):
    ns = SimpleNamespace(
        id=task_id,
        title=task_id,
        status=status,
        priority=priority,
        blocked=blocked,
        dependencies=deps or [],
        phase=phase,
        component=component,
        domain=domain,
        subtasks=[],
    )
    ns.calculate_progress = lambda: progress
    return ns


def _deps(manager):
    return cmds.CliDeps(
        manager_factory=lambda: manager,
        translate=lambda key, **_: key,
        derive_domain_explicit=lambda d, p, c: d or "",
        resolve_task_reference=lambda a, b, c, d: (None, None),
        save_last_task=lambda tid, dom: manager.__dict__.setdefault("last", (tid, dom)),
        normalize_task_id=lambda tid: tid.upper(),
        task_to_dict=lambda task, include_subtasks=False: {"id": task.id, "status": task.status},
    )


def test_cmd_list_filters_and_summary():
    manager = DummyManager([
        _task("TASK-1", status="OK", phase="p1"),
        _task("TASK-2", status="FAIL", phase="p2"),
    ])
    args = SimpleNamespace(domain="", phase="p1", component="", status="OK", progress=False)
    rc = cmds.cmd_list(args, _deps(manager))
    assert rc == 0
    assert manager.list_calls == 1


def test_cmd_show_uses_last_and_saves():
    task = _task("TASK-9", domain="d1")
    manager = DummyManager([task])

    def resolve(task_id, domain, phase, comp):
        return ("TASK-9", "d1")

    deps = _deps(manager)
    deps.resolve_task_reference = resolve
    args = SimpleNamespace(task_id=None, domain="", phase="", component="")
    rc = cmds.cmd_show(args, deps)
    assert rc == 0
    assert manager.load_calls == 1
    assert manager.last == ("TASK-9", "d1")


def test_cmd_analyze_errors_on_missing():
    manager = DummyManager([])
    args = SimpleNamespace(task_id="TASK-404", domain="", phase="", component="")
    rc = cmds.cmd_analyze(args, _deps(manager))
    assert rc == 1


def test_cmd_next_selects_and_remembers():
    manager = DummyManager([
        _task("TASK-1", status="WARN", priority="HIGH", progress=10),
        _task("TASK-2", status="FAIL", progress=50, blocked=True),
    ])
    args = SimpleNamespace(domain="", phase="", component="")
    deps = _deps(manager)
    rc = cmds.cmd_next(args, deps)
    assert rc == 0
    assert manager.last[0] == "TASK-2"


def test_cmd_suggest_and_quick_use_filters():
    manager = DummyManager([
        _task("TASK-1", status="FAIL", priority="HIGH"),
        _task("TASK-2", status="FAIL", priority="LOW"),
    ])
    deps = _deps(manager)
    args = SimpleNamespace(folder="", domain="", phase="", component="")
    assert cmds.cmd_suggest(args, deps) == 0
    assert cmds.cmd_quick(args, deps) == 0
