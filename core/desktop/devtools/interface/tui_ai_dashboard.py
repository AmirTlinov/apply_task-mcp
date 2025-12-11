"""AI Dashboard component for TUI.

Full AI dashboard showing:
- Current plan and progress
- Activity history
- User signal buttons
- Statistics
"""

from typing import List, Tuple, Callable

from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.mouse_events import MouseButton, MouseEventType

from core.desktop.devtools.interface.ai_state import (
    get_ai_state,
    AIStatus,
    UserSignal,
    write_user_signal,
)


def build_ai_dashboard(tui, width: int = 80) -> List[FormattedText]:
    """Build AI dashboard content as list of FormattedText lines."""
    ai_state = get_ai_state()
    lines = []

    # Header
    lines.append(_build_header(ai_state, width))
    lines.append(FormattedText([("class:text.dim", "─" * width)]))

    # Current status section
    lines.extend(_build_status_section(ai_state, width))
    lines.append(FormattedText([("class:text", "")]))

    # Plan section (if available)
    if ai_state.plan:
        lines.extend(_build_plan_section(ai_state, width))
        lines.append(FormattedText([("class:text", "")]))

    # Activity history
    lines.extend(_build_history_section(ai_state, width))
    lines.append(FormattedText([("class:text", "")]))

    # Controls section
    lines.extend(_build_controls_section(tui, ai_state, width))

    # Statistics footer
    lines.append(FormattedText([("class:text.dim", "─" * width)]))
    lines.append(_build_stats_footer(ai_state, width))

    return lines


def _build_header(ai_state, width: int) -> FormattedText:
    """Build dashboard header."""
    status_icon = _status_icon(ai_state.status)
    status_text = ai_state.status.value.upper()

    parts: List[Tuple[str, str]] = [
        ("class:header.bigicon", " AI Dashboard "),
        ("class:text.dim", " │ "),
        (_status_style(ai_state.status), f"{status_icon} {status_text}"),
    ]

    # Pad to width
    current_len = sum(len(text) for _, text in parts)
    if current_len < width:
        parts.append(("class:text", " " * (width - current_len)))

    return FormattedText(parts)


def _build_status_section(ai_state, width: int) -> List[FormattedText]:
    """Build current status section."""
    lines = []

    lines.append(FormattedText([("class:header", "Current Operation")]))

    if ai_state.current_operation:
        lines.append(FormattedText([
            ("class:text.dim", "  Operation: "),
            ("class:text", ai_state.current_operation),
        ]))
        if ai_state.current_task_id:
            lines.append(FormattedText([
                ("class:text.dim", "  Task: "),
                ("class:text", ai_state.current_task_id),
            ]))
        if ai_state.current_path:
            lines.append(FormattedText([
                ("class:text.dim", "  Path: "),
                ("class:text", ai_state.current_path),
            ]))
    else:
        lines.append(FormattedText([
            ("class:text.dim", "  No active operation"),
        ]))

    return lines


def _build_plan_section(ai_state, width: int) -> List[FormattedText]:
    """Build plan section."""
    lines = []
    plan = ai_state.plan

    if not plan:
        return lines

    progress = f"{plan.current_step}/{len(plan.steps)}"
    lines.append(FormattedText([
        ("class:header", "Execution Plan "),
        ("class:text.dim", f"({progress})"),
    ]))

    # Show up to 5 steps around current
    start = max(0, plan.current_step - 1)
    end = min(len(plan.steps), start + 5)

    for i in range(start, end):
        step = plan.steps[i]
        if i < plan.current_step:
            # Completed
            icon = "✓"
            style = "class:icon.check"
        elif i == plan.current_step:
            # Current
            icon = "→"
            style = "class:icon.warn"
        else:
            # Pending
            icon = "○"
            style = "class:text.dim"

        line_text = f"  {icon} {i+1}. {step[:width-10]}"
        lines.append(FormattedText([(style, line_text)]))

    if end < len(plan.steps):
        remaining = len(plan.steps) - end
        lines.append(FormattedText([
            ("class:text.dim", f"  ... +{remaining} more steps"),
        ]))

    return lines


