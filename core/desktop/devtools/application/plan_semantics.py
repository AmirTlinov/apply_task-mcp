"""Plan semantics shared across MCP/TUI/GUI."""

from __future__ import annotations

from typing import Any, Optional

from datetime import datetime, timezone

from core import (
    ACTOR_AI,
    ACTOR_HUMAN,
    EVENT_CONTRACT_UPDATED,
    EVENT_PLAN_UPDATED,
    StepEvent,
    TaskDetail,
)


def normalize_tag(value: str) -> str:
    return str(value or "").strip().lower().lstrip("#")


def is_plan_task(detail: TaskDetail) -> bool:
    """Return True when the detail represents a Plan (not a Task)."""
    kind = str(getattr(detail, "kind", "") or "").strip().lower()
    if kind in {"plan", "task"}:
        return kind == "plan"
    return str(getattr(detail, "id", "") or "").startswith("PLAN-")


def contract_versions_count(plan: TaskDetail) -> int:
    versions = getattr(plan, "contract_versions", None) or []
    return len(versions) if isinstance(versions, list) else 0


def last_plan_contract_version(plan: TaskDetail) -> Optional[int]:
    """Return contract version snapshot stored on the last plan update event."""
    events = getattr(plan, "events", None) or []
    if not isinstance(events, list) or not events:
        return None
    try:
        events_sorted = sorted(events, key=lambda e: getattr(e, "timestamp", "") or "", reverse=True)
    except Exception:
        events_sorted = list(events)
    for e in events_sorted:
        if getattr(e, "event_type", "") != EVENT_PLAN_UPDATED:
            continue
        data = getattr(e, "data", None) or {}
        raw = data.get("contract_version") if isinstance(data, dict) else None
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None
    return None


def plan_stale(plan: TaskDetail) -> bool:
    """Return True when contract changed since the last plan update."""
    has_plan = bool(str(getattr(plan, "plan_doc", "") or "").strip()) or bool(getattr(plan, "plan_steps", []) or [])
    if not has_plan:
        return False
    current = contract_versions_count(plan)
    at_plan = last_plan_contract_version(plan)
    if at_plan is None:
        # If we don't have an explicit snapshot, treat plan as aligned.
        return False
    return current != at_plan


def mark_plan_updated(plan: TaskDetail, *, actor: str = ACTOR_HUMAN) -> None:
    """Append plan_updated event with contract version snapshot (best-effort)."""
    cv = contract_versions_count(plan)
    try:
        plan.events.append(
            StepEvent.now(
                EVENT_PLAN_UPDATED,
                actor=actor,
                target="",
                contract_version=cv,
            )
        )
    except Exception:
        # Do not fail plan edits because timeline logging failed.
        return


def append_contract_version_if_changed(plan: TaskDetail, *, actor: str = ACTOR_AI, note: str = "") -> bool:
    """Append a contract_versions entry when contract (text or data) changed.

    This keeps the contract history consistent across interfaces (TUI/GUI/MCP).

    Returns True if a new version entry was appended.
    """
    entries = list(getattr(plan, "contract_versions", []) or [])

    latest: Optional[dict] = None
    latest_v = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            v = int(entry.get("version"))
        except (TypeError, ValueError):
            continue
        if v >= latest_v:
            latest_v = v
            latest = entry

    current_text = str(getattr(plan, "contract", "") or "")
    current_done = list(getattr(plan, "success_criteria", []) or [])
    current_data = getattr(plan, "contract_data", None)
    if not isinstance(current_data, dict):
        current_data = {}

    if latest is not None:
        latest_text = str(latest.get("text", "") or "")
        latest_done = latest.get("done_criteria") or []
        if not isinstance(latest_done, list):
            latest_done = []
        latest_data = latest.get("data") if isinstance(latest.get("data"), dict) else {}
        if latest_text == current_text and list(latest_done) == current_done and latest_data == current_data:
            return False

    version = int(latest_v) + 1
    entries.append(
        {
            "version": version,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "text": current_text,
            "done_criteria": current_done,
            "data": dict(current_data),
        }
    )
    plan.contract_versions = entries
    try:
        plan.events.append(
            StepEvent.now(
                EVENT_CONTRACT_UPDATED,
                actor=actor,
                target="",
                version=version,
                note=str(note or "").strip(),
            )
        )
    except Exception:
        pass
    return True


__all__ = [
    "normalize_tag",
    "is_plan_task",
    "contract_versions_count",
    "last_plan_contract_version",
    "plan_stale",
    "mark_plan_updated",
    "append_contract_version_if_changed",
]
