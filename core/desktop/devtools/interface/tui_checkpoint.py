"""Checkpoint mode mixin for TUI."""

from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from core.desktop.devtools.application.task_manager import TaskManager
    from core.subtask import SubTask
    from core.task_detail import TaskDetail


class CheckpointMixin:
    """Mixin providing checkpoint mode operations for TUI."""

    checkpoint_mode: bool
    checkpoint_selected_index: int
    current_task_detail: Optional["TaskDetail"]
    detail_selected_path: str
    manager: "TaskManager"
    task_details_cache: Dict[str, "TaskDetail"]
    navigation_stack: List[Tuple[str, str, int]]

    def _get_subtask_by_path(self, path: str) -> Optional["SubTask"]:
        """Get subtask stub - implemented by main class."""
        raise NotImplementedError

    def _get_root_task_context(self) -> Tuple[str, str, str]:
        """Get root task context stub - implemented by main class."""
        raise NotImplementedError

    def _rebuild_detail_flat(self, selected_path: Optional[str] = None) -> None:
        """Rebuild detail stub - implemented by main class."""
        raise NotImplementedError

    def _update_tasks_list_silent(self, skip_sync: bool = False) -> None:
        """Update tasks stub - implemented by main class."""
        raise NotImplementedError

    def _set_footer_height(self, lines: int) -> None:
        """Set footer height stub - implemented by main class."""
        raise NotImplementedError

    def force_render(self) -> None:
        """Force render stub - implemented by main class."""
        raise NotImplementedError

    def enter_checkpoint_mode(self) -> None:
        """Enter checkpoint editing mode for current subtask."""
        self.checkpoint_mode = True
        self.checkpoint_selected_index = 0
        self._set_footer_height(0)
        self.force_render()

    def exit_checkpoint_mode(self) -> None:
        """Exit checkpoint editing mode."""
        self.checkpoint_mode = False
        self.force_render()

    def toggle_checkpoint_state(self) -> None:
        """Toggle the selected checkpoint (criteria/tests/blockers)."""
        from core.desktop.devtools.application.context import save_last_task
        from core.desktop.devtools.application.task_manager import _find_subtask_by_path
        from core.task_detail import subtask_to_task_detail

        if not self.current_task_detail or not getattr(self, "detail_selected_path", ""):
            return
        path = self.detail_selected_path
        subtask = self._get_subtask_by_path(path)
        if not subtask:
            return

        checkpoints = ["criteria", "tests", "blockers"]
        if 0 <= self.checkpoint_selected_index < len(checkpoints):
            key = checkpoints[self.checkpoint_selected_index]
            current = False
            if key == "criteria":
                current = subtask.criteria_confirmed
                subtask.criteria_confirmed = not current
            elif key == "tests":
                current = subtask.tests_confirmed
                subtask.tests_confirmed = not current
            elif key == "blockers":
                current = subtask.blockers_resolved
                subtask.blockers_resolved = not current

            # Get root task context for nested navigation
            root_task_id, root_domain, path_prefix = self._get_root_task_context()

            # Build full path from root
            if path_prefix:
                full_path = f"{path_prefix}.{path}"
            else:
                full_path = path

            # Save changes
            try:
                top_level_index = int(full_path.split(".")[0])
                self.manager.update_subtask_checkpoint(
                    root_task_id,
                    top_level_index,
                    key,
                    not current,
                    "",  # note
                    root_domain,
                    path=full_path,
                )
                # Reload root task to get updated state
                updated_root = self.manager.load_task(root_task_id, root_domain, skip_sync=True)
                if updated_root:
                    # Update cache
                    self.task_details_cache[root_task_id] = updated_root

                    # If we're at root level, update current_task_detail directly
                    if not self.navigation_stack:
                        self.current_task_detail = updated_root
                    else:
                        # We're inside nested subtask - rebuild current view from updated root
                        nested_subtask, _, _ = _find_subtask_by_path(updated_root.subtasks, path_prefix)
                        if nested_subtask:
                            new_detail = subtask_to_task_detail(nested_subtask, root_task_id, path_prefix)
                            new_detail.domain = root_domain
                            self.current_task_detail = new_detail

                    self._rebuild_detail_flat(path)

                # Update tasks list without resetting view state
                self._update_tasks_list_silent(skip_sync=True)
                save_last_task(root_task_id, root_domain)
                self.force_render()
            except (ValueError, IndexError):
                pass

    def move_checkpoint_selection(self, delta: int) -> None:
        """Move checkpoint selection up/down."""
        self.checkpoint_selected_index = max(0, min(self.checkpoint_selected_index + delta, 2))
        self.force_render()


__all__ = ["CheckpointMixin"]
