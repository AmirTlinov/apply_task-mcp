import pytest

from tasks import Status, SubTask, Task, TaskDetail, TaskTrackerTUI


def build_tui(tmp_path):
    """Helper to construct TUI with an isolated tasks directory."""
    tasks_dir = tmp_path / ".tasks"
    return TaskTrackerTUI(tasks_dir=tasks_dir)


def test_move_selection_clamps_task_list(tmp_path):
    tui = build_tui(tmp_path)
    tui.tasks = [
        Task(name="Item A", status=Status.FAIL, description="", category="tests"),
        Task(name="Item B", status=Status.OK, description="", category="tests"),
    ]
    tui.selected_index = 0

    tui.move_vertical_selection(-1)
    assert tui.selected_index == 0

    tui.move_vertical_selection(1)
    assert tui.selected_index == 1

    tui.move_vertical_selection(5)
    assert tui.selected_index == 1  # Clamp to last task when moving beyond bounds


def test_move_selection_clamps_detail_mode(tmp_path):
    tui = build_tui(tmp_path)
    detail = TaskDetail(id="TASK-999", title="Detail", status="FAIL")
    detail.subtasks = [
        SubTask(False, "Subtask example long enough to be valid"),
        SubTask(False, "Second subtask example with details"),
    ]
    detail.next_steps = ["Ship vertical mouse scroll"]
    detail.dependencies = ["TASK-001"]

    tui.detail_mode = True
    tui.current_task_detail = detail
    tui.detail_selected_index = 0

    # Jump beyond total items and ensure we clamp to the last slot
    tui.move_vertical_selection(10)
    total_items = len(detail.subtasks)
    assert tui.detail_selected_index == total_items - 1

    tui.move_vertical_selection(-20)
    assert tui.detail_selected_index == 0


def test_subtasks_view_stays_within_height(tmp_path):
    tui = build_tui(tmp_path)
    tui.get_terminal_height = lambda: 15
    detail = TaskDetail(
        id="TASK-TEST",
        title="Detail",
        status="WARN",
        description="\n".join(f"Line {i}" for i in range(12)),
    )
    detail.subtasks = [SubTask(False, f"Subtask {i} long text goes here {i}") for i in range(8)]

    tui.detail_mode = True
    tui.current_task_detail = detail
    tui.detail_selected_index = 6
    tui._set_footer_height(0)

    rendered = "".join(text for _, text in tui.get_detail_text())
    lines = rendered.split("\n")

    assert len(lines) <= tui.get_terminal_height()
    assert f"> {tui.detail_selected_index + 1}. " in rendered
    assert "↑" in rendered and "↓" in rendered


def test_selection_stops_at_last_item(tmp_path):
    tui = build_tui(tmp_path)
    tui.get_terminal_height = lambda: 12
    detail = TaskDetail(id="TASK-STOP", title="Detail", status="WARN")
    detail.subtasks = [SubTask(False, f"Subtask {i} long body text") for i in range(6)]

    tui.detail_mode = True
    tui.current_task_detail = detail
    tui.detail_selected_index = len(detail.subtasks) - 1  # уже на последнем
    tui._set_footer_height(0)

    # Дополнительные скроллы вниз не должны убирать подсветку
    for _ in range(3):
        tui.move_vertical_selection(1)
        rendered = "".join(text for _, text in tui.get_detail_text())
        assert f"> {len(detail.subtasks)}. " in rendered

    assert tui.detail_selected_index == len(detail.subtasks) - 1
    # подсветка последнего элемента остаётся на экране
    assert f"> {len(detail.subtasks)}. " in rendered


def test_last_subtask_visible_with_long_header(tmp_path):
    tui = build_tui(tmp_path)
    tui.get_terminal_height = lambda: 14
    detail = TaskDetail(
        id="TASK-LONG",
        title="Detail",
        status="WARN",
        description="\n".join(f"Line {i}" for i in range(8)),  # съедает место
    )
    detail.subtasks = [SubTask(False, f"Subtask {i} body text") for i in range(13)]

    tui.detail_mode = True
    tui.current_task_detail = detail
    tui.detail_selected_index = len(detail.subtasks) - 1  # последний
    tui._set_footer_height(0)

    rendered = "".join(text for _, text in tui.get_detail_text())
    assert f"> {len(detail.subtasks)}. " in rendered  # последний виден
    # нижний маркер может отсутствовать, но последний элемент должен быть в окне


