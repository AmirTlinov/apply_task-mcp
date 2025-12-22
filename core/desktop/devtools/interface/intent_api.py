#!/usr/bin/env python3
"""AI-first JSON intent API for apply_task (Plans → Tasks → Steps).

This module is the single source of truth for the JSON intent API used by:
- MCP server: `apply_task mcp` (tools map 1:1 to intents here)
- In-process adapters (TUI/GUI) when they need deterministic intent routing

Canonical model:
- Plan: TaskDetail(kind="plan", id="PLAN-###") stores contract + plan checklist (doc/steps/current).
- Task: TaskDetail(kind="task", id="TASK-###", parent="PLAN-###") stores nested Steps (recursive).
- Step: recursive node inside a Task (`TaskDetail.steps`), with checkpoints:
  - criteria (explicit)
  - tests (explicit OR auto-confirmed when empty at creation)
Blockers are stored as data (`blockers: [str]`) but are NOT a checkpoint.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from core import PlanNode, Step, TaskDetail, TaskNode, Attachment, VerificationCheck, StepEvent
from core.desktop.devtools.application.context import (
    clear_last_task,
    get_last_task,
    normalize_task_id,
    save_last_task,
)
from core.desktop.devtools.application.plan_semantics import append_contract_version_if_changed
from core.desktop.devtools.application.task_manager import TaskManager, _find_step_by_path, _find_task_by_path
from core.desktop.devtools.application.linting import lint_item
from core.desktop.devtools.interface.evidence_collectors import collect_auto_verification_checks
from core.desktop.devtools.interface.operation_history import OperationHistory
from core.desktop.devtools.interface.serializers import plan_to_dict, plan_node_to_dict, step_to_dict, task_to_dict, task_node_to_dict
from core.desktop.devtools.interface.tasks_dir_resolver import resolve_project_root


MAX_STRING_LENGTH = 500
MAX_ARRAY_LENGTH = 200
MAX_NESTING_DEPTH = 24

_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_PATH_PATTERN = re.compile(r"^s:\d+(\.t:\d+\.s:\d+)*(\.t:\d+)?$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Suggestion:
    action: str
    target: str
    reason: str
    priority: str = "normal"
    params: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "action": self.action,
            "target": self.target,
            "reason": self.reason,
            "priority": self.priority,
        }
        if self.params:
            data["params"] = dict(self.params)
        return data


@dataclass
class AIResponse:
    success: bool
    intent: str
    result: Dict[str, Any] = field(default_factory=dict)
    context: Dict[str, Any] = field(default_factory=dict)
    suggestions: List[Suggestion] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)
    summary: Optional[str] = None
    state: Optional[Dict[str, Any]] = None
    hints: Optional[List[Dict[str, Any]]] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    error_recovery: Optional[str] = None
    timestamp: str = field(default_factory=_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "success": self.success,
            "intent": self.intent,
            "result": self.result or {},
            "summary": self.summary,
            "state": self.state,
            "hints": self.hints,
            "warnings": self.warnings or [],
            "context": self.context or {},
            "suggestions": [s.to_dict() for s in (self.suggestions or [])],
            "meta": self.meta or {},
            "error": None,
            "timestamp": self.timestamp,
        }
        if not self.success:
            payload["error"] = {
                "code": self.error_code or "ERROR",
                "message": self.error_message or "Unknown error",
            }
            if self.error_recovery:
                payload["error"]["recovery"] = self.error_recovery
        # Keep output stable: drop None fields at top-level, but keep `error: null`.
        return {k: v for k, v in payload.items() if v is not None or k == "error"}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


def error_response(
    intent: str,
    code: str,
    message: str,
    *,
    recovery: str = "",
    result: Optional[Dict[str, Any]] = None,
    context: Optional[Dict[str, Any]] = None,
    suggestions: Optional[List[Suggestion]] = None,
) -> AIResponse:
    return AIResponse(
        success=False,
        intent=intent,
        result=result or {},
        context=context or {},
        suggestions=list(suggestions or []),
        error_code=code,
        error_message=message,
        error_recovery=recovery or None,
    )


def _missing_target_suggestions(manager: TaskManager, *, want: str | List[str]) -> List[Suggestion]:
    wants = [want] if isinstance(want, str) else list(want or [])
    if not wants:
        wants = ["TASK-"]
    suggestions: List[Suggestion] = [
        Suggestion(
            action="context",
            target="tasks_context",
            reason="Покажи доступные планы/задачи и выбери id для явной адресации.",
            priority="high",
            params={"include_all": True, "compact": True},
        ),
        Suggestion(
            action="focus_get",
            target="tasks_focus_get",
            reason="Покажи текущий focus (.last) и используй его явно или обнови через focus_set.",
            priority="normal",
        ),
    ]
    details = manager.list_all_tasks(skip_sync=True)
    # Provide a couple of concrete candidates as set_focus suggestions.
    candidates = [d for d in details if any(str(getattr(d, "id", "") or "").startswith(prefix) for prefix in wants)]
    for cand in candidates[:3]:
        suggestions.append(
            Suggestion(
                action="focus_set",
                target=str(getattr(cand, "id", "") or ""),
                reason="Установи focus на существующий объект, если хочешь опускать id в следующих вызовах.",
                priority="normal",
                params={"task": str(getattr(cand, "id", "") or ""), "domain": str(getattr(cand, "domain", "") or "")},
            )
        )
    return suggestions


def _path_help_suggestions(task_id: str) -> List[Suggestion]:
    tid = str(task_id or "").strip()
    if not tid:
        return []
    return [
        Suggestion(
            action="radar",
            target="tasks_radar",
            reason="Покажи Now/Why/Verify и текущий активный шаг (чтобы взять корректный path/step_id).",
            priority="high",
            params={"task": tid},
        ),
        Suggestion(
            action="mirror",
            target="tasks_mirror",
            reason="Покажи дерево и canonical path/step_id для точной адресации.",
            priority="normal",
            params={"task": tid, "limit": 10},
        ),
    ]


def _preview_text(value: str, *, max_len: int = 280) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _dedupe_strs(items: List[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for raw in items:
        val = str(raw or "").strip()
        if not val or val in seen:
            continue
        seen.add(val)
        out.append(val)
    return out


def _contract_summary(value: Any) -> Dict[str, Any]:
    """Compact contract summary for Radar View (1 screen → 1 truth)."""
    if not isinstance(value, dict):
        return {}
    data = dict(value)
    out: Dict[str, Any] = {}

    goal = data.get("goal")
    if isinstance(goal, str) and goal.strip():
        out["goal"] = _preview_text(goal, max_len=180)

    for key, limit in (("checks", 5), ("done", 5), ("constraints", 3), ("risks", 3)):
        raw = data.get(key)
        if not isinstance(raw, list):
            continue
        items = _dedupe_strs([str(x or "").strip() for x in raw])
        if items:
            out[key] = items[:limit]

    return out


def validate_task_id(value: Any) -> Optional[str]:
    if value is None:
        return "id не указан"
    if not isinstance(value, str):
        return "id должен быть строкой"
    value = value.strip()
    if not value:
        return "id пустой"
    if len(value) > 64:
        return "id слишком длинный (max 64)"
    if ".." in value or "/" in value or "\\" in value:
        return "id содержит недопустимые символы пути"
    if not _ID_PATTERN.match(value):
        return "id должен содержать только буквы/цифры/_/-"
    return None


def validate_node_id(value: Any, field_name: str = "id") -> Optional[str]:
    err = validate_task_id(value)
    if not err:
        return None
    if "id" in err:
        return err.replace("id", field_name)
    return f"{field_name}: {err}"


def validate_path(value: Any) -> Optional[str]:
    if value is None:
        return "path не указан"
    if not isinstance(value, str):
        return "path должен быть строкой"
    value = value.strip()
    if not value:
        return "path пустой"
    if len(value) > 128:
        return "path слишком длинный"
    if ".." in value or "/" in value or "\\" in value:
        return "path содержит недопустимые символы пути"
    if not _PATH_PATTERN.match(value):
        return "path должен быть в формате s:0 или s:0.t:1.s:2"
    depth = value.count(".") + 1
    if depth > MAX_NESTING_DEPTH:
        return f"path слишком глубокий (max {MAX_NESTING_DEPTH})"
    return None


def validate_step_path(value: Any) -> Optional[str]:
    err = validate_path(value)
    if err:
        return err
    value = str(value or "").strip()
    if value.split(".")[-1].startswith("t:"):
        return "path должен указывать на шаг (оканчивается на s:<n>)"
    return None


def validate_task_path(value: Any) -> Optional[str]:
    err = validate_path(value)
    if err:
        return err
    value = str(value or "").strip()
    if not value.split(".")[-1].startswith("t:"):
        return "path должен указывать на задание (оканчивается на t:<n>)"
    return None


def _resolve_step_path(manager: TaskManager, task: TaskDetail, data: Dict[str, Any], *, path_field: str = "path") -> Tuple[Optional[str], Optional[Tuple[str, str]]]:
    step_id = data.get("step_id")
    if step_id is not None:
        err = validate_node_id(step_id, "step_id")
        if err:
            return None, ("INVALID_STEP_ID", err)
        step_id = str(step_id)
        path = manager.find_step_path_by_id(task, step_id)
        if not path:
            return None, ("STEP_ID_NOT_FOUND", f"Шаг step_id={step_id} не найден")
        return path, None

    path = data.get(path_field)
    path_err = validate_step_path(path)
    if path_err:
        return None, ("INVALID_PATH", path_err)
    return str(path), None


def _resolve_task_path(manager: TaskManager, task: TaskDetail, data: Dict[str, Any], *, path_field: str = "path") -> Tuple[Optional[str], Optional[Tuple[str, str]]]:
    node_id = data.get("task_node_id")
    if node_id is not None:
        err = validate_node_id(node_id, "task_node_id")
        if err:
            return None, ("INVALID_TASK_NODE_ID", err)
        node_id = str(node_id)
        path = manager.find_task_node_path_by_id(task, node_id)
        if not path:
            return None, ("TASK_NODE_ID_NOT_FOUND", f"Задание task_node_id={node_id} не найдено")
        return path, None

    path = data.get(path_field)
    path_err = validate_task_path(path)
    if path_err:
        return None, ("INVALID_PATH", path_err)
    return str(path), None


def validate_string(value: Any, field_name: str, *, max_length: int = MAX_STRING_LENGTH) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        return f"{field_name} должен быть строкой"
    if len(value) > max_length:
        return f"{field_name} слишком длинный (max {max_length})"
    return None


def validate_array(value: Any, field_name: str, *, max_length: int = MAX_ARRAY_LENGTH) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, list):
        return f"{field_name} должен быть массивом"
    if len(value) > max_length:
        return f"{field_name} слишком длинный (max {max_length})"
    return None


def _normalize_str_list(raw: Any) -> List[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("must be list")
    out: List[str] = []
    for item in raw:
        text = str(item or "").strip()
        if text:
            out.append(text)
    return out


def _normalize_filter_list(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        raise ValueError("must be list or string")
    out: List[str] = []
    for item in raw:
        text = str(item or "").strip()
        if text:
            out.append(text)
    return out


def _normalize_status_filter(raw: Any) -> List[str]:
    values = _normalize_filter_list(raw)
    normalized: List[str] = []
    for val in values:
        upper = val.strip().upper()
        if upper not in {"TODO", "ACTIVE", "DONE"}:
            raise ValueError(f"unknown status: {val}")
        normalized.append(upper)
    return normalized


def _parse_limit(value: Any, field: str) -> Tuple[Optional[int], Optional[str]]:
    if value is None:
        return None, None
    try:
        limit = int(value)
    except Exception:
        return None, f"{field} должен быть числом"
    if limit < 0:
        return None, f"{field} должен быть >= 0"
    if limit > MAX_ARRAY_LENGTH:
        return None, f"{field} слишком большой (max {MAX_ARRAY_LENGTH})"
    return limit, None


def _parse_cursor(value: Any, field: str) -> Tuple[Optional[int], Optional[str]]:
    if value is None:
        return None, None
    try:
        cursor = int(value)
    except Exception:
        return None, f"{field} должен быть числом"
    if cursor < 0:
        return None, f"{field} должен быть >= 0"
    return cursor, None


def _apply_filters(
    items: List[TaskDetail],
    *,
    statuses: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
    domain: Optional[str] = None,
    parent: Optional[str] = None,
) -> List[TaskDetail]:
    statuses_set = {s.upper() for s in (statuses or [])}
    tags_set = {t.lower() for t in (tags or [])}
    domain_norm = str(domain or "").strip()
    parent_norm = str(parent or "").strip()
    filtered: List[TaskDetail] = []
    for item in items:
        if statuses_set:
            status = str(getattr(item, "status", "") or "").strip().upper()
            if status not in statuses_set:
                continue
        if tags_set:
            item_tags = [t.strip().lower() for t in (getattr(item, "tags", []) or [])]
            if not set(item_tags).intersection(tags_set):
                continue
        if domain_norm:
            if str(getattr(item, "domain", "") or "").strip() != domain_norm:
                continue
        if parent_norm:
            if str(getattr(item, "parent", "") or "").strip() != parent_norm:
                continue
        filtered.append(item)
    return filtered


def _paginate_items(
    items: List[TaskDetail],
    *,
    cursor: Optional[int] = None,
    limit: Optional[int] = None,
) -> Tuple[List[TaskDetail], Dict[str, Any]]:
    total = len(items)
    offset = max(0, int(cursor or 0))
    if limit is None:
        limit = max(0, total - offset)
    limit = max(0, min(int(limit or 0), max(0, total - offset)))
    page = items[offset: offset + limit]
    next_cursor = str(offset + limit) if offset + limit < total else None
    meta = {
        "cursor": str(offset) if cursor is not None else None,
        "next_cursor": next_cursor,
        "total": total,
        "count": len(page),
        "limit": limit,
    }
    return page, meta


def _build_subtree_payload(
    task: TaskDetail,
    *,
    path: str,
    kind: str,
    compact: bool,
) -> Optional[Dict[str, Any]]:
    kind = str(kind or "").strip().lower()
    if kind == "task":
        node, _, _ = _find_task_by_path(list(getattr(task, "steps", []) or []), path)
        if not node:
            return None
        return {
            "kind": "task",
            "path": path,
            "node": task_node_to_dict(node, path=path, compact=compact, include_steps=True),
        }
    if kind == "plan":
        step, _, _ = _find_step_by_path(list(getattr(task, "steps", []) or []), path)
        plan = getattr(step, "plan", None) if step else None
        if not plan:
            return None
        return {
            "kind": "plan",
            "path": path,
            "node": plan_node_to_dict(plan, base_path=path, compact=compact, include_steps=True),
        }
    step, _, _ = _find_step_by_path(list(getattr(task, "steps", []) or []), path)
    if not step:
        return None
    return {
        "kind": "step",
        "path": path,
        "node": step_to_dict(step, path=path, compact=compact, include_steps=True),
    }


def _parse_step_node(node: Dict[str, Any], *, depth: int = 0) -> Step:
    if depth > MAX_NESTING_DEPTH:
        raise ValueError("steps nesting too deep")
    title = str(node.get("title", "") or "").strip()
    if not title:
        raise ValueError("step.title is required")
    if len(title) > MAX_STRING_LENGTH:
        raise ValueError("step.title too long")
    criteria = _normalize_str_list(node.get("success_criteria"))
    tests = _normalize_str_list(node.get("tests"))
    blockers = _normalize_str_list(node.get("blockers"))
    step = Step.new(title, criteria=criteria, tests=tests, blockers=blockers, created_at=None)
    if not step:
        raise ValueError("step.success_criteria is required")
    node_id = str(node.get("id", "") or "").strip()
    if node_id:
        step.id = node_id
    checks_raw = node.get("verification_checks", []) or []
    if isinstance(checks_raw, list):
        try:
            step.verification_checks = [VerificationCheck.from_dict(c) for c in checks_raw if isinstance(c, dict)]
        except Exception:
            step.verification_checks = []
    step.verification_outcome = str(node.get("verification_outcome", "") or "").strip()
    attachments_raw = node.get("attachments", []) or []
    if isinstance(attachments_raw, list):
        try:
            step.attachments = [Attachment.from_dict(a) for a in attachments_raw if isinstance(a, dict)]
        except Exception:
            step.attachments = []
    plan_raw = node.get("plan")
    if plan_raw is not None:
        if not isinstance(plan_raw, dict):
            raise ValueError("step.plan must be an object")
        step.plan = _parse_plan_node(plan_raw, depth=depth + 1)
    elif "steps" in node:
        raise ValueError("step.steps is not supported; use step.plan.tasks[].steps")
    return step


def _parse_plan_node(node: Dict[str, Any], *, depth: int = 0) -> PlanNode:
    if depth > MAX_NESTING_DEPTH:
        raise ValueError("plan nesting too deep")
    tasks_raw = node.get("tasks", [])
    if tasks_raw is None:
        tasks_raw = []
    if not isinstance(tasks_raw, list):
        raise ValueError("plan.tasks must be an array")
    tasks = [_parse_task_node(task, depth=depth + 1) for task in tasks_raw if isinstance(task, dict)]
    attachments_raw = node.get("attachments", []) or []
    attachments: List[Attachment] = []
    if isinstance(attachments_raw, list):
        try:
            attachments = [Attachment.from_dict(a) for a in attachments_raw if isinstance(a, dict)]
        except Exception:
            attachments = []
    return PlanNode(
        title=str(node.get("title", "") or ""),
        doc=str(node.get("doc", "") or ""),
        attachments=attachments,
        steps=_normalize_str_list(node.get("steps")),
        current=int(node.get("current", 0) or 0),
        tasks=tasks,
    )


def _parse_task_node(node: Dict[str, Any], *, depth: int = 0) -> TaskNode:
    if depth > MAX_NESTING_DEPTH:
        raise ValueError("task nesting too deep")
    title = str(node.get("title", "") or "").strip()
    if not title:
        raise ValueError("task.title is required")
    if len(title) > MAX_STRING_LENGTH:
        raise ValueError("task.title too long")
    steps_raw = node.get("steps", [])
    if steps_raw is None:
        steps_raw = []
    if not isinstance(steps_raw, list):
        raise ValueError("task.steps must be an array")
    attachments_raw = node.get("attachments", []) or []
    attachments: List[Attachment] = []
    if isinstance(attachments_raw, list):
        try:
            attachments = [Attachment.from_dict(a) for a in attachments_raw if isinstance(a, dict)]
        except Exception:
            attachments = []
    task = TaskNode(
        title=title,
        status=str(node.get("status", "TODO") or "TODO"),
        priority=str(node.get("priority", "MEDIUM") or "MEDIUM"),
        description=str(node.get("description", "") or ""),
        context=str(node.get("context", "") or ""),
        attachments=attachments,
        success_criteria=_normalize_str_list(node.get("success_criteria")),
        tests=_normalize_str_list(node.get("tests")),
        criteria_confirmed=bool(node.get("criteria_confirmed", False)),
        tests_confirmed=bool(node.get("tests_confirmed", False)),
        criteria_auto_confirmed=bool(node.get("criteria_auto_confirmed", False)),
        tests_auto_confirmed=bool(node.get("tests_auto_confirmed", False)),
        criteria_notes=_normalize_str_list(node.get("criteria_notes")),
        tests_notes=_normalize_str_list(node.get("tests_notes")),
        dependencies=_normalize_str_list(node.get("dependencies")),
        next_steps=_normalize_str_list(node.get("next_steps")),
        problems=_normalize_str_list(node.get("problems")),
        risks=_normalize_str_list(node.get("risks")),
        blocked=bool(node.get("blocked", False)),
        blockers=_normalize_str_list(node.get("blockers")),
        status_manual=bool(node.get("status_manual", False)),
        steps=[_parse_step_node(ch, depth=depth + 1) for ch in steps_raw if isinstance(ch, dict)],
    )
    node_id = str(node.get("id", "") or "").strip()
    if node_id:
        task.id = node_id
    return task


def validate_steps_data(value: Any) -> Optional[str]:
    err = validate_array(value, "steps")
    if err:
        return err
    assert isinstance(value, list)
    def _validate_step(node: Dict[str, Any], label: str) -> Optional[str]:
        title_err = validate_string(node.get("title"), f"{label}.title", max_length=MAX_STRING_LENGTH)
        if title_err:
            return title_err
        if not str(node.get("title", "") or "").strip():
            return f"{label}.title обязателен"
        sc = node.get("success_criteria")
        sc_err = validate_array(sc, f"{label}.success_criteria")
        if sc_err:
            return sc_err
        try:
            crit = _normalize_str_list(sc)
        except Exception:
            return f"{label}.success_criteria должен быть массивом строк"
        if not crit:
            return f"{label}.success_criteria обязателен"
        plan = node.get("plan", None)
        if plan is not None:
            if not isinstance(plan, dict):
                return f"{label}.plan должен быть объектом"
            tasks = plan.get("tasks", [])
            if tasks is None:
                tasks = []
            if not isinstance(tasks, list):
                return f"{label}.plan.tasks должен быть массивом"
            for t_idx, task in enumerate(tasks, 1):
                if not isinstance(task, dict):
                    return f"{label}.plan.tasks[{t_idx}] должен быть объектом"
                err_task = _validate_task(task, f"{label}.plan.tasks[{t_idx}]")
                if err_task:
                    return err_task
        elif "steps" in node:
            return f"{label}.steps не поддерживается; используй {label}.plan.tasks[].steps"
        return None

    def _validate_task(node: Dict[str, Any], label: str) -> Optional[str]:
        title_err = validate_string(node.get("title"), f"{label}.title", max_length=MAX_STRING_LENGTH)
        if title_err:
            return title_err
        if not str(node.get("title", "") or "").strip():
            return f"{label}.title обязателен"
        steps = node.get("steps", [])
        if steps is None:
            steps = []
        if not isinstance(steps, list):
            return f"{label}.steps должен быть массивом"
        for s_idx, child in enumerate(steps, 1):
            if not isinstance(child, dict):
                return f"{label}.steps[{s_idx}] должен быть объектом"
            err_child = _validate_step(child, f"{label}.steps[{s_idx}]")
            if err_child:
                return err_child
        return None

    for idx, node in enumerate(value, 1):
        if not isinstance(node, dict):
            return f"steps[{idx}] должен быть объектом"
        err = _validate_step(node, f"steps[{idx}]")
        if err:
            return err
    return None


def _compute_checkpoint_status(task: TaskDetail) -> Dict[str, List[str]]:
    pending: List[str] = []
    ready: List[str] = []
    pending_ids: List[str] = []
    ready_ids: List[str] = []

    def walk(nodes: List[Step], prefix: str = "") -> None:
        for idx, st in enumerate(nodes):
            path = f"{prefix}.s:{idx}" if prefix else f"s:{idx}"
            if not st.completed:
                if st.ready_for_completion():
                    ready.append(path)
                    ready_ids.append(str(getattr(st, "id", "") or ""))
                else:
                    pending.append(path)
                    pending_ids.append(str(getattr(st, "id", "") or ""))
            plan = getattr(st, "plan", None)
            if plan and getattr(plan, "tasks", None):
                for t_idx, task in enumerate(plan.tasks):
                    task_prefix = f"{path}.t:{t_idx}"
                    walk(list(getattr(task, "steps", []) or []), task_prefix)

    walk(list(getattr(task, "steps", []) or []))
    return {"pending": pending, "ready": ready, "pending_ids": pending_ids, "ready_ids": ready_ids}


def _count_step_tree(nodes: List[Step]) -> Tuple[int, int]:
    total = 0
    done = 0
    stack = [iter(list(nodes or []))]
    while stack:
        try:
            st = next(stack[-1])
        except StopIteration:
            stack.pop()
            continue
        total += 1
        if bool(getattr(st, "completed", False)):
            done += 1
        plan = getattr(st, "plan", None)
        tasks = list(getattr(plan, "tasks", []) or []) if plan else []
        for task in reversed(tasks):
            child_steps = list(getattr(task, "steps", []) or [])
            if child_steps:
                stack.append(iter(child_steps))
    return total, done


def _normalize_mirror_progress(items: List[Dict[str, Any]]) -> None:
    first_active: Optional[int] = None
    for idx, item in enumerate(items):
        if item.get("status") == "in_progress":
            if first_active is None:
                first_active = idx
            else:
                item["status"] = "pending"
    if first_active is None:
        for item in items:
            if item.get("status") == "pending":
                item["status"] = "in_progress"
                break


def _mirror_items_from_steps(steps: List[Step], *, prefix: str = "") -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for idx, st in enumerate(list(steps or [])):
        path = f"{prefix}.s:{idx}" if prefix else f"s:{idx}"
        plan = getattr(st, "plan", None)
        tasks = list(getattr(plan, "tasks", []) or []) if plan else []
        children_total = len(tasks)
        children_done = sum(1 for task in tasks if getattr(task, "is_done", lambda: False)())
        if getattr(st, "completed", False):
            status = "completed"
            progress = 100
        elif getattr(st, "ready_for_completion", lambda: False)():
            status = "in_progress"
            progress = 100 if children_total == 0 else int((children_done / children_total) * 100)
        else:
            status = "pending"
            progress = 0 if children_total == 0 else int((children_done / children_total) * 100)
        items.append(
            {
                "kind": "step",
                "path": path,
                "id": str(getattr(st, "id", "") or ""),
                "title": str(getattr(st, "title", "") or ""),
                "status": status,
                "progress": progress,
                "children_done": children_done,
                "children_total": children_total,
                "criteria_confirmed": bool(getattr(st, "criteria_confirmed", False)),
                "tests_confirmed": bool(getattr(st, "tests_confirmed", False)),
                "criteria_auto_confirmed": bool(getattr(st, "criteria_auto_confirmed", False)),
                "tests_auto_confirmed": bool(getattr(st, "tests_auto_confirmed", False)),
                "blocked": bool(getattr(st, "blocked", False)),
            }
        )
    return items


def _mirror_items_from_task_nodes(nodes: List[TaskNode], *, prefix: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for idx, node in enumerate(list(nodes or [])):
        path = f"{prefix}.t:{idx}" if prefix else f"t:{idx}"
        total, done = _count_step_tree(list(getattr(node, "steps", []) or []))
        progress = int((done / total) * 100) if total else 0
        status_raw = str(getattr(node, "status", "") or "TODO").strip().upper()
        if bool(getattr(node, "is_done", lambda: False)()) or (status_raw == "DONE" and not getattr(node, "blocked", False)):
            status = "completed"
        elif status_raw == "ACTIVE":
            status = "in_progress"
        else:
            status = "pending"
        items.append(
            {
                "kind": "task",
                "path": path,
                "id": str(getattr(node, "id", "") or ""),
                "title": str(getattr(node, "title", "") or ""),
                "status": status,
                "progress": progress,
                "children_done": done,
                "children_total": total,
                "criteria_confirmed": bool(getattr(node, "criteria_confirmed", False)),
                "tests_confirmed": bool(getattr(node, "tests_confirmed", False)),
                "criteria_auto_confirmed": bool(getattr(node, "criteria_auto_confirmed", False)),
                "tests_auto_confirmed": bool(getattr(node, "tests_auto_confirmed", False)),
                "blocked": bool(getattr(node, "blocked", False)),
            }
        )
    return items


def _mirror_items_from_tasks(tasks: List[TaskDetail]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for task in list(tasks or []):
        total, done = _count_step_tree(list(getattr(task, "steps", []) or []))
        progress = int((done / total) * 100) if total else int(getattr(task, "progress", 0) or 0)
        status_raw = str(getattr(task, "status", "") or "TODO").strip().upper()
        blocked = bool(getattr(task, "blocked", False))
        if progress >= 100 and not blocked:
            status = "completed"
        elif status_raw == "ACTIVE":
            status = "in_progress"
        elif status_raw == "DONE":
            status = "completed"
        else:
            status = "pending"
        items.append(
            {
                "kind": "task",
                "task_id": str(getattr(task, "id", "") or ""),
                "title": str(getattr(task, "title", "") or ""),
                "status": status,
                "progress": progress,
                "children_done": done,
                "children_total": total,
                "criteria_confirmed": bool(getattr(task, "criteria_confirmed", False)),
                "tests_confirmed": bool(getattr(task, "tests_confirmed", False)),
                "criteria_auto_confirmed": bool(getattr(task, "criteria_auto_confirmed", False)),
                "tests_auto_confirmed": bool(getattr(task, "tests_auto_confirmed", False)),
                "blocked": blocked,
            }
        )
    return items


def build_context(
    manager: TaskManager,
    focus_id: Optional[str] = None,
    *,
    include_all_tasks: bool = False,
    compact: bool = True,
    tasks_filter: Optional[Dict[str, Any]] = None,
    plans_filter: Optional[Dict[str, Any]] = None,
    tasks_page: Optional[Dict[str, Any]] = None,
    plans_page: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    details = manager.list_all_tasks(skip_sync=True)
    plans = [d for d in details if getattr(d, "kind", "task") == "plan"]
    tasks = [d for d in details if getattr(d, "kind", "task") == "task"]

    by_status = {"DONE": 0, "ACTIVE": 0, "TODO": 0}
    for t in tasks:
        key = str(getattr(t, "status", "TODO") or "TODO").upper()
        if key not in by_status:
            key = "TODO"
        by_status[key] += 1

    ctx: Dict[str, Any] = {
        "counts": {"plans": len(plans), "tasks": len(tasks)},
        "by_status": by_status,
    }
    if include_all_tasks:
        filtered_plans = plans
        filtered_tasks = tasks
        if plans_filter:
            filtered_plans = _apply_filters(
                plans,
                statuses=plans_filter.get("statuses"),
                tags=plans_filter.get("tags"),
                domain=plans_filter.get("domain"),
            )
        if tasks_filter:
            filtered_tasks = _apply_filters(
                tasks,
                statuses=tasks_filter.get("statuses"),
                tags=tasks_filter.get("tags"),
                domain=tasks_filter.get("domain"),
                parent=tasks_filter.get("parent"),
            )

        plans_sorted = sorted(filtered_plans, key=lambda d: d.id)
        tasks_sorted = sorted(filtered_tasks, key=lambda d: d.id)
        plans_page = plans_page or {}
        tasks_page = tasks_page or {}
        plans_cursor = plans_page.get("cursor")
        plans_limit = plans_page.get("limit")
        tasks_cursor = tasks_page.get("cursor")
        tasks_limit = tasks_page.get("limit")
        plans_slice, plans_meta = _paginate_items(plans_sorted, cursor=plans_cursor, limit=plans_limit)
        tasks_slice, tasks_meta = _paginate_items(tasks_sorted, cursor=tasks_cursor, limit=tasks_limit)

        ctx["plans"] = [plan_to_dict(p, compact=compact) for p in plans_slice]
        ctx["tasks"] = [task_to_dict(t, include_steps=True, compact=compact) for t in tasks_slice]
        ctx["plans_pagination"] = plans_meta
        ctx["tasks_pagination"] = tasks_meta
        if plans_filter or tasks_filter:
            ctx["filtered_counts"] = {"plans": len(filtered_plans), "tasks": len(filtered_tasks)}

    if focus_id:
        focus = manager.load_task(focus_id, skip_sync=True)
        if focus:
            if getattr(focus, "kind", "task") == "plan":
                ctx["current_plan"] = plan_to_dict(focus, compact=False)
            else:
                ctx["current_task"] = task_to_dict(focus, include_steps=True, compact=False)
    return ctx


def generate_suggestions(manager: TaskManager, focus_id: Optional[str] = None) -> List[Suggestion]:
    details = manager.list_all_tasks(skip_sync=True)
    plans = [d for d in details if getattr(d, "kind", "task") == "plan"]
    tasks = [d for d in details if getattr(d, "kind", "task") == "task"]
    if not plans:
        return [
            Suggestion(
                action="create",
                target="PLAN",
                reason="Нет планов — создай план (kind=plan) и зафиксируй контракт.",
                priority="high",
                params={"kind": "plan"},
            )
        ]
    if focus_id and focus_id.startswith("PLAN-"):
        has_tasks = any(getattr(t, "parent", None) == focus_id for t in tasks)
        if not has_tasks:
            return [
                Suggestion(
                    action="create",
                    target="TASK",
                    reason="В плане нет заданий — добавь первое задание в план.",
                    priority="high",
                    params={"kind": "task", "parent": focus_id},
                )
            ]
    if focus_id and focus_id.startswith("TASK-"):
        task = manager.load_task(focus_id, skip_sync=True)
        if task and task.steps:
            checkpoints = _compute_checkpoint_status(task)
            if checkpoints["pending"]:
                step_id = checkpoints.get("pending_ids", [""])[0] if isinstance(checkpoints.get("pending_ids"), list) else ""
                return [
                    Suggestion(
                        action="verify",
                        target=checkpoints["pending"][0],
                        reason="Есть шаги без подтверждённых чекпоинтов (criteria/tests).",
                        priority="normal",
                        params={"task": focus_id, "path": checkpoints["pending"][0], "step_id": step_id or None},
                    )
                ]
            if checkpoints["ready"]:
                step_id = checkpoints.get("ready_ids", [""])[0] if isinstance(checkpoints.get("ready_ids"), list) else ""
                return [
                    Suggestion(
                        action="done",
                        target=checkpoints["ready"][0],
                        reason="Есть шаги готовые к завершению.",
                        priority="normal",
                        params={"task": focus_id, "path": checkpoints["ready"][0], "step_id": step_id or None},
                    )
                ]
    return []


def handle_context(manager: TaskManager, data: Dict[str, Any]) -> AIResponse:
    focus = data.get("task") or data.get("plan")  # allow explicit focus keys
    if focus is not None:
        err = validate_task_id(focus)
        if err:
            return error_response("context", "INVALID_ID", err)
        focus = str(focus)
    include_all = bool(data.get("include_all", False))
    compact = bool(data.get("compact", True))

    tasks_filter: Dict[str, Any] = {}
    plans_filter: Dict[str, Any] = {}
    filters_applied: List[str] = []

    tasks_status_raw = data.get("tasks_status")
    plans_status_raw = data.get("plans_status")
    tags_raw = data.get("tags") if data.get("tags") is not None else data.get("tag")
    domain_raw = data.get("domain")
    tasks_parent = data.get("tasks_parent") or data.get("parent")

    try:
        if tasks_status_raw is not None:
            tasks_filter["statuses"] = _normalize_status_filter(tasks_status_raw)
            filters_applied.append("tasks_status")
        if plans_status_raw is not None:
            plans_filter["statuses"] = _normalize_status_filter(plans_status_raw)
            filters_applied.append("plans_status")
        if tags_raw is not None:
            tags = _normalize_filter_list(tags_raw)
            tasks_filter["tags"] = tags
            plans_filter["tags"] = tags
            filters_applied.append("tags")
        if domain_raw is not None:
            domain = str(domain_raw or "").strip()
            tasks_filter["domain"] = domain
            plans_filter["domain"] = domain
            filters_applied.append("domain")
    except ValueError as exc:
        return error_response("context", "INVALID_FILTER", str(exc))

    if tasks_parent is not None:
        err = validate_task_id(tasks_parent)
        if err:
            return error_response("context", "INVALID_PARENT", err)
        tasks_filter["parent"] = str(tasks_parent)
        filters_applied.append("tasks_parent")

    tasks_limit, err = _parse_limit(data.get("tasks_limit"), "tasks_limit")
    if err:
        return error_response("context", "INVALID_PAGINATION", err)
    plans_limit, err = _parse_limit(data.get("plans_limit"), "plans_limit")
    if err:
        return error_response("context", "INVALID_PAGINATION", err)
    tasks_cursor, err = _parse_cursor(data.get("tasks_cursor"), "tasks_cursor")
    if err:
        return error_response("context", "INVALID_PAGINATION", err)
    plans_cursor, err = _parse_cursor(data.get("plans_cursor"), "plans_cursor")
    if err:
        return error_response("context", "INVALID_PAGINATION", err)

    ctx = build_context(
        manager,
        focus,
        include_all_tasks=include_all,
        compact=compact,
        tasks_filter=tasks_filter or None,
        plans_filter=plans_filter or None,
        tasks_page={"cursor": tasks_cursor, "limit": tasks_limit} if (tasks_cursor is not None or tasks_limit is not None) else None,
        plans_page={"cursor": plans_cursor, "limit": plans_limit} if (plans_cursor is not None or plans_limit is not None) else None,
    )

    if include_all and filters_applied:
        ctx["filters_applied"] = filters_applied

    subtree = data.get("subtree")
    if subtree is not None:
        if not isinstance(subtree, dict):
            return error_response("context", "INVALID_SUBTREE", "subtree должен быть объектом")
        subtree_task_id = subtree.get("task") or focus
        if not subtree_task_id:
            return error_response("context", "MISSING_SUBTREE_TASK", "subtree.task обязателен")
        err = validate_task_id(subtree_task_id)
        if err:
            return error_response("context", "INVALID_SUBTREE_TASK", err)
        subtree_task = manager.load_task(str(subtree_task_id), skip_sync=True)
        if not subtree_task:
            return error_response("context", "SUBTREE_NOT_FOUND", f"Не найдено: {subtree_task_id}")
        kind = str(subtree.get("kind", "") or "").strip().lower()
        path = str(subtree.get("path", "") or "").strip()
        step_id = subtree.get("step_id")
        task_node_id = subtree.get("task_node_id")
        if not kind:
            if task_node_id is not None:
                kind = "task"
            elif step_id is not None:
                kind = "step"
            elif path.split(".")[-1].startswith("t:"):
                kind = "task"
            else:
                kind = "step"
        if not path:
            if kind == "task" and task_node_id is not None:
                err = validate_node_id(task_node_id, "task_node_id")
                if err:
                    return error_response("context", "INVALID_TASK_NODE_ID", err)
                path = TaskManager.find_task_node_path_by_id(subtree_task, str(task_node_id)) or ""
            elif kind in {"step", "plan"} and step_id is not None:
                err = validate_node_id(step_id, "step_id")
                if err:
                    return error_response("context", "INVALID_STEP_ID", err)
                path = TaskManager.find_step_path_by_id(subtree_task, str(step_id)) or ""
        if not path:
            return error_response("context", "MISSING_SUBTREE_PATH", "subtree.path обязателен")
        path_err = validate_task_path(path) if kind == "task" else validate_step_path(path)
        if path_err:
            return error_response("context", "INVALID_SUBTREE_PATH", path_err)

        compact_flag = bool(subtree.get("compact", compact))
        subtree_payload = _build_subtree_payload(subtree_task, path=path, kind=kind, compact=compact_flag)
        if not subtree_payload:
            return error_response("context", "SUBTREE_NOT_FOUND", "Узел не найден по subtree.path")
        subtree_payload["task_id"] = str(subtree_task_id)
        ctx["subtree"] = subtree_payload
    return AIResponse(
        success=True,
        intent="context",
        result=ctx,
        context={"focus_id": focus} if focus else {},
        suggestions=generate_suggestions(manager, focus),
    )


def handle_focus_get(manager: TaskManager, data: Dict[str, Any]) -> AIResponse:
    last_id, last_domain = get_last_task()
    focus_id: Optional[str] = None
    if last_id:
        try:
            focus_id = normalize_task_id(last_id)
        except Exception:
            focus_id = str(last_id).strip() or None
    domain = str(last_domain or "")
    return AIResponse(
        success=True,
        intent="focus_get",
        result={"focus": {"id": focus_id, "domain": domain} if focus_id else None},
        context={"focus_id": focus_id, "domain": domain} if focus_id else {},
    )


def handle_focus_set(manager: TaskManager, data: Dict[str, Any]) -> AIResponse:
    raw = data.get("task")
    if not raw:
        return error_response(
            "focus_set",
            "MISSING_TASK",
            "task обязателен",
            recovery="Передай task=TASK-###|PLAN-### или сначала создай объект через create.",
            suggestions=_missing_target_suggestions(manager, want=["TASK-", "PLAN-"]),
        )
    err = validate_task_id(raw)
    if err:
        return error_response(
            "focus_set",
            "INVALID_TASK",
            err,
            recovery="Проверь id через context(include_all=true).",
            suggestions=_missing_target_suggestions(manager, want=["TASK-", "PLAN-"]),
        )
    focus_id = normalize_task_id(str(raw))

    # Best-effort validation: focus must point to an existing object.
    if not manager.load_task(focus_id, skip_sync=True):
        return error_response(
            "focus_set",
            "NOT_FOUND",
            f"Не найдено: {focus_id}",
            recovery="Проверь id через context(include_all=true) или создай объект.",
            suggestions=_missing_target_suggestions(manager, want="PLAN-" if focus_id.startswith("PLAN-") else "TASK-"),
            result={"task": focus_id},
        )

    domain = str(data.get("domain", "") or "").strip()
    save_last_task(focus_id, domain)
    return AIResponse(
        success=True,
        intent="focus_set",
        result={"focus": {"id": focus_id, "domain": domain}},
        context={"focus_id": focus_id, "domain": domain},
    )


def handle_focus_clear(manager: TaskManager, data: Dict[str, Any]) -> AIResponse:
    removed = clear_last_task()
    return AIResponse(success=True, intent="focus_clear", result={"cleared": bool(removed), "focus": None})


def handle_radar(manager: TaskManager, data: Dict[str, Any]) -> AIResponse:
    focus = data.get("task") or data.get("plan")
    focus_domain: str = ""
    if focus is None:
        last_id, last_domain = get_last_task()
        focus = last_id
        focus_domain = str(last_domain or "")
    if not focus:
        return error_response(
            "radar",
            "MISSING_ID",
            "Не указан task/plan и нет focus",
            recovery="Передай task=TASK-###|plan=PLAN-### или установи focus через focus_set.",
            suggestions=_missing_target_suggestions(manager, want=["TASK-", "PLAN-"]),
        )
    err = validate_task_id(focus)
    if err:
        return error_response(
            "radar",
            "INVALID_ID",
            err,
            recovery="Проверь id через context(include_all=true) или установи focus через focus_set.",
            suggestions=_missing_target_suggestions(manager, want=["TASK-", "PLAN-"]),
        )
    focus_id = str(focus)
    detail = manager.load_task(focus_id, skip_sync=True)
    if not detail:
        return error_response(
            "radar",
            "NOT_FOUND",
            f"Не найдено: {focus_id}",
            recovery="Проверь id через context(include_all=true) или установи focus заново.",
            suggestions=_missing_target_suggestions(manager, want="PLAN-" if focus_id.startswith("PLAN-") else "TASK-"),
        )

    try:
        limit = int(data.get("limit", 3) or 3)
    except Exception:
        return error_response("radar", "INVALID_LIMIT", "limit должен быть числом")
    limit = max(0, min(limit, 10))

    focus_payload = {
        "id": focus_id,
        "kind": str(getattr(detail, "kind", "task") or "task"),
        "revision": int(getattr(detail, "revision", 0) or 0),
        "domain": str(getattr(detail, "domain", "") or focus_domain or ""),
        "title": str(getattr(detail, "title", "") or ""),
    }

    next_suggestions = list(generate_suggestions(manager, focus_id))[:limit]

    focus_key = "plan" if getattr(detail, "kind", "task") == "plan" else "task"
    result: Dict[str, Any] = {
        "focus": focus_payload,
        "next": [s.to_dict() for s in next_suggestions],
        "links": {
            "resume": {"intent": "resume", focus_key: focus_id},
            "mirror": {"intent": "mirror", focus_key: focus_id, "limit": 10},
            "context": {"intent": "context", "include_all": True, "compact": True},
            "focus_get": {"intent": "focus_get"},
            "history": {"intent": "history", "limit": 20},
        },
    }

    if getattr(detail, "kind", "task") == "plan":
        contract_summary = _contract_summary(getattr(detail, "contract_data", {}) or {})
        steps = list(getattr(detail, "plan_steps", []) or [])
        current = int(getattr(detail, "plan_current", 0) or 0)
        current = max(0, min(current, len(steps)))
        title = steps[current] if current < len(steps) else ""
        status = "completed" if steps and current >= len(steps) else ("in_progress" if steps else "pending")
        result["now"] = {"kind": "plan_step", "index": current, "title": title, "total": len(steps), "status": status}
        why_payload: Dict[str, Any] = {
            "plan_id": focus_id,
            "contract_preview": _preview_text(str(getattr(detail, "contract", "") or "")),
        }
        if contract_summary:
            why_payload["contract"] = contract_summary
        result["why"] = why_payload
        result["verify"] = {
            "checks": list(getattr(detail, "tests", []) or []),
            "criteria_confirmed": bool(getattr(detail, "criteria_confirmed", False)),
            "tests_confirmed": bool(getattr(detail, "tests_confirmed", False)),
        }
        open_checkpoints: List[str] = []
        if list(getattr(detail, "success_criteria", []) or []) and not bool(getattr(detail, "criteria_confirmed", False)):
            open_checkpoints.append("criteria")
        tests_auto = bool(getattr(detail, "tests_auto_confirmed", False))
        if list(getattr(detail, "tests", []) or []) and not (bool(getattr(detail, "tests_confirmed", False)) or tests_auto):
            open_checkpoints.append("tests")
        result["open_checkpoints"] = open_checkpoints
        result["how_to_verify"] = {
            "commands": _dedupe_strs(list(contract_summary.get("checks", []) or []) + list(getattr(detail, "tests", []) or [])),
            "open_checkpoints": open_checkpoints,
        }
        result["blockers"] = {"blocked": bool(getattr(detail, "blocked", False)), "blockers": list(getattr(detail, "blockers", []) or [])}
        return AIResponse(
            success=True,
            intent="radar",
            result=result,
            context={"task_id": focus_id},
            suggestions=next_suggestions,
        )

    task = detail
    items = _mirror_items_from_steps(list(getattr(task, "steps", []) or []))
    _normalize_mirror_progress(items)
    now = next((i for i in items if i.get("status") == "in_progress"), items[0] if items else None)
    result["now"] = now

    plan_id = str(getattr(task, "parent", "") or "").strip()
    plan = manager.load_task(plan_id, skip_sync=True) if plan_id else None
    plan_contract_summary = _contract_summary(getattr(plan, "contract_data", {}) or {}) if plan else {}
    why_payload = {
        "plan_id": plan_id or None,
        "contract_preview": _preview_text(str(getattr(plan, "contract", "") or "")) if plan else "",
    }
    if plan_contract_summary:
        why_payload["contract"] = plan_contract_summary
    result["why"] = why_payload
    result["how_to_verify"] = {"commands": list(plan_contract_summary.get("checks", []) or [])}

    if now and now.get("path"):
        path = str(now.get("path") or "")
        st, _, _ = _find_step_by_path(list(getattr(task, "steps", []) or []), path)
        if st:
            missing: List[Dict[str, Any]] = []
            if not bool(getattr(st, "criteria_confirmed", False)):
                missing.append({"checkpoint": "criteria", "path": path})
            if not (bool(getattr(st, "tests_confirmed", False)) or bool(getattr(st, "tests_auto_confirmed", False))):
                missing.append({"checkpoint": "tests", "path": path})
            if bool(getattr(st, "blocked", False)):
                missing.append({"checkpoint": "unblocked", "path": path})
            result["verify"] = {
                "path": path,
                "step_id": str(getattr(st, "id", "") or ""),
                "tests": list(getattr(st, "tests", []) or []),
                "missing": missing,
            }
            result["how_to_verify"] = {
                "path": path,
                "step_id": str(getattr(st, "id", "") or ""),
                "commands": _dedupe_strs(list(plan_contract_summary.get("checks", []) or []) + list(getattr(st, "tests", []) or [])),
                "missing_checkpoints": [m.get("checkpoint") for m in missing if m.get("checkpoint")],
            }

    deps = [str(d or "").strip() for d in list(getattr(task, "depends_on", []) or []) if str(d or "").strip()]
    unresolved: List[str] = []
    for dep_id in deps:
        dep = manager.load_task(dep_id, skip_sync=True)
        if not dep or str(getattr(dep, "status", "") or "").upper() != "DONE":
            unresolved.append(dep_id)
    result["blockers"] = {
        "blocked": bool(getattr(task, "blocked", False)),
        "blockers": list(getattr(task, "blockers", []) or []),
        "depends_on": deps,
        "unresolved_depends_on": unresolved,
    }
    result["open_checkpoints"] = _compute_checkpoint_status(task)

    return AIResponse(
        success=True,
        intent="radar",
        result=result,
        context={"task_id": focus_id},
        suggestions=next_suggestions,
    )


def handle_resume(manager: TaskManager, data: Dict[str, Any]) -> AIResponse:
    focus = data.get("task") or data.get("plan")
    if focus is None:
        last_id, _domain = get_last_task()
        focus = last_id
    if not focus:
        return error_response(
            "resume",
            "MISSING_ID",
            "Не указан task/plan и нет focus",
            recovery="Передай task=TASK-###|plan=PLAN-### или установи focus через focus_set.",
            suggestions=_missing_target_suggestions(manager, want=["TASK-", "PLAN-"]),
        )
    err = validate_task_id(focus)
    if err:
        return error_response(
            "resume",
            "INVALID_ID",
            err,
            recovery="Проверь id через context(include_all=true) или установи focus через focus_set.",
            suggestions=_missing_target_suggestions(manager, want=["TASK-", "PLAN-"]),
        )
    focus_id = str(focus)
    detail = manager.load_task(focus_id, skip_sync=True)
    if not detail:
        return error_response(
            "resume",
            "NOT_FOUND",
            f"Не найдено: {focus_id}",
            recovery="Проверь id через context(include_all=true) или установи focus заново.",
            suggestions=_missing_target_suggestions(manager, want="PLAN-" if focus_id.startswith("PLAN-") else "TASK-"),
            result={"task": focus_id},
        )
    result: Dict[str, Any] = {}
    if getattr(detail, "kind", "task") == "plan":
        result["plan"] = plan_to_dict(detail, compact=False)
    else:
        result["task"] = task_to_dict(detail, include_steps=True, compact=False)
        result["checkpoint_status"] = _compute_checkpoint_status(detail)
    # Timeline: expose events if present (already structured)
    events = list(getattr(detail, "events", []) or [])
    if events:
        try:
            events_sorted = sorted(events, key=lambda e: getattr(e, "timestamp", "") or "", reverse=True)
        except Exception:
            events_sorted = events
        limit = int(data.get("events_limit", 20) or 20)
        result["timeline"] = [e.to_dict() for e in events_sorted[: max(0, limit)]]
    return AIResponse(
        success=True,
        intent="resume",
        result=result,
        context={"task_id": focus_id},
        suggestions=generate_suggestions(manager, focus_id),
    )


def handle_lint(manager: TaskManager, data: Dict[str, Any]) -> AIResponse:
    focus = data.get("task") or data.get("plan")
    if focus is None:
        last_id, _domain = get_last_task()
        focus = last_id
    if not focus:
        return error_response(
            "lint",
            "MISSING_ID",
            "Не указан task/plan и нет focus",
            recovery="Передай task=TASK-###|plan=PLAN-### или установи focus через focus_set.",
            suggestions=_missing_target_suggestions(manager, want=["TASK-", "PLAN-"]),
        )
    err = validate_task_id(focus)
    if err:
        return error_response(
            "lint",
            "INVALID_ID",
            err,
            recovery="Проверь id через context(include_all=true) или установи focus через focus_set.",
            suggestions=_missing_target_suggestions(manager, want=["TASK-", "PLAN-"]),
        )
    focus_id = str(focus)
    detail = manager.load_task(focus_id, skip_sync=True)
    if not detail:
        return error_response(
            "lint",
            "NOT_FOUND",
            f"Не найдено: {focus_id}",
            recovery="Проверь id через context(include_all=true) или установи focus заново.",
            suggestions=_missing_target_suggestions(manager, want="PLAN-" if focus_id.startswith("PLAN-") else "TASK-"),
            result={"task": focus_id},
        )

    # Read-only: avoid TaskManager.list_all_tasks (may auto-clean DONE tasks depending on user config).
    all_items = manager.repo.list("", skip_sync=True)
    report = lint_item(manager, detail, all_items)

    # Actionable fixes (top 3, deterministic).
    suggestions: List[Suggestion] = []
    for issue in list(getattr(report, "issues", []) or []):
        code = str(getattr(issue, "code", "") or "")
        target = dict(getattr(issue, "target", {}) or {})
        if code in {"STEP_SUCCESS_CRITERIA_MISSING", "STEP_TESTS_MISSING", "STEP_BLOCKERS_MISSING"} and target.get("path"):
            path = str(target.get("path") or "")
            ops: List[Dict[str, Any]] = []
            if code == "STEP_SUCCESS_CRITERIA_MISSING":
                ops.append({"op": "set", "field": "success_criteria", "value": ["<define measurable outcome>"]})
            if code == "STEP_TESTS_MISSING":
                ops.append({"op": "set", "field": "tests", "value": ["<how to verify (cmd/test)>"]})
            if code == "STEP_BLOCKERS_MISSING":
                ops.append({"op": "set", "field": "blockers", "value": ["<dependency/assumption>"]})
            if ops:
                suggestions.append(
                    Suggestion(
                        action="patch",
                        target="tasks_patch",
                        reason="Заполни поля шага через patch (diff-oriented).",
                        priority="high" if code == "STEP_SUCCESS_CRITERIA_MISSING" else "normal",
                        params={"task": focus_id, "kind": "step", "path": path, "ops": ops},
                    )
                )
        elif code == "TASK_SUCCESS_CRITERIA_MISSING":
            suggestions.append(
                Suggestion(
                    action="patch",
                    target="tasks_patch",
                    reason="Добавь root success_criteria (иначе done будет заблокирован).",
                    priority="high",
                    params={"task": focus_id, "kind": "task_detail", "ops": [{"op": "set", "field": "success_criteria", "value": ["<definition of done>"]}]},
                )
            )
        elif code in {"INVALID_DEPENDENCIES", "CIRCULAR_DEPENDENCY", "DEPENDS_ON_INVALID"}:
            suggestions.append(
                Suggestion(
                    action="context",
                    target="tasks_context",
                    reason="Проверь существующие TASK-### и статусы зависимостей перед правкой depends_on.",
                    priority="high",
                    params={"include_all": True, "compact": True},
                )
            )
        if len(suggestions) >= 3:
            break

    result = report.to_dict()
    result["links"] = {
        "radar": {"intent": "radar", "task": focus_id, "limit": 3},
        "resume": {"intent": "resume", "task": focus_id},
        "mirror": {"intent": "mirror", "task": focus_id, "limit": 10},
    }
    return AIResponse(
        success=True,
        intent="lint",
        result=result,
        context={"task_id": focus_id},
        suggestions=suggestions,
    )


def handle_create(manager: TaskManager, data: Dict[str, Any]) -> AIResponse:
    title = str(data.get("title", "") or "").strip()
    if not title:
        return error_response("create", "MISSING_TITLE", "title обязателен")
    title_err = validate_string(title, "title")
    if title_err:
        return error_response("create", "INVALID_TITLE", title_err)

    kind = str(data.get("kind", "") or "").strip().lower()
    parent = data.get("parent")
    if parent is not None:
        err = validate_task_id(parent)
        if err:
            return error_response("create", "INVALID_PARENT", err)
        parent = str(parent)

    if kind not in {"", "plan", "task"}:
        return error_response("create", "INVALID_KIND", "kind должен быть 'plan' или 'task'")
    if not kind:
        kind = "task" if parent else "plan"

    dry_run = bool(data.get("dry_run", False))

    if kind == "plan":
        plan = manager.create_plan(title, priority=str(data.get("priority", "MEDIUM") or "MEDIUM"))
        plan.description = str(data.get("description", "") or "")
        plan.context = str(data.get("context", "") or "")
        plan.contract = str(data.get("contract", "") or "")
        contract_data = data.get("contract_data")
        if contract_data is not None:
            if not isinstance(contract_data, dict):
                return error_response("create", "INVALID_CONTRACT_DATA", "contract_data должен быть объектом")
            plan.contract_data = dict(contract_data)
        sc = data.get("success_criteria")
        tests = data.get("tests")
        blockers = data.get("blockers")
        for field, value in (("success_criteria", sc), ("tests", tests), ("blockers", blockers)):
            if value is None:
                continue
            err = validate_array(value, field)
            if err:
                return error_response("create", "INVALID_FIELDS", err, result={field: value})
        if sc is not None:
            plan.success_criteria = _normalize_str_list(sc)
            plan.criteria_confirmed = False
            plan.criteria_auto_confirmed = False
        if tests is not None:
            plan.tests = _normalize_str_list(tests)
            plan.tests_confirmed = False
            plan.tests_auto_confirmed = not plan.tests
        if blockers is not None:
            plan.blockers = _normalize_str_list(blockers)
        if dry_run:
            return AIResponse(
                success=True,
                intent="create",
                result={"dry_run": True, "would_execute": True, "plan": plan_to_dict(plan, compact=False)},
            )
        if str(getattr(plan, "contract", "") or "").strip() or dict(getattr(plan, "contract_data", {}) or {}) or list(getattr(plan, "success_criteria", []) or []):
            append_contract_version_if_changed(plan, note="create")
        manager.save_task(plan, skip_sync=True)
        return AIResponse(
            success=True,
            intent="create",
            result={"plan_id": plan.id, "plan": plan_to_dict(plan, compact=False)},
            context={"task_id": plan.id},
        )

    # kind == task
    if not parent:
        return error_response("create", "MISSING_PARENT", "Для задания нужен parent=PLAN-###")
    if not str(parent).startswith("PLAN-"):
        return error_response("create", "INVALID_PARENT", "parent должен быть PLAN-###")
    try:
        task = manager.create_task(title, parent=str(parent), priority=str(data.get("priority", "MEDIUM") or "MEDIUM"))
    except ValueError as exc:
        msg = str(exc) or "Invalid parent"
        code = "PARENT_NOT_FOUND" if "not found" in msg.lower() else "INVALID_PARENT"
        return error_response("create", code, msg, result={"parent": str(parent)})
    task.description = str(data.get("description", "") or "")
    task.context = str(data.get("context", "") or "")
    task.contract = str(data.get("contract", "") or "")
    sc = data.get("success_criteria")
    tests = data.get("tests")
    blockers = data.get("blockers")
    for field, value in (("success_criteria", sc), ("tests", tests), ("blockers", blockers)):
        if value is None:
            continue
        err = validate_array(value, field)
        if err:
            return error_response("create", "INVALID_FIELDS", err, result={field: value})
    if sc is not None:
        task.success_criteria = _normalize_str_list(sc)
        task.criteria_confirmed = False
        task.criteria_auto_confirmed = False
    if tests is not None:
        task.tests = _normalize_str_list(tests)
        task.tests_confirmed = False
        task.tests_auto_confirmed = not task.tests
    if blockers is not None:
        task.blockers = _normalize_str_list(blockers)
    steps_payload = data.get("steps")
    if steps_payload is not None:
        err = validate_steps_data(steps_payload)
        if err:
            return error_response("create", "INVALID_STEPS", err)
        task.steps = [_parse_step_node(node) for node in steps_payload]
    if dry_run:
        return AIResponse(
            success=True,
            intent="create",
            result={"dry_run": True, "would_execute": True, "task": task_to_dict(task, include_steps=True, compact=False)},
        )
    manager.save_task(task, skip_sync=True)
    return AIResponse(
        success=True,
        intent="create",
        result={"task_id": task.id, "task": task_to_dict(task, include_steps=True, compact=False)},
        context={"task_id": task.id},
    )


def handle_decompose(manager: TaskManager, data: Dict[str, Any]) -> AIResponse:
    task_id = data.get("task")
    if not task_id:
        return error_response(
            "decompose",
            "MISSING_TASK",
            "task обязателен",
            recovery="Передай task=TASK-### (явная адресация) или установи focus через focus_set и передай его явно.",
            suggestions=_missing_target_suggestions(manager, want="TASK-"),
        )
    err = validate_task_id(task_id)
    if err:
        return error_response(
            "decompose",
            "INVALID_TASK",
            err,
            recovery="Проверь id через context(include_all=true).",
            suggestions=_missing_target_suggestions(manager, want="TASK-"),
        )
    task_id = str(task_id)
    steps_payload = data.get("steps")
    if steps_payload is None:
        return error_response("decompose", "MISSING_STEPS", "steps обязателен")
    err = validate_steps_data(steps_payload)
    if err:
        return error_response("decompose", "INVALID_STEPS", err)

    parent_path = data.get("parent")
    parent_task_node_id = data.get("parent_task_node_id")
    if parent_path is not None and parent_task_node_id is not None:
        return error_response("decompose", "INVALID_PARENT_PATH", "Укажи только parent или parent_task_node_id")

    task = manager.load_task(task_id, skip_sync=True)
    if not task:
        return error_response(
            "decompose",
            "NOT_FOUND",
            f"Не найдено: {task_id}",
            recovery="Проверь id через context(include_all=true).",
            suggestions=_missing_target_suggestions(manager, want="TASK-"),
            result={"task": task_id},
        )
    if getattr(task, "kind", "task") != "task":
        return error_response("decompose", "NOT_A_TASK", "decompose применим только к заданиям (TASK-###)")

    if parent_task_node_id is not None:
        err = validate_node_id(parent_task_node_id, "parent_task_node_id")
        if err:
            return error_response(
                "decompose",
                "INVALID_PARENT_TASK_NODE_ID",
                err,
                recovery="Чтобы найти parent_task_node_id, используй mirror(kind=step|task) или radar.",
                suggestions=_path_help_suggestions(task_id),
            )
        parent_path = manager.find_task_node_path_by_id(task, str(parent_task_node_id))
        if not parent_path:
            return error_response(
                "decompose",
                "PARENT_TASK_NODE_ID_NOT_FOUND",
                f"Задание parent_task_node_id={parent_task_node_id} не найдено",
                recovery="Возьми корректный task_node_id через mirror (он показывает task_node_id и path).",
                suggestions=_path_help_suggestions(task_id),
            )
    elif parent_path is not None:
        path_err = validate_task_path(parent_path)
        if path_err:
            return error_response(
                "decompose",
                "INVALID_PARENT_PATH",
                path_err,
                recovery="Возьми корректный parent path через mirror/radar.",
                suggestions=_path_help_suggestions(task_id),
            )
        parent_path = str(parent_path)

    created = 0
    for node in steps_payload:
        step = _parse_step_node(node)
        ok, msg = manager.add_step(task_id, step.title, task.domain, step.success_criteria, step.tests, step.blockers, parent_path=parent_path)
        if not ok:
            if msg == "path":
                return error_response(
                    "decompose",
                    "PATH_NOT_FOUND",
                    "Неверный parent path",
                    recovery="Возьми корректный parent path через mirror/radar.",
                    suggestions=_path_help_suggestions(task_id),
                    result={"parent": parent_path},
                )
            return error_response("decompose", "FAILED", msg or "Не удалось добавить шаг")
        created += 1

    updated = manager.load_task(task_id, task.domain, skip_sync=True)
    return AIResponse(
        success=True,
        intent="decompose",
        result={
            "task_id": task_id,
            "total_created": created,
            "task": task_to_dict(updated or task, include_steps=True, compact=False),
        },
        context={"task_id": task_id},
    )


def handle_task_add(manager: TaskManager, data: Dict[str, Any]) -> AIResponse:
    task_id = data.get("task")
    if not task_id:
        return error_response(
            "task_add",
            "MISSING_TASK",
            "task обязателен",
            recovery="Передай task=TASK-### или установи focus через focus_set и передай его явно.",
            suggestions=_missing_target_suggestions(manager, want="TASK-"),
        )
    err = validate_task_id(task_id)
    if err:
        return error_response(
            "task_add",
            "INVALID_TASK",
            err,
            recovery="Проверь id через context(include_all=true).",
            suggestions=_missing_target_suggestions(manager, want="TASK-"),
        )
    task_id = str(task_id)

    parent_step = data.get("parent_step") or data.get("step") or data.get("step_path")
    parent_step_id = data.get("parent_step_id") or data.get("step_id")
    if parent_step is None and parent_step_id is None:
        return error_response(
            "task_add",
            "MISSING_PARENT_STEP",
            "parent_step обязателен",
            recovery="Передай parent_step=s:<n> или parent_step_id=STEP-... (чтобы найти — вызови mirror/radar).",
            suggestions=_path_help_suggestions(task_id),
        )

    title = str(data.get("title", "") or "").strip()
    if not title:
        return error_response("task_add", "MISSING_TITLE", "title обязателен")

    task = manager.load_task(task_id, skip_sync=True)
    if not task:
        return error_response(
            "task_add",
            "NOT_FOUND",
            f"Не найдено: {task_id}",
            recovery="Проверь id через context(include_all=true).",
            suggestions=_missing_target_suggestions(manager, want="TASK-"),
            result={"task": task_id},
        )
    if getattr(task, "kind", "task") != "task":
        return error_response("task_add", "NOT_A_TASK", "task_add применим только к заданиям (TASK-###)")

    if parent_step_id is not None:
        err = validate_node_id(parent_step_id, "parent_step_id")
        if err:
            return error_response(
                "task_add",
                "INVALID_PARENT_STEP_ID",
                err,
                recovery="Возьми корректный step_id через radar/mirror.",
                suggestions=_path_help_suggestions(task_id),
            )
        parent_step = manager.find_step_path_by_id(task, str(parent_step_id))
        if not parent_step:
            return error_response(
                "task_add",
                "PARENT_STEP_ID_NOT_FOUND",
                f"Шаг parent_step_id={parent_step_id} не найден",
                recovery="Возьми корректный step_id через radar/mirror.",
                suggestions=_path_help_suggestions(task_id),
            )
    else:
        path_err = validate_step_path(parent_step)
        if path_err:
            return error_response(
                "task_add",
                "INVALID_PARENT_STEP",
                path_err,
                recovery="Возьми корректный parent_step через radar/mirror.",
                suggestions=_path_help_suggestions(task_id),
            )
        parent_step = str(parent_step)

    try:
        sc_list = _normalize_str_list(data.get("success_criteria")) if data.get("success_criteria") is not None else None
        tests_list = _normalize_str_list(data.get("tests")) if data.get("tests") is not None else None
        deps_list = _normalize_str_list(data.get("dependencies")) if data.get("dependencies") is not None else None
        next_list = _normalize_str_list(data.get("next_steps")) if data.get("next_steps") is not None else None
        problems_list = _normalize_str_list(data.get("problems")) if data.get("problems") is not None else None
        risks_list = _normalize_str_list(data.get("risks")) if data.get("risks") is not None else None
        blockers_list = _normalize_str_list(data.get("blockers")) if data.get("blockers") is not None else None
    except Exception:
        return error_response("task_add", "INVALID_FIELDS", "поля списков должны быть массивами строк")

    ok, code, node, task_path = manager.add_task_node(
        task_id,
        step_path=parent_step,
        title=title,
        status=data.get("status"),
        priority=data.get("priority"),
        description=str(data.get("description", "") or ""),
        context=str(data.get("context", "") or ""),
        success_criteria=sc_list,
        tests=tests_list,
        dependencies=deps_list,
        next_steps=next_list,
        problems=problems_list,
        risks=risks_list,
        blocked=bool(data.get("blocked")) if data.get("blocked") is not None else None,
        blockers=blockers_list,
        status_manual=bool(data.get("status_manual")) if data.get("status_manual") is not None else None,
        domain=task.domain,
    )
    if not ok:
        mapping = {
            "not_found": ("NOT_FOUND", f"Не найдено: {task_id}"),
            "path": ("PATH_NOT_FOUND", f"Шаг path={parent_step} не найден"),
            "missing_title": ("MISSING_TITLE", "title обязателен"),
            "invalid_status": ("INVALID_STATUS", "status должен быть TODO/ACTIVE/DONE"),
            "invalid_priority": ("INVALID_PRIORITY", "priority должен быть LOW/MEDIUM/HIGH"),
        }
        err_code, msg = mapping.get(code or "", ("FAILED", code or "Не удалось добавить задание"))
        return error_response("task_add", err_code, msg, result={"task": task_id, "parent_step": parent_step})

    updated = manager.load_task(task_id, task.domain, skip_sync=True)
    return AIResponse(
        success=True,
        intent="task_add",
        result={
            "task_id": task_id,
            "task_path": task_path,
            "task_node": task_node_to_dict(node, path=task_path, compact=False, include_steps=True) if node and task_path else None,
            "task": task_to_dict(updated or task, include_steps=True, compact=False),
        },
        context={"task_id": task_id},
    )


def handle_task_define(manager: TaskManager, data: Dict[str, Any]) -> AIResponse:
    task_id = data.get("task")
    if not task_id:
        return error_response(
            "task_define",
            "MISSING_TASK",
            "task обязателен",
            recovery="Передай task=TASK-### или установи focus через focus_set и передай его явно.",
            suggestions=_missing_target_suggestions(manager, want="TASK-"),
        )
    err = validate_task_id(task_id)
    if err:
        return error_response(
            "task_define",
            "INVALID_TASK",
            err,
            recovery="Проверь id через context(include_all=true).",
            suggestions=_missing_target_suggestions(manager, want="TASK-"),
        )
    task_id = str(task_id)

    allowed_fields = {"title", "status", "priority", "description", "context", "success_criteria", "tests", "dependencies", "next_steps", "problems", "risks", "blocked", "blockers", "status_manual"}
    if not any(field in data for field in allowed_fields):
        return error_response("task_define", "NO_FIELDS", "Нечего обновлять: укажи хотя бы одно поле")

    task = manager.load_task(task_id, skip_sync=True)
    if not task:
        return error_response(
            "task_define",
            "NOT_FOUND",
            f"Не найдено: {task_id}",
            recovery="Проверь id через context(include_all=true).",
            suggestions=_missing_target_suggestions(manager, want="TASK-"),
            result={"task": task_id},
        )
    if getattr(task, "kind", "task") != "task":
        return error_response("task_define", "NOT_A_TASK", "task_define применим только к заданиям (TASK-###)")

    path, path_err = _resolve_task_path(manager, task, data)
    if path_err:
        code, message = path_err
        return error_response(
            "task_define",
            code,
            message,
            recovery="Возьми корректный task path/task_node_id через mirror/radar.",
            suggestions=_path_help_suggestions(task_id),
        )

    try:
        sc_list = _normalize_str_list(data.get("success_criteria")) if data.get("success_criteria") is not None else None
        tests_list = _normalize_str_list(data.get("tests")) if data.get("tests") is not None else None
        deps_list = _normalize_str_list(data.get("dependencies")) if data.get("dependencies") is not None else None
        next_list = _normalize_str_list(data.get("next_steps")) if data.get("next_steps") is not None else None
        problems_list = _normalize_str_list(data.get("problems")) if data.get("problems") is not None else None
        risks_list = _normalize_str_list(data.get("risks")) if data.get("risks") is not None else None
        blockers_list = _normalize_str_list(data.get("blockers")) if data.get("blockers") is not None else None
    except Exception:
        return error_response("task_define", "INVALID_FIELDS", "поля списков должны быть массивами строк")

    ok, code, node = manager.update_task_node(
        task_id,
        path=path,
        title=data.get("title") if "title" in data else None,
        status=data.get("status") if "status" in data else None,
        priority=data.get("priority") if "priority" in data else None,
        description=data.get("description") if "description" in data else None,
        context=data.get("context") if "context" in data else None,
        success_criteria=sc_list,
        tests=tests_list,
        dependencies=deps_list,
        next_steps=next_list,
        problems=problems_list,
        risks=risks_list,
        blocked=bool(data.get("blocked")) if "blocked" in data else None,
        blockers=blockers_list,
        status_manual=bool(data.get("status_manual")) if "status_manual" in data else None,
        domain=task.domain,
    )
    if not ok:
        mapping = {
            "not_found": ("NOT_FOUND", f"Не найдено: {task_id}"),
            "path": ("PATH_NOT_FOUND", f"Задание path={path} не найдено"),
            "missing_title": ("MISSING_TITLE", "title обязателен"),
            "invalid_status": ("INVALID_STATUS", "status должен быть TODO/ACTIVE/DONE"),
        }
        err_code, msg = mapping.get(code or "", ("FAILED", code or "Не удалось обновить задание"))
        return error_response("task_define", err_code, msg, result={"task": task_id, "path": path})

    updated = manager.load_task(task_id, task.domain, skip_sync=True)
    return AIResponse(
        success=True,
        intent="task_define",
        result={
            "task_id": task_id,
            "path": path,
            "updated": task_node_to_dict(node, path=path, compact=False, include_steps=True) if node else None,
            "task": task_to_dict(updated or task, include_steps=True, compact=False),
        },
        context={"task_id": task_id},
    )


def handle_task_delete(manager: TaskManager, data: Dict[str, Any]) -> AIResponse:
    task_id = data.get("task")
    if not task_id:
        return error_response(
            "task_delete",
            "MISSING_TASK",
            "task обязателен",
            recovery="Передай task=TASK-### или установи focus через focus_set и передай его явно.",
            suggestions=_missing_target_suggestions(manager, want="TASK-"),
        )
    err = validate_task_id(task_id)
    if err:
        return error_response(
            "task_delete",
            "INVALID_TASK",
            err,
            recovery="Проверь id через context(include_all=true).",
            suggestions=_missing_target_suggestions(manager, want="TASK-"),
        )
    task_id = str(task_id)

    task = manager.load_task(task_id, skip_sync=True)
    if not task:
        return error_response(
            "task_delete",
            "NOT_FOUND",
            f"Не найдено: {task_id}",
            recovery="Проверь id через context(include_all=true).",
            suggestions=_missing_target_suggestions(manager, want="TASK-"),
            result={"task": task_id},
        )
    if getattr(task, "kind", "task") != "task":
        return error_response("task_delete", "NOT_A_TASK", "task_delete применим только к заданиям (TASK-###)")

    path, path_err = _resolve_task_path(manager, task, data)
    if path_err:
        code, message = path_err
        return error_response(
            "task_delete",
            code,
            message,
            recovery="Возьми корректный task path/task_node_id через mirror/radar.",
            suggestions=_path_help_suggestions(task_id),
        )

    ok, code, deleted = manager.delete_task_node(task_id, path=path, domain=task.domain)
    if not ok:
        mapping = {
            "not_found": ("NOT_FOUND", f"Не найдено: {task_id}"),
            "path": ("PATH_NOT_FOUND", f"Задание path={path} не найдено"),
        }
        err_code, msg = mapping.get(code or "", ("FAILED", code or "Не удалось удалить задание"))
        return error_response("task_delete", err_code, msg, result={"task": task_id, "path": path})

    updated = manager.load_task(task_id, task.domain, skip_sync=True)
    return AIResponse(
        success=True,
        intent="task_delete",
        result={
            "task_id": task_id,
            "path": path,
            "deleted": True,
            "deleted_task": task_node_to_dict(deleted, path=path, compact=False, include_steps=True) if deleted else None,
            "task": task_to_dict(updated or task, include_steps=True, compact=False),
        },
        context={"task_id": task_id},
    )


def handle_define(manager: TaskManager, data: Dict[str, Any]) -> AIResponse:
    task_id = data.get("task")
    if not task_id:
        return error_response(
            "define",
            "MISSING_TASK",
            "task обязателен",
            recovery="Передай task=TASK-### или установи focus через focus_set и передай его явно.",
            suggestions=_missing_target_suggestions(manager, want="TASK-"),
        )
    err = validate_task_id(task_id)
    if err:
        return error_response(
            "define",
            "INVALID_TASK",
            err,
            recovery="Проверь id через context(include_all=true).",
            suggestions=_missing_target_suggestions(manager, want="TASK-"),
        )
    task_id = str(task_id)

    title = data.get("title")
    success_criteria = data.get("success_criteria")
    tests = data.get("tests")
    blockers = data.get("blockers")

    if title is None and success_criteria is None and tests is None and blockers is None:
        return error_response("define", "NO_FIELDS", "Нечего обновлять: укажи title/success_criteria/tests/blockers")

    task = manager.load_task(task_id, skip_sync=True)
    if not task:
        return error_response(
            "define",
            "NOT_FOUND",
            f"Не найдено: {task_id}",
            recovery="Проверь id через context(include_all=true).",
            suggestions=_missing_target_suggestions(manager, want="TASK-"),
            result={"task": task_id},
        )
    if getattr(task, "kind", "task") != "task":
        return error_response("define", "NOT_A_TASK", "define применим только к заданиям (TASK-###)")

    path, path_err = _resolve_step_path(manager, task, data)
    if path_err:
        code, message = path_err
        return error_response(
            "define",
            code,
            message,
            recovery="Возьми корректный path/step_id через radar/mirror.",
            suggestions=_path_help_suggestions(task_id),
        )

    try:
        sc_list = _normalize_str_list(success_criteria) if success_criteria is not None else None
        tests_list = _normalize_str_list(tests) if tests is not None else None
        blockers_list = _normalize_str_list(blockers) if blockers is not None else None
    except Exception:
        return error_response("define", "INVALID_FIELDS", "success_criteria/tests/blockers должны быть массивами строк")

    ok, code, updated_step = manager.update_step_fields(
        task_id,
        path=path,
        title=title if title is not None else None,
        criteria=sc_list,
        tests=tests_list,
        blockers=blockers_list,
        domain=task.domain,
    )
    if not ok:
        mapping = {
            "not_found": ("NOT_FOUND", f"Не найдено: {task_id}"),
            "path": ("PATH_NOT_FOUND", f"Шаг path={path} не найден"),
            "missing_title": ("MISSING_TITLE", "title обязателен"),
            "missing_criteria": ("MISSING_CRITERIA", "success_criteria обязателен"),
        }
        err_code, msg = mapping.get(code or "", ("FAILED", code or "Не удалось обновить"))
        return error_response("define", err_code, msg, result={"task": task_id, "path": path})

    updated = manager.load_task(task_id, task.domain, skip_sync=True)
    return AIResponse(
        success=True,
        intent="define",
        result={
            "task_id": task_id,
            "path": path,
            "updated": step_to_dict(updated_step, path=path, compact=False) if updated_step else None,
            "task": task_to_dict(updated or task, include_steps=True, compact=False),
        },
        context={"task_id": task_id},
    )


def handle_verify(manager: TaskManager, data: Dict[str, Any]) -> AIResponse:
    task_id = data.get("task")
    if not task_id:
        return error_response(
            "verify",
            "MISSING_TASK",
            "task обязателен",
            recovery="Передай task=TASK-### (или task=PLAN-### с kind=task_detail) либо установи focus через focus_set и передай его явно.",
            suggestions=_missing_target_suggestions(manager, want=["TASK-", "PLAN-"]),
        )
    err = validate_task_id(task_id)
    if err:
        return error_response(
            "verify",
            "INVALID_TASK",
            err,
            recovery="Проверь id через context(include_all=true).",
            suggestions=_missing_target_suggestions(manager, want=["TASK-", "PLAN-"]),
        )
    task_id = str(task_id)

    checkpoints = data.get("checkpoints") or {}
    if not isinstance(checkpoints, dict):
        return error_response("verify", "INVALID_CHECKPOINTS", "checkpoints должен быть объектом")

    allowed = {"criteria", "tests"}
    keys = set(checkpoints.keys())
    if not keys or not keys.issubset(allowed):
        return error_response("verify", "INVALID_CHECKPOINTS", "Допустимо: checkpoints.criteria / checkpoints.tests")

    task = manager.load_task(task_id, skip_sync=True)
    if not task:
        return error_response(
            "verify",
            "NOT_FOUND",
            f"Не найдено: {task_id}",
            recovery="Проверь id через context(include_all=true).",
            suggestions=_missing_target_suggestions(manager, want="PLAN-" if task_id.startswith("PLAN-") else "TASK-"),
            result={"task": task_id},
        )
    kind = str(data.get("kind", "") or "").strip().lower()
    if not kind:
        kind = "step"
    if kind not in {"step", "task", "plan", "task_detail"}:
        return error_response("verify", "INVALID_KIND", "kind должен быть: step|task|plan|task_detail")
    if kind != "task_detail" and getattr(task, "kind", "task") != "task":
        return error_response("verify", "NOT_A_TASK", "verify path применим только к заданиям (TASK-###)")

    path = None
    if kind == "step":
        path, path_err = _resolve_step_path(manager, task, data)
        if path_err:
            code, message = path_err
            return error_response(
                "verify",
                code,
                message,
                recovery="Возьми корректный path/step_id через radar/mirror.",
                suggestions=_path_help_suggestions(task_id),
            )
    elif kind == "task":
        path, path_err = _resolve_task_path(manager, task, data)
        if path_err:
            code, message = path_err
            return error_response(
                "verify",
                code,
                message,
                recovery="Возьми корректный task path/task_node_id через mirror/radar.",
                suggestions=_path_help_suggestions(task_id),
            )
    elif kind == "plan":
        path, path_err = _resolve_step_path(manager, task, data)
        if path_err:
            code, message = path_err
            return error_response(
                "verify",
                code,
                message,
                recovery="Возьми корректный path/step_id через radar/mirror.",
                suggestions=_path_help_suggestions(task_id),
            )

    for name in ("criteria", "tests"):
        if name not in checkpoints:
            continue
        item = checkpoints.get(name) or {}
        if not isinstance(item, dict):
            return error_response("verify", "INVALID_CHECKPOINTS", f"checkpoints.{name} должен быть объектом")
        confirmed = bool(item.get("confirmed", False))
        note = str(item.get("note", "") or "").strip()
        ok, msg = manager.update_checkpoint(
            task_id,
            kind=kind,
            checkpoint=name,
            value=confirmed,
            note=note,
            domain=task.domain,
            path=path,
        )
        if not ok:
            mapping = {
                "not_found": "NOT_FOUND",
                "path": "PATH_NOT_FOUND",
                "index": "PATH_NOT_FOUND",
                "unknown_checkpoint": "INVALID_CHECKPOINT",
                "unknown_target": "INVALID_KIND",
            }
            return error_response("verify", mapping.get(msg or "", "FAILED"), msg or "Не удалось подтвердить")

    checks_raw = data.get("checks") or data.get("verification_checks")
    attachments_raw = data.get("attachments")
    verification_outcome = data.get("verification_outcome")
    any_confirmed = any(
        bool((checkpoints.get(name) or {}).get("confirmed", False))
        for name in ("criteria", "tests")
        if name in checkpoints
    )

    updated = manager.load_task(task_id, task.domain, skip_sync=True)
    st = None
    if kind == "step" and path:
        st, _, _ = _find_step_by_path((updated or task).steps, path)
    if (checks_raw is not None or attachments_raw is not None or verification_outcome is not None) and kind != "step":
        return error_response("verify", "INVALID_TARGET", "checks/attachments доступны только для шагов")
    if st and kind == "step":
        def _extend_unique_checks(items: List[VerificationCheck]) -> int:
            existing = {str(getattr(x, "digest", "") or "").strip() for x in (st.verification_checks or []) if getattr(x, "digest", "")}
            added = 0
            for check in items:
                digest = str(getattr(check, "digest", "") or "").strip()
                if digest and digest in existing:
                    continue
                st.verification_checks.append(check)
                added += 1
                if digest:
                    existing.add(digest)
            return added

        def _extend_unique_attachments(items: List[Attachment]) -> int:
            existing = {str(getattr(x, "digest", "") or "").strip() for x in (st.attachments or []) if getattr(x, "digest", "")}
            added = 0
            for att in items:
                digest = str(getattr(att, "digest", "") or "").strip()
                if digest and digest in existing:
                    continue
                st.attachments.append(att)
                added += 1
                if digest:
                    existing.add(digest)
            return added

        needs_save = False
        if checks_raw is not None:
            if not isinstance(checks_raw, list):
                return error_response("verify", "INVALID_CHECKS", "checks должен быть массивом")
            try:
                parsed_checks = [VerificationCheck.from_dict(c) for c in checks_raw if isinstance(c, dict)]
            except Exception:
                return error_response("verify", "INVALID_CHECKS", "checks содержит некорректные элементы")
            if _extend_unique_checks(parsed_checks):
                needs_save = True
        if attachments_raw is not None:
            if not isinstance(attachments_raw, list):
                return error_response("verify", "INVALID_ATTACHMENTS", "attachments должен быть массивом")
            try:
                parsed_attachments = [Attachment.from_dict(a) for a in attachments_raw if isinstance(a, dict)]
            except Exception:
                return error_response("verify", "INVALID_ATTACHMENTS", "attachments содержит некорректные элементы")
            if _extend_unique_attachments(parsed_attachments):
                needs_save = True
        if verification_outcome is not None:
            st.verification_outcome = str(verification_outcome or "").strip()
            needs_save = True

        # Best-effort: auto-evidence for verified checkpoints (CI + git).
        if any_confirmed:
            try:
                auto_checks = collect_auto_verification_checks(resolve_project_root())
                if auto_checks and _extend_unique_checks(list(auto_checks)):
                    needs_save = True
            except Exception:
                pass

        if needs_save and updated:
            manager.save_task(updated, skip_sync=True)
    payload_key = "plan" if getattr(updated or task, "kind", "task") == "plan" else "task"
    return AIResponse(
        success=True,
        intent="verify",
        result={
            "task_id": task_id,
            "path": path,
            "step": step_to_dict(st, path=path, compact=False) if st else None,
            payload_key: plan_to_dict(updated or task, compact=False)
            if payload_key == "plan"
            else task_to_dict(updated or task, include_steps=True, compact=False),
        },
        context={"task_id": task_id},
    )


def handle_progress(manager: TaskManager, data: Dict[str, Any]) -> AIResponse:
    task_id = data.get("task")
    if not task_id:
        return error_response(
            "progress",
            "MISSING_TASK",
            "task обязателен",
            recovery="Передай task=TASK-### или установи focus через focus_set и передай его явно.",
            suggestions=_missing_target_suggestions(manager, want="TASK-"),
        )
    err = validate_task_id(task_id)
    if err:
        return error_response(
            "progress",
            "INVALID_TASK",
            err,
            recovery="Проверь id через context(include_all=true).",
            suggestions=_missing_target_suggestions(manager, want="TASK-"),
        )
    task_id = str(task_id)

    completed = bool(data.get("completed", False))
    force = bool(data.get("force", False))
    override_reason = str(data.get("override_reason", "") or "").strip()
    if force and not override_reason:
        return error_response("progress", "MISSING_OVERRIDE_REASON", "override_reason обязателен при force=true")

    task = manager.load_task(task_id, skip_sync=True)
    if not task:
        return error_response(
            "progress",
            "NOT_FOUND",
            f"Не найдено: {task_id}",
            recovery="Проверь id через context(include_all=true).",
            suggestions=_missing_target_suggestions(manager, want="TASK-"),
            result={"task": task_id},
        )
    if getattr(task, "kind", "task") != "task":
        return error_response("progress", "NOT_A_TASK", "progress применим только к заданиям (TASK-###)")

    path, path_err = _resolve_step_path(manager, task, data)
    if path_err:
        code, message = path_err
        return error_response(
            "progress",
            code,
            message,
            recovery="Возьми корректный path/step_id через radar/mirror.",
            suggestions=_path_help_suggestions(task_id),
        )

    ok, msg = manager.set_step_completed(task_id, 0, completed, task.domain, path=path, force=force)
    if not ok:
        mapping = {"not_found": "NOT_FOUND", "index": "PATH_NOT_FOUND"}
        code = mapping.get(msg or "", "FAILED")
        return error_response("progress", code, msg or "Не удалось обновить completed", result={"task": task_id, "path": path})

    updated = manager.load_task(task_id, task.domain, skip_sync=True)
    st, _, _ = _find_step_by_path((updated or task).steps, path)
    if force and override_reason and updated:
        try:
            updated.events.append(StepEvent.override("progress", override_reason, target=f"step:{path}"))
            manager.save_task(updated, skip_sync=True)
        except Exception:
            pass
    return AIResponse(
        success=True,
        intent="progress",
        result={
            "task_id": task_id,
            "path": path,
            "completed": completed,
            "step": step_to_dict(st, path=path, compact=False) if st else None,
            "task": task_to_dict(updated or task, include_steps=True, compact=False),
        },
        context={"task_id": task_id},
    )


def handle_done(manager: TaskManager, data: Dict[str, Any]) -> AIResponse:
    task_id = data.get("task")
    if not task_id:
        return error_response(
            "done",
            "MISSING_TASK",
            "task обязателен",
            recovery="Передай task=TASK-### или установи focus через focus_set и передай его явно.",
            suggestions=_missing_target_suggestions(manager, want="TASK-"),
        )
    err = validate_task_id(task_id)
    if err:
        return error_response(
            "done",
            "INVALID_TASK",
            err,
            recovery="Проверь id через context(include_all=true).",
            suggestions=_missing_target_suggestions(manager, want="TASK-"),
        )
    task_id = str(task_id)

    force = bool(data.get("force", False))
    override_reason = str(data.get("override_reason", "") or "").strip()
    if force and not override_reason:
        return error_response("done", "MISSING_OVERRIDE_REASON", "override_reason обязателен при force=true")
    note = str(data.get("note", "") or "").strip()

    task = manager.load_task(task_id, skip_sync=True)
    if not task:
        return error_response(
            "done",
            "NOT_FOUND",
            f"Не найдено: {task_id}",
            recovery="Проверь id через context(include_all=true).",
            suggestions=_missing_target_suggestions(manager, want="TASK-"),
            result={"task": task_id},
        )
    if getattr(task, "kind", "task") != "task":
        return error_response("done", "NOT_A_TASK", "done применим только к заданиям (TASK-###)")

    path, path_err = _resolve_step_path(manager, task, data)
    if path_err:
        code, message = path_err
        return error_response(
            "done",
            code,
            message,
            recovery="Возьми корректный path/step_id через radar/mirror.",
            suggestions=_path_help_suggestions(task_id),
        )

    if note:
        manager.add_step_progress_note(task_id, path=path, note=note, domain=task.domain)
    ok, msg = manager.set_step_completed(task_id, 0, True, task.domain, path=path, force=force)
    if not ok:
        mapping = {"not_found": "NOT_FOUND", "index": "PATH_NOT_FOUND"}
        return error_response("done", mapping.get(msg or "", "FAILED"), msg or "Не удалось завершить шаг")

    updated = manager.load_task(task_id, task.domain, skip_sync=True)
    st, _, _ = _find_step_by_path((updated or task).steps, path)
    if force and override_reason and updated:
        try:
            updated.events.append(StepEvent.override("done", override_reason, target=f"step:{path}"))
            manager.save_task(updated, skip_sync=True)
        except Exception:
            pass
    return AIResponse(
        success=True,
        intent="done",
        result={
            "task_id": task_id,
            "path": path,
            "task": task_to_dict(updated or task, include_steps=True, compact=False),
            "step": step_to_dict(st, path=path, compact=False) if st else None,
        },
        context={"task_id": task_id},
    )


def handle_edit(manager: TaskManager, data: Dict[str, Any]) -> AIResponse:
    task_id = data.get("task")
    if not task_id:
        return error_response(
            "edit",
            "MISSING_TASK",
            "task обязателен",
            recovery="Передай task=TASK-###|PLAN-### или установи focus через focus_set и передай его явно.",
            suggestions=_missing_target_suggestions(manager, want=["TASK-", "PLAN-"]),
        )
    err = validate_task_id(task_id)
    if err:
        return error_response(
            "edit",
            "INVALID_TASK",
            err,
            recovery="Проверь id через context(include_all=true).",
            suggestions=_missing_target_suggestions(manager, want=["TASK-", "PLAN-"]),
        )
    task_id = str(task_id)
    task = manager.load_task(task_id, skip_sync=True)
    if not task:
        return error_response(
            "edit",
            "NOT_FOUND",
            f"Не найдено: {task_id}",
            recovery="Проверь id через context(include_all=true).",
            suggestions=_missing_target_suggestions(manager, want="PLAN-" if task_id.startswith("PLAN-") else "TASK-"),
            result={"task": task_id},
        )

    updated_fields: List[str] = []
    old_domain = str(getattr(task, "domain", "") or "")

    def _maybe_set(field: str, value: Any, transform: Callable[[Any], Any] = lambda x: x) -> None:
        nonlocal updated_fields
        if value is None:
            return
        setattr(task, field, transform(value))
        updated_fields.append(field)

    _maybe_set("description", data.get("description"), lambda v: str(v or ""))
    _maybe_set("context", data.get("context"), lambda v: str(v or ""))
    _maybe_set("priority", data.get("priority"), lambda v: str(v or task.priority))
    if data.get("tags") is not None:
        try:
            task.tags = _normalize_str_list(data.get("tags"))
            updated_fields.append("tags")
        except Exception:
            return error_response("edit", "INVALID_TAGS", "tags должен быть массивом строк")
    if data.get("depends_on") is not None:
        try:
            raw_deps = _normalize_str_list(data.get("depends_on"))
        except Exception:
            return error_response("edit", "INVALID_DEPENDS_ON", "depends_on должен быть массивом строк")
        # Validate dependencies (existence + cycle detection).
        try:
            from core import validate_dependencies, build_dependency_graph
            from core.desktop.devtools.application.context import normalize_task_id
        except Exception:
            return error_response("edit", "FAILED", "Не удалось загрузить валидатор зависимостей")
        normalized = [normalize_task_id(d) for d in raw_deps]
        if any(not d.startswith("TASK-") for d in normalized):
            return error_response("edit", "INVALID_DEPENDENCIES", "depends_on должен содержать только TASK-###")
        all_items = manager.list_all_tasks(skip_sync=True)
        existing_ids = {t.id for t in all_items if getattr(t, "kind", "task") == "task"}
        dep_graph = build_dependency_graph(
            [
                (t.id, list(getattr(t, "depends_on", []) or []))
                for t in all_items
                if getattr(t, "kind", "task") == "task"
            ]
        )
        errors, cycle = validate_dependencies(task.id, normalized, existing_ids, dep_graph)
        if errors:
            return error_response("edit", "INVALID_DEPENDENCIES", "; ".join(str(e) for e in errors))
        if cycle:
            return error_response("edit", "CIRCULAR_DEPENDENCY", " → ".join(cycle))
        task.depends_on = normalized
        updated_fields.append("depends_on")

    new_domain = data.get("new_domain")
    if new_domain is not None:
        task.domain = str(new_domain or "").strip()
        updated_fields.append("domain")

    if not updated_fields:
        return error_response("edit", "NO_FIELDS", "Нечего обновлять")

    # Persist updates, and if domain changed: move (write to new domain + delete old file).
    if "domain" in updated_fields and task.domain != old_domain:
        manager.save_task(task, skip_sync=True)
        # Ensure this is a move, not copy: remove the old file location.
        manager.repo.delete(task.id, old_domain)
    else:
        manager.save_task(task, skip_sync=True)

    reloaded = manager.load_task(task.id, task.domain, skip_sync=True) or task
    snapshot = plan_to_dict(reloaded, compact=False) if getattr(reloaded, "kind", "task") == "plan" else task_to_dict(reloaded, include_steps=True, compact=False)
    key = "plan" if getattr(reloaded, "kind", "task") == "plan" else "task"
    return AIResponse(
        success=True,
        intent="edit",
        result={key: snapshot, "updated_fields": sorted(set(updated_fields))},
        context={"task_id": task_id},
    )


_PATCH_OPS = {"set", "unset", "append", "remove"}

_CONTRACT_DATA_FIELDS: Dict[str, str] = {
    "goal": "str",
    "constraints": "str_list",
    "assumptions": "str_list",
    "non_goals": "str_list",
    "done": "str_list",
    "risks": "str_list",
    "checks": "str_list",
}

_PATCHABLE_TASK_DETAIL_FIELDS: Dict[str, str] = {
    "title": "str",
    "description": "str",
    "context": "str",
    "priority": "priority",
    "tags": "str_list",
    "blocked": "bool",
    "blockers": "str_list",
    "success_criteria": "str_list",
    "tests": "str_list",
    "dependencies": "str_list",
    "next_steps": "str_list",
    "problems": "str_list",
    "risks": "str_list",
    "depends_on": "str_list",
    "contract": "str",
    # Plan-only fields (validated by kind at runtime)
    "plan_doc": "str",
    "plan_steps": "str_list",
    "plan_current": "int",
}

_PATCHABLE_STEP_FIELDS: Dict[str, str] = {
    "title": "str",
    "success_criteria": "str_list",
    "tests": "str_list",
    "blockers": "str_list",
}

_PATCHABLE_TASK_NODE_FIELDS: Dict[str, str] = {
    "title": "str",
    "status": "status",
    "priority": "priority",
    "status_manual": "bool",
    "description": "str",
    "context": "str",
    "success_criteria": "str_list",
    "tests": "str_list",
    "dependencies": "str_list",
    "next_steps": "str_list",
    "problems": "str_list",
    "risks": "str_list",
    "blocked": "bool",
    "blockers": "str_list",
}


def _normalize_patch_items(value: Any, *, field: str) -> Tuple[Optional[List[str]], Optional[str]]:
    if value is None:
        return [], None
    if isinstance(value, str):
        return _normalize_str_list([value]), None
    if isinstance(value, list):
        try:
            return _normalize_str_list(value), None
        except Exception:
            return None, f"{field} должен быть массивом строк"
    return None, f"{field} должен быть строкой или массивом строк"


def _infer_patch_kind(data: Dict[str, Any]) -> str:
    raw = str(data.get("kind", "") or "").strip().lower()
    if raw:
        return raw
    # Infer from addressing hints (explicit, no hidden state).
    if data.get("task_node_id") is not None:
        return "task"
    path = str(data.get("path", "") or "").strip()
    if path and path.split(".")[-1].startswith("t:"):
        return "task"
    if data.get("step_id") is not None or path:
        return "step"
    return "task_detail"


def _apply_patch_list_field(current: List[str], op: str, value: Any, *, field: str) -> Tuple[Optional[List[str]], Optional[str]]:
    if op == "unset":
        return [], None
    if op == "set":
        if not isinstance(value, list):
            return None, f"{field} должен быть массивом строк для op=set"
        try:
            return _normalize_str_list(value), None
        except Exception:
            return None, f"{field} должен быть массивом строк"
    items, err = _normalize_patch_items(value, field=field)
    if err:
        return None, err
    items = list(items or [])
    if op == "append":
        return _dedupe_strs(list(current or []) + items), None
    if op == "remove":
        remove_set = set(items)
        return [v for v in list(current or []) if v not in remove_set], None
    return None, f"Неизвестный op для списка: {op}"


def _apply_patch_scalar_field(value_type: str, op: str, value: Any, *, field: str) -> Tuple[Optional[Any], Optional[str]]:
    if value_type == "str":
        if op == "unset":
            return "", None
        if not isinstance(value, str):
            return None, f"{field} должен быть строкой"
        return str(value or ""), None
    if value_type == "bool":
        if op == "unset":
            return False, None
        if not isinstance(value, bool):
            return None, f"{field} должен быть boolean"
        return bool(value), None
    if value_type == "int":
        if op == "unset":
            return 0, None
        if isinstance(value, bool):
            return None, f"{field} должен быть числом"
        try:
            return int(value), None
        except Exception:
            return None, f"{field} должен быть числом"
    if value_type == "priority":
        if op == "unset":
            return "MEDIUM", None
        if not isinstance(value, str):
            return None, f"{field} должен быть строкой"
        up = str(value or "").strip().upper()
        if up not in {"LOW", "MEDIUM", "HIGH"}:
            return None, f"{field} должен быть LOW|MEDIUM|HIGH"
        return up, None
    if value_type == "status":
        if op == "unset":
            return "TODO", None
        if not isinstance(value, str):
            return None, f"{field} должен быть строкой"
        up = str(value or "").strip().upper()
        if up not in {"TODO", "ACTIVE", "DONE"}:
            return None, f"{field} должен быть TODO|ACTIVE|DONE"
        return up, None
    return None, f"Неизвестный тип поля: {value_type}"


def handle_patch(manager: TaskManager, data: Dict[str, Any]) -> AIResponse:
    task_id = data.get("task") or data.get("plan")
    if not task_id:
        return error_response(
            "patch",
            "MISSING_TASK",
            "task обязателен",
            recovery="Передай task=TASK-###|PLAN-### (явная адресация). Чтобы выбрать id — вызови context(include_all=true).",
            suggestions=_missing_target_suggestions(manager, want=["TASK-", "PLAN-"]),
        )
    err = validate_task_id(task_id)
    if err:
        return error_response(
            "patch",
            "INVALID_TASK",
            err,
            recovery="Проверь id через context(include_all=true).",
            suggestions=_missing_target_suggestions(manager, want=["TASK-", "PLAN-"]),
        )
    task_id = str(task_id)

    ops = data.get("ops")
    if ops is None:
        ops = data.get("operations")
    if ops is None:
        return error_response("patch", "MISSING_OPS", "ops обязателен")
    err = validate_array(ops, "ops")
    if err:
        return error_response("patch", "INVALID_OPS", err)

    kind = _infer_patch_kind(data)
    if kind not in {"task_detail", "step", "task"}:
        return error_response("patch", "INVALID_KIND", "kind должен быть: task_detail|step|task")

    base = manager.load_task(task_id, skip_sync=True)
    if not base:
        return error_response(
            "patch",
            "NOT_FOUND",
            f"Не найдено: {task_id}",
            recovery="Проверь id через context(include_all=true).",
            suggestions=_missing_target_suggestions(manager, want="PLAN-" if task_id.startswith("PLAN-") else "TASK-"),
            result={"task": task_id},
        )

    dry_run = bool(data.get("dry_run", False))
    detail = copy.deepcopy(base) if dry_run else base

    if kind != "task_detail" and getattr(detail, "kind", "task") != "task":
        return error_response("patch", "NOT_A_TASK", "patch(kind=step|task) применим только к заданиям (TASK-###)")

    updated_fields: List[str] = []
    contract_touched = False
    path: Optional[str] = None

    def apply_ops(target: Any, allow: Dict[str, str]) -> Optional[AIResponse]:
        nonlocal updated_fields, contract_touched
        for idx, raw_op in enumerate(list(ops or [])):
            if not isinstance(raw_op, dict):
                return error_response("patch", "INVALID_OPS", f"ops[{idx}] должен быть объектом")
            op = str(raw_op.get("op", "") or "").strip().lower()
            if op not in _PATCH_OPS:
                return error_response("patch", "INVALID_OP", f"ops[{idx}].op должен быть: set|unset|append|remove", result={"op": raw_op})
            field = str(raw_op.get("field", "") or "").strip()
            if not field:
                return error_response("patch", "MISSING_FIELD", f"ops[{idx}].field обязателен", result={"op": raw_op})
            value_present = "value" in raw_op
            value = raw_op.get("value")
            if op != "unset" and not value_present:
                return error_response("patch", "MISSING_VALUE", f"ops[{idx}].value обязателен для op={op}", result={"op": raw_op})

            if field.startswith("contract_data."):
                if not isinstance(target, TaskDetail):
                    return error_response("patch", "INVALID_FIELD", f"{field} допустим только для kind=task_detail")
                parts = field.split(".", 2)
                if len(parts) != 2 or not parts[1]:
                    return error_response("patch", "INVALID_FIELD", f"Неверный field: {field} (ожидается contract_data.<key>)")
                key = parts[1]
                value_type = _CONTRACT_DATA_FIELDS.get(key)
                if not value_type:
                    return error_response("patch", "FORBIDDEN_FIELD", f"contract_data.{key} не поддерживается", result={"field": field})
                contract_touched = True
                cd = dict(getattr(target, "contract_data", {}) or {})
                if op == "unset":
                    if key in cd:
                        cd.pop(key, None)
                        updated_fields.append(field)
                    setattr(target, "contract_data", cd)
                    continue
                if value_type == "str_list":
                    current = cd.get(key, []) if isinstance(cd.get(key, []), list) else []
                    new_list, list_err = _apply_patch_list_field(list(current or []), op, value, field=field)
                    if list_err:
                        return error_response("patch", "INVALID_VALUE", list_err, result={"field": field, "op": op})
                    cd[key] = list(new_list or [])
                    updated_fields.append(field)
                    setattr(target, "contract_data", cd)
                    continue
                # value_type == str
                if op in {"append", "remove"}:
                    return error_response("patch", "INVALID_OP", f"{field} не поддерживает op={op}")
                new_val, val_err = _apply_patch_scalar_field("str", op, value, field=field)
                if val_err:
                    return error_response("patch", "INVALID_VALUE", val_err, result={"field": field, "op": op})
                cd[key] = str(new_val or "")
                updated_fields.append(field)
                setattr(target, "contract_data", cd)
                continue

            value_type = allow.get(field)
            if not value_type:
                return error_response("patch", "FORBIDDEN_FIELD", f"Поле не поддерживается: {field}", result={"field": field})

            if "." in field:
                return error_response("patch", "INVALID_FIELD", f"Неверный field: {field} (поддерживаются только contract_data.<key> и top-level поля)")

            if value_type == "str_list":
                current = list(getattr(target, field, []) or [])
                new_list, list_err = _apply_patch_list_field(current, op, value, field=field)
                if list_err:
                    return error_response("patch", "INVALID_VALUE", list_err, result={"field": field, "op": op})
                setattr(target, field, list(new_list or []))
                updated_fields.append(field)
                continue

            if op in {"append", "remove"}:
                return error_response("patch", "INVALID_OP", f"{field} не поддерживает op={op}")
            new_val, val_err = _apply_patch_scalar_field(value_type, op, value, field=field)
            if val_err:
                return error_response("patch", "INVALID_VALUE", val_err, result={"field": field, "op": op})
            setattr(target, field, new_val)
            updated_fields.append(field)

        return None

    if kind == "task_detail":
        # Plan-only fields guard.
        if getattr(detail, "kind", "task") != "plan":
            plan_only = {"plan_doc", "plan_steps", "plan_current"}
            if any(f in plan_only for f in (str(o.get("field", "") or "") for o in list(ops or []) if isinstance(o, dict))):
                return error_response("patch", "NOT_A_PLAN", "plan_* поля применимы только к планам (PLAN-###)")
        err_resp = apply_ops(detail, _PATCHABLE_TASK_DETAIL_FIELDS)
        if err_resp:
            return err_resp

        # Checkpoint semantics for root criteria/tests.
        if "success_criteria" in updated_fields:
            detail.criteria_confirmed = False
            detail.criteria_auto_confirmed = False
        if "tests" in updated_fields:
            tests_list = list(getattr(detail, "tests", []) or [])
            detail.tests_confirmed = False
            detail.tests_auto_confirmed = not tests_list
        if any(f.startswith("contract_data.") or f == "contract" for f in updated_fields):
            contract_touched = True

        # Keep status consistent (esp. plans: plan_current/steps).
        try:
            detail.update_status_from_progress()
        except Exception:
            pass

        if dry_run:
            key = "plan" if getattr(detail, "kind", "task") == "plan" else "task"
            snapshot = plan_to_dict(detail, compact=False) if key == "plan" else task_to_dict(detail, include_steps=True, compact=False)
            return AIResponse(
                success=True,
                intent="patch",
                result={"dry_run": True, "would_execute": True, "updated_fields": sorted(set(updated_fields)), key: snapshot},
                context={"task_id": task_id},
            )

        if contract_touched and getattr(detail, "kind", "task") == "plan":
            try:
                append_contract_version_if_changed(detail, note="patch")
            except Exception:
                pass
        manager.save_task(detail, skip_sync=True)
        reloaded = manager.load_task(task_id, getattr(detail, "domain", ""), skip_sync=True) or detail
        key = "plan" if getattr(reloaded, "kind", "task") == "plan" else "task"
        snapshot = plan_to_dict(reloaded, compact=False) if key == "plan" else task_to_dict(reloaded, include_steps=True, compact=False)
        return AIResponse(
            success=True,
            intent="patch",
            result={"task_id": task_id, "kind": "task_detail", "updated_fields": sorted(set(updated_fields)), key: snapshot},
            context={"task_id": task_id},
        )

    if kind == "step":
        path, path_err = _resolve_step_path(manager, detail, data)
        if path_err:
            code, message = path_err
            return error_response(
                "patch",
                code,
                message,
                recovery="Возьми корректный path/step_id через radar/mirror.",
                suggestions=_path_help_suggestions(task_id),
            )
        step, _, _ = _find_step_by_path(list(getattr(detail, "steps", []) or []), path)
        if not step:
            return error_response(
                "patch",
                "PATH_NOT_FOUND",
                f"Шаг path={path} не найден",
                recovery="Возьми корректный path/step_id через radar/mirror.",
                suggestions=_path_help_suggestions(task_id),
            )
        err_resp = apply_ops(step, _PATCHABLE_STEP_FIELDS)
        if err_resp:
            return err_resp
        # Any step field change invalidates completion and checkpoints for criteria/tests.
        if updated_fields:
            step.completed = False
            step.completed_at = None
        if "success_criteria" in updated_fields:
            step.criteria_confirmed = False
            step.criteria_auto_confirmed = False
        if "tests" in updated_fields:
            tests_list = list(getattr(step, "tests", []) or [])
            step.tests_confirmed = False
            step.tests_auto_confirmed = not tests_list
        try:
            detail.update_status_from_progress()
        except Exception:
            pass

        if dry_run:
            return AIResponse(
                success=True,
                intent="patch",
                result={
                    "dry_run": True,
                    "would_execute": True,
                    "task_id": task_id,
                    "kind": "step",
                    "path": path,
                    "updated_fields": sorted(set(updated_fields)),
                    "step": step_to_dict(step, path=path, compact=False),
                    "task": task_to_dict(detail, include_steps=True, compact=False),
                },
                context={"task_id": task_id},
            )

        manager.save_task(detail, skip_sync=True)
        reloaded = manager.load_task(task_id, getattr(detail, "domain", ""), skip_sync=True) or detail
        st, _, _ = _find_step_by_path(list(getattr(reloaded, "steps", []) or []), path)
        return AIResponse(
            success=True,
            intent="patch",
            result={
                "task_id": task_id,
                "kind": "step",
                "path": path,
                "updated_fields": sorted(set(updated_fields)),
                "step": step_to_dict(st, path=path, compact=False) if st else None,
                "task": task_to_dict(reloaded, include_steps=True, compact=False),
            },
            context={"task_id": task_id},
        )

    # kind == task (task node)
    path, path_err = _resolve_task_path(manager, detail, data)
    if path_err:
        code, message = path_err
        return error_response(
            "patch",
            code,
            message,
            recovery="Возьми корректный task path/task_node_id через mirror/radar.",
            suggestions=_path_help_suggestions(task_id),
        )
    node, _, _ = _find_task_by_path(list(getattr(detail, "steps", []) or []), path)
    if not node:
        return error_response(
            "patch",
            "PATH_NOT_FOUND",
            f"Задание path={path} не найдено",
            recovery="Возьми корректный task path/task_node_id через mirror/radar.",
            suggestions=_path_help_suggestions(task_id),
        )
    err_resp = apply_ops(node, _PATCHABLE_TASK_NODE_FIELDS)
    if err_resp:
        return err_resp
    if "success_criteria" in updated_fields:
        node.criteria_confirmed = False
        node.criteria_auto_confirmed = False
    if "tests" in updated_fields:
        tests_list = list(getattr(node, "tests", []) or [])
        node.tests_confirmed = False
        node.tests_auto_confirmed = not tests_list
    if "status" in updated_fields and "status_manual" not in updated_fields:
        node.status_manual = True
    try:
        detail.update_status_from_progress()
    except Exception:
        pass

    if dry_run:
        return AIResponse(
            success=True,
            intent="patch",
            result={
                "dry_run": True,
                "would_execute": True,
                "task_id": task_id,
                "kind": "task",
                "path": path,
                "updated_fields": sorted(set(updated_fields)),
                "task_node": task_node_to_dict(node, path=path, compact=False),
                "task": task_to_dict(detail, include_steps=True, compact=False),
            },
            context={"task_id": task_id},
        )

    manager.save_task(detail, skip_sync=True)
    reloaded = manager.load_task(task_id, getattr(detail, "domain", ""), skip_sync=True) or detail
    patched, _, _ = _find_task_by_path(list(getattr(reloaded, "steps", []) or []), path)
    return AIResponse(
        success=True,
        intent="patch",
        result={
            "task_id": task_id,
            "kind": "task",
            "path": path,
            "updated_fields": sorted(set(updated_fields)),
            "task_node": task_node_to_dict(patched, path=path, compact=False) if patched else None,
            "task": task_to_dict(reloaded, include_steps=True, compact=False),
        },
        context={"task_id": task_id},
    )


def handle_note(manager: TaskManager, data: Dict[str, Any]) -> AIResponse:
    task_id = data.get("task")
    if not task_id:
        return error_response(
            "note",
            "MISSING_TASK",
            "task обязателен",
            recovery="Передай task=TASK-### или установи focus через focus_set и передай его явно.",
            suggestions=_missing_target_suggestions(manager, want="TASK-"),
        )
    err = validate_task_id(task_id)
    if err:
        return error_response(
            "note",
            "INVALID_TASK",
            err,
            recovery="Проверь id через context(include_all=true).",
            suggestions=_missing_target_suggestions(manager, want="TASK-"),
        )
    task_id = str(task_id)

    note = str(data.get("note", "") or "").strip()
    if not note:
        return error_response("note", "MISSING_NOTE", "note обязателен")

    task = manager.load_task(task_id, skip_sync=True)
    if not task:
        return error_response(
            "note",
            "NOT_FOUND",
            f"Не найдено: {task_id}",
            recovery="Проверь id через context(include_all=true).",
            suggestions=_missing_target_suggestions(manager, want="TASK-"),
            result={"task": task_id},
        )
    if getattr(task, "kind", "task") != "task":
        return error_response("note", "NOT_A_TASK", "note применим только к заданиям (TASK-###)")

    path, path_err = _resolve_step_path(manager, task, data)
    if path_err:
        code, message = path_err
        return error_response(
            "note",
            code,
            message,
            recovery="Возьми корректный path/step_id через radar/mirror.",
            suggestions=_path_help_suggestions(task_id),
        )

    ok, code, step = manager.add_step_progress_note(task_id, path=path, note=note, domain=task.domain)
    if not ok:
        mapping = {"not_found": "NOT_FOUND", "index": "PATH_NOT_FOUND", "missing_note": "MISSING_NOTE"}
        return error_response("note", mapping.get(code or "", "FAILED"), code or "Не удалось добавить note")
    return AIResponse(
        success=True,
        intent="note",
        result={
            "task_id": task_id,
            "path": path,
            "note": note,
            "total_notes": len(getattr(step, "progress_notes", []) or []) if step else 0,
            "computed_status": getattr(step, "computed_status", "pending") if step else "pending",
        },
        context={"task_id": task_id},
    )


def handle_block(manager: TaskManager, data: Dict[str, Any]) -> AIResponse:
    task_id = data.get("task")
    if not task_id:
        return error_response(
            "block",
            "MISSING_TASK",
            "task обязателен",
            recovery="Передай task=TASK-### или установи focus через focus_set и передай его явно.",
            suggestions=_missing_target_suggestions(manager, want="TASK-"),
        )
    err = validate_task_id(task_id)
    if err:
        return error_response(
            "block",
            "INVALID_TASK",
            err,
            recovery="Проверь id через context(include_all=true).",
            suggestions=_missing_target_suggestions(manager, want="TASK-"),
        )
    task_id = str(task_id)

    blocked = bool(data.get("blocked", True))
    reason = str(data.get("reason", "") or "").strip()
    task = manager.load_task(task_id, skip_sync=True)
    if not task:
        return error_response(
            "block",
            "NOT_FOUND",
            f"Не найдено: {task_id}",
            recovery="Проверь id через context(include_all=true).",
            suggestions=_missing_target_suggestions(manager, want="TASK-"),
            result={"task": task_id},
        )
    if getattr(task, "kind", "task") != "task":
        return error_response("block", "NOT_A_TASK", "block применим только к заданиям (TASK-###)")

    path, path_err = _resolve_step_path(manager, task, data)
    if path_err:
        code, message = path_err
        return error_response(
            "block",
            code,
            message,
            recovery="Возьми корректный path/step_id через radar/mirror.",
            suggestions=_path_help_suggestions(task_id),
        )

    ok, code, step = manager.set_step_blocked(task_id, path=path, blocked=blocked, reason=reason, domain=task.domain)
    if not ok:
        mapping = {"not_found": "NOT_FOUND", "index": "PATH_NOT_FOUND"}
        return error_response("block", mapping.get(code or "", "FAILED"), code or "Не удалось обновить blocked")
    return AIResponse(
        success=True,
        intent="block",
        result={
            "task_id": task_id,
            "path": path,
            "blocked": blocked,
            "reason": reason,
            "computed_status": getattr(step, "computed_status", "pending") if step else "pending",
        },
        context={"task_id": task_id},
    )


def handle_contract(manager: TaskManager, data: Dict[str, Any]) -> AIResponse:
    plan_id = data.get("plan") or data.get("task")
    if not plan_id:
        return error_response(
            "contract",
            "MISSING_PLAN",
            "plan обязателен",
            recovery="Передай plan=PLAN-### или установи focus на план через focus_set.",
            suggestions=_missing_target_suggestions(manager, want="PLAN-"),
        )
    err = validate_task_id(plan_id)
    if err:
        return error_response("contract", "INVALID_PLAN", err)
    plan_id = str(plan_id)
    plan = manager.load_task(plan_id, skip_sync=True)
    if not plan:
        return error_response(
            "contract",
            "NOT_FOUND",
            f"Не найдено: {plan_id}",
            recovery="Проверь plan через context(include_all=true) или создай новый plan.",
            suggestions=_missing_target_suggestions(manager, want="PLAN-"),
            result={"plan": plan_id},
        )
    if getattr(plan, "kind", "task") != "plan":
        return error_response("contract", "NOT_A_PLAN", "contract применим только к планам (PLAN-###)")

    if bool(data.get("clear", False)):
        plan.contract = ""
    if data.get("current") is not None:
        plan.contract = str(data.get("current") or "")
    contract_data = data.get("contract_data")
    if contract_data is not None:
        if not isinstance(contract_data, dict):
            return error_response("contract", "INVALID_CONTRACT_DATA", "contract_data должен быть объектом")
        plan.contract_data = dict(contract_data)
    append_contract_version_if_changed(plan, note="contract")
    manager.save_task(plan, skip_sync=True)
    return AIResponse(
        success=True,
        intent="contract",
        result={"plan": plan_to_dict(plan, compact=False)},
        context={"task_id": plan_id},
    )


def handle_plan(manager: TaskManager, data: Dict[str, Any]) -> AIResponse:
    plan_id = data.get("plan") or data.get("task")
    if not plan_id:
        return error_response(
            "plan",
            "MISSING_PLAN",
            "plan обязателен",
            recovery="Передай plan=PLAN-### или установи focus на план через focus_set.",
            suggestions=_missing_target_suggestions(manager, want="PLAN-"),
        )
    err = validate_task_id(plan_id)
    if err:
        return error_response("plan", "INVALID_PLAN", err)
    plan_id = str(plan_id)
    plan = manager.load_task(plan_id, skip_sync=True)
    if not plan:
        return error_response(
            "plan",
            "NOT_FOUND",
            f"Не найдено: {plan_id}",
            recovery="Проверь plan через context(include_all=true) или создай новый plan.",
            suggestions=_missing_target_suggestions(manager, want="PLAN-"),
            result={"plan": plan_id},
        )
    if getattr(plan, "kind", "task") != "plan":
        return error_response("plan", "NOT_A_PLAN", "plan intent применим только к планам (PLAN-###)")

    if data.get("doc") is not None:
        plan.plan_doc = str(data.get("doc") or "")
    if data.get("steps") is not None:
        try:
            plan.plan_steps = _normalize_str_list(data.get("steps"))
        except Exception:
            return error_response("plan", "INVALID_STEPS", "plan.steps должен быть массивом строк")
    if data.get("current") is not None:
        try:
            plan.plan_current = int(data.get("current") or 0)
        except Exception:
            return error_response("plan", "INVALID_CURRENT", "plan.current должен быть числом")

    if bool(data.get("advance", False)):
        plan.plan_current = int(getattr(plan, "plan_current", 0) or 0) + 1

    # Clamp current index.
    plan_steps = list(getattr(plan, "plan_steps", []) or [])
    if plan_steps:
        plan.plan_current = max(0, min(int(getattr(plan, "plan_current", 0) or 0), len(plan_steps)))
    else:
        plan.plan_current = 0
    plan.update_status_from_progress()
    manager.save_task(plan, skip_sync=True)
    return AIResponse(
        success=True,
        intent="plan",
        result={"plan": plan_to_dict(plan, compact=False)},
        context={"task_id": plan_id},
    )


def handle_mirror(manager: TaskManager, data: Dict[str, Any]) -> AIResponse:
    focus = data.get("task") or data.get("plan")
    if focus is None:
        last_id, _domain = get_last_task()
        focus = last_id
    if not focus:
        return error_response(
            "mirror",
            "MISSING_ID",
            "Не указан task/plan и нет focus",
            recovery="Передай task=TASK-###|plan=PLAN-### или установи focus через focus_set.",
            suggestions=_missing_target_suggestions(manager, want=["TASK-", "PLAN-"]),
        )
    err = validate_task_id(focus)
    if err:
        return error_response(
            "mirror",
            "INVALID_ID",
            err,
            recovery="Проверь id через context(include_all=true) или установи focus через focus_set.",
            suggestions=_missing_target_suggestions(manager, want=["TASK-", "PLAN-"]),
        )
    focus_id = str(focus)
    detail = manager.load_task(focus_id, skip_sync=True)
    if not detail:
        return error_response(
            "mirror",
            "NOT_FOUND",
            f"Не найдено: {focus_id}",
            recovery="Проверь id через context(include_all=true) или установи focus заново.",
            suggestions=_missing_target_suggestions(manager, want="PLAN-" if focus_id.startswith("PLAN-") else "TASK-"),
            result={"task": focus_id},
        )

    limit = data.get("limit")
    if limit is not None:
        try:
            limit = int(limit)
        except Exception:
            return error_response("mirror", "INVALID_LIMIT", "limit должен быть числом")
        if limit < 0:
            return error_response("mirror", "INVALID_LIMIT", "limit должен быть >= 0")

    path = data.get("path")
    kind = str(data.get("kind", "") or "").strip().lower()
    step_id = data.get("step_id")
    task_node_id = data.get("task_node_id")

    items: List[Dict[str, Any]] = []
    scope: Dict[str, Any] = {"task_id": focus_id, "kind": getattr(detail, "kind", "task")}

    if getattr(detail, "kind", "task") == "plan":
        all_details = manager.list_all_tasks(skip_sync=True)
        plan_tasks = [
            t
            for t in all_details
            if str(getattr(t, "parent", "") or "") == focus_id
            and getattr(t, "kind", "task") != "plan"
        ]
        plan_tasks = sorted(plan_tasks, key=lambda t: str(getattr(t, "id", "") or ""))
        items = _mirror_items_from_tasks(plan_tasks)
    else:
        target_kind = kind
        target_path = ""
        if task_node_id is not None or (path and str(path).split(".")[-1].startswith("t:")) or kind == "task":
            target_kind = "task"
            target_path, err_pair = _resolve_task_path(manager, detail, data, path_field="path")
            if err_pair:
                code, msg = err_pair
                return error_response(
                    "mirror",
                    code,
                    msg,
                    recovery="Вызови mirror без path/kind чтобы увидеть корневое дерево, или radar чтобы взять активный path.",
                    suggestions=_path_help_suggestions(focus_id),
                )
            task_node, _, _ = _find_task_by_path(list(getattr(detail, "steps", []) or []), target_path)
            if not task_node:
                return error_response(
                    "mirror",
                    "TASK_NODE_NOT_FOUND",
                    f"Задание не найдено: {target_path}",
                    recovery="Возьми корректный task_node_id/path через mirror без path или через radar.",
                    suggestions=_path_help_suggestions(focus_id),
                )
            scope.update({"path": target_path, "kind": "task"})
            items = _mirror_items_from_steps(list(getattr(task_node, "steps", []) or []), prefix=target_path)
        elif step_id is not None or path is not None or kind == "step":
            target_kind = "step"
            target_path, err_pair = _resolve_step_path(manager, detail, data, path_field="path")
            if err_pair:
                code, msg = err_pair
                return error_response(
                    "mirror",
                    code,
                    msg,
                    recovery="Вызови mirror без path/kind чтобы увидеть корневое дерево, или radar чтобы взять активный path.",
                    suggestions=_path_help_suggestions(focus_id),
                )
            step, _, _ = _find_step_by_path(list(getattr(detail, "steps", []) or []), target_path)
            if not step:
                return error_response(
                    "mirror",
                    "STEP_NOT_FOUND",
                    f"Шаг не найден: {target_path}",
                    recovery="Возьми корректный step_id/path через mirror без path или через radar.",
                    suggestions=_path_help_suggestions(focus_id),
                )
            plan = getattr(step, "plan", None)
            tasks = list(getattr(plan, "tasks", []) or []) if plan else []
            scope.update({"path": target_path, "kind": "step"})
            items = _mirror_items_from_task_nodes(tasks, prefix=target_path)
        else:
            items = _mirror_items_from_steps(list(getattr(detail, "steps", []) or []))

    if limit is not None:
        items = items[: max(0, limit)]

    _normalize_mirror_progress(items)
    summary = {
        "total": len(items),
        "completed": sum(1 for i in items if i.get("status") == "completed"),
        "in_progress": sum(1 for i in items if i.get("status") == "in_progress"),
        "pending": sum(1 for i in items if i.get("status") == "pending"),
    }

    return AIResponse(
        success=True,
        intent="mirror",
        result={"scope": scope, "items": items, "summary": summary},
        context={"task_id": focus_id},
    )


def handle_complete(manager: TaskManager, data: Dict[str, Any]) -> AIResponse:
    task_id = data.get("task")
    if not task_id:
        return error_response(
            "complete",
            "MISSING_TASK",
            "task обязателен",
            recovery="Передай task=TASK-###|PLAN-### или установи focus через focus_set и передай его явно.",
            suggestions=_missing_target_suggestions(manager, want=["TASK-", "PLAN-"]),
        )
    err = validate_task_id(task_id)
    if err:
        return error_response(
            "complete",
            "INVALID_TASK",
            err,
            recovery="Проверь id через context(include_all=true).",
            suggestions=_missing_target_suggestions(manager, want=["TASK-", "PLAN-"]),
        )
    task_id = str(task_id)

    status = str(data.get("status", "DONE") or "DONE").strip().upper()
    force = bool(data.get("force", False))
    override_reason = str(data.get("override_reason", "") or "").strip()
    if force and not override_reason:
        return error_response("complete", "MISSING_OVERRIDE_REASON", "override_reason обязателен при force=true")
    ok, error = manager.update_task_status(task_id, status, domain=str(data.get("domain", "") or ""), force=force)
    if not ok:
        code = (error or {}).get("code", "FAILED")
        msg = (error or {}).get("message", "Не удалось обновить статус")
        return error_response("complete", str(code).upper(), msg, result={"task": task_id})
    detail = manager.load_task(task_id, skip_sync=True)
    if not detail:
        return error_response(
            "complete",
            "NOT_FOUND",
            f"Не найдено: {task_id}",
            recovery="Проверь id через context(include_all=true).",
            suggestions=_missing_target_suggestions(manager, want="PLAN-" if task_id.startswith("PLAN-") else "TASK-"),
            result={"task": task_id},
        )
    if force and override_reason and detail:
        try:
            detail.events.append(StepEvent.override(f"complete:{status}", override_reason, target=f"task:{task_id}"))
            manager.save_task(detail, skip_sync=True)
        except Exception:
            pass
    key = "plan" if getattr(detail, "kind", "task") == "plan" else "task"
    payload = plan_to_dict(detail, compact=False) if key == "plan" else task_to_dict(detail, include_steps=True, compact=False)
    return AIResponse(success=True, intent="complete", result={key: payload}, context={"task_id": task_id})


def handle_delete(manager: TaskManager, data: Dict[str, Any]) -> AIResponse:
    task_id = data.get("task")
    if not task_id:
        return error_response(
            "delete",
            "MISSING_TASK",
            "task обязателен",
            recovery="Передай task=TASK-###|PLAN-### или установи focus через focus_set и передай его явно.",
            suggestions=_missing_target_suggestions(manager, want=["TASK-", "PLAN-"]),
        )
    err = validate_task_id(task_id)
    if err:
        return error_response(
            "delete",
            "INVALID_TASK",
            err,
            recovery="Проверь id через context(include_all=true).",
            suggestions=_missing_target_suggestions(manager, want=["TASK-", "PLAN-"]),
        )
    task_id = str(task_id)
    path = data.get("path")
    if path is None and data.get("step_id") is None:
        deleted = manager.delete_task(task_id, domain=str(data.get("domain", "") or ""))
        return AIResponse(success=True, intent="delete", result={"task_id": task_id, "deleted": bool(deleted)})
    task = manager.load_task(task_id, skip_sync=True)
    if not task:
        return error_response(
            "delete",
            "NOT_FOUND",
            f"Не найдено: {task_id}",
            recovery="Проверь id через context(include_all=true).",
            suggestions=_missing_target_suggestions(manager, want="PLAN-" if task_id.startswith("PLAN-") else "TASK-"),
            result={"task": task_id},
        )
    if getattr(task, "kind", "task") != "task":
        return error_response("delete", "NOT_A_TASK", "delete path применим только к заданиям (TASK-###)")
    path, path_err = _resolve_step_path(manager, task, data)
    if path_err:
        code, message = path_err
        return error_response(
            "delete",
            code,
            message,
            recovery="Возьми корректный path/step_id через radar/mirror.",
            suggestions=_path_help_suggestions(task_id),
        )
    ok, code, deleted_step = manager.delete_step_node(task_id, path=path, domain=task.domain)
    if not ok:
        mapping = {"not_found": "NOT_FOUND", "path": "PATH_NOT_FOUND"}
        return error_response("delete", mapping.get(code or "", "FAILED"), code or "Не удалось удалить")
    return AIResponse(
        success=True,
        intent="delete",
        result={"task_id": task_id, "path": path, "deleted_step": step_to_dict(deleted_step, path=path, compact=False) if deleted_step else None},
        context={"task_id": task_id},
    )


def _copy_dir(src: Path, dst: Path) -> None:
    if not src.exists():
        dst.mkdir(parents=True, exist_ok=True)
        return
    shutil.copytree(src, dst, dirs_exist_ok=True)


def _restore_dir(backup: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(backup, target)


def handle_batch(manager: TaskManager, data: Dict[str, Any]) -> AIResponse:
    ops = data.get("operations")
    if ops is None:
        return error_response("batch", "MISSING_OPERATIONS", "operations обязателен")
    if not isinstance(ops, list) or not ops:
        return error_response("batch", "INVALID_OPERATIONS", "operations должен быть непустым массивом")
    atomic = bool(data.get("atomic", False))
    default_task = data.get("task")
    if default_task is not None:
        err = validate_task_id(default_task)
        if err:
            return error_response("batch", "INVALID_TASK", err)
        default_task = str(default_task)

    try:
        history_before = OperationHistory(storage_dir=Path(manager.tasks_dir))
        initial_latest_id = history_before.operations[-1].id if history_before.operations else None
    except Exception:
        initial_latest_id = None

    # Optional sugar: allow one operation to target multiple step paths via `paths: [...]`.
    # This keeps the batch payload small and deterministic while preserving the canonical single-path intents.
    expanded_ops: List[Dict[str, Any]] = []
    for op in ops:
        if not isinstance(op, dict):
            expanded_ops.append(op)  # will be validated below
            continue
        paths = op.get("paths")
        if isinstance(paths, list):
            if not paths:
                continue
            for raw_path in paths:
                path_value = str(raw_path)
                path_err = validate_path(path_value)
                if path_err:
                    return error_response("batch", "INVALID_PATH", path_err, result={"path": path_value})
                cloned = dict(op)
                cloned.pop("paths", None)
                cloned["path"] = path_value
                expanded_ops.append(cloned)
            continue
        expanded_ops.append(op)

    if len(expanded_ops) > MAX_ARRAY_LENGTH:
        return error_response(
            "batch",
            "TOO_MANY_OPERATIONS_AFTER_EXPANSION",
            f"Too many operations after paths expansion (max {MAX_ARRAY_LENGTH})",
        )

    # If everything was filtered out (e.g., only empty paths), return a stable no-op response.
    if not expanded_ops:
        return AIResponse(success=True, intent="batch", result={"total": 0, "completed": 0, "results": [], "latest_id": initial_latest_id})

    ops = expanded_ops
    total = len(ops)

    completed = 0
    results: List[Dict[str, Any]] = []

    backup_dir: Optional[Path] = None
    if atomic:
        tmp = Path(tempfile.mkdtemp(prefix="apply_task_batch_"))
        backup_dir = tmp / "backup"
        _copy_dir(Path(manager.tasks_dir), backup_dir)
    try:
        for op in ops:
            if not isinstance(op, dict):
                raise ValueError("operation must be object")
            op_payload = dict(op)
            if default_task and "task" not in op_payload and op_payload.get("intent") not in {"context", "storage", "history"}:
                op_payload["task"] = default_task
            resp = process_intent(manager, op_payload)
            if not resp.success:
                if atomic and backup_dir:
                    _restore_dir(backup_dir, Path(manager.tasks_dir))
                    latest_id = initial_latest_id
                else:
                    try:
                        history_now = OperationHistory(storage_dir=Path(manager.tasks_dir))
                        latest_id = history_now.operations[-1].id if history_now.operations else None
                    except Exception:
                        latest_id = None
                return AIResponse(
                    success=False,
                    intent="batch",
                    result={"total": total, "completed": completed, "results": results, "latest_id": latest_id},
                    error_code=resp.error_code or "BATCH_FAILED",
                    error_message=resp.error_message or "Batch failed",
                )
            results.append(resp.to_dict())
            completed += 1
        try:
            history_after = OperationHistory(storage_dir=Path(manager.tasks_dir))
            latest_id = history_after.operations[-1].id if history_after.operations else None
        except Exception:
            latest_id = None
        return AIResponse(
            success=True,
            intent="batch",
            result={"total": total, "completed": completed, "results": results, "latest_id": latest_id},
        )
    finally:
        if atomic and backup_dir:
            try:
                shutil.rmtree(backup_dir.parent)
            except Exception:
                pass


def handle_history(manager: TaskManager, data: Dict[str, Any]) -> AIResponse:
    history = OperationHistory(storage_dir=Path(manager.tasks_dir))
    limit = int(data.get("limit", 20) or 20)
    ops = history.operations[-max(0, limit):]
    return AIResponse(
        success=True,
        intent="history",
        result={
            "operations": [op.to_dict() for op in ops],
            "can_undo": history.can_undo(),
            "can_redo": history.can_redo(),
        },
    )


def handle_delta(manager: TaskManager, data: Dict[str, Any]) -> AIResponse:
    """Return operations since a given operation id (delta updates for agents)."""
    history = OperationHistory(storage_dir=Path(manager.tasks_dir))
    since = str(data.get("since") or data.get("since_operation_id") or data.get("since_id") or "").strip()
    task_filter = data.get("task") or data.get("task_id") or data.get("filter_task")
    if task_filter is not None:
        err = validate_task_id(task_filter)
        if err:
            return error_response("delta", "INVALID_TASK", err)
        task_filter = str(task_filter)
    limit_raw = data.get("limit", 50)
    try:
        limit = int(limit_raw or 50)
    except Exception:
        return error_response("delta", "INVALID_LIMIT", "limit должен быть числом")
    limit = max(0, min(limit, 500))
    include_undone = bool(data.get("include_undone", True))

    ops = list(history.operations or [])
    start_idx = 0
    if since:
        found = next((idx for idx, op in enumerate(ops) if getattr(op, "id", None) == since), None)
        if found is None:
            return error_response(
                "delta",
                "SINCE_NOT_FOUND",
                f"since={since} не найден",
                recovery="Вызови history чтобы получить актуальные operation.id.",
                result={"since": since},
            )
        start_idx = int(found) + 1

    sliced = ops[start_idx:]
    if task_filter:
        sliced = [op for op in sliced if str(getattr(op, "task_id", "") or "") == task_filter]
    if not include_undone:
        sliced = [op for op in sliced if not bool(getattr(op, "undone", False))]
    sliced = sliced[:limit] if limit else []

    latest_id = ops[-1].id if ops else None
    return AIResponse(
        success=True,
        intent="delta",
        result={
            "since": since or None,
            "task": task_filter or None,
            "latest_id": latest_id,
            "operations": [op.to_dict() for op in sliced],
            "can_undo": history.can_undo(),
            "can_redo": history.can_redo(),
        },
    )


def handle_undo(manager: TaskManager, data: Dict[str, Any]) -> AIResponse:
    history = OperationHistory(storage_dir=Path(manager.tasks_dir))
    if not history.can_undo():
        return error_response("undo", "NOTHING_TO_UNDO", "Нет операций для отмены")
    ok, err, undone = history.undo(Path(manager.tasks_dir))
    if not ok:
        return error_response("undo", "UNDO_FAILED", err or "Не удалось отменить")
    return AIResponse(
        success=True,
        intent="undo",
        result={
            "undone_operation": undone.to_dict() if undone else None,
            "can_undo": history.can_undo(),
            "can_redo": history.can_redo(),
        },
    )


def handle_redo(manager: TaskManager, data: Dict[str, Any]) -> AIResponse:
    history = OperationHistory(storage_dir=Path(manager.tasks_dir))
    if not history.can_redo():
        return error_response("redo", "NOTHING_TO_REDO", "Нет операций для повтора")
    ok, err, redone = history.redo(Path(manager.tasks_dir))
    if not ok:
        return error_response("redo", "REDO_FAILED", err or "Не удалось повторить")
    return AIResponse(
        success=True,
        intent="redo",
        result={
            "redone_operation": redone.to_dict() if redone else None,
            "can_undo": history.can_undo(),
            "can_redo": history.can_redo(),
        },
    )


def handle_storage(manager: TaskManager, data: Dict[str, Any]) -> AIResponse:
    from core.desktop.devtools.interface.tasks_dir_resolver import (
        get_project_namespace,
        migrate_legacy_github_namespaces,
        resolve_project_root,
    )

    def count_task_files(root: Path) -> int:
        if not root.exists():
            return 0
        total = 0
        for f in root.rglob("*.task"):
            if ".snapshots" in f.parts or ".trash" in f.parts:
                continue
            total += 1
        return total

    project_root = resolve_project_root()
    namespace = get_project_namespace(project_root)
    global_root = (Path.home() / ".tasks").resolve()
    local_dir = (project_root / ".tasks").resolve()

    # Best-effort normalize legacy global namespaces (cheap after first run).
    try:
        migrate_legacy_github_namespaces(global_root)
    except Exception:
        pass

    namespaces: List[Dict[str, Any]] = []
    if global_root.exists():
        for ns_dir in sorted([p for p in global_root.iterdir() if p.is_dir() and not p.name.startswith(".")]):
            namespaces.append(
                {
                    "namespace": ns_dir.name,
                    "path": str(ns_dir.resolve()),
                    "task_count": count_task_files(ns_dir),
                }
            )

    return AIResponse(
        success=True,
        intent="storage",
        result={
            "global_storage": str(global_root),
            "global_exists": bool(global_root.exists()),
            "local_storage": str(local_dir),
            "local_exists": bool(local_dir.exists()),
            "current_storage": str(Path(manager.tasks_dir).resolve()),
            "current_namespace": namespace,
            "namespaces": namespaces,
        },
    )


INTENT_HANDLERS: Dict[str, Callable[[TaskManager, Dict[str, Any]], AIResponse]] = {
    "context": handle_context,
    "focus_get": handle_focus_get,
    "focus_set": handle_focus_set,
    "focus_clear": handle_focus_clear,
    "radar": handle_radar,
    "resume": handle_resume,
    "lint": handle_lint,
    "create": handle_create,
    "decompose": handle_decompose,
    "task_add": handle_task_add,
    "task_define": handle_task_define,
    "task_delete": handle_task_delete,
    "define": handle_define,
    "verify": handle_verify,
    "done": handle_done,
    "progress": handle_progress,
    "edit": handle_edit,
    "patch": handle_patch,
    "note": handle_note,
    "block": handle_block,
    "contract": handle_contract,
    "plan": handle_plan,
    "mirror": handle_mirror,
    "complete": handle_complete,
    "delete": handle_delete,
    "batch": handle_batch,
    "undo": handle_undo,
    "redo": handle_redo,
    "history": handle_history,
    "delta": handle_delta,
    "storage": handle_storage,
}

_MUTATING_INTENTS = {
    "create",
    "decompose",
    "task_add",
    "task_define",
    "task_delete",
    "define",
    "verify",
    "done",
    "progress",
    "edit",
    "patch",
    "note",
    "block",
    "contract",
    "plan",
    "complete",
    "delete",
}
_TARGETED_MUTATING_INTENTS = set(_MUTATING_INTENTS) - {"create"}


def _parse_expected_revision(data: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
    raw = data.get("expected_revision", None)
    if raw is None:
        raw = data.get("expected_version", None)
    if raw is None:
        return None, None
    if isinstance(raw, bool):
        return None, "expected_revision должен быть целым числом"
    try:
        expected = int(raw)
    except (TypeError, ValueError):
        return None, "expected_revision должен быть целым числом"
    if expected < 0:
        return None, "expected_revision должен быть >= 0"
    return expected, None


def _revision_mismatch_response(intent: str, *, task_id: str, expected: int, current: int) -> AIResponse:
    tid = str(task_id or "").strip()
    recovery = (
        "Состояние изменилось (optimistic concurrency): revision не совпадает.\n"
        "1) Получи актуальную revision через resume(task=...) или radar(task=...).\n"
        "2) Повтори запрос с expected_revision=<current_revision>."
    )
    suggestions: List[Suggestion] = []
    if tid:
        suggestions.append(
            Suggestion(
                action="resume",
                target="tasks_resume",
                reason="Получить актуальную revision и текущее состояние объекта перед повтором операции.",
                priority="high",
                params={"task": tid},
            )
        )
        suggestions.extend(_path_help_suggestions(tid))
    return error_response(
        intent,
        "REVISION_MISMATCH",
        f"revision не совпадает: expected={expected}, current={current}",
        recovery=recovery,
        result={"task": tid, "expected_revision": expected, "current_revision": current},
        context={"task_id": tid} if tid else {},
        suggestions=suggestions,
    )


def _task_file_for(manager: TaskManager, task_id: str, domain: str = "") -> Path:
    base = Path(manager.tasks_dir)
    safe_domain = str(domain or "").strip()
    if safe_domain:
        return (base / safe_domain / f"{task_id}.task").resolve()
    return (base / f"{task_id}.task").resolve()


def process_intent(manager: TaskManager, data: Dict[str, Any]) -> AIResponse:
    if not isinstance(data, dict):
        return error_response("unknown", "INVALID_REQUEST", "payload должен быть объектом JSON")
    intent = str(data.get("intent", "") or "").strip().lower()
    if not intent:
        return error_response("unknown", "MISSING_INTENT", "intent обязателен")
    handler = INTENT_HANDLERS.get(intent)
    if not handler:
        return error_response(intent, "UNKNOWN_INTENT", f"Неизвестный intent: {intent}")

    expected_revision, expected_err = _parse_expected_revision(data)
    if expected_err:
        return error_response(
            intent,
            "INVALID_EXPECTED_REVISION",
            expected_err,
            recovery="Передай expected_revision как целое число (etag-like). Чтобы узнать текущую revision — вызови radar/resume.",
            suggestions=_missing_target_suggestions(manager, want=["TASK-", "PLAN-"]),
        )

    # History tracking: snapshot root task file before mutation.
    history = OperationHistory(storage_dir=Path(manager.tasks_dir))
    task_id = data.get("task") or data.get("plan")

    if expected_revision is not None and intent in _TARGETED_MUTATING_INTENTS and task_id:
        norm_err = validate_task_id(task_id)
        if not norm_err:
            current_detail = manager.load_task(str(task_id), skip_sync=True)
            if current_detail:
                current_revision = int(getattr(current_detail, "revision", 0) or 0)
                if current_revision != int(expected_revision):
                    return _revision_mismatch_response(
                        intent,
                        task_id=str(task_id),
                        expected=int(expected_revision),
                        current=current_revision,
                    )

    task_file: Optional[Path] = None
    if intent in _MUTATING_INTENTS and task_id:
        norm = validate_task_id(task_id)
        if norm is None:
            # Best-effort: domain might be present, otherwise infer from disk.
            domain = str(data.get("domain", "") or "")
            if not domain:
                existing = manager.load_task(str(task_id), skip_sync=True)
                domain = str(getattr(existing, "domain", "") or "") if existing else ""
            task_file = _task_file_for(manager, str(task_id), domain)

    try:
        resp = handler(manager, data)
    except Exception as exc:  # pragma: no cover
        return error_response(intent, "INTERNAL_ERROR", f"internal error: {exc}")

    if intent in _MUTATING_INTENTS and resp.success and not bool(data.get("dry_run", False)):
        try:
            op = history.record(intent=intent, task_id=str(task_id) if task_id else None, data=dict(data), task_file=task_file, result=resp.to_dict())
            # Make delta-chaining trivial for agents: every mutating response carries the op id.
            if op and getattr(op, "id", None):
                resp.meta = dict(resp.meta or {})
                resp.meta.setdefault("operation_id", str(op.id))
        except Exception:
            # Never fail the operation because history failed.
            pass
    return resp


__all__ = [
    "AIResponse",
    "Suggestion",
    "INTENT_HANDLERS",
    "MAX_NESTING_DEPTH",
    "MAX_STRING_LENGTH",
    "MAX_ARRAY_LENGTH",
    "validate_task_id",
    "validate_path",
    "validate_string",
    "validate_array",
    "validate_steps_data",
    "build_context",
    "generate_suggestions",
    "error_response",
    "process_intent",
    "handle_context",
    "handle_resume",
    "handle_lint",
    "handle_create",
    "handle_decompose",
    "handle_define",
    "handle_verify",
    "handle_done",
    "handle_progress",
    "handle_complete",
    "handle_batch",
    "handle_contract",
    "handle_plan",
    "handle_edit",
    "handle_patch",
    "handle_note",
    "handle_block",
    "handle_delete",
    "handle_undo",
    "handle_redo",
    "handle_history",
    "handle_storage",
]
