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

    assert "SUBTASKS (0/2" in rendered
    assert ">▾ 0 " in rendered  # parent with expand indicator
    assert "0.0 " in rendered  # nested subtask visible


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


    def test_collapse_state_persists_per_task(tmp_path):
        tui = build_tui(tmp_path, project_mode=False)
    child = SubTask(False, "Child", success_criteria=["c"], tests=["t"], blockers=["b"])
    parent = SubTask(False, "Parent", success_criteria=["p"], tests=["t"], blockers=["b"], children=[child])
    detail = TaskDetail(id="TASK-PERSIST", title="Demo", status="WARN")
    detail.subtasks = [parent]
    task = Task(name="Demo", status=Status.FAIL, description="", category="", task_file="")
    task.detail = detail

    tui.show_task_details(task)
    tui._toggle_collapse_selected(expand=False)
    collapsed = "".join(text for _, text in tui.get_detail_text())
    assert "0.0" not in collapsed

    tui.show_task_details(task)  # reopen should keep collapsed
    reopened = "".join(text for _, text in tui.get_detail_text())
    assert "0.0" not in reopened


def test_selected_subtask_details_rendered(tmp_path):
    tui = build_tui(tmp_path)
    tui.get_terminal_height = lambda: 40
    detail = TaskDetail(id="TASK-DETAIL", title="Detail", status="WARN")
    detail.subtasks = [
        SubTask(
            False,
            "Sub with data",
            success_criteria=["crit A"],
            tests=["test A"],
            blockers=["block A"],
        ),
        SubTask(False, "Other"),
    ]
    tui.detail_mode = True
    tui.current_task_detail = detail
    tui._rebuild_detail_flat()
    tui.detail_selected_index = 0
    rendered = "".join(text for _, text in tui.get_detail_text())

    assert "SUBTASK DETAILS" in rendered
    assert "crit A" in rendered
    assert "test A" in rendered
    assert "block A" in rendered


def test_compact_summary_shows_blockers_when_room(tmp_path):
    tui = build_tui(tmp_path)
    tui.get_terminal_height = lambda: 30
    detail = TaskDetail(
        id="TASK-SUMMARY",
        title="Detail",
        status="WARN",
        description="Line one; Line two;",
    )
    detail.blockers = ["dep a", "dep b"]
    detail.subtasks = [SubTask(False, "Subtask 1 long body text") for _ in range(2)]
    tui.detail_mode = True
    tui.current_task_detail = detail
    rendered = "".join(text for _, text in tui.get_detail_text())

    assert "Description:" in rendered
    assert "Blockers:" in rendered
    assert "dep a" in rendered


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

