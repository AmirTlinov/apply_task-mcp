"""Common serializers for Plan/Task/Step.

This module defines the canonical JSON contract for:
- Plans (TaskDetail(kind="plan"))
- Tasks (TaskDetail(kind="task"))
- Nested steps (Step)

All external boundaries (intent API / MCP / GUI / TUI) must either use these
serializers directly or match them exactly to prevent contract drift.
"""

from typing import Any, Dict

from core import PlanNode, Step, TaskDetail, TaskNode
from core.status import status_label


def step_to_dict(
    step: Step,
    path: str = "s:0",
    *,
    compact: bool = False,
    include_steps: bool = True,
) -> Dict[str, Any]:
    """Serialize nested step to dict.

    compact=True: minimal output for AI (path, title, completed, ready status)
    compact=False: full output with all fields
    """
    if compact:
        # Minimal representation for AI consumption
        d: Dict[str, Any] = {
            "path": path,
            "id": getattr(step, "id", "") or "",
            "title": step.title,
            "completed": step.completed,
            "criteria_confirmed": bool(getattr(step, "criteria_confirmed", False)),
            "tests_confirmed": bool(getattr(step, "tests_confirmed", False)),
            "criteria_auto_confirmed": bool(getattr(step, "criteria_auto_confirmed", False)),
            "tests_auto_confirmed": bool(getattr(step, "tests_auto_confirmed", False)),
        }
        # Only include what's needed to understand status
        if not step.completed:
            ready = step.ready_for_completion()
            d["ready"] = ready
            if not ready:
                # Show what's blocking
                blocking = []
                if step.success_criteria and not step.criteria_confirmed:
                    blocking.append("criteria")
                if step.tests and not (step.tests_confirmed or step.tests_auto_confirmed):
                    blocking.append("tests")
                if blocking:
                    d["needs"] = blocking
        # Phase 1: Add status and blocked info
        d["status"] = getattr(step, "computed_status", "pending")
        if getattr(step, "blocked", False):
            d["blocked"] = True
            if getattr(step, "block_reason", ""):
                d["block_reason"] = step.block_reason
        plan = getattr(step, "plan", None)
        if include_steps and plan and getattr(plan, "tasks", None):
            d["plan"] = plan_node_to_dict(plan, base_path=path, compact=True, include_steps=True)
        return d

    # Full representation
    data: Dict[str, Any] = {
        "path": path,
        "id": getattr(step, "id", "") or "",
        "title": step.title,
        "completed": step.completed,
        "success_criteria": list(step.success_criteria),
        "tests": list(step.tests),
        "blockers": list(step.blockers),
        "attachments": [a.to_dict() for a in list(getattr(step, "attachments", []) or [])],
        "verification_checks": [c.to_dict() for c in list(getattr(step, "verification_checks", []) or [])],
        "verification_outcome": str(getattr(step, "verification_outcome", "") or ""),
        "criteria_confirmed": step.criteria_confirmed,
        "tests_confirmed": step.tests_confirmed,
        "criteria_auto_confirmed": getattr(step, "criteria_auto_confirmed", False),
        "tests_auto_confirmed": getattr(step, "tests_auto_confirmed", False),
        "criteria_notes": list(step.criteria_notes),
        "tests_notes": list(step.tests_notes),
        "created_at": getattr(step, "created_at", None),
        "completed_at": getattr(step, "completed_at", None),
        "progress_notes": list(getattr(step, "progress_notes", [])),
        "started_at": getattr(step, "started_at", None),
        "blocked": getattr(step, "blocked", False),
        "block_reason": getattr(step, "block_reason", ""),
        "computed_status": getattr(step, "computed_status", "pending"),
    }
    plan = getattr(step, "plan", None)
    if include_steps and plan and getattr(plan, "tasks", None):
        data["plan"] = plan_node_to_dict(plan, base_path=path, compact=False, include_steps=True)
    return data


def plan_node_to_dict(
    plan: PlanNode,
    *,
    base_path: str,
    compact: bool = False,
    include_steps: bool = True,
) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "title": getattr(plan, "title", "") or "",
        "doc": getattr(plan, "doc", "") or "",
        "attachments": [a.to_dict() for a in list(getattr(plan, "attachments", []) or [])],
        "success_criteria": list(getattr(plan, "success_criteria", []) or []),
        "tests": list(getattr(plan, "tests", []) or []),
        "blockers": list(getattr(plan, "blockers", []) or []),
        "criteria_confirmed": bool(getattr(plan, "criteria_confirmed", False)),
        "tests_confirmed": bool(getattr(plan, "tests_confirmed", False)),
        "criteria_auto_confirmed": bool(getattr(plan, "criteria_auto_confirmed", False)),
        "tests_auto_confirmed": bool(getattr(plan, "tests_auto_confirmed", False)),
        "criteria_notes": list(getattr(plan, "criteria_notes", []) or []),
        "tests_notes": list(getattr(plan, "tests_notes", []) or []),
        "steps": list(getattr(plan, "steps", []) or []),
        "current": int(getattr(plan, "current", 0) or 0),
    }
    if include_steps and getattr(plan, "tasks", None):
        data["tasks"] = [
            task_node_to_dict(task, path=f"{base_path}.t:{idx}", compact=compact, include_steps=include_steps)
            for idx, task in enumerate(plan.tasks)
        ]
    return data


