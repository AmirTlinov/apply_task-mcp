from types import SimpleNamespace

from core.desktop.devtools.interface import cli_checkpoint


def test_cli_checkpoint_validate_only(monkeypatch, capsys):
    class DummyManager:
        def __init__(self):
            pass

        def load_task(self, *_, **__):
            st = SimpleNamespace(
                title="sub",
                success_criteria=["c"],
                tests=["t"],
                blockers=["b"],
                criteria_confirmed=True,
                tests_confirmed=True,
                blockers_resolved=True,
                completed=False,
                criteria_notes=[],
                tests_notes=[],
                blockers_notes=[],
                children=[],
            )
            return SimpleNamespace(
                id="TASK-1",
                title="t",
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
                description="",
                context="",
                success_criteria=[],
                dependencies=[],
                next_steps=[],
                problems=[],
                risks=[],
                history=[],
                subtasks=[st],
                project_remote_updated=False,
                calculate_progress=lambda: 0,
            )

        def update_subtask_checkpoint(self, *args, **kwargs):
            return True, None

        def set_subtask(self, *args, **kwargs):
            return True, None

    monkeypatch.setattr(cli_checkpoint, "TaskManager", lambda: DummyManager())
    monkeypatch.setattr(cli_checkpoint, "translate", lambda key, **kwargs: key)
    # Pass task_id directly to avoid needing .last file
    args = SimpleNamespace(task_id="TASK-1", auto=True, domain=None, phase=None, component=None, note="n", validate_only=True, subtask=None, path=None)
    rc = cli_checkpoint.cmd_checkpoint(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "checkpoint" in out
