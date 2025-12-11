"""Navigation helpers for TaskTrackerTUI to keep tasks_app slim."""



def move_vertical_selection(tui, delta: int) -> None:
    """
    Move selected row/panel pointer by `delta`, clamping to available items.

    Works both in list mode (task rows) and detail mode (subtasks/dependencies).
    """
    if getattr(tui, "detail_mode", False):
        if getattr(tui, "current_task_detail", None) and not tui.detail_flat_subtasks and tui.current_task_detail.subtasks:
            tui._rebuild_detail_flat(tui.detail_selected_path)
        items = tui.get_detail_items_count()
        if items <= 0:
            tui.detail_selected_index = 0
            return
        new_index = max(0, min(tui.detail_selected_index + delta, items - 1))
        tui.detail_selected_index = new_index
        tui._selected_subtask_entry()
        tui._ensure_detail_selection_visible(items)
    elif getattr(tui, "settings_mode", False):
        options = tui._settings_options()
        total = len(options)
        if total <= 0:
            tui.settings_selected_index = 0
            return
        tui.settings_selected_index = max(0, min(tui.settings_selected_index + delta, total - 1))
        tui._ensure_settings_selection_visible(total)
    else:
        total = len(tui.filtered_tasks)
        if total <= 0:
            tui.selected_index = 0
            return
        tui.selected_index = max(0, min(tui.selected_index + delta, total - 1))
        tui._ensure_selection_visible()
    tui.force_render()


__all__ = ["move_vertical_selection"]
