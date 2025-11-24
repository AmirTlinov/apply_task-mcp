"""Action handlers extracted from TaskTrackerTUI to reduce coupling."""

from typing import Any

from projects_sync import update_projects_enabled, reload_projects_sync
from core.desktop.devtools.application.task_manager import _find_subtask_by_path


def activate_settings_option(tui) -> None:
    options = tui._settings_options()
    if not options:
        return
    idx = tui.settings_selected_index
    option = options[idx]
    if option.get("disabled"):
        tui.set_status_message(option.get("disabled_msg") or tui._t("OPTION_DISABLED"))
        return
    action = option.get("action")
    if not action:
        return
    if action == "edit_pat":
        tui.set_status_message(tui._t("STATUS_MESSAGE_PASTE_PAT"))
        tui.start_editing("token", "", None)
        tui.edit_buffer.cursor_position = 0
        return
    if action == "toggle_sync":
        snapshot = tui._project_config_snapshot()
        desired = not snapshot["config_enabled"]
        update_projects_enabled(desired)
        state = tui._t("STATUS_MESSAGE_SYNC_ON") if desired else tui._t("STATUS_MESSAGE_SYNC_OFF")
        tui.set_status_message(state)
        tui.force_render()
        return
    if action == "edit_number":
        snapshot = tui._project_config_snapshot()
        tui.start_editing("project_number", str(snapshot["number"]), None)
        tui.edit_buffer.cursor_position = len(tui.edit_buffer.text)
        return
    if action == "edit_workers":
        snapshot = tui._project_config_snapshot()
        current = snapshot.get("workers")
        tui.start_editing("project_workers", str(current) if current else "0", None)
        tui.edit_buffer.cursor_position = len(tui.edit_buffer.text)
        return
    if action == "bootstrap_git":
        tui.start_editing("bootstrap_remote", "https://github.com/owner/repo.git", None)
        tui.edit_buffer.cursor_position = 0
        return
    if action == "refresh_metadata":
        reload_projects_sync()
        tui.set_status_message(tui._t("STATUS_MESSAGE_REFRESHED"))
        tui.force_render()
        return
    if action == "validate_pat":
        tui._start_pat_validation()
        return
    if action == "cycle_lang":
        tui._cycle_language()
        return
    tui.set_status_message(tui._t("STATUS_MESSAGE_OPTION_DISABLED"))


def delete_current_item(tui) -> None:
    if getattr(tui, "detail_mode", False) and getattr(tui, "current_task_detail", None):
        entry = tui._selected_subtask_entry()
        if not entry:
            return
        path, _, _, _, _ = entry
        target, parent, idx = _find_subtask_by_path(tui.current_task_detail.subtasks, path)
        if target is None or idx is None:
            return
        if parent is None:
            del tui.current_task_detail.subtasks[idx]
        else:
            del parent.children[idx]
        tui.manager.save_task(tui.current_task_detail)
        tui._rebuild_detail_flat()
        if tui.detail_selected_index >= len(tui.detail_flat_subtasks):
            tui.detail_selected_index = max(0, len(tui.detail_flat_subtasks) - 1)
        tui.detail_selected_path = tui.detail_flat_subtasks[tui.detail_selected_index][0] if tui.detail_flat_subtasks else ""
        if tui.current_task_detail.id in tui.task_details_cache:
            tui.task_details_cache[tui.current_task_detail.id] = tui.current_task_detail
        tui.load_tasks(preserve_selection=True, skip_sync=True)
        return

    if getattr(tui, "filtered_tasks", None):
        task = tui.filtered_tasks[tui.selected_index]
        tui.manager.delete_task(task.id, task.domain)
        if tui.selected_index >= len(tui.filtered_tasks) - 1:
            tui.selected_index = max(0, len(tui.filtered_tasks) - 2)
        tui.load_tasks(preserve_selection=False, skip_sync=True)


__all__ = ["activate_settings_option", "delete_current_item"]
