"""Display utilities mixin for TUI - text width, trimming, padding, wrapping."""

from typing import List, Tuple

from wcwidth import wcwidth


class DisplayMixin:
    """Mixin providing text display utilities with proper Unicode width handling."""

    horizontal_offset: int

    @staticmethod
    def _display_width(text: str) -> int:
        """Return visual width of text accounting for wide/narrow characters."""
        width = 0
        for ch in text:
            w = wcwidth(ch)
            if w is None:
                w = 0
            width += max(0, w)
        return width

    def _trim_display(self, text: str, width: int) -> str:
        """Trim text so visible width doesn't exceed specified width."""
        acc = []
        used = 0
        for ch in text:
            w = wcwidth(ch) or 0
            if w < 0:
                w = 0
            if used + w > width:
                break
            acc.append(ch)
            used += w
        return "".join(acc)

    def _pad_display(self, text: str, width: int) -> str:
        """Trim and pad with spaces to exact visible width."""
        trimmed = self._trim_display(text, width)
        trimmed_width = self._display_width(trimmed)
        if trimmed_width < width:
            trimmed += " " * (width - trimmed_width)
        return trimmed

    def _wrap_display(self, text: str, width: int) -> List[str]:
        """Wrap text into lines of fixed visible width."""
        lines: List[str] = []
        current = ""
        used = 0
        for ch in text:
            w = wcwidth(ch) or 0
            if w < 0:
                w = 0
            if used + w > width and current:
                lines.append(self._pad_display(current, width))
                current = ch
                used = w
            else:
                current += ch
                used += w
        lines.append(self._pad_display(current, width))
        return lines

    def _wrap_with_prefix(self, text: str, width: int, prefix: str) -> List[Tuple[str, bool]]:
        """Wrap text with a prefix on continuation lines.

        Returns list of (line_text, is_continuation) tuples.
        """
        lines: List[Tuple[str, bool]] = []
        prefix_width = self._display_width(prefix)
        effective_width = width - prefix_width if lines else width

        current = ""
        used = 0
        is_first = True

        for ch in text:
            w = wcwidth(ch) or 0
            if w < 0:
                w = 0
            if used + w > effective_width and current:
                if is_first:
                    lines.append((self._pad_display(current, width), False))
                    is_first = False
                else:
                    lines.append((prefix + self._pad_display(current, width - prefix_width), True))
                current = ch
                used = w
                effective_width = width - prefix_width
            else:
                current += ch
                used += w

        if current:
            if is_first:
                lines.append((self._pad_display(current, width), False))
            else:
                lines.append((prefix + self._pad_display(current, width - prefix_width), True))

        return lines

    def apply_horizontal_scroll(self, text: str) -> str:
        """Apply horizontal scroll offset to text."""
        if self.horizontal_offset <= 0:
            return text
        # Skip characters based on their visual width
        skipped = 0
        start_idx = 0
        for i, ch in enumerate(text):
            w = wcwidth(ch) or 0
            if w < 0:
                w = 0
            if skipped >= self.horizontal_offset:
                start_idx = i
                break
            skipped += w
        else:
            return ""
        return text[start_idx:]


__all__ = ["DisplayMixin"]
