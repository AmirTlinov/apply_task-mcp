"""Helpers to load and filter tasks for TaskTrackerTUI."""

from typing import Callable, List, Tuple

from core.desktop.devtools.application.task_manager import TaskManager
from core.desktop.devtools.interface.i18n import translate
from core import TaskDetail, Status


def load_tasks_snapshot(manager: TaskManager, domain_filter: str, current_filter) -> List[TaskDetail]:
    items = manager.list_tasks(domain_filter)
    if current_filter:
        items = [t for t in items if t.status.name == current_filter.value[0]]
    items.sort(key=lambda t: (t.status.value, t.progress), reverse=False)
    return items


def load_tasks_with_state(tui) -> Tuple[List, str]:
    """Loads tasks for TUI, returns (items, message)."""
    manager = getattr(tui, "manager", TaskManager())
    domain = getattr(tui, "domain_filter", "") or ""
    try:
        items = load_tasks_snapshot(manager, domain, getattr(tui, "current_filter", None))
    except Exception as exc:
        return [], translate("ERR_TASK_LIST_FAILED", error=str(exc))
    if getattr(tui, "current_filter", None):
        label = tui.current_filter.value[0]
        message = translate("FILTER_APPLIED", value=label)
    else:
        message = ""
    return items, message


def apply_context_filters(details: List[TaskDetail], phase_filter: str, component_filter: str) -> List[TaskDetail]:
    filtered = details
    if phase_filter:
        filtered = [d for d in filtered if d.phase == phase_filter]
    if component_filter:
        filtered = [d for d in filtered if d.component == component_filter]
    return filtered


def build_task_models(details: List[TaskDetail], factory: Callable) -> List:
    tasks = []
    for det in details:
        calc_progress = det.calculate_progress()
        derived_status = Status.OK if calc_progress == 100 and not det.blocked else Status.from_string(det.status)
        subtasks_completed = sum(1 for st in det.subtasks if st.completed)
        tasks.append(factory(det, derived_status, calc_progress, subtasks_completed))
    return tasks


def select_index_after_load(tasks: List, preserve_selection: bool, selected_task_file: str) -> int:
    if preserve_selection and selected_task_file:
        for idx, t in enumerate(tasks):
            if getattr(t, "task_file", None) == selected_task_file:
                return idx
    return 0


__all__ = [
    "load_tasks_snapshot",
    "load_tasks_with_state",
    "apply_context_filters",
    "build_task_models",
    "select_index_after_load",
]
