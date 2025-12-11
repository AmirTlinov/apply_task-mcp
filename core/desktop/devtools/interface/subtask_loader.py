"""Reusable helpers for parsing/validating subtasks payloads."""

import json
import sys
from pathlib import Path
from typing import List, Tuple

from core import SubTask
from core.desktop.devtools.application.task_manager import _flatten_subtasks
from core.desktop.devtools.interface.i18n import translate


class SubtaskParseError(Exception):
    """Subtask payload parsing error."""


def _load_input_source(raw: str, label: str) -> str:
    """Load text payload from string, file, or STDIN."""
    source = (raw or "").strip()
    if not source:
        return source
    if source == "-":
        data = sys.stdin.read()
        if not data.strip():
            raise SubtaskParseError(f"STDIN is empty: provide {label}")
        return data
    if source.startswith("@"):  # file reference
        path_str = source[1:].strip()
        if not path_str:
            raise SubtaskParseError(f"Specify path to {label} after '@'")
        file_path = Path(path_str).expanduser()
        if not file_path.exists():
            raise SubtaskParseError(f"File not found: {file_path}")
        return file_path.read_text(encoding="utf-8")
    return source


def load_subtasks_source(raw: str) -> str:
    return _load_input_source(raw, translate("LABEL_SUBTASKS_JSON"))


def _to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "y", "ok", "done", "ready", "готов", "готово", "+")
    return bool(value)


def parse_subtasks_json(raw: str) -> List[SubTask]:
    try:
        data = json.loads(raw)
        if not isinstance(data, list):
            raise SubtaskParseError(translate("ERR_JSON_ARRAY_REQUIRED"))

        subtasks = []
        for idx, item in enumerate(data, 1):
            if not isinstance(item, dict):
                raise SubtaskParseError(translate("ERR_JSON_ELEMENT_OBJECT", idx=idx))

            title = item.get("title", "")
            if not title:
                raise SubtaskParseError(translate("ERR_JSON_ELEMENT_TITLE", idx=idx))

            criteria = item.get("criteria", item.get("success_criteria", []))
            tests = item.get("tests", [])
            blockers = item.get("blockers", [])

            if not isinstance(criteria, list):
                criteria = [str(criteria)]
            if not isinstance(tests, list):
                tests = [str(tests)]
            if not isinstance(blockers, list):
                blockers = [str(blockers)]

            if not criteria:
                raise SubtaskParseError(translate("ERR_JSON_ELEMENT_CRITERIA", idx=idx))
            if not tests:
                raise SubtaskParseError(translate("ERR_JSON_ELEMENT_TESTS", idx=idx))
            if not blockers:
                raise SubtaskParseError(translate("ERR_JSON_ELEMENT_BLOCKERS", idx=idx))

            criteria_notes = item.get("criteria_notes", [])
            tests_notes = item.get("tests_notes", [])
            blockers_notes = item.get("blockers_notes", [])
            if not isinstance(criteria_notes, list):
                criteria_notes = [str(criteria_notes)]
            if not isinstance(tests_notes, list):
                tests_notes = [str(tests_notes)]
            if not isinstance(blockers_notes, list):
                blockers_notes = [str(blockers_notes)]

            progress_notes = item.get("progress_notes", [])
            started_at = item.get("started_at", None)
            blocked = item.get("blocked", False)
            block_reason = item.get("block_reason", "")

            if not isinstance(progress_notes, list):
                progress_notes = [str(progress_notes)]

            st = SubTask(
                False,
                title,
                criteria,
                tests,
                blockers,
                criteria_confirmed=_to_bool(item.get("criteria_confirmed", False)),
                tests_confirmed=_to_bool(item.get("tests_confirmed", False)),
                blockers_resolved=_to_bool(item.get("blockers_resolved", False)),
                criteria_notes=[str(n).strip() for n in criteria_notes if str(n).strip()],
                tests_notes=[str(n).strip() for n in tests_notes if str(n).strip()],
                blockers_notes=[str(n).strip() for n in blockers_notes if str(n).strip()],
                progress_notes=[str(n).strip() for n in progress_notes if str(n).strip()],
                started_at=str(started_at).strip() if started_at else None,
                blocked=_to_bool(blocked),
                block_reason=str(block_reason).strip() if block_reason else "",
            )
            subtasks.append(st)

        return subtasks
    except json.JSONDecodeError as e:
        raise SubtaskParseError(translate("ERR_JSON_INVALID", error=e))


def parse_subtasks_flexible(raw: str) -> List[SubTask]:
    raw = raw.strip()
    if not raw:
        return []
    try:
        return parse_subtasks_json(raw)
    except SubtaskParseError as e:
        raise SubtaskParseError(translate("ERR_JSON_FORMAT_HINT", error=e))


def validate_flagship_subtasks(subtasks: List[SubTask]) -> Tuple[bool, List[str]]:
    """Flagship validation for subtasks."""
    flat = _flatten_subtasks(subtasks)
    if not flat:
        return False, [translate("ERR_TASK_NEEDS_SUBTASKS")]
    if len(flat) < 3:
        return False, [translate("ERR_SUBTASKS_MIN", count=len(flat))]

    all_issues = []
    for idx, (_, st) in enumerate(flat, 1):
        valid, issues = st.is_valid_flagship()
        if not valid:
            all_issues.extend([translate("ERR_SUBTASK_PREFIX", idx=idx, issue=issue) for issue in issues])

    return len(all_issues) == 0, all_issues
