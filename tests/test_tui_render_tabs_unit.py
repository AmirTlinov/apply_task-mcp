"""Unit tests for detail rendering (tabs + overview variants)."""

from core import TaskDetail
from core.desktop.devtools.interface.tui_app import TaskTrackerTUI
from core.desktop.devtools.interface.tui_render import (
    _checkpoint_marks_fragments,
    render_checkpoint_view_impl,
    render_detail_text_impl,
)
from core.step import Step


def _plain_text(formatted) -> str:
    return "".join(fragment[1] for fragment in formatted if isinstance(fragment, tuple) and len(fragment) >= 2)


def test_render_plan_tab_shows_doc_only(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    tui = TaskTrackerTUI(tasks_dir=tasks_dir)

    detail = TaskDetail(id="TASK-001", title="Test", status="ACTIVE", domain="dom/a", created="", updated="")
    detail.plan_doc = "My plan narrative"
    detail.plan_steps = ["First step", "Second step"]
    detail.plan_current = 1

    tui.current_task_detail = detail
    tui.detail_mode = True
    tui.detail_tab = "plan"

    text = _plain_text(render_detail_text_impl(tui))
    assert "My plan narrative" in text
    assert "First step" not in text
    assert "Second step" not in text


def test_render_contract_tab_shows_contract_text(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    tui = TaskTrackerTUI(tasks_dir=tasks_dir)

    detail = TaskDetail(id="TASK-001", title="Test", status="ACTIVE", domain="dom/a", created="", updated="")
    detail.contract = "User request contract text"

    tui.current_task_detail = detail
    tui.detail_mode = True
    tui.detail_tab = "contract"

    text = _plain_text(render_detail_text_impl(tui))
    assert "User request contract text" in text


def test_render_meta_tab_shows_sections_and_items(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    tui = TaskTrackerTUI(tasks_dir=tasks_dir)

    detail = TaskDetail(id="TASK-001", title="Test", status="ACTIVE", domain="dom/a", created="", updated="")
    detail.risks = ["perf regression"]
    detail.next_steps = ["ship it"]
    detail.depends_on = ["TASK-123"]

    tui.current_task_detail = detail
    tui.detail_mode = True
    tui.detail_tab = "meta"

    text = _plain_text(render_detail_text_impl(tui))
    assert "Risks" in text
    assert "perf regression" in text
    assert "Next steps" in text
    assert "ship it" in text
    assert "Depends on" in text
    assert "TASK-123" in text


def test_render_overview_tab_shows_tasks_for_plan(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    tui = TaskTrackerTUI(tasks_dir=tasks_dir)
    tui.language = "en"

    class Manager:
        def list_tasks(self, *args, **kwargs):
            return [
                TaskDetail(id="TASK-001", title="Child 1", status="TODO", kind="task", parent="PLAN-001", domain="dom/a", created="", updated=""),
                TaskDetail(id="TASK-002", title="Child 2", status="ACTIVE", kind="task", parent="PLAN-001", domain="dom/a", created="", updated=""),
                TaskDetail(id="TASK-999", title="Other", status="TODO", kind="task", parent="PLAN-999", domain="dom/a", created="", updated=""),
            ]

    tui.manager = Manager()

    plan = TaskDetail(id="PLAN-001", title="Plan", status="TODO", kind="plan", domain="dom/a", created="", updated="")
    tui.current_task_detail = plan
    tui.detail_mode = True
    tui.detail_tab = "overview"

    text = _plain_text(render_detail_text_impl(tui))
    assert "[Tasks]" in text
    assert "Child 1" in text and "Child 2" in text
    assert "002" in text
    assert "TASK-999" not in text


def test_render_checkpoint_view_renders_blockers_without_checkbox(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    tui = TaskTrackerTUI(tasks_dir=tasks_dir)
    tui.language = "en"

    step = Step(False, "Step", success_criteria=["c1"], tests=["t1"], blockers=["blocked by api"])
    task = TaskDetail(id="TASK-001", title="T", status="ACTIVE", kind="task", domain="dom/a", created="", updated="", steps=[step])

    tui.current_task_detail = task
    tui.detail_selected_path = "s:0"

    text = _plain_text(render_checkpoint_view_impl(tui))
    assert text.count("[ ]") == 2
    assert "blocked by api" in text


def test_render_overview_shows_breadcrumbs_line(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    tui = TaskTrackerTUI(tasks_dir=tasks_dir)
    tui.language = "en"

    step = Step(False, "Step", success_criteria=["c1"])
    task = TaskDetail(id="TASK-001", title="T", status="TODO", kind="task", domain="dom/a", created="", updated="", steps=[step])
    tui.current_task_detail = task
    tui.detail_mode = True
    tui.detail_tab = "overview"
    tui._rebuild_detail_flat()

    text = _plain_text(render_detail_text_impl(tui))
    assert "Path:" in text


def test_detail_tree_renders_marks_column_separately(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    tui = TaskTrackerTUI(tasks_dir=tasks_dir)
    tui.language = "en"

    step = Step(False, "Step", success_criteria=["c1"])
    task = TaskDetail(id="TASK-001", title="T", status="TODO", kind="task", domain="dom/a", created="", updated="", steps=[step])
    tui.current_task_detail = task
    tui.detail_mode = True
    tui.detail_tab = "overview"
    tui._rebuild_detail_flat()

    text = _plain_text(render_detail_text_impl(tui))
    assert "✓✓" in text
    assert "Step [" not in text


def test_checkpoint_marks_fragments_use_color_only():
    step = Step(False, "Step", success_criteria=["c1"])
    step.criteria_confirmed = True
    step.tests_confirmed = False
    fragments = _checkpoint_marks_fragments(step, 5)
    # Token layout: [, crit, space, test, ]
    assert fragments[1][1] == "•"
    assert fragments[3][1] == "•"
    assert "class:icon.check" in fragments[1][0]
    assert "class:text.dim" in fragments[3][0]


def test_render_radar_tab_shows_runway_evidence_and_next(tmp_path, monkeypatch):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    tui = TaskTrackerTUI(tasks_dir=tasks_dir)
    tui.language = "en"

    detail = TaskDetail(id="TASK-001", title="Test", status="ACTIVE", domain="dom/a", created="", updated="")
    tui.current_task_detail = detail
    tui.detail_mode = True
    tui.detail_tab = "radar"

    payload = {
        "runway": {
            "open": False,
            "blocking": {"lint": {"summary": {}, "errors_count": 1, "top_errors": [{"code": "X", "message": "broken"}]}, "validation": None},
            "recipe": {"intent": "patch"},
        },
        "verify": {"evidence_task": {"steps_total": 2, "steps_with_any_evidence": 1, "checks": {"count": 3, "last_observed_at": ""}, "attachments": {"count": 1, "last_observed_at": ""}}},
        "next": [{"action": "patch", "reason": "Fix it", "validated": True, "params": {"task": "TASK-001", "ops": [{"op": "set", "field": "description", "value": "x"}]}}],
    }
    monkeypatch.setattr(tui, "_radar_snapshot", lambda force=False: (payload, ""))

    text = _plain_text(render_detail_text_impl(tui))
    assert "Radar" in text
    assert "Runway closed" in text
    assert "evidence:" in text
    assert '"intent": "patch"' in text
    assert "Enter — run next" in text
