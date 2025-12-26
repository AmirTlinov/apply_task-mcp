"""Detail tab renderers for TaskTrackerTUI (Plan/Contract/Meta)."""

from __future__ import annotations

import json

from typing import List, Tuple

from prompt_toolkit.formatted_text import FormattedText

from core.desktop.devtools.application.task_manager import _flatten_steps

def detail_tab_definitions(tui, detail) -> List[Tuple[str, str]]:
    """Single source of truth for detail tabs + labels (used by overview + tab renderers)."""
    overview_label = tui._t("TAB_TASKS") if getattr(detail, "kind", "task") == "plan" else tui._t("TAB_OVERVIEW")
    return [
        ("radar", tui._t("TAB_RADAR", fallback="Radar")),
        ("overview", overview_label),
        ("plan", tui._t("TAB_PLAN")),
        ("contract", tui._t("TAB_CONTRACT")),
        ("notes", tui._t("TAB_NOTES")),
        ("meta", tui._t("TAB_META")),
    ]

def render_detail_tab_text(tui) -> FormattedText:
    """Render the currently selected non-overview detail tab."""
    try:
        allowed = set(getattr(tui, "_detail_tabs")() or [])
    except Exception:
        allowed = None
    tab = getattr(tui, "detail_tab", "overview") or "overview"
    if allowed is not None and tab not in allowed:
        tab = "overview"
    if tab == "notes":
        return _render_notes_tab(tui)
    if tab == "radar":
        return _render_radar_tab(tui)
    if tab == "plan":
        return _render_plan_tab(tui)
    if tab == "contract":
        return _render_contract_tab(tui)
    if tab == "meta":
        return _render_meta_tab(tui)
    # Fallback to overview if the tab is unknown.
    return _render_plan_tab(tui)

