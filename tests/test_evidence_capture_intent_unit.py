"""Unit tests for evidence_capture intent (artifact store + redaction)."""

from pathlib import Path

from core import Step, TaskDetail
from core.desktop.devtools.application.task_manager import TaskManager
from core.desktop.devtools.interface.intent_api import handle_evidence_capture


def test_evidence_capture_writes_artifacts_and_redacts_secrets(tmp_path: Path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    step = Step(False, "Step", success_criteria=["c"], tests=["t"])
    task = TaskDetail(id="TASK-001", title="Example", status="TODO", steps=[step])
    manager.save_task(task, skip_sync=True)

    token = "ghp_" + ("A" * 40)
    resp = handle_evidence_capture(
        manager,
        {
            "intent": "evidence_capture",
            "task": "TASK-001",
            "path": "s:0",
            "artifacts": [
                {
                    "kind": "cmd_output",
                    "command": f"curl -H 'Authorization: Bearer {token}' https://example.com",
                    "stdout": f"ok {token}\nAuthorization: Bearer {token}\n",
                    "exit_code": 0,
                },
                {"kind": "diff", "diff": f"+ added {token}\n- removed {token}\n"},
                {"kind": "url", "url": f"https://example.com/callback?token={token}"},
            ],
            "checks": [
                {"kind": "command", "spec": "pytest -q", "outcome": "pass", "preview": f"all green {token}"},
            ],
        },
    )
    assert resp.success is True

    reloaded = manager.load_task("TASK-001", skip_sync=True)
    st = reloaded.steps[0]
    kinds = [a.kind for a in st.attachments]
    assert "cmd_output" in kinds
    assert "diff" in kinds
    assert "url" in kinds
    assert st.verification_checks
    assert "<redacted>" in st.verification_checks[0].preview
    assert token not in st.verification_checks[0].preview

    cmd = next(a for a in st.attachments if a.kind == "cmd_output")
    assert cmd.uri.startswith(".artifacts/")
    assert cmd.size > 0
    cmd_path = (tasks_dir / cmd.uri).resolve()
    assert cmd_path.exists()
    cmd_text = cmd_path.read_text(encoding="utf-8")
    assert token not in cmd_text
    assert "<redacted>" in cmd_text

    diff = next(a for a in st.attachments if a.kind == "diff")
    assert diff.uri.startswith(".artifacts/")
    diff_path = (tasks_dir / diff.uri).resolve()
    assert diff_path.exists()
    diff_text = diff_path.read_text(encoding="utf-8")
    assert token not in diff_text
    assert "<redacted>" in diff_text

    url = next(a for a in st.attachments if a.kind == "url")
    assert "<redacted>" in url.external_uri
    assert token not in url.external_uri

