from typing import Callable, Dict, List, Optional, Tuple

from core.task_detail import TaskDetail

TaskSerializer = Callable[[TaskDetail], Dict[str, object]]
TaskRemember = Callable[[str, str], None]


def _priority_value(task: TaskDetail) -> int:
    return {"HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(task.priority, 0)


def next_recommendations(
    tasks: List[TaskDetail],
    filters: Dict[str, str],
    *,
    remember: Optional[TaskRemember] = None,
    serializer: Optional[TaskSerializer] = None,
) -> Tuple[Dict[str, object], Optional[TaskDetail]]:
    serializer = serializer or (lambda t: t)  # type: ignore[return-value]
    candidates = [t for t in tasks if t.status != "OK" and t.calculate_progress() < 100]
    if not candidates:
        return {"filters": filters, "candidates": []}, None

    def score(task: TaskDetail):
        blocked = -100 if task.blocked else 0
        return (blocked, -_priority_value(task), task.calculate_progress())

    candidates.sort(key=score)
    top = candidates[:3]
    selected = candidates[0]
    if remember:
        remember(selected.id, selected.domain)
    payload = {
        "filters": filters,
        "candidates": [serializer(task) for task in top],
        "selected": serializer(selected),
    }
    return payload, selected


def suggest_tasks(
    tasks: List[TaskDetail],
    filters: Dict[str, str],
    *,
    remember: Optional[TaskRemember] = None,
    serializer: Optional[TaskSerializer] = None,
) -> Tuple[Dict[str, object], List[TaskDetail]]:
    serializer = serializer or (lambda t: t)  # type: ignore[return-value]
    active = [t for t in tasks if t.status != "OK"]
    if not active:
        return {"filters": filters, "suggestions": []}, []

    def score(task: TaskDetail):
        progress = task.calculate_progress()
        return (-_priority_value(task), progress, len(task.dependencies))

    sorted_tasks = sorted(active, key=score)
    lead = sorted_tasks[0]
    if remember:
        remember(lead.id, lead.domain)
    payload = {
        "filters": filters,
        "suggestions": [serializer(task) for task in sorted_tasks[:5]],
    }
    return payload, sorted_tasks


def quick_overview(
    tasks: List[TaskDetail],
    filters: Dict[str, str],
    *,
    remember: Optional[TaskRemember] = None,
    serializer: Optional[TaskSerializer] = None,
) -> Tuple[Dict[str, object], List[TaskDetail]]:
    serializer = serializer or (lambda t: t)  # type: ignore[return-value]
    active = [t for t in tasks if t.status != "OK"]
    active.sort(key=lambda t: (t.priority, t.calculate_progress()))
    if not active:
        return {"filters": filters, "top": []}, []

    if remember:
        remember(active[0].id, active[0].domain)
    top = active[:3]
    payload = {
        "filters": filters,
        "top": [serializer(task) for task in top],
    }
    return payload, top


__all__ = ["next_recommendations", "suggest_tasks", "quick_overview"]
