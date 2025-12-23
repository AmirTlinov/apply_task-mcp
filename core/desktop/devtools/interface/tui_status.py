"""Status bar builder for TaskTrackerTUI."""

import time
from pathlib import Path
from typing import List, Tuple

from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.mouse_events import MouseButton, MouseEventType

from core import Status
from core.desktop.devtools.interface.ai_state import get_ai_state, AIStatus


def build_status_text(tui) -> FormattedText:
    items = tui.filtered_tasks
    total = len(items)
    ok = sum(1 for t in items if t.status == Status.DONE)
    warn = sum(1 for t in items if t.status == Status.ACTIVE)
    fail = sum(1 for t in items if t.status == Status.TODO)
    filter_labels = {
        "DONE": tui._t("FILTER_DONE"),
        "ACTIVE": tui._t("FILTER_IN_PROGRESS"),
        "TODO": tui._t("FILTER_BACKLOG"),
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
            navigate_back = getattr(tui, "navigate_back", None)
            if callable(navigate_back):
                navigate_back()
            else:  # pragma: no cover - legacy fallback
                if getattr(tui, "detail_mode", False):
                    tui.exit_detail_view()
                else:
                    fast = getattr(tui, "return_to_projects_fast", None)
                    if callable(fast):
                        fast()
                    else:
                        tui.return_to_projects()
            return None
        return NotImplemented

    def settings_handler(event):
        if event.event_type == MouseEventType.MOUSE_UP and event.button == MouseButton.LEFT:
            tui.open_settings_dialog()
            return None
        return NotImplemented

    def handoff_handler(event):
        if event.event_type == MouseEventType.MOUSE_UP and event.button == MouseButton.LEFT:
            export_handoff = getattr(tui, "export_handoff", None)
            if callable(export_handoff):
                export_handoff()
            return None
        return NotImplemented

    parts: List[Tuple[str, str]] = []
    project_mode = bool(getattr(tui, "project_mode", True))
    if getattr(tui, "detail_mode", False) or not project_mode:
        parts.append(("class:header.bigicon", f"{tui._t('BTN_BACK')} ", back_handler))
    if not project_mode:
        project_name = str(getattr(tui, "last_project_name", "") or "").strip()
        if not project_name:
            path = getattr(tui, "current_project_path", None) or getattr(tui, "tasks_dir", None)
            if path:
                try:
                    project_name = Path(str(path)).name
                except Exception:
                    project_name = ""
        if project_name:
            if len(project_name) > 32:
                project_name = project_name[:31] + "…"
            parts.extend(
                [
                    ("class:text.dim", f"{tui._t('TABLE_HEADER_PROJECT')}: "),
                    ("class:header", project_name),
                    ("class:text.dim", " | "),
                ]
            )
        plan_title = str(getattr(tui, "plan_filter_title", "") or "").strip()
        if plan_title:
            if len(plan_title) > 32:
                plan_title = plan_title[:31] + "…"
            parts.extend(
                [
                    ("class:text.dim", f"{tui._t('TABLE_HEADER_PLAN', fallback='Plan')}: "),
                    ("class:header", plan_title),
                    ("class:text.dim", " | "),
                ]
            )

    if project_mode:
        count_key = "STATUS_PROJECTS_COUNT"
    else:
        section = getattr(tui, "project_section", "tasks") or "tasks"
        count_key = "STATUS_PLANS_COUNT" if section == "plans" else "STATUS_TASKS_COUNT"
    parts.extend(
        [
            ("class:text.dim", f"{tui._t(count_key, count=total)} | "),
            ("class:icon.check", str(ok)),
            ("class:text.dim", "/"),
            ("class:icon.warn", str(warn)),
            ("class:text.dim", "/"),
            ("class:icon.fail", str(fail)),
        ]
    )
    query = (getattr(tui, "search_query", "") or "").strip()
    if query:
        cursor = "▏" if getattr(tui, "search_mode", False) else ""
        preview = (query[:24] + "…") if len(query) > 25 else query
        parts.extend(
            [
                ("class:text.dim", " | "),
                ("class:icon.info", f"{tui._t('SEARCH_ICON', fallback='⌕')} "),
                ("class:header", f"{preview}{cursor}"),
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
    show_handoff = False
    target_fn = getattr(tui, "_command_palette_target", None)
    if callable(target_fn):
        try:
            show_handoff = bool(target_fn())
        except Exception:
            show_handoff = False
    if getattr(tui, "project_mode", False) and not getattr(tui, "detail_mode", False):
        show_handoff = False
    settings_symbol = tui._t("BTN_SETTINGS")
    handoff_symbol = tui._t("BTN_HANDOFF")
    extra = len(settings_symbol)
    if show_handoff:
        extra += len(handoff_symbol) + 1
    needed = max(1, term_width - current_len - extra)
    parts.append(("class:text", " " * needed))
    if show_handoff:
        parts.append(("class:header.bigicon", handoff_symbol, handoff_handler))
        parts.append(("class:text", " "))
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
