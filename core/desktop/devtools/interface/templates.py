"""Templates helpers shared between CLI and TUI creation flows."""

from typing import Tuple

from core.desktop.devtools.application.task_manager import TaskManager


def load_template(kind: str, manager: TaskManager) -> Tuple[str, str]:
    cfg = manager.config.get("templates", {})
    tpl = cfg.get(kind, cfg.get("default", {})) or {}
    desc = tpl.get("description", "")
    tests = tpl.get("tests", "")
    if not desc and not tests:
        return "TBD", "acceptance"
    return desc, tests
