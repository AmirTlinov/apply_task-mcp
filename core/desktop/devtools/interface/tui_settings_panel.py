"""Settings panel renderer extracted from TaskTrackerTUI."""

from typing import List, Tuple

from prompt_toolkit.formatted_text import FormattedText


def render_settings_panel(tui) -> FormattedText:
    options = tui._settings_options()
    if not options:
        return FormattedText([("class:text.dim", tui._t("SETTINGS_UNAVAILABLE"))])
    width = max(70, min(110, tui.get_terminal_width() - 4))
    inner_width = max(30, width - 2)
    max_label = max(len(opt["label"]) for opt in options)
    label_width = max(14, min(inner_width - 12, max_label + 2))
    value_width = max(10, inner_width - label_width - 2)
    tui.settings_selected_index = min(tui.settings_selected_index, len(options) - 1)

    occupied = 8  # header/footer space
    available = tui.get_terminal_height() - tui.footer_height - occupied
    visible = max(3, available - 3)
    max_offset = max(0, len(options) - visible)
    tui.settings_view_offset = max(0, min(tui.settings_view_offset, max_offset))
    if tui.settings_selected_index < tui.settings_view_offset:
        tui.settings_view_offset = tui.settings_selected_index
    elif tui.settings_selected_index >= tui.settings_view_offset + visible:
        tui.settings_view_offset = tui.settings_selected_index - visible + 1
    start = tui.settings_view_offset
    end = min(len(options), start + visible)

    lines: List[Tuple[str, str]] = []
    lines.append(("class:border", "+" + "=" * width + "+\n"))
    lines.append(("class:border", "| "))
    title = tui._t("SETTINGS_TITLE")
    lines.append(("class:header", title.center(width - 2)))
    lines.append(("class:border", " |\n"))
    lines.append(("class:border", "+" + "-" * width + "+\n"))

    for idx in range(start, end):
        option = options[idx]
        prefix = "▸" if idx == tui.settings_selected_index else " "
        label_text = option["label"][:label_width].ljust(label_width)
        value_text = option["value"]
        if len(value_text) > value_width:
            value_text = value_text[: max(1, value_width - 1)] + "…"
        row_text = f"{prefix} {label_text}{value_text.ljust(value_width)}"
        style = "class:selected" if idx == tui.settings_selected_index else ("class:text.dim" if option.get("disabled") else "class:text")
        lines.append(("class:border", "| "))
        lines.append((style, row_text.ljust(inner_width)))
        lines.append(("class:border", " |\n"))

    hidden_above = start
    hidden_below = len(options) - end
    if hidden_above or hidden_below:
        hint = tui._t("SETTINGS_SCROLL_HINT", above=hidden_above, below=hidden_below)
        lines.append(("class:border", "| "))
        lines.append(("class:text.dim", hint[: inner_width].ljust(inner_width)))
        lines.append(("class:border", " |\n"))

    lines.append(("class:border", "+" + "=" * width + "+"))
    return FormattedText(lines)


__all__ = ["render_settings_panel"]