def _render_radar_tab(tui) -> FormattedText:
    detail = tui.current_task_detail
    if not detail:
        return FormattedText([("class:text.dim", tui._t("STATUS_TASK_NOT_SELECTED"))])
    content_width = tui._detail_content_width()
    header_lines = _header_lines(tui, detail, content_width)
    tab_lines, tab_hitboxes = _tab_bar_lines(tui, content_width)
    tui._detail_tab_hitboxes = {"y": len(header_lines), "ranges": tab_hitboxes}
    footer_lines = _footer_lines(tui, content_width)

    body_lines: List[List[Tuple[str, str]]] = []
    _append_section_header(tui, body_lines, content_width, tui._t("TAB_RADAR", fallback="Radar"))

    payload = None
    err = ""
    getter = getattr(tui, "_radar_snapshot", None)
    if callable(getter):
        try:
            payload, err = getter(force=False)
        except Exception as exc:  # pragma: no cover
            payload, err = None, str(exc)
    if not isinstance(payload, dict):
        payload = {}

    if err:
        _append_blank_line(body_lines, content_width)
        _append_paragraph(tui, body_lines, content_width, f"⚠ {err}", style="class:icon.warn")
        return _compose_tab_view(
            tui=tui,
            tab="radar",
            content_width=content_width,
            header_lines=header_lines + tab_lines,
            body_lines=body_lines,
            footer_lines=footer_lines,
        )

    runway = payload.get("runway") if isinstance(payload.get("runway"), dict) else {}
    open_runway = bool(runway.get("open", True))
    runway_line = tui._t("RADAR_RUNWAY_OPEN") if open_runway else tui._t("RADAR_RUNWAY_CLOSED")
    _append_blank_line(body_lines, content_width)
    _append_paragraph(
        tui,
        body_lines,
        content_width,
        f"{'✓' if open_runway else '✗'} {runway_line}",
        style="class:icon.check" if open_runway else "class:icon.fail",
    )

    blocking = runway.get("blocking") if isinstance(runway.get("blocking"), dict) else {}
    lint = blocking.get("lint") if isinstance(blocking.get("lint"), dict) else {}
    errors_count = int(lint.get("errors_count", 0) or 0)
    top_errors = lint.get("top_errors") if isinstance(lint.get("top_errors"), list) else []
    validation = blocking.get("validation") if isinstance(blocking.get("validation"), dict) else None
    if errors_count > 0 and top_errors:
        top = top_errors[0] if isinstance(top_errors[0], dict) else {}
        msg = str(top.get("message", "") or "").strip()
        code = str(top.get("code", "") or "").strip()
        summary = f"{code}: {msg}" if code and msg else (code or msg or tui._t("RADAR_LINT_ERRORS", count=errors_count))
        _append_blank_line(body_lines, content_width)
        _append_paragraph(tui, body_lines, content_width, f"lint: {errors_count} errors · {summary}", style="class:icon.warn")
    elif validation and str(validation.get("message", "") or "").strip():
        _append_blank_line(body_lines, content_width)
        _append_paragraph(tui, body_lines, content_width, f"validation: {validation.get('message')}", style="class:icon.warn")

    verify = payload.get("verify") if isinstance(payload.get("verify"), dict) else {}
    evidence = verify.get("evidence_task") if isinstance(verify.get("evidence_task"), dict) else {}
    if evidence:
        steps_total = int(evidence.get("steps_total", 0) or 0)
        steps_with = int(evidence.get("steps_with_any_evidence", 0) or 0)
        checks = evidence.get("checks") if isinstance(evidence.get("checks"), dict) else {}
        atts = evidence.get("attachments") if isinstance(evidence.get("attachments"), dict) else {}
        checks_count = int(checks.get("count", 0) or 0)
        atts_count = int(atts.get("count", 0) or 0)
        last = str(checks.get("last_observed_at", "") or atts.get("last_observed_at", "") or "").strip()
        last_part = f" · last {last}" if last else ""
        _append_blank_line(body_lines, content_width)
        _append_paragraph(
            tui,
            body_lines,
            content_width,
            f"evidence: steps {steps_with}/{steps_total} · checks {checks_count} · attachments {atts_count}{last_part}",
            style="class:text.dim",
        )

    next_list = payload.get("next") if isinstance(payload.get("next"), list) else []
    next_item = next_list[0] if next_list and isinstance(next_list[0], dict) else {}
    if next_item:
        reason = str(next_item.get("reason", "") or "").strip() or str(next_item.get("action", "") or "")
        action = str(next_item.get("action", "") or "").strip()
        params = next_item.get("params") if isinstance(next_item.get("params"), dict) else {}
        validated = bool(next_item.get("validated", False))
        cmd = {"intent": action, **dict(params or {})} if action else {}
        _append_blank_line(body_lines, content_width)
        _append_section_header(tui, body_lines, content_width, tui._t("RADAR_NEXT", fallback="Next"), compact=True)
        _append_paragraph(tui, body_lines, content_width, reason, style="class:text")
        _append_paragraph(tui, body_lines, content_width, json.dumps(cmd, ensure_ascii=False), style="class:text.dim")
        if not validated:
            _append_paragraph(tui, body_lines, content_width, tui._t("RADAR_NEXT_NOT_VALIDATED"), style="class:icon.warn")
    else:
        _append_blank_line(body_lines, content_width)
        _append_paragraph(tui, body_lines, content_width, tui._t("RADAR_NO_NEXT"), style="class:text.dim")

    _append_blank_line(body_lines, content_width)
    _append_paragraph(tui, body_lines, content_width, tui._t("RADAR_HINT"), style="class:text.dim")

    return _compose_tab_view(
        tui=tui,
        tab="radar",
        content_width=content_width,
        header_lines=header_lines + tab_lines,
        body_lines=body_lines,
        footer_lines=footer_lines,
    )


