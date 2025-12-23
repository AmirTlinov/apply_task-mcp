"""Clipboard handling mixin for TUI."""

import subprocess
from typing import TYPE_CHECKING, Optional

from prompt_toolkit.clipboard import InMemoryClipboard, ClipboardData

try:
    from prompt_toolkit.clipboard.pyperclip import PyperclipClipboard
except ImportError:
    PyperclipClipboard = None  # type: ignore[misc,assignment]

if TYPE_CHECKING:
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.clipboard import Clipboard


class ClipboardMixin:
    """Mixin providing clipboard operations for TUI."""

    clipboard: Optional["Clipboard"]
    editing_mode: bool
    edit_buffer: "Buffer"

    def _t(self, key: str, **kwargs) -> str:
        """Translation stub - implemented by main class."""
        raise NotImplementedError

    def set_status_message(self, message: str, ttl: float = 4.0) -> None:
        """Status message stub - implemented by main class."""
        raise NotImplementedError

    def force_render(self) -> None:
        """Force render stub - implemented by main class."""
        raise NotImplementedError

    def _build_clipboard(self) -> "Clipboard":
        """Create clipboard instance with fallback."""
        if PyperclipClipboard:
            try:
                return PyperclipClipboard()
            except Exception:
                pass
        return InMemoryClipboard()

    def _clipboard_text(self) -> str:
        """Get text from clipboard."""
        clipboard = getattr(self, "clipboard", None)
        if not clipboard:
            return ""
        try:
            data = clipboard.get_data()
        except Exception:
            return ""
        if not data:
            return self._system_clipboard_fallback()
        text = data.text or ""
        if text:
            return text
        return self._system_clipboard_fallback()

    def _system_clipboard_fallback(self) -> str:
        """Try system clipboard when prompt_toolkit fails."""
        if PyperclipClipboard:
            try:
                clip = PyperclipClipboard()
                data = clip.get_data()
                if data and data.text:
                    return data.text
            except Exception:
                pass
        # Try native commands (pbpaste/wl-paste/xclip)
        commands = [
            ["pbpaste"],
            ["wl-paste", "-n"],
            ["wl-copy", "-o"],
            ["xclip", "-selection", "clipboard", "-out"],
            ["clip.exe"],
        ]
        for cmd in commands:
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=1)
            except Exception:
                continue
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout
        return ""

    def _paste_from_clipboard(self) -> None:
        """Paste clipboard content into edit buffer."""
        if not self.editing_mode:
            return
        text = self._clipboard_text()
        if not text:
            self.set_status_message(self._t("CLIPBOARD_EMPTY"), ttl=3)
            return
        buf = self.edit_buffer
        cursor = buf.cursor_position
        buf.text = buf.text[:cursor] + text + buf.text[cursor:]
        buf.cursor_position = cursor + len(text)
        self.force_render()

    def _copy_to_clipboard(self, text: str) -> bool:
        """Copy text to clipboard (best-effort)."""
        payload = str(text or "")
        if not payload:
            return False
        clipboard = getattr(self, "clipboard", None)
        if clipboard:
            try:
                clipboard.set_data(ClipboardData(payload))
                return True
            except Exception:
                pass
        commands = [
            ["pbcopy"],
            ["wl-copy"],
            ["xclip", "-selection", "clipboard", "-in"],
            ["clip.exe"],
        ]
        for cmd in commands:
            try:
                result = subprocess.run(cmd, input=payload, text=True, timeout=1)
            except Exception:
                continue
            if result.returncode == 0:
                return True
        return False


__all__ = ["ClipboardMixin"]
