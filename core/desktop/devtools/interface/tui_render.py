"""Rendering helpers for TaskTrackerTUI to keep the class slim."""

from typing import Dict, List, Tuple

from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.mouse_events import MouseEventType, MouseButton

from core import Status
from util.responsive import ResponsiveLayoutManager


def render_task_list_text(tui) -> FormattedText:
    return render_task_list_text_impl(tui)


def render_task_list_text_impl(tui) -> FormattedText:
    term_width = max(1, tui.get_terminal_width())
    if not tui.filtered_tasks:
        empty_width = min(term_width, max(10, min(80, term_width - 2)))
        tui.task_row_map = []
        return FormattedText([
            ('class:border', '+' + '-' * empty_width + '+\n'),
            ('class:text.dim', '| ' + tui._t("TASK_LIST_EMPTY").ljust(empty_width - 2) + ' |\n'),
            ('class:border', '+' + '-' * empty_width + '+'),
        ])

    result: List[Tuple[str, str]] = []
    tui.task_row_map = []
    line_counter = 0

    layout = ResponsiveLayoutManager.select_layout(term_width)
    desired_widths: Dict[str, int] = {}
    if layout.has_column('progress'):
        max_prog = max((len(f"{t.progress}%") for t in tui.filtered_tasks), default=4)
        desired_widths['progress'] = max(3, max_prog)
    if layout.has_column('subtasks'):
        max_sub = 0
        for t in tui.filtered_tasks:
            if t.subtasks_count:
                max_sub = max(max_sub, len(f"{t.subtasks_completed}/{t.subtasks_count}"))
            else:
                max_sub = max(max_sub, 1)
        desired_widths['subtasks'] = max(3, max_sub)

    widths = layout.calculate_widths(term_width, desired_widths)

    header_parts = []
    for col in layout.columns:
        if col in widths:
            header_parts.append('-' * widths[col])
    header_line = '+' + '+'.join(header_parts) + '+'
    header_style = 'class:border.dim'

    result.append((header_style, header_line + '\n'))
    line_counter += 1
    result.append((header_style, '|'))

    column_labels = {
        'idx': ('#', widths.get('idx', 3)),
        'stat': ('◉', widths.get('stat', 3)),
        'title': (tui._t("TABLE_HEADER_TASK"), widths.get('title', 20)),
        'progress': (tui._t("TABLE_HEADER_PROGRESS"), widths.get('progress', 4)),
        'subtasks': (tui._t("TABLE_HEADER_SUBTASKS"), widths.get('subtasks', 3)),
    }

    header_align = {
        'idx': 'center',
        'stat': 'center',
        'progress': 'center',
        'subtasks': 'center',
    }
    for col in layout.columns:
        if col in column_labels:
            label, width = column_labels[col]
            align = header_align.get(col, 'left')
            result.append(('class:header', tui._format_cell(label, width, align=align)))
            result.append(('class:border', '|'))

    result.append(('', '\n'))
    line_counter += 1
    result.append((header_style, header_line + '\n'))
    line_counter += 1

    compact_status_mode = len(layout.columns) <= 3
    visible_rows = tui._visible_row_limit()
    start_idx = min(tui.list_view_offset, max(0, len(tui.filtered_tasks) - visible_rows))
    end_idx = min(len(tui.filtered_tasks), start_idx + visible_rows)

    for idx in range(start_idx, end_idx):
        task = tui.filtered_tasks[idx]
        status_text, status_class, _ = tui._get_status_info(task)

        cell_data = {}

        if 'idx' in layout.columns:
            cell_data['idx'] = (tui._format_cell(str(idx), widths['idx'], align='center'), 'class:text.dim')

        if 'stat' in layout.columns:
            if compact_status_mode:
                marker = status_text if status_class != 'class:status.unknown' else '○'
                stat_width = widths['stat']
                marker_text = marker.center(stat_width) if stat_width > 1 else marker
                cell_data['stat'] = (marker_text, status_class)
            else:
                cell_data['stat'] = (tui._format_cell(status_text, widths['stat'], align='center'), status_class)

        if 'title' in layout.columns:
            title_scrolled = tui._apply_scroll(task.name)
            cell_data['title'] = (tui._format_cell(title_scrolled, widths['title']), 'class:text')

        if 'progress' in layout.columns:
            prog_text = f"{task.progress}%"
            prog_style = 'class:icon.check' if task.progress >= 100 else 'class:text.dim'
            cell_data['progress'] = (tui._format_cell(prog_text, widths['progress'], align='center'), prog_style)

        if 'subtasks' in layout.columns:
            subt_text = f"{task.subtasks_completed}/{task.subtasks_count}" if task.subtasks_count else "—"
            cell_data['subtasks'] = (tui._format_cell(subt_text, widths['subtasks'], align='center'), 'class:text.dim')

        row_line = line_counter
        style_key = tui._selection_style_for_status(task.status)
        selected = idx == tui.selected_index
        result.append(('class:border', '|'))
        for col in layout.columns:
            if col in cell_data:
                text, css_class = cell_data[col]
                cell_style = f"class:{style_key}" if selected else css_class
                result.append((cell_style, text))
                result.append(('class:border', '|'))

        tui.task_row_map.append((row_line, idx))
        result.append(('', '\n'))
        line_counter += 1

    result.append((header_style, header_line))
    return FormattedText(result)


