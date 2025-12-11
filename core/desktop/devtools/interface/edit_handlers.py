"""Small helpers to keep TaskTrackerTUI.save_edit slim."""

from typing import Optional

from core import TaskDetail, SubTask
from config import set_user_token
from projects_sync import update_project_workers, reload_projects_sync


def handle_token(tui, new_value: str) -> bool:
    if tui.edit_context != "token":
        return False
    set_user_token(new_value)
    tui.set_status_message(
        tui._t("STATUS_MESSAGE_PAT_SAVED") if new_value else tui._t("STATUS_MESSAGE_PAT_CLEARED")
    )
    tui.cancel_edit()
    if tui.settings_mode:
        tui.force_render()
    return True


def handle_project_number(tui, new_value: str) -> bool:
    if tui.edit_context != "project_number":
        return False
    try:
        number_value = int(new_value)
        if number_value <= 0:
            raise ValueError
    except ValueError:
        tui.set_status_message(tui._t("STATUS_MESSAGE_PROJECT_NUMBER_REQUIRED"))
    else:
        tui._set_project_number(number_value)
        tui.set_status_message(tui._t("STATUS_MESSAGE_PROJECT_NUMBER_UPDATED"))
    tui.cancel_edit()
    if tui.settings_mode:
        tui.force_render()
    return True


def handle_project_workers(tui, new_value: str) -> bool:
    if tui.edit_context != "project_workers":
        return False
    try:
        workers_value = int(new_value)
        if workers_value < 0:
            raise ValueError
    except ValueError:
        tui.set_status_message(tui._t("STATUS_MESSAGE_POOL_INTEGER"))
    else:
        update_project_workers(None if workers_value == 0 else workers_value)
        reload_projects_sync()
        tui.set_status_message(tui._t("STATUS_MESSAGE_POOL_UPDATED"))
    tui.cancel_edit()
    if tui.settings_mode:
        tui.force_render()
    return True


def handle_bootstrap_remote(tui, new_value: str) -> bool:
    if tui.edit_context != "bootstrap_remote":
        return False
    tui._bootstrap_git(new_value)
    tui.cancel_edit()
    return True


def _resolve_subtask(tui, path: str) -> Optional[SubTask]:
    return tui._get_subtask_by_path(path) if path else None


def _selected_path(tui) -> str:
    if getattr(tui, "detail_selected_path", ""):
        return tui.detail_selected_path
    if getattr(tui, "detail_flat_subtasks", None) and tui.detail_selected_index < len(tui.detail_flat_subtasks):
        return tui.detail_flat_subtasks[tui.detail_selected_index][0]
    return ""


def _path_by_index(tui, edit_index: int) -> str:
    if getattr(tui, "detail_selected_path", ""):
        return tui.detail_selected_path
    if getattr(tui, "detail_flat_subtasks", None) and edit_index < len(tui.detail_flat_subtasks):
        return tui.detail_flat_subtasks[edit_index][0]
    return ""


def handle_task_edit(tui, context: str, new_value: str, edit_index: Optional[int]) -> bool:
    task: Optional[TaskDetail] = tui.current_task_detail
    if not task:
        return False

    if context == "task_title":
        task.title = new_value
    elif context == "task_description":
        task.description = new_value
    elif context == "subtask_title" and edit_index is not None:
        path = _path_by_index(tui, edit_index)
        st = _resolve_subtask(tui, path)
        if not st:
            return False
        st.title = new_value
    elif context in {"criterion", "test", "blocker"} and edit_index is not None:
        path = _selected_path(tui)
        st = _resolve_subtask(tui, path)
        if not st:
            return False
        if context == "criterion" and edit_index < len(st.success_criteria):
            st.success_criteria[edit_index] = new_value
        elif context == "test" and edit_index < len(st.tests):
            st.tests[edit_index] = new_value
        elif context == "blocker" and edit_index < len(st.blockers):
            st.blockers[edit_index] = new_value
        else:
            return False
    else:
        return False

    tui.manager.save_task(task)
    if task.id in tui.task_details_cache:
        tui.task_details_cache[task.id] = task
    tui.load_tasks(preserve_selection=True)
    tui.cancel_edit()
    return True
