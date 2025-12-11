"""Scrolling helpers split from TaskTrackerTUI."""

from typing import List, Tuple


def scroll_line_preserve_borders(tui, line: str) -> str:
    if not line or tui.horizontal_offset == 0:
        return line
    if line.startswith(("+", "|")):
        border_char = line[0]
        content = line[1:]
        scrolled_content = content[tui.horizontal_offset :] if len(content) > tui.horizontal_offset else ""
        return border_char + scrolled_content
    return tui.apply_horizontal_scroll(line)


def apply_scroll_to_formatted(tui, formatted_items: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """Apply horizontal scroll to formatted text line by line, preserving table structure."""
    if tui.horizontal_offset == 0:
        return formatted_items

    result: List[Tuple[str, str]] = []
    current_line: List[Tuple[str, str]] = []

    for style, text in formatted_items:
        parts = text.split("\n")
        for i, part in enumerate(parts):
            if i > 0:
                if current_line:
                    line_text = "".join(t for _, t in current_line)
                    scrolled = scroll_line_preserve_borders(tui, line_text)
                    if scrolled:
                        result.append(("class:text", scrolled))
                    result.append(("", "\n"))
                    current_line = []
            if part:
                current_line.append((style, part))

    if current_line:
        line_text = "".join(t for _, t in current_line)
        scrolled = scroll_line_preserve_borders(tui, line_text)
        if scrolled:
            result.append(("class:text", scrolled))

    return result


__all__ = ["scroll_line_preserve_borders", "apply_scroll_to_formatted"]
