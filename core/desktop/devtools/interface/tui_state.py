"""Small helpers to keep TaskTrackerTUI methods slim."""

from typing import Optional

from core.desktop.devtools.interface.cli_activity import read_activity_marker, ACTIVITY_TTL


def toggle_collapse_selected(tui) -> None:
    if tui.detail_mode or not tui.filtered_tasks:
        return
    task = tui.filtered_tasks[tui.selected]
    if task.id not in tui.collapsed_tasks:
        tui.collapsed_tasks.add(task.id)
    else:
        tui.collapsed_tasks.remove(task.id)
    tui.render(force=True)


def toggle_subtask_collapse(tui, expand: bool) -> None:
    entry = tui._selected_subtask_entry()
    if not entry:
        return
    path, st, _, collapsed, has_children = entry
    if not has_children:
        if not expand and "." in path:
            parent_path = ".".join(path.split(".")[:-1])
            tui._select_subtask_by_path(parent_path)
            tui._ensure_detail_selection_visible(len(tui.detail_flat_subtasks))
            tui.force_render()
        return
    if expand:
        if collapsed:
            tui.detail_collapsed.discard(path)
            tui._rebuild_detail_flat(path)
        else:
            child_path = f"{path}.0" if st.children else path
            tui._select_subtask_by_path(child_path)
            tui._rebuild_detail_flat(child_path)
    else:
        if not collapsed:
            tui.detail_collapsed.add(path)
            tui._rebuild_detail_flat(path)
        elif "." in path:
            parent_path = ".".join(path.split(".")[:-1])
            tui._select_subtask_by_path(parent_path)
            tui._ensure_detail_selection_visible(len(tui.detail_flat_subtasks))
            tui.force_render()


def maybe_reload(tui, now: Optional[float] = None) -> None:
    from time import time

    ts = now if now is not None else time()
    if ts - tui._last_check < 0.3:  # Reduced from 0.7s for faster CLI updates
        return
    tui._last_check = ts

    # Check for CLI activity marker
    activity = read_activity_marker(getattr(tui, "tasks_dir", None))
    if activity:
        task_id = activity.get("task_id", "")
        subtask_path = activity.get("subtask_path")
        command = activity.get("command", "")
        timestamp = activity.get("timestamp", 0)
        # Store activity info for rendering
        tui._cli_activity_task_id = task_id
        tui._cli_activity_subtask_path = subtask_path
        tui._cli_activity_command = command
        tui._cli_activity_expires = timestamp + ACTIVITY_TTL
    else:
        # Clear expired activity
        if ts > getattr(tui, "_cli_activity_expires", 0):
            tui._cli_activity_task_id = None
            tui._cli_activity_subtask_path = None
            tui._cli_activity_command = None

    sig = tui.compute_signature()
    if sig == tui._last_signature:
        return
    selected_task_file = tui.tasks[tui.selected_index].task_file if tui.tasks else None
    prev_detail = tui.current_task_detail.id if (tui.detail_mode and tui.current_task_detail) else None
    prev_detail_path = tui.detail_selected_path

    tui.load_tasks(preserve_selection=True, selected_task_file=selected_task_file, skip_sync=True)
    tui._last_signature = sig
    tui.set_status_message(tui._t("STATUS_MESSAGE_CLI_UPDATED"), ttl=3)

    if prev_detail:
        for t in tui.tasks:
            if t.id != prev_detail:
                continue
            tui.show_task_details(t)
            if prev_detail_path:
                tui._select_subtask_by_path(prev_detail_path)
            items = tui.get_detail_items_count()
            tui._ensure_detail_selection_visible(items)
            break


__all__ = ["toggle_collapse_selected", "toggle_subtask_collapse", "maybe_reload"]
