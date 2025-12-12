from enum import Enum
from typing import Final, Literal


class Status(Enum):
    OK = ("OK", "green", "+")
    WARN = ("WARN", "yellow", "~")
    FAIL = ("FAIL", "red", "x")
    UNKNOWN = ("?", "blue", "?")

    @classmethod
    def from_string(cls, value: str) -> "Status":
        val = normalize_task_status(value, allow_unknown=True)
        for status in cls:
            if status.value[0] == val:
                return status
        return cls.UNKNOWN


TaskStatusCode = Literal["OK", "WARN", "FAIL"]
TaskStatusLabel = Literal["DONE", "ACTIVE", "TODO"]

_CODE_TO_LABEL: Final[dict[TaskStatusCode, TaskStatusLabel]] = {
    "OK": "DONE",
    "WARN": "ACTIVE",
    "FAIL": "TODO",
}

_ALIASES_TO_CODE: Final[dict[str, TaskStatusCode]] = {
    "DONE": "OK",
    "ACTIVE": "WARN",
    "TODO": "FAIL",
    # Backward-compatible UI labels (older TUI wording)
    "IN_PROGRESS": "WARN",
    "BACKLOG": "FAIL",
}


def normalize_task_status(value: str, *, allow_unknown: bool = False) -> str:
    """Normalize task status input to internal status code.

    Canonical internal codes: OK, WARN, FAIL.
    Accepted aliases: DONE/ACTIVE/TODO (preferred), plus IN_PROGRESS/BACKLOG for compatibility.

    When allow_unknown=True, returns the normalized token (uppercased, spaces→underscores)
    even if it is not a known status.
    """
    token = (value or "").strip().upper().replace(" ", "_")
    if not token:
        return token
    token = _ALIASES_TO_CODE.get(token, token)
    if token in _CODE_TO_LABEL:
        return token
    if allow_unknown:
        return token
    raise ValueError(f"Invalid task status: {value!r}")


def task_status_label(status: str) -> str:
    """Return human-facing status label (TODO/ACTIVE/DONE) for any known status token."""
    try:
        code = normalize_task_status(status)
    except ValueError:
        return (status or "").strip()
    return _CODE_TO_LABEL.get(code, (status or "").strip())


def task_status_code(status: str) -> TaskStatusCode:
    """Normalize any accepted status token to internal status code (OK/WARN/FAIL)."""
    code = normalize_task_status(status)
    return code  # type: ignore[return-value]