def _render_plan_tab(tui) -> FormattedText:
    detail = tui.current_task_detail
    if not detail:
        return FormattedText([("class:text.dim", tui._t("STATUS_TASK_NOT_SELECTED"))])
    content_width = tui._detail_content_width()
    header_lines = _header_lines(tui, detail, content_width)
    tab_lines, tab_hitboxes = _tab_bar_lines(tui, content_width)
    tui._detail_tab_hitboxes = {"y": len(header_lines), "ranges": tab_hitboxes}
    footer_lines = _footer_lines(tui, content_width)

    body_lines: List[List[Tuple[str, str]]] = []

    _append_section_header(tui, body_lines, content_width, tui._t("TAB_PLAN"))

    # Soft hygiene warnings to prevent Plan/Contract/Subtasks duplication.
    try:
        from core.desktop.devtools.application.plan_hygiene import plan_doc_overlap_reasons
        from core.desktop.devtools.application.plan_semantics import plan_stale

        stale = bool(plan_stale(detail))
        doc_reasons = plan_doc_overlap_reasons(str(getattr(detail, "plan_doc", "") or ""))

        def _label(reason: str) -> str:
            if reason == "contract":
                return tui._t("TAB_CONTRACT")
            if reason == "done_criteria":
                return tui._t("DETAIL_DONE_CRITERIA")
            if reason == "subtasks":
                return tui._t("SUBTASKS")
            if reason == "checkbox_checklist":
                return tui._t("LABEL_CHECKLIST")
            if reason == "task_ids":
                return tui._t("LABEL_TASK_IDS")
            return reason

        if stale:
            _append_blank_line(body_lines, content_width)
            _append_paragraph(tui, body_lines, content_width, f"ℹ {tui._t('WARN_PLAN_STALE')}", style="class:text.dim")

        if doc_reasons:
            reasons_text = ", ".join(_label(r) for r in doc_reasons)
            _append_blank_line(body_lines, content_width)
            _append_paragraph(
                tui,
                body_lines,
                content_width,
                f"⚠ {tui._t('WARN_PLAN_DOC_OVERLAP', reasons=reasons_text)}",
                style="class:icon.warn",
            )
    except Exception:
        pass

    plan_doc = str(getattr(detail, "plan_doc", "") or "").strip()
    if not plan_doc:
        _append_paragraph(tui, body_lines, content_width, tui._t("DETAIL_EMPTY_PLAN"), style="class:text.dim")
    else:
        _append_paragraph(tui, body_lines, content_width, plan_doc, style="class:text")

    return _compose_tab_view(
        tui=tui,
        tab="plan",
        content_width=content_width,
        header_lines=header_lines + tab_lines,
        body_lines=body_lines,
        footer_lines=footer_lines,
    )


def _render_notes_tab(tui) -> FormattedText:
    detail = tui.current_task_detail
    if not detail:
        return FormattedText([("class:text.dim", tui._t("STATUS_TASK_NOT_SELECTED"))])
    content_width = tui._detail_content_width()
    header_lines = _header_lines(tui, detail, content_width)
    tab_lines, tab_hitboxes = _tab_bar_lines(tui, content_width)
    tui._detail_tab_hitboxes = {"y": len(header_lines), "ranges": tab_hitboxes}
    footer_lines = _footer_lines(tui, content_width)

    body_lines: List[List[Tuple[str, str]]] = []
    _append_section_header(tui, body_lines, content_width, tui._t("TAB_NOTES"))

    description = str(getattr(detail, "description", "") or "").rstrip()
    context = str(getattr(detail, "context", "") or "").rstrip()

    if not description and not context:
        _append_paragraph(tui, body_lines, content_width, tui._t("DETAIL_EMPTY_NOTES"), style="class:text.dim")
    else:
        if description:
            _append_blank_line(body_lines, content_width)
            _append_section_header(tui, body_lines, content_width, tui._t("DESCRIPTION"), compact=True)
            _append_paragraph(tui, body_lines, content_width, description, style="class:text")
        if context:
            _append_blank_line(body_lines, content_width)
            _append_section_header(tui, body_lines, content_width, tui._t("STATUS_CONTEXT"), compact=True)
            _append_paragraph(tui, body_lines, content_width, context, style="class:text")

    return _compose_tab_view(
        tui=tui,
        tab="notes",
        content_width=content_width,
        header_lines=header_lines + tab_lines,
        body_lines=body_lines,
        footer_lines=footer_lines,
    )


