"""Status bar builder for TaskTrackerTUI."""

import time
from typing import List, Tuple

from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.mouse_events import MouseButton, MouseEventType

from core import Status
from core.desktop.devtools.interface.ai_state import get_ai_state, AIStatus


def build_status_text(tui) -> FormattedText:
    items = tui.filtered_tasks
    total = len(items)
    ok = sum(1 for t in items if t.status == Status.OK)
    warn = sum(1 for t in items if t.status == Status.WARN)
    fail = sum(1 for t in items if t.status == Status.FAIL)
    filter_labels = {
        "OK": tui._t("FILTER_DONE"),
        "WARN": tui._t("FILTER_IN_PROGRESS"),
        "FAIL": tui._t("FILTER_BACKLOG"),
    }
    flt = tui.current_filter.value[0] if tui.current_filter else "ALL"
    flt_display = filter_labels.get(flt, tui._t("FILTER_ALL"))
    now = time.time()
    if getattr(tui, "_last_filter_value", None) != flt_display:
        tui._filter_flash_until = now + 1.0
        tui._last_filter_value = flt_display
    filter_flash_active = now < getattr(tui, "_filter_flash_until", 0)

    def back_handler(event):
        if event.event_type == MouseEventType.MOUSE_UP and event.button == MouseButton.LEFT:
            tui.exit_detail_view()
            return None
        return NotImplemented

    def settings_handler(event):
        if event.event_type == MouseEventType.MOUSE_UP and event.button == MouseButton.LEFT:
            tui.open_settings_dialog()
            return None
        return NotImplemented

    parts: List[Tuple[str, str]] = []
    if getattr(tui, "detail_mode", False):
        parts.append(("class:header.bigicon", f"{tui._t('BTN_BACK')} ", back_handler))

    parts.extend(
        [
            ("class:text.dim", f"{tui._t('STATUS_TASKS_COUNT', count=total)} | "),
            ("class:icon.check", str(ok)),
            ("class:text.dim", "/"),
            ("class:icon.warn", str(warn)),
            ("class:text.dim", "/"),
            ("class:icon.fail", str(fail)),
        ]
    )
    filter_style = "class:icon.warn" if filter_flash_active else "class:header"
    parts.extend(
        [
            ("class:text.dim", " | "),
            (filter_style, f"{flt_display}"),
            ("class:text.dim", " | "),
        ]
    )
    parts.extend(tui._sync_indicator_fragments(filter_flash_active))
    spinner_frame = tui._spinner_frame()
    if spinner_frame:
        parts.extend(
            [
                ("class:text.dim", " | "),
                ("class:header", f"{spinner_frame} {tui.spinner_message or tui._t('STATUS_LOADING')}"),
            ]
        )
    if getattr(tui, "status_message", "") and time.time() < getattr(tui, "status_message_expires", 0):
        parts.extend(
            [
                ("class:text.dim", " | "),
                ("class:header", tui.status_message[:80]),
            ]
        )
    elif getattr(tui, "status_message", ""):
        tui.status_message = ""

    # AI status indicator
    ai_state = get_ai_state()
    ai_status_line = ai_state.to_status_line()
    if ai_status_line:
        ai_style = _get_ai_status_style(ai_state.status)
        parts.extend(
            [
                ("class:text.dim", " | "),
                (ai_style, ai_status_line),
            ]
        )

    try:
        term_width = tui.get_terminal_width()
    except Exception:
        term_width = 120
    current_len = sum(len(text) for _, text, *rest in parts)
    settings_symbol = tui._t("BTN_SETTINGS")
    needed = max(1, term_width - current_len - len(settings_symbol))
    parts.append(("class:text", " " * needed))
    parts.append(("class:header.bigicon", settings_symbol, settings_handler))
    return FormattedText(parts)


def _get_ai_status_style(status: AIStatus) -> str:
    """Get style class for AI status."""
    style_map = {
        AIStatus.IDLE: "class:text.dim",
        AIStatus.THINKING: "class:icon.warn",
        AIStatus.EXECUTING: "class:header",
        AIStatus.WAITING: "class:icon.warn",
        AIStatus.PAUSED: "class:icon.fail",
        AIStatus.ERROR: "class:icon.fail",
    }
    return style_map.get(status, "class:text.dim")


__all__ = ["build_status_text"]
