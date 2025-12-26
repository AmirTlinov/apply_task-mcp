"""Rendering helpers for TaskTrackerTUI to keep the class slim."""
import re
from dataclasses import replace
from typing import Dict, List, Optional, Tuple

from prompt_toolkit.formatted_text import FormattedText

from core import Status
from util.responsive import ResponsiveLayoutManager
from core.desktop.devtools.interface.tui_detail_tree import canonical_path as _detail_canonical_path, node_kind as _detail_node_kind


def _merge_style(selected_style: Optional[str], fragment_style: str) -> str:
    if not selected_style:
        return fragment_style
    return f"{selected_style} {fragment_style}".strip()


def _display_row_id(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    for prefix in ("PLAN-", "TASK-", "STEP-", "NODE-"):
        if raw.upper().startswith(prefix):
            return raw[len(prefix):]
    return raw


_TITLE_ID_PREFIX = re.compile(r"^(PLAN|TASK|STEP|NODE)-\\d+[A-Z0-9./_-]*\\b", re.IGNORECASE)


def _strip_leading_id(title: str) -> str:
    raw = str(title or "").strip()
    if not raw:
        return ""
    match = _TITLE_ID_PREFIX.match(raw)
    if not match:
        return raw
    remainder = raw[match.end():].lstrip(" :#-/\\|")
    return remainder or raw


def _checkpoint_marks_fragments(node, width: int, *, selected_style: Optional[str] = None) -> List[Tuple[str, str]]:
    """Return per-dot styled fragments for criteria/tests marks, padded to `width`."""
    if width <= 0:
        return []

    dim = "class:text.dim"

    if node is None:
        return [(dim, " " * width)]

    has_any = any(
        hasattr(node, attr)
        for attr in (
            "criteria_confirmed",
            "tests_confirmed",
            "criteria_auto_confirmed",
            "tests_auto_confirmed",
            "success_criteria",
            "tests",
        )
    )
    if not has_any:
        return [(dim, " " * width)]

    criteria_ok = bool(getattr(node, "criteria_confirmed", False) or getattr(node, "criteria_auto_confirmed", False))
    tests_ok = bool(getattr(node, "tests_confirmed", False) or getattr(node, "tests_auto_confirmed", False))

    # Use the same glyph for both states; readiness is encoded by color.
    crit_symbol = "•"
    test_symbol = "•"

    crit_style = "class:icon.check" if criteria_ok else "class:text.dim"
    test_style = "class:icon.check" if tests_ok else "class:text.dim"

    if width >= 5:
        token: List[Tuple[str, str]] = [
            (dim, "["),
            (crit_style, crit_symbol),
            (dim, " "),
            (test_style, test_symbol),
            (dim, "]"),
        ]
        token_len = 5
    elif width == 4:
        token = [
            (dim, "["),
            (crit_style, crit_symbol),
            (test_style, test_symbol),
            (dim, "]"),
        ]
        token_len = 4
    elif width == 3:
        token = [
            (crit_style, crit_symbol),
            (dim, " "),
            (test_style, test_symbol),
        ]
        token_len = 3
    elif width == 2:
        token = [
            (crit_style, crit_symbol),
            (test_style, test_symbol),
        ]
        token_len = 2
    else:  # width == 1
        token = [(crit_style, crit_symbol)]
        token_len = 1

    if width <= token_len:
        return token

    pad_total = width - token_len
    left_pad = pad_total // 2
    right_pad = pad_total - left_pad
    fragments: List[Tuple[str, str]] = []
    if left_pad:
        fragments.append((dim, " " * left_pad))
    fragments.extend(token)
    if right_pad:
        fragments.append((dim, " " * right_pad))
    return fragments


def render_task_list_text(tui) -> FormattedText:
    return render_task_list_text_impl(tui)


def render_task_list_text_impl(tui) -> FormattedText:
    term_width = max(1, tui.get_terminal_width())
    filtered = tui.filtered_tasks
    if not filtered:
        empty_width = min(term_width, max(20, min(90, term_width - 2)))
        tui.task_row_map = []
        empty_key = "TASK_LIST_EMPTY"
        cta_key = "CTA_CREATE_TASK"
        if not getattr(tui, "project_mode", False) and getattr(tui, "project_section", "tasks") == "plans":
            empty_key = "TASK_LIST_EMPTY_PLANS"
            cta_key = "CTA_CREATE_PLAN"
        lines = [
            ('class:border', '+' + '-' * empty_width + '+\n'),
            ('class:text.dim', '| ' + tui._t(empty_key).ljust(empty_width - 2) + ' |\n'),
            ('class:text', '| ' + tui._t(cta_key).ljust(empty_width - 2) + ' |\n'),
            ('class:text', '| ' + tui._t("CTA_IMPORT_TASK").ljust(empty_width - 2) + ' |\n'),
            ('class:text.dim', '| ' + tui._t("CTA_DOMAIN_HINT").ljust(empty_width - 2) + ' |\n'),
            ('class:text.dim', '| ' + tui._t("CTA_KEYS_HINT").ljust(empty_width - 2) + ' |\n'),
            ('class:border', '+' + '-' * empty_width + '+'),
        ]
        return FormattedText(lines)

    result: List[Tuple[str, str]] = []
    tui.task_row_map = []
    line_counter = 0

    layout = ResponsiveLayoutManager.select_layout(term_width)
    project_mode = bool(getattr(tui, "project_mode", False))
    if project_mode and layout.has_column("id"):
        if layout.columns == ["idx", "id", "title"]:
            layout = replace(layout, columns=["idx", "stat", "title"])
        else:
            layout = replace(layout, columns=[c for c in layout.columns if c != "id"])
    desired_widths: Dict[str, int] = {}
    if layout.has_column('progress'):
        metrics = getattr(tui, "_filtered_tasks_metrics", {}) or {}
        max_prog = metrics.get("max_progress_len") or max((len(f"{t.progress}%") for t in filtered), default=4)
        desired_widths['progress'] = max(3, int(max_prog))
    if layout.has_column('id'):
        max_id = max((len(_display_row_id(getattr(t, "id", ""))) for t in filtered), default=2)
        desired_widths['id'] = max(2, int(max_id))
    if layout.has_column('children'):
        metrics = getattr(tui, "_filtered_tasks_metrics", {}) or {}
        max_sub = metrics.get("max_children_len") or 0
        if not max_sub:
            for t in filtered:
                done = int(getattr(t, "children_completed", 0) or 0)
                total = int(getattr(t, "children_count", 0) or 0)
                max_sub = max(max_sub, len(f"{done}/{total}"))
        desired_widths['children'] = max(3, int(max_sub), int(tui._display_width(tui._t("TABLE_HEADER_SUBTASKS"))))

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

    title_label = tui._t("TABLE_HEADER_TASK")
    if getattr(tui, "project_mode", False):
        title_label = tui._t("TABLE_HEADER_PROJECT", fallback="Проект")
    elif getattr(tui, "project_section", "tasks") == "plans":
        title_label = tui._t("TABLE_HEADER_PLAN", fallback=title_label)
    column_labels = {
        'idx': ('#', widths.get('idx', 3)),
        'id': (tui._t("TABLE_HEADER_ID"), widths.get('id', 4)),
        'stat': ('◉', widths.get('stat', 3)),
        'title': (title_label, widths.get('title', 20)),
        'marks': ('✓✓', widths.get('marks', 5)),
        'progress': (tui._t("TABLE_HEADER_PROGRESS"), widths.get('progress', 4)),
        'children': (tui._t("TABLE_HEADER_SUBTASKS"), widths.get('children', 3)),
    }

    header_align = {
        'idx': 'center',
        'id': 'center',
        'stat': 'center',
        'marks': 'center',
        'progress': 'center',
        'children': 'center',
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
    start_idx = min(tui.list_view_offset, max(0, len(filtered) - visible_rows))
    end_idx = min(len(filtered), start_idx + visible_rows)

    for idx in range(start_idx, end_idx):
        task = filtered[idx]
        status_text, status_class, _ = tui._get_status_info(task)
        selected = idx == tui.selected_index
        style_key = tui._selection_style_for_status(task.status)

        cell_data = {}

        if 'idx' in layout.columns:
            cell_data['idx'] = (tui._format_cell(str(idx + 1), widths['idx'], align='center'), 'class:text.dim')

        if 'id' in layout.columns:
            id_text = _display_row_id(getattr(task, "id", ""))
            cell_data['id'] = (tui._format_cell(id_text, widths['id'], align='center'), 'class:text.dim')

        if 'stat' in layout.columns:
            if compact_status_mode:
                marker = status_text if status_class != 'class:status.unknown' else '○'
                stat_width = widths['stat']
                marker_text = marker.center(stat_width) if stat_width > 1 else marker
                cell_data['stat'] = (marker_text, status_class)
            else:
                cell_data['stat'] = (tui._format_cell(status_text, widths['stat'], align='center'), status_class)

        if 'title' in layout.columns:
            title_scrolled = tui._apply_scroll(_strip_leading_id(task.name))
            cell_data['title'] = (tui._format_cell(title_scrolled, widths['title']), 'class:text')

        if 'marks' in layout.columns:
            detail = getattr(task, "detail", None)
            selected_style = f"class:{style_key}" if selected else None
            cell_data['marks'] = _checkpoint_marks_fragments(detail, widths['marks'], selected_style=selected_style)

        if 'progress' in layout.columns:
            prog_text = f"{task.progress}%"
            prog_style = 'class:icon.check' if task.progress >= 100 else 'class:text.dim'
            cell_data['progress'] = (tui._format_cell(prog_text, widths['progress'], align='center'), prog_style)

        if 'children' in layout.columns:
            done = int(getattr(task, "children_completed", 0) or 0)
            total = int(getattr(task, "children_count", 0) or 0)
            subt_text = f"{done}/{total}"
            cell_data['children'] = (tui._format_cell(subt_text, widths['children'], align='center'), 'class:text.dim')

        row_line = line_counter
        result.append(('class:border', '|'))
        for col in layout.columns:
            if col in cell_data:
                if col == 'marks':
                    result.extend(cell_data[col])
                else:
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

    if getattr(tui, "detail_tab", "overview") != "overview":
        from core.desktop.devtools.interface.tui_detail_tabs import render_detail_tab_text
        return render_detail_tab_text(tui)

    detail = tui.current_task_detail
    if getattr(detail, "kind", "task") != "plan":
        if hasattr(tui, "_ensure_detail_flat"):
            tui._ensure_detail_flat(getattr(tui, "detail_selected_path", None))
        elif not getattr(tui, "detail_flat_subtasks", []):
            tui._rebuild_detail_flat()
        if not getattr(tui, "detail_selected_path", "") and tui.detail_flat_subtasks:
            sel_idx = min(getattr(tui, "detail_selected_index", 0), len(tui.detail_flat_subtasks) - 1)
            tui.detail_selected_path = tui.detail_flat_subtasks[sel_idx].key
    tui.subtask_row_map = []
    result: List[Tuple[str, str]] = []

    content_width = tui._detail_content_width()

    result.append(('class:border', '+' + '='*content_width + '+\n'))
    inner_width = max(0, content_width - 2)
    result.append(('class:border', '| '))

    status_map = {
        "DONE": ("class:icon.check", tui._t("STATUS_DONE")),
        "ACTIVE": ("class:icon.warn", tui._t("STATUS_IN_PROGRESS")),
        "TODO": ("class:icon.fail", tui._t("STATUS_BACKLOG")),
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

    # Breadcrumbs / navigation context (stable even for deep nesting).
    try:
        _, _, path_prefix = tui._get_root_task_context()
    except Exception:
        path_prefix = ""
    path_display = tui._display_subtask_path(path_prefix) if path_prefix else "—"
    crumb_line = tui._t("DETAIL_BREADCRUMBS", path=path_display)
    result.append(('class:border', '| '))
    result.append(('class:text.dim', tui._pad_display(crumb_line, content_width - 2)))
    result.append(('class:border', ' |\n'))
    result.append(('class:border', '+' + '-'*content_width + '+\n'))

    # ---------- Tab bar (overview is a tab too) ----------
    inner_width = max(0, content_width - 2)
    current_tab = getattr(tui, "detail_tab", "overview") or "overview"
    from core.desktop.devtools.interface.tui_detail_tabs import detail_tab_definitions

    all_tabs = detail_tab_definitions(tui, detail)
    allowed_ids = None
    try:
        allowed_ids = set(getattr(tui, "_detail_tabs")() or [])
    except Exception:
        allowed_ids = None
    tabs = [t for t in all_tabs if (allowed_ids is None or t[0] in allowed_ids)]
    row: List[Tuple[str, str]] = [('class:border', '| ')]
    remaining = inner_width
    tabbar_y = sum(text.count("\n") for _, text in result)
    tab_hitboxes: List[Tuple[int, int, str]] = []
    x_cursor = 2  # account for leading "| "

    def _push_tab(style: str, text: str, *, tab_id: str | None = None) -> None:
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

    for idx_tab, (tab_id, label) in enumerate(tabs):
        selected = tab_id == current_tab
        style = "class:header" if selected else "class:text.dim"
        token = f"[{label}]" if selected else label
        if idx_tab > 0:
            _push_tab("class:text.dim", "  ")
        _push_tab(style, token, tab_id=tab_id)
    if remaining > 0:
        row.append(("class:text.dim", " " * remaining))
    row.append(("class:border", " |\n"))
    result.extend(row)
    result.append(('class:border', '+' + '-'*content_width + '+\n'))
    tui._detail_tab_hitboxes = {"y": tabbar_y, "ranges": tab_hitboxes}

    # Flagship density: keep the subtasks list as the primary content.
    # ---------------- Overview list with scrolling window -----------------
    aux_sections = {}

    used_lines = 0
    for frag in result:
        if isinstance(frag, tuple) and len(frag) >= 2:
            used_lines += frag[1].count('\n')
    list_budget = max(1, tui.get_terminal_height() - tui.footer_height - used_lines - 3)
    inner_width = max(1, content_width - 2)
    table_width = inner_width + 2

    def _count_steps(nodes) -> tuple[int, int]:
        """Count nested steps (iterative, no recursion). Returns (total, done)."""
        total = 0
        done = 0
        stack = [iter(list(nodes or []))]
        while stack:
            try:
                node = next(stack[-1])
            except StopIteration:
                stack.pop()
                continue
            total += 1
            if getattr(node, "completed", False):
                done += 1
            plan = getattr(node, "plan", None)
            tasks = list(getattr(plan, "tasks", []) or []) if plan else []
            for task in reversed(tasks):
                child_steps = list(getattr(task, "steps", []) or [])
                if child_steps:
                    stack.append(iter(child_steps))
        return total, done

    def _display_node_path(node_key: str) -> str:
        kind = _detail_node_kind(node_key)
        if kind == "plan":
            return tui._display_subtask_path(_detail_canonical_path(node_key, kind)) + ".P"
        return tui._display_subtask_path(node_key)

    def _detail_table_config(rows: List[Dict[str, object]], title_label: str):
        # Detail views lose a couple of columns worth of width vs the main list view due to the
        # outer frame. Select the layout as if we had the full terminal width so the column set
        # stays consistent with the Plans table, then shrink columns during width calculation.
        layout = ResponsiveLayoutManager.select_layout(table_width + 2)
        required_cols = {"id", "marks", "progress", "children"}
        if not required_cols.issubset(set(layout.columns)):
            for candidate in reversed(ResponsiveLayoutManager.LAYOUTS):
                if required_cols.issubset(set(candidate.columns)):
                    layout = candidate
                    break
        desired_widths: Dict[str, int] = {}
        if layout.has_column('progress'):
            max_prog = max((len(f"{r['progress']}%") for r in rows), default=3)
            desired_widths['progress'] = max(3, max_prog)
        if layout.has_column('id'):
            max_id = max((len(str(r.get("id_value", "") or "")) for r in rows), default=2)
            desired_widths['id'] = max(2, max_id)
        if layout.has_column('children'):
            max_sub = max((len(f"{r['children_done']}/{r['children_total']}") for r in rows), default=3)
            desired_widths['children'] = max(3, max_sub, int(tui._display_width(tui._t("TABLE_HEADER_SUBTASKS"))))
        widths = layout.calculate_widths(table_width, desired_widths)
        column_labels = {
            'idx': ('#', widths.get('idx', 3)),
            'id': (tui._t("TABLE_HEADER_ID"), widths.get('id', 4)),
            'stat': ('◉', widths.get('stat', 3)),
            'title': (title_label, widths.get('title', 20)),
            'marks': ('✓✓', widths.get('marks', 5)),
            'progress': (tui._t("TABLE_HEADER_PROGRESS"), widths.get('progress', 4)),
            'children': (tui._t("TABLE_HEADER_SUBTASKS"), widths.get('children', 3)),
        }
        header_align = {
            'idx': 'center',
            'id': 'center',
            'stat': 'center',
            'marks': 'center',
            'progress': 'center',
            'children': 'center',
        }
        separator_line = "+".join("-" * widths[col] for col in layout.columns)
        return layout, widths, column_labels, header_align, separator_line

    # Plan overview shows Tasks in plan; Task overview shows nested Steps.
    if getattr(detail, "kind", "task") == "plan":
        plan_tasks = tui._plan_detail_tasks()
        cache_key = getattr(tui, "_detail_plan_tasks_cache_key", None)
        plan_rows: List[Dict[str, int]] = []
        cached_rows = None
        cached_summary = None
        if cache_key and not getattr(tui, "_detail_plan_tasks_dirty", False):
            cached_key = getattr(tui, "_detail_plan_rows_cache_key", None)
            if cached_key == cache_key:
                cached_rows = getattr(tui, "_detail_plan_rows_cache", None)
                cached_summary = getattr(tui, "_detail_plan_summary_cache", None)
        if cached_rows is not None and len(cached_rows) == len(plan_tasks) and cached_summary:
            plan_rows = cached_rows
            total_items = int(cached_summary.get("total", len(plan_tasks)))
            completed = int(cached_summary.get("completed", 0))
        else:
            completed = 0
            for t in plan_tasks:
                blocked = bool(getattr(t, "blocked", False))
                steps_total, steps_done = (
                    tui._cached_step_tree_counts(t)
                    if hasattr(tui, "_cached_step_tree_counts")
                    else _count_steps(list(getattr(t, "steps", []) or []))
                )
                if steps_total > 0:
                    prog = int((steps_done / steps_total) * 100)
                else:
                    try:
                        prog = int(getattr(t, "calculate_progress")() or 0)
                    except Exception:
                        prog = int(getattr(t, "progress", 0) or 0)
                status_raw = str(getattr(t, "status", "") or "").strip().upper()
                if prog == 100 and not blocked:
                    status_raw = "DONE"
                status_obj = Status.from_string(status_raw)
                if status_obj == Status.DONE:
                    completed += 1
                symbol, icon_class = tui._status_indicator(status_obj)
                plan_rows.append({
                    "id_value": _display_row_id(getattr(t, "id", "")),
                    "progress": prog,
                    "children_done": steps_done,
                    "children_total": steps_total,
                    "status_obj": status_obj,
                    "status_symbol": symbol,
                    "status_class": icon_class,
                })
            total_items = len(plan_tasks)
            if cache_key:
                tui._detail_plan_rows_cache = list(plan_rows)
                tui._detail_plan_rows_cache_key = cache_key
                tui._detail_plan_summary_cache = {"total": total_items, "completed": completed}
        layout, widths, column_labels, header_align, separator_line = _detail_table_config(
            plan_rows,
            tui._t("TABLE_HEADER_TASK"),
        )
        table_overhead = 3
        rows_budget = max(1, list_budget - table_overhead)

        line_counter = 0
        above_marker_line = None
        below_marker_line = None
        for frag in result:
            if isinstance(frag, tuple) and len(frag) >= 2:
                line_counter += frag[1].count('\n')

        if total_items:
            tui.detail_selected_index = max(0, min(tui.detail_selected_index, total_items - 1))
            visible = min(total_items, rows_budget)

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
                if visible + marker_lines <= rows_budget:
                    break
                if visible == 1:
                    break
                visible = max(1, min(total_items, rows_budget - marker_lines))
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
            tui.detail_selected_index = 0

        result.append(("class:border", "| "))
        header_label = tui._t("TAB_TASKS").upper()
        overflow_hint = ("↑" if hidden_above else "") + ("↓" if hidden_below else "")
        header = f"{overflow_hint + ' ' if overflow_hint else ''}{header_label} ({completed}/{total_items} {tui._t('COMPLETED_SUFFIX')})"
        result.append(("class:header", header[: content_width - 2].ljust(content_width - 2)))
        result.append(("class:border", " |\n"))
        line_counter += 1

        if hidden_above:
            above_marker_line = line_counter
            result.append(("class:border", "| "))
            result.append(("class:text.dim", f"↑ +{hidden_above}".ljust(content_width - 2)))
            result.append(("class:border", " |\n"))
            line_counter += 1

        result.append(("class:border.dim", "| "))
        result.append(("class:border.dim", separator_line.ljust(inner_width)))
        result.append(("class:border.dim", " |\n"))
        line_counter += 1

        result.append(("class:border", "| "))
        for idx_col, col in enumerate(layout.columns):
            if idx_col > 0:
                result.append(("class:border", "|"))
            label, width = column_labels[col]
            align = header_align.get(col, "left")
            result.append(("class:header", tui._format_cell(label, width, align=align)))
        result.append(("class:border", " |\n"))
        line_counter += 1

        result.append(("class:border.dim", "| "))
        result.append(("class:border.dim", separator_line.ljust(inner_width)))
        result.append(("class:border.dim", " |\n"))
        line_counter += 1

        tui.subtask_row_map = []
        for global_idx in range(start, end):
            t = plan_tasks[global_idx]
            row_data = plan_rows[global_idx]
            status_obj = row_data["status_obj"]
            symbol = row_data["status_symbol"]
            icon_class = row_data["status_class"]
            selected = global_idx == tui.detail_selected_index
            style_key = tui._selection_style_for_status(status_obj)
            compact_status_mode = len(layout.columns) <= 3
            cell_data = {}

            if 'idx' in layout.columns:
                cell_data['idx'] = (tui._format_cell(str(global_idx + 1), widths['idx'], align='center'), 'class:text.dim')

            if 'id' in layout.columns:
                id_text = str(row_data.get("id_value", "") or "")
                cell_data['id'] = (tui._format_cell(id_text, widths['id'], align='center'), 'class:text.dim')

            if 'stat' in layout.columns:
                if compact_status_mode:
                    marker = symbol if icon_class != 'class:status.unknown' else '○'
                    stat_width = widths['stat']
                    marker_text = marker.center(stat_width) if stat_width > 1 else marker
                    cell_data['stat'] = (marker_text, icon_class)
                else:
                    cell_data['stat'] = (tui._format_cell(symbol, widths['stat'], align='center'), icon_class)

            if 'title' in layout.columns:
                title = _strip_leading_id(f"{getattr(t, 'title', '')}".strip())
                title_scrolled = tui._apply_scroll(title)
                cell_data['title'] = (tui._format_cell(title_scrolled, widths['title']), 'class:text')

            if 'marks' in layout.columns:
                selected_style = f"class:{style_key}" if selected else None
                cell_data['marks'] = _checkpoint_marks_fragments(t, widths['marks'], selected_style=selected_style)

            if 'progress' in layout.columns:
                prog_text = f"{row_data['progress']}%"
                prog_style = 'class:icon.check' if row_data['progress'] >= 100 else 'class:text.dim'
                cell_data['progress'] = (tui._format_cell(prog_text, widths['progress'], align='center'), prog_style)

            if 'children' in layout.columns:
                subt_text = f"{row_data['children_done']}/{row_data['children_total']}"
                cell_data['children'] = (tui._format_cell(subt_text, widths['children'], align='center'), 'class:text.dim')

            row_line = line_counter
            result.append(("class:border", "| "))
            for idx_col, col in enumerate(layout.columns):
                if idx_col > 0:
                    result.append(("class:border", "|"))
                if col == 'marks':
                    result.extend(cell_data[col])
                else:
                    text, css_class = cell_data[col]
                    cell_style = f"class:{style_key}" if selected else css_class
                    result.append((cell_style, text))
            result.append(("class:border", " |\n"))
            line_counter += 1
            tui.subtask_row_map.append((row_line, global_idx))

        if hidden_below:
            below_marker_line = line_counter
            result.append(("class:border", "| "))
            result.append(("class:text.dim", f"↓ +{hidden_below}".ljust(content_width - 2)))
            result.append(("class:border", " |\n"))
            line_counter += 1
    else:
        if hasattr(tui, "_ensure_detail_flat"):
            tui._ensure_detail_flat(getattr(tui, "detail_selected_path", None))
        else:
            tui._rebuild_detail_flat(getattr(tui, "detail_selected_path", None))
        items = list(tui.detail_flat_subtasks or [])
        if any(getattr(entry, "kind", "") != "step" for entry in items):
            items = [entry for entry in items if getattr(entry, "kind", "") == "step"]
            tui.detail_selected_index = max(0, min(tui.detail_selected_index, max(0, len(items) - 1)))
            tui.detail_selected_path = items[tui.detail_selected_index].key if items else ""
        stats_by_key = dict(getattr(tui, "detail_stats_by_key", {}) or {})
        node_rows: List[Dict[str, object]] = []
        for entry in items:
            stats = stats_by_key.get(entry.key)
            if stats is None:
                status_obj = Status.TODO
                symbol, icon_class = tui._status_indicator(status_obj)
                progress = 0
                children_done = 0
                children_total = 0
            else:
                status_obj = stats.status
                symbol, icon_class = tui._status_indicator(status_obj)
                progress = int(getattr(stats, "progress", 0) or 0)
                children_done = int(getattr(stats, "children_done", 0) or 0)
                children_total = int(getattr(stats, "children_total", 0) or 0)

            node_rows.append(
                {
                    "id_value": _display_row_id(getattr(entry.node, "id", "")),
                    "progress": progress,
                    "children_done": children_done,
                    "children_total": children_total,
                    "status_obj": status_obj,
                    "status_symbol": symbol,
                    "status_class": icon_class,
                }
            )

        total_items = len(items)
        layout, widths, column_labels, header_align, separator_line = _detail_table_config(
            node_rows,
            tui._t("LIST_EDITOR_SCOPE_SUBTASK", fallback=tui._t("DETAIL_STEPS")),
        )
        table_overhead = 3
        rows_budget = max(1, list_budget - table_overhead)
        if total_items:
            tui.detail_selected_index = max(0, min(tui.detail_selected_index, total_items - 1))
            visible = min(total_items, rows_budget)

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
                if visible + marker_lines <= rows_budget:
                    break
                if visible == 1:
                    break
                visible = max(1, min(total_items, rows_budget - marker_lines))
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

        # Drill-down: task detail shows exactly one level (steps list).
        visible_steps = [e for e in items if getattr(e, "kind", "") == "step"]
        step_total = len(visible_steps)
        completed = sum(1 for e in visible_steps if bool(getattr(getattr(e, "node", None), "completed", False)))
        line_counter = 0
        above_marker_line = None
        below_marker_line = None
        for frag in result:
            if isinstance(frag, tuple) and len(frag) >= 2:
                line_counter += frag[1].count('\n')

        result.append(('class:border', '| '))
        overflow_hint = ("↑" if hidden_above else "") + ("↓" if hidden_below else "")
        header = f"{overflow_hint + ' ' if overflow_hint else ''}{tui._t('SUBTASKS')} ({completed}/{step_total} {tui._t('COMPLETED_SUFFIX')})"
        result.append(('class:header', header[: content_width - 2].ljust(content_width - 2)))
        result.append(('class:border', ' |\n'))
        line_counter += 1

        if hidden_above:
            above_marker_line = line_counter
            result.append(('class:border', '| '))
            result.append(('class:text.dim', f"↑ +{hidden_above}".ljust(content_width - 2)))
            result.append(('class:border', ' |\n'))
            line_counter += 1

        result.append(("class:border.dim", "| "))
        result.append(("class:border.dim", separator_line.ljust(inner_width)))
        result.append(("class:border.dim", " |\n"))
        line_counter += 1

        result.append(("class:border", "| "))
        for idx_col, col in enumerate(layout.columns):
            if idx_col > 0:
                result.append(("class:border", "|"))
            label, width = column_labels[col]
            align = header_align.get(col, "left")
            result.append(("class:header", tui._format_cell(label, width, align=align)))
        result.append(("class:border", " |\n"))
        line_counter += 1

        result.append(("class:border.dim", "| "))
        result.append(("class:border.dim", separator_line.ljust(inner_width)))
        result.append(("class:border.dim", " |\n"))
        line_counter += 1

        tui.subtask_row_map = []
        for global_idx in range(start, end):
            entry = items[global_idx]
            path = entry.key
            node = entry.node
            row_data = node_rows[global_idx]
            selected = global_idx == tui.detail_selected_index
            title_raw = _strip_leading_id(str(getattr(node, "title", "") or "").strip())
            if not title_raw:
                title_raw = tui._t("LIST_EDITOR_SCOPE_SUBTASK", fallback="Step")

            if tui.horizontal_offset > 0:
                title_raw = title_raw[tui.horizontal_offset:] if len(title_raw) > tui.horizontal_offset else ""

            status_obj = row_data["status_obj"]
            symbol = row_data["status_symbol"]
            icon_class = row_data["status_class"]
            style_key = tui._selection_style_for_status(status_obj)
            compact_status_mode = len(layout.columns) <= 3
            cell_data = {}

            if 'idx' in layout.columns:
                cell_data['idx'] = (tui._format_cell(str(global_idx + 1), widths['idx'], align='center'), 'class:text.dim')

            if 'id' in layout.columns:
                id_text = str(row_data.get("id_value", "") or "")
                cell_data['id'] = (tui._format_cell(id_text, widths['id'], align='center'), 'class:text.dim')

            if 'stat' in layout.columns:
                if compact_status_mode:
                    marker = symbol if icon_class != 'class:status.unknown' else '○'
                    stat_width = widths['stat']
                    marker_text = marker.center(stat_width) if stat_width > 1 else marker
                    cell_data['stat'] = (marker_text, icon_class)
                else:
                    cell_data['stat'] = (tui._format_cell(symbol, widths['stat'], align='center'), icon_class)

            if 'title' in layout.columns:
                cell_data['title'] = (tui._format_cell(title_raw, widths['title']), 'class:text')

            if 'marks' in layout.columns:
                selected_style = f"class:{style_key}" if selected else None
                cell_data['marks'] = _checkpoint_marks_fragments(node, widths['marks'], selected_style=selected_style)

            if 'progress' in layout.columns:
                prog_text = f"{row_data['progress']}%"
                prog_style = 'class:icon.check' if row_data['progress'] >= 100 else 'class:text.dim'
                cell_data['progress'] = (tui._format_cell(prog_text, widths['progress'], align='center'), prog_style)

            if 'children' in layout.columns:
                subt_text = f"{row_data['children_done']}/{row_data['children_total']}"
                cell_data['children'] = (tui._format_cell(subt_text, widths['children'], align='center'), 'class:text.dim')

            row_line = line_counter
            result.append(('class:border', '| '))
            for idx_col, col in enumerate(layout.columns):
                if idx_col > 0:
                    result.append(('class:border', '|'))
                if col == 'marks':
                    result.extend(cell_data[col])
                else:
                    text, css_class = cell_data[col]
                    cell_style = f"class:{style_key}" if selected else css_class
                    result.append((cell_style, text))
            result.append(('class:border', ' |\n'))
            line_counter += 1
            tui.subtask_row_map.append((row_line, global_idx))

        if hidden_below:
            below_marker_line = line_counter
            result.append(('class:border', '| '))
            result.append(('class:text.dim', f"↓ +{hidden_below}".ljust(content_width - 2)))
            result.append(('class:border', ' |\n'))
            line_counter += 1

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


def _trim_to_height(fragments: List[Tuple[str, str]], max_lines: int) -> FormattedText:
    if max_lines <= 0:
        return FormattedText([])
    text = "".join(fragment for _, fragment in fragments)
    lines = text.split("\n")[:max_lines]
    clamped = "\n".join(lines)
    return FormattedText([("", clamped)])


def render_checkpoint_view(tui) -> FormattedText:
    return render_checkpoint_view_impl(tui)


def render_checkpoint_view_impl(tui) -> FormattedText:
    detail = getattr(tui, "current_task_detail", None)
    if not detail:
        return FormattedText([("class:text.dim", tui._t("STATUS_TASK_NOT_SELECTED"))])

    target = None
    target_title = ""
    if getattr(detail, "kind", "task") == "plan":
        target = detail
        target_title = str(getattr(detail, "title", "") or "")
    else:
        entry = tui._selected_subtask_entry() if hasattr(tui, "_selected_subtask_entry") else None
        if not entry and hasattr(tui, "_rebuild_detail_flat"):
            try:
                tui._rebuild_detail_flat(getattr(tui, "detail_selected_path", "") or None)
                entry = tui._selected_subtask_entry() if hasattr(tui, "_selected_subtask_entry") else None
            except Exception:
                entry = None
        if not entry:
            return FormattedText([("class:text.dim", tui._t("ERR_SUBTASK_NOT_FOUND"))])
        target = getattr(entry, "node", None)
        if not target:
            return FormattedText([("class:text.dim", tui._t("ERR_SUBTASK_NOT_FOUND"))])
        target_title = str(getattr(target, "title", "") or "")
        if getattr(entry, "kind", "") == "plan":
            target_title = target_title.strip() or tui._t("TAB_PLAN")
        elif getattr(entry, "kind", "") == "task":
            target_title = target_title.strip() or tui._t("TABLE_HEADER_TASK")

    term_width = max(1, tui.get_terminal_width())
    content_width = tui._detail_content_width(term_width)

    result: List[Tuple[str, str]] = []

    # Compact Header (similar to task list)
    header_line = '+' + '-' * (content_width - 2) + '+'
    header_style = 'class:border.dim'

    result.append((header_style, header_line + '\n'))

    # Title Row
    title_display = f"{tui._t('CHECKPOINTS')}: {target_title}"
    if tui.horizontal_offset > 0:
        title_display = title_display[tui.horizontal_offset:] if len(title_display) > tui.horizontal_offset else ""

    result.append(('class:border', '| '))
    result.append(('class:header', tui._pad_display(title_display, content_width - 4)))
    result.append(('class:border', ' |\n'))

    result.append((header_style, header_line + '\n'))

    # Checkpoints List
    checkpoints_data = [
        ("criteria", tui._t("CHECKPOINT_CRITERIA"), bool(getattr(target, "criteria_confirmed", False) or getattr(target, "criteria_auto_confirmed", False)), list(getattr(target, "success_criteria", []) or [])),
        ("tests", tui._t("CHECKPOINT_TESTS"), bool(getattr(target, "tests_confirmed", False) or getattr(target, "tests_auto_confirmed", False)), list(getattr(target, "tests", []) or [])),
    ]

    tui.checkpoint_row_map = []
    line_counter = 0
    # Count header lines
    for frag in result:
        if isinstance(frag, tuple) and len(frag) >= 2:
            line_counter += frag[1].count('\n')

    for idx, (key, label, done, content_items) in enumerate(checkpoints_data):
        selected = idx == getattr(tui, "checkpoint_selected_index", 0)
        style_key = "selected" if selected else "text"
        bg_style = "class:selected" if selected else None

        # Row Border
        result.append(('class:border', '|'))

        # Checkbox
        checkbox = "[x]" if done else "[ ]"
        checkbox_style = "class:icon.check" if done else "class:text.dim"
        if selected:
            checkbox_style = tui._merge_styles(checkbox_style, bg_style)

        result.append((checkbox_style, f" {checkbox} "))

        # Label
        label_text = label.ljust(content_width - 8)
        label_style = tui._merge_styles(f"class:{style_key}", bg_style) if selected else f"class:{style_key}"
        result.append((label_style, label_text))

        result.append(('class:border', '|\n'))
        tui.checkpoint_row_map.append((line_counter, idx))
        line_counter += 1

        # Render Content (always visible or only when selected? Plan said "below the header row when selected or always". Let's do always for now as it's cleaner)
        if content_items:
            for item in content_items:
                prefix = "      - "
                wrapped = tui._wrap_display(item, content_width - 10)
                for line in wrapped:
                    result.append(('class:border', '|'))
                    content_style = tui._merge_styles("class:text.dim", bg_style) if selected else "class:text.dim"
                    result.append((content_style, prefix + line.ljust(content_width - 10 - len(prefix))))
                    result.append(('class:border', '|\n'))
                    line_counter += 1

    # Blockers are data, not a checkpoint: render as a read-only section.
    blockers = list(getattr(target, "blockers", []) or [])
    if blockers:
        result.append(('class:border', '|'))
        result.append(('class:text.dim', f"  {tui._t('BLOCKERS')}:".ljust(content_width - 2)))
        result.append(('class:border', '|\n'))
        line_counter += 1
        for item in blockers:
            prefix = "      - "
            wrapped = tui._wrap_display(str(item), content_width - 10)
            for line in wrapped:
                result.append(('class:border', '|'))
                result.append(('class:text.dim', prefix + line.ljust(content_width - 10 - len(prefix))))
                result.append(('class:border', '|\n'))
                line_counter += 1

    result.append((header_style, header_line + '\n'))

    # Instructions Footer
    instructions = [
        "SPACE/ENTER: Toggle  UP/DOWN: Navigate  ESC: Back"
    ]
    for instr in instructions:
        result.append(('class:border', '| '))
        result.append(('class:text.dim', instr.center(content_width - 4)))
        result.append(('class:border', ' |\n'))

    result.append((header_style, header_line))

    return FormattedText(result)