def _render_contract_tab(tui) -> FormattedText:
    detail = tui.current_task_detail
    if not detail:
        return FormattedText([("class:text.dim", tui._t("STATUS_TASK_NOT_SELECTED"))])
    content_width = tui._detail_content_width()
    header_lines = _header_lines(tui, detail, content_width)
    tab_lines, tab_hitboxes = _tab_bar_lines(tui, content_width)
    tui._detail_tab_hitboxes = {"y": len(header_lines), "ranges": tab_hitboxes}
    footer_lines = _footer_lines(tui, content_width)

    body_lines: List[List[Tuple[str, str]]] = []
    _append_section_header(tui, body_lines, content_width, tui._t("TAB_CONTRACT"))
    contract = str(getattr(detail, "contract", "") or "").strip()
    if not contract:
        _append_paragraph(tui, body_lines, content_width, tui._t("DETAIL_EMPTY_CONTRACT"), style="class:text.dim")
    else:
        _append_paragraph(tui, body_lines, content_width, contract, style="class:text")

    _append_blank_line(body_lines, content_width)
    _append_section_header(tui, body_lines, content_width, tui._t("DETAIL_DONE_CRITERIA"), compact=True)
    done_criteria = list(getattr(detail, "success_criteria", []) or [])
    if done_criteria:
        _append_numbered_list(tui, body_lines, content_width, done_criteria)
    else:
        _append_paragraph(tui, body_lines, content_width, tui._t("DETAIL_EMPTY_DONE_CRITERIA"), style="class:text.dim")

    _append_blank_line(body_lines, content_width)
    _append_section_header(tui, body_lines, content_width, tui._t("TESTS"), compact=True)
    tests = list(getattr(detail, "tests", []) or [])
    if tests:
        _append_numbered_list(tui, body_lines, content_width, tests)
    else:
        _append_paragraph(tui, body_lines, content_width, tui._t("DETAIL_EMPTY_TESTS"), style="class:text.dim")

    _append_blank_line(body_lines, content_width)
    _append_section_header(tui, body_lines, content_width, tui._t("BLOCKERS"), compact=True)
    blockers = list(getattr(detail, "blockers", []) or [])
    if blockers:
        _append_numbered_list(tui, body_lines, content_width, blockers)
    else:
        _append_paragraph(tui, body_lines, content_width, tui._t("DETAIL_EMPTY_BLOCKERS"), style="class:text.dim")

    return _compose_tab_view(
        tui=tui,
        tab="contract",
        content_width=content_width,
        header_lines=header_lines + tab_lines,
        body_lines=body_lines,
        footer_lines=footer_lines,
    )


