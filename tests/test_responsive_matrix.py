import itertools

import pytest

from tasks import SubTask, TaskDetail, TaskTrackerTUI


@pytest.mark.parametrize(
    "term_width,expected_max",
    [
        (70, 66),   # tw-4
        (100, 94),  # tw-6
        (200, 160), # capped at 160
    ],
)
def test_detail_content_width_breakpoints(term_width, expected_max, tmp_path):
    tui = TaskTrackerTUI(tasks_dir=tmp_path / ".tasks")
    tui.get_terminal_width = lambda: term_width
    width = tui._detail_content_width()

    assert width <= expected_max
    assert width <= term_width - 2
    assert width >= 30


@pytest.mark.parametrize("term_width,term_height", [(58, 10), (82, 14), (120, 18)])
def test_detail_view_resizes_for_various_widths(tmp_path, term_width, term_height):
    tui = TaskTrackerTUI(tasks_dir=tmp_path / ".tasks")
    tui.get_terminal_width = lambda: term_width
    tui.get_terminal_height = lambda: term_height
    tui._set_footer_height(2)

    detail = TaskDetail(
        id="TASK-MATRIX",
        title="Matrix sizing check",
        status="WARN",
        description="\n".join(f"Line {i} content text" for i in range(8)),
        blockers=[f"blocker {i}" for i in range(3)],
        domain="devtools",
    )
    detail.subtasks = []

    tui.detail_mode = True
    tui.current_task_detail = detail
    rendered = "".join(text for _, text in tui.get_detail_text())
    lines = rendered.split("\n")

    assert len(lines) <= tui.get_terminal_height()
    assert all(tui._display_width(line) <= tui.get_terminal_width() for line in lines if line)


