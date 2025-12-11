"""Common serializers for tasks and subtasks."""

from typing import Any, Dict

from core import SubTask, TaskDetail


def subtask_to_dict(subtask: SubTask, path: str = "0", compact: bool = False) -> Dict[str, Any]:
    """Serialize subtask to dict.

    compact=True: minimal output for AI (path, title, completed, ready status)
    compact=False: full output with all fields
    """
    if compact:
        # Minimal representation for AI consumption
        d: Dict[str, Any] = {
            "path": path,
            "title": subtask.title,
            "completed": subtask.completed,
        }
        # Only include what's needed to understand status
        if not subtask.completed:
            ready = subtask.ready_for_completion()
            d["ready"] = ready
            if not ready:
                # Show what's blocking
                blocking = []
                if subtask.success_criteria and not subtask.criteria_confirmed:
                    blocking.append("criteria")
                if subtask.tests and not (subtask.tests_confirmed or subtask.tests_auto_confirmed):
                    blocking.append("tests")
                if subtask.blockers and not (subtask.blockers_resolved or subtask.blockers_auto_resolved):
                    blocking.append("blockers")
                if blocking:
                    d["needs"] = blocking
        # Phase 1: Add status and blocked info
        d["status"] = getattr(subtask, "computed_status", "pending")
        if getattr(subtask, "blocked", False):
            d["blocked"] = True
            if getattr(subtask, "block_reason", ""):
                d["block_reason"] = subtask.block_reason
        return d

    # Full representation
    return {
        "path": path,
        "title": subtask.title,
        "completed": subtask.completed,
        "success_criteria": list(subtask.success_criteria),
        "tests": list(subtask.tests),
        "blockers": list(subtask.blockers),
        "criteria_confirmed": subtask.criteria_confirmed,
        "tests_confirmed": subtask.tests_confirmed,
        "blockers_resolved": subtask.blockers_resolved,
        "criteria_notes": list(subtask.criteria_notes),
        "tests_notes": list(subtask.tests_notes),
        "blockers_notes": list(subtask.blockers_notes),
        "created_at": getattr(subtask, "created_at", None),
        "completed_at": getattr(subtask, "completed_at", None),
        "progress_notes": list(getattr(subtask, "progress_notes", [])),
        "started_at": getattr(subtask, "started_at", None),
        "blocked": getattr(subtask, "blocked", False),
        "block_reason": getattr(subtask, "block_reason", ""),
        "computed_status": getattr(subtask, "computed_status", "pending"),
    }


def task_to_dict(
    task: TaskDetail, include_subtasks: bool = False, compact: bool = False
) -> Dict[str, Any]:
    """Serialize task to dict.

    compact=True: minimal output (id, title, status, progress, subtasks)
    compact=False: full output with all fields
    """
    if compact:
        data: Dict[str, Any] = {
            "id": task.id,
            "title": task.title,
            "status": task.status,
            "progress": task.calculate_progress(),
        }
        # Include domain for GUI task disambiguation
        if task.domain:
            data["domain"] = task.domain
        if task.blocked:
            data["blocked"] = True
        if include_subtasks:
            data["subtasks"] = [
                subtask_to_dict(st, str(i), compact=True)
                for i, st in enumerate(task.subtasks)
            ]
        return data

    # Full representation
    data = {
        "id": task.id,
        "title": task.title,
        "status": task.status,
        "progress": task.calculate_progress(),
        "priority": task.priority,
        "domain": task.domain,
        "phase": task.phase,
        "component": task.component,
        "parent": task.parent,
        "status_manual": getattr(task, "status_manual", False),
        "tags": list(task.tags),
        "assignee": task.assignee,
        "blocked": task.blocked,
        "blockers": list(task.blockers),
        "description": task.description,
        "context": task.context,
        "success_criteria": list(task.success_criteria),
        "dependencies": list(task.dependencies),
        "next_steps": list(task.next_steps),
        "problems": list(task.problems),
        "risks": list(task.risks),
        "history": list(task.history),
        "subtasks_count": len(task.subtasks),
        "project_remote_updated": task.project_remote_updated,
    }
    if include_subtasks:
        data["subtasks"] = [subtask_to_dict(st, str(i)) for i, st in enumerate(task.subtasks)]
    return data
