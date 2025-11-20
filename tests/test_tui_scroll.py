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
    total_items = len(detail.subtasks) + len(detail.next_steps) + len(detail.dependencies)
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
