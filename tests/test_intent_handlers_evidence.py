from core import Step, TaskDetail
from core.desktop.devtools.application.task_manager import TaskManager
from core.desktop.devtools.interface.intent_api import handle_progress, handle_verify, handle_radar, handle_evidence_capture
from core.desktop.devtools.interface import evidence_collectors
from core.evidence import Attachment, VerificationCheck


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


def test_handle_verify_normalizes_checks_and_file_paths(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    step = Step(False, "Step", success_criteria=["c"], tests=["t"])
    task = TaskDetail(id="TASK-001", title="Example", status="TODO", steps=[step])
    manager.save_task(task, skip_sync=True)

    response = handle_verify(
        manager,
        {
            "intent": "verify",
            "task": "TASK-001",
            "path": "s:0",
            "checkpoints": {"criteria": {"confirmed": True}, "tests": {"confirmed": True}},
            "checks": ["pytest -q"],
            "attachments": ["logs/output.log", {"kind": "log", "file_path": "logs/test.log"}],
        },
    )
    assert response.success is True

    updated = manager.load_task("TASK-001", skip_sync=True)
    updated_step = updated.steps[0]
    assert any(check.spec == "pytest -q" for check in updated_step.verification_checks)
    assert any(att.path == "logs/output.log" for att in updated_step.attachments)
    assert any(att.path == "logs/test.log" for att in updated_step.attachments)

    expected_check_digest = VerificationCheck.from_dict({"kind": "command", "spec": "pytest -q", "outcome": "info"}).digest
    expected_file_digest = Attachment.from_dict({"kind": "file", "path": "logs/output.log"}).digest
    expected_log_digest = Attachment.from_dict({"kind": "log", "path": "logs/test.log"}).digest
    criteria_refs = list(updated_step.criteria_evidence_refs or [])
    tests_refs = list(updated_step.tests_evidence_refs or [])
    assert expected_check_digest in criteria_refs
    assert expected_file_digest in criteria_refs
    assert expected_log_digest in criteria_refs
    assert expected_check_digest in tests_refs
    assert expected_file_digest in tests_refs
    assert expected_log_digest in tests_refs

def test_handle_verify_extended_checkpoints_adds_evidence_refs(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    step = Step(False, "Step", success_criteria=["c"], tests=["t"])
    task = TaskDetail(id="TASK-001", title="Example", status="TODO", steps=[step])
    manager.save_task(task, skip_sync=True)

    check_payload = {"kind": "command", "spec": "bandit -q -r .", "outcome": "pass"}
    attachment_payload = {"kind": "url", "external_uri": "https://example.com/security-review"}
    expected_check_digest = VerificationCheck.from_dict(check_payload).digest
    expected_attachment_digest = Attachment.from_dict(attachment_payload).digest

    resp = handle_verify(
        manager,
        {
            "intent": "verify",
            "task": "TASK-001",
            "path": "s:0",
            "checkpoints": {"security": {"confirmed": True, "note": "reviewed"}},
            "checks": [check_payload],
            "attachments": [attachment_payload],
        },
    )
    assert resp.success is True

    updated = manager.load_task("TASK-001", skip_sync=True)
    assert updated is not None
    updated_step = updated.steps[0]
    assert updated_step.security_confirmed is True
    assert "reviewed" in updated_step.security_notes
    assert expected_check_digest in list(updated_step.security_evidence_refs or [])
    assert expected_attachment_digest in list(updated_step.security_evidence_refs or [])


def test_radar_evidence_reflects_verify_and_capture(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    step = Step(False, "Step", success_criteria=["c"], tests=["t"])
    task = TaskDetail(id="TASK-001", title="Example", status="TODO", steps=[step])
    manager.save_task(task, skip_sync=True)

    verify_resp = handle_verify(
        manager,
        {
            "intent": "verify",
            "task": "TASK-001",
            "path": "s:0",
            "checkpoints": {"criteria": {"confirmed": True}},
            "checks": [{"kind": "command", "spec": "pytest -q", "outcome": "pass"}],
            "attachments": [{"kind": "url", "external_uri": "https://example.com/report"}],
            "verification_outcome": "pass",
        },
    )
    assert verify_resp.success is True

    radar = handle_radar(manager, {"intent": "radar", "task": "TASK-001"})
    assert radar.success is True
    evidence = radar.result["verify"]["evidence"]
    assert evidence["verification_outcome"] == "pass"
    assert evidence["checks"]["count"] >= 1
    assert evidence["attachments"]["count"] >= 1

    capture = handle_evidence_capture(
        manager,
        {
            "intent": "evidence_capture",
            "task": "TASK-001",
            "path": "s:0",
            "verification_outcome": "manual",
        },
    )
    assert capture.success is True

    radar2 = handle_radar(manager, {"intent": "radar", "task": "TASK-001"})
    assert radar2.result["verify"]["evidence"]["verification_outcome"] == "manual"


def test_progress_gating_respects_required_checkpoints(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    step = Step(False, "Step", success_criteria=["c"], tests=["t"])
    step.criteria_confirmed = True
    step.tests_confirmed = True
    step.required_checkpoints = ["criteria", "tests", "security"]
    task = TaskDetail(id="TASK-001", title="Example", status="TODO", steps=[step])
    manager.save_task(task, skip_sync=True)

    blocked = handle_progress(manager, {"intent": "progress", "task": "TASK-001", "path": "s:0", "completed": True})
    assert blocked.success is False
    assert blocked.error_code == "GATING_FAILED"
    assert "security" in list(((blocked.result or {}).get("missing_checkpoints") or []))

    ok = handle_verify(manager, {"intent": "verify", "task": "TASK-001", "path": "s:0", "checkpoints": {"security": {"confirmed": True}}})
    assert ok.success is True

    done = handle_progress(manager, {"intent": "progress", "task": "TASK-001", "path": "s:0", "completed": True})
    assert done.success is True


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