def _render_meta_tab(tui) -> FormattedText:
    detail = tui.current_task_detail
    if not detail:
        return FormattedText([("class:text.dim", tui._t("STATUS_TASK_NOT_SELECTED"))])
    content_width = tui._detail_content_width()
    header_lines = _header_lines(tui, detail, content_width)
    tab_lines, tab_hitboxes = _tab_bar_lines(tui, content_width)
    tui._detail_tab_hitboxes = {"y": len(header_lines), "ranges": tab_hitboxes}
    footer_lines = _footer_lines(tui, content_width)

    body_lines: List[List[Tuple[str, str]]] = []
    _append_section_header(tui, body_lines, content_width, tui._t("TAB_META"))

    # ---- At-a-glance metadata (moved from the old Overview) ----
    domain_value = str(getattr(detail, "domain", "") or "").strip() or "-"
    phase_value = str(getattr(detail, "phase", "") or "").strip() or "-"
    component_value = str(getattr(detail, "component", "") or "").strip() or "-"
    parent_value = str(getattr(detail, "parent", "") or "").strip() or "-"

    status_map = {
        "DONE": tui._t("STATUS_DONE"),
        "ACTIVE": tui._t("STATUS_IN_PROGRESS"),
        "TODO": tui._t("STATUS_BACKLOG"),
    }
    status_value = status_map.get(str(getattr(detail, "status", "") or ""), str(getattr(detail, "status", "") or "-") or "-")
    progress_value = int(getattr(detail, "calculate_progress")() or 0)

    flat_subtasks = _flatten_steps(list(getattr(detail, "steps", []) or []))
    subtasks_total = len(flat_subtasks)
    subtasks_done = sum(1 for _, st in flat_subtasks if getattr(st, "completed", False)) if subtasks_total else 0

    priority_value = str(getattr(detail, "priority", "") or "").strip() or "-"
    tags_value = ", ".join(list(getattr(detail, "tags", []) or [])) or "-"
    _append_blank_line(body_lines, content_width)
    _append_paragraph(
        tui,
        body_lines,
        content_width,
        f"{tui._t('DOMAIN')}: {domain_value} | {tui._t('PHASE')}: {phase_value} | {tui._t('COMPONENT')}: {component_value} | {tui._t('PARENT')}: {parent_value}",
        style="class:text.dim",
    )
    _append_paragraph(
        tui,
        body_lines,
        content_width,
        f"{tui._t('STATUS')}: {status_value} | {tui._t('PROGRESS')}: {progress_value:>3}% | {tui._t('SUBTASKS')}: {subtasks_done}/{subtasks_total} | {tui._t('PRIORITY')}: {priority_value} | {tui._t('TAGS')}: {tags_value}",
        style="class:text.dim",
    )

    sections = [
        (tui._t("DETAIL_META_NEXT_STEPS"), list(getattr(detail, "next_steps", []) or [])),
        (tui._t("DETAIL_META_DEPENDENCIES"), list(getattr(detail, "dependencies", []) or [])),
        (tui._t("DETAIL_META_DEPENDS_ON"), list(getattr(detail, "depends_on", []) or [])),
        (tui._t("DETAIL_META_PROBLEMS"), list(getattr(detail, "problems", []) or [])),
        (tui._t("DETAIL_META_RISKS"), list(getattr(detail, "risks", []) or [])),
        (tui._t("DETAIL_META_HISTORY"), list(getattr(detail, "history", []) or [])),
    ]
    non_empty = [(title, items) for title, items in sections if items]
    for title, items in non_empty:
        _append_blank_line(body_lines, content_width)
        _append_section_header(tui, body_lines, content_width, f"{title} ({len(items)})", compact=True)
        _append_numbered_list(tui, body_lines, content_width, items)

    return _compose_tab_view(
        tui=tui,
        tab="meta",
        content_width=content_width,
        header_lines=header_lines + tab_lines,
        body_lines=body_lines,
        footer_lines=footer_lines,
    )


