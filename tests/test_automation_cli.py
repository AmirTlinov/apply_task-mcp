import json
import os
import subprocess
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tasks  # type: ignore
from core import SubTask  # type: ignore


SCRIPT = ROOT / "apply_task"


def _run(args, cwd):
    env = os.environ.copy()
    env["APPLY_TASK_TASKS_DIR"] = str(cwd / ".tasks")  # use local .tasks for isolation
    result = subprocess.run([sys.executable, str(SCRIPT)] + args, cwd=cwd, text=True, capture_output=True, env=env)
    return result


def _json_body(result):
    output = (result.stdout or "").strip()
    if not output:
        raise AssertionError(f"empty stdout, stderr={result.stderr}")
    return json.loads(output)


def _make_task(tmp_path) -> str:
    tasks_dir = tmp_path / ".tasks"
    manager = tasks.TaskManager(tasks_dir=tasks_dir)
    task = manager.create_task("Demo automation task", parent="ROOT", domain="desktop/devtools")
    task.description = "Long enough description"
    task.subtasks = [
        SubTask(False, "Long enough subtask for automation", ["c"], ["t"], ["b"], False, False, False),
    ]
    manager.save_task(task)
    return task.id


def test_automation_template_and_create_validation(tmp_path):
    out_path = tmp_path / "subtasks.json"
    res = _run(
        [
            "automation",
            "task-template",
            "--count",
            "3",
            "--coverage",
            "90",
            "--risks",
            "perf;deps",
            "--sla",
            "p95<=150ms",
            "--output",
            str(out_path),
        ],
        cwd=tmp_path,
    )
    assert res.returncode == 0, res.stderr
    body = _json_body(res)
    assert body["status"] == "OK"
    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert data["defaults"]["coverage"] == 90
    assert len(data["subtasks"]) == 3
    assert "p95" in data["defaults"]["sla"]

    res_create = _run(
        [
            "automation",
            "task-create",
            "Title from automation",
            "--parent",
            "TASK-050",
            "--description",
            "Automation description",
            "--subtasks",
            f"@{out_path}",
            "--domain",
            "desktop/devtools",
        ],
        cwd=tmp_path,
    )
    assert res_create.returncode == 0, res_create.stderr
    body_create = _json_body(res_create)
    assert body_create["status"] == "OK"
    assert "validate" in body_create["command"]


def test_automation_health_skips_when_empty_cmd(tmp_path):
    res = _run(
        [
            "automation",
            "health",
            "--pytest-cmd",
            "",
            "--log",
            str(tmp_path / "health.log"),
        ],
        cwd=tmp_path,
    )
    assert res.returncode == 0, res.stderr
    body = _json_body(res)
    assert body["status"] == "OK"
    log_path = Path(body["payload"]["log"])
    assert log_path.exists()
    saved = json.loads(log_path.read_text(encoding="utf-8"))
    assert saved["pytest_cmd"] == ""
    assert saved["rc"] == 0


def test_automation_checkpoint_note_and_ok(tmp_path):
    task_id = _make_task(tmp_path)
    log_path = tmp_path / "checkpoint.log"
    log_path.write_text("note from automation", encoding="utf-8")

    res_note = _run(
        [
            "automation",
            "checkpoint",
            task_id,
            "0",
            "--mode",
            "note",
            "--checkpoint",
            "tests",
            "--log",
            str(log_path),
        ],
        cwd=tmp_path,
    )
    assert res_note.returncode == 0, res_note.stderr
    body_note = _json_body(res_note)
    assert body_note["status"] == "OK"
    assert body_note["payload"]["index"] == 0

    res_ok = _run(
        [
            "automation",
            "checkpoint",
            task_id,
            "0",
            "--mode",
            "ok",
            "--log",
            str(log_path),
        ],
        cwd=tmp_path,
    )
    assert res_ok.returncode == 0, res_ok.stderr
    body_ok = _json_body(res_ok)
    assert body_ok["status"] == "OK"
    task = tasks.TaskManager(tasks_dir=tmp_path / ".tasks").load_task(task_id, "desktop/devtools")
    assert task.subtasks[0].completed
