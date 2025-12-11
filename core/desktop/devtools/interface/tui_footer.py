"""Footer renderer for TaskTrackerTUI."""

import textwrap
from typing import List, Tuple

from prompt_toolkit.formatted_text import FormattedText


def build_footer_text(tui) -> FormattedText:
    # Empty-state CTA footer
    if not getattr(tui, "filtered_tasks", []):
        return FormattedText([
            ('class:border', '╭─ '),
            ('class:header', tui._t("CTA_CREATE_TASK")),
            ('class:border', ' · '),
            ('class:header', tui._t("CTA_IMPORT_TASK")),
            ('class:border', ' · '),
            ('class:text.dim', tui._t("CTA_DOMAIN_HINT")),
            ('class:border', ' · '),
            ('class:text.dim', f"Duration: — | Legend: — {tui._t('OFFSET_LABEL') if hasattr(tui,'_t') else ''}0"),
        ])

    scroll_info = f"{tui._t('OFFSET_LABEL')}{tui.horizontal_offset}" if tui.horizontal_offset > 0 else ""
    desc = tui._current_description_snippet() or tui._t("DESCRIPTION_MISSING")
    detail = tui._current_task_detail_obj()
    segments: List[str] = []
    seen: set[str] = set()
    if detail:
        domain = detail.domain or ""
        if domain:
            for part in domain.split("/"):
                if part:
                    formatted = f"[{part}]"
                    if formatted not in seen:
                        segments.append(formatted)
                        seen.add(formatted)
        for comp in (detail.phase, detail.component):
            if comp:
                formatted = f"[{comp}]"
                if formatted not in seen:
                    segments.append(formatted)
                    seen.add(formatted)
    path_text = "->".join(segments) if segments else "-"
    start_time = "-"
    finish_time = "-"
    if detail:
        if getattr(detail, "created", None):
            start_time = str(detail.created)
        if detail.status == "OK" and getattr(detail, "updated", None):
            finish_time = str(detail.updated)
    duration_value = tui._task_duration_value(detail)
    table_width = max(60, tui.get_terminal_width())
    inner_width = max(30, table_width - 4)

    def add_block(rows: List[str], label: str, value: str, max_lines: int = 1) -> None:
        label_len = len(label)
        available = max(1, inner_width - label_len)
        text_value = value or "—"
        chunks = textwrap.wrap(text_value, available) or ["—"]
        truncated = len(chunks) > max_lines
        chunks = chunks[:max_lines]
        if truncated and chunks:
            tail = chunks[-1]
            if len(tail) >= available:
                tail = tail[:-1] + "…"
            else:
                tail = tail + "…"
            chunks[-1] = tail
        for idx in range(max_lines):
            prefix = label if idx == 0 else " " * label_len
            chunk = chunks[idx] if idx < len(chunks) else ""
            row = (prefix + chunk).ljust(inner_width)
            rows.append(row[:inner_width])

    rows: List[str] = []
    add_block(rows, f" {tui._t('DOMAIN')}: ", path_text, max_lines=2)
    add_block(rows, " Time: ", f"{start_time} → {finish_time}", max_lines=1)
    add_block(rows, " Duration: ", duration_value, max_lines=1)
    add_block(rows, f" {tui._t('DESCRIPTION')}: ", desc, max_lines=2)
    legend_text = "◉=Done/In Progress | ◎=Backlog | %=progress | Σ=subtasks | ?=help" + scroll_info
    add_block(rows, " Legend: ", legend_text, max_lines=1)
    while len(rows) < 7:
        rows.append(" " * inner_width)

    border = "+" + "-" * (inner_width + 2) + "+"
    parts: List[Tuple[str, str]] = []
    parts.append(("class:border", border + "\n"))
    for row in rows:
        parts.append(("class:border", "| "))
        parts.append(("class:text", row))
        parts.append(("class:border", " |\n"))
    parts.append(("class:border", border))
    return FormattedText(parts)


__all__ = ["build_footer_text"]
