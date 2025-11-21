import pytest

from tasks import Status, SubTask, Task, TaskDetail, TaskTrackerTUI


def build_tui(tmp_path):
    """Helper to construct TUI with an isolated tasks directory."""
    tasks_dir = tmp_path / ".tasks"
    return TaskTrackerTUI(tasks_dir=tasks_dir)


def open_subtask_detail(tui: TaskTrackerTUI, subtask: SubTask, *, task_id: str = "TASK-TEST"):
    detail = TaskDetail(id=task_id, title="Detail", status="FAIL")
    detail.subtasks = [subtask]
    tui.detail_mode = True
    tui.current_task_detail = detail
    tui._rebuild_detail_flat()
    tui.detail_selected_path = "0"
    tui.show_subtask_details("0")
    return detail


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
    tui._rebuild_detail_flat()
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
    assert f">  {tui.detail_selected_index} " in rendered
    assert "↑" in rendered and "↓" in rendered


def test_detail_renders_nested_subtasks_with_paths(tmp_path):
    tui = build_tui(tmp_path)
    detail = TaskDetail(id="TASK-NEST", title="Detail", status="WARN")
    child = SubTask(False, "Child item", success_criteria=["c"], tests=["t"], blockers=["b"])
    parent = SubTask(False, "Parent item", success_criteria=["p"], tests=["t"], blockers=["b"], children=[child])
    detail.subtasks = [parent]
    tui.detail_mode = True
    tui.current_task_detail = detail
    tui.detail_selected_index = 0
    tui._rebuild_detail_flat()

    rendered = "".join(text for _, text in tui.get_detail_text())

    assert "ПОДЗАДАЧИ (0/2 завершено)" in rendered
    assert "| >▾ 0 " in rendered  # родитель с индикатором раскрытия
    assert "|      0.0 " in rendered  # вложенная подзадача с отступом


def test_collapse_expand_toggles_visibility(tmp_path):
    tui = build_tui(tmp_path)
    detail = TaskDetail(id="TASK-NEST", title="Detail", status="WARN")
    child = SubTask(False, "Child item", success_criteria=["c"], tests=["t"], blockers=["b"])
    parent = SubTask(False, "Parent item", success_criteria=["p"], tests=["t"], blockers=["b"], children=[child])
    detail.subtasks = [parent]
    tui.detail_mode = True
    tui.current_task_detail = detail
    tui._rebuild_detail_flat()

    rendered = "".join(text for _, text in tui.get_detail_text())
    assert "0.0" in rendered  # expanded by default

    tui._toggle_collapse_selected(expand=False)
    collapsed = "".join(text for _, text in tui.get_detail_text())
    assert "0.0" not in collapsed
    assert ">▸ 0 " in collapsed  # свернутый индикатор

    tui._toggle_collapse_selected(expand=True)
    expanded = "".join(text for _, text in tui.get_detail_text())
    assert "0.0" in expanded


def test_selection_stops_at_last_item(tmp_path):
    tui = build_tui(tmp_path)
    tui.get_terminal_height = lambda: 12
    detail = TaskDetail(id="TASK-STOP", title="Detail", status="WARN")
    detail.subtasks = [SubTask(False, f"Subtask {i} long body text") for i in range(6)]

    tui.detail_mode = True
    tui.current_task_detail = detail
    tui._rebuild_detail_flat()
    tui.detail_selected_index = len(detail.subtasks) - 1  # уже на последнем
    tui._set_footer_height(0)

    # Дополнительные скроллы вниз не должны убирать подсветку
    for _ in range(3):
        tui.move_vertical_selection(1)
        rendered = "".join(text for _, text in tui.get_detail_text())
        assert f">  {tui.detail_selected_index} " in rendered

    assert tui.detail_selected_index == len(detail.subtasks) - 1
    # подсветка последнего элемента остаётся на экране
    assert f">  {tui.detail_selected_index} " in rendered


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
    tui._rebuild_detail_flat()
    tui.detail_selected_index = len(detail.subtasks) - 1  # последний
    tui._set_footer_height(0)

    rendered = "".join(text for _, text in tui.get_detail_text())
    assert f">  {tui.detail_selected_index} " in rendered  # последний виден
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
    open_subtask_detail(tui, st)
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
    open_subtask_detail(tui, st)
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
    open_subtask_detail(tui, st)
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
    open_subtask_detail(tui, st)
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
    open_subtask_detail(tui, st)
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
    open_subtask_detail(tui, st)
    styles = [style or "" for style, _ in tui.single_subtask_view]
    # выбрана первая линия; бордеры должны остаться без selected
    assert all("selected" not in s for s in styles if "border" in s)


def test_maybe_reload_works_in_detail_mode(tmp_path, monkeypatch):
    tui = build_tui(tmp_path)
    detail = TaskDetail(id="TASK-1", title="Detail", status="OK")
    detail.subtasks = [SubTask(False, "A"), SubTask(False, "B")]
    tui.detail_mode = True
    tui.current_task_detail = detail
    tui.tasks = [Task(id="TASK-1", name="Detail", status=Status.OK, description="", category="", detail=detail)]
    tui.selected_index = 0
    called = {}

    def fake_compute():
        return 2

    def fake_load(*args, **kwargs):
        called["load"] = True
        # simulate updated task detail
        tui.tasks[0].detail = detail
        return None

    tui._last_signature = 1
    monkeypatch.setattr(tui, "compute_signature", fake_compute)
    monkeypatch.setattr(tui, "load_tasks", fake_load)
    tui.maybe_reload()
    assert called.get("load")
    assert "↻ CLI" in tui.status_message

def test_single_subtask_view_highlight(tmp_path):
    tui = build_tui(tmp_path)
    tui.get_terminal_height = lambda: 12
    st = SubTask(False, "Subtask", success_criteria=[f"Criterion {i}" for i in range(3)])
    open_subtask_detail(tui, st)
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
    open_subtask_detail(tui, st)
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
    open_subtask_detail(tui, st)
    tui.subtask_detail_scroll = 5
    tui._render_single_subtask_view(max(40, tui.get_terminal_width() - 2))
    rendered_lines = "".join(text for _, text in tui.single_subtask_view).split("\n")

    visible_nonempty = [ln for ln in rendered_lines if ln.strip()]
    # Первые строки вида должны содержать шапку SUBTASK 0, даже после скролла
    assert any("SUBTASK 0" in ln for ln in visible_nonempty[:3])
