import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

HISTORY_PATH = Path(os.environ.get("APPLY_TASK_HISTORY") or Path.home() / ".cache" / "apply_task" / "history.jsonl")
SKIP_HISTORY_ENV = "APPLY_TASK_SKIP_HISTORY"
LAST_POINTER = Path(".last")


def _iso_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_history(argv, history_path: Optional[Path] = None) -> None:
    target = Path(history_path or HISTORY_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    entry = {"timestamp": _iso_timestamp(), "args": argv}
    with target.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(entry, ensure_ascii=False))
        fp.write("\n")


def load_history(history_path: Optional[Path] = None):
    target = Path(history_path or HISTORY_PATH)
    if not target.exists():
        return []
    entries = []
    with target.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def read_last_pointer(pointer_path: Optional[Path] = None) -> Tuple[Optional[str], Optional[str]]:
    target = Path(pointer_path or LAST_POINTER)
    if not target.exists():
        return None, None
    raw = target.read_text(encoding="utf-8").strip()
    if not raw:
        return None, None
    if "@" in raw:
        tid, domain = raw.split("@", 1)
        return tid or None, domain or ""
    return raw, ""


def explain_source(source: str, path: Path) -> str:
    return f"[apply_task] using {source}: {path}"


def find_git_root() -> Optional[Path]:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
        root = Path(result.stdout.strip())
        return root if root.exists() else None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def find_tasks_py(verbose: bool = False):
    current = Path.cwd()

    env_override = os.environ.get("APPLY_TASKS_PY")
    if env_override:
        candidate = Path(env_override).expanduser().resolve()
        if candidate.exists():
            return candidate, "env"

    git_root = find_git_root()
    if git_root:
        tasks_in_git = git_root / "tasks.py"
        if tasks_in_git.exists():
            return tasks_in_git.resolve(), "git"

    if (current / "tasks.py").exists():
        return (current / "tasks.py"), "cwd"

    search_limit = git_root if git_root else None
    for parent in current.parents:
        if search_limit and parent == search_limit.parent:
            break
        candidate = parent / "tasks.py"
        if candidate.exists():
            return candidate.resolve(), "parent"

    script_dir = Path(__file__).resolve().parent
    fallback = script_dir / "tasks.py"
    if fallback.exists():
        return fallback.resolve(), "script"

    return None, None


def run_tasks_py(args, verbose: bool = False):
    tasks_py, source = find_tasks_py(verbose=verbose)
    git_root = find_git_root()

    if not tasks_py:
        payload = {
            "git_root": str(git_root) if git_root else None,
            "hint": "Установи/обнови apply_task так, чтобы tasks.py был доступен в PATH или через APPLY_TASKS_PY",
        }
        if not git_root:
            payload["hint"] = "Инициализируй git или задай APPLY_TASKS_PY=/abs/path/to/tasks.py"
        return None, payload

    if verbose and source:
        print(explain_source(source, tasks_py), file=os.sys.stderr)

    project_root = git_root if git_root else Path.cwd()
    cmd = [str(tasks_py)] + args
    env = os.environ.copy()
    env["APPLY_TASK_PROJECT_ROOT"] = str(project_root)
    result = subprocess.run(cmd, cwd=project_root, env=env)
    return result.returncode, None