def test_single_subtask_view_scrolls_content(tmp_path):
    tui = build_tui(tmp_path)
    tui.get_terminal_height = lambda: 10
    st = SubTask(
        False,
        "Subtask with long content",
        success_criteria=[f"Criterion {i}" for i in range(8)],
        tests=[f"Test {i}" for i in range(4)],
        blockers=[f"Blocker {i}" for i in range(3)],
        criteria_notes=[f"Note {i}" for i in range(2)],
    )
    tui.show_subtask_details(st, 0)
    # Прокручиваем сразу к низу
    tui.subtask_detail_scroll = 50
    tui._render_single_subtask_view(max(40, tui.get_terminal_width() - 2))
    rendered = "".join(text for _, text in tui.single_subtask_view)

    assert "Blocker 2" in rendered  # нижняя часть стала видимой после скролла


def test_single_subtask_cursor_stays_visible_with_footer(tmp_path):
    tui = build_tui(tmp_path)
    tui.get_terminal_height = lambda: 12
    st = SubTask(
        False,
        "Subtask",
        success_criteria=[f"Criterion {i}" for i in range(15)],
    )
    tui.show_subtask_details(st, 0)
    tui._set_footer_height(3)  # имитируем высокий футер
    tui.move_vertical_selection(50)  # уйдём в конец
    styles = [style for style, _ in tui.single_subtask_view]
    assert any("selected" in (s or "") for s in styles)  # выделение осталось в видимой области


def test_single_subtask_long_lines_do_not_wrap(tmp_path):
    tui = build_tui(tmp_path)
    tui.get_terminal_height = lambda: 14
    tui.get_terminal_width = lambda: 60
    st = SubTask(
        False,
        "Очень длинный заголовок ✅ с разноширинными символами чтобы проверить паддинг и обрезку",
        success_criteria=[
            "Проверить, что строки не вылезают за рамку даже при узком окне",
            "Дважды проверить ✅ emoji и длинные слова_without_breaks_here_to_force_trim",
        ],
    )
    tui.show_subtask_details(st, 0)
    tui.move_vertical_selection(10)
    lines = tui._formatted_lines(tui.single_subtask_view)
    max_width = max(tui._display_width("".join(t for _, t in line)) for line in lines)
    assert max_width <= tui.get_terminal_width()  # ни одна строка не уходит за ширину терминала
    # курсор виден
    assert any("selected" in (s or "") for s, _ in tui.single_subtask_view)


def test_wrapped_bullet_entire_group_highlighted(tmp_path):
    tui = build_tui(tmp_path)
    tui.get_terminal_height = lambda: 12
    tui.get_terminal_width = lambda: 50  # заставим перенос
    st = SubTask(
        False,
        "Subtask",
        success_criteria=["Очень длинная строка без переноса чтобы занять две строки подряд и проверить выделение"],
    )
    tui.show_subtask_details(st, 0)
    # перейти к следующей фокусируемой строке (вероятно вторая часть переноса)
    tui.move_vertical_selection(1)
    styles = [style for style, _ in tui.single_subtask_view]
    selected_lines = sum(1 for style in styles if style and "selected" in style)
    # обе части пункта должны подсвечиваться
    assert selected_lines >= 2


