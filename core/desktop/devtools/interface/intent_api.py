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
from core.evidence import redact, redact_text
from core.desktop.devtools.application.context import (
    clear_last_task,
    get_last_task,
    normalize_task_id,
    save_last_task,
)
from core.desktop.devtools.application.plan_semantics import append_contract_version_if_changed
from core.desktop.devtools.application.scaffolding import (
    apply_preview_ids,
    build_plan_from_template,
    build_task_from_template,
    get_template,
    list_templates,
)
from core.desktop.devtools.application.task_manager import TaskManager, _find_step_by_path, _find_task_by_path
from core.desktop.devtools.application.linting import lint_item
from core.desktop.devtools.interface.artifacts_store import write_artifact
from core.desktop.devtools.interface.evidence_collectors import collect_auto_verification_checks
from core.desktop.devtools.interface.operation_history import OperationHistory
from core.desktop.devtools.interface.serializers import plan_to_dict, plan_node_to_dict, step_to_dict, task_to_dict, task_node_to_dict
from core.desktop.devtools.interface.tasks_dir_resolver import resolve_project_root


MAX_STRING_LENGTH = 500
MAX_ARRAY_LENGTH = 200
MAX_NESTING_DEPTH = 24
MAX_ARTIFACT_BYTES = 256_000
MAX_EVIDENCE_ITEMS = 20

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


