from tasks import Status, Step, TaskDetail, TaskTrackerTUI


def test_detail_view_clamped_to_terminal(tmp_path):
    tui = TaskTrackerTUI(tasks_dir=tmp_path / ".tasks")
    tui.get_terminal_width = lambda: 62
    tui.get_terminal_height = lambda: 14
    tui._set_footer_height(2)

    detail = TaskDetail(
        id="TASK-DET",
        title="Very long detail title that would otherwise stretch the frame",
        status="ACTIVE",
        description="\n".join(f"Line {i} with more text to consume space" for i in range(12)),
        blockers=[f"blocker {i}" for i in range(5)],
        domain="devtools",
    )
    detail.steps = [Step(False, f"Step {i}") for i in range(6)]

    tui.detail_mode = True
    tui.current_task_detail = detail
    tui.detail_tab = "overview"
    text = tui.get_detail_text()
    rendered = "".join(fragment for _, fragment in text)
    lines = rendered.split("\n")

    assert len(lines) <= tui.get_terminal_height()
    assert all(tui._display_width(line) <= tui.get_terminal_width() for line in lines if line)
