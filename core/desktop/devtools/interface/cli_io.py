import json
from datetime import datetime, timezone
from typing import Dict, Optional


def iso_timestamp() -> str:
    """UTC timestamp for structured CLI output."""
    return datetime.now(timezone.utc).isoformat()


def structured_response(
    command: str,
    *,
    status: str = "OK",
    message: str = "",
    payload: Optional[Dict] = None,
    summary: Optional[str] = None,
    exit_code: int = 0,
) -> int:
    """Unified JSON response for non-interactive commands."""
    body: Dict[str, object] = {
        "command": command,
        "status": status,
        "message": message,
        "timestamp": iso_timestamp(),
        "payload": payload or {},
    }
    if summary:
        body["summary"] = summary
    print(json.dumps(body, ensure_ascii=False, indent=2))
    return exit_code


def structured_error(command: str, message: str, *, payload: Optional[Dict] = None, status: str = "ERROR") -> int:
    """Short-hand for structured error responses."""
    return structured_response(command, status=status, message=message, payload=payload, exit_code=1)


def validation_response(command: str, success: bool, message: str, payload: Optional[Dict] = None) -> int:
    body = payload.copy() if payload else {}
    body["mode"] = "validate-only"
    label = f"{command}.validate"
    status = "OK" if success else "ERROR"
    return structured_response(
        label,
        status=status,
        message=message,
        payload=body,
        summary=message,
        exit_code=0 if success else 1,
    )


__all__ = ["iso_timestamp", "structured_response", "structured_error", "validation_response"]