def _header_lines(tui, detail, content_width: int) -> List[List[Tuple[str, str]]]:
    inner = max(0, content_width - 2)
    lines: List[List[Tuple[str, str]]] = []

    # Top border
    lines.append([("class:border", "+" + "=" * content_width + "+")])

    # Status line (same layout as overview)
    status_map = {
        "DONE": ("class:icon.check", tui._t("STATUS_DONE")),
        "ACTIVE": ("class:icon.warn", tui._t("STATUS_IN_PROGRESS")),
        "TODO": ("class:icon.fail", tui._t("STATUS_BACKLOG")),
    }
    status_style, status_label = status_map.get(getattr(detail, "status", ""), ("class:icon.fail", getattr(detail, "status", "")))

    row: List[Tuple[str, str]] = [("class:border", "| ")]
    remaining = inner

    def push(style: str, text: str) -> None:
        nonlocal remaining
        if remaining <= 0:
            return
        chunk = tui._trim_display(text, remaining)
        row.append((style, chunk))
        remaining -= tui._display_width(chunk)

    push("class:header", f"{getattr(detail, 'id', '')} ")
    push("class:text.dim", "| ")
    push(status_style, status_label)
    push("class:text.dim", f" | {tui._t('PRIORITY')}: {getattr(detail, 'priority', '')}")
    push("class:text.dim", f" | {tui._t('PROGRESS')}: {getattr(detail, 'calculate_progress')():>3}%")
    if remaining > 0:
        row.append(("class:text.dim", " " * remaining))
    row.append(("class:border", " |"))
    lines.append(row)

    # Separator
    lines.append([("class:border", "+" + "=" * content_width + "+")])

    # Title
    title_display = str(getattr(detail, "title", "") or "")
    if getattr(tui, "horizontal_offset", 0) > 0:
        off = int(getattr(tui, "horizontal_offset", 0))
        title_display = title_display[off:] if len(title_display) > off else ""
    lines.append(
        [
            ("class:border", "| "),
            ("class:header", tui._pad_display(title_display, inner)),
            ("class:border", " |"),
        ]
    )
    lines.append([("class:border", "+" + "-" * content_width + "+")])
    return lines


def _tab_bar_lines(tui, content_width: int) -> tuple[List[List[Tuple[str, str]]], List[Tuple[int, int, str]]]:
    inner = max(0, content_width - 2)
    current = getattr(tui, "detail_tab", "overview") or "overview"
    detail = getattr(tui, "current_task_detail", None)
    all_tabs = detail_tab_definitions(tui, detail)
    allowed_ids = None
    try:
        allowed_ids = set(getattr(tui, "_detail_tabs")() or [])
    except Exception:
        allowed_ids = None
    tabs = [t for t in all_tabs if (allowed_ids is None or t[0] in allowed_ids)]

    row: List[Tuple[str, str]] = [("class:border", "| ")]
    remaining = inner
    x_cursor = 2  # account for leading "| "
    tab_hitboxes: List[Tuple[int, int, str]] = []

    def push(style: str, text: str, *, tab_id: str | None = None) -> None:
        nonlocal remaining, x_cursor
        if remaining <= 0:
            return
        chunk = tui._trim_display(text, remaining)
        if not chunk:
            return
        row.append((style, chunk))
        used = tui._display_width(chunk)
        if tab_id is not None and used > 0:
            tab_hitboxes.append((x_cursor, x_cursor + used, tab_id))
        x_cursor += used
        remaining -= used

    for idx, (tab, label) in enumerate(tabs):
        selected = tab == current
        style = "class:header" if selected else "class:text.dim"
        token = f"[{label}]" if selected else label
        if idx > 0:
            push("class:text.dim", "  ")
        push(style, token, tab_id=tab)
    if remaining > 0:
        row.append(("class:text.dim", " " * remaining))
    row.append(("class:border", " |"))

    return [row, [("class:border", "+" + "-" * content_width + "+")]], tab_hitboxes


def _footer_lines(tui, content_width: int) -> List[List[Tuple[str, str]]]:
    return [[("class:border", "+" + "=" * content_width + "+")]]


def _append_blank_line(body_lines: List[List[Tuple[str, str]]], content_width: int) -> None:
    inner = max(0, content_width - 2)
    body_lines.append([("class:border", "| "), ("class:text.dim", " " * inner), ("class:border", " |")])


def _append_section_header(
    tui,
    body_lines: List[List[Tuple[str, str]]],
    content_width: int,
    title: str,
    *,
    compact: bool = False,
) -> None:
    inner = max(0, content_width - 2)
    prefix = " " if compact else ""
    body_lines.append(
        [
            ("class:border", "| "),
            ("class:text.dim", tui._pad_display(f"{prefix}{title}:", inner)),
            ("class:border", " |"),
        ]
    )


