"""AI Session State Management for TUI.

This module manages the state of AI interactions, including:
- Current AI operation status
- AI plan visibility
- User signals (pause, stop, skip, message)
- Activity history for dashboard display
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
import json
import time


class AIStatus(Enum):
    """AI operation status."""
    IDLE = "idle"           # No AI activity
    THINKING = "thinking"   # AI is processing
    EXECUTING = "executing" # AI is executing tool
    WAITING = "waiting"     # AI is waiting for user
    PAUSED = "paused"       # User paused AI
    ERROR = "error"         # AI encountered error


class UserSignal(Enum):
    """Signals user can send to AI."""
    NONE = "none"
    PAUSE = "pause"         # Pause execution
    RESUME = "resume"       # Resume from pause
    STOP = "stop"           # Stop current task
    SKIP = "skip"           # Skip current subtask
    MESSAGE = "message"     # Send message to AI


@dataclass
class AIActivity:
    """Single AI activity entry for history."""
    timestamp: float
    operation: str          # Intent name
    task_id: Optional[str]
    path: Optional[str]
    summary: str            # Human-readable summary
    success: bool
    duration_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "time": datetime.fromtimestamp(self.timestamp).strftime("%H:%M:%S"),
            "op": self.operation,
            "task": self.task_id,
            "path": self.path,
            "summary": self.summary,
            "ok": self.success,
            "ms": self.duration_ms,
        }


@dataclass
class AIPlan:
    """AI's current plan for task execution."""
    task_id: str
    steps: List[str]                    # Planned steps (human readable)
    current_step: int = 0               # Currently executing step
    estimated_total: int = 0            # Estimated total operations

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task": self.task_id,
            "steps": self.steps,
            "current": self.current_step,
            "total": len(self.steps),
            "progress": f"{self.current_step}/{len(self.steps)}",
        }