def test_wrapped_bullet_moves_in_single_step(tmp_path):
    tui = build_tui(tmp_path)
    tui.get_terminal_height = lambda: 14
    tui.get_terminal_width = lambda: 46  # узко, будет перенос
    st = SubTask(
        False,
        "Subtask",
        success_criteria=[
            "Очень длинная строка номер один без переноса в исходнике чтобы занять две строки",
            "Вторая строка короче",
        ],
    )
    tui.show_subtask_details(st, 0)
    # курсор на первом элементе
    start_cursor = tui.subtask_detail_cursor
    lines = tui._formatted_lines(tui._subtask_detail_buffer)
    start_group = TaskTrackerTUI._extract_group(lines[start_cursor])
    tui.move_vertical_selection(1)  # один шаг вниз
    lines_after = tui._formatted_lines(tui._subtask_detail_buffer)
    cursor_after = tui.subtask_detail_cursor
    group_after = TaskTrackerTUI._extract_group(lines_after[cursor_after])
    # Должен перейти на следующий пункт, а не на вторую визуальную строку того же
    assert start_group != group_after


def test_selection_not_paint_border(tmp_path):
    tui = build_tui(tmp_path)
    tui.get_terminal_height = lambda: 12
    st = SubTask(False, "Subtask", success_criteria=["a" * 10, "b" * 10])
    tui.show_subtask_details(st, 0)
    styles = [style or "" for style, _ in tui.single_subtask_view]
    # выбрана первая линия; бордеры должны остаться без selected
    assert all("selected" not in s for s in styles if "border" in s)

def test_single_subtask_view_highlight(tmp_path):
    tui = build_tui(tmp_path)
    tui.get_terminal_height = lambda: 12
    st = SubTask(False, "Subtask", success_criteria=[f"Criterion {i}" for i in range(3)])
    tui.show_subtask_details(st, 0)
    styles = [style for style, _ in tui.single_subtask_view]
    assert any("selected" in (style or "") for style in styles)

    # move cursor
    tui.move_vertical_selection(1)
    styles_after = [style for style, _ in tui.single_subtask_view]
    assert any("selected" in (style or "") for style in styles_after)


def test_single_subtask_view_skips_headers(tmp_path):
    tui = build_tui(tmp_path)
    tui.get_terminal_height = lambda: 20
    st = SubTask(
        False,
        "Subtask",
        success_criteria=["line a", "line b"],
        tests=["line c"],
        blockers=["line d"],
    )
    tui.show_subtask_details(st, 0)
    focusables = tui._focusable_line_indices(tui._formatted_lines(tui._subtask_detail_buffer))
    # Заголовки (например, строка со словом 'Критерии выполнения') не должны быть в фокусируемых
    header_lines = [i for i, line in enumerate(tui._formatted_lines(tui._subtask_detail_buffer)) if any('status.' in (s or '') or 'header' in (s or '') for s, _ in line)]
    assert all(h not in focusables for h in header_lines)


def test_border_lines_not_focusable(tmp_path):
    tui = build_tui(tmp_path)
    content_width = 12
    formatted = [
        ('class:border', '+' + '=' * content_width + '+\n'),
        ('class:border', '| ' + 'body'.ljust(content_width - 1) + '|\n'),
        ('class:border', '+' + '=' * content_width + '+'),
    ]
    lines = tui._formatted_lines(formatted)
    focusables = tui._focusable_line_indices(lines)
    # Верхняя и нижняя рамка не должны попадать в фокус
    assert 0 not in focusables
    assert 2 not in focusables
    assert 1 in focusables  # строка с текстом остаётся кликабельной


def test_single_subtask_header_sticks_on_scroll(tmp_path):
    tui = build_tui(tmp_path)
    tui.get_terminal_height = lambda: 8  # маленькое окно, чтобы принудить скролл
    st = SubTask(
        False,
        "Subtask title fits here",
        success_criteria=[f"Criterion {i}" for i in range(12)],
    )
    tui.show_subtask_details(st, 0)
    tui.subtask_detail_scroll = 5
    tui._render_single_subtask_view(max(40, tui.get_terminal_width() - 2))
    rendered_lines = "".join(text for _, text in tui.single_subtask_view).split("\n")

    visible_nonempty = [ln for ln in rendered_lines if ln.strip()]
    # Первые строки вида должны содержать шапку SUBTASK 1, даже после скролла
    assert any("SUBTASK 1" in ln for ln in visible_nonempty[:3])
