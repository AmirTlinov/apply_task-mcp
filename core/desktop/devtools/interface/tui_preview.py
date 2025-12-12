"""Side preview renderer extracted from TaskTrackerTUI."""

from pathlib import Path
from typing import List

from prompt_toolkit.formatted_text import FormattedText

from infrastructure.task_file_parser import TaskFileParser


def _empty_box(message: str) -> FormattedText:
    return FormattedText(
        [
            ("class:border", "+------------------------------+\n"),
            ("class:text.dim", "| " + message.ljust(26) + " |\n"),
            ("class:border", "+------------------------------+"),
        ]
    )


def _status_chunk(detail) -> List:
    if detail.status == "OK":
        return [("class:icon.check", "DONE ")]
    if detail.status == "WARN":
        return [("class:icon.warn", "ACTV ")]
    return [("class:icon.fail", "TODO ")]


def build_side_preview_text(tui) -> FormattedText:
    if not tui.filtered_tasks:
        return _empty_box(tui._t("SIDE_EMPTY_TASKS"))

    idx = min(tui.selected_index, len(tui.filtered_tasks) - 1)
    task = tui.filtered_tasks[idx]
    detail = task.detail
    if not detail and task.task_file:
        try:
            detail = TaskFileParser.parse(Path(task.task_file))
        except Exception:
            detail = None
    if not detail:
        return _empty_box(tui._t("SIDE_NO_DATA"))

    result = []
    result.append(("class:border", "+------------------------------------------+\n"))
    result.append(("class:border", "| "))
    result.append(("class:header", f"{detail.id} "))
    result.append(("class:text.dim", "| "))
    result.extend(_status_chunk(detail))
    result.append(("class:text.dim", f"| {detail.priority}"))
    result.append(("class:border", "                   |\n"))
    result.append(("class:border", "+------------------------------------------+\n"))

    title_lines = [detail.title[i : i + 38] for i in range(0, len(detail.title), 38)]
    for tline in title_lines:
        result.append(("class:border", "| "))
        result.append(("class:text", tline.ljust(40)))
        result.append(("class:border", " |\n"))

    ctx = detail.domain or detail.phase or detail.component
    if ctx:
        result.append(("class:border", "| "))
        result.append(("class:text.dim", tui._t("STATUS_CONTEXT", ctx=ctx[:32]).ljust(40)))
        result.append(("class:border", " |\n"))

    prog = detail.calculate_progress()
    bar_width = 30
    filled = int(prog * bar_width / 100)
    bar = "#" * filled + "-" * (bar_width - filled)
    result.append(("class:border", "| "))
    result.append(("class:text.dim", f"{prog:3d}% ["))
    result.append(("class:text.dim", bar[:30]))
    result.append(("class:text.dim", "]"))
    result.append(("class:border", "    |\n"))

    if detail.description:
        result.append(("class:border", "+------------------------------------------+\n"))
        desc_lines = detail.description.split("\n")
        for dline in desc_lines[:5]:
            chunks = [dline[i : i + 38] for i in range(0, len(dline), 38)]
            for chunk in chunks[:3]:
                result.append(("class:border", "| "))
                result.append(("class:text", chunk.ljust(40)))
                result.append(("class:border", " |\n"))

    result.append(("class:border", "+------------------------------------------+"))
    return FormattedText(result)


__all__ = ["build_side_preview_text"]
