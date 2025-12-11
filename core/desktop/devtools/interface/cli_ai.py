"""AI-first CLI interface.

Когнитивная модель для ИИ-агентов:
- Декларативный интерфейс: ИИ описывает "что", система делает "как"
- Единый JSON in/out: предсказуемость и парсинг без регулярок
- Контекст в каждом ответе: ИИ всегда видит полную картину
- Семантические операции: decompose, define, verify, progress
- Подсказки следующих действий: снижение когнитивной нагрузки

Использование:
    tasks ai '{"intent": "context"}'
    tasks ai '{"intent": "decompose", "task": "TASK-001", "subtasks": [...]}'
    echo '{"intent": "..."}' | tasks ai

Формат ответа (всегда):
    {
        "success": bool,
        "intent": str,
        "result": {...},
        "context": {"task_id": str, "progress": int, ...},
        "suggestions": [{"action": str, "target": str, "reason": str}],
        "error": {"code": str, "message": str} | null
    }
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from core import SubTask

import re
from core.desktop.devtools.interface.tasks_dir_resolver import resolve_project_root

from core.desktop.devtools.application.task_manager import TaskManager, current_timestamp
from core.desktop.devtools.application.context import derive_domain_explicit
from core.desktop.devtools.interface.cli_activity import write_activity_marker
from core.desktop.devtools.interface.serializers import task_to_dict
from core.desktop.devtools.interface.cli_history import (
    OperationHistory,
    get_project_tasks_dir,
    get_global_storage_dir,
    get_project_namespace,
    migrate_to_global,
)


# ═══════════════════════════════════════════════════════════════════════════════
# SECURITY CONSTANTS & VALIDATORS
# ═══════════════════════════════════════════════════════════════════════════════

# Limits
MAX_JSON_SIZE = 10 * 1024 * 1024  # 10 MB
MAX_SUBTASKS = 1000
MAX_NESTING_DEPTH = 10
MAX_STRING_LENGTH = 10000
MAX_ARRAY_LENGTH = 1000

# Patterns
TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
PATH_PATTERN = re.compile(r"^[0-9]+(\.[0-9]+)*$")


def validate_task_id(task_id: str) -> Optional[str]:
    """Validate task_id for security (path traversal prevention).

    Returns error message if invalid, None if valid.
    """
    if not task_id:
        return "task_id пустой"
    if not isinstance(task_id, str):
        return "task_id должен быть строкой"
    if len(task_id) > 64:
        return "task_id слишком длинный (макс 64 символа)"
    # Prevent path traversal
    if ".." in task_id or "/" in task_id or "\\" in task_id:
        return "task_id содержит недопустимые символы"
    if not TASK_ID_PATTERN.match(task_id):
        return "task_id должен содержать только буквы, цифры, - и _"
    return None


def validate_path(path: str) -> Optional[str]:
    """Validate subtask path.

    Returns error message if invalid, None if valid.
    """
    if path is None:
        return "path не указан"
    path_str = str(path)
    if len(path_str) > 100:
        return "path слишком длинный"
    if not PATH_PATTERN.match(path_str):
        return "path должен быть в формате '0' или '0.1.2'"
    # Check nesting depth
    parts = path_str.split(".")
    if len(parts) > MAX_NESTING_DEPTH:
        return f"path слишком глубокий (макс {MAX_NESTING_DEPTH} уровней)"
    return None


def validate_string(value: Any, field_name: str, max_length: int = MAX_STRING_LENGTH) -> Optional[str]:
    """Validate string field."""
    if value is None:
        return None
    if not isinstance(value, str):
        return f"{field_name} должен быть строкой"
    if len(value) > max_length:
        return f"{field_name} слишком длинный (макс {max_length} символов)"
    return None


def validate_array(value: Any, field_name: str, max_length: int = MAX_ARRAY_LENGTH) -> Optional[str]:
    """Validate array field."""
    if value is None:
        return None
    if not isinstance(value, list):
        return f"{field_name} должен быть массивом"
    if len(value) > max_length:
        return f"{field_name} слишком длинный (макс {max_length} элементов)"
    return None


def validate_subtasks_data(subtasks: List[Dict], depth: int = 0) -> Optional[str]:
    """Validate subtasks structure recursively."""
    if depth > MAX_NESTING_DEPTH:
        return f"Слишком глубокая вложенность подзадач (макс {MAX_NESTING_DEPTH})"
    if len(subtasks) > MAX_SUBTASKS:
        return f"Слишком много подзадач (макс {MAX_SUBTASKS})"

    for i, st in enumerate(subtasks):
        if not isinstance(st, dict):
            return f"Подзадача {i} должна быть объектом"

        err = validate_string(st.get("title"), f"title подзадачи {i}", 500)
        if err:
            return err

        for field_name in ["criteria", "tests", "blockers"]:
            err = validate_array(st.get(field_name), f"{field_name} подзадачи {i}", 100)
            if err:
                return err

        # Check nested children
        children = st.get("children", [])
        if children:
            err = validate_subtasks_data(children, depth + 1)
            if err:
                return err

    return None


def _load_task(manager: TaskManager, task_id: str, domain: str = ""):
    """Domain-aware task loader."""
    return manager.load_task(task_id, domain or "")


# ═══════════════════════════════════════════════════════════════════════════════
# RESPONSE MODEL v2 - Compact and actionable for AI
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class TaskState:
    """Compact task state for AI context - minimal but sufficient.

    Designed for cognitive simplicity:
    - One-line progress: "3/5 (60%)"
    - Ready subtasks: paths of subtasks that can be marked done
    - Blocked subtasks: paths of subtasks waiting for checkpoints
    """
    task_id: str
    title: str
    status: str  # OK, WARN, FAIL
    progress: str  # "3/5 (60%)" format
    ready: List[str] = field(default_factory=list)  # paths ready for "done"
    blocked: List[str] = field(default_factory=list)  # paths waiting for verification
    next_path: Optional[str] = None  # suggested next subtask to work on

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.task_id,
            "title": self.title,
            "status": self.status,
            "progress": self.progress,
            "ready": self.ready,
            "blocked": self.blocked,
            "next": self.next_path,
        }

    @classmethod
    def from_task(cls, task) -> "TaskState":
        """Build TaskState from TaskDetail."""
        completed = sum(1 for st in task.subtasks if st.completed)
        total = len(task.subtasks)
        pct = round(completed / total * 100) if total > 0 else 0

        ready = []
        blocked = []
        next_path = None

        for i, st in enumerate(task.subtasks):
            path = str(i)
            if st.completed:
                continue

            # Check if ready for "done"
            is_ready = st.ready_for_completion()
            if is_ready:
                ready.append(path)
                if next_path is None:
                    next_path = path
            else:
                blocked.append(path)

        return cls(
            task_id=task.id,
            title=task.title,
            status=task.status,
            progress=f"{completed}/{total} ({pct}%)",
            ready=ready,
            blocked=blocked,
            next_path=next_path,
        )


@dataclass
class ActionHint:
    """Ready-to-use tool call hint for AI.

    Example: {"tool": "tasks_done", "args": {"task": "T-1", "path": "0"}}
    AI can copy-paste this to execute immediately.
    """
    tool: str  # MCP tool name
    args: Dict[str, Any]  # Ready arguments
    reason: str  # Why this action

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool": self.tool,
            "args": self.args,
            "why": self.reason,
        }


def generate_summary(intent: str, result: Dict[str, Any], task_state: Optional[TaskState] = None) -> str:
    """Generate concise English summary of operation result.

    Templates are designed for quick comprehension:
    - Max 80 chars
    - Action + result + next step hint
    """
    templates = {
        "context": lambda r: f"Context loaded. {r.get('total_tasks', 0)} tasks.",
        "create": lambda r: f"Created {r.get('task_id', 'task')}. Add subtasks with decompose.",
        "decompose": lambda r: f"Added {r.get('total_created', 0)} subtasks. Verify criteria when ready.",
        "define": lambda r: f"Defined {', '.join(r.get('updated', {}).keys())} at path {r.get('path', '?')}.",
        "verify": lambda r: f"Verified {', '.join(r.get('verified', {}).keys())} at path {r.get('path', '?')}.",
        "progress": lambda r: f"Marked path {r.get('path', '?')} {'complete' if r.get('completed') else 'incomplete'}.",
        "done": lambda r: _done_summary(r),
        "delete": lambda r: f"Deleted {r.get('deleted', {}).get('type', 'item')}.",
        "complete": lambda r: f"Task {r.get('task_id', '?')} completed with status {r.get('status', 'OK')}.",
        "batch": lambda r: f"Batch: {r.get('completed', 0)}/{r.get('total', 0)} operations.",
        "undo": lambda r: f"Undone: {r.get('undone_operation', {}).get('intent', '?')}.",
        "redo": lambda r: f"Redone: {r.get('redo_operation', {}).get('intent', '?')}.",
        "history": lambda r: f"{r.get('total', 0)} operations in history.",
        "storage": lambda r: f"Storage: {r.get('current_namespace', 'default')}.",
    }

    template = templates.get(intent, lambda r: f"{intent} completed.")
    summary = template(result)

    # Add task state hint if available
    if task_state and task_state.next_path:
        summary += f" Next: path {task_state.next_path}."

    return summary[:100]  # Hard limit


def _done_summary(result: Dict) -> str:
    """Generate summary for 'done' intent."""
    path = result.get("path", "?")
    if result.get("already_completed"):
        return f"Path {path} was already completed."
    verified = result.get("verified", {})
    auto_count = sum(1 for v in verified.values() if "auto" in str(v))
    if result.get("forced"):
        return f"Path {path} force-completed."
    if auto_count > 0:
        return f"Path {path} done ({auto_count} auto-verified)."
    return f"Path {path} done. Progress: {result.get('task_progress', 0)}%."


def generate_action_hints(manager: TaskManager, task_id: Optional[str] = None) -> List[ActionHint]:
    """Generate ready-to-use action hints for AI.

    Returns up to 3 most relevant actions.
    """
    hints = []

    if not task_id:
        # No current task - suggest getting context
        tasks = manager.list_tasks()
        fail_tasks = [t for t in tasks if t.status == "FAIL"]
        if fail_tasks:
            hints.append(ActionHint(
                tool="tasks_context",
                args={"task": fail_tasks[0].id},
                reason="Focus on incomplete task"
            ))
        return hints[:3]

    task = manager.load_task(task_id)
    if not task:
        return hints

    for i, st in enumerate(task.subtasks):
        if st.completed:
            continue

        path = str(i)

        # Check if ready for "done"
        if st.ready_for_completion():
            hints.append(ActionHint(
                tool="tasks_done",
                args={"task": task_id, "path": path},
                reason=f"Subtask ready: {st.title[:30]}"
            ))
        else:
            # Check what's missing
            if st.success_criteria and not st.criteria_confirmed:
                hints.append(ActionHint(
                    tool="tasks_verify",
                    args={
                        "task": task_id,
                        "path": path,
                        "checkpoints": {"criteria": {"confirmed": True}}
                    },
                    reason=f"Verify criteria: {st.title[:30]}"
                ))

    # If all subtasks done, suggest completing task
    if task.subtasks and all(st.completed for st in task.subtasks):
        hints.append(ActionHint(
            tool="tasks_complete",
            args={"task": task_id},
            reason="All subtasks done, complete task"
        ))

    return hints[:3]


@dataclass
class Suggestion:
    """Подсказка следующего действия для ИИ."""

    action: str
    target: str
    reason: str
    priority: str = "normal"  # high, normal, low
    params: Dict[str, Any] = field(default_factory=dict)  # Additional parameters for the action

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "action": self.action,
            "target": self.target,
            "reason": self.reason,
            "priority": self.priority,
        }
        if self.params:
            result["params"] = self.params
        return result


@dataclass
class ErrorDetail:
    """Семантическая ошибка с подсказкой восстановления."""

    code: str
    message: str
    recoverable: bool = True
    field: Optional[str] = None  # Поле с ошибкой (e.g., "subtasks[0].criteria")
    expected: Optional[str] = None  # Ожидаемый тип/формат
    got: Optional[str] = None  # Полученное значение
    recovery_action: Optional[str] = None  # Интент для восстановления
    recovery_hint: Optional[Dict[str, Any]] = None  # Готовый запрос для восстановления

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "code": self.code,
            "message": self.message,
            "recoverable": self.recoverable,
        }
        if self.field:
            d["field"] = self.field
        if self.expected:
            d["expected"] = self.expected
        if self.got:
            d["got"] = self.got
        if self.recovery_action:
            d["recovery"] = {
                "action": self.recovery_action,
                "hint": self.recovery_hint or {},
            }
        return d


@dataclass
class Meta:
    """Мини-контекст в каждом ответе — ИИ всегда видит картину."""

    task_id: Optional[str] = None
    task_status: Optional[str] = None
    task_progress: int = 0
    subtasks_total: int = 0
    subtasks_completed: int = 0
    pending_verifications: int = 0
    unresolved_blockers: int = 0
    next_action_hint: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_status": self.task_status,
            "task_progress": self.task_progress,
            "subtasks": {
                "total": self.subtasks_total,
                "completed": self.subtasks_completed,
            },
            "pending_verifications": self.pending_verifications,
            "unresolved_blockers": self.unresolved_blockers,
            "next_action_hint": self.next_action_hint,
        }


@dataclass
class AIResponse:
    """Unified response for AI - now with compact v2 fields.

    New v2 fields (cognitive simplicity):
    - summary: One-line English description of what happened
    - state: Compact TaskState with ready/blocked paths
    - hints: Ready-to-use ActionHint tool calls

    Legacy fields (context, suggestions, meta) still available for compatibility.
    """

    success: bool
    intent: str
    result: Dict[str, Any] = field(default_factory=dict)
    # v2 compact fields
    summary: str = ""  # One-line summary
    state: Optional[TaskState] = None  # Compact task state
    hints: List[ActionHint] = field(default_factory=list)  # Ready tool calls
    # Legacy fields (still supported)
    context: Dict[str, Any] = field(default_factory=dict)
    suggestions: List[Suggestion] = field(default_factory=list)
    meta: Optional[Meta] = None
    error: Optional[ErrorDetail] = None
    # Legacy fields for compatibility
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    # Idempotency
    idempotency_key: Optional[str] = None
    cached: bool = False  # True if result from idempotency cache

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "success": self.success,
            "intent": self.intent,
            "result": self.result,
            "timestamp": datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        # Include optional fields only if non-empty
        if self.summary:
            d["summary"] = self.summary
        if self.state:
            d["state"] = self.state.to_dict()
        if self.hints:
            d["hints"] = [h.to_dict() for h in self.hints]
        if self.context:
            d["context"] = self.context
        if self.suggestions:
            d["suggestions"] = [s.to_dict() for s in self.suggestions]
        if self.meta:
            d["meta"] = self.meta.to_dict()

        # Error handling - prefer new ErrorDetail over legacy
        if self.error:
            d["error"] = self.error.to_dict()
        elif self.error_code:
            d["error"] = {"code": self.error_code, "message": self.error_message}
        else:
            d["error"] = None

        # Idempotency info
        if self.idempotency_key:
            d["idempotency"] = {
                "key": self.idempotency_key,
                "cached": self.cached,
            }

        return d

    def to_compact_dict(self) -> Dict[str, Any]:
        """Return only v2 compact fields for minimal response size."""
        d = {
            "ok": self.success,
            "op": self.intent,
            "summary": self.summary,
        }
        if self.state:
            d["state"] = self.state.to_dict()
        if self.hints:
            d["hints"] = [h.to_dict() for h in self.hints]
        if self.error:
            d["error"] = {"code": self.error.code, "msg": self.error.message}
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


def build_meta(manager: TaskManager, task_id: Optional[str] = None, domain_filter: str = "") -> Meta:
    """Построить мини-контекст для ответа."""
    meta = Meta()

    if not task_id:
        return meta

    task = _load_task(manager, task_id, domain_filter)
    if not task:
        return meta

    meta.task_id = task_id
    meta.task_status = task.status
    meta.task_progress = task.calculate_progress()
    meta.subtasks_total = len(task.subtasks)
    meta.subtasks_completed = sum(1 for st in task.subtasks if st.completed)

    # Count pending verifications
    for st in task.subtasks:
        if st.success_criteria and not st.criteria_confirmed:
            meta.pending_verifications += 1
        if st.tests and not st.tests_confirmed:
            meta.pending_verifications += 1
        if st.blockers and not st.blockers_resolved:
            meta.unresolved_blockers += 1

    # Generate next action hint
    if meta.unresolved_blockers > 0:
        for i, st in enumerate(task.subtasks):
            if st.blockers and not st.blockers_resolved:
                meta.next_action_hint = f"resolve blockers at path {i}"
                break
    elif meta.pending_verifications > 0:
        for i, st in enumerate(task.subtasks):
            if st.success_criteria and not st.criteria_confirmed:
                meta.next_action_hint = f"verify criteria at path {i}"
                break
    elif meta.subtasks_completed == meta.subtasks_total and meta.subtasks_total > 0:
        meta.next_action_hint = "complete task"

    return meta


def error_response(
    intent: str,
    code: str,
    message: str,
    *,
    recoverable: bool = True,
    field: Optional[str] = None,
    expected: Optional[str] = None,
    got: Optional[str] = None,
    recovery_action: Optional[str] = None,
    recovery_hint: Optional[Dict[str, Any]] = None,
) -> AIResponse:
    """Создать ответ с семантической ошибкой."""
    error = ErrorDetail(
        code=code,
        message=message,
        recoverable=recoverable,
        field=field,
        expected=expected,
        got=got,
        recovery_action=recovery_action,
        recovery_hint=recovery_hint,
    )
    return AIResponse(
        success=False,
        intent=intent,
        error=error,
        error_code=code,  # Legacy compatibility
        error_message=message,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# CONTEXT BUILDER
# ═══════════════════════════════════════════════════════════════════════════════


def build_context(
    manager: TaskManager,
    task_id: Optional[str] = None,
    include_all_tasks: bool = False,
    compact: bool = False,
    domain_filter: str = "",
) -> Dict[str, Any]:
    """Построить контекст для ИИ.

    compact=True: minimal output for routine operations
    compact=False: full output for detailed inspection
    """
    ctx: Dict[str, Any] = {
        "tasks_dir": str(manager.tasks_dir) if manager.tasks_dir else None,
        "total_tasks": 0,
        "by_status": {"OK": 0, "WARN": 0, "FAIL": 0},
    }

    # Check for external changes (created/modified/deleted by user via TUI/CLI)
    external_changes = manager.get_and_clear_external_changes()
    if external_changes:
        ctx["external_changes"] = external_changes

    all_tasks = manager.list_tasks(domain_filter or "")
    ctx["total_tasks"] = len(all_tasks)

    for t in all_tasks:
        status = getattr(t, "status", "FAIL")
        ctx["by_status"][status] = ctx["by_status"].get(status, 0) + 1

    if include_all_tasks:
        ctx["tasks"] = [
            {
                "id": t.id,
                "title": t.title,
                "status": t.status,
                "progress": t.calculate_progress(),
                "subtasks_count": len(t.subtasks),
                "blocked": t.blocked,
            }
            for t in all_tasks
        ]

    if task_id:
        task = manager.load_task(task_id)
        if task:
            ctx["current_task"] = task_to_dict(task, include_subtasks=True, compact=compact)
            # Update snapshot for change detection
            manager.track_task(task_id, task)

    return ctx


def render_context_markdown(
    manager: TaskManager,
    task_id: Optional[str] = None,
    include_all_tasks: bool = False,
    domain_filter: str = "",
) -> str:
    """Render context as prompt-friendly markdown for LLM consumption.

    Optimized for:
    - Compact yet complete information
    - Clear structure for easy parsing
    - Actionable next steps
    - Minimal token usage
    """
    lines: List[str] = []
    all_tasks = manager.list_tasks(domain_filter or "")

    # Header with summary
    by_status = {"OK": 0, "WARN": 0, "FAIL": 0}
    for t in all_tasks:
        by_status[t.status] = by_status.get(t.status, 0) + 1

    lines.append(f"## Task Summary ({len(all_tasks)} total)")
    lines.append(f"✅ OK: {by_status['OK']} | ⚠️ WARN: {by_status['WARN']} | ❌ FAIL: {by_status['FAIL']}")
    lines.append("")

    # All tasks list (if requested or no specific task)
    if include_all_tasks or not task_id:
        lines.append("### All Tasks")
        for t in all_tasks:
            status_icon = {"OK": "✅", "WARN": "⚠️", "FAIL": "❌"}.get(t.status, "❓")
            progress = t.calculate_progress()
            blocked_marker = " 🔒BLOCKED" if t.blocked else ""
            lines.append(f"- {status_icon} `{t.id}` {t.title} ({progress}%){blocked_marker}")
        lines.append("")

    # Current task details
    if task_id:
        task = manager.load_task(task_id)
        if task:
            lines.append(f"## Current: {task.id}")
            lines.append(f"**{task.title}**")
            lines.append("")

            if task.description:
                lines.append(f"> {task.description}")
                lines.append("")

            # Status and progress
            status_icon = {"OK": "✅", "WARN": "⚠️", "FAIL": "❌"}.get(task.status, "❓")
            lines.append(f"Status: {status_icon} {task.status} | Progress: {task.calculate_progress()}%")

            # Dependencies
            if task.depends_on:
                blocked_deps = []
                for dep_id in task.depends_on:
                    dep_task = manager.load_task(dep_id)
                    if dep_task and dep_task.status != "OK":
                        blocked_deps.append(dep_id)
                if blocked_deps:
                    lines.append(f"🔒 Blocked by: {', '.join(blocked_deps)}")
                else:
                    lines.append(f"Dependencies: {', '.join(task.depends_on)} (all resolved)")
            lines.append("")

            # Subtasks
            if task.subtasks:
                lines.append("### Subtasks")
                _render_subtasks_md(task.subtasks, lines, "")
                lines.append("")

            # Success criteria
            if task.success_criteria:
                lines.append("### Success Criteria")
                for sc in task.success_criteria:
                    lines.append(f"- [ ] {sc}")
                lines.append("")

            # Risks
            if task.risks:
                lines.append("### Risks")
                for risk in task.risks:
                    lines.append(f"- ⚠️ {risk}")
                lines.append("")

            # Next actions
            pending_subtasks = [
                st for st in task.subtasks
                if not st.completed and st.criteria_confirmed and st.tests_confirmed
            ]
            if pending_subtasks:
                lines.append("### Ready for Completion")
                for st in pending_subtasks[:3]:
                    lines.append(f"- `{st.title}` — checkpoints confirmed, mark done")
            else:
                unconfirmed = [
                    st for st in task.subtasks
                    if not st.completed and (not st.criteria_confirmed or not st.tests_confirmed)
                ]
                if unconfirmed:
                    lines.append("### Next: Confirm Checkpoints")
                    for st in unconfirmed[:3]:
                        missing = []
                        if not st.criteria_confirmed:
                            missing.append("criteria")
                        if not st.tests_confirmed:
                            missing.append("tests")
                        lines.append(f"- `{st.title}` — confirm: {', '.join(missing)}")

    return "\n".join(lines)


def _render_subtasks_md(subtasks: List, lines: List[str], prefix: str, depth: int = 0) -> None:
    """Recursively render subtasks as markdown."""
    indent = "  " * depth
    for i, st in enumerate(subtasks):
        path = f"{prefix}{i}" if not prefix else f"{prefix}.{i}"
        checkbox = "x" if st.completed else " "
        checkpoint_status = ""
        if not st.completed:
            checks = []
            if st.criteria_confirmed:
                checks.append("✓crit")
            if st.tests_confirmed:
                checks.append("✓test")
            if st.blockers_resolved:
                checks.append("✓block")
            if checks:
                checkpoint_status = f" [{', '.join(checks)}]"
        lines.append(f"{indent}- [{checkbox}] **{path}** {st.title}{checkpoint_status}")

        # Show blockers if any
        if st.blockers and not st.blockers_resolved:
            for blocker in st.blockers:
                lines.append(f"{indent}  - 🚫 {blocker}")

        children = getattr(st, "children", [])
        if children:
            _render_subtasks_md(children, lines, path, depth + 1)


def _build_subtasks_tree(
    subtasks: List, prefix: str = ""
) -> List[Dict[str, Any]]:
    """Построить дерево подзадач с путями."""
    result = []
    for i, st in enumerate(subtasks):
        path = f"{prefix}{i}" if not prefix else f"{prefix}.{i}"
        node = {
            "path": path,
            "title": st.title,
            "completed": st.completed,
            "criteria": list(st.success_criteria),
            "tests": list(st.tests),
            "blockers": list(st.blockers),
            "checkpoints": {
                "criteria": st.criteria_confirmed,
                "tests": st.tests_confirmed,
                "blockers": st.blockers_resolved,
            },
            "notes": {
                "criteria": list(st.criteria_notes),
                "tests": list(st.tests_notes),
                "blockers": list(st.blockers_notes),
            },
            "created_at": getattr(st, "created_at", None),
            "completed_at": getattr(st, "completed_at", None),
        }
        children = getattr(st, "children", [])
        if children:
            node["children"] = _build_subtasks_tree(children, path)
        result.append(node)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# SUGGESTION ENGINE
# ═══════════════════════════════════════════════════════════════════════════════


def generate_suggestions(
    manager: TaskManager, task_id: Optional[str] = None, domain: str = ""
) -> List[Suggestion]:
    """Генерировать подсказки следующих действий."""
    suggestions = []

    if not task_id:
        # Нет текущей задачи - предложить выбрать или создать
        tasks = manager.list_tasks()
        fail_tasks = [t for t in tasks if t.status == "FAIL"]
        if fail_tasks:
            suggestions.append(
                Suggestion(
                    action="context",
                    target=fail_tasks[0].id,
                    reason=f"Есть {len(fail_tasks)} незавершённых задач",
                    priority="high",
                )
            )
        else:
            suggestions.append(
                Suggestion(
                    action="decompose",
                    target="new",
                    reason="Все задачи завершены, можно создать новую",
                )
            )
        return suggestions

    task = _load_task(manager, task_id, domain)
    if not task:
        # Fallback: try to locate task across all domains
        for candidate in manager.list_tasks("", skip_sync=True):
            if candidate.id == task_id:
                task = candidate
                break
    if not task:
        return suggestions

    # Анализ подзадач
    for i, st in enumerate(task.subtasks):
        path = str(i)

        # Нет критериев - предложить определить
        if not st.success_criteria:
            suggestions.append(
                Suggestion(
                    action="define",
                    target=path,
                    reason=f"Подзадача '{st.title}' без критериев успеха",
                    priority="high",
                )
            )
            continue

        # Критерии есть, но не подтверждены
        if st.success_criteria and not st.criteria_confirmed and st.completed:
            suggestions.append(
                Suggestion(
                    action="verify",
                    target=path,
                    reason=f"Критерии подзадачи '{st.title}' не верифицированы",
                    priority="high",
                )
            )

        # Тесты есть, но не подтверждены
        if st.tests and not st.tests_confirmed and st.completed:
            suggestions.append(
                Suggestion(
                    action="verify",
                    target=path,
                    reason=f"Тесты подзадачи '{st.title}' не верифицированы",
                )
            )

        # Блокеры не разрешены
        if st.blockers and not st.blockers_resolved:
            suggestions.append(
                Suggestion(
                    action="resolve",
                    target=path,
                    reason=f"Блокеры подзадачи '{st.title}' не разрешены",
                    priority="high",
                )
            )

        # Подзадача готова к завершению
        if (
            not st.completed
            and st.criteria_confirmed
            and (not st.tests or st.tests_confirmed)
            and (not st.blockers or st.blockers_resolved)
        ):
            suggestions.append(
                Suggestion(
                    action="progress",
                    target=path,
                    reason=f"Подзадача '{st.title}' готова к завершению",
                    priority="high",
                )
            )

    # Проверить готовность задачи к завершению
    if task.subtasks and all(st.completed for st in task.subtasks):
        all_verified = all(
            st.criteria_confirmed
            and (not st.tests or st.tests_confirmed)
            and (not st.blockers or st.blockers_resolved)
            for st in task.subtasks
        )
        if all_verified and task.status != "OK":
            suggestions.append(
                Suggestion(
                    action="complete",
                    target=task_id,
                    reason="Все подзадачи завершены и верифицированы",
                    priority="high",
                )
            )

    return suggestions[:5]  # Максимум 5 подсказок


# ═══════════════════════════════════════════════════════════════════════════════
# INTENT HANDLERS
# ═══════════════════════════════════════════════════════════════════════════════


def handle_context(
    manager: TaskManager, data: Dict[str, Any]
) -> AIResponse:
    """Получить полный контекст.

    Input:
        {"intent": "context"}
        {"intent": "context", "task": "TASK-001"}
        {"intent": "context", "include_all": true}
        {"intent": "context", "compact": true}  # minimal output
        {"intent": "context", "format": "markdown"}  # prompt-friendly markdown
    """
    task_id = data.get("task")
    include_all = data.get("include_all", False)
    compact = data.get("compact", False)
    output_format = data.get("format", "json")
    domain_path = derive_domain_explicit(data.get("domain", ""), data.get("phase"), data.get("component"))

    # Markdown format for LLM prompts
    if output_format == "markdown":
        markdown = render_context_markdown(
            manager, task_id, include_all_tasks=include_all, domain_filter=domain_path
        )
        return AIResponse(
            success=True,
            intent="context",
            result={"markdown": markdown},
            summary="Context rendered as markdown for LLM prompt",
        )

    # Default JSON format
    ctx = build_context(manager, task_id, include_all_tasks=include_all, compact=compact, domain_filter=domain_path)
    suggestions = generate_suggestions(manager, task_id, domain_path)

    return AIResponse(
        success=True,
        intent="context",
        result={"snapshot": ctx},
        context=ctx,
        suggestions=suggestions,
    )


def handle_resume(
    manager: TaskManager, data: Dict[str, Any]
) -> AIResponse:
    """Restore AI session context with timeline and dependencies.

    Designed for AI agents resuming work after context loss.
    Provides:
    - Last task being worked on (from .last file)
    - Task state with all checkpoints
    - Event timeline (recent changes)
    - Dependency status (blocked by which tasks)
    - Clear next action suggestions

    Input:
        {"intent": "resume"}
        {"intent": "resume", "task": "TASK-001"}  # specific task
        {"intent": "resume", "events_limit": 20}  # limit events

    Output:
        {
            "success": true,
            "intent": "resume",
            "result": {
                "task": {...full task with subtasks...},
                "timeline": [...recent events...],
                "dependencies": {
                    "depends_on": ["TASK-001", "TASK-002"],
                    "blocked_by": ["TASK-001"],  # incomplete deps
                    "blocking": ["TASK-005"]  # tasks waiting for this one
                },
                "checkpoint_status": {
                    "pending": ["0", "1.0"],  # subtask paths needing checkpoints
                    "ready": ["2"]  # subtasks ready for completion
                }
            },
            "suggestions": [...]
        }
    """
    from core import events_to_timeline, get_blocked_by_dependencies
    from core.desktop.devtools.application.context import get_last_task

    task_id = data.get("task")
    events_limit = data.get("events_limit", 20)
    domain_path = derive_domain_explicit(data.get("domain", ""), data.get("phase"), data.get("component"))

    # If no task specified, get last task from context
    if not task_id:
        last = get_last_task()
        if last:
            task_id, _ = last  # get_last_task() returns (task_id, domain) tuple

    if not task_id:
        return AIResponse(
            success=True,
            intent="resume",
            result={
                "task": None,
                "timeline": [],
                "dependencies": {},
                "checkpoint_status": {},
                "message": "No last task. Use 'context' intent with include_all=true to see all tasks.",
            },
            context=build_context(manager, include_all_tasks=True, domain_filter=domain_path),
            suggestions=[
                Suggestion(
                    action="context",
                    target="",
                    reason="List all tasks to choose one",
                    priority="high",
                    params={"include_all": True},
                )
            ],
        )

    # Load the task
    task = manager.load_task(task_id, domain_path)
    if not task:
        return error_response(
            "resume",
            "TASK_NOT_FOUND",
            f"Task {task_id} not found",
            recovery_action="context",
            recovery_hint={"include_all": True},
        )

    # Build task dict with full details
    task_dict = task_to_dict(task, include_subtasks=True)

    # Build timeline from events
    timeline = []
    if task.events:
        sorted_events = sorted(task.events, key=lambda e: e.timestamp or "", reverse=True)
        for event in sorted_events[:events_limit]:
            timeline.append({
                "timestamp": event.timestamp,
                "type": event.event_type,
                "actor": event.actor,
                "target": event.target,
                "data": event.data,
                "formatted": event.format_timeline(),
            })

    # Build dependency status
    all_tasks = manager.list_all_tasks()
    task_statuses = {t.id: t.status for t in all_tasks}

    dependencies_info: Dict[str, Any] = {
        "depends_on": list(task.depends_on),
        "blocked_by": get_blocked_by_dependencies(task.id, task.depends_on, task_statuses),
        "blocking": [],  # Tasks that depend on this one
    }

    # Find tasks that are blocked by this task
    for t in all_tasks:
        if task.id in t.depends_on:
            dependencies_info["blocking"].append(t.id)

    # Build checkpoint status for subtasks
    checkpoint_status: Dict[str, List[str]] = {
        "pending": [],  # Need checkpoints confirmed
        "ready": [],    # Ready for completion
    }

    def analyze_subtasks(subtasks, prefix: str = "") -> None:
        for i, st in enumerate(subtasks):
            path = f"{prefix}{i}" if not prefix else f"{prefix}.{i}"
            if st.completed:
                continue
            if st.ready_for_completion():
                checkpoint_status["ready"].append(path)
            else:
                checkpoint_status["pending"].append(path)
            # Recurse into children
            if hasattr(st, "children") and st.children:
                analyze_subtasks(st.children, f"{path}.")

    analyze_subtasks(task.subtasks)

    # Build suggestions
    suggestions = []
    if checkpoint_status["ready"]:
        first_ready = checkpoint_status["ready"][0]
        suggestions.append(
            Suggestion(
                action="done",
                target=first_ready,
                reason=f"Subtask {first_ready} has all checkpoints confirmed, ready for completion",
                priority="high",
                params={"task": task_id, "path": first_ready},
            )
        )
    elif checkpoint_status["pending"]:
        first_pending = checkpoint_status["pending"][0]
        suggestions.append(
            Suggestion(
                action="verify",
                target=first_pending,
                reason=f"Subtask {first_pending} needs checkpoint confirmation",
                priority="high",
                params={"task": task_id, "path": first_pending},
            )
        )

    if dependencies_info["blocked_by"]:
        blocker = dependencies_info["blocked_by"][0]
        suggestions.append(
            Suggestion(
                action="context",
                target=blocker,
                reason=f"Task is blocked by {blocker}, consider working on it first",
                priority="medium",
                params={"task": blocker},
            )
        )

    return AIResponse(
        success=True,
        intent="resume",
        result={
            "task": task_dict,
            "timeline": timeline,
            "dependencies": dependencies_info,
            "checkpoint_status": checkpoint_status,
        },
        context={
            "task_id": task.id,
            "status": task.status,
            "progress": task.calculate_progress(),
            "blocked": task.blocked,
            "events_count": len(task.events),
            "deps_count": len(task.depends_on),
        },
        suggestions=suggestions,
    )


def handle_decompose(
    manager: TaskManager, data: Dict[str, Any]
) -> AIResponse:
    """Декомпозировать задачу на подзадачи.

    Input:
        {
            "intent": "decompose",
            "task": "TASK-001",
            "subtasks": [
                {
                    "title": "Реализовать API",
                    "criteria": ["Endpoint возвращает 200", "Данные валидны"],
                    "tests": ["test_api_returns_200"],
                    "blockers": []
                },
                {
                    "title": "Написать тесты",
                    "criteria": ["Coverage > 80%"],
                    "tests": ["pytest --cov"],
                    "blockers": ["Зависит от API"]
                }
            ]
        }

    Или для вложенных:
        {
            "intent": "decompose",
            "task": "TASK-001",
            "parent": "0",  # путь родительской подзадачи
            "subtasks": [...]
        }
    """
    task_id = data.get("task")
    domain_path = derive_domain_explicit(data.get("domain", ""), data.get("phase"), data.get("component"))
    if not task_id:
        return error_response("decompose", "MISSING_TASK", "Поле 'task' обязательно")

    # SEC-001: Validate task_id
    err = validate_task_id(task_id)
    if err:
        return error_response("decompose", "INVALID_TASK_ID", err)

    subtasks_data = data.get("subtasks", [])
    if not subtasks_data:
        return error_response(
            "decompose", "MISSING_SUBTASKS", "Поле 'subtasks' обязательно"
        )

    # SEC-002: Validate subtasks structure
    err = validate_subtasks_data(subtasks_data)
    if err:
        return error_response("decompose", "INVALID_SUBTASKS", err)

    parent_path = data.get("parent")
    # Validate parent path if provided
    if parent_path is not None:
        err = validate_path(parent_path)
        if err:
            return error_response("decompose", "INVALID_PATH", err)

    created = []
    errors = []

    for idx, st_data in enumerate(subtasks_data):
        title = st_data.get("title", "")
        if not title:
            continue

        criteria = st_data.get("criteria", [])
        tests = st_data.get("tests", [])
        blockers = st_data.get("blockers", [])

        # Создать подзадачу - API возвращает Tuple[bool, Optional[str]]
        success, error = manager.add_subtask(
            task_id,
            title,
            domain=domain_path,
            criteria=criteria,
            tests=tests,
            blockers=blockers,
            parent_path=parent_path,
        )

        if success:
            created.append(
                {
                    "path": parent_path + f".{idx}" if parent_path else str(idx),
                    "title": title,
                    "criteria": criteria,
                    "tests": tests,
                    "blockers": blockers,
                }
            )
        else:
            # Track failed subtasks
            error_detail = {
                "index": idx,
                "title": title,
                "error": error or "unknown",
            }
            if error == "missing_fields":
                # Normal mode: only criteria is required
                missing = []
                if not criteria:
                    missing.append("criteria")
                error_detail["missing"] = missing
                error_detail["note"] = "Normal mode: only criteria is required, tests/blockers are optional"
            errors.append(error_detail)

    # Return error if no subtasks were created
    if not created and errors:
        return error_response(
            "decompose",
            "NO_SUBTASKS_CREATED",
            f"Не удалось создать подзадачи: {len(errors)} ошибок",
            recovery_hint={"errors": errors, "required_fields": ["criteria", "tests", "blockers"]},
        )

    # Записать маркер активности для TUI
    write_activity_marker(
        task_id,
        "decompose",
        subtask_path=parent_path,
        tasks_dir=getattr(manager, "tasks_dir", None),
    )

    ctx = build_context(manager, task_id, domain_filter=domain_path)
    suggestions = generate_suggestions(manager, task_id, domain_path)

    return AIResponse(
        success=True,
        intent="decompose",
        result={
            "created": created,
            "total_created": len(created),
            "errors": errors if errors else None,
        },
        context=ctx,
        suggestions=suggestions,
    )


def handle_define(
    manager: TaskManager, data: Dict[str, Any]
) -> AIResponse:
    """Определить атрибуты подзадачи.

    Input:
        {
            "intent": "define",
            "task": "TASK-001",
            "path": "0",
            "criteria": ["Критерий 1", "Критерий 2"],
            "tests": ["test_something"],
            "blockers": ["Блокер"]
        }
    """
    task_id = data.get("task")
    path = data.get("path")
    domain_path = derive_domain_explicit(data.get("domain", ""), data.get("phase"), data.get("component"))

    if not task_id:
        return error_response("define", "MISSING_TASK", "Поле 'task' обязательно")

    # SEC-001: Validate task_id
    err = validate_task_id(task_id)
    if err:
        return error_response("define", "INVALID_TASK_ID", err)

    if path is None:
        return error_response("define", "MISSING_PATH", "Поле 'path' обязательно")

    # Validate path
    err = validate_path(path)
    if err:
        return error_response("define", "INVALID_PATH", err)

    # SEC-002: Validate arrays
    for field_name in ["criteria", "tests", "blockers"]:
        if field_name in data:
            err = validate_array(data[field_name], field_name, 100)
            if err:
                return error_response("define", "INVALID_DATA", err)

    task = _load_task(manager, task_id, domain_path)
    if not task:
        return error_response("define", "TASK_NOT_FOUND", f"Задача {task_id} не найдена")

    # Найти подзадачу по пути
    subtask = _get_subtask_by_path(task.subtasks, str(path))
    if not subtask:
        return error_response(
            "define", "SUBTASK_NOT_FOUND", f"Подзадача по пути '{path}' не найдена"
        )

    # Обновить атрибуты
    updated = {}
    if "criteria" in data:
        subtask.success_criteria = list(data["criteria"])
        updated["criteria"] = subtask.success_criteria
    if "tests" in data:
        subtask.tests = list(data["tests"])
        updated["tests"] = subtask.tests
    if "blockers" in data:
        subtask.blockers = list(data["blockers"])
        updated["blockers"] = subtask.blockers

    # Сохранить
    manager.save_task(task)
    write_activity_marker(
        task_id,
        "define",
        subtask_path=str(path),
        tasks_dir=getattr(manager, "tasks_dir", None),
    )

    ctx = build_context(manager, task_id, domain_filter=domain_path)
    suggestions = generate_suggestions(manager, task_id, domain_path)

    return AIResponse(
        success=True,
        intent="define",
        result={
            "path": str(path),
            "updated": updated,
        },
        context=ctx,
        suggestions=suggestions,
    )


def handle_verify(
    manager: TaskManager, data: Dict[str, Any]
) -> AIResponse:
    """Верифицировать чекпоинты подзадачи.

    Input:
        {
            "intent": "verify",
            "task": "TASK-001",
            "path": "0",
            "checkpoints": {
                "criteria": {"confirmed": true, "note": "Все критерии выполнены"},
                "tests": {"confirmed": true, "note": "pytest passed"},
                "blockers": {"confirmed": true, "note": "Разрешены"}
            }
        }

    Можно верифицировать частично - только указанные чекпоинты.
    """
    task_id = data.get("task")
    path = data.get("path")
    checkpoints = data.get("checkpoints", {})
    domain_path = derive_domain_explicit(data.get("domain", ""), data.get("phase"), data.get("component"))

    if not task_id:
        return error_response("verify", "MISSING_TASK", "Поле 'task' обязательно")

    # SEC-001: Validate task_id
    err = validate_task_id(task_id)
    if err:
        return error_response("verify", "INVALID_TASK_ID", err)

    if path is None:
        return error_response("verify", "MISSING_PATH", "Поле 'path' обязательно")

    # Validate path
    err = validate_path(path)
    if err:
        return error_response("verify", "INVALID_PATH", err)

    task = _load_task(manager, task_id, domain_path)
    if not task:
        return error_response("verify", "TASK_NOT_FOUND", f"Задача {task_id} не найдена")

    subtask = _get_subtask_by_path(task.subtasks, str(path))
    if not subtask:
        return error_response(
            "verify", "SUBTASK_NOT_FOUND", f"Подзадача по пути '{path}' не найдена"
        )

    verified = {}

    # Критерии
    if "criteria" in checkpoints:
        cp = checkpoints["criteria"]
        if cp.get("confirmed"):
            subtask.criteria_confirmed = True
            if cp.get("note"):
                subtask.criteria_notes.append(cp["note"])
            verified["criteria"] = True

    # Тесты
    if "tests" in checkpoints:
        cp = checkpoints["tests"]
        if cp.get("confirmed"):
            subtask.tests_confirmed = True
            if cp.get("note"):
                subtask.tests_notes.append(cp["note"])
            verified["tests"] = True

    # Блокеры
    if "blockers" in checkpoints:
        cp = checkpoints["blockers"]
        if cp.get("confirmed"):
            subtask.blockers_resolved = True
            if cp.get("note"):
                subtask.blockers_notes.append(cp["note"])
            verified["blockers"] = True

    manager.save_task(task)
    write_activity_marker(
        task_id,
        "verify",
        subtask_path=str(path),
        tasks_dir=getattr(manager, "tasks_dir", None),
    )

    ctx = build_context(manager, task_id, domain_filter=domain_path)
    suggestions = generate_suggestions(manager, task_id, domain_path)

    return AIResponse(
        success=True,
        intent="verify",
        result={
            "path": str(path),
            "verified": verified,
            "subtask_state": {
                "criteria_confirmed": subtask.criteria_confirmed,
                "tests_confirmed": subtask.tests_confirmed,
                "blockers_resolved": subtask.blockers_resolved,
            },
        },
        context=ctx,
        suggestions=suggestions,
    )


def handle_done(
    manager: TaskManager, data: Dict[str, Any]
) -> AIResponse:
    """Unified completion: auto-verify all checkpoints + mark as completed.

    Replaces 4 calls with 1:
        OLD: verify(criteria) → verify(tests) → verify(blockers) → progress(completed)
        NEW: done(path, note)

    Normal mode logic:
        - criteria: MUST be confirmed (explicit confirmation required)
        - tests: auto-OK if tests_auto_confirmed=True (empty at creation), otherwise must confirm
        - blockers: auto-OK if blockers_auto_resolved=True (empty at creation), otherwise must confirm

    Input:
        {"intent": "done", "task": "TASK-001", "path": "0"}
        {"intent": "done", "task": "TASK-001", "path": "0", "note": "All tests passed"}
        {"intent": "done", "task": "TASK-001", "path": "0.1", "force": true}  # skip verification

    Note field is optional and will be added to all checkpoint notes.
    """
    task_id = data.get("task")
    path = data.get("path")
    note = data.get("note", "").strip()
    force = data.get("force", False)
    domain_path = derive_domain_explicit(data.get("domain", ""), data.get("phase"), data.get("component"))

    if not task_id:
        return error_response("done", "MISSING_TASK", "Field 'task' is required")

    # SEC-001: Validate task_id
    err = validate_task_id(task_id)
    if err:
        return error_response("done", "INVALID_TASK_ID", err)

    if path is None:
        return error_response("done", "MISSING_PATH", "Field 'path' is required")

    # SEC-001: Validate path
    err = validate_path(str(path))
    if err:
        return error_response("done", "INVALID_PATH", err)

    task = _load_task(manager, task_id, domain_path)
    if not task:
        return error_response("done", "TASK_NOT_FOUND", f"Task {task_id} not found")

    subtask = _get_subtask_by_path(task.subtasks, str(path))
    if not subtask:
        return error_response(
            "done", "SUBTASK_NOT_FOUND", f"Subtask at path '{path}' not found"
        )

    # Check if already completed
    if subtask.completed:
        ctx = build_context(manager, task_id, domain_filter=domain_path)
        return AIResponse(
            success=True,
            intent="done",
            result={
                "path": str(path),
                "already_completed": True,
                "completed_at": subtask.completed_at,
            },
            context=ctx,
        )

    # Auto-verify checkpoints based on Normal mode logic
    verified = {}
    blocking_issues = []

    # Criteria: auto-confirm if note provided, otherwise must be pre-confirmed
    if subtask.success_criteria:
        if not subtask.criteria_confirmed:
            if note:
                # Auto-confirm criteria when note is provided
                subtask.criteria_confirmed = True
                subtask.criteria_notes.append(note)
                verified["criteria"] = "auto_with_note"
            elif force:
                subtask.criteria_confirmed = True
                verified["criteria"] = "forced"
            else:
                blocking_issues.append("criteria not confirmed")
        else:
            verified["criteria"] = "already_confirmed"
    else:
        verified["criteria"] = "empty"

    # Tests: auto-OK if tests_auto_confirmed, otherwise must confirm
    if subtask.tests:
        if subtask.tests_auto_confirmed or subtask.tests_confirmed:
            verified["tests"] = "auto" if subtask.tests_auto_confirmed else "already_confirmed"
        else:
            if force:
                subtask.tests_confirmed = True
                if note:
                    subtask.tests_notes.append(f"[FORCE] {note}")
                verified["tests"] = "forced"
            else:
                blocking_issues.append("tests not confirmed")
    else:
        # No tests defined - auto-OK
        if not subtask.tests_auto_confirmed:
            subtask.tests_auto_confirmed = True
        verified["tests"] = "auto_empty"

    # Blockers: auto-OK if blockers_auto_resolved, otherwise must confirm
    if subtask.blockers:
        if subtask.blockers_auto_resolved or subtask.blockers_resolved:
            verified["blockers"] = "auto" if subtask.blockers_auto_resolved else "already_resolved"
        else:
            if force:
                subtask.blockers_resolved = True
                if note:
                    subtask.blockers_notes.append(f"[FORCE] {note}")
                verified["blockers"] = "forced"
            else:
                blocking_issues.append("blockers not resolved")
    else:
        # No blockers defined - auto-OK
        if not subtask.blockers_auto_resolved:
            subtask.blockers_auto_resolved = True
        verified["blockers"] = "auto_empty"

    # Check children completion
    if subtask.children:
        incomplete_children = [ch for ch in subtask.children if not ch.completed]
        if incomplete_children:
            blocking_issues.append(f"{len(incomplete_children)} children not completed")

    # If blocking issues and not forced, return error
    if blocking_issues and not force:
        return error_response(
            "done",
            "CANNOT_COMPLETE",
            f"Cannot complete subtask: {', '.join(blocking_issues)}",
            recovery_action="done",
            recovery_hint={
                "task": task_id,
                "path": str(path),
                "force": True,
                "note": "Forced completion",
            },
        )

    # All checks passed or forced - mark as completed
    subtask.completed = True
    subtask.completed_at = current_timestamp()

    # Confirm checkpoints that weren't auto-confirmed
    if not subtask.criteria_confirmed and subtask.success_criteria:
        subtask.criteria_confirmed = True
    if not subtask.tests_confirmed and not subtask.tests_auto_confirmed:
        subtask.tests_confirmed = True
    if not subtask.blockers_resolved and not subtask.blockers_auto_resolved:
        subtask.blockers_resolved = True

    # Update task status
    task.update_status_from_progress()
    manager.save_task(task)

    write_activity_marker(
        task_id,
        "done",
        subtask_path=str(path),
        tasks_dir=getattr(manager, "tasks_dir", None),
    )

    ctx = build_context(manager, task_id, domain_filter=domain_path)
    suggestions = generate_suggestions(manager, task_id, domain_path)
    meta = build_meta(manager, task_id, domain_filter=domain_path)

    return AIResponse(
        success=True,
        intent="done",
        result={
            "path": str(path),
            "completed": True,
            "completed_at": subtask.completed_at,
            "verified": verified,
            "forced": force,
            "task_progress": ctx.get("current_task", {}).get("progress", 0),
        },
        context=ctx,
        suggestions=suggestions,
        meta=meta,
    )


def handle_progress(
    manager: TaskManager, data: Dict[str, Any]
) -> AIResponse:
    """Отметить прогресс подзадачи.

    Input:
        {"intent": "progress", "task": "TASK-001", "path": "0", "completed": true}
        {"intent": "progress", "task": "TASK-001", "path": "0.1", "completed": false}

    NOTE: Consider using "done" intent instead for unified completion with auto-verification.
    """
    task_id = data.get("task")
    path = data.get("path")
    completed = data.get("completed", True)
    domain_path = derive_domain_explicit(data.get("domain", ""), data.get("phase"), data.get("component"))

    if not task_id:
        return error_response("progress", "MISSING_TASK", "Поле 'task' обязательно")

    # SEC-001: Validate task_id
    err = validate_task_id(task_id)
    if err:
        return error_response("progress", "INVALID_TASK_ID", err)

    if path is None:
        return error_response("progress", "MISSING_PATH", "Поле 'path' обязательно")

    # SEC-001: Validate path
    err = validate_path(str(path))
    if err:
        return error_response("progress", "INVALID_PATH", err)

    # Используем существующий метод - возвращает Tuple[bool, Optional[str]]
    # Нужно преобразовать path в int для совместимости
    try:
        path_parts = str(path).split(".")
        index = int(path_parts[0])
        nested_path = ".".join(path_parts[1:]) if len(path_parts) > 1 else None
    except (ValueError, IndexError):
        return error_response("progress", "INVALID_PATH", f"Некорректный путь: {path}")

    success, error = manager.set_subtask(task_id, index, completed, domain=domain_path, path=nested_path)

    if not success:
        return error_response(
            "progress",
            "UPDATE_FAILED",
            error or "Не удалось обновить подзадачу",
        )

    write_activity_marker(
        task_id,
        "progress",
        subtask_path=str(path),
        tasks_dir=getattr(manager, "tasks_dir", None),
    )

    ctx = build_context(manager, task_id, domain_filter=domain_path)
    suggestions = generate_suggestions(manager, task_id, domain_path)

    return AIResponse(
        success=True,
        intent="progress",
        result={
            "path": str(path),
            "completed": completed,
            "task_progress": ctx.get("current_task", {}).get("progress", 0),
        },
        context=ctx,
        suggestions=suggestions,
    )


def handle_note(
    manager: TaskManager, data: Dict[str, Any]
) -> AIResponse:
    """Add progress note to subtask without marking complete.

    Input:
        {"intent": "note", "task": "TASK-001", "path": "0", "note": "Implemented auth logic"}
    """
    task_id = data.get("task")
    path = data.get("path")
    note = data.get("note", "")
    domain_path = derive_domain_explicit(data.get("domain", ""), data.get("phase"), data.get("component"))

    if not task_id:
        return error_response("note", "MISSING_TASK", "Field 'task' is required")

    err = validate_task_id(task_id)
    if err:
        return error_response("note", "INVALID_TASK_ID", err)

    if path is None:
        return error_response("note", "MISSING_PATH", "Field 'path' is required")

    err = validate_path(str(path))
    if err:
        return error_response("note", "INVALID_PATH", err)

    if not note:
        return error_response("note", "MISSING_NOTE", "Field 'note' is required")

    task = _load_task(manager, task_id, domain_path)
    if not task:
        return error_response("note", "TASK_NOT_FOUND", f"Task {task_id} not found")

    subtask = _get_subtask_by_path(task.subtasks, str(path))
    if not subtask:
        return error_response("note", "SUBTASK_NOT_FOUND", f"Subtask at path '{path}' not found")

    # Add note to progress_notes
    subtask.progress_notes.append(note)

    # Auto-set started_at if not set
    if not subtask.started_at:
        subtask.started_at = datetime.now().isoformat()

    manager.save_task(task)

    write_activity_marker(
        task_id,
        "note",
        subtask_path=str(path),
        tasks_dir=getattr(manager, "tasks_dir", None),
    )

    ctx = build_context(manager, task_id, domain_filter=domain_path)
    suggestions = generate_suggestions(manager, task_id, domain_path)

    return AIResponse(
        success=True,
        intent="note",
        result={
            "path": str(path),
            "note": note,
            "total_notes": len(subtask.progress_notes),
            "computed_status": subtask.computed_status,
        },
        context=ctx,
        suggestions=suggestions,
    )


def handle_block(
    manager: TaskManager, data: Dict[str, Any]
) -> AIResponse:
    """Block or unblock a subtask.

    Input:
        {"intent": "block", "task": "TASK-001", "path": "0", "blocked": true, "reason": "Waiting for API"}
        {"intent": "block", "task": "TASK-001", "path": "0", "blocked": false}
    """
    task_id = data.get("task")
    path = data.get("path")
    blocked = data.get("blocked", True)
    reason = data.get("reason", "")
    domain_path = derive_domain_explicit(data.get("domain", ""), data.get("phase"), data.get("component"))

    if not task_id:
        return error_response("block", "MISSING_TASK", "Field 'task' is required")

    err = validate_task_id(task_id)
    if err:
        return error_response("block", "INVALID_TASK_ID", err)

    if path is None:
        return error_response("block", "MISSING_PATH", "Field 'path' is required")

    err = validate_path(str(path))
    if err:
        return error_response("block", "INVALID_PATH", err)

    task = _load_task(manager, task_id, domain_path)
    if not task:
        return error_response("block", "TASK_NOT_FOUND", f"Task {task_id} not found")

    subtask = _get_subtask_by_path(task.subtasks, str(path))
    if not subtask:
        return error_response("block", "SUBTASK_NOT_FOUND", f"Subtask at path '{path}' not found")

    # Update blocked status
    subtask.blocked = bool(blocked)
    subtask.block_reason = str(reason).strip() if blocked else ""

    manager.save_task(task)

    write_activity_marker(
        task_id,
        "block",
        subtask_path=str(path),
        tasks_dir=getattr(manager, "tasks_dir", None),
    )

    ctx = build_context(manager, task_id, domain_filter=domain_path)
    suggestions = generate_suggestions(manager, task_id, domain_path)

    return AIResponse(
        success=True,
        intent="block",
        result={
            "path": str(path),
            "blocked": subtask.blocked,
            "reason": subtask.block_reason,
            "computed_status": subtask.computed_status,
        },
        context=ctx,
        suggestions=suggestions,
    )


def handle_delete(
    manager: TaskManager, data: Dict[str, Any]
) -> AIResponse:
    """Delete a task or subtask.

    Input:
        {"intent": "delete", "task": "TASK-001"}  # Delete entire task
        {"intent": "delete", "task": "TASK-001", "path": "0"}  # Delete subtask at path

    Returns deleted item info.
    """
    task_id = data.get("task")
    path = data.get("path")
    domain_path = derive_domain_explicit(data.get("domain", ""), data.get("phase"), data.get("component"))

    if not task_id:
        return error_response("delete", "MISSING_TASK", "Field 'task' is required")

    # SEC-001: Validate task_id
    err = validate_task_id(task_id)
    if err:
        return error_response("delete", "INVALID_TASK_ID", err)

    task = _load_task(manager, task_id, domain_path)
    if not task:
        return error_response("delete", "TASK_NOT_FOUND", f"Task {task_id} not found")

    # Delete subtask if path provided
    if path is not None:
        err = validate_path(str(path))
        if err:
            return error_response("delete", "INVALID_PATH", err)

        # Find and remove subtask
        path_parts = str(path).split(".")
        if len(path_parts) == 1:
            # Top-level subtask
            idx = int(path_parts[0])
            if idx < 0 or idx >= len(task.subtasks):
                return error_response(
                    "delete", "SUBTASK_NOT_FOUND", f"Subtask at path '{path}' not found"
                )
            deleted_subtask = task.subtasks.pop(idx)
            deleted_info = {
                "type": "subtask",
                "path": str(path),
                "title": deleted_subtask.title,
                "was_completed": deleted_subtask.completed,
            }
        else:
            # Nested subtask - navigate to parent
            parent_path = ".".join(path_parts[:-1])
            child_idx = int(path_parts[-1])
            parent = _get_subtask_by_path(task.subtasks, parent_path)
            if not parent:
                return error_response(
                    "delete", "PARENT_NOT_FOUND", f"Parent subtask at path '{parent_path}' not found"
                )
            if child_idx < 0 or child_idx >= len(parent.children):
                return error_response(
                    "delete", "SUBTASK_NOT_FOUND", f"Subtask at path '{path}' not found"
                )
            deleted_subtask = parent.children.pop(child_idx)
            deleted_info = {
                "type": "subtask",
                "path": str(path),
                "title": deleted_subtask.title,
                "was_completed": deleted_subtask.completed,
            }

        # Update task status and save
        task.update_status_from_progress()
        manager.save_task(task)

        write_activity_marker(
            task_id,
            "delete",
            subtask_path=str(path),
            tasks_dir=getattr(manager, "tasks_dir", None),
        )

        ctx = build_context(manager, task_id)
        suggestions = generate_suggestions(manager, task_id)

        return AIResponse(
            success=True,
            intent="delete",
            result={
                "deleted": deleted_info,
                "remaining_subtasks": len(task.subtasks),
                "task_progress": ctx.get("current_task", {}).get("progress", 0),
            },
            context=ctx,
            suggestions=suggestions,
        )

    # Delete entire task
    deleted_info = {
        "type": "task",
        "id": task_id,
        "title": task.title,
        "status": task.status,
        "subtasks_count": len(task.subtasks),
    }

    success = manager.delete_task(task_id)
    if not success:
        return error_response("delete", "DELETE_FAILED", f"Failed to delete task {task_id}")

    write_activity_marker(
        task_id, "delete", tasks_dir=getattr(manager, "tasks_dir", None)
    )

    # No task context after deletion
    ctx = build_context(manager)
    suggestions = generate_suggestions(manager)

    return AIResponse(
        success=True,
        intent="delete",
        result={
            "deleted": deleted_info,
        },
        context=ctx,
        suggestions=suggestions,
    )


def handle_complete(
    manager: TaskManager, data: Dict[str, Any]
) -> AIResponse:
    """Завершить задачу.

    Input:
        {"intent": "complete", "task": "TASK-001"}
        {"intent": "complete", "task": "TASK-001", "status": "OK"}
    """
    task_id = data.get("task")
    status = data.get("status", "OK")
    domain_path = derive_domain_explicit(data.get("domain", ""), data.get("phase"), data.get("component"))

    if not task_id:
        return error_response("complete", "MISSING_TASK", "Поле 'task' обязательно")

    # SEC-001: Validate task_id
    err = validate_task_id(task_id)
    if err:
        return error_response("complete", "INVALID_TASK_ID", err)

    # SEC-002: Validate status
    if status not in ("OK", "WARN", "FAIL"):
        return error_response("complete", "INVALID_STATUS", "status должен быть OK, WARN или FAIL")

    task = _load_task(manager, task_id, domain_path)
    if not task:
        return error_response(
            "complete", "TASK_NOT_FOUND", f"Задача {task_id} не найдена"
        )

    # Проверить готовность
    if task.subtasks:
        incomplete = [st for st in task.subtasks if not st.completed]
        if incomplete:
            return error_response(
                "complete",
                "INCOMPLETE_SUBTASKS",
                f"Есть {len(incomplete)} незавершённых подзадач",
            )

        unverified = [
            st
            for st in task.subtasks
            if st.success_criteria and not st.criteria_confirmed
        ]
        if unverified:
            return error_response(
                "complete",
                "UNVERIFIED_CRITERIA",
                f"Критерии {len(unverified)} подзадач не верифицированы",
            )

    task.status = status
    manager.save_task(task)

    write_activity_marker(
        task_id, "complete", tasks_dir=getattr(manager, "tasks_dir", None)
    )

    ctx = build_context(manager, task_id, domain_filter=domain_path)
    suggestions = generate_suggestions(manager, task_id, domain_path)

    return AIResponse(
        success=True,
        intent="complete",
        result={
            "task_id": task_id,
            "status": status,
        },
        context=ctx,
        suggestions=suggestions,
    )


def handle_batch(
    manager: TaskManager, data: Dict[str, Any]
) -> AIResponse:
    """Выполнить несколько операций за один вызов.

    Input:
        {
            "intent": "batch",
            "task": "TASK-001",
            "atomic": true,  # all-or-nothing with rollback
            "operations": [
                {"intent": "decompose", "subtasks": [...]},
                {"intent": "verify", "path": "0", "checkpoints": {...}},
                {"intent": "progress", "path": "1", "completed": true}
            ]
        }

    atomic=true: При ошибке откатывает ВСЕ изменения (all-or-nothing).
    atomic=false (default): Выполняет пока не ошибка, частичные изменения сохраняются.
    """
    import shutil
    import tempfile

    task_id = data.get("task")
    operations = data.get("operations", [])
    atomic = data.get("atomic", False)
    domain_path = derive_domain_explicit(data.get("domain", ""), data.get("phase"), data.get("component"))

    if not operations:
        return error_response(
            "batch", "MISSING_OPERATIONS", "Поле 'operations' обязательно",
            recovery_action="batch",
            recovery_hint={"operations": [{"intent": "context"}]},
        )

    # SEC-002: Validate operations count
    if len(operations) > MAX_ARRAY_LENGTH:
        return error_response(
            "batch", "TOO_MANY_OPERATIONS",
            f"Слишком много операций (макс {MAX_ARRAY_LENGTH})",
        )

    # Atomic mode: backup all affected task files before execution
    backups: Dict[str, Path] = {}  # task_id -> backup_path
    temp_dir = None

    if atomic:
        temp_dir = Path(tempfile.mkdtemp(prefix="batch_atomic_"))
        # Determine affected tasks
        affected_tasks = {task_id} if task_id else set()
        for op in operations:
            op_task = op.get("task", task_id)
            if op_task:
                affected_tasks.add(op_task)

        # Create backups
        for tid in affected_tasks:
            if tid:
                task_file = manager.tasks_dir / f"{tid}.task"
                if task_file.exists():
                    backup_path = temp_dir / f"{tid}.task"
                    shutil.copy2(task_file, backup_path)
                    backups[tid] = backup_path

    # Phase 1: Expand paths array into individual operations
    expanded_operations = []
    for op in operations:
        paths = op.get("paths")
        if isinstance(paths, list):
            if len(paths) > 0:
                # Expand operation for each path
                base_op = {k: v for k, v in op.items() if k != "paths"}
                for p in paths:
                    expanded_op = dict(base_op)
                    expanded_op["path"] = str(p)
                    expanded_operations.append(expanded_op)
            # else: Skip empty paths array - don't add to expanded operations
        else:
            # No paths field or paths is not a list - keep operation as-is
            expanded_operations.append(op)

    # Replace operations with expanded list
    operations = expanded_operations

    # SEC-002: Re-validate after expansion
    if len(operations) > MAX_ARRAY_LENGTH:
        return error_response(
            "batch", "TOO_MANY_OPERATIONS_AFTER_EXPANSION",
            f"Too many operations after paths expansion (max {MAX_ARRAY_LENGTH})",
        )

    results = []
    failed = False

    for i, op in enumerate(operations):
        op_intent = op.get("intent")
        if not op_intent:
            continue

        # Подставить task_id если не указан
        if task_id and "task" not in op:
            op["task"] = task_id
        # Подставить контекст
        op.setdefault("domain", domain_path)
        if data.get("phase"):
            op.setdefault("phase", data.get("phase"))
        if data.get("component"):
            op.setdefault("component", data.get("component"))

        # Выполнить операцию (без записи в историю - batch сам записывает)
        handler = INTENT_HANDLERS.get(op_intent)
        if not handler:
            results.append({
                "index": i,
                "intent": op_intent,
                "success": False,
                "error": f"Неизвестный intent: {op_intent}",
            })
            if atomic:
                failed = True
                break
            continue

        response = handler(manager, op)
        results.append({
            "index": i,
            "intent": op_intent,
            "success": response.success,
            "result": response.result,
            "error": response.error.to_dict() if response.error else None,
        })

        if not response.success:
            failed = True
            if atomic:
                break  # Stop and rollback
            else:
                break  # Stop but keep partial changes

    # Atomic rollback on failure
    rolled_back = False
    if atomic and failed and backups:
        for tid, backup_path in backups.items():
            task_file = manager.tasks_dir / f"{tid}.task"
            shutil.copy2(backup_path, task_file)
        rolled_back = True

    # Cleanup temp dir
    if temp_dir and temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)

    ctx = build_context(manager, task_id, domain_filter=domain_path)
    suggestions = generate_suggestions(manager, task_id)
    meta = build_meta(manager, task_id, domain_filter=domain_path) if task_id else None

    all_success = all(r["success"] for r in results) if results else False

    return AIResponse(
        success=all_success,
        intent="batch",
        result={
            "atomic": atomic,
            "rolled_back": rolled_back,
            "operations": results,
            "completed": len([r for r in results if r["success"]]),
            "total": len(operations),
        },
        context=ctx,
        suggestions=suggestions,
        meta=meta,
        error=ErrorDetail(
            code="BATCH_FAILED" if rolled_back else "BATCH_PARTIAL",
            message="Откат выполнен, изменения отменены" if rolled_back else "Некоторые операции не выполнены",
            recoverable=True,
            recovery_action="batch",
            recovery_hint={"operations": operations, "atomic": False},
        ) if not all_success else None,
    )


def handle_create(
    manager: TaskManager, data: Dict[str, Any]
) -> AIResponse:
    """Создать новую задачу.

    Input:
        {
            "intent": "create",
            "title": "Название задачи",
            "description": "Описание",
            "priority": "HIGH",
            "parent": "PARENT-001",
            "tags": ["backend", "api"],
            "subtasks": [...]  # опционально - сразу декомпозировать
        }
    """
    title = data.get("title")

    if not title:
        return error_response("create", "MISSING_TITLE", "Поле 'title' обязательно")

    # SEC-002: Validate string fields
    err = validate_string(title, "title", 500)
    if err:
        return error_response("create", "INVALID_TITLE", err)

    err = validate_string(data.get("description"), "description", MAX_STRING_LENGTH)
    if err:
        return error_response("create", "INVALID_DESCRIPTION", err)

    err = validate_string(data.get("context"), "context", MAX_STRING_LENGTH)
    if err:
        return error_response("create", "INVALID_CONTEXT", err)

    # Validate parent task_id if provided
    parent = data.get("parent")
    if parent:
        err = validate_task_id(parent)
        if err:
            return error_response("create", "INVALID_PARENT", err)

    # Validate priority
    priority = data.get("priority", "MEDIUM")
    if priority not in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
        return error_response("create", "INVALID_PRIORITY", "priority должен быть LOW, MEDIUM, HIGH или CRITICAL")

    # Validate tags
    err = validate_array(data.get("tags"), "tags", 50)
    if err:
        return error_response("create", "INVALID_TAGS", err)

    # Validate subtasks if provided
    subtasks_data = data.get("subtasks", [])
    if subtasks_data:
        err = validate_subtasks_data(subtasks_data)
        if err:
            return error_response("create", "INVALID_SUBTASKS", err)

    domain_path = derive_domain_explicit(data.get("domain", ""), data.get("phase"), data.get("component"))

    # Создать задачу - create_task возвращает TaskDetail (без сохранения)
    task = manager.create_task(
        title=title,
        status=data.get("status", "FAIL"),
        priority=data.get("priority", "MEDIUM"),
        parent=data.get("parent"),
        domain=domain_path,
        phase=data.get("phase", ""),
        component=data.get("component", ""),
    )

    # Заполнить дополнительные поля
    task.description = data.get("description", "")
    task.context = data.get("context", "")
    if data.get("tags"):
        task.tags = list(data["tags"])

    # Сохранить задачу
    manager.save_task(task)
    task_id = task.id

    # Если есть подзадачи - декомпозировать
    subtasks_data = data.get("subtasks", [])
    created_subtasks = []
    if subtasks_data:
        decompose_result = handle_decompose(
            manager, {"task": task_id, "subtasks": subtasks_data}
        )
        if decompose_result.success:
            created_subtasks = decompose_result.result.get("created", [])

    write_activity_marker(
        task_id, "create", tasks_dir=getattr(manager, "tasks_dir", None)
    )

    # Reload task to get full state after decompose, then track it
    task = _load_task(manager, task_id, domain_path)
    manager.track_task(task_id, task)

    ctx = build_context(manager, task_id, domain_filter=domain_path)
    suggestions = generate_suggestions(manager, task_id)

    return AIResponse(
        success=True,
        intent="create",
        result={
            "task_id": task_id,
            "title": title,
            "subtasks_created": len(created_subtasks),
        },
        context=ctx,
        suggestions=suggestions,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# IDEMPOTENCY CACHE
# ═══════════════════════════════════════════════════════════════════════════════

# In-memory cache: idempotency_key -> (response_dict, timestamp)
_idempotency_cache: Dict[str, tuple] = {}
IDEMPOTENCY_TTL = 3600  # 1 hour


def _check_idempotency(key: str) -> Optional[Dict[str, Any]]:
    """Check if operation with this key was already performed.

    Returns cached response dict if found and not expired, None otherwise.
    """
    import time

    if key not in _idempotency_cache:
        return None

    response_dict, timestamp = _idempotency_cache[key]
    if time.time() - timestamp > IDEMPOTENCY_TTL:
        del _idempotency_cache[key]
        return None

    return response_dict


def _store_idempotency(key: str, response: AIResponse) -> None:
    """Store response in idempotency cache."""
    import time

    # Cleanup old entries
    current_time = time.time()
    expired_keys = [
        k for k, (_, ts) in _idempotency_cache.items()
        if current_time - ts > IDEMPOTENCY_TTL
    ]
    for k in expired_keys:
        del _idempotency_cache[k]

    # Limit cache size
    if len(_idempotency_cache) > 1000:
        # Remove oldest entries
        sorted_keys = sorted(
            _idempotency_cache.keys(),
            key=lambda k: _idempotency_cache[k][1]
        )
        for k in sorted_keys[:100]:
            del _idempotency_cache[k]

    _idempotency_cache[key] = (response.to_dict(), current_time)


def clear_idempotency_cache() -> int:
    """Clear idempotency cache. Returns number of cleared entries."""
    global _idempotency_cache
    count = len(_idempotency_cache)
    _idempotency_cache = {}
    return count


# ═══════════════════════════════════════════════════════════════════════════════
# UNDO/REDO & HISTORY HANDLERS
# ═══════════════════════════════════════════════════════════════════════════════

# Global history instance (lazy initialized)
_history: Optional[OperationHistory] = None


def _get_history(tasks_dir: Path) -> OperationHistory:
    """Get or create history instance."""
    global _history
    if _history is None or _history.storage_dir != tasks_dir:
        _history = OperationHistory(tasks_dir)
    return _history


def handle_undo(
    manager: TaskManager, data: Dict[str, Any]
) -> AIResponse:
    """Отменить последнюю операцию.

    Input:
        {"intent": "undo"}
    """
    tasks_dir = manager.tasks_dir
    history = _get_history(tasks_dir)

    if not history.can_undo():
        return error_response("undo", "NOTHING_TO_UNDO", "Нет операций для отмены")

    success, error, undone_op = history.undo(tasks_dir)

    if not success:
        return error_response("undo", "UNDO_FAILED", error or "Не удалось отменить")

    # Reload task to get fresh state
    ctx = build_context(manager, undone_op.task_id if undone_op else None)

    return AIResponse(
        success=True,
        intent="undo",
        result={
            "undone_operation": {
                "id": undone_op.id,
                "intent": undone_op.intent,
                "task_id": undone_op.task_id,
                "timestamp": undone_op.timestamp,
            } if undone_op else None,
            "can_undo": history.can_undo(),
            "can_redo": history.can_redo(),
        },
        context=ctx,
    )


def handle_redo(
    manager: TaskManager, data: Dict[str, Any]
) -> AIResponse:
    """Повторить отменённую операцию.

    Input:
        {"intent": "redo"}

    Note: Redo только восстанавливает снимок, не перевыполняет операцию.
    """
    tasks_dir = manager.tasks_dir
    history = _get_history(tasks_dir)

    if not history.can_redo():
        return error_response("redo", "NOTHING_TO_REDO", "Нет операций для повтора")

    success, error, redo_op = history.redo(tasks_dir)

    if not success:
        return error_response("redo", "REDO_FAILED", error or "Не удалось повторить")

    ctx = build_context(manager, redo_op.task_id if redo_op else None)

    return AIResponse(
        success=True,
        intent="redo",
        result={
            "redo_operation": {
                "id": redo_op.id,
                "intent": redo_op.intent,
                "task_id": redo_op.task_id,
                "data": redo_op.data,  # Caller can re-execute if needed
            } if redo_op else None,
            "can_undo": history.can_undo(),
            "can_redo": history.can_redo(),
        },
        context=ctx,
    )


def handle_history(
    manager: TaskManager, data: Dict[str, Any]
) -> AIResponse:
    """Показать историю операций или timeline событий задачи.

    Input:
        {"intent": "history"}  # operation history (undo/redo)
        {"intent": "history", "limit": 20}
        {"intent": "history", "task": "TASK-001"}  # task event timeline
        {"intent": "history", "task": "TASK-001", "format": "markdown"}
    """
    task_id = data.get("task")

    # Task event timeline
    if task_id:
        from core import events_to_timeline

        task = manager.load_task(task_id)
        if not task:
            return AIResponse(
                success=False,
                intent="history",
                error_message=f"Task {task_id} not found",
            )

        limit = data.get("limit", 50)
        events = sorted(task.events, key=lambda e: e.timestamp or "", reverse=True)[:limit]
        output_format = data.get("format", "json")

        if output_format == "markdown":
            timeline_md = events_to_timeline(list(reversed(events)))
            return AIResponse(
                success=True,
                intent="history",
                result={"timeline": timeline_md, "task_id": task_id},
                summary=f"Timeline for {task_id} ({len(events)} events)",
            )

        return AIResponse(
            success=True,
            intent="history",
            result={
                "task_id": task_id,
                "events": [e.to_dict() for e in events],
                "total_events": len(task.events),
            },
        )

    # Operation history (undo/redo)
    tasks_dir = manager.tasks_dir
    history = _get_history(tasks_dir)
    limit = data.get("limit", 10)

    operations = history.list_recent(limit)

    return AIResponse(
        success=True,
        intent="history",
        result={
            "operations": [
                {
                    "id": op.id,
                    "intent": op.intent,
                    "task_id": op.task_id,
                    "timestamp": op.timestamp,
                    "datetime": datetime.fromtimestamp(op.timestamp).isoformat(),
                    "undone": op.undone,
                }
                for op in operations
            ],
            "total": len(history.operations),
            "current_index": history.current_index,
            "can_undo": history.can_undo(),
            "can_redo": history.can_redo(),
        },
    )


def handle_migrate(
    manager: TaskManager, data: Dict[str, Any]
) -> AIResponse:
    """Мигрировать локальные задачи в глобальное хранилище.

    Input:
        {"intent": "migrate"}
        {"intent": "migrate", "project_dir": "/path/to/project"}
    """
    project_dir = data.get("project_dir")
    if project_dir:
        project_dir = Path(project_dir)

    dry_run = data.get("dry_run", False)

    # Get info about migration
    local_tasks = (project_dir or Path.cwd()) / ".tasks"
    global_tasks = get_project_tasks_dir(project_dir, use_global=True)
    namespace = get_project_namespace(project_dir)

    if not local_tasks.exists():
        return error_response(
            "migrate", "NO_LOCAL_TASKS",
            f"Локальная директория .tasks не найдена в {project_dir or Path.cwd()}"
        )

    local_count = len(list(local_tasks.glob("*.task")))

    if dry_run:
        return AIResponse(
            success=True,
            intent="migrate",
            result={
                "dry_run": True,
                "would_migrate": {
                    "from": str(local_tasks),
                    "to": str(global_tasks),
                    "namespace": namespace,
                    "task_count": local_count,
                },
            },
        )

    success, message = migrate_to_global(project_dir)

    return AIResponse(
        success=success,
        intent="migrate",
        result={
            "migrated": success,
            "message": message,
            "from": str(local_tasks),
            "to": str(global_tasks),
            "namespace": namespace,
        },
        error_code="MIGRATION_FAILED" if not success else None,
        error_message=message if not success else None,
    )


def handle_storage_info(
    manager: TaskManager, data: Dict[str, Any]
) -> AIResponse:
    """Информация о хранилище.

    Input:
        {"intent": "storage"}
    """
    global_dir = get_global_storage_dir()
    local_dir = Path.cwd() / ".tasks"
    current_dir = manager.tasks_dir or get_project_tasks_dir(resolve_project_root(), use_global=True)

    # Count tasks in global storage
    namespaces = []
    if global_dir.exists():
        for ns_dir in global_dir.iterdir():
            if ns_dir.is_dir() and not ns_dir.name.startswith("."):
                task_count = len(list(ns_dir.glob("*.task")))
                namespaces.append({
                    "namespace": ns_dir.name,
                    "path": str(ns_dir),
                    "task_count": task_count,
                })

    return AIResponse(
        success=True,
        intent="storage",
        result={
            "global_storage": str(global_dir),
            "global_exists": global_dir.exists(),
            "local_storage": str(local_dir),
            "local_exists": local_dir.exists(),
            "current_storage": str(current_dir),
            "current_namespace": get_project_namespace(resolve_project_root()),
            "namespaces": namespaces,
        },
    )


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════


def _record_operation(
    manager: TaskManager,
    intent: str,
    task_id: Optional[str],
    data: Dict[str, Any],
    result: Optional[Dict[str, Any]] = None,
) -> None:
    """Record operation to history for undo support."""
    tasks_dir = manager.tasks_dir
    history = _get_history(tasks_dir)

    task_file = None
    if task_id:
        task_file = tasks_dir / f"{task_id}.task"

    history.record(
        intent=intent,
        task_id=task_id,
        data=data,
        task_file=task_file,
        result=result,
    )


def _get_subtask_by_path(subtasks: List, path: str) -> Optional["SubTask"]:
    """Получить подзадачу по пути (например "0.1.2")."""
    parts = [int(p) for p in path.split(".") if p.isdigit()]
    current_list = subtasks

    for i, idx in enumerate(parts):
        if idx >= len(current_list):
            return None
        subtask = current_list[idx]
        if i == len(parts) - 1:
            return subtask
        current_list = getattr(subtask, "children", [])

    return None


# ═══════════════════════════════════════════════════════════════════════════════
# PROMPTS HISTORY
# ═══════════════════════════════════════════════════════════════════════════════


def handle_prompts(
    manager: TaskManager, data: Dict[str, Any]
) -> AIResponse:
    """View user prompt history from Claude Code hook.

    Input:
        {"intent": "prompts"}  # all prompts
        {"intent": "prompts", "limit": 20}
        {"intent": "prompts", "task": "TASK-001"}  # prompts for specific task
        {"intent": "prompts", "format": "markdown"}
    """
    import json
    from pathlib import Path
    from core.desktop.devtools.interface.tasks_dir_resolver import (
        get_project_namespace,
        resolve_project_root,
    )

    # Always read from global storage (hook writes there)
    namespace = get_project_namespace(resolve_project_root())
    global_history = Path.home() / ".tasks" / namespace / ".history" / "prompts.jsonl"

    # Also check local .tasks if exists
    local_history = manager.tasks_dir / ".history" / "prompts.jsonl"

    history_files = [f for f in [global_history, local_history] if f.exists()]
    if not history_files:
        history_file = global_history  # Will show "not found"
    else:
        history_file = history_files[0]  # Prefer global

    if not history_file.exists():
        return AIResponse(
            success=True,
            intent="prompts",
            result={"prompts": [], "total": 0},
            summary="No prompt history found",
        )

    task_filter = data.get("task")
    limit = data.get("limit", 50)
    output_format = data.get("format", "json")

    prompts = []
    try:
        with open(history_file) as f:
            for line in f:
                if line.strip():
                    try:
                        entry = json.loads(line)
                        if task_filter and entry.get("task_id") != task_filter:
                            continue
                        prompts.append(entry)
                    except json.JSONDecodeError:
                        continue
    except IOError:
        pass

    # Most recent first, apply limit
    prompts = list(reversed(prompts))[:limit]

    if output_format == "markdown":
        lines = ["# User Prompt History", ""]
        for p in prompts:
            ts = p.get("timestamp", "?")[:19]
            task = p.get("task_id") or "-"
            prompt = p.get("prompt", "")[:200]
            skills = ", ".join(p.get("skills", [])) or "-"
            lines.append(f"**[{ts}]** Task: `{task}` | Skills: {skills}")
            lines.append(f"> {prompt}")
            lines.append("")
        return AIResponse(
            success=True,
            intent="prompts",
            result={"markdown": "\n".join(lines)},
            summary=f"{len(prompts)} prompts",
        )

    return AIResponse(
        success=True,
        intent="prompts",
        result={"prompts": prompts, "total": len(prompts)},
        summary=f"{len(prompts)} prompts",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# INTENT ROUTER
# ═══════════════════════════════════════════════════════════════════════════════

# Интенты которые модифицируют данные (поддерживают dry_run и записываются в историю)
MODIFYING_INTENTS = {"decompose", "define", "verify", "progress", "note", "block", "complete", "create", "done", "delete"}

# Интенты только для чтения
READONLY_INTENTS = {"context", "history", "storage", "resume", "prompts"}

# Интенты управления историей (не записываются в историю)
HISTORY_INTENTS = {"undo", "redo", "history"}

INTENT_HANDLERS = {
    # Чтение
    "context": handle_context,
    "history": handle_history,
    "prompts": handle_prompts,
    "storage": handle_storage_info,
    "resume": handle_resume,
    # Модификация
    "decompose": handle_decompose,
    "define": handle_define,
    "verify": handle_verify,
    "progress": handle_progress,
    "note": handle_note,  # NEW: add progress note to subtask
    "block": handle_block,  # NEW: block/unblock subtask
    "done": handle_done,  # NEW: unified completion (auto-verify + progress)
    "delete": handle_delete,  # NEW: delete task/subtask
    "complete": handle_complete,
    "create": handle_create,
    "batch": handle_batch,
    # Undo/Redo
    "undo": handle_undo,
    "redo": handle_redo,
    # Миграция
    "migrate": handle_migrate,
}


def process_intent(
    manager: TaskManager,
    data: Dict[str, Any],
    record_history: bool = True,
) -> AIResponse:
    """Обработать интент.

    Args:
        manager: TaskManager instance
        data: Request data with intent
        record_history: Whether to record modifying operations to history

    Supports:
        - dry_run mode: {"intent": "...", "dry_run": true}
        - idempotency: {"intent": "create", "idempotency_key": "unique-key-123"}

    Idempotency prevents duplicate operations (e.g., double-create).
    If the same idempotency_key is reused within TTL, cached response is returned.
    """
    intent = data.get("intent")
    dry_run = data.get("dry_run", False)
    idempotency_key = data.get("idempotency_key")
    domain_path = derive_domain_explicit(data.get("domain", ""), data.get("phase"), data.get("component"))

    if not intent:
        return error_response("unknown", "MISSING_INTENT", "Поле 'intent' обязательно")

    handler = INTENT_HANDLERS.get(intent)
    if not handler:
        available = ", ".join(sorted(INTENT_HANDLERS.keys()))
        return error_response(
            intent,
            "UNKNOWN_INTENT",
            f"Неизвестный intent '{intent}'. Доступные: {available}",
        )

    # Idempotency check for modifying intents
    if idempotency_key and intent in MODIFYING_INTENTS:
        cached = _check_idempotency(idempotency_key)
        if cached:
            # Return cached response with cached flag
            cached["idempotency"] = {"key": idempotency_key, "cached": True}
            return AIResponse(
                success=cached.get("success", True),
                intent=intent,
                result=cached.get("result", {}),
                context=cached.get("context", {}),
                idempotency_key=idempotency_key,
                cached=True,
            )

    # Dry-run mode for modifying intents
    if dry_run and intent in MODIFYING_INTENTS:
        # Validate without executing
        return _dry_run_validate(manager, data, intent)

    try:
        # Record to history before execution (for snapshot)
        task_id = data.get("task")
        should_record = (
            record_history and
            intent in MODIFYING_INTENTS and
            intent not in HISTORY_INTENTS
        )

        if should_record and task_id:
            # Create snapshot before modification
            _record_operation(manager, intent, task_id, data)

        response = handler(manager, data)

        # Add meta context to response
        if response.meta is None and task_id:
            response.meta = build_meta(manager, task_id, domain_filter=domain_path)

        # === v2: Add compact fields ===
        # Build TaskState if we have a task
        if task_id and response.state is None:
            task = manager.load_task(task_id)
            if task:
                response.state = TaskState.from_task(task)

        # Generate summary if not set
        if not response.summary:
            response.summary = generate_summary(intent, response.result, response.state)

        # Generate action hints if not set
        if not response.hints:
            response.hints = generate_action_hints(manager, task_id)

        # Store in idempotency cache (only for successful modifying operations)
        if idempotency_key and intent in MODIFYING_INTENTS and response.success:
            response.idempotency_key = idempotency_key
            _store_idempotency(idempotency_key, response)

        return response
    except Exception as e:
        return error_response(intent, "INTERNAL_ERROR", str(e))


def _dry_run_validate(
    manager: TaskManager,
    data: Dict[str, Any],
    intent: str,
) -> AIResponse:
    """Validate operation without executing (dry-run mode).

    Returns what would happen if executed.
    """
    task_id = data.get("task")
    result: Dict[str, Any] = {
        "dry_run": True,
        "intent": intent,
        "would_execute": True,
        "validation": {},
    }

    # Validate task_id if required
    if intent in ("decompose", "define", "verify", "progress", "complete", "done", "delete"):
        if not task_id:
            return AIResponse(
                success=True,
                intent=intent,
                result={
                    "dry_run": True,
                    "would_execute": False,
                    "reason": "Поле 'task' обязательно",
                },
            )

        err = validate_task_id(task_id)
        if err:
            return AIResponse(
                success=True,
                intent=intent,
                result={
                    "dry_run": True,
                    "would_execute": False,
                    "reason": err,
                },
            )

        task = manager.load_task(task_id)
        if not task:
            return AIResponse(
                success=True,
                intent=intent,
                result={
                    "dry_run": True,
                    "would_execute": False,
                    "reason": f"Задача {task_id} не найдена",
                },
            )

        result["validation"]["task_exists"] = True
        result["validation"]["task_status"] = task.status
        result["validation"]["subtasks_count"] = len(task.subtasks)

    # Intent-specific validation
    if intent == "decompose":
        subtasks = data.get("subtasks", [])
        if not subtasks:
            result["would_execute"] = False
            result["reason"] = "Поле 'subtasks' обязательно"
        else:
            err = validate_subtasks_data(subtasks)
            if err:
                result["would_execute"] = False
                result["reason"] = err
            else:
                result["validation"]["subtasks_to_create"] = len(subtasks)

    elif intent == "define":
        path = data.get("path")
        if path is None:
            result["would_execute"] = False
            result["reason"] = "Поле 'path' обязательно"
        else:
            err = validate_path(str(path))
            if err:
                result["would_execute"] = False
                result["reason"] = err

    elif intent == "verify":
        path = data.get("path")
        if path is None:
            result["would_execute"] = False
            result["reason"] = "Поле 'path' обязательно"

    elif intent == "progress":
        path = data.get("path")
        if path is None:
            result["would_execute"] = False
            result["reason"] = "Field 'path' is required"

    elif intent == "done":
        path = data.get("path")
        if path is None:
            result["would_execute"] = False
            result["reason"] = "Field 'path' is required"
        else:
            err = validate_path(str(path))
            if err:
                result["would_execute"] = False
                result["reason"] = err
            else:
                # Check subtask exists and its state
                task = manager.load_task(task_id) if task_id else None
                if task:
                    subtask = _get_subtask_by_path(task.subtasks, str(path))
                    if subtask:
                        result["validation"]["subtask_exists"] = True
                        result["validation"]["already_completed"] = subtask.completed
                        result["validation"]["criteria_confirmed"] = subtask.criteria_confirmed
                        result["validation"]["tests_auto_confirmed"] = getattr(subtask, "tests_auto_confirmed", False)
                        result["validation"]["blockers_auto_resolved"] = getattr(subtask, "blockers_auto_resolved", False)
                    else:
                        result["would_execute"] = False
                        result["reason"] = f"Subtask at path '{path}' not found"

    elif intent == "delete":
        path = data.get("path")
        # path is optional for delete (can delete entire task)
        if path is not None:
            err = validate_path(str(path))
            if err:
                result["would_execute"] = False
                result["reason"] = err
            else:
                # Check subtask exists
                task = manager.load_task(task_id) if task_id else None
                if task:
                    subtask = _get_subtask_by_path(task.subtasks, str(path))
                    if subtask:
                        result["validation"]["subtask_exists"] = True
                        result["validation"]["subtask_title"] = subtask.title
                        result["validation"]["would_delete"] = "subtask"
                    else:
                        result["would_execute"] = False
                        result["reason"] = f"Subtask at path '{path}' not found"
        else:
            result["validation"]["would_delete"] = "task"
            task = manager.load_task(task_id) if task_id else None
            if task:
                result["validation"]["task_title"] = task.title
                result["validation"]["subtasks_count"] = len(task.subtasks)

    elif intent == "complete":
        status = data.get("status", "OK")
        if status not in ("OK", "WARN", "FAIL"):
            result["would_execute"] = False
            result["reason"] = "status должен быть OK, WARN или FAIL"

    elif intent == "create":
        title = data.get("title")
        if not title:
            result["would_execute"] = False
            result["reason"] = "Поле 'title' обязательно"
        else:
            err = validate_string(title, "title", 500)
            if err:
                result["would_execute"] = False
                result["reason"] = err

    ctx = build_context(manager, task_id) if task_id else {}

    return AIResponse(
        success=True,
        intent=intent,
        result=result,
        context=ctx,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_ai(args) -> int:
    """Точка входа для команды 'tasks ai'."""
    # Получить JSON из аргумента или stdin
    json_input = getattr(args, "json_input", None)

    if json_input == "-" or (not json_input and not sys.stdin.isatty()):
        # Читать из stdin
        json_input = sys.stdin.read().strip()

    # SEC-002: Check JSON size limit
    if json_input and len(json_input) > MAX_JSON_SIZE:
        response = error_response(
            "parse",
            "INPUT_TOO_LARGE",
            f"Размер входных данных превышает лимит ({MAX_JSON_SIZE // 1024 // 1024} MB)",
        )
        print(response.to_json())
        return 1

    if not json_input:
        # Показать справку
        help_response = AIResponse(
            success=True,
            intent="help",
            result={
                "usage": "tasks ai '<json>' или echo '<json>' | tasks ai",
                "flags": {
                    "--global": "Использовать глобальное хранилище ~/.tasks",
                },
                "intents": {
                    # Чтение
                    "context": "Get full context",
                    "history": "Operation history",
                    "storage": "Storage info",
                    # Модификация
                    "create": "Create task",
                    "decompose": "Decompose into subtasks (criteria required, tests/blockers optional)",
                    "define": "Define criteria/tests/blockers",
                    "verify": "Verify checkpoints",
                    "progress": "Mark progress (legacy)",
                    "done": "Unified completion: auto-verify + mark completed (replaces verify×3 + progress)",
                    "delete": "Delete task or subtask",
                    "complete": "Complete task",
                    "batch": "Multiple operations (atomic: true for transaction)",
                    # Undo/Redo
                    "undo": "Undo last operation",
                    "redo": "Redo undone operation",
                    # Миграция
                    "migrate": "Migrate to global storage",
                },
                "features": {
                    "dry_run": "Добавьте 'dry_run': true для проверки без выполнения",
                    "idempotency": "Добавьте 'idempotency_key': 'unique-id' для предотвращения дублей",
                    "atomic_batch": "batch с 'atomic': true откатывает ВСЕ при ошибке",
                    "meta": "Каждый ответ содержит 'meta' с мини-контекстом задачи",
                    "recovery": "Ошибки содержат 'recovery' с подсказкой исправления",
                },
                "examples": [
                    '{"intent": "context"}',
                    '{"intent": "storage"}',
                    '{"intent": "create", "title": "Task", "idempotency_key": "create-task-1"}',
                    '{"intent": "create", "title": "Task", "dry_run": true}',
                    '{"intent": "batch", "task": "T-1", "atomic": true, "operations": [...]}',
                    '{"intent": "undo"}',
                ],
            },
        )
        print(help_response.to_json())
        return 0

    # Парсить JSON
    try:
        data = json.loads(json_input)
    except json.JSONDecodeError as e:
        response = error_response("parse", "INVALID_JSON", f"Ошибка парсинга JSON: {e}")
        print(response.to_json())
        return 1

    # Определить директорию хранилища
    use_global = getattr(args, "use_global", False)

    if use_global:
        # Использовать глобальное хранилище ~/.tasks/<namespace>
        tasks_dir = get_project_tasks_dir(use_global=True)
    else:
        # Локальное хранилище (legacy)
        tasks_dir = Path(getattr(args, "tasks_dir", ".tasks"))
        domain = getattr(args, "domain", None)
        phase = getattr(args, "phase", None)
        component = getattr(args, "component", None)

        if domain or phase or component:
            subpath = Path(domain or "") / (phase or "") / (component or "")
            tasks_dir = tasks_dir / subpath

    manager = TaskManager(tasks_dir=tasks_dir)

    # Обработать интент
    response = process_intent(manager, data)
    print(response.to_json())

    return 0 if response.success else 1


__all__ = [
    "cmd_ai",
    "process_intent",
    "AIResponse",
    # v2 compact types
    "TaskState",
    "ActionHint",
    "generate_summary",
    "generate_action_hints",
    # Legacy types
    "Suggestion",
    "ErrorDetail",
    "Meta",
    "INTENT_HANDLERS",
    "MODIFYING_INTENTS",
    "READONLY_INTENTS",
    "HISTORY_INTENTS",
    # Security validators
    "validate_task_id",
    "validate_path",
    "validate_string",
    "validate_array",
    "validate_subtasks_data",
    # Security constants
    "MAX_JSON_SIZE",
    "MAX_SUBTASKS",
    "MAX_NESTING_DEPTH",
    "MAX_STRING_LENGTH",
    "MAX_ARRAY_LENGTH",
    "TASK_ID_PATTERN",
    "PATH_PATTERN",
    # Handlers for testing
    "handle_context",
    "handle_decompose",
    "handle_define",
    "handle_verify",
    "handle_progress",
    "handle_done",  # NEW: unified completion
    "handle_delete",  # NEW: delete task/subtask
    "handle_complete",
    "handle_batch",
    "handle_create",
    # Undo/Redo handlers
    "handle_undo",
    "handle_redo",
    "handle_history",
    "handle_migrate",
    "handle_storage_info",
    # Helpers
    "error_response",
    "build_context",
    "build_meta",
    "generate_suggestions",
    "_get_subtask_by_path",
    "_record_operation",
    "_get_history",
    # Idempotency
    "clear_idempotency_cache",
    "IDEMPOTENCY_TTL",
    # Storage helpers
    "get_project_tasks_dir",
    "get_global_storage_dir",
    "get_project_namespace",
    "migrate_to_global",
]
