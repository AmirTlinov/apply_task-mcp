import pytest

from tasks import Status, Step, Task, TaskDetail, TaskTrackerTUI
from core import PlanNode, TaskNode


def build_tui(tmp_path, *, project_mode: bool = False):
    """Helper to construct TUI with an isolated tasks directory."""
    tasks_dir = tmp_path / ".tasks"
    tui = TaskTrackerTUI(tasks_dir=tasks_dir)
    tui.project_mode = project_mode
    tui.detail_tab = "overview"
    return tui


def open_subtask_detail(tui: TaskTrackerTUI, subtask: Step, *, task_id: str = "TASK-TEST"):
    detail = TaskDetail(id=task_id, title="Detail", status="TODO")
    detail.steps = [subtask]
    tui.detail_mode = True
    tui.current_task_detail = detail
    tui._rebuild_detail_flat()
    tui.detail_selected_path = "s:0"
    tui.show_subtask_details("s:0")
    return detail


def test_move_selection_clamps_task_list(tmp_path):
    tui = build_tui(tmp_path)
    tui.tasks = [
        Task(name="Item A", status=Status.TODO, description="", category="tests"),
        Task(name="Item B", status=Status.DONE, description="", category="tests"),
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
    detail = TaskDetail(id="TASK-999", title="Detail", status="TODO")
    detail.steps = [
        Step(False, "Step example long enough to be valid"),
        Step(False, "Second step example with details"),
    ]
    detail.next_steps = ["Ship vertical mouse scroll"]
    detail.dependencies = ["TASK-001"]

    tui.detail_mode = True
    tui.current_task_detail = detail
    tui._rebuild_detail_flat()
    tui.detail_selected_index = 0

    # Jump beyond total items and ensure we clamp to the last slot
    tui.move_vertical_selection(10)
    total_items = len(detail.steps)
    assert tui.detail_selected_index == total_items - 1

    tui.move_vertical_selection(-20)
    assert tui.detail_selected_index == 0


def test_subtasks_view_stays_within_height(tmp_path):
    tui = build_tui(tmp_path)
    tui.get_terminal_height = lambda: 15
    detail = TaskDetail(
        id="TASK-TEST",
        title="Detail",
        status="ACTIVE",
        description="\n".join(f"Line {i}" for i in range(12)),
    )
    detail.steps = [Step(False, f"Step {i} long text goes here {i}") for i in range(8)]

    tui.detail_mode = True
    tui.current_task_detail = detail
    tui.detail_selected_index = 6
    tui._set_footer_height(0)

    rendered = "".join(text for _, text in tui.get_detail_text())
    lines = rendered.split("\n")

    assert len(lines) <= tui.get_terminal_height()
    expected_title = f"Step {tui.detail_selected_index} long text goes here {tui.detail_selected_index}"
    assert expected_title in rendered
    assert "↑" in rendered and "↓" in rendered


def test_detail_drilldown_cycle_step_plan_task(tmp_path):
    tui = build_tui(tmp_path)
    detail = TaskDetail(id="TASK-NEST", title="Detail", status="ACTIVE")
    child = Step(False, "Child item", success_criteria=["c"], tests=["t"], blockers=["b"])
    parent = Step(False, "Parent item", success_criteria=["p"], tests=["t"], blockers=["b"])
    parent.plan = PlanNode(tasks=[TaskNode(title="Nested task", steps=[child])])
    detail.steps = [parent]
    tui.detail_mode = True
    tui.current_task_detail = detail
    tui.detail_selected_index = 0
    tui._rebuild_detail_flat()

    rendered = "".join(text for _, text in tui.get_detail_text())

    # Root task detail shows only one level (steps list) — no inline plan/task/step mixing.
    assert f"{tui._t('SUBTASKS')} (0/1" in rendered
    assert "1.P " not in rendered
    assert "1.T1 " not in rendered
    assert "1.T1.1 " not in rendered

    # Enter Step → Plan (Tasks list)
    tui.show_subtask_details("s:0")
    assert getattr(tui.current_task_detail, "kind", "") == "plan"
    tui.detail_tab = "overview"
    rendered_plan = "".join(text for _, text in tui.get_detail_text())
    assert "Nested task" in rendered_plan
    assert "Child item" not in rendered_plan

    # Enter Task → Steps list
    tui._open_selected_plan_task_detail()
    assert getattr(tui.current_task_detail, "kind", "") == "task"
    tui.detail_tab = "overview"
    rendered_task = "".join(text for _, text in tui.get_detail_text())
    assert "Child item" in rendered_task


def test_drilldown_back_restores_previous_view(tmp_path):
    tui = build_tui(tmp_path)
    detail = TaskDetail(id="TASK-NEST", title="Detail", status="ACTIVE")
    child = Step(False, "Child item", success_criteria=["c"], tests=["t"], blockers=["b"])
    parent = Step(False, "Parent item", success_criteria=["p"], tests=["t"], blockers=["b"])
    parent.plan = PlanNode(tasks=[TaskNode(title="Nested task", steps=[child])])
    detail.steps = [parent]
    tui.detail_mode = True
    tui.current_task_detail = detail
    tui.detail_selected_index = 0
    tui._rebuild_detail_flat()

    tui.show_subtask_details("s:0")  # step → plan
    assert getattr(tui.current_task_detail, "kind", "") == "plan"
    tui._open_selected_plan_task_detail()  # plan task → task
    assert getattr(tui.current_task_detail, "kind", "") == "task"

    tui.exit_detail_view()
    assert getattr(tui.current_task_detail, "kind", "") == "plan"
    tui.exit_detail_view()
    assert getattr(tui.current_task_detail, "kind", "") == "task"
    assert getattr(tui.current_task_detail, "title", "") == "Detail"


def test_collapse_state_persists_per_task(tmp_path):
    tui = build_tui(tmp_path)
    child = Step(False, "Child", success_criteria=["c"], tests=["t"], blockers=["b"])
    parent = Step(False, "Parent", success_criteria=["p"], tests=["t"], blockers=["b"])
    parent.plan = PlanNode(tasks=[TaskNode(title="Nested task", steps=[child])])
    detail = TaskDetail(id="TASK-PERSIST", title="Demo", status="ACTIVE")
    detail.steps = [parent]
    task = Task(name="Demo", status=Status.TODO, description="", category="", task_file="")
    task.detail = detail

    tui.show_task_details(task)
    tui.detail_tab = "overview"
    tui._toggle_collapse_selected(expand=False)
    collapsed = "".join(text for _, text in tui.get_detail_text())
    assert "1.T1.1" not in collapsed

    tui.show_task_details(task)  # reopen should keep collapsed
    tui.detail_tab = "overview"
    reopened = "".join(text for _, text in tui.get_detail_text())
    assert "1.T1.1" not in reopened


def test_selected_subtask_details_rendered(tmp_path):
    tui = build_tui(tmp_path)
    tui.get_terminal_height = lambda: 40
    detail = TaskDetail(id="TASK-DETAIL", title="Detail", status="ACTIVE")
    detail.steps = [
        Step(
            False,
            "Sub with data",
            success_criteria=["crit A"],
            tests=["test A"],
            blockers=["block A"],
        ),
        Step(False, "Other"),
    ]
    tui.detail_mode = True
    tui.current_task_detail = detail
    tui._rebuild_detail_flat()
    tui.detail_selected_index = 0
    rendered = "".join(text for _, text in tui.get_footer_text())

    assert "crit A" in rendered
    assert "test A" in rendered
    assert "block A" in rendered


def test_compact_summary_shows_blockers_when_room(tmp_path):
    tui = build_tui(tmp_path)
    tui.get_terminal_height = lambda: 30
    detail = TaskDetail(
        id="TASK-SUMMARY",
        title="Detail",
        status="ACTIVE",
        description="Line one; Line two;",
    )
    detail.blockers = ["dep a", "dep b"]
    detail.steps = [Step(False, "Step 1 long body text") for _ in range(2)]
    tui.detail_mode = True
    tui.current_task_detail = detail
    rendered = "".join(text for _, text in tui.get_footer_text())

    assert "Description:" in rendered
    assert "dep a" in rendered


def test_selection_stops_at_last_item(tmp_path):
    tui = build_tui(tmp_path)
    tui.get_terminal_height = lambda: 12
    detail = TaskDetail(id="TASK-STOP", title="Detail", status="ACTIVE")
    detail.steps = [Step(False, f"Step {i} long body text") for i in range(6)]

    tui.detail_mode = True
    tui.current_task_detail = detail
    tui._rebuild_detail_flat()
    tui.detail_selected_index = len(detail.steps) - 1  # уже на последнем
    tui.detail_selected_path = f"s:{tui.detail_selected_index}"
    tui._set_footer_height(0)

    # Дополнительные скроллы вниз не должны убирать подсветку
    for _ in range(3):
        tui.move_vertical_selection(1)
        rendered = "".join(text for _, text in tui.get_detail_text())
        expected_title = f"Step {tui.detail_selected_index} long body text"
        assert expected_title in rendered

    assert tui.detail_selected_index == len(detail.steps) - 1
    # последний элемент остаётся на экране
    expected_title = f"Step {tui.detail_selected_index} long body text"
    assert expected_title in rendered


def test_last_subtask_visible_with_long_header(tmp_path):
    tui = build_tui(tmp_path)
    tui.get_terminal_height = lambda: 14
    detail = TaskDetail(
        id="TASK-LONG",
        title="Detail",
        status="ACTIVE",
        description="\n".join(f"Line {i}" for i in range(8)),  # съедает место
    )
    detail.steps = [Step(False, f"Step {i} body text") for i in range(13)]

    tui.detail_mode = True
    tui.current_task_detail = detail
    tui._rebuild_detail_flat()
    tui.detail_selected_index = len(detail.steps) - 1  # последний
    tui.detail_selected_path = f"s:{tui.detail_selected_index}"
    tui._set_footer_height(0)

    rendered = "".join(text for _, text in tui.get_detail_text())
    expected_title = f"Step {tui.detail_selected_index} body text"
    assert expected_title in rendered  # последний виден
    # нижний маркер может отсутствовать, но последний элемент должен быть в окне


def test_maybe_reload_works_in_detail_mode(tmp_path, monkeypatch):
    tui = build_tui(tmp_path)
    detail = TaskDetail(id="TASK-1", title="Detail", status="DONE")
    detail.steps = [Step(False, "A"), Step(False, "B")]
    tui.detail_mode = True
    tui.current_task_detail = detail
    tui.tasks = [Task(id="TASK-1", name="Detail", status=Status.DONE, description="", category="", detail=detail)]
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
    assert tui.status_message.startswith("↻")

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