def _append_paragraph(tui, body_lines: List[List[Tuple[str, str]]], content_width: int, text: str, *, style: str) -> None:
    inner = max(0, content_width - 2)
    raw = str(text or "")
    if getattr(tui, "horizontal_offset", 0) > 0:
        off = int(getattr(tui, "horizontal_offset", 0))
        raw = raw[off:] if len(raw) > off else ""
    for line in tui._wrap_display(raw, inner):
        body_lines.append([("class:border", "| "), (style, line), ("class:border", " |")])


def _append_numbered_list(tui, body_lines: List[List[Tuple[str, str]]], content_width: int, items: List[str]) -> None:
    inner = max(0, content_width - 2)
    for idx, item in enumerate(items, 1):
        prefix = f"  {idx}. "
        indent = " " * tui._display_width(prefix)
        wrapped = tui._wrap_with_prefix(prefix + str(item), inner, indent)
        for line_text, _ in wrapped:
            body_lines.append([("class:border", "| "), ("class:text", tui._pad_display(line_text, inner)), ("class:border", " |")])


def _compose_tab_view(
    *,
    tui,
    tab: str,
    content_width: int,
    header_lines: List[List[Tuple[str, str]]],
    body_lines: List[List[Tuple[str, str]]],
    footer_lines: List[List[Tuple[str, str]]],
) -> FormattedText:
    max_lines = max(5, tui.get_terminal_height() - tui.footer_height - 1)
    header_count = len(header_lines)
    footer_count = len(footer_lines)
    base_avail = max(1, max_lines - header_count - footer_count)

    desired_offset = int(getattr(tui, "detail_tab_scroll_offsets", {}).get(tab, 0) or 0)
    desired_offset = max(0, desired_offset)
    body_total = len(body_lines)

    def clamp_offset(avail: int) -> int:
        return min(desired_offset, max(0, body_total - avail))

    avail = base_avail
    offset = clamp_offset(avail)
    hidden_above = offset
    hidden_below = max(0, body_total - (offset + avail))

    marker_count = int(hidden_above > 0) + int(hidden_below > 0)
    if marker_count and avail > 1:
        avail = max(1, base_avail - marker_count)
        offset = clamp_offset(avail)
        hidden_above = offset
        hidden_below = max(0, body_total - (offset + avail))

    # Persist clamped offset for stable scrolling.
    tui.detail_tab_scroll_offsets[tab] = offset

    visible_body = body_lines[offset : offset + avail] if body_total else []
    composed: List[List[Tuple[str, str]]] = []
    composed.extend(header_lines)

    inner = max(0, content_width - 2)
    if hidden_above:
        composed.append([("class:border", "| "), ("class:text.dim", f"↑ +{hidden_above}".ljust(inner)), ("class:border", " |")])
    composed.extend(visible_body)
    if hidden_below:
        composed.append([("class:border", "| "), ("class:text.dim", f"↓ +{hidden_below}".ljust(inner)), ("class:border", " |")])

    composed.extend(footer_lines)

    # Final clamp to max_lines (prefer keeping header+footer).
    if len(composed) > max_lines:
        excess = len(composed) - max_lines
        if excess > 0 and visible_body:
            # Drop from the middle (body) region.
            drop_from = header_count + int(hidden_above > 0)
            keep_before = composed[:drop_from]
            keep_after = composed[drop_from + excess :]
            composed = keep_before + keep_after
        composed = composed[:max_lines]

    output: List[Tuple[str, str]] = []
    for idx, line in enumerate(composed):
        output.extend(line)
        if idx < len(composed) - 1:
            output.append(("", "\n"))
    return FormattedText(output)


__all__ = ["render_detail_tab_text"]
