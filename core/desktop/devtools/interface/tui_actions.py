"""Action handlers extracted from TaskTrackerTUI to reduce coupling."""

import shutil
from pathlib import Path

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
    # Project-level deletion: remove project folder with all tasks
    if getattr(tui, "project_mode", False) and not getattr(tui, "detail_mode", False):
        if not tui.tasks:
            return
        project = tui.filtered_tasks[tui.selected_index]
        prev_index = tui.selected_index
        path_raw = getattr(project, "task_file", None)
        if not path_raw:
            return
        path = Path(path_raw).resolve()
        root = getattr(tui, "projects_root", None)
        if not root:
            return
        root_resolved = Path(root).resolve()
        try:
            if not path.is_relative_to(root_resolved):
                return
        except AttributeError:
            # Python <3.9 compatibility: manual check
            if root_resolved not in path.parents and path != root_resolved:
                return
        # Не удаляем текущий namespace, чтобы не ломать активный менеджер
        try:
            if path.samefile(getattr(tui, "tasks_dir", Path())):
                tui.set_status_message("Нельзя удалить активный проект", ttl=3)
                return
        except Exception:
            pass
        try:
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.exists():
                shutil.rmtree(path)
            tui.set_status_message(f"Проект удален: {project.name}", ttl=3)
        except OSError:
            tui.set_status_message("Не удалось удалить проект", ttl=3)
            return
        if path.exists():
            tui.set_status_message("Не удалось удалить проект: путь остался", ttl=3)
            return
        # reload list and keep selection in bounds
        tui.load_projects()
        tui.selected_index = min(prev_index, max(0, len(tui.tasks) - 1))
        if tui.tasks:
            tui.last_project_index = tui.selected_index
            tui.last_project_name = tui.tasks[tui.selected_index].name
        else:
            tui.last_project_index = 0
            tui.last_project_name = None
        tui._ensure_selection_visible()
        tui.force_render()
        return

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
        deleted = tui.manager.delete_task(task.id, task.domain)
        if deleted:
            if tui.selected_index >= len(tui.filtered_tasks) - 1:
                tui.selected_index = max(0, len(tui.filtered_tasks) - 2)
            tui.load_tasks(preserve_selection=False, skip_sync=True)
            tui.set_status_message(tui._t("STATUS_MESSAGE_DELETED", task_id=task.id))
        else:
            tui.set_status_message(tui._t("ERR_DELETE_FAILED", task_id=task.id))


__all__ = ["activate_settings_option", "delete_current_item"]
