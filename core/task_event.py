"""Structured task events for timeline tracking and audit.

Events provide a chronological log of all significant changes to a task,
enabling:
- Timeline view of task history
- Audit trail for checkpoint discipline
- Analytics on task duration and bottlenecks
- Session restoration for AI agents
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# Event types
EVENT_CREATED = "created"
EVENT_CHECKPOINT = "checkpoint"  # criteria/tests/blockers confirmed
EVENT_STATUS = "status"  # status changed (OK/WARN/FAIL)
EVENT_BLOCKED = "blocked"  # task became blocked
EVENT_UNBLOCKED = "unblocked"  # task became unblocked
EVENT_SUBTASK_DONE = "subtask_done"  # subtask completed
EVENT_COMMENT = "comment"  # manual note added
EVENT_DEPENDENCY_ADDED = "dependency_added"
EVENT_DEPENDENCY_RESOLVED = "dependency_resolved"

# Actors
ACTOR_AI = "ai"
ACTOR_HUMAN = "human"
ACTOR_SYSTEM = "system"


@dataclass
class TaskEvent:
    """A single event in task history.

    Attributes:
        timestamp: ISO 8601 timestamp when event occurred
        event_type: Type of event (created, checkpoint, status, etc.)
        actor: Who caused the event (ai, human, system)
        target: What was affected ("" for task-level, "subtask:0" for subtask)
        data: Event-specific payload
    """

    timestamp: str
    event_type: str
    actor: str = ACTOR_AI
    target: str = ""  # "" = task level, "subtask:0", "subtask:1.2" for nested
    data: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def now(cls, event_type: str, actor: str = ACTOR_AI, target: str = "", **data) -> "TaskEvent":
        """Create event with current timestamp."""
        return cls(
            timestamp=datetime.now(timezone.utc).isoformat(),
            event_type=event_type,
            actor=actor,
            target=target,
            data=data,
        )

    @classmethod
    def created(cls, actor: str = ACTOR_AI) -> "TaskEvent":
        """Create 'task created' event."""
        return cls.now(EVENT_CREATED, actor)

    @classmethod
    def checkpoint(
        cls,
        checkpoint_type: str,
        subtask_index: int,
        note: str = "",
        actor: str = ACTOR_AI,
    ) -> "TaskEvent":
        """Create checkpoint confirmation event.

        Args:
            checkpoint_type: 'criteria', 'tests', or 'blockers'
            subtask_index: Index of subtask (0-based)
            note: Optional evidence/note
            actor: Who confirmed
        """
        return cls.now(
            EVENT_CHECKPOINT,
            actor,
            target=f"subtask:{subtask_index}",
            checkpoint=checkpoint_type,
            note=note,
        )

    @classmethod
    def status_changed(cls, old_status: str, new_status: str, actor: str = ACTOR_AI) -> "TaskEvent":
        """Create status change event."""
        return cls.now(EVENT_STATUS, actor, old=old_status, new=new_status)

    @classmethod
    def subtask_done(cls, subtask_index: int, actor: str = ACTOR_AI) -> "TaskEvent":
        """Create subtask completion event."""
        return cls.now(EVENT_SUBTASK_DONE, actor, target=f"subtask:{subtask_index}")

    @classmethod
    def blocked(cls, reason: str, blocker_task: Optional[str] = None, actor: str = ACTOR_SYSTEM) -> "TaskEvent":
        """Create blocked event."""
        return cls.now(EVENT_BLOCKED, actor, reason=reason, blocker_task=blocker_task)

    @classmethod
    def unblocked(cls, actor: str = ACTOR_SYSTEM) -> "TaskEvent":
        """Create unblocked event."""
        return cls.now(EVENT_UNBLOCKED, actor)

    @classmethod
    def dependency_added(cls, depends_on: str, actor: str = ACTOR_AI) -> "TaskEvent":
        """Create dependency added event."""
        return cls.now(EVENT_DEPENDENCY_ADDED, actor, depends_on=depends_on)

    @classmethod
    def dependency_resolved(cls, depends_on: str, actor: str = ACTOR_SYSTEM) -> "TaskEvent":
        """Create dependency resolved event."""
        return cls.now(EVENT_DEPENDENCY_RESOLVED, actor, depends_on=depends_on)

    @classmethod
    def comment(cls, text: str, actor: str = ACTOR_AI) -> "TaskEvent":
        """Create comment/note event."""
        return cls.now(EVENT_COMMENT, actor, text=text)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary for storage."""
        return {
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "actor": self.actor,
            "target": self.target,
            "data": self.data,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskEvent":
        """Deserialize from dictionary."""
        return cls(
            timestamp=data.get("timestamp", ""),
            event_type=data.get("event_type", ""),
            actor=data.get("actor", ACTOR_AI),
            target=data.get("target", ""),
            data=data.get("data", {}),
        )

    @classmethod
    def from_legacy_history(cls, history_line: str) -> "TaskEvent":
        """Convert legacy history string to event.

        Legacy format: "2025-12-07: created" or just "created"
        """
        # Try to parse timestamp prefix
        if ": " in history_line and history_line[:10].replace("-", "").isdigit():
            timestamp_part = history_line.split(": ", 1)[0]
            text_part = history_line.split(": ", 1)[1] if ": " in history_line else history_line
        else:
            timestamp_part = ""
            text_part = history_line

        return cls(
            timestamp=timestamp_part or "",
            event_type="legacy",
            actor=ACTOR_AI,
            target="",
            data={"text": text_part},
        )

    def format_timeline(self) -> str:
        """Format event for timeline display."""
        ts = self.timestamp[:19].replace("T", " ") if self.timestamp else "unknown"

        if self.event_type == EVENT_CREATED:
            return f"[{ts}] Task created"
        elif self.event_type == EVENT_CHECKPOINT:
            checkpoint = self.data.get("checkpoint", "?")
            note = self.data.get("note", "")
            note_suffix = f" — {note}" if note else ""
            return f"[{ts}] {self.target}: {checkpoint} confirmed{note_suffix}"
        elif self.event_type == EVENT_STATUS:
            return f"[{ts}] Status: {self.data.get('old')} → {self.data.get('new')}"
        elif self.event_type == EVENT_SUBTASK_DONE:
            return f"[{ts}] {self.target} completed"
        elif self.event_type == EVENT_BLOCKED:
            reason = self.data.get("reason", "")
            blocker = self.data.get("blocker_task", "")
            return f"[{ts}] Blocked: {reason}" + (f" (by {blocker})" if blocker else "")
        elif self.event_type == EVENT_UNBLOCKED:
            return f"[{ts}] Unblocked"
        elif self.event_type == EVENT_DEPENDENCY_ADDED:
            return f"[{ts}] Dependency added: {self.data.get('depends_on')}"
        elif self.event_type == EVENT_DEPENDENCY_RESOLVED:
            return f"[{ts}] Dependency resolved: {self.data.get('depends_on')}"
        elif self.event_type == EVENT_COMMENT:
            return f"[{ts}] Note: {self.data.get('text', '')}"
        elif self.event_type == "legacy":
            return f"[{ts}] {self.data.get('text', '')}"
        else:
            return f"[{ts}] {self.event_type}: {self.data}"


def events_to_timeline(events: List[TaskEvent]) -> str:
    """Format list of events as human-readable timeline."""
    if not events:
        return "No events recorded."

    lines = []
    for event in sorted(events, key=lambda e: e.timestamp or ""):
        lines.append(event.format_timeline())

    return "\n".join(lines)


__all__ = [
    "TaskEvent",
    "events_to_timeline",
    # Event types
    "EVENT_CREATED",
    "EVENT_CHECKPOINT",
    "EVENT_STATUS",
    "EVENT_BLOCKED",
    "EVENT_UNBLOCKED",
    "EVENT_SUBTASK_DONE",
    "EVENT_COMMENT",
    "EVENT_DEPENDENCY_ADDED",
    "EVENT_DEPENDENCY_RESOLVED",
    # Actors
    "ACTOR_AI",
    "ACTOR_HUMAN",
    "ACTOR_SYSTEM",
]
