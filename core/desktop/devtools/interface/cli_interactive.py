"""Shared interactive CLI helpers to keep tasks_app slim."""

import sys
from typing import Dict, List

from core import SubTask
from core.desktop.devtools.interface.i18n import translate


def is_interactive() -> bool:
    """Check that both stdin and stdout are TTYs."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def prompt(question: str, default: str = "") -> str:
    """Request a single line of input with optional default."""
    if default:
        question = f"{question} [{default}]"
    try:
        response = input(f"{question}: ").strip()
        return response if response else default
    except (EOFError, KeyboardInterrupt):
        print(f"\n{translate('PROMPT_ABORTED')}")
        sys.exit(1)


def prompt_required(question: str) -> str:
    """Request non-empty input."""
    while True:
        response = prompt(question)
        if response:
            return response
        print(f"  {translate('PROMPT_REQUIRED')}")


def prompt_list(question: str, min_items: int = 0) -> List[str]:
    """Request a list of values until an empty line, enforcing minimal length."""
    items: List[str] = []
    print(f"{question} {translate('PROMPT_EMPTY_TO_FINISH')}")
    while True:
        try:
            line = input(f"  {len(items) + 1}. ").strip()
            if not line:
                if len(items) >= min_items:
                    break
                print(f"  {translate('PROMPT_MIN_ITEMS', count=min_items)}")
                continue
            items.append(line)
        except (EOFError, KeyboardInterrupt):
            print(f"\n{translate('PROMPT_ABORTED')}")
            sys.exit(1)
    return items


def confirm(question: str, default: bool = True) -> bool:
    """Yes/No confirmation helper."""
    suffix = " [Y/n]" if default else " [y/N]"
    try:
        response = input(f"{question}{suffix}: ").strip().lower()
        if not response:
            return default
        return response in ("y", "yes", "д", "да")
    except (EOFError, KeyboardInterrupt):
        print(f"\n{translate('PROMPT_ABORTED')}")
        sys.exit(1)


def prompt_subtask_interactive(index: int) -> SubTask:
    """Interactive subtask creation."""
    print(f"\n{translate('PROMPT_SUBTASK_HEADER', index=index)}")
    title = prompt_required(translate("PROMPT_SUBTASK_TITLE_REQ"))
    while len(title) < 20:
        print(translate("PROMPT_SUBTASK_TITLE_SHORT", length=len(title)))
        title = prompt_required(translate("PROMPT_SUBTASK_TITLE"))

    criteria = prompt_list(translate("PROMPT_SUBTASK_CRITERIA"), min_items=1)
    tests = prompt_list(translate("PROMPT_SUBTASK_TESTS"), min_items=1)
    blockers = prompt_list(translate("PROMPT_SUBTASK_BLOCKERS"), min_items=1)

    return SubTask(False, title, criteria, tests, blockers)


def subtask_flags(st: SubTask) -> Dict[str, bool]:
    """Return checkpoint flags for a subtask."""
    return {
        "criteria": st.criteria_confirmed,
        "tests": st.tests_confirmed,
        "blockers": st.blockers_resolved,
    }
