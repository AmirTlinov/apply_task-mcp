from core import Step, TaskDetail
from core.desktop.devtools.application.task_manager import TaskManager
from core.desktop.devtools.interface.intent_api import handle_progress, handle_verify
from core.desktop.devtools.interface import evidence_collectors


def test_handle_verify_persists_checks_and_attachments(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    step = Step(False, "Step", success_criteria=["c"], tests=["t"])
    task = TaskDetail(id="TASK-001", title="Example", status="TODO", steps=[step])
    manager.save_task(task, skip_sync=True)

    data = {
        "intent": "verify",
        "task": "TASK-001",
        "path": "s:0",
        "checkpoints": {"criteria": {"confirmed": True}, "tests": {"confirmed": True}},
        "checks": [
            {
                "kind": "command",
                "spec": "pytest -q",
                "outcome": "pass",
                "observed_at": "2025-12-21T00:00:00Z",
                "digest": "abc123",
                "preview": "ok",
            }
        ],
        "attachments": [{"kind": "log", "path": "logs/test.log"}],
        "verification_outcome": "pass",
    }
    response = handle_verify(manager, data)
    assert response.success is True

    updated = manager.load_task("TASK-001", skip_sync=True)
    updated_step = updated.steps[0]
    assert updated_step.verification_checks
    assert updated_step.verification_checks[0].spec == "pytest -q"
    assert updated_step.attachments
    assert updated_step.attachments[0].kind == "log"
    assert updated_step.verification_outcome == "pass"


def test_handle_progress_force_requires_override(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    step = Step(False, "Step", success_criteria=["c"], tests=["t"])
    task = TaskDetail(id="TASK-001", title="Example", status="TODO", steps=[step])
    manager.save_task(task, skip_sync=True)

    response = handle_progress(
        manager,
        {"intent": "progress", "task": "TASK-001", "path": "s:0", "completed": True, "force": True},
    )
    assert response.success is False
    assert response.error_code == "MISSING_OVERRIDE_REASON"


def test_handle_progress_records_override(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    step = Step(False, "Step", success_criteria=["c"], tests=["t"])
    task = TaskDetail(id="TASK-001", title="Example", status="TODO", steps=[step])
    manager.save_task(task, skip_sync=True)

    response = handle_progress(
        manager,
        {
            "intent": "progress",
            "task": "TASK-001",
            "path": "s:0",
            "completed": True,
            "force": True,
            "override_reason": "manual override for demo",
        },
    )
    assert response.success is True

    updated = manager.load_task("TASK-001", skip_sync=True)
    assert updated.events
    assert any(e.event_type == "override" for e in updated.events)


def test_handle_verify_auto_adds_github_actions_evidence(tmp_path, monkeypatch):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    step = Step(False, "Step", success_criteria=["c"], tests=["t"])
    task = TaskDetail(id="TASK-001", title="Example", status="TODO", steps=[step])
    manager.save_task(task, skip_sync=True)

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_SHA", "0123456789abcdef")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setattr(evidence_collectors, "_run_git", lambda *args, **kwargs: None)

    payload = {"intent": "verify", "task": "TASK-001", "path": "s:0", "checkpoints": {"criteria": {"confirmed": True}}}
    assert handle_verify(manager, payload).success is True
    assert handle_verify(manager, payload).success is True

    updated = manager.load_task("TASK-001", skip_sync=True)
    checks = [c for c in updated.steps[0].verification_checks if c.kind == "ci" and c.spec == "github_actions"]
    assert len(checks) == 1
    assert checks[0].details["run_url"] == "https://github.com/owner/repo/actions/runs/123"
    assert checks[0].details["sha"] == "0123456789abcdef"


def test_handle_verify_auto_adds_git_evidence(tmp_path, monkeypatch):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    step = Step(False, "Step", success_criteria=["c"], tests=["t"])
    task = TaskDetail(id="TASK-001", title="Example", status="TODO", steps=[step])
    manager.save_task(task, skip_sync=True)

    def fake_run_git(args, *, cwd, timeout_s=2.0):
        if args == ["rev-parse", "HEAD"]:
            return "0123456789abcdef0123456789abcdef01234567"
        if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
            return "main"
        if args == ["status", "--porcelain"]:
            return " M file.txt\n"
        if args == ["describe", "--always", "--dirty"]:
            return "v1.0.0-dirty"
        return ""

    monkeypatch.setattr(evidence_collectors, "_run_git", fake_run_git)

    payload = {"intent": "verify", "task": "TASK-001", "path": "s:0", "checkpoints": {"criteria": {"confirmed": True}}}
    assert handle_verify(manager, payload).success is True

    updated = manager.load_task("TASK-001", skip_sync=True)
    checks = [c for c in updated.steps[0].verification_checks if c.kind == "git" and c.spec == "head"]
    assert len(checks) == 1
    assert checks[0].details["sha"] == "0123456789abcdef0123456789abcdef01234567"
    assert checks[0].details["branch"] == "main"
    assert checks[0].details["dirty"] is True
