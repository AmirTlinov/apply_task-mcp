from tasks import ResponsiveLayoutManager, Status, Task, TaskTrackerTUI


def _render_lines(text):
    return "".join(part for _, part in text).split("\n")


def test_task_list_respects_width_on_small_terminal(tmp_path):
    tui = TaskTrackerTUI(tasks_dir=tmp_path / ".tasks")
    tui.get_terminal_width = lambda: 48
    tui.get_terminal_height = lambda: 12
    tui.tasks = [
        Task(
            name="Extremely long task title that should be trimmed but not break borders",
            status=Status.OK,
            description="",
            category="",
            progress=87,
            subtasks_count=5,
            subtasks_completed=2,
        ),
        Task(
            name="Secondary long title to verify wrapping",
            status=Status.WARN,
            description="",
            category="",
            progress=3,
            subtasks_count=0,
            subtasks_completed=0,
        ),
    ]

    rendered_lines = _render_lines(tui.get_task_list_text())
    max_width = max(tui._display_width(line) for line in rendered_lines if line)

    assert max_width <= tui.get_terminal_width()
    layout = ResponsiveLayoutManager.select_layout(tui.get_terminal_width())
    assert layout.columns == ['idx', 'stat', 'title']


def test_task_list_handles_wide_numbers_without_overflow(tmp_path):
    tui = TaskTrackerTUI(tasks_dir=tmp_path / ".tasks")
    tui.get_terminal_width = lambda: 72
    tui.get_terminal_height = lambda: 14
    tui.tasks = [
        Task(
            name="Alpha release milestone",
            status=Status.OK,
            description="",
            category="",
            progress=123,
            subtasks_count=123,
            subtasks_completed=7,
        ),
        Task(
            name="Beta prep",
            status=Status.FAIL,
            description="",
            category="",
            progress=99,
            subtasks_count=45,
            subtasks_completed=12,
        ),
    ]

    rendered_lines = _render_lines(tui.get_task_list_text())
    for line in rendered_lines:
        if not line:
            continue
        assert tui._display_width(line) <= tui.get_terminal_width()

    text_blob = "\n".join(rendered_lines)
    assert "123%" in text_blob
    assert "7/123" in text_blob


def test_task_list_clamps_ultra_narrow_width(tmp_path):
    tui = TaskTrackerTUI(tasks_dir=tmp_path / ".tasks")
    tui.get_terminal_width = lambda: 8
    tui.get_terminal_height = lambda: 6
    tui.tasks = [
        Task(
            name="Tiny",
            status=Status.OK,
            description="",
            category="",
            progress=12,
            subtasks_count=1,
            subtasks_completed=0,
        )
    ]

    rendered_lines = _render_lines(tui.get_task_list_text())
    max_width = max(tui._display_width(line) for line in rendered_lines if line)

    assert max_width <= tui.get_terminal_width()
    assert ResponsiveLayoutManager.select_layout(tui.get_terminal_width()).columns == ['idx', 'stat', 'title']


def test_task_list_shows_indices(tmp_path):
    tui = TaskTrackerTUI(tasks_dir=tmp_path / ".tasks")
    tui.get_terminal_width = lambda: 90
    tui.get_terminal_height = lambda: 12
    tui.tasks = [
        Task(name="Alpha", status=Status.OK, description="", category="", progress=10, subtasks_count=0, subtasks_completed=0),
        Task(name="Beta", status=Status.WARN, description="", category="", progress=20, subtasks_count=0, subtasks_completed=0),
    ]

    text = "".join(part for _, part in tui.get_task_list_text())

    assert " 0 " in text
    assert " 1 " in text