def task_node_to_dict(
    task: TaskNode,
    *,
    path: str,
    compact: bool = False,
    include_steps: bool = True,
) -> Dict[str, Any]:
    if compact:
        data: Dict[str, Any] = {
            "path": path,
            "id": getattr(task, "id", "") or "",
            "title": task.title,
            "status": status_label(task.status),
            "status_code": status_label(task.status),
            "progress": task.calculate_progress(),
            "criteria_confirmed": bool(getattr(task, "criteria_confirmed", False)),
            "tests_confirmed": bool(getattr(task, "tests_confirmed", False)),
            "criteria_auto_confirmed": bool(getattr(task, "criteria_auto_confirmed", False)),
            "tests_auto_confirmed": bool(getattr(task, "tests_auto_confirmed", False)),
        }
        if include_steps:
            data["steps"] = [
                step_to_dict(st, f"{path}.s:{i}", compact=True, include_steps=True)
                for i, st in enumerate(task.steps)
            ]
        return data
    data = {
        "path": path,
        "id": getattr(task, "id", "") or "",
        "title": task.title,
        "status": status_label(task.status),
        "status_code": status_label(task.status),
        "progress": task.calculate_progress(),
        "priority": getattr(task, "priority", "MEDIUM"),
        "description": getattr(task, "description", "") or "",
        "context": getattr(task, "context", "") or "",
        "attachments": [a.to_dict() for a in list(getattr(task, "attachments", []) or [])],
        "success_criteria": list(getattr(task, "success_criteria", []) or []),
        "tests": list(getattr(task, "tests", []) or []),
        "blockers": list(getattr(task, "blockers", []) or []),
        "criteria_confirmed": bool(getattr(task, "criteria_confirmed", False)),
        "tests_confirmed": bool(getattr(task, "tests_confirmed", False)),
        "criteria_auto_confirmed": bool(getattr(task, "criteria_auto_confirmed", False)),
        "tests_auto_confirmed": bool(getattr(task, "tests_auto_confirmed", False)),
        "criteria_notes": list(getattr(task, "criteria_notes", []) or []),
        "tests_notes": list(getattr(task, "tests_notes", []) or []),
        "dependencies": list(getattr(task, "dependencies", []) or []),
        "next_steps": list(getattr(task, "next_steps", []) or []),
        "problems": list(getattr(task, "problems", []) or []),
        "risks": list(getattr(task, "risks", []) or []),
        "blocked": bool(getattr(task, "blocked", False)),
        "status_manual": bool(getattr(task, "status_manual", False)),
    }
    if include_steps:
        data["steps"] = [
            step_to_dict(st, f"{path}.s:{i}", compact=False, include_steps=True)
            for i, st in enumerate(task.steps)
        ]
    return data


def plan_to_dict(plan: TaskDetail, *, compact: bool = False) -> Dict[str, Any]:
    if compact:
        data: Dict[str, Any] = {"id": plan.id, "kind": getattr(plan, "kind", "plan"), "title": plan.title}
        if plan.domain:
            data["domain"] = plan.domain
        contract_text = getattr(plan, "contract", "") or ""
        if contract_text:
            data["contract_preview"] = (contract_text[:160] + "…") if len(contract_text) > 160 else contract_text
        data["contract_versions_count"] = len(getattr(plan, "contract_versions", []) or [])
        data["criteria_confirmed"] = bool(getattr(plan, "criteria_confirmed", False))
        data["tests_confirmed"] = bool(getattr(plan, "tests_confirmed", False))
        data["criteria_auto_confirmed"] = bool(getattr(plan, "criteria_auto_confirmed", False))
        data["tests_auto_confirmed"] = bool(getattr(plan, "tests_auto_confirmed", False))
        plan_steps = list(getattr(plan, "plan_steps", []) or [])
        plan_current = int(getattr(plan, "plan_current", 0) or 0)
        if plan_steps:
            data["plan_progress"] = f"{plan_current}/{len(plan_steps)}"
        plan_doc = str(getattr(plan, "plan_doc", "") or "").strip()
        if plan_doc:
            data["plan_doc_preview"] = (plan_doc[:160] + "…") if len(plan_doc) > 160 else plan_doc
        return data

    data = {
        "id": plan.id,
        "kind": getattr(plan, "kind", "plan"),
        "title": plan.title,
        "domain": plan.domain,
        "created_at": getattr(plan, "created", "") or None,
        "updated_at": getattr(plan, "updated", "") or None,
        "tags": list(getattr(plan, "tags", []) or []),
        "description": getattr(plan, "description", "") or "",
        "contract": getattr(plan, "contract", "") or "",
        "contract_data": dict(getattr(plan, "contract_data", {}) or {}),
        "attachments": [a.to_dict() for a in list(getattr(plan, "attachments", []) or [])],
        "contract_versions_count": len(getattr(plan, "contract_versions", []) or []),
        "context": getattr(plan, "context", "") or "",
        "success_criteria": list(getattr(plan, "success_criteria", []) or []),
        "tests": list(getattr(plan, "tests", []) or []),
        "blockers": list(getattr(plan, "blockers", []) or []),
        "criteria_confirmed": bool(getattr(plan, "criteria_confirmed", False)),
        "tests_confirmed": bool(getattr(plan, "tests_confirmed", False)),
        "criteria_auto_confirmed": bool(getattr(plan, "criteria_auto_confirmed", False)),
        "tests_auto_confirmed": bool(getattr(plan, "tests_auto_confirmed", False)),
        "criteria_notes": list(getattr(plan, "criteria_notes", []) or []),
        "tests_notes": list(getattr(plan, "tests_notes", []) or []),
        "plan": {
            "steps": list(getattr(plan, "plan_steps", []) or []),
            "current": int(getattr(plan, "plan_current", 0) or 0),
            "doc": str(getattr(plan, "plan_doc", "") or ""),
        },
        "project_remote_updated": getattr(plan, "project_remote_updated", None),
    }
    events = list(getattr(plan, "events", []) or [])
    if events:
        data["events"] = [e.to_dict() for e in events]
    return data


