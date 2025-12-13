import json
import os
import subprocess
import sys
from pathlib import Path


def _run_apply(tmp_path: Path, args: list[str]) -> dict:
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["APPLY_TASKS_PY"] = str(root / "tasks.py")  # явно указываем tasks.py
    env["APPLY_TASK_TASKS_DIR"] = str(tmp_path / ".tasks")  # use local .tasks for isolation
    cmd = [sys.executable, str(root / "apply_task")] + args
    result = subprocess.run(cmd, cwd=tmp_path, capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_apply_move_glob(tmp_path: Path):
    tasks_dir = tmp_path / ".tasks" / "phase1"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    for i in range(2):
        (tasks_dir / f"TASK-10{i}.task").write_text(f"---\nid: TASK-10{i}\ntitle: Demo {i}\nstatus: TODO\ncreated: now\nupdated: now\n---\n")

    payload = _run_apply(tmp_path, ["move", "--glob", "phase1/TASK-10*.task", "--to", "phase2"])
    assert payload["status"] == "OK"
    assert payload["payload"]["moved"] == 2
    assert (tmp_path / ".tasks" / "phase2" / "TASK-100.task").exists()


def test_apply_clean_glob(tmp_path: Path):
    file_path = tmp_path / ".tasks" / "phaseX" / "TASK-999.task"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text("---\nid: TASK-999\ntitle: Demo\nstatus: TODO\ncreated: now\nupdated: now\n---\n")

    payload = _run_apply(tmp_path, ["clean", "--glob", "phaseX/TASK-*.task"])
    assert payload["status"] == "OK"
    assert payload["payload"]["removed"] == 1
    assert not file_path.exists()
