"""Editing mode mixin for TUI."""

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from prompt_toolkit.application import Application
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.layout import Container

    from core.task_detail import TaskDetail


class EditingMixin:
    """Mixin providing inline editing operations for TUI."""

    editing_mode: bool
    edit_context: Optional[str]
    edit_index: Optional[int]
    edit_buffer: "Buffer"
    edit_field: "Container"
    main_window: "Container"
    app: Optional["Application"]
    current_task_detail: Optional["TaskDetail"]

    def start_editing(self, context: str, current_value: str, index: Optional[int] = None) -> None:
        """Start inline editing mode.

        Args:
            context: What is being edited (e.g., 'title', 'description', 'token')
            current_value: Current value to edit
            index: Optional index for list items
        """
        self.editing_mode = True
        self.edit_context = context
        self.edit_index = index
        self.edit_buffer.text = current_value
        self.edit_buffer.cursor_position = len(current_value)
        if hasattr(self, "app") and self.app:
            self.app.layout.focus(self.edit_field)

    def save_edit(self) -> None:
        """Save edit result and dispatch to appropriate handler."""
        from core.desktop.devtools.interface.edit_handlers import (
            handle_bootstrap_remote,
            handle_project_number,
            handle_project_workers,
            handle_task_edit,
            handle_token,
        )

        if not self.editing_mode:
            return

        context = self.edit_context
        raw_value = self.edit_buffer.text
        new_value = raw_value.strip()

        # Try specialized handlers first
        if handle_token(self, new_value):
            return
        if handle_project_number(self, new_value):
            return
        if handle_project_workers(self, new_value):
            return
        if handle_bootstrap_remote(self, new_value):
            return

        if not new_value:
            self.cancel_edit()
            return

        if handle_task_edit(self, context or "", new_value, self.edit_index):
            return

        self.cancel_edit()

    def cancel_edit(self) -> None:
        """Cancel editing mode and restore focus."""
        self.editing_mode = False
        self.edit_context = None
        self.edit_index = None
        self.edit_buffer.text = ""
        if hasattr(self, "app") and self.app:
            self.app.layout.focus(self.main_window)


__all__ = ["EditingMixin"]