def task_to_dict(
    task: TaskDetail, include_steps: bool = False, compact: bool = False
) -> Dict[str, Any]:
    """Serialize task to dict.

    compact=True: minimal output (id, title, status, progress, steps_count)
    compact=False: full output with all fields
    """
    if compact:
        raw_status = task.status
        normalized_status = status_label(raw_status)
        data: Dict[str, Any] = {
            "id": task.id,
            "kind": getattr(task, "kind", "task"),
            "title": task.title,
            "status": normalized_status,
            "status_code": normalized_status,
            "progress": task.calculate_progress(),
            "criteria_confirmed": bool(getattr(task, "criteria_confirmed", False)),
            "tests_confirmed": bool(getattr(task, "tests_confirmed", False)),
            "criteria_auto_confirmed": bool(getattr(task, "criteria_auto_confirmed", False)),
            "tests_auto_confirmed": bool(getattr(task, "tests_auto_confirmed", False)),
        }
        # Include domain for GUI step disambiguation
        if task.domain:
            data["domain"] = task.domain
        parent = getattr(task, "parent", None)
        if parent:
            data["parent"] = parent
        if task.blocked:
            data["blocked"] = True
        data["steps_count"] = len(task.steps or [])
        if include_steps:
            data["steps"] = [
                step_to_dict(st, f"s:{i}", compact=True, include_steps=True)
                for i, st in enumerate(task.steps)
            ]
        return data

    # Full representation
    raw_status = task.status
    normalized_status = status_label(raw_status)
    data = {
        "id": task.id,
        "kind": getattr(task, "kind", "task"),
        "title": task.title,
        "status": normalized_status,
        "status_code": normalized_status,
        "progress": task.calculate_progress(),
        "created_at": getattr(task, "created", "") or None,
        "updated_at": getattr(task, "updated", "") or None,
        "priority": task.priority,
        "domain": task.domain,
        "phase": task.phase,
        "component": task.component,
        "parent": getattr(task, "parent", None),
        "status_manual": getattr(task, "status_manual", False),
        "tags": list(task.tags),
        "assignee": task.assignee,
        "blocked": task.blocked,
        "blockers": list(task.blockers),
        "description": task.description,
        "context": task.context,
        "depends_on": list(getattr(task, "depends_on", []) or []),
        "success_criteria": list(task.success_criteria),
        "tests": list(getattr(task, "tests", []) or []),
        "criteria_confirmed": bool(getattr(task, "criteria_confirmed", False)),
        "tests_confirmed": bool(getattr(task, "tests_confirmed", False)),
        "criteria_auto_confirmed": bool(getattr(task, "criteria_auto_confirmed", False)),
        "tests_auto_confirmed": bool(getattr(task, "tests_auto_confirmed", False)),
        "criteria_notes": list(getattr(task, "criteria_notes", []) or []),
        "tests_notes": list(getattr(task, "tests_notes", []) or []),
        "dependencies": list(task.dependencies),
        "next_steps": list(task.next_steps),
        "problems": list(task.problems),
        "risks": list(task.risks),
        "history": list(task.history),
        "steps_count": len(task.steps),
        "project_remote_updated": task.project_remote_updated,
    }
    if include_steps:
        data["steps"] = [step_to_dict(st, f"s:{i}", include_steps=True) for i, st in enumerate(task.steps)]
    events = list(getattr(task, "events", []) or [])
    if events:
        data["events"] = [e.to_dict() for e in events]
    return data
