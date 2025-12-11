from types import SimpleNamespace

from core.desktop.devtools.interface import cli_edit


def test_cmd_edit_updates_fields(monkeypatch, capsys):
    saved = {}

    class DummyManager:
        def __init__(self):
            self.loaded = SimpleNamespace(
                id="TASK-1",
                title="Title",
                status="FAIL",
                description="old",
                context="",
                tags=[],
                priority="LOW",
                phase="",
                component="",
                domain="",
                subtasks=[],
                parent="P",
                assignee=None,
                blocked=False,
                blockers=[],
                success_criteria=[],
                dependencies=[],
                next_steps=[],
                problems=[],
                risks=[],
                history=[],
                project_remote_updated=False,
            )
            self.loaded.calculate_progress = lambda: 0

        def load_task(self, task_id, domain):
            saved["load"] = (task_id, domain)
            return self.loaded

        def save_task(self, task):
            saved["task"] = task

    monkeypatch.setattr(cli_edit, "TaskManager", lambda: DummyManager())
    args = SimpleNamespace(task_id="TASK-1", domain="d", phase="p", component="c", description="new desc", context="ctx", tags="a,b", priority="HIGH", new_domain="nd")
    rc = cli_edit.cmd_edit(args)
    out = capsys.readouterr().out
    assert rc == 0 and "TASK-1" in out
    assert saved["task"].description == "new desc"
    assert saved["task"].domain == "nd"


def test_cmd_edit_not_found(monkeypatch, capsys):
    class DummyManager:
        def load_task(self, *_, **__):
            return None

    monkeypatch.setattr(cli_edit, "TaskManager", lambda: DummyManager())
    args = SimpleNamespace(task_id="MISS", domain=None, phase=None, component=None, description=None, context=None, tags=None, priority=None, new_domain=None)
    rc = cli_edit.cmd_edit(args)
    assert rc == 1
    assert "MISS" in capsys.readouterr().out