@dataclass
class AISessionState:
    """Complete AI session state for TUI dashboard."""

    # Current status
    status: AIStatus = AIStatus.IDLE
    current_operation: Optional[str] = None
    current_task_id: Optional[str] = None
    current_path: Optional[str] = None

    # Plan
    plan: Optional[AIPlan] = None

    # Activity history (last N operations)
    history: List[AIActivity] = field(default_factory=list)
    history_max: int = 20

    # User signal
    pending_signal: UserSignal = UserSignal.NONE
    pending_message: str = ""

    # Timing
    operation_start: float = 0.0
    last_update: float = 0.0

    # Statistics
    operations_count: int = 0
    errors_count: int = 0

    def start_operation(self, operation: str, task_id: Optional[str] = None, path: Optional[str] = None) -> None:
        """Mark start of AI operation."""
        self.status = AIStatus.EXECUTING
        self.current_operation = operation
        self.current_task_id = task_id
        self.current_path = path
        self.operation_start = time.time()
        self.last_update = self.operation_start

    def end_operation(self, summary: str, success: bool = True) -> None:
        """Mark end of AI operation and add to history."""
        duration_ms = int((time.time() - self.operation_start) * 1000)

        activity = AIActivity(
            timestamp=self.operation_start,
            operation=self.current_operation or "unknown",
            task_id=self.current_task_id,
            path=self.current_path,
            summary=summary,
            success=success,
            duration_ms=duration_ms,
        )

        self.history.insert(0, activity)
        if len(self.history) > self.history_max:
            self.history.pop()

        self.operations_count += 1
        if not success:
            self.errors_count += 1

        self.status = AIStatus.IDLE
        self.current_operation = None
        self.last_update = time.time()

    def set_plan(self, task_id: str, steps: List[str]) -> None:
        """Set AI's execution plan."""
        self.plan = AIPlan(task_id=task_id, steps=steps, estimated_total=len(steps))

    def advance_plan(self) -> None:
        """Move to next step in plan."""
        if self.plan:
            self.plan.current_step = min(self.plan.current_step + 1, len(self.plan.steps))

    def clear_plan(self) -> None:
        """Clear current plan."""
        self.plan = None

    def send_signal(self, signal: UserSignal, message: str = "") -> None:
        """Send user signal to AI."""
        self.pending_signal = signal
        self.pending_message = message
        if signal == UserSignal.PAUSE:
            self.status = AIStatus.PAUSED
        elif signal == UserSignal.RESUME:
            self.status = AIStatus.IDLE

    def consume_signal(self) -> tuple[UserSignal, str]:
        """Consume pending signal (returns and clears it)."""
        signal = self.pending_signal
        message = self.pending_message
        self.pending_signal = UserSignal.NONE
        self.pending_message = ""
        return signal, message

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for JSON serialization."""
        return {
            "status": self.status.value,
            "current": {
                "op": self.current_operation,
                "task": self.current_task_id,
                "path": self.current_path,
            } if self.current_operation else None,
            "plan": self.plan.to_dict() if self.plan else None,
            "history": [a.to_dict() for a in self.history[:5]],  # Last 5 for summary
            "signal": {
                "pending": self.pending_signal.value,
                "message": self.pending_message,
            },
            "stats": {
                "total_ops": self.operations_count,
                "errors": self.errors_count,
            },
        }

    def to_status_line(self) -> str:
        """Generate status line for TUI status bar."""
        if self.status == AIStatus.IDLE:
            return ""
        elif self.status == AIStatus.PAUSED:
            return "[PAUSED]"
        elif self.status == AIStatus.EXECUTING:
            op = self.current_operation or "..."
            elapsed = int((time.time() - self.operation_start) * 1000)
            return f"AI:{op} ({elapsed}ms)"
        elif self.status == AIStatus.WAITING:
            return "AI:waiting"
        elif self.status == AIStatus.ERROR:
            return "AI:ERROR"
        return ""


# Global session state instance
_session_state: Optional[AISessionState] = None


def get_ai_state() -> AISessionState:
    """Get or create global AI session state."""
    global _session_state
    if _session_state is None:
        _session_state = AISessionState()
    return _session_state


def reset_ai_state() -> None:
    """Reset AI session state."""
    global _session_state
    _session_state = AISessionState()


# Signals file for cross-process communication
def _get_signals_file(tasks_dir: Optional[Path] = None) -> Path:
    """Get path to signals file."""
    if tasks_dir:
        return tasks_dir / ".ai_signals"
    return Path.home() / ".tasks" / ".ai_signals"


def write_user_signal(signal: UserSignal, message: str = "", tasks_dir: Optional[Path] = None) -> None:
    """Write user signal to file for AI to read."""
    signals_file = _get_signals_file(tasks_dir)
    signals_file.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "signal": signal.value,
        "message": message,
        "timestamp": time.time(),
    }
    signals_file.write_text(json.dumps(data))


def read_user_signal(tasks_dir: Optional[Path] = None) -> tuple[UserSignal, str]:
    """Read and consume user signal from file."""
    signals_file = _get_signals_file(tasks_dir)

    if not signals_file.exists():
        return UserSignal.NONE, ""

    try:
        data = json.loads(signals_file.read_text())
        signal = UserSignal(data.get("signal", "none"))
        message = data.get("message", "")
        timestamp = data.get("timestamp", 0)

        # Expire signals after 60 seconds
        if time.time() - timestamp > 60:
            signals_file.unlink(missing_ok=True)
            return UserSignal.NONE, ""

        # Consume signal
        signals_file.unlink(missing_ok=True)
        return signal, message
    except (json.JSONDecodeError, ValueError):
        signals_file.unlink(missing_ok=True)
        return UserSignal.NONE, ""


__all__ = [
    "AIStatus",
    "UserSignal",
    "AIActivity",
    "AIPlan",
    "AISessionState",
    "get_ai_state",
    "reset_ai_state",
    "write_user_signal",
    "read_user_signal",
]