def _build_history_section(ai_state, width: int) -> List[FormattedText]:
    """Build activity history section."""
    lines = []

    lines.append(FormattedText([("class:header", "Recent Activity")]))

    if not ai_state.history:
        lines.append(FormattedText([
            ("class:text.dim", "  No recent activity"),
        ]))
        return lines

    # Show last 5 activities
    for activity in ai_state.history[:5]:
        icon = "✓" if activity.success else "✗"
        icon_style = "class:icon.check" if activity.success else "class:icon.fail"
        time_str = activity.to_dict()["time"]

        summary = activity.summary[:width-25] if len(activity.summary) > width-25 else activity.summary

        lines.append(FormattedText([
            ("class:text.dim", f"  {time_str} "),
            (icon_style, icon),
            ("class:text.dim", " "),
            ("class:text", summary),
        ]))

    return lines


def _build_controls_section(tui, ai_state, width: int) -> List[FormattedText]:
    """Build user controls section."""
    lines = []

    lines.append(FormattedText([("class:header", "Controls")]))

    # Create mouse handlers
    def make_handler(signal: UserSignal) -> Callable:
        def handler(event):
            if event.event_type == MouseEventType.MOUSE_UP and event.button == MouseButton.LEFT:
                write_user_signal(signal, tasks_dir=getattr(tui, "tasks_dir", None))
                tui.set_status_message(f"Signal sent: {signal.value}", ttl=2)
                return None
            return NotImplemented
        return handler

    # Button line
    buttons: List[Tuple] = []

    if ai_state.status == AIStatus.PAUSED:
        buttons.append(("class:button", " [Resume] ", make_handler(UserSignal.RESUME)))
    elif ai_state.status in (AIStatus.EXECUTING, AIStatus.THINKING):
        buttons.append(("class:button", " [Pause] ", make_handler(UserSignal.PAUSE)))

    buttons.append(("class:text", " "))
    buttons.append(("class:button", " [Skip] ", make_handler(UserSignal.SKIP)))
    buttons.append(("class:text", " "))
    buttons.append(("class:button.danger", " [Stop] ", make_handler(UserSignal.STOP)))

    lines.append(FormattedText([("class:text", "  ")] + buttons))

    # Help text
    lines.append(FormattedText([
        ("class:text.dim", "  Press 'q' to close dashboard, 'm' to send message"),
    ]))

    return lines


def _build_stats_footer(ai_state, width: int) -> FormattedText:
    """Build statistics footer."""
    stats = ai_state.to_dict()["stats"]

    parts: List[Tuple[str, str]] = [
        ("class:text.dim", f" Operations: {stats['total_ops']}"),
        ("class:text.dim", " │ "),
        ("class:text.dim" if stats['errors'] == 0 else "class:icon.fail",
         f"Errors: {stats['errors']}"),
    ]

    # Pad to width
    current_len = sum(len(text) for _, text in parts)
    if current_len < width:
        parts.insert(0, ("class:text", " " * (width - current_len - 1)))

    return FormattedText(parts)


def _status_icon(status: AIStatus) -> str:
    """Get icon for AI status."""
    icons = {
        AIStatus.IDLE: "○",
        AIStatus.THINKING: "◐",
        AIStatus.EXECUTING: "●",
        AIStatus.WAITING: "◑",
        AIStatus.PAUSED: "⏸",
        AIStatus.ERROR: "✗",
    }
    return icons.get(status, "?")


def _status_style(status: AIStatus) -> str:
    """Get style for AI status."""
    styles = {
        AIStatus.IDLE: "class:text.dim",
        AIStatus.THINKING: "class:icon.warn",
        AIStatus.EXECUTING: "class:header",
        AIStatus.WAITING: "class:icon.warn",
        AIStatus.PAUSED: "class:icon.fail",
        AIStatus.ERROR: "class:icon.fail",
    }
    return styles.get(status, "class:text")


__all__ = ["build_ai_dashboard"]
