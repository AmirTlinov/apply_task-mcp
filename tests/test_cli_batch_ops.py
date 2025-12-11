import json
import os
from pathlib import Path
import subprocess
import sys


def _run_apply(root: Path, cwd: Path, args: list[str]) -> dict:
    # Используем tasks.py напрямую, чтобы избежать обёртки apply_task
    env = os.environ.copy()
    env["APPLY_TASK_TASKS_DIR"] = str(cwd / ".tasks")  # use local .tasks for isolation
    result = subprocess.run([sys.executable, str(root / "tasks.py")] + args, cwd=cwd, capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_move_glob_cli(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir(exist_ok=True)
    (tasks_dir / "phase1").mkdir(exist_ok=True)
    for i in range(2):
        (tasks_dir / "phase1" / f"TASK-10{i}.task").write_text(f"---\nid: TASK-10{i}\ntitle: Demo {i}\nstatus: FAIL\nupdated: now\ncreated: now\n---\n")

    payload = _run_apply(root, tmp_path, ["move", "--glob", "phase1/TASK-10*.task", "--to", "phase2"])
    assert payload["status"] == "OK"
    assert payload["payload"]["moved"] == 2
    assert (tasks_dir / "phase2" / "TASK-100.task").exists()


def test_clean_glob_cli(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir(exist_ok=True)
    (tasks_dir / "phaseX").mkdir(exist_ok=True)
    (tasks_dir / "phaseX" / "TASK-999.task").write_text("---\nid: TASK-999\ntitle: Demo\nstatus: FAIL\ncreated: now\nupdated: now\n---\n")

    payload = _run_apply(root, tmp_path, ["clean", "--glob", "phaseX/TASK-*.task"])
    assert payload["status"] == "OK"
    assert payload["payload"]["removed"] == 1
    assert not (tasks_dir / "phaseX" / "TASK-999.task").exists()