def _counts_by_kind(items: List[Any]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in items:
        kind = str(getattr(item, "kind", "") or "").strip() or "unknown"
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def _latest_observed_at(items: List[Any]) -> str:
    observed = [str(getattr(item, "observed_at", "") or "").strip() for item in items]
    observed = [v for v in observed if v]
    return max(observed) if observed else ""


def _normalize_checks_payload(raw: Any) -> List[Dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("checks должен быть массивом")
    normalized: List[Dict[str, Any]] = []
    for idx, item in enumerate(raw):
        if isinstance(item, str):
            value = item.strip()
            if not value:
                raise ValueError(f"checks[{idx}] пустая строка")
            normalized.append({"kind": "command", "spec": value, "outcome": "info"})
            continue
        if isinstance(item, dict):
            normalized.append(dict(item))
            continue
        raise ValueError(f"checks[{idx}] должен быть объектом или строкой")
    return normalized


def _normalize_attachments_payload(raw: Any) -> List[Dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("attachments должен быть массивом")
    normalized: List[Dict[str, Any]] = []
    for idx, item in enumerate(raw):
        if isinstance(item, str):
            value = item.strip()
            if not value:
                raise ValueError(f"attachments[{idx}] пустая строка")
            normalized.append({"kind": "file", "path": value})
            continue
        if isinstance(item, dict):
            payload = dict(item)
            if not str(payload.get("path", "") or "").strip():
                file_path = str(payload.get("file_path", "") or "").strip()
                if file_path:
                    payload["path"] = file_path
            normalized.append(payload)
            continue
        raise ValueError(f"attachments[{idx}] должен быть объектом или строкой")
    return normalized


def _checkpoint_snapshot_for_node(target: Any) -> Dict[str, Any]:
    """Return a compact before/after snapshot of checkpoint state for mutation responses."""
    return {
        "criteria": {
            "confirmed": bool(getattr(target, "criteria_confirmed", False)),
            "auto_confirmed": bool(getattr(target, "criteria_auto_confirmed", False)),
            "notes_count": len(list(getattr(target, "criteria_notes", []) or [])),
            "evidence_refs_count": len(list(getattr(target, "criteria_evidence_refs", []) or [])),
        },
        "tests": {
            "confirmed": bool(getattr(target, "tests_confirmed", False)),
            "auto_confirmed": bool(getattr(target, "tests_auto_confirmed", False)),
            "notes_count": len(list(getattr(target, "tests_notes", []) or [])),
            "evidence_refs_count": len(list(getattr(target, "tests_evidence_refs", []) or [])),
        },
        "security": {
            "confirmed": bool(getattr(target, "security_confirmed", False)),
            "auto_confirmed": False,
            "notes_count": len(list(getattr(target, "security_notes", []) or [])),
            "evidence_refs_count": len(list(getattr(target, "security_evidence_refs", []) or [])),
        },
        "perf": {
            "confirmed": bool(getattr(target, "perf_confirmed", False)),
            "auto_confirmed": False,
            "notes_count": len(list(getattr(target, "perf_notes", []) or [])),
            "evidence_refs_count": len(list(getattr(target, "perf_evidence_refs", []) or [])),
        },
        "docs": {
            "confirmed": bool(getattr(target, "docs_confirmed", False)),
            "auto_confirmed": False,
            "notes_count": len(list(getattr(target, "docs_notes", []) or [])),
            "evidence_refs_count": len(list(getattr(target, "docs_evidence_refs", []) or [])),
        },
    }


def _step_needs_for_completion(step: Step) -> List[str]:
    """Return a stable list of gating reasons for completion (agent-friendly tokens)."""
    needs: List[str] = []
    if bool(getattr(step, "blocked", False)):
        needs.append("blocked")
    raw_required = [str(v or "").strip().lower() for v in list(getattr(step, "required_checkpoints", []) or []) if str(v or "").strip()]
    required = raw_required or ["criteria", "tests"]
    if "criteria" in required and not bool(getattr(step, "criteria_confirmed", False)):
        needs.append("criteria")
    if "tests" in required and not (bool(getattr(step, "tests_confirmed", False)) or bool(getattr(step, "tests_auto_confirmed", False))):
        needs.append("tests")
    if "security" in required and not bool(getattr(step, "security_confirmed", False)):
        needs.append("security")
    if "perf" in required and not bool(getattr(step, "perf_confirmed", False)):
        needs.append("perf")
    if "docs" in required and not bool(getattr(step, "docs_confirmed", False)):
        needs.append("docs")
    plan = getattr(step, "plan", None)
    tasks = list(getattr(plan, "tasks", []) or []) if plan else []
    if tasks and not all(t.is_done() for t in tasks):
        needs.append("plan_tasks")
    return needs


def _truncate_utf8(text: str, *, max_bytes: int) -> Tuple[str, bool, int]:
    raw = str(text or "").encode("utf-8")
    original = len(raw)
    if original <= int(max_bytes):
        return str(text or ""), False, original
    truncated = raw[: int(max_bytes)].decode("utf-8", errors="ignore")
    return truncated, True, original


def _json_bytes(value: Any) -> int:
    try:
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return len(raw)
    except Exception:
        return 0


def _apply_radar_budget(result: Dict[str, Any], *, max_chars: int) -> None:
    """Enforce a hard output budget for Radar View (agent-friendly, compact-by-default).

    The budget is applied as UTF-8 bytes of the JSON rendering. The function is
    deterministic: it applies reductions in a stable order and never removes the
    main Radar keys (now/why/verify/next/blockers/open_checkpoints).
    """

    max_bytes = int(max_chars)
    if max_bytes <= 0:
        return

    original = _json_bytes(result)
    truncated = False
    if original > max_bytes:
        truncated = True

        # 1) Keep next suggestions short.
        if isinstance(result.get("next"), list):
            result["next"] = list(result["next"])[:1]

        # 2) Drop auxiliary navigation links first.
        if _json_bytes(result) > max_bytes:
            result.pop("links", None)

        # 3) Cap verify commands.
        verify = result.get("verify")
        if isinstance(verify, dict):
            cmds = verify.get("commands")
            if isinstance(cmds, list):
                verify["commands"] = [_preview_text(str(c or ""), max_len=180) for c in list(cmds)[:5]]

        # 4) Cap contract preview (why).
        why = result.get("why")
        if isinstance(why, dict) and why.get("contract_preview") is not None:
            why["contract_preview"] = _preview_text(str(why.get("contract_preview") or ""), max_len=140)

        # 5) Drop contract summary (still available via resume/context).
        if _json_bytes(result) > max_bytes and isinstance(why, dict):
            why.pop("contract", None)

        # 6) Drop heavy evidence details.
        if _json_bytes(result) > max_bytes and isinstance(verify, dict):
            verify.pop("evidence", None)
            verify.pop("missing", None)

        # 7) Last resort: shrink focus title.
        focus = result.get("focus")
        if _json_bytes(result) > max_bytes and isinstance(focus, dict):
            focus["title"] = _preview_text(str(focus.get("title") or ""), max_len=80)

        # 8) Shrink now title and drop next if still above budget.
        if _json_bytes(result) > max_bytes:
            now = result.get("now")
            if isinstance(now, dict) and now.get("title") is not None:
                now["title"] = _preview_text(str(now.get("title") or ""), max_len=80)
        if _json_bytes(result) > max_bytes and isinstance(result.get("next"), list):
            result["next"] = []

        # 9) Hard clamp: if still too large, return a minimal stable skeleton.
        if _json_bytes(result) > max_bytes:
            focus = result.get("focus") if isinstance(result.get("focus"), dict) else {}
            minimal = {
                "focus": {
                    "id": str((focus or {}).get("id") or ""),
                    "kind": str((focus or {}).get("kind") or ""),
                    "revision": int((focus or {}).get("revision") or 0),
                    "domain": str((focus or {}).get("domain") or ""),
                    "title": _preview_text(str((focus or {}).get("title") or ""), max_len=80),
                },
                "now": {},
                "why": {},
                "verify": {"commands": [], "open_checkpoints": [], "ready": None, "needs": None},
                "next": [],
                "blockers": {"blocked": False, "blockers": [], "depends_on": [], "unresolved_depends_on": []},
                "open_checkpoints": [],
            }
            result.clear()
            result.update(minimal)

    used = _json_bytes(result)
    result["budget"] = {"max_chars": int(max_chars), "used_chars": int(used), "truncated": bool(truncated or used > max_bytes)}


def _apply_context_pack_budget(result: Dict[str, Any], *, max_chars: int) -> None:
    """Enforce a hard output budget for context_pack (radar + delta)."""
    max_bytes = int(max_chars)
    if max_bytes <= 0:
        return

    truncated = False
    if _json_bytes(result) > max_bytes:
        truncated = True
        delta = result.get("delta")
        if isinstance(delta, dict):
            ops = delta.get("operations")
            if isinstance(ops, list):
                for op in ops:
                    if isinstance(op, dict):
                        op.pop("snapshot", None)
                delta["include_snapshot"] = False

            if _json_bytes(result) > max_bytes and isinstance(ops, list):
                keep = min(len(ops), 3)
                delta["operations"] = list(ops)[:keep]

            if _json_bytes(result) > max_bytes and isinstance(delta.get("operations"), list):
                compact_ops = []
                for op in list(delta.get("operations") or []):
                    if not isinstance(op, dict):
                        continue
                    compact_ops.append(
                        {
                            key: op.get(key)
                            for key in ("id", "timestamp", "intent", "task_id", "undone", "has_result")
                            if key in op
                        }
                    )
                delta["operations"] = compact_ops
                delta["include_details"] = False

            if _json_bytes(result) > max_bytes:
                result["delta"] = {"operations": [], "truncated": True}

        if _json_bytes(result) > max_bytes:
            result.pop("radar_budget", None)
            _apply_radar_budget(result, max_chars=max_bytes)

    result.pop("budget", None)
    used = _json_bytes(result)
    if used > max_bytes:
        focus = result.get("focus") if isinstance(result.get("focus"), dict) else {}
        minimal = {
            "focus": {
                "id": str((focus or {}).get("id") or ""),
                "kind": str((focus or {}).get("kind") or ""),
                "revision": int((focus or {}).get("revision") or 0),
                "domain": str((focus or {}).get("domain") or ""),
                "title": _preview_text(str((focus or {}).get("title") or ""), max_len=80),
            },
            "now": {},
            "why": {},
            "verify": {"commands": [], "open_checkpoints": [], "ready": None, "needs": None},
            "next": [],
            "blockers": {"blocked": False, "blockers": [], "depends_on": [], "unresolved_depends_on": []},
            "open_checkpoints": [],
            "delta": {"operations": [], "truncated": True},
        }
        result.clear()
        result.update(minimal)
        used = _json_bytes(result)
    result["budget"] = {"max_chars": int(max_chars), "used_chars": int(used), "truncated": bool(truncated or used > max_bytes)}


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


def _focus_pointer() -> Tuple[Optional[str], str]:
    """Return (focus_id, focus_domain) from `.last` pointer (best-effort)."""
    last_id, last_domain = get_last_task()
    focus_id: Optional[str] = None
    if last_id:
        try:
            focus_id = normalize_task_id(str(last_id))
        except Exception:
            focus_id = str(last_id).strip() or None
    return focus_id, str(last_domain or "")


# Focus is convenience, never magic: only used when explicit target ids are omitted.
_FOCUSABLE_MUTATING_INTENTS: set[str] = {
    # Task/Plan item mutations
    "edit",
    "patch",
    "complete",
    "delete",
    # Task-only step tree mutations
    "decompose",
    "task_add",
    "task_define",
    "task_delete",
    "define",
    "verify",
    "evidence_capture",
    "done",
    "close_step",
    "progress",
    "note",
    "block",
    # Plan-specific mutations
    "contract",
    "plan",
}

_TASK_ONLY_INTENTS: set[str] = {
    "decompose",
    "task_add",
    "task_define",
    "task_delete",
    "define",
    "verify",
    "evidence_capture",
    "done",
    "close_step",
    "progress",
    "note",
    "block",
}

_PLAN_ONLY_INTENTS: set[str] = {"contract", "plan"}


def _apply_focus_to_mutation(manager: TaskManager, *, intent: str, data: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any], Optional[AIResponse]]:
    """Fill missing target ids from focus for mutating intents (explicit > focus).

    Returns: (payload, context_additions, error_response_or_none).
    """
    payload: Dict[str, Any] = dict(data or {})
    if intent not in _FOCUSABLE_MUTATING_INTENTS:
        return payload, {}, None

    # Explicit addressing always wins.
    has_explicit = payload.get("task") is not None or payload.get("plan") is not None
    if has_explicit:
        return payload, {"target_resolution": {"source": "explicit"}}, None

    focus_id, focus_domain = _focus_pointer()
    if not focus_id:
        return (
            payload,
            {"target_resolution": {"source": "missing", "focus": None}},
            error_response(
                intent,
                "MISSING_TARGET",
                "Не указан target id и нет focus",
                recovery="Передай task=TASK-###|PLAN-### (или plan=PLAN-### для plan/contract) либо установи focus через focus_set.",
                suggestions=_missing_target_suggestions(manager, want=["TASK-", "PLAN-"]),
            ),
        )

    # Plan-only intents: accept focus plan, or derive parent plan from focus task.
    if intent in _PLAN_ONLY_INTENTS:
        if focus_id.startswith("PLAN-"):
            payload["plan"] = focus_id
            return payload, {"target_resolution": {"source": "focus", "focus": focus_id, "plan": focus_id, "domain": focus_domain}}, None
        if focus_id.startswith("TASK-"):
            focus_task = manager.load_task(focus_id, skip_sync=True)
            parent = str(getattr(focus_task, "parent", "") or "").strip()
            if parent.startswith("PLAN-"):
                payload["plan"] = parent
                return (
                    payload,
                    {"target_resolution": {"source": "focus_task_parent", "focus": focus_id, "plan": parent, "domain": focus_domain}},
                    None,
                )
        return (
            payload,
            {"target_resolution": {"source": "focus_incompatible", "focus": focus_id, "domain": focus_domain}},
            error_response(
                intent,
                "FOCUS_INCOMPATIBLE",
                f"focus={focus_id} не подходит для intent={intent} (нужен PLAN-###)",
                recovery="Установи focus на PLAN-### через focus_set или передай plan=PLAN-### явно.",
                suggestions=_missing_target_suggestions(manager, want="PLAN-"),
            ),
        )

    # Task-only intents: require TASK focus.
    if intent in _TASK_ONLY_INTENTS:
        if not focus_id.startswith("TASK-"):
            return (
                payload,
                {"target_resolution": {"source": "focus_incompatible", "focus": focus_id, "domain": focus_domain}},
                error_response(
                    intent,
                    "FOCUS_INCOMPATIBLE",
                    f"focus={focus_id} не подходит для intent={intent} (нужен TASK-###)",
                    recovery="Установи focus на TASK-### через focus_set или передай task=TASK-### явно.",
                    suggestions=_missing_target_suggestions(manager, want="TASK-"),
                ),
            )
        payload["task"] = focus_id
        return payload, {"target_resolution": {"source": "focus", "focus": focus_id, "task": focus_id, "domain": focus_domain}}, None

    # Item-level (TASK or PLAN) intents: accept any focus id.
    payload["task"] = focus_id
    return payload, {"target_resolution": {"source": "focus", "focus": focus_id, "task": focus_id, "domain": focus_domain}}, None


def _auto_strict_writes_required(manager: TaskManager) -> Tuple[bool, int]:
    """Auto-enable strict targeting when multiple ACTIVE targets exist."""
    try:
        details = manager.list_all_tasks(skip_sync=True)
    except Exception:
        return False, 0
    active_count = 0
    for detail in details:
        status = str(getattr(detail, "status", "") or "").strip().upper()
        if status == "ACTIVE":
            active_count += 1
    return active_count > 1, active_count


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
            items = _mirror_items_from_steps(list(getattr(task, "steps", []) or []))
            _normalize_mirror_progress(items)
            now = next((i for i in items if i.get("status") == "in_progress"), None)
            if not now:
                now = next((i for i in items if i.get("status") == "pending"), None)
            if not now and items:
                now = items[0]
            if not now or not now.get("path"):
                return []
            path = str(now.get("path") or "").strip()
            step_id = str(now.get("id", "") or "").strip() or None
            st, _, _ = _find_step_by_path(list(getattr(task, "steps", []) or []), path)

            ready = bool(getattr(st, "ready_for_completion", lambda: False)()) if st else False
            needs = _step_needs_for_completion(st) if st and not ready else []
            confirmable = {"criteria", "tests", "security", "perf", "docs"}
            missing_checkpoints = [n for n in needs if n in confirmable]
            non_confirmable = [n for n in needs if n not in confirmable]
            if non_confirmable:
                return []
            checkpoints_payload: Dict[str, Any] = {}
            if missing_checkpoints:
                checkpoints_payload = {k: {"confirmed": True} for k in missing_checkpoints}
            else:
                defaults: List[str] = []
                if st and list(getattr(st, "success_criteria", []) or []):
                    defaults.append("criteria")
                if st and (list(getattr(st, "tests", []) or []) or bool(getattr(st, "tests_auto_confirmed", False))):
                    defaults.append("tests")
                if not defaults:
                    defaults = ["criteria"]
                checkpoints_payload = {k: {"confirmed": True} for k in _dedupe_strs(defaults)}

            # Radar suggestions are executable-by-shape: provide a canonical atomic batch skeleton
            # for the common "confirm checkpoints → close step" loop.
            if st and getattr(st, "completed", False):
                return []
            return [
                Suggestion(
                    action="batch",
                    target=path,
                    reason="Золотой путь: подтверди чекпоинты и заверши шаг одной атомарной пачкой.",
                    priority="high",
                    params={
                        "atomic": True,
                        "task": focus_id,
                        "expected_target_id": focus_id,
                        "expected_kind": "task",
                        "strict_targeting": True,
                        "operations": [
                            {
                                "intent": "close_step",
                                "path": path,
                                "step_id": step_id,
                                "note": "",
                                "checkpoints": checkpoints_payload or {"criteria": {"confirmed": True}},
                            },
                        ],
                    },
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


def _build_radar_payload(
    manager: TaskManager,
    detail: Any,
    focus_id: str,
    focus_domain: str,
    *,
    limit: int,
    max_chars: int,
) -> Tuple[Dict[str, Any], List[Suggestion]]:
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
        "now": {},
        "why": {},
        "verify": {"commands": [], "ready": None, "needs": None},
        "next": [s.to_dict() for s in next_suggestions],
        "blockers": {"blocked": False, "blockers": [], "depends_on": [], "unresolved_depends_on": []},
        "open_checkpoints": [],
        "links": {
            "resume": {"intent": "resume", focus_key: focus_id},
            "mirror": {"intent": "mirror", focus_key: focus_id, "limit": 10},
            "context": {"intent": "context", "include_all": True, "compact": True},
            "focus_get": {"intent": "focus_get"},
            "history": {"intent": "history", "limit": 20},
            "handoff": {"intent": "handoff", focus_key: focus_id, "limit": limit, "max_chars": max_chars},
        },
    }

    if getattr(detail, "kind", "task") == "plan":
        contract_summary = _contract_summary(getattr(detail, "contract_data", {}) or {})
        steps = list(getattr(detail, "plan_steps", []) or [])
        current = int(getattr(detail, "plan_current", 0) or 0)
        current = max(0, min(current, len(steps)))
        title = steps[current] if current < len(steps) else ""
        status = "completed" if steps and current >= len(steps) else ("in_progress" if steps else "pending")
        result["now"] = {
            "kind": "plan_step",
            "index": current,
            "title": title,
            "total": len(steps),
            "status": status,
            "queue": {"remaining": max(0, len(steps) - current), "total": len(steps)},
        }
        why_payload: Dict[str, Any] = {
            "plan_id": focus_id,
            "contract_preview": _preview_text(str(getattr(detail, "contract", "") or "")),
        }
        if contract_summary:
            why_payload["contract"] = contract_summary
        result["why"] = why_payload
        open_checkpoints: List[str] = []
        if list(getattr(detail, "success_criteria", []) or []) and not bool(getattr(detail, "criteria_confirmed", False)):
            open_checkpoints.append("criteria")
        tests_auto = bool(getattr(detail, "tests_auto_confirmed", False))
        if list(getattr(detail, "tests", []) or []) and not (bool(getattr(detail, "tests_confirmed", False)) or tests_auto):
            open_checkpoints.append("tests")
        commands = _dedupe_strs(list(contract_summary.get("checks", []) or []) + list(getattr(detail, "tests", []) or []))
        result["verify"] = {
            "commands": commands[:10],
            "open_checkpoints": open_checkpoints,
            "criteria_confirmed": bool(getattr(detail, "criteria_confirmed", False)),
            "tests_confirmed": bool(getattr(detail, "tests_confirmed", False)),
            "ready": None,
            "needs": None,
        }
        result["open_checkpoints"] = open_checkpoints
        result["how_to_verify"] = {"commands": result["verify"]["commands"], "open_checkpoints": open_checkpoints}
        deps = [str(d or "").strip() for d in list(getattr(detail, "depends_on", []) or []) if str(d or "").strip()]
        unresolved: List[str] = []
        for dep_id in deps:
            dep = manager.load_task(dep_id, skip_sync=True)
            if not dep or str(getattr(dep, "status", "") or "").upper() != "DONE":
                unresolved.append(dep_id)
        result["blockers"] = {
            "blocked": bool(getattr(detail, "blocked", False)),
            "blockers": list(getattr(detail, "blockers", []) or []),
            "depends_on": deps,
            "unresolved_depends_on": unresolved,
        }
        return result, next_suggestions

    task = detail
    items = _mirror_items_from_steps(list(getattr(task, "steps", []) or []))
    _normalize_mirror_progress(items)
    now = next((i for i in items if i.get("status") == "in_progress"), None)
    if not now:
        now = next((i for i in items if i.get("status") == "pending"), None)
    if not now and items:
        now = items[0]
    queue = _compute_checkpoint_status(task)
    queue_summary = {
        "pending": len(list(queue.get("pending", []) or [])),
        "ready": len(list(queue.get("ready", []) or [])),
        "next_pending": (list(queue.get("pending", []) or [])[:1] or [None])[0],
        "next_ready": (list(queue.get("ready", []) or [])[:1] or [None])[0],
    }
    now_payload = dict(now or {})
    if now_payload:
        now_payload.setdefault("queue", queue_summary)
    else:
        now_payload = {"kind": "step", "status": "missing", "queue": queue_summary}
    result["now"] = now_payload

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

    verify_payload: Dict[str, Any] = {
        "commands": _dedupe_strs(list(plan_contract_summary.get("checks", []) or []))[:10],
        "open_checkpoints": [],
    }
    open_checkpoints: List[str] = []
    if now_payload.get("path"):
        path = str(now_payload.get("path") or "")
        st, _, _ = _find_step_by_path(list(getattr(task, "steps", []) or []), path)
        if st:
            ready = bool(st.ready_for_completion())
            needs = [] if ready else _step_needs_for_completion(st)
            missing: List[Dict[str, Any]] = []
            if "criteria" in needs:
                missing.append({"checkpoint": "criteria", "path": path})
                open_checkpoints.append("criteria")
            if "tests" in needs:
                missing.append({"checkpoint": "tests", "path": path})
                open_checkpoints.append("tests")
            if "security" in needs:
                missing.append({"checkpoint": "security", "path": path})
                open_checkpoints.append("security")
            if "perf" in needs:
                missing.append({"checkpoint": "perf", "path": path})
                open_checkpoints.append("perf")
            if "docs" in needs:
                missing.append({"checkpoint": "docs", "path": path})
                open_checkpoints.append("docs")
            if "blocked" in needs:
                missing.append({"checkpoint": "unblocked", "path": path})
                open_checkpoints.append("unblocked")
            if "plan_tasks" in needs:
                missing.append({"checkpoint": "plan_tasks", "path": path})
                open_checkpoints.append("plan_tasks")
            checks = list(getattr(st, "verification_checks", []) or [])
            attachments = list(getattr(st, "attachments", []) or [])
            commands = _dedupe_strs(list(plan_contract_summary.get("checks", []) or []) + list(getattr(st, "tests", []) or []))
            verify_payload = {
                "path": path,
                "step_id": str(getattr(st, "id", "") or ""),
                "commands": commands[:10],
                "open_checkpoints": [],
                "missing_checkpoints": [],
                "tests": list(getattr(st, "tests", []) or [])[:10],
                "ready": ready,
                "needs": needs,
                "missing": missing,
                "evidence": {
                    "verification_outcome": str(getattr(st, "verification_outcome", "") or ""),
                    "checks": {
                        "count": len(checks),
                        "kinds": _counts_by_kind(checks),
                        "last_observed_at": _latest_observed_at(checks),
                    },
                    "attachments": {
                        "count": len(attachments),
                        "kinds": _counts_by_kind(attachments),
                        "last_observed_at": _latest_observed_at(attachments),
                    },
                },
            }
            result["how_to_verify"] = {
                "path": path,
                "step_id": str(getattr(st, "id", "") or ""),
                "commands": commands[:10],
                "missing_checkpoints": [m.get("checkpoint") for m in missing if m.get("checkpoint")],
            }
            verify_payload["open_checkpoints"] = list(open_checkpoints)
            verify_payload["missing_checkpoints"] = [m.get("checkpoint") for m in missing if m.get("checkpoint")]
    if isinstance(verify_payload, dict) and "open_checkpoints" in verify_payload:
        verify_payload["open_checkpoints"] = list(open_checkpoints)
    result["verify"] = verify_payload
    result["open_checkpoints"] = open_checkpoints

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
    return result, next_suggestions


def _handoff_progress_snapshot(detail: Any) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    max_items = 5
    if getattr(detail, "kind", "task") == "plan":
        steps = list(getattr(detail, "plan_steps", []) or [])
        current = int(getattr(detail, "plan_current", 0) or 0)
        current = max(0, min(current, len(steps)))
        done_items = [str(s or "") for s in steps[:current]]
        remaining_items = [str(s or "") for s in steps[current:]]
    else:
        steps = list(getattr(detail, "steps", []) or [])
        done_items = [str(getattr(s, "title", "") or "") for s in steps if getattr(s, "completed", False)]
        remaining_items = [str(getattr(s, "title", "") or "") for s in steps if not getattr(s, "completed", False)]

    total = len(done_items) + len(remaining_items)
    done_payload = {"count": len(done_items), "total": total, "items": done_items[:max_items]}
    remaining_payload = {"count": len(remaining_items), "total": total, "items": remaining_items[:max_items]}
    return done_payload, remaining_payload


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

    try:
        max_chars = int(data.get("max_chars", 12_000) or 12_000)
    except Exception:
        return error_response("radar", "INVALID_MAX_CHARS", "max_chars должен быть числом")
    max_chars = max(1_000, min(max_chars, 50_000))

    result, next_suggestions = _build_radar_payload(
        manager,
        detail,
        focus_id,
        focus_domain,
        limit=limit,
        max_chars=max_chars,
    )
    _apply_radar_budget(result, max_chars=max_chars)

    return AIResponse(
        success=True,
        intent="radar",
        result=result,
        context={"task_id": focus_id},
        suggestions=next_suggestions,
    )


def handle_handoff(manager: TaskManager, data: Dict[str, Any]) -> AIResponse:
    focus = data.get("task") or data.get("plan")
    focus_domain: str = ""
    if focus is None:
        last_id, last_domain = get_last_task()
        focus = last_id
        focus_domain = str(last_domain or "")
    if not focus:
        return error_response(
            "handoff",
            "MISSING_ID",
            "Не указан task/plan и нет focus",
            recovery="Передай task=TASK-###|plan=PLAN-### или установи focus через focus_set.",
            suggestions=_missing_target_suggestions(manager, want=["TASK-", "PLAN-"]),
        )
    err = validate_task_id(focus)
    if err:
        return error_response(
            "handoff",
            "INVALID_ID",
            err,
            recovery="Проверь id через context(include_all=true) или установи focus через focus_set.",
            suggestions=_missing_target_suggestions(manager, want=["TASK-", "PLAN-"]),
        )
    focus_id = str(focus)
    detail = manager.load_task(focus_id, skip_sync=True)
    if not detail:
        return error_response(
            "handoff",
            "NOT_FOUND",
            f"Не найдено: {focus_id}",
            recovery="Проверь id через context(include_all=true) или установи focus заново.",
            suggestions=_missing_target_suggestions(manager, want="PLAN-" if focus_id.startswith("PLAN-") else "TASK-"),
        )

    try:
        limit = int(data.get("limit", 3) or 3)
    except Exception:
        return error_response("handoff", "INVALID_LIMIT", "limit должен быть числом")
    limit = max(0, min(limit, 10))

    try:
        max_chars = int(data.get("max_chars", 12_000) or 12_000)
    except Exception:
        return error_response("handoff", "INVALID_MAX_CHARS", "max_chars должен быть числом")
    max_chars = max(1_000, min(max_chars, 50_000))

    result, next_suggestions = _build_radar_payload(
        manager,
        detail,
        focus_id,
        focus_domain,
        limit=limit,
        max_chars=max_chars,
    )

    done_payload, remaining_payload = _handoff_progress_snapshot(detail)
    result["done"] = done_payload
    result["remaining"] = remaining_payload
    risks = list(getattr(detail, "risks", []) or [])
    if not risks:
        contract_payload = result.get("why", {}).get("contract") if isinstance(result.get("why"), dict) else None
        if isinstance(contract_payload, dict):
            risks = list(contract_payload.get("risks", []) or [])
    result["risks"] = risks

    _apply_radar_budget(result, max_chars=max_chars)

    return AIResponse(
        success=True,
        intent="handoff",
        result=result,
        context={"task_id": focus_id},
        suggestions=next_suggestions,
    )


def handle_context_pack(manager: TaskManager, data: Dict[str, Any]) -> AIResponse:
    """Cold-start pack: Radar View + delta slice under a hard budget."""
    focus = data.get("task") or data.get("plan")
    focus_domain: str = ""
    if focus is None:
        last_id, last_domain = get_last_task()
        focus = last_id
        focus_domain = str(last_domain or "")
    if not focus:
        return error_response(
            "context_pack",
            "MISSING_ID",
            "Не указан task/plan и нет focus",
            recovery="Передай task=TASK-###|plan=PLAN-### или установи focus через focus_set.",
            suggestions=_missing_target_suggestions(manager, want=["TASK-", "PLAN-"]),
        )
    err = validate_task_id(focus)
    if err:
        return error_response(
            "context_pack",
            "INVALID_ID",
            err,
            recovery="Проверь id через context(include_all=true) или установи focus через focus_set.",
            suggestions=_missing_target_suggestions(manager, want=["TASK-", "PLAN-"]),
        )
    focus_id = str(focus)
    detail = manager.load_task(focus_id, skip_sync=True)
    if not detail:
        return error_response(
            "context_pack",
            "NOT_FOUND",
            f"Не найдено: {focus_id}",
            recovery="Проверь id через context(include_all=true) или установи focus заново.",
            suggestions=_missing_target_suggestions(manager, want="PLAN-" if focus_id.startswith("PLAN-") else "TASK-"),
        )

    try:
        limit = int(data.get("limit", 3) or 3)
    except Exception:
        return error_response("context_pack", "INVALID_LIMIT", "limit должен быть числом")
    limit = max(0, min(limit, 10))

    try:
        max_chars = int(data.get("max_chars", 12_000) or 12_000)
    except Exception:
        return error_response("context_pack", "INVALID_MAX_CHARS", "max_chars должен быть числом")
    max_chars = max(1_000, min(max_chars, 50_000))

    try:
        delta_limit = int(data.get("delta_limit", 20) or 20)
    except Exception:
        return error_response("context_pack", "INVALID_DELTA_LIMIT", "delta_limit должен быть числом")
    delta_limit = max(0, min(delta_limit, 500))

    include_details = bool(data.get("include_details", False))
    include_snapshot = bool(data.get("include_snapshot", False))
    include_undone = bool(data.get("include_undone", True))
    since = str(data.get("since") or data.get("since_operation_id") or data.get("since_id") or "").strip()

    radar_payload, next_suggestions = _build_radar_payload(
        manager,
        detail,
        focus_id,
        focus_domain,
        limit=limit,
        max_chars=max_chars,
    )
    _apply_radar_budget(radar_payload, max_chars=max_chars)
    radar_budget = radar_payload.pop("budget", None)

    delta_resp = handle_delta(
        manager,
        {
            "since": since or None,
            "task": focus_id,
            "limit": delta_limit,
            "include_details": include_details,
            "include_snapshot": include_snapshot,
            "include_undone": include_undone,
        },
    )
    if not delta_resp.success:
        return AIResponse(
            success=False,
            intent="context_pack",
            result={"radar": radar_payload, "radar_budget": radar_budget},
            context={"task_id": focus_id},
            suggestions=next_suggestions,
            warnings=list(delta_resp.warnings or []),
            meta=dict(delta_resp.meta or {}),
            error_code=str(delta_resp.error_code or "DELTA_FAILED"),
            error_message=str(delta_resp.error_message or "delta failed"),
            error_recovery=delta_resp.error_recovery,
        )

    payload: Dict[str, Any] = dict(radar_payload)
    payload["delta"] = delta_resp.result or {}
    if radar_budget is not None:
        payload["radar_budget"] = radar_budget
    _apply_context_pack_budget(payload, max_chars=max_chars)

    return AIResponse(
        success=True,
        intent="context_pack",
        result=payload,
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
                ops.append({"op": "append", "field": "success_criteria", "value": "<define measurable outcome>"})
            if code == "STEP_TESTS_MISSING":
                ops.append({"op": "append", "field": "tests", "value": "<how to verify (cmd/test)>"})
            if code == "STEP_BLOCKERS_MISSING":
                ops.append({"op": "append", "field": "blockers", "value": "<dependency/assumption>"})
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
                    params={
                        "task": focus_id,
                        "kind": "task_detail",
                        "ops": [{"op": "append", "field": "success_criteria", "value": "<definition of done>"}],
                    },
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


def handle_templates_list(manager: TaskManager, data: Dict[str, Any]) -> AIResponse:
    templates = [t.to_dict() for t in list_templates()]
    return AIResponse(success=True, intent="templates_list", result={"templates": templates})


def handle_scaffold(manager: TaskManager, data: Dict[str, Any]) -> AIResponse:
    template_id = str(data.get("template", "") or "").strip().lower()
    if not template_id:
        return error_response(
            "scaffold",
            "MISSING_TEMPLATE",
            "template обязателен",
            recovery="Сначала вызови templates_list и выбери template id.",
            suggestions=[
                Suggestion(
                    action="templates_list",
                    target="tasks_templates_list",
                    reason="Показать доступные шаблоны.",
                    priority="high",
                )
            ],
        )
    template = get_template(template_id)
    if not template:
        return error_response(
            "scaffold",
            "UNKNOWN_TEMPLATE",
            f"Неизвестный template: {template_id}",
            recovery="Вызови templates_list и выбери корректный template id.",
            suggestions=[
                Suggestion(
                    action="templates_list",
                    target="tasks_templates_list",
                    reason="Показать доступные шаблоны.",
                    priority="high",
                )
            ],
        )

    kind = str(data.get("kind", "") or "").strip().lower()
    if kind not in {"plan", "task"}:
        return error_response(
            "scaffold",
            "INVALID_KIND",
            "kind должен быть 'plan' или 'task'",
            recovery="Передай kind=plan|task явно (без угадываний).",
            result={"kind": kind, "template": template.to_dict()},
        )

    title = str(data.get("title", "") or "").strip()
    if not title:
        return error_response("scaffold", "MISSING_TITLE", "title обязателен")
    title_err = validate_string(title, "title")
    if title_err:
        return error_response("scaffold", "INVALID_TITLE", title_err)

    dry_run = bool(data.get("dry_run", True))
    priority = str(data.get("priority", "MEDIUM") or "MEDIUM")

    used_focus_parent = False
    parent_source: str = "explicit"
    parent: Optional[str] = data.get("parent")
    if parent is not None:
        err = validate_task_id(parent)
        if err:
            return error_response("scaffold", "INVALID_PARENT", err, result={"parent": parent})
        parent = str(parent)

    if kind == "task":
        if not parent:
            last_id, _last_domain = get_last_task()
            focus_id: Optional[str] = None
            if last_id:
                try:
                    focus_id = normalize_task_id(str(last_id))
                except Exception:
                    focus_id = str(last_id).strip() or None
            if focus_id and focus_id.startswith("PLAN-"):
                parent = focus_id
                used_focus_parent = True
                parent_source = "focus_plan"
            elif focus_id and focus_id.startswith("TASK-"):
                focus_task = manager.load_task(focus_id, skip_sync=True)
                inferred = str(getattr(focus_task, "parent", "") or "").strip()
                if inferred.startswith("PLAN-"):
                    parent = inferred
                    used_focus_parent = True
                    parent_source = "focus_task_parent"

        if not parent:
            return error_response(
                "scaffold",
                "MISSING_PARENT",
                "Для kind=task нужен parent=PLAN-### (или focus на PLAN-###/TASK-### с parent).",
                recovery="Передай parent=PLAN-### явно или установи focus через focus_set.",
                suggestions=_missing_target_suggestions(manager, want="PLAN-"),
            )
        if not str(parent).startswith("PLAN-"):
            return error_response("scaffold", "INVALID_PARENT", "parent должен быть PLAN-###", result={"parent": parent})

        if template.task is None:
            return error_response(
                "scaffold",
                "UNSUPPORTED_KIND",
                f"template не поддерживает kind=task: {template.template_id}",
                result={"template": template.to_dict()},
            )
        try:
            task = build_task_from_template(manager, template, title=title, parent=str(parent), priority=priority)
        except ValueError as exc:
            msg = str(exc) or "Invalid parent"
            code = "PARENT_NOT_FOUND" if "not found" in msg.lower() else "INVALID_PARENT"
            return error_response(
                "scaffold",
                code,
                msg,
                recovery="Проверь parent через context(include_all=true) или установи focus на план.",
                suggestions=_missing_target_suggestions(manager, want="PLAN-"),
                result={"parent": str(parent)},
            )
        if dry_run:
            apply_preview_ids(task)
            return AIResponse(
                success=True,
                intent="scaffold",
                result={
                    "dry_run": True,
                    "would_execute": True,
                    "kind": "task",
                    "template": template.to_dict(),
                    "parent": str(parent),
                    "parent_source": parent_source,
                    "task_id": task.id,
                    "task": task_to_dict(task, include_steps=True, compact=False),
                },
                context={"used_focus_parent": used_focus_parent} if used_focus_parent else {},
                suggestions=[
                    Suggestion(
                        action="scaffold",
                        target="tasks_scaffold",
                        reason="Создать задание по шаблону (dry_run=false).",
                        priority="high",
                        params={
                            "template": template.template_id,
                            "kind": "task",
                            "title": title,
                            "parent": str(parent),
                            "priority": priority,
                            "dry_run": False,
                        },
                    )
                ],
            )

        if dict(getattr(task, "contract_data", {}) or {}) or str(getattr(task, "contract", "") or "").strip() or list(getattr(task, "success_criteria", []) or []):
            append_contract_version_if_changed(task, note="scaffold")
        manager.save_task(task, skip_sync=True)

        # Create-like operation: record without snapshots (undo is delete/restore).
        history = OperationHistory(storage_dir=Path(manager.tasks_dir))
        try:
            payload = dict(data)
            payload["created_id"] = task.id
            created_domain = str(getattr(task, "domain", "") or "")
            created_file = _task_file_for(manager, str(task.id), created_domain)
            op = history.record(
                intent="scaffold",
                task_id=task.id,
                data=payload,
                task_file=created_file,
                result=None,
                take_snapshot=False,
            )
        except Exception:
            op = None

        resp = AIResponse(
            success=True,
            intent="scaffold",
            result={
                "dry_run": False,
                "kind": "task",
                "template": template.to_dict(),
                "parent": str(parent),
                "parent_source": parent_source,
                "task_id": task.id,
                "task": task_to_dict(task, include_steps=True, compact=False),
            },
            context={"task_id": task.id, "used_focus_parent": used_focus_parent} if used_focus_parent else {"task_id": task.id},
            suggestions=[
                Suggestion(
                    action="focus_set",
                    target=str(task.id),
                    reason="Установить focus на созданное задание.",
                    priority="high",
                    params={"task": str(task.id), "domain": str(getattr(task, "domain", "") or "")},
                ),
                Suggestion(
                    action="radar",
                    target="tasks_radar",
                    reason="Открыть Radar View для нового задания.",
                    priority="high",
                    params={"task": str(task.id), "limit": 3},
                ),
                Suggestion(
                    action="lint",
                    target="tasks_lint",
                    reason="Предполётная проверка дисциплины (criteria/tests/atomicity/deps).",
                    priority="normal",
                    params={"task": str(task.id)},
                ),
            ],
        )
        if op and getattr(op, "id", None):
            resp.meta = dict(resp.meta or {})
            resp.meta["operation_id"] = str(op.id)
        return resp

    # kind == plan
    if template.plan is None:
        return error_response(
            "scaffold",
            "UNSUPPORTED_KIND",
            f"template не поддерживает kind=plan: {template.template_id}",
            result={"template": template.to_dict()},
        )
    plan = build_plan_from_template(manager, template, title=title, priority=priority)
    if dry_run:
        return AIResponse(
            success=True,
            intent="scaffold",
            result={
                "dry_run": True,
                "would_execute": True,
                "kind": "plan",
                "template": template.to_dict(),
                "plan_id": plan.id,
                "plan": plan_to_dict(plan, compact=False),
            },
            suggestions=[
                Suggestion(
                    action="scaffold",
                    target="tasks_scaffold",
                    reason="Создать план по шаблону (dry_run=false).",
                    priority="high",
                    params={"template": template.template_id, "kind": "plan", "title": title, "priority": priority, "dry_run": False},
                )
            ],
        )

    if dict(getattr(plan, "contract_data", {}) or {}) or str(getattr(plan, "contract", "") or "").strip() or list(getattr(plan, "success_criteria", []) or []):
        append_contract_version_if_changed(plan, note="scaffold")
    manager.save_task(plan, skip_sync=True)

    history = OperationHistory(storage_dir=Path(manager.tasks_dir))
    try:
        payload = dict(data)
        payload["created_id"] = plan.id
        created_domain = str(getattr(plan, "domain", "") or "")
        created_file = _task_file_for(manager, str(plan.id), created_domain)
        op = history.record(
            intent="scaffold",
            task_id=plan.id,
            data=payload,
            task_file=created_file,
            result=None,
            take_snapshot=False,
        )
    except Exception:
        op = None

    resp = AIResponse(
        success=True,
        intent="scaffold",
        result={
            "dry_run": False,
            "kind": "plan",
            "template": template.to_dict(),
            "plan_id": plan.id,
            "plan": plan_to_dict(plan, compact=False),
        },
        context={"task_id": plan.id},
        suggestions=[
            Suggestion(
                action="focus_set",
                target=str(plan.id),
                reason="Установить focus на созданный план.",
                priority="high",
                params={"task": str(plan.id), "domain": str(getattr(plan, "domain", "") or "")},
            ),
            Suggestion(
                action="radar",
                target="tasks_radar",
                reason="Открыть Radar View для плана (Now/Why/Verify/Next).",
                priority="high",
                params={"plan": str(plan.id), "limit": 3},
            ),
            Suggestion(
                action="scaffold",
                target="tasks_scaffold",
                reason="Создать первое задание под планом по шаблону (task).",
                priority="normal",
                params={"template": template.template_id, "kind": "task", "title": "<first task>", "parent": str(plan.id), "dry_run": True},
            ),
        ],
    )
    if op and getattr(op, "id", None):
        resp.meta = dict(resp.meta or {})
        resp.meta["operation_id"] = str(op.id)
    return resp


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
            recovery="Передай task=TASK-### (или task=PLAN-### с kind=plan|auto) либо установи focus через focus_set и передай его явно.",
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

    allowed = {"criteria", "tests", "security", "perf", "docs"}
    keys = set(checkpoints.keys())
    if not keys or not keys.issubset(allowed):
        return error_response(
            "verify",
            "INVALID_CHECKPOINTS",
            "Допустимо: checkpoints.criteria / checkpoints.tests / checkpoints.security / checkpoints.perf / checkpoints.docs",
        )

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
    detail_kind = str(getattr(task, "kind", "task") or "task")
    is_plan_detail = detail_kind == "plan"
    if not kind:
        kind = "plan" if is_plan_detail else "step"
    if kind == "auto":
        kind = "plan" if is_plan_detail else "step"
    if kind not in {"step", "task", "plan", "task_detail"}:
        return error_response("verify", "INVALID_KIND", "kind должен быть: step|task|plan|task_detail|auto")

    # For root PLAN-###, kind=plan targets plan checkpoints (no path required).
    # For nested plan nodes (within TASK-### step plans), kind=plan still requires a step path.
    checkpoint_target_kind = kind
    if kind == "plan" and is_plan_detail:
        checkpoint_target_kind = "task_detail"
    if kind in {"step", "task"} and detail_kind != "task":
        return error_response("verify", "NOT_A_TASK", "verify kind=step|task применим только к заданиям (TASK-###)")

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
    elif kind == "plan" and not is_plan_detail:
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

    checks_raw = data.get("checks") or data.get("verification_checks")
    attachments_raw = data.get("attachments")
    verification_outcome = data.get("verification_outcome")
    if (checks_raw is not None or verification_outcome is not None) and kind != "step":
        return error_response("verify", "INVALID_TARGET", "checks/verification_outcome доступны только для шагов")

    # Strict: verify is confirmation-only.
    # Every provided checkpoint entry must include confirmed=true, otherwise this is a NOOP/FAILED call and must not mutate state.
    for name in sorted(keys):
        item = checkpoints.get(name) or {}
        if not isinstance(item, dict):
            return error_response("verify", "INVALID_CHECKPOINTS", f"checkpoints.{name} должен быть объектом")
        if item.get("confirmed", None) is not True:
            return error_response(
                "verify",
                "VERIFY_NOOP",
                f"checkpoints.{name}.confirmed должен быть true",
                recovery="verify не поддерживает сброс/\"холостую\" верификацию. Передай confirmed:true для подтверждения.",
                result={"task": task_id, "checkpoint": name},
            )

    def _checkpoint_snapshot(target: Any) -> Dict[str, Any]:
        return _checkpoint_snapshot_for_node(target)

    def _locate_target(detail: TaskDetail, *, kind_key: str, path_value: Optional[str]) -> Optional[Any]:
        if kind_key == "task_detail":
            return detail
        if kind_key == "step":
            if not path_value:
                return None
            st0, _, _ = _find_step_by_path(list(getattr(detail, "steps", []) or []), path_value)
            return st0
        if kind_key == "plan":
            if not path_value:
                return None
            st0, _, _ = _find_step_by_path(list(getattr(detail, "steps", []) or []), path_value)
            return getattr(st0, "plan", None) if st0 else None
        if kind_key == "task":
            if not path_value:
                return None
            node0, _, _ = _find_task_by_path(list(getattr(detail, "steps", []) or []), path_value)
            return node0
        return None

    before_target = _locate_target(task, kind_key=checkpoint_target_kind, path_value=path)
    checkpoints_before = _checkpoint_snapshot(before_target) if before_target is not None else None

    for name in sorted(keys):
        item = checkpoints.get(name) or {}
        note = str(item.get("note", "") or "").strip()
        ok, msg = manager.update_checkpoint(
            task_id,
            kind=checkpoint_target_kind,
            checkpoint=name,
            value=True,
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
    any_confirmed = True

    updated = manager.load_task(task_id, task.domain, skip_sync=True)
    st = None
    if kind == "step" and path:
        st, _, _ = _find_step_by_path((updated or task).steps, path)
    if st and kind == "step":
        confirmed_keys = sorted(keys)

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

        def _extend_unique_evidence_refs(target: Any, attr: str, digests: List[str]) -> int:
            current = getattr(target, attr, None)
            if not isinstance(current, list):
                current = []
                setattr(target, attr, current)
            existing = {str(v or "").strip() for v in list(current or []) if str(v or "").strip()}
            added = 0
            for raw in digests:
                val = str(raw or "").strip()
                if not val or val in existing:
                    continue
                current.append(val)
                existing.add(val)
                added += 1
            return added

        needs_save = False
        evidence_digests: List[str] = []
        if checks_raw is not None:
            try:
                normalized_checks = _normalize_checks_payload(checks_raw)
            except ValueError as exc:
                return error_response("verify", "INVALID_CHECKS", str(exc))
            try:
                parsed_checks = [VerificationCheck.from_dict(c) for c in normalized_checks]
            except Exception:
                return error_response("verify", "INVALID_CHECKS", "checks содержит некорректные элементы")
            if _extend_unique_checks(parsed_checks):
                needs_save = True
            evidence_digests.extend([str(getattr(c, "digest", "") or "").strip() for c in parsed_checks if str(getattr(c, "digest", "") or "").strip()])
        if attachments_raw is not None:
            try:
                normalized_attachments = _normalize_attachments_payload(attachments_raw)
            except ValueError as exc:
                return error_response("verify", "INVALID_ATTACHMENTS", str(exc))
            try:
                parsed_attachments = [Attachment.from_dict(a) for a in normalized_attachments]
            except Exception:
                return error_response("verify", "INVALID_ATTACHMENTS", "attachments содержит некорректные элементы")
            if _extend_unique_attachments(parsed_attachments):
                needs_save = True
            evidence_digests.extend(
                [str(getattr(a, "digest", "") or "").strip() for a in parsed_attachments if str(getattr(a, "digest", "") or "").strip()]
            )
        if verification_outcome is not None:
            st.verification_outcome = str(verification_outcome or "").strip()
            needs_save = True

        # Best-effort: auto-evidence for verified checkpoints (CI + git).
        if any_confirmed:
            try:
                auto_checks = collect_auto_verification_checks(resolve_project_root())
                auto_checks_list = list(auto_checks or [])
                if auto_checks_list and _extend_unique_checks(auto_checks_list):
                    needs_save = True
                evidence_digests.extend(
                    [str(getattr(c, "digest", "") or "").strip() for c in auto_checks_list if str(getattr(c, "digest", "") or "").strip()]
                )
            except Exception:
                pass

            # Link any already captured evidence to this verification (golden path: capture → verify).
            existing_checks = list(getattr(st, "verification_checks", []) or [])
            existing_attachments = list(getattr(st, "attachments", []) or [])
            evidence_digests.extend(
                [str(getattr(c, "digest", "") or "").strip() for c in existing_checks if str(getattr(c, "digest", "") or "").strip()]
            )
            evidence_digests.extend(
                [str(getattr(a, "digest", "") or "").strip() for a in existing_attachments if str(getattr(a, "digest", "") or "").strip()]
            )

        # Tie evidence to the checkpoints confirmed by this call (evidence-first traceability).
        if evidence_digests:
            evidence_digests = _dedupe_strs(evidence_digests)
            refs_map = {
                "criteria": "criteria_evidence_refs",
                "tests": "tests_evidence_refs",
                "security": "security_evidence_refs",
                "perf": "perf_evidence_refs",
                "docs": "docs_evidence_refs",
            }
            for ck in confirmed_keys:
                attr = refs_map.get(str(ck or "").strip().lower())
                if not attr:
                    continue
                if _extend_unique_evidence_refs(st, attr, evidence_digests):
                    needs_save = True

        if needs_save and updated:
            manager.save_task(updated, skip_sync=True)
            updated = manager.load_task(task_id, task.domain, skip_sync=True) or updated
            if path:
                st, _, _ = _find_step_by_path((updated or task).steps, path)

    # Attach evidence/evidence_refs to non-step targets (plan/task/task_detail) when attachments are provided.
    if kind != "step" and attachments_raw is not None and updated:
        try:
            normalized_attachments = _normalize_attachments_payload(attachments_raw)
        except ValueError as exc:
            return error_response("verify", "INVALID_ATTACHMENTS", str(exc))
        try:
            parsed_attachments = [Attachment.from_dict(a) for a in normalized_attachments]
        except Exception:
            return error_response("verify", "INVALID_ATTACHMENTS", "attachments содержит некорректные элементы")
        if parsed_attachments:
            after_target0 = _locate_target(updated, kind_key=checkpoint_target_kind, path_value=path)
            if after_target0 is not None:
                current_atts = getattr(after_target0, "attachments", None)
                if not isinstance(current_atts, list):
                    current_atts = []
                    setattr(after_target0, "attachments", current_atts)
                existing = {str(getattr(x, "digest", "") or "").strip() for x in current_atts if getattr(x, "digest", "")}
                changed = False
                evidence_digests = []
                for att in parsed_attachments:
                    digest = str(getattr(att, "digest", "") or "").strip()
                    if digest and digest in existing:
                        continue
                    current_atts.append(att)
                    changed = True
                    if digest:
                        existing.add(digest)
                        evidence_digests.append(digest)
                if evidence_digests:
                    evidence_digests = _dedupe_strs(evidence_digests)
                    refs_map = {
                        "criteria": "criteria_evidence_refs",
                        "tests": "tests_evidence_refs",
                        "security": "security_evidence_refs",
                        "perf": "perf_evidence_refs",
                        "docs": "docs_evidence_refs",
                    }
                    for ck in sorted(keys):
                        attr = refs_map.get(str(ck or "").strip().lower())
                        if not attr:
                            continue
                        lst = getattr(after_target0, attr, None)
                        if not isinstance(lst, list):
                            lst = []
                            setattr(after_target0, attr, lst)
                        before = set(str(v or "").strip() for v in lst if str(v or "").strip())
                        for d in evidence_digests:
                            if d and d not in before:
                                lst.append(d)
                                before.add(d)
                                changed = True
                if changed:
                    manager.save_task(updated, skip_sync=True)
                    updated = manager.load_task(task_id, task.domain, skip_sync=True) or updated

    after_target = _locate_target(updated or task, kind_key=checkpoint_target_kind, path_value=path) if (updated or task) else None
    checkpoints_after = _checkpoint_snapshot(after_target) if after_target is not None else None

    ready: Optional[bool] = None
    needs: Optional[List[str]] = None
    if st and kind == "step":
        ready = bool(st.ready_for_completion())
        needs = [] if ready else _step_needs_for_completion(st)
    payload_key = "plan" if getattr(updated or task, "kind", "task") == "plan" else "task"
    return AIResponse(
        success=True,
        intent="verify",
        result={
            "task_id": task_id,
            "path": path,
            "kind": kind,
            "checkpoints_before": checkpoints_before,
            "checkpoints_after": checkpoints_after,
            "ready": ready,
            "needs": needs,
            "step": step_to_dict(st, path=path, compact=False) if st else None,
            payload_key: plan_to_dict(updated or task, compact=False)
            if payload_key == "plan"
            else task_to_dict(updated or task, include_steps=True, compact=False),
        },
        context={"task_id": task_id},
    )


def handle_evidence_capture(manager: TaskManager, data: Dict[str, Any]) -> AIResponse:
    """Capture evidence artifacts (cmd_output/url/diff) and attach them to a step.

    Unlike `verify`, this intent does NOT confirm checkpoints. It only appends evidence
    (attachments/checks) in a safe-by-default, redacted form.
    """
    task_id = data.get("task")
    if not task_id:
        return error_response(
            "evidence_capture",
            "MISSING_TASK",
            "task обязателен",
            recovery="Передай task=TASK-### или установи focus через focus_set.",
            suggestions=_missing_target_suggestions(manager, want="TASK-"),
        )
    err = validate_task_id(task_id)
    if err:
        return error_response(
            "evidence_capture",
            "INVALID_TASK",
            err,
            recovery="Проверь id через context(include_all=true).",
            suggestions=_missing_target_suggestions(manager, want="TASK-"),
        )
    task_id = str(task_id)

    task = manager.load_task(task_id, skip_sync=True)
    if not task:
        return error_response(
            "evidence_capture",
            "NOT_FOUND",
            f"Не найдено: {task_id}",
            recovery="Проверь id через context(include_all=true).",
            suggestions=_missing_target_suggestions(manager, want="TASK-"),
            result={"task": task_id},
        )
    if getattr(task, "kind", "task") != "task":
        return error_response("evidence_capture", "NOT_A_TASK", "evidence_capture применим только к заданиям (TASK-###)")

    path, path_err = _resolve_step_path(manager, task, data)
    if path_err:
        code, message = path_err
        return error_response(
            "evidence_capture",
            code,
            message,
            recovery="Возьми корректный path/step_id через radar/mirror.",
            suggestions=_path_help_suggestions(task_id),
        )

    st, _, _ = _find_step_by_path(task.steps, path)
    if not st:
        return error_response(
            "evidence_capture",
            "PATH_NOT_FOUND",
            f"Шаг path={path} не найден",
            recovery="Возьми корректный path/step_id через radar/mirror.",
            suggestions=_path_help_suggestions(task_id),
            result={"task_id": task_id, "path": path},
        )

    artifacts_raw = data.get("artifacts")
    if artifacts_raw is None and isinstance(data.get("items"), list):
        artifacts_raw = data.get("items")
    attachments_raw = data.get("attachments")
    checks_raw = data.get("checks") or data.get("verification_checks")
    verification_outcome = data.get("verification_outcome")

    if artifacts_raw is None and attachments_raw is None and checks_raw is None and verification_outcome is None:
        return error_response(
            "evidence_capture",
            "MISSING_EVIDENCE",
            "Нужно передать хотя бы одно из: artifacts|attachments|checks",
            recovery="Передай artifacts для cmd_output/url/diff или attachments/checks/verification_outcome как в verify (без confirmed).",
            suggestions=_path_help_suggestions(task_id),
            result={"task_id": task_id, "path": path},
        )

    # Parse checks (optional)
    checks_added: List[Dict[str, Any]] = []
    if checks_raw is not None:
        try:
            normalized_checks = _normalize_checks_payload(checks_raw)
        except ValueError as exc:
            return error_response("evidence_capture", "INVALID_CHECKS", str(exc))
        try:
            parsed_checks = [VerificationCheck.from_dict(c) for c in normalized_checks]
        except Exception:
            return error_response("evidence_capture", "INVALID_CHECKS", "checks содержит некорректные элементы")
        existing = {str(getattr(x, "digest", "") or "").strip() for x in (st.verification_checks or []) if getattr(x, "digest", "")}
        for check in parsed_checks:
            digest = str(getattr(check, "digest", "") or "").strip()
            if digest and digest in existing:
                continue
            st.verification_checks.append(check)
            if digest:
                existing.add(digest)
            checks_added.append(check.to_dict())

    # Parse plain attachments (optional, already-referenced)
    attachments_added: List[Dict[str, Any]] = []
    if attachments_raw is not None:
        try:
            normalized_attachments = _normalize_attachments_payload(attachments_raw)
        except ValueError as exc:
            return error_response("evidence_capture", "INVALID_ATTACHMENTS", str(exc))
        try:
            parsed_attachments = [Attachment.from_dict(a) for a in normalized_attachments]
        except Exception:
            return error_response("evidence_capture", "INVALID_ATTACHMENTS", "attachments содержит некорректные элементы")
        existing = {str(getattr(x, "digest", "") or "").strip() for x in (st.attachments or []) if getattr(x, "digest", "")}
        for att in parsed_attachments:
            digest = str(getattr(att, "digest", "") or "").strip()
            if digest and digest in existing:
                continue
            st.attachments.append(att)
            if digest:
                existing.add(digest)
            attachments_added.append(att.to_dict())

    # Capture artifacts (optional): create attachment + store blob in tasks_dir/.artifacts when needed.
    artifacts_written: List[Dict[str, Any]] = []
    if artifacts_raw is not None:
        if not isinstance(artifacts_raw, list):
            return error_response("evidence_capture", "INVALID_ARTIFACTS", "artifacts должен быть массивом")
        if len(artifacts_raw) > MAX_EVIDENCE_ITEMS:
            return error_response(
                "evidence_capture",
                "TOO_MANY_ARTIFACTS",
                f"artifacts слишком большой (max {MAX_EVIDENCE_ITEMS})",
            )
        existing = {str(getattr(x, "digest", "") or "").strip() for x in (st.attachments or []) if getattr(x, "digest", "")}
        for item in artifacts_raw:
            if not isinstance(item, dict):
                return error_response("evidence_capture", "INVALID_ARTIFACTS", "artifacts должен содержать объекты")
            kind = str(item.get("kind", "") or "").strip()
            meta_in = item.get("meta")
            meta_value: Dict[str, Any] = dict(meta_in) if isinstance(meta_in, dict) else {}

            if kind == "url":
                url = str(item.get("url", "") or item.get("external_uri", "") or "").strip()
                if not url:
                    return error_response("evidence_capture", "MISSING_URL", "url обязателен", result={"artifact": item})
                att = Attachment.from_dict({"kind": "url", "external_uri": url, "meta": meta_value})
                digest = str(getattr(att, "digest", "") or "").strip()
                if digest and digest in existing:
                    continue
                st.attachments.append(att)
                if digest:
                    existing.add(digest)
                attachments_added.append(att.to_dict())
                continue

            if kind == "diff":
                diff_text = str(item.get("diff", "") or item.get("content", "") or "").strip()
                if not diff_text:
                    return error_response("evidence_capture", "MISSING_DIFF", "diff обязателен", result={"artifact": item})
                redacted = redact_text(diff_text)
                truncated_text, truncated, original_size = _truncate_utf8(redacted, max_bytes=MAX_ARTIFACT_BYTES)
                blob = truncated_text.encode("utf-8")
                uri, size, sha = write_artifact(Path(manager.tasks_dir), content=blob, ext="patch")
                meta_value.update({"artifact_sha256": sha, "truncated": truncated, "original_size": original_size})
                att = Attachment.from_dict({"kind": "diff", "uri": uri, "size": size, "meta": meta_value})
                digest = str(getattr(att, "digest", "") or "").strip()
                if digest and digest in existing:
                    continue
                st.attachments.append(att)
                if digest:
                    existing.add(digest)
                attachments_added.append(att.to_dict())
                artifacts_written.append({"kind": "diff", "uri": uri, "size": size, "sha256": sha, "truncated": truncated, "original_size": original_size})
                continue

            if kind == "cmd_output":
                command = str(item.get("command", "") or "").strip()
                stdout = str(item.get("stdout", "") or item.get("output", "") or "")
                stderr = str(item.get("stderr", "") or "")
                exit_code = item.get("exit_code", None)
                if not (command or stdout or stderr):
                    return error_response("evidence_capture", "MISSING_OUTPUT", "cmd_output требует command и/или stdout/stderr", result={"artifact": item})
                payload = {
                    "command": command,
                    "exit_code": exit_code,
                    "stdout": stdout,
                    "stderr": stderr,
                    "meta": meta_value,
                }
                safe_payload = redact(payload)
                text = json.dumps(safe_payload, ensure_ascii=False, sort_keys=True, indent=2)
                truncated_text, truncated, original_size = _truncate_utf8(text, max_bytes=MAX_ARTIFACT_BYTES)
                blob = truncated_text.encode("utf-8")
                uri, size, sha = write_artifact(Path(manager.tasks_dir), content=blob, ext="json")
                safe_command = redact_text(command)
                meta_value.update(
                    {
                        "artifact_sha256": sha,
                        "command": safe_command,
                        "exit_code": exit_code,
                        "truncated": truncated,
                        "original_size": original_size,
                    }
                )
                att = Attachment.from_dict({"kind": "cmd_output", "uri": uri, "size": size, "meta": meta_value})
                digest = str(getattr(att, "digest", "") or "").strip()
                if digest and digest in existing:
                    continue
                st.attachments.append(att)
                if digest:
                    existing.add(digest)
                attachments_added.append(att.to_dict())
                artifacts_written.append(
                    {"kind": "cmd_output", "uri": uri, "size": size, "sha256": sha, "truncated": truncated, "original_size": original_size}
                )
                continue

            return error_response(
                "evidence_capture",
                "INVALID_ARTIFACT_KIND",
                f"Неизвестный artifact.kind: {kind}",
                recovery="kind должен быть одним из: cmd_output|diff|url",
                result={"artifact": item},
            )

    outcome_updated = False
    if verification_outcome is not None:
        st.verification_outcome = str(verification_outcome or "").strip()
        outcome_updated = True

    if checks_added or attachments_added or artifacts_written or outcome_updated:
        manager.save_task(task, skip_sync=True)

    updated = manager.load_task(task_id, task.domain, skip_sync=True) or task
    st1, _, _ = _find_step_by_path((updated or task).steps, path)
    return AIResponse(
        success=True,
        intent="evidence_capture",
        result={
            "task_id": task_id,
            "path": path,
            "captured": {
                "artifacts_written": artifacts_written,
                "attachments_added": attachments_added,
                "checks_added": checks_added,
                "verification_outcome": str(verification_outcome or "").strip() if verification_outcome is not None else None,
            },
            "step": step_to_dict(st1, path=path, compact=False) if st1 else None,
            "task": task_to_dict(updated or task, include_steps=True, compact=False),
        },
        context={"task_id": task_id},
    )


def _handle_close_step_like(manager: TaskManager, data: Dict[str, Any], *, intent_name: str) -> AIResponse:
    """Atomic verify(step) -> done(step) in a single call."""
    task_id = data.get("task")
    if not task_id:
        return error_response(
            intent_name,
            "MISSING_TASK",
            "task обязателен",
            recovery="Передай task=TASK-### или установи focus через focus_set и передай его явно.",
            suggestions=_missing_target_suggestions(manager, want="TASK-"),
        )
    err = validate_task_id(task_id)
    if err:
        return error_response(
            intent_name,
            "INVALID_TASK",
            err,
            recovery="Проверь id через context(include_all=true).",
            suggestions=_missing_target_suggestions(manager, want="TASK-"),
        )
    task_id = str(task_id)

    force = bool(data.get("force", False))
    override_reason = str(data.get("override_reason", "") or "").strip()
    if force and not override_reason:
        return error_response(intent_name, "MISSING_OVERRIDE_REASON", "override_reason обязателен при force=true")
    note = str(data.get("note", "") or "").strip()

    checkpoints = data.get("checkpoints")
    if checkpoints is None:
        return error_response(
            intent_name,
            "MISSING_CHECKPOINTS",
            "checkpoints обязателен",
            recovery="Передай checkpoints.criteria/tests с confirmed:true (как в verify).",
            suggestions=_path_help_suggestions(task_id),
            result={"task": task_id},
        )

    verify_payload = dict(data or {})
    verify_payload["task"] = task_id
    verify_payload["kind"] = "step"
    verify_resp = handle_verify(manager, verify_payload)
    if not verify_resp.success:
        # Preserve original error code and suggestions, but make the intent explicit.
        return AIResponse(
            success=False,
            intent=intent_name,
            result=dict(verify_resp.result or {}),
            context=dict(verify_resp.context or {}),
            suggestions=list(verify_resp.suggestions or []),
            warnings=list(verify_resp.warnings or []),
            meta=dict(verify_resp.meta or {}),
            error_code=str(verify_resp.error_code or "FAILED"),
            error_message=str(verify_resp.error_message or "verify failed"),
            error_recovery=verify_resp.error_recovery,
        )

    path = str((verify_resp.result or {}).get("path") or "").strip()
    if not path:
        return error_response(intent_name, "INVALID_PATH", "path обязателен", result={"task": task_id})

    task = manager.load_task(task_id, skip_sync=True)
    if not task:
        return error_response(
            intent_name,
            "NOT_FOUND",
            f"Не найдено: {task_id}",
            recovery="Проверь id через context(include_all=true).",
            suggestions=_missing_target_suggestions(manager, want="TASK-"),
            result={"task": task_id},
        )
    if getattr(task, "kind", "task") != "task":
        return error_response(intent_name, "NOT_A_TASK", f"{intent_name} применим только к заданиям (TASK-###)")

    st0, _, _ = _find_step_by_path(task.steps, path)
    if not st0:
        return error_response(
            intent_name,
            "PATH_NOT_FOUND",
            f"Шаг path={path} не найден",
            recovery="Возьми корректный path/step_id через radar/mirror.",
            suggestions=_path_help_suggestions(task_id),
            result={"task": task_id, "path": path},
        )

    checkpoints_before = (verify_resp.result or {}).get("checkpoints_before") or _checkpoint_snapshot_for_node(st0)
    checkpoints_after = (verify_resp.result or {}).get("checkpoints_after") or _checkpoint_snapshot_for_node(st0)

    ready = bool(st0.ready_for_completion())
    needs = _step_needs_for_completion(st0) if not ready else []
    confirmable = {"criteria", "tests", "security", "perf", "docs"}
    missing_checkpoints = [n for n in needs if n in confirmable]
    if not ready and not force:
        return error_response(
            intent_name,
            "GATING_FAILED",
            f"Нельзя завершить шаг path={path}: требуется {', '.join(needs) if needs else 'готовность'}",
            recovery="Подтверди недостающие чекпоинты через verify или заверши вложенные задачи плана, затем повтори операцию.",
            suggestions=_path_help_suggestions(task_id),
            result={
                "task_id": task_id,
                "path": path,
                "ready": ready,
                "needs": needs,
                "missing_checkpoints": missing_checkpoints,
                "checkpoints_before": checkpoints_before,
                "checkpoints_after": checkpoints_after,
                "step": step_to_dict(st0, path=path, compact=False),
            },
        )

    if note:
        manager.add_step_progress_note(task_id, path=path, note=note, domain=task.domain)

    ok, msg = manager.set_step_completed(task_id, 0, True, task.domain, path=path, force=force)
    if not ok:
        mapping = {"not_found": "NOT_FOUND", "index": "PATH_NOT_FOUND"}
        code = mapping.get(msg or "", "FAILED")
        updated = manager.load_task(task_id, task.domain, skip_sync=True) or task
        st1, _, _ = _find_step_by_path(updated.steps, path)
        if not force and st1 and not bool(st1.ready_for_completion()):
            needs1 = _step_needs_for_completion(st1)
            confirmable = {"criteria", "tests", "security", "perf", "docs"}
            missing1 = [n for n in needs1 if n in confirmable]
            return error_response(
                intent_name,
                "GATING_FAILED",
                f"Нельзя завершить шаг path={path}: требуется {', '.join(needs1) if needs1 else 'готовность'}",
                recovery="Подтверди недостающие чекпоинты через verify или заверши вложенные задачи плана, затем повтори операцию.",
                suggestions=_path_help_suggestions(task_id),
                result={
                    "task_id": task_id,
                    "path": path,
                    "ready": False,
                    "needs": needs1,
                    "missing_checkpoints": missing1,
                    "checkpoints_before": checkpoints_before,
                    "checkpoints_after": _checkpoint_snapshot_for_node(st1),
                    "step": step_to_dict(st1, path=path, compact=False),
                },
            )
        return error_response(intent_name, code, msg or "Не удалось завершить шаг", result={"task": task_id, "path": path})

    updated = manager.load_task(task_id, task.domain, skip_sync=True) or task
    st, _, _ = _find_step_by_path(updated.steps, path)
    if force and override_reason and updated:
        try:
            updated.events.append(StepEvent.override(intent_name, override_reason, target=f"step:{path}"))
            manager.save_task(updated, skip_sync=True)
        except Exception:
            pass
    checkpoints_final = _checkpoint_snapshot_for_node(st) if st else checkpoints_after
    payload = {
        "task_id": task_id,
        "path": path,
        "completed": True,
        "checkpoints_before": checkpoints_before,
        "checkpoints_after": checkpoints_final,
        "ready": True,
        "needs": [],
        "task": task_to_dict(updated, include_steps=True, compact=False),
        "step": step_to_dict(st, path=path, compact=False) if st else None,
    }
    return AIResponse(success=True, intent=intent_name, result=payload, context={"task_id": task_id})


def handle_close_step(manager: TaskManager, data: Dict[str, Any]) -> AIResponse:
    return _handle_close_step_like(manager, data, intent_name="close_step")


def handle_progress(manager: TaskManager, data: Dict[str, Any]) -> AIResponse:
    task_id = data.get("task")
    if not task_id:
        return error_response(
            "progress",
            "MISSING_TASK",
            "task обязателен",
            recovery="Передай task=TASK-### или установи focus через focus_set.",
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

    st0, _, _ = _find_step_by_path(task.steps, path)
    if not st0:
        return error_response(
            "progress",
            "PATH_NOT_FOUND",
            f"Шаг path={path} не найден",
            recovery="Возьми корректный path/step_id через radar/mirror.",
            suggestions=_path_help_suggestions(task_id),
            result={"task_id": task_id, "path": path},
        )

    checkpoints_before = _checkpoint_snapshot_for_node(st0)
    if completed and not force and not bool(st0.ready_for_completion()):
        needs0 = _step_needs_for_completion(st0)
        confirmable = {"criteria", "tests", "security", "perf", "docs"}
        missing0 = [n for n in needs0 if n in confirmable]
        return error_response(
            "progress",
            "GATING_FAILED",
            f"Нельзя завершить шаг path={path}: требуется {', '.join(needs0) if needs0 else 'готовность'}",
            recovery="Подтверди недостающие чекпоинты через verify или заверши вложенные задачи плана, затем повтори операцию.",
            suggestions=_path_help_suggestions(task_id),
            result={
                "task_id": task_id,
                "path": path,
                "ready": False,
                "needs": needs0,
                "missing_checkpoints": missing0,
                "checkpoints_before": checkpoints_before,
                "checkpoints_after": checkpoints_before,
                "step": step_to_dict(st0, path=path, compact=False),
            },
        )

    ok, msg = manager.set_step_completed(task_id, 0, completed, task.domain, path=path, force=force)
    if not ok:
        mapping = {"not_found": "NOT_FOUND", "index": "PATH_NOT_FOUND"}
        code = mapping.get(msg or "", "FAILED")
        updated = manager.load_task(task_id, task.domain, skip_sync=True) or task
        st1, _, _ = _find_step_by_path(updated.steps, path)
        if completed and not force and st1 and not bool(st1.ready_for_completion()):
            needs1 = _step_needs_for_completion(st1)
            confirmable = {"criteria", "tests", "security", "perf", "docs"}
            missing1 = [n for n in needs1 if n in confirmable]
            return error_response(
                "progress",
                "GATING_FAILED",
                f"Нельзя завершить шаг path={path}: требуется {', '.join(needs1) if needs1 else 'готовность'}",
                recovery="Подтверди недостающие чекпоинты через verify или заверши вложенные задачи плана, затем повтори операцию.",
                suggestions=_path_help_suggestions(task_id),
                result={
                    "task_id": task_id,
                    "path": path,
                    "ready": False,
                    "needs": needs1,
                    "missing_checkpoints": missing1,
                    "checkpoints_before": checkpoints_before,
                    "checkpoints_after": _checkpoint_snapshot_for_node(st1),
                    "step": step_to_dict(st1, path=path, compact=False),
                },
            )
        return error_response("progress", code, msg or "Не удалось обновить completed", result={"task": task_id, "path": path})

    updated = manager.load_task(task_id, task.domain, skip_sync=True)
    st, _, _ = _find_step_by_path((updated or task).steps, path)
    if force and override_reason and updated:
        try:
            updated.events.append(StepEvent.override("progress", override_reason, target=f"step:{path}"))
            manager.save_task(updated, skip_sync=True)
        except Exception:
            pass
    checkpoints_after = _checkpoint_snapshot_for_node(st) if st else checkpoints_before
    ready = bool(st.ready_for_completion()) if st else None
    needs = [] if ready else _step_needs_for_completion(st) if st else None
    return AIResponse(
        success=True,
        intent="progress",
        result={
            "task_id": task_id,
            "path": path,
            "completed": completed,
            "checkpoints_before": checkpoints_before,
            "checkpoints_after": checkpoints_after,
            "ready": ready,
            "needs": needs,
            "step": step_to_dict(st, path=path, compact=False) if st else None,
            "task": task_to_dict(updated or task, include_steps=True, compact=False),
        },
        context={"task_id": task_id},
    )


def handle_done(manager: TaskManager, data: Dict[str, Any]) -> AIResponse:
    if bool(data.get("auto_verify", False)):
        return _handle_close_step_like(manager, data, intent_name="done")

    task_id = data.get("task")
    if not task_id:
        return error_response(
            "done",
            "MISSING_TASK",
            "task обязателен",
            recovery="Передай task=TASK-### или установи focus через focus_set.",
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

    st0, _, _ = _find_step_by_path(task.steps, path)
    if not st0:
        return error_response(
            "done",
            "PATH_NOT_FOUND",
            f"Шаг path={path} не найден",
            recovery="Возьми корректный path/step_id через radar/mirror.",
            suggestions=_path_help_suggestions(task_id),
            result={"task_id": task_id, "path": path},
        )

    checkpoints_before = _checkpoint_snapshot_for_node(st0)
    if not force and not bool(st0.ready_for_completion()):
        needs0 = _step_needs_for_completion(st0)
        missing0 = [n for n in needs0 if n in {"criteria", "tests"}]
        return error_response(
            "done",
            "GATING_FAILED",
            f"Нельзя завершить шаг path={path}: требуется {', '.join(needs0) if needs0 else 'готовность'}",
            recovery="Подтверди недостающие чекпоинты через verify или заверши вложенные задачи плана, затем повтори операцию.",
            suggestions=_path_help_suggestions(task_id),
            result={
                "task_id": task_id,
                "path": path,
                "ready": False,
                "needs": needs0,
                "missing_checkpoints": missing0,
                "checkpoints_before": checkpoints_before,
                "checkpoints_after": checkpoints_before,
                "step": step_to_dict(st0, path=path, compact=False),
            },
        )

    if note:
        manager.add_step_progress_note(task_id, path=path, note=note, domain=task.domain)
    ok, msg = manager.set_step_completed(task_id, 0, True, task.domain, path=path, force=force)
    if not ok:
        mapping = {"not_found": "NOT_FOUND", "index": "PATH_NOT_FOUND"}
        code = mapping.get(msg or "", "FAILED")
        updated = manager.load_task(task_id, task.domain, skip_sync=True) or task
        st1, _, _ = _find_step_by_path(updated.steps, path)
        if not force and st1 and not bool(st1.ready_for_completion()):
            needs1 = _step_needs_for_completion(st1)
            missing1 = [n for n in needs1 if n in {"criteria", "tests"}]
            return error_response(
                "done",
                "GATING_FAILED",
                f"Нельзя завершить шаг path={path}: требуется {', '.join(needs1) if needs1 else 'готовность'}",
                recovery="Подтверди недостающие чекпоинты через verify или заверши вложенные задачи плана, затем повтори операцию.",
                suggestions=_path_help_suggestions(task_id),
                result={
                    "task_id": task_id,
                    "path": path,
                    "ready": False,
                    "needs": needs1,
                    "missing_checkpoints": missing1,
                    "checkpoints_before": checkpoints_before,
                    "checkpoints_after": _checkpoint_snapshot_for_node(st1),
                    "step": step_to_dict(st1, path=path, compact=False),
                },
            )
        return error_response("done", code, msg or "Не удалось завершить шаг")

    updated = manager.load_task(task_id, task.domain, skip_sync=True)
    st, _, _ = _find_step_by_path((updated or task).steps, path)
    if force and override_reason and updated:
        try:
            updated.events.append(StepEvent.override("done", override_reason, target=f"step:{path}"))
            manager.save_task(updated, skip_sync=True)
        except Exception:
            pass
    checkpoints_after = _checkpoint_snapshot_for_node(st) if st else checkpoints_before
    ready = bool(st.ready_for_completion()) if st else None
    needs = [] if ready else _step_needs_for_completion(st) if st else None
    return AIResponse(
        success=True,
        intent="done",
        result={
            "task_id": task_id,
            "path": path,
            "completed": True,
            "checkpoints_before": checkpoints_before,
            "checkpoints_after": checkpoints_after,
            "ready": ready,
            "needs": needs,
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
    "required_checkpoints": "str_list",
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
    if status == "DONE" and not force:
        all_items = manager.repo.list("", skip_sync=True)
        report = lint_item(manager, detail, all_items)
        errors = [i.to_dict() for i in list(getattr(report, "issues", []) or []) if i.severity == "error"]
        if errors:
            return error_response(
                "complete",
                "LINT_ERRORS_BLOCKING",
                "Нельзя завершить: есть lint-ошибки",
                recovery="Исправь lint errors или используй force=true с override_reason.",
                result={"task": task_id, "lint": report.to_dict(), "blocking_errors": errors},
            )
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
    expected_revision, expected_err = _parse_expected_revision(data)
    if expected_err:
        return error_response(
            "batch",
            "INVALID_EXPECTED_REVISION",
            expected_err,
            recovery="Передай expected_revision как целое число (etag-like). Чтобы узнать текущую revision — вызови radar/resume.",
        )
    if default_task is not None:
        err = validate_task_id(default_task)
        if err:
            return error_response("batch", "INVALID_TASK", err)
        default_task = str(default_task)

    # Batch-level safe write defaults (optional).
    batch_expected_target_id = data.get("expected_target_id")
    batch_expected_kind = data.get("expected_kind")
    batch_strict_targeting = bool(data.get("strict_targeting", False))

    # Batch-level optimistic concurrency preflight applies to the default task only.
    if expected_revision is not None:
        if not default_task:
            return error_response(
                "batch",
                "MISSING_TASK_FOR_EXPECTED_REVISION",
                "task обязателен при expected_revision на batch",
                recovery="Передай task=TASK-###|PLAN-### на уровне batch, либо перенеси expected_revision в конкретную операцию.",
            )
        current_detail = manager.load_task(default_task, skip_sync=True)
        if current_detail:
            current_revision = int(getattr(current_detail, "revision", 0) or 0)
            if current_revision != int(expected_revision):
                return _revision_mismatch_response(
                    "batch",
                    task_id=default_task,
                    expected=int(expected_revision),
                    current=current_revision,
                )

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
        return AIResponse(
            success=True,
            intent="batch",
            result={"total": 0, "completed": 0, "results": [], "latest_id": initial_latest_id, "operation_ids": []},
        )

    ops = expanded_ops
    total = len(ops)

    completed = 0
    results: List[Dict[str, Any]] = []
    operation_ids: List[str] = []

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
            if batch_expected_target_id is not None and "expected_target_id" not in op_payload:
                op_payload["expected_target_id"] = batch_expected_target_id
            if batch_expected_kind is not None and "expected_kind" not in op_payload:
                op_payload["expected_kind"] = batch_expected_kind
            if batch_strict_targeting and "strict_targeting" not in op_payload:
                op_payload["strict_targeting"] = True
            resp = process_intent(manager, op_payload)
            if not resp.success:
                if atomic and backup_dir:
                    _restore_dir(backup_dir, Path(manager.tasks_dir))
                    latest_id = initial_latest_id
                    rolled_back = True
                else:
                    try:
                        history_now = OperationHistory(storage_dir=Path(manager.tasks_dir))
                        latest_id = history_now.operations[-1].id if history_now.operations else None
                    except Exception:
                        latest_id = None
                    rolled_back = False
                return AIResponse(
                    success=False,
                    intent="batch",
                    result={
                        "total": total,
                        "completed": completed,
                        "results": results,
                        "latest_id": latest_id,
                        "operation_ids": operation_ids,
                        "rolled_back": rolled_back,
                    },
                    error_code=resp.error_code or "BATCH_FAILED",
                    error_message=resp.error_message or "Batch failed",
                )
            results.append(resp.to_dict())
            op_id = str((resp.meta or {}).get("operation_id") or "").strip()
            if op_id:
                operation_ids.append(op_id)
            completed += 1
        try:
            history_after = OperationHistory(storage_dir=Path(manager.tasks_dir))
            latest_id = history_after.operations[-1].id if history_after.operations else None
        except Exception:
            latest_id = None
        return AIResponse(
            success=True,
            intent="batch",
            result={"total": total, "completed": completed, "results": results, "latest_id": latest_id, "operation_ids": operation_ids},
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
    include_details = bool(data.get("include_details", False))
    include_snapshot = bool(data.get("include_snapshot", False))

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
    has_more = bool(limit and len(sliced) > limit)
    sliced = sliced[:limit] if limit else []

    latest_id = ops[-1].id if ops else None
    snapshots_dir = Path(manager.tasks_dir) / ".snapshots"

    def _load_snapshot(snapshot_id: Optional[str]) -> Optional[str]:
        if not snapshot_id:
            return None
        try:
            path = snapshots_dir / f"{snapshot_id}.task"
            if not path.exists():
                return None
            return path.read_text(encoding="utf-8")
        except Exception:
            return None

    operations: List[Dict[str, Any]] = []
    for op in sliced:
        payload = op.to_dict() if include_details else op.to_summary_dict()
        if include_snapshot:
            payload["snapshot"] = {
                "before_id": op.snapshot_id,
                "after_id": op.after_snapshot_id,
                "before": _load_snapshot(op.snapshot_id),
                "after": _load_snapshot(op.after_snapshot_id),
            }
        operations.append(payload)

    return AIResponse(
        success=True,
        intent="delta",
        result={
            "since": since or None,
            "task": task_filter or None,
            "latest_id": latest_id,
            "include_details": include_details,
            "include_snapshot": include_snapshot,
            "operations": operations,
            "has_more": has_more,
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
    "handoff": handle_handoff,
    "context_pack": handle_context_pack,
    "resume": handle_resume,
    "lint": handle_lint,
    "templates_list": handle_templates_list,
    "scaffold": handle_scaffold,
    "create": handle_create,
    "decompose": handle_decompose,
    "task_add": handle_task_add,
    "task_define": handle_task_define,
    "task_delete": handle_task_delete,
    "define": handle_define,
    "verify": handle_verify,
    "evidence_capture": handle_evidence_capture,
    "done": handle_done,
    "close_step": handle_close_step,
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
    "evidence_capture",
    "done",
    "close_step",
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

    payload, ctx_add, early_error = _apply_focus_to_mutation(manager, intent=intent, data=data)
    if early_error:
        if ctx_add:
            early_error.context = dict(early_error.context or {})
            early_error.context.update(ctx_add)
        return early_error

    # Safe writes (explicit > focus): prevent silent mis-target when the caller expects a specific target.
    if intent in _FOCUSABLE_MUTATING_INTENTS:
        expected_target_alias = payload.get("expected_target", None)
        if expected_target_alias is not None:
            if payload.get("expected_target_id", None) is None:
                payload["expected_target_id"] = expected_target_alias
            else:
                left = str(payload.get("expected_target_id") or "").strip()
                right = str(expected_target_alias or "").strip()
                if left and right and left != right:
                    return error_response(
                        intent,
                        "EXPECTED_TARGET_MISMATCH",
                        f"expected_target={right} != expected_target_id={left}",
                        recovery="Оставь только expected_target_id либо задай совпадающее expected_target.",
                    )
        if "strict_writes" in payload:
            payload["strict_targeting"] = bool(payload.get("strict_targeting", False)) or bool(payload.get("strict_writes", False))

        target_resolution = (ctx_add or {}).get("target_resolution") if isinstance(ctx_add, dict) else {}
        source = str((target_resolution or {}).get("source") or "").strip()
        resolved_target_id = payload.get("plan") if payload.get("plan") is not None else payload.get("task")
        resolved_target_id = str(resolved_target_id or "").strip() or None
        resolved_kind: Optional[str] = None
        if resolved_target_id:
            if resolved_target_id.startswith("PLAN-"):
                resolved_kind = "plan"
            elif resolved_target_id.startswith("TASK-"):
                resolved_kind = "task"

        if source and source != "explicit" and "strict_targeting" not in payload and "strict_writes" not in payload:
            auto_required, active_count = _auto_strict_writes_required(manager)
            if auto_required:
                payload["strict_targeting"] = True
                ctx_add = dict(ctx_add or {})
                ctx_add.setdefault("strict_writes_auto", True)
                ctx_add.setdefault("strict_writes_reason", "multiple_active_targets")
                ctx_add.setdefault("strict_writes_active_count", int(active_count))

        expected_target_id = payload.get("expected_target_id", None)
        if expected_target_id is not None:
            err = validate_node_id(expected_target_id, "expected_target_id")
            if err:
                return error_response(intent, "INVALID_EXPECTED_TARGET_ID", err)
            expected_target_id = str(expected_target_id).strip()
        expected_kind = payload.get("expected_kind", None)
        if expected_kind is not None:
            if not isinstance(expected_kind, str):
                return error_response(intent, "INVALID_EXPECTED_KIND", "expected_kind должен быть строкой (task|plan)")
            expected_kind = str(expected_kind or "").strip().lower()
            if expected_kind not in {"task", "plan"}:
                return error_response(intent, "INVALID_EXPECTED_KIND", "expected_kind должен быть task|plan")
        strict_targeting = bool(payload.get("strict_targeting", False))

        if strict_targeting and source and source != "explicit" and not expected_target_id:
            err_resp = error_response(
                intent,
                "STRICT_TARGETING_REQUIRES_EXPECTED_TARGET_ID",
                "expected_target_id обязателен при strict_targeting=true и использовании focus",
                recovery="Передай expected_target_id (и опционально expected_kind) либо адресуй операцию явно через task=/plan=.",
                suggestions=[
                    Suggestion(action="focus_get", target="focus_get", reason="Проверь текущий focus перед записью.", priority="high", params={}),
                    Suggestion(action="radar", target="tasks_radar", reason="Проверь, что focus указывает на нужную цель.", priority="normal", params={}),
                ],
                result={
                    "expected_target_id": expected_target_id,
                    "expected_kind": expected_kind,
                    "resolved_target_id": resolved_target_id,
                    "resolved_kind": resolved_kind,
                    "target_resolution": target_resolution,
                },
            )
            err_resp.context = dict(err_resp.context or {})
            err_resp.context.update(ctx_add or {})
            return err_resp

        if expected_target_id and resolved_target_id and resolved_target_id != expected_target_id:
            err_resp = error_response(
                intent,
                "EXPECTED_TARGET_MISMATCH",
                f"resolved_target_id={resolved_target_id} != expected_target_id={expected_target_id}",
                recovery="Исправь target (task=/plan=) или установи корректный focus через focus_set, затем повтори вызов.",
                suggestions=_missing_target_suggestions(manager, want=["TASK-", "PLAN-"]),
                result={
                    "expected_target_id": expected_target_id,
                    "expected_kind": expected_kind,
                    "resolved_target_id": resolved_target_id,
                    "resolved_kind": resolved_kind,
                    "target_resolution": target_resolution,
                },
            )
            err_resp.context = dict(err_resp.context or {})
            err_resp.context.update(ctx_add or {})
            return err_resp

        if expected_kind and resolved_kind and resolved_kind != expected_kind:
            err_resp = error_response(
                intent,
                "EXPECTED_TARGET_MISMATCH",
                f"resolved_kind={resolved_kind} != expected_kind={expected_kind}",
                recovery="Исправь target (task=/plan=) или установи корректный focus через focus_set, затем повтори вызов.",
                suggestions=_missing_target_suggestions(manager, want=["TASK-", "PLAN-"]),
                result={
                    "expected_target_id": expected_target_id,
                    "expected_kind": expected_kind,
                    "resolved_target_id": resolved_target_id,
                    "resolved_kind": resolved_kind,
                    "target_resolution": target_resolution,
                },
            )
            err_resp.context = dict(err_resp.context or {})
            err_resp.context.update(ctx_add or {})
            return err_resp

    # History tracking (undo/redo + delta): capture *before* snapshots for mutations.
    history = OperationHistory(storage_dir=Path(manager.tasks_dir))
    task_id = payload.get("task") or payload.get("plan")

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
    before_snapshot_id: Optional[str] = None
    if intent in _MUTATING_INTENTS and task_id:
        norm = validate_task_id(task_id)
        if norm is None:
            # Best-effort: domain might be present, otherwise infer from disk.
            domain = str(payload.get("domain", "") or "")
            if not domain:
                existing = manager.load_task(str(task_id), skip_sync=True)
                domain = str(getattr(existing, "domain", "") or "") if existing else ""
            task_file = _task_file_for(manager, str(task_id), domain)
            try:
                before_snapshot_id = history.snapshot(task_file)
            except Exception:
                before_snapshot_id = None

    try:
        resp = handler(manager, payload)
    except Exception as exc:  # pragma: no cover
        return error_response(intent, "INTERNAL_ERROR", f"internal error: {exc}")

    if ctx_add:
        resp.context = dict(resp.context or {})
        resp.context.update(ctx_add)

    if intent in _MUTATING_INTENTS and resp.success and not bool(payload.get("dry_run", False)):
        try:
            history_task_id = str(task_id) if task_id else None
            history_task_file = task_file
            history_payload = dict(payload)

            # create has no explicit target id; bind history to the created file so undo/redo works.
            if intent == "create":
                created_id = (resp.result or {}).get("task_id") or (resp.result or {}).get("plan_id")
                if created_id:
                    history_task_id = str(created_id)
                    history_payload["created_id"] = str(created_id)
                    created_detail = manager.load_task(str(created_id), skip_sync=True)
                    created_domain = str(getattr(created_detail, "domain", "") or "") if created_detail else ""
                    history_task_file = _task_file_for(manager, str(created_id), created_domain)

            op = history.record(
                intent=intent,
                task_id=history_task_id,
                data=history_payload,
                task_file=history_task_file,
                result=resp.to_dict(),
                before_snapshot_id=before_snapshot_id,
                take_snapshot=False,
            )
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
    "handle_radar",
    "handle_handoff",
    "handle_context_pack",
    "handle_resume",
    "handle_lint",
    "handle_templates_list",
    "handle_scaffold",
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