def render_detail_text(tui) -> FormattedText:
    return render_detail_text_impl(tui)


def render_detail_text_impl(tui) -> FormattedText:
    if not tui.current_task_detail:
        return FormattedText([("class:text.dim", tui._t("STATUS_TASK_NOT_SELECTED"))])

    detail = tui.current_task_detail
    if not getattr(tui, "detail_flat_subtasks", []):
        tui._rebuild_detail_flat()
    if not getattr(tui, "detail_selected_path", "") and tui.detail_flat_subtasks:
        sel_idx = min(getattr(tui, "detail_selected_index", 0), len(tui.detail_flat_subtasks) - 1)
        tui.detail_selected_path = tui.detail_flat_subtasks[sel_idx][0]
    tui.subtask_row_map = []
    result: List[Tuple[str, str]] = []

    content_width = tui._detail_content_width()
    compact = tui.get_terminal_height() < 32 or content_width < 90

    result.append(('class:border', '+' + '='*content_width + '+\n'))
    inner_width = max(0, content_width - 2)
    result.append(('class:border', '| '))

    status_map = {
        'OK': ('class:icon.check', tui._t("STATUS_DONE")),
        'WARN': ('class:icon.warn', tui._t("STATUS_IN_PROGRESS")),
        'FAIL': ('class:icon.fail', tui._t("STATUS_BACKLOG")),
    }
    status_style, status_label = status_map.get(detail.status, ('class:icon.fail', detail.status))

    def _push(style: str, text: str) -> None:
        nonlocal inner_width
        if inner_width <= 0:
            return
        chunk = tui._trim_display(text, inner_width)
        result.append((style, chunk))
        inner_width -= tui._display_width(chunk)

    _push('class:header', f'{detail.id} ')
    _push('class:text.dim', '| ')
    _push(status_style, status_label)
    _push('class:text.dim', f' | {tui._t("PRIORITY")}: {detail.priority}')
    _push('class:text.dim', f' | {tui._t("PROGRESS")}: {detail.calculate_progress():>3}%')
    if inner_width > 0:
        result.append(('class:text.dim', ' ' * inner_width))
        inner_width = 0
    result.append(('class:border', ' |\n'))
    result.append(('class:border', '+' + '='*content_width + '+\n'))

    title_display = detail.title
    if tui.horizontal_offset > 0:
        title_display = title_display[tui.horizontal_offset:] if len(title_display) > tui.horizontal_offset else ""
    result.append(('class:border', '| '))
    result.append(('class:header', tui._pad_display(title_display, content_width - 2)))
    result.append(('class:border', ' |\n'))
    result.append(('class:border', '+' + '-'*content_width + '+\n'))

    meta_left = [
        f"{tui._t('DOMAIN')}: {detail.domain or '-'}",
        f"{tui._t('PHASE')}: {detail.phase or '-'}",
        f"{tui._t('COMPONENT')}: {detail.component or '-'}",
        f"{tui._t('PARENT')}: {detail.parent or '-'}",
    ]
    meta_right = [
        f"{tui._t('STATUS_DONE')}: {detail.status}",
        f"{tui._t('PROGRESS')}: {detail.calculate_progress():.0f}%",
        f"Σ: {len(detail.subtasks)}",
        f"{tui._t('TAGS')}: {', '.join(detail.tags) if detail.tags else '-'}",
    ]

    def _render_meta_row(left: str, right: str) -> None:
        inner = content_width - 2
        left_padded = tui._pad_display(left, inner//2)
        right_trimmed = tui._trim_display(right, inner - len(left_padded))
        line = f"{left_padded}{right_trimmed}"
        result.append(('class:border', '| '))
        result.append(('class:text.dim', tui._pad_display(line, inner)))
        result.append(('class:border', ' |\n'))

    for l, r in zip(meta_left, meta_right):
        _render_meta_row(l, r)

    result.append(('class:border', '+' + '-'*content_width + '+\n'))

    def _render_desc(label: str, text: str) -> None:
        if not text:
            return
        result.append(('class:border', '| '))
        result.append(('class:header', f"{label}:".ljust(content_width - 2)))
        result.append(('class:border', ' |\n'))
        for line in tui._wrap_display(text, content_width - 2):
            result.append(('class:border', '| '))
            result.append(('class:text', line))
            result.append(('class:border', ' |\n'))

    _render_desc(tui._t("DESCRIPTION"), detail.description or "-")
    if detail.context:
        _render_desc(tui._t("STATUS_CONTEXT"), detail.context)

    # ---------------- Subtask list with scrolling window -----------------
    tui._rebuild_detail_flat(getattr(tui, "detail_selected_path", None))
    items: List[Tuple[str, object, int, bool, bool]] = list(tui.detail_flat_subtasks)
    aux_sections = {"blockers": detail.blockers}

    total_items = len(items)
    used_lines = 0
    for frag in result:
        if isinstance(frag, tuple) and len(frag) >= 2:
            used_lines += frag[1].count('\n')
    list_budget = max(1, tui.get_terminal_height() - tui.footer_height - used_lines - 3)

    if total_items:
        tui.detail_selected_index = max(0, min(tui.detail_selected_index, total_items - 1))
        visible = min(total_items, list_budget)

        def _adjust_offset(vis: int) -> int:
            max_offset = max(0, total_items - vis)
            offset = min(getattr(tui, "detail_view_offset", 0), max_offset)
            if tui.detail_selected_index < offset:
                offset = tui.detail_selected_index
            elif tui.detail_selected_index >= offset + vis:
                offset = tui.detail_selected_index - vis + 1
            return max(0, min(offset, max_offset))

        tui.detail_view_offset = _adjust_offset(visible)
        start = tui.detail_view_offset
        end = min(total_items, start + visible)
        hidden_above = start
        hidden_below = total_items - end

        while True:
            marker_lines = int(hidden_above > 0) + int(hidden_below > 0)
            if visible + marker_lines <= list_budget:
                break
            if visible == 1:
                break
            visible = max(1, min(total_items, list_budget - marker_lines))
            tui.detail_view_offset = _adjust_offset(visible)
            start = tui.detail_view_offset
            end = min(total_items, start + visible)
            hidden_above = start
            hidden_below = total_items - end
    else:
        visible = 0
        start = end = 0
        hidden_above = hidden_below = 0
        tui.detail_view_offset = 0
    if items:
        tui._selected_subtask_entry()

    completed = sum(1 for _, st, _, _, _ in items if getattr(st, "completed", False))
    line_counter = 0
    above_marker_line = None
    below_marker_line = None
    for frag in result:
        if isinstance(frag, tuple) and len(frag) >= 2:
            line_counter += frag[1].count('\n')

    result.append(('class:border', '+' + '-'*content_width + '+\n'))
    result.append(('class:border', '| '))
    overflow_hint = ("↑" if hidden_above else "") + ("↓" if hidden_below else "")
    header = f"{overflow_hint + ' ' if overflow_hint else ''}{tui._t('SUBTASKS')} ({completed}/{len(items)} {tui._t('COMPLETED_SUFFIX')})"
    result.append(('class:header', header[: content_width - 2].ljust(content_width - 2)))
    result.append(('class:border', ' |\n'))
    line_counter += 1

    if hidden_above:
        above_marker_line = line_counter
        result.append(('class:border', '| '))
        result.append(('class:text.dim', f"↑ +{hidden_above}".ljust(content_width - 2)))
        result.append(('class:border', ' |\n'))
        line_counter += 1

    tui.subtask_row_map = []
    for global_idx in range(start, end):
        path, st, level, collapsed, has_children = items[global_idx]
        selected = global_idx == tui.detail_selected_index
        bg_style = f"class:{tui._selection_style_for_status(Status.OK if selected else None)}" if selected else None
        base_border = 'class:border'

        pointer = '>' if selected else ' '
        indicator = "▸" if (has_children and collapsed) else ("▾" if has_children else " ")
        base_prefix = f"{'  ' * level}{pointer}{indicator} {path} "

        st_title = st.title
        if tui.horizontal_offset > 0:
            st_title = st_title[tui.horizontal_offset:] if len(st_title) > tui.horizontal_offset else ""

        sub_status = tui._subtask_status(st)
        symbol, icon_class = tui._status_indicator(sub_status)
        if selected:
            icon_class = tui._merge_styles(icon_class, bg_style)

        prefix_len = len(base_prefix) + len(symbol) + 1

        row_line = line_counter
        result.append((base_border, '| '))
        result.append((tui._merge_styles('class:text', bg_style), base_prefix))
        result.append((icon_class, f"{symbol} "))

        flags = {
            "criteria": getattr(st, "criteria_confirmed", False),
            "tests": getattr(st, "tests_confirmed", False),
            "blockers": getattr(st, "blockers_resolved", False),
        }
        glyphs = [
            ('class:icon.check', '•') if flags['criteria'] else ('class:text.dim', '·'),
            ('class:icon.check', '•') if flags['tests'] else ('class:text.dim', '·'),
            ('class:icon.check', '•') if flags['blockers'] else ('class:text.dim', '·'),
        ]
        flag_text = []
        for idxf, (cls, symbol_f) in enumerate(glyphs):
            flag_text.append((cls, symbol_f))
            if idxf < 2:
                flag_text.append(('class:text.dim', ' '))
        flag_width = len(' [• • •]')
        title_width = max(5, content_width - 2 - prefix_len - flag_width)
        title_style = tui._merge_styles('class:text', bg_style) if selected else 'class:text'
        result.append((title_style, st_title[:title_width].ljust(title_width)))
        bracket_style = tui._merge_styles('class:text.dim', bg_style) if selected else 'class:text.dim'
        result.append((bracket_style, ' ['))
        for frag_style, frag_text in flag_text:
            style = tui._merge_styles(frag_style, bg_style) if selected else frag_style
            result.append((style, frag_text))
        result.append((bracket_style, ']'))
        result.append((base_border, ' |\n'))
        line_counter += 1
        tui.subtask_row_map.append((row_line, global_idx))

    if hidden_below:
        below_marker_line = line_counter
        result.append(('class:border', '| '))
        result.append(('class:text.dim', f"↓ +{hidden_below}".ljust(content_width - 2)))
        result.append(('class:border', ' |\n'))
        line_counter += 1

    selected_entry = tui._selected_subtask_entry() if items else None
    if selected_entry:
        remaining = max(0, tui.get_terminal_height() - tui.footer_height - line_counter - 1)
        if remaining > 2:
            _, st_sel, _, _, _ = selected_entry
            detail_lines: List[Tuple[str, str]] = []
            detail_lines.append(('class:border', '+' + '-'*content_width + '+\n'))
            detail_lines.append(('class:border', '| '))
            header = f"{tui._t('SUBTASK_DETAILS')}: {tui.detail_selected_path or ''}"
            detail_lines.append(('class:header', header[: content_width - 2].ljust(content_width - 2)))
            detail_lines.append(('class:border', ' |\n'))

            def _append_block(title: str, rows: List[str]) -> None:
                if not rows:
                    return
                detail_lines.append(('class:border', '| '))
                detail_lines.append(('class:text.dim', f" {title}:".ljust(content_width - 2)))
                detail_lines.append(('class:border', ' |\n'))
                for idxr, row in enumerate(rows, 1):
                    prefix = f"  {idxr}. "
                    raw = prefix + row
                    if tui.horizontal_offset > 0:
                        raw = raw[tui.horizontal_offset:] if len(raw) > tui.horizontal_offset else ""
                    for chunk, _ in tui._wrap_with_prefix(row, content_width - 2, prefix):
                        detail_lines.append(('class:border', '| '))
                        detail_lines.append(('class:text', chunk))
                        detail_lines.append(('class:border', ' |\n'))

            _append_block(tui._t("CRITERIA"), getattr(st_sel, "success_criteria", []))
            _append_block(tui._t("TESTS"), getattr(st_sel, "tests", []))
            _append_block(tui._t("BLOCKERS"), getattr(st_sel, "blockers", []))

            sliced = tui._slice_formatted_lines(detail_lines, 0, remaining)
            result.extend(sliced)
            line_counter += remaining

    section_titles = {"blockers": "Blockers:"}
    for key, entries in aux_sections.items():
        if not entries:
            continue
        result.append(('class:border', '+' + '-'*content_width + '+\n'))
        result.append(('class:border', '| '))
        result.append(('class:header', section_titles.get(key, key).ljust(content_width - 2)))
        result.append(('class:border', ' |\n'))
        for entry in entries:
            text = str(entry)
            if tui.horizontal_offset > 0:
                text = text[tui.horizontal_offset:] if len(text) > tui.horizontal_offset else ""
            chunks = [text[i:i+content_width-4] for i in range(0, len(text), content_width-4)] or ['']
            for ch in chunks:
                result.append(('class:border', '| '))
                result.append(('class:text', f"  - {ch}".ljust(content_width - 2)))
                result.append(('class:border', ' |\n'))

    result.append(('class:border', '+' + '='*content_width + '+'))

    formatted = tui._formatted_lines(result)
    max_lines = max(5, tui.get_terminal_height() - tui.footer_height - 1)
    computed_hidden_above = tui.detail_view_offset
    computed_hidden_below = max(0, total_items - (tui.detail_view_offset + len(tui.subtask_row_map)))
    focus_line = None
    for row_line, idx in tui.subtask_row_map:
        if idx == tui.detail_selected_index:
            focus_line = row_line
            break
    if focus_line is None:
        focus_line = 0
    needed = [focus_line]
    if above_marker_line is not None:
        needed.append(above_marker_line)
    if below_marker_line is not None:
        needed.append(below_marker_line)

    if len(formatted) > max_lines:
        min_needed = min(needed)
        max_needed = max(needed)
        start = max(0, min(min_needed, len(formatted) - max_lines))
        if max_needed >= start + max_lines:
            start = max(0, min(max_needed - max_lines + 1, len(formatted) - max_lines))
    else:
        start = 0
    end = min(len(formatted), start + max_lines)
    sliced = formatted[start:end]

    marker_up_line = [('class:border', '| '), ('class:text.dim', f"↑ +{computed_hidden_above}".ljust(content_width - 2)), ('class:border', ' |')] if computed_hidden_above else None
    marker_down_line = [('class:border', '| '), ('class:text.dim', f"↓ +{computed_hidden_below}".ljust(content_width - 2)), ('class:border', ' |')] if computed_hidden_below else None

    if computed_hidden_above:
        first_has_up = sliced and any('↑' in frag for _, frag in sliced[0])
        if not first_has_up:
            sliced.insert(0, marker_up_line)
            if len(sliced) > max_lines:
                sliced = sliced[:max_lines]
    if computed_hidden_below:
        last_has_down = sliced and any('↓' in frag for _, frag in sliced[-1])
        if not last_has_down:
            if len(sliced) >= max_lines:
                sliced = sliced[1:] if sliced else []
            sliced.append(marker_down_line)
    output: List[Tuple[str, str]] = []
    for i, line in enumerate(sliced):
        output.extend(line)
        if i < len(sliced) - 1:
            output.append(('', '\n'))
    return FormattedText(output)


def render_subtask_details(tui, path: str):
    return render_subtask_details_impl(tui, path)


def render_subtask_details_impl(tui, path: str):
    if not tui.current_task_detail:
        return
    subtask = tui._get_subtask_by_path(path)
    if not subtask:
        return
    tui._subtask_detail_buffer = []
    tui._select_subtask_by_path(path)
    tui._set_footer_height(0)
    term_width = tui.get_terminal_width()
    content_width = tui._detail_content_width(term_width)

    lines: List[Tuple[str, str]] = []
    group_id = 0

    def next_group() -> int:
        nonlocal group_id
        group_id += 1
        return group_id
    lines.append(('class:border', '+' + '='*content_width + '+\n'))

    def back_handler(event):
        if event.event_type == MouseEventType.MOUSE_UP and event.button == MouseButton.LEFT:
            tui.exit_detail_view()
            return None
        return NotImplemented

    sub_status = tui._subtask_status(subtask)
    symbol, icon_style = tui._status_indicator(sub_status)
    header_label = f"SUBTASK {path}"
    lines.append(('class:border', '| '))
    lines.append((icon_style, f"{symbol} "))
    inner_width = content_width - 2
    remaining = max(0, inner_width - 2)
    lines.append(('class:header', tui._pad_display(header_label, remaining)))
    lines.append(('class:border', ' |\n'))
    lines.append(('class:border', '+' + '-'*content_width + '+\n'))
    header_lines = tui._formatted_lines(lines)
    tui._subtask_header_lines_count = max(1, len(header_lines) - 1)

    for line in header_lines:
        line_text = "".join(text for _, text in line)
        tui._subtask_detail_buffer.append(('class:header', f"{line_text}\n"))

    text = subtask.title
    for ch in tui._wrap_display(text, content_width - 2):
        lines.append(('class:border', '| '))
        lines.append(('class:text', ch))
        lines.append(('class:border', ' |\n'))

    lines.append(('class:border', '+' + '-'*content_width + '+\n'))

    def append_indicator_row(entries: List[Tuple[str, bool]]):
        inner = content_width - 2
        lines.append(('class:border', '| '))
        for label, flag in entries:
            glyph = '✓' if flag else '·'
            glyph_style = 'class:icon.check' if flag else 'class:text.dim'
            chunk = f"[{glyph}] {label}"
            chunk = tui._pad_display(chunk, 12)
            lines.append((glyph_style, chunk))
            inner -= len(chunk)
        if inner > 0:
            lines.append(('class:text.dim', ' ' * inner))
        lines.append(('class:border', ' |\n'))

    append_indicator_row([
        (tui._t("CHECKPOINT_CRITERIA"), subtask.criteria_confirmed),
        (tui._t("CHECKPOINT_TESTS"), subtask.tests_confirmed),
        (tui._t("CHECKPOINT_BLOCKERS"), subtask.blockers_resolved),
    ])

    lines.append(('class:border', '+' + '-'*content_width + '+\n'))

    def append_section(title: str, items: List[str]):
        nonlocal group_id
        if not items:
            return
        lines.append(('class:border', '| '))
        lines.append(('class:header', f"{title}:".ljust(content_width - 2)))
        lines.append(('class:border', ' |\n'))
        tui._subtask_detail_buffer.append((tui._item_style(group_id), f"{title}\n"))
        group_id = next_group()
        for item in items:
            continuation = False
            for wrapped in tui._wrap_display(item, content_width - 4):
                style = tui._item_style(group_id, continuation=continuation)
                lines.append(('class:border', '|  '))
                lines.append(('class:text', wrapped))
                lines.append(('class:border', ' |\n'))
                tui._subtask_detail_buffer.append((style, f"{wrapped}\n"))
                continuation = True

    append_section(tui._t("CRITERIA"), subtask.success_criteria)
    append_section(tui._t("TESTS"), subtask.tests)
    append_section(tui._t("BLOCKERS"), subtask.blockers)
    append_section(tui._t("DESCRIPTION"), getattr(subtask, "notes", []) or [])

    lines.append(('class:border', '+' + '='*content_width + '+'))
    tui._subtask_detail_buffer.append((tui._item_style(next_group()), "footer\n"))
    tui._subtask_detail_total_lines = len(tui._subtask_detail_buffer)

    tui.detail_mode = True
    tui.subtask_detail_scroll = 0
    tui.subtask_detail_cursor = 0
    tui._render_single_subtask_view(max(40, tui.get_terminal_width() - 2))
    tui.force_render()


def _trim_to_height(fragments: List[Tuple[str, str]], max_lines: int) -> FormattedText:
    if max_lines <= 0:
        return FormattedText([])
    text = "".join(fragment for _, fragment in fragments)
    lines = text.split("\n")[:max_lines]
    clamped = "\n".join(lines)
    return FormattedText([("", clamped)])


def render_single_subtask_view(tui, content_width: int) -> None:
    """Apply vertical scroll to a single-subtask card, keeping header pinned."""
    if not getattr(tui, "_subtask_detail_buffer", None):
        return
    lines = tui._formatted_lines(tui._subtask_detail_buffer)
    total = len(lines)
    pinned = min(total, getattr(tui, "_subtask_header_lines_count", 0))
    scrollable = lines[pinned:]
    focusables = tui._focusable_line_indices(lines)
    if total:
        tui.subtask_detail_cursor = tui._snap_cursor(tui.subtask_detail_cursor, focusables)

    offset, visible_content, indicator_top, indicator_bottom, remaining_below = tui._calculate_subtask_viewport(
        total=len(lines),
        pinned=pinned,
    )

    visible_lines = scrollable[offset: offset + visible_content]

    rendered: List[Tuple[str, str]] = []
    for idx, line in enumerate(lines[:pinned]):
        global_idx = idx
        highlight = global_idx == tui.subtask_detail_cursor and global_idx in focusables
        style_prefix = 'class:selected' if highlight else None
        for frag_style, frag_text in line:
            is_border = frag_style and 'border' in frag_style
            style = tui._merge_styles(style_prefix, frag_style) if (highlight and not is_border) else frag_style
            rendered.append((style, frag_text))
        if pinned and idx < pinned - 1:
            rendered.append(('', '\n'))

    if pinned and (indicator_top or visible_lines):
        rendered.append(('', '\n'))

    if indicator_top:
        rendered.extend([
            ('class:border', '| '),
            ('class:text.dim', tui._pad_display(f"↑ +{offset}", content_width - 2)),
            ('class:border', ' |\n'),
        ])

    visible_meta: List[Tuple[List[Tuple[str, str]], int, int, bool]] = []
    selected_group: int | None = None
    for idx, line in enumerate(visible_lines):
        global_idx = pinned + offset + idx
        group = tui._extract_group(line)
        is_cursor = global_idx == tui.subtask_detail_cursor and global_idx in focusables
        if is_cursor:
            selected_group = group
        visible_meta.append((line, global_idx, group, is_cursor))

    for idx, (line, global_idx, group, is_cursor) in enumerate(visible_meta):
        highlight = is_cursor or (selected_group is not None and group == selected_group and group is not None)
        style_prefix = 'class:selected' if highlight else None
        for frag_style, frag_text in line:
            is_border = frag_style and 'border' in frag_style
            style = tui._merge_styles(style_prefix, frag_style) if (highlight and not is_border) else frag_style
            rendered.append((style, frag_text))
        if idx < len(visible_meta) - 1:
            rendered.append(('', '\n'))

    if indicator_bottom:
        if rendered and not rendered[-1][1].endswith('\n'):
            rendered.append(('', '\n'))
        rendered.extend([
            ('class:border', '| '),
            ('class:text.dim', tui._pad_display(f"↓ +{remaining_below}", content_width - 2)),
            ('class:border', ' |\n'),
        ])

    tui.single_subtask_view = FormattedText(rendered)
