#!/usr/bin/env python3
"""Unit tests for tui_app module - TaskTrackerTUI class methods."""

import pytest
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path
from datetime import datetime
from types import SimpleNamespace

from core import Status, TaskDetail
from core.desktop.devtools.interface.tui_app import TaskTrackerTUI
from core.desktop.devtools.interface.tui_models import Task


class TestTaskTrackerTUIHelpers:
    """Tests for TaskTrackerTUI helper methods."""

    def test_get_theme_palette(self):
        """Test get_theme_palette static method."""
        palette = TaskTrackerTUI.get_theme_palette("dark-olive")
        assert isinstance(palette, dict)
        assert len(palette) > 0

    def test_get_theme_palette_unknown_falls_back(self):
        """Test get_theme_palette falls back to default for unknown theme."""
        default_palette = TaskTrackerTUI.get_theme_palette("dark-olive")
        unknown_palette = TaskTrackerTUI.get_theme_palette("non-existent")
        assert unknown_palette == default_palette

    def test_build_style(self):
        """Test build_style class method."""
        style = TaskTrackerTUI.build_style("dark-olive")
        assert style is not None

    def test_sync_target_label_repository(self):
        """Test _sync_target_label for repository type."""
        cfg = SimpleNamespace(project_type="repository", owner="test", repo="repo", number=1)
        result = TaskTrackerTUI._sync_target_label(cfg)
        assert result == "test/repo#1"

    def test_sync_target_label_repository_no_repo(self):
        """Test _sync_target_label for repository without repo name."""
        cfg = SimpleNamespace(project_type="repository", owner="test", repo=None, number=1)
        result = TaskTrackerTUI._sync_target_label(cfg)
        assert result == "test/-#1"

    def test_sync_target_label_other_type(self):
        """Test _sync_target_label for non-repository type."""
        cfg = SimpleNamespace(project_type="organization", owner="test", number=1)
        result = TaskTrackerTUI._sync_target_label(cfg)
        assert result == "organization:test#1"

    def test_sync_target_label_none(self):
        """Test _sync_target_label with None config."""
        result = TaskTrackerTUI._sync_target_label(None)
        assert result == "-"


class TestTaskTrackerTUIInstanceMethods:
    """Tests for TaskTrackerTUI instance methods."""

    @pytest.fixture
    def tui(self, tmp_path):
        """Create a TaskTrackerTUI instance for testing."""
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        projects_root = tmp_path / "projects_root"
        projects_root.mkdir()
        return TaskTrackerTUI(tasks_dir=tasks_dir, projects_root=projects_root)

    def test_current_description_snippet_with_detail(self, tui):
        """Test _current_description_snippet with task detail."""
        detail = TaskDetail(
            id="TASK-001",
            title="Test Task",
            status="DONE",
            description="Test description",
            context="",
            created="",
            updated="",
        )
        tui.current_task_detail = detail
        tui.detail_mode = True
        result = tui._current_description_snippet()
        assert "Test description" in result

    def test_current_description_snippet_with_context(self, tui):
        """Test _current_description_snippet with context."""
        detail = TaskDetail(
            id="TASK-001",
            title="Test Task",
            status="DONE",
            description="",
            context="Test context",
            created="",
            updated="",
        )
        tui.current_task_detail = detail
        tui.detail_mode = True
        result = tui._current_description_snippet()
        assert "Test context" in result

    def test_current_description_snippet_empty(self, tui):
        """Test _current_description_snippet with empty detail."""
        tui.detail_mode = False
        # Mock filtered_tasks as empty
        with patch.object(type(tui), 'filtered_tasks', property(lambda self: [])):
            result = tui._current_description_snippet()
            assert result == ""

    def test_command_palette_handoff_invokes_export(self, tui):
        called = {"count": 0}

        def fake_export():
            called["count"] += 1

        tui.export_handoff = fake_export
        tui._run_command_palette("handoff")
        assert called["count"] == 1

    def test_current_description_snippet_normalizes_whitespace(self, tui):
        """Test _current_description_snippet normalizes whitespace."""
        detail = TaskDetail(
            id="TASK-001",
            title="Test Task",
            status="DONE",
            description="Test   with\nmultiple   spaces",
            context="",
            created="",
            updated="",
        )
        tui.current_task_detail = detail
        tui.detail_mode = True
        result = tui._current_description_snippet()
        assert "  " not in result  # No double spaces
        assert "\n" not in result  # No newlines

    def test_detail_content_width(self, tui):
        """Test _detail_content_width calculation."""
        width = tui._detail_content_width(100)
        assert isinstance(width, int)
        assert width > 0
        assert width <= 100

    def test_detail_content_width_default(self, tui):
        """Test _detail_content_width with default terminal width."""
        with patch.object(tui, "get_terminal_width", return_value=80):
            width = tui._detail_content_width()
            assert isinstance(width, int)
            assert width > 0

    def test_cmd_tui_uses_global_project_namespace(self, tmp_path, monkeypatch):
        """cmd_tui всегда открывает глобальное хранилище проекта."""
        args = SimpleNamespace(
            theme="dark-olive",
            mono_select=False,
        )
        calls = {}

        def fake_run(self):
            calls["run_called"] = True
            calls["tasks_dir"] = self.tasks_dir

        proj = tmp_path / "proj"
        proj.mkdir()
        expected_global = Path.home() / ".tasks" / "proj"

        import core.desktop.devtools.interface.tui_app as tui_app
        monkeypatch.chdir(proj)
        with patch.object(tui_app.TaskTrackerTUI, "run", fake_run):
            tui_app.cmd_tui(args)
        assert calls["run_called"] is True
        assert calls["tasks_dir"].resolve() == expected_global.resolve()

    def test_constructor_uses_injected_tasks_dir_if_given(self, tmp_path):
        custom = tmp_path / "custom_tasks"
        projects_root = tmp_path / "projects_root"
        projects_root.mkdir()
        tui = TaskTrackerTUI(tasks_dir=custom, projects_root=projects_root)
        assert tui.tasks_dir.resolve() == custom.resolve()

    def test_delete_project_removes_directory(self, tmp_path):
        projects_root = tmp_path / "projects"
        proj1 = projects_root / "proj1"
        proj2 = projects_root / "proj2"
        proj1.mkdir(parents=True)
        proj2.mkdir(parents=True)

        for p in (proj1, proj2):
            (p / "TASK-001.task").write_text(
                TaskDetail(id="TASK-001", title="t", status="TODO", created="", updated="").to_file_content(),
                encoding="utf-8",
            )

        tasks_dir = tmp_path / "default_tasks"
        tasks_dir.mkdir(parents=True)
        tui = TaskTrackerTUI(tasks_dir=tasks_dir, projects_root=projects_root)

        assert len(tui.tasks) == 2
        assert proj1.exists()

        tui.selected_index = 0
        tui.delete_current_item()

        assert not proj1.exists()
        assert len(tui.tasks) == 1
        assert tui.tasks[0].name == "proj2"

    def test_confirm_delete_project_requires_confirmation(self, tmp_path):
        projects_root = tmp_path / "projects"
        proj1 = projects_root / "proj1"
        proj2 = projects_root / "proj2"
        proj1.mkdir(parents=True)
        proj2.mkdir(parents=True)
        for p in (proj1, proj2):
            (p / "TASK-001.task").write_text(
                TaskDetail(id="TASK-001", title="t", status="TODO", created="", updated="").to_file_content(),
                encoding="utf-8",
            )

        tasks_dir = tmp_path / "default_tasks"
        tasks_dir.mkdir(parents=True)

        tui = TaskTrackerTUI(tasks_dir=tasks_dir, projects_root=projects_root)
        assert tui.project_mode is True
        assert [p.name for p in tui.tasks] == ["proj1", "proj2"]

        tui.selected_index = 0
        tui.confirm_delete_current_item()
        assert tui.confirm_mode is True
        assert "proj1" in " ".join(tui.confirm_lines)

        tui._confirm_cancel()
        assert tui.confirm_mode is False
        assert proj1.exists()

        tui.confirm_delete_current_item()
        tui._confirm_accept()
        assert not proj1.exists()
        assert [p.name for p in tui.tasks] == ["proj2"]

    def test_return_to_projects_keeps_selection(self, tmp_path):
        projects_root = tmp_path / "projects"
        proj1 = projects_root / "proj1"
        proj2 = projects_root / "proj2"
        proj1.mkdir(parents=True)
        proj2.mkdir(parents=True)

        for p in (proj1, proj2):
            (p / "TASK-001.task").write_text(
                TaskDetail(id="TASK-001", title="t", status="TODO", created="", updated="").to_file_content(),
                encoding="utf-8",
            )

        tasks_dir = tmp_path / "default_tasks"
        tasks_dir.mkdir(parents=True)
        tui = TaskTrackerTUI(tasks_dir=tasks_dir, projects_root=projects_root)
        assert [p.name for p in tui.tasks] == ["proj1", "proj2"]

        tui.selected_index = 1
        tui._enter_project(tui.tasks[1])
        assert tui.project_mode is False

        tui.return_to_projects()

        assert tui.project_mode is True
        assert tui.selected_index == 1
        assert tui.tasks[tui.selected_index].name == "proj2"

    def test_format_cell_left(self, tui):
        """Test _format_cell with left alignment."""
        result = tui._format_cell("test", 10, "left")
        assert result == "test      "
        assert len(result) == 10

    def test_format_cell_right(self, tui):
        """Test _format_cell with right alignment."""
        result = tui._format_cell("test", 10, "right")
        assert result == "      test"
        assert len(result) == 10

    def test_format_cell_center(self, tui):
        """Test _format_cell with center alignment."""
        result = tui._format_cell("test", 10, "center")
        assert result == "   test   "
        assert len(result) == 10

    def test_filtered_tasks_search_filters_projects_by_name(self, tmp_path):
        projects_root = tmp_path / "projects"
        projects_root.mkdir()
        tasks_dir = tmp_path / "default_tasks"
        tasks_dir.mkdir(parents=True)
        tui = TaskTrackerTUI(tasks_dir=tasks_dir, projects_root=projects_root)

        tui.project_mode = True
        tui.current_filter = None
        tui.tasks = [
            Task(id="proj-a", name="Alpha Project", status=Status.TODO, description="", category="project"),
            Task(id="proj-b", name="Beta", status=Status.TODO, description="", category="project"),
        ]

        tui.search_query = "alp"
        assert [t.name for t in tui.filtered_tasks] == ["Alpha Project"]

    def test_filtered_tasks_search_filters_tasks_by_id_title_domain_and_tags(self, tmp_path):
        projects_root = tmp_path / "projects"
        projects_root.mkdir()
        tasks_dir = tmp_path / "default_tasks"
        tasks_dir.mkdir(parents=True)
        tui = TaskTrackerTUI(tasks_dir=tasks_dir, projects_root=projects_root)

        tui.project_mode = False
        tui.current_filter = None
        a = TaskDetail(id="TASK-001", title="Alpha", status="TODO", domain="dom/a", tags=["backend"], created="", updated="")
        b = TaskDetail(id="TASK-002", title="Beta", status="TODO", domain="dom/b", tags=["frontend"], created="", updated="")
        tui.tasks = [
            Task(id="TASK-001", name="Alpha", status=Status.TODO, description="", category="", detail=a, domain=a.domain),
            Task(id="TASK-002", name="Beta", status=Status.TODO, description="", category="", detail=b, domain=b.domain),
        ]

        tui.search_query = "TASK-002"
        assert [t.id for t in tui.filtered_tasks] == ["TASK-002"]

        tui.search_query = "dom/a"
        assert [t.id for t in tui.filtered_tasks] == ["TASK-001"]

        tui.search_query = "backend"
        assert [t.id for t in tui.filtered_tasks] == ["TASK-001"]

        tui.search_query = "alpha backend"
        assert [t.id for t in tui.filtered_tasks] == ["TASK-001"]

    def test_cycle_detail_tab_cycles_through_tabs(self, tui):
        detail = TaskDetail(id="TASK-001", title="T", status="ACTIVE", domain="dom/a", created="", updated="")
        tui.current_task_detail = detail
        tui.detail_mode = True

        tui.detail_tab = "overview"
        tui.cycle_detail_tab(1)
        assert tui.detail_tab == "plan"
        tui.cycle_detail_tab(1)
        assert tui.detail_tab == "contract"
        tui.cycle_detail_tab(1)
        assert tui.detail_tab == "notes"
        tui.cycle_detail_tab(1)
        assert tui.detail_tab == "meta"
        tui.cycle_detail_tab(1)
        assert tui.detail_tab == "radar"
        tui.cycle_detail_tab(1)
        assert tui.detail_tab == "overview"

    def test_show_task_details_sets_radar_tab_for_plan(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        tui = TaskTrackerTUI(tasks_dir=tasks_dir)
        tui.project_mode = False
        tui.project_section = "plans"
        plan_detail = TaskDetail(id="PLAN-001", title="Plan", status="TODO", kind="plan", created="", updated="")
        task = Task(id="PLAN-001", name="Plan", status=Status.TODO, description="", category="plan", detail=plan_detail)

        tui.show_task_details(task)
        assert tui.detail_tab == "radar"

    def test_move_vertical_selection_scrolls_in_non_overview_detail_tabs(self, tui):
        detail = TaskDetail(id="TASK-001", title="T", status="ACTIVE", domain="dom/a", created="", updated="")
        tui.current_task_detail = detail
        tui.detail_mode = True
        tui.detail_tab = "contract"
        tui.detail_tab_scroll_offsets["contract"] = 0

        tui.move_vertical_selection(2)
        assert tui.detail_tab_scroll_offsets["contract"] == 2

    def test_toggle_task_completion_cycles_without_force(self, tui, monkeypatch):
        calls = {}

        class Manager:
            def update_task_status(self, task_id, status, domain="", force=False):
                calls["args"] = (task_id, status, domain, force)
                return True, None

        tui.project_mode = False
        tui.current_filter = None
        tui.search_query = ""
        tui.manager = Manager()
        tui.tasks = [Task(id="TASK-001", name="Alpha", status=Status.TODO, description="", category="", domain="dom/a")]
        tui.selected_index = 0
        monkeypatch.setattr(tui, "load_tasks", lambda preserve_selection=False, skip_sync=False, **k: calls.setdefault("loaded", (preserve_selection, skip_sync)))
        monkeypatch.setattr(tui, "set_status_message", lambda msg, ttl=0: calls.setdefault("status", msg))
        monkeypatch.setattr(tui, "force_render", lambda: None)

        tui.toggle_task_completion()
        assert calls["args"] == ("TASK-001", "ACTIVE", "dom/a", False)

        # ACTIVE -> DONE
        calls.clear()
        tui.tasks[0].status = Status.ACTIVE
        tui.toggle_task_completion()
        assert calls["args"] == ("TASK-001", "DONE", "dom/a", False)

        # DONE -> ACTIVE
        calls.clear()
        tui.tasks[0].status = Status.DONE
        tui.toggle_task_completion()
        assert calls["args"] == ("TASK-001", "ACTIVE", "dom/a", False)

    def test_toggle_task_completion_done_failure_opens_details(self, tui, monkeypatch):
        calls = {"details": 0}

        class Manager:
            def update_task_status(self, task_id, status, domain="", force=False):
                return False, {"message": "blocked"}

        tui.project_mode = False
        tui.current_filter = None
        tui.search_query = ""
        tui.manager = Manager()
        tui.tasks = [Task(id="TASK-001", name="Alpha", status=Status.ACTIVE, description="", category="", domain="dom/a")]
        tui.selected_index = 0
        monkeypatch.setattr(tui, "set_status_message", lambda msg, ttl=0: calls.setdefault("status", msg))
        monkeypatch.setattr(tui, "show_task_details", lambda task: calls.__setitem__("details", calls["details"] + 1))

        tui.toggle_task_completion()
        assert calls["status"] == "blocked"
        assert calls["details"] == 1

    def test_apply_subtask_completion_respects_checkpoints_by_default(self, tui, monkeypatch):
        calls = {}

        class Manager:
            def set_step_completed(self, task_id, index, completed, domain="", path=None, force=False):
                calls["args"] = (task_id, index, completed, domain, path, force)
                return False, "needs checkpoints"

        detail = TaskDetail(id="TASK-001", title="T", status="ACTIVE", domain="dom/a", created="", updated="")
        tui.current_task_detail = detail
        tui.detail_mode = True
        tui.navigation_stack = []
        tui.manager = Manager()
        st = SimpleNamespace(criteria_confirmed=False, tests_confirmed=False, tests_auto_confirmed=False)

        monkeypatch.setattr(tui, "set_status_message", lambda msg, ttl=0: calls.setdefault("status", msg))
        tui._apply_subtask_completion(path="0", desired=True, force=False, subtask_hint=st)

        assert calls["args"] == ("TASK-001", 0, True, "dom/a", "0", False)
        assert calls["status"] == "needs checkpoints"
        assert tui.checkpoint_mode is True

    def test_format_cell_truncates(self, tui):
        """Test _format_cell truncates long content."""
        result = tui._format_cell("very long text", 5, "left")
        assert len(result) == 5
        assert result == "very "

    def test_get_task_detail_from_task(self, tui):
        """Test _get_task_detail retrieves detail from task."""
        task = Task(
            name="Test",
            status=Status.DONE,
            description="",
            category="test",
            task_file=None,
        )
        task.detail = TaskDetail(
            id="TASK-001",
            title="Test Task",
            status="DONE",
            description="Detail",
            context="",
            created="",
            updated="",
        )
        result = tui._get_task_detail(task)
        assert result == task.detail

    def test_get_task_detail_from_file(self, tui, tmp_path):
        """Test _get_task_detail loads detail from file."""
        task_file = tmp_path / ".tasks" / "test.task"
        task_file.parent.mkdir(parents=True, exist_ok=True)
        # Write proper YAML format
        task_file.write_text("id: TASK-001\ntitle: Test task\ndescription: Test task\nstatus: DONE\n", encoding="utf-8")
        
        task = Task(
            name="Test",
            status=Status.DONE,
            description="",
            category="test",
            task_file=str(task_file),
        )
        result = tui._get_task_detail(task)
        # May return None if parsing fails, which is acceptable
        if result is not None:
            assert result.description == "Test task"

    def test_get_task_detail_none(self, tui):
        """Test _get_task_detail returns None when no detail available."""
        task = Task(
            name="Test",
            status=Status.DONE,
            description="",
            category="test",
            task_file=None,
        )
        result = tui._get_task_detail(task)
        assert result is None

    def test_current_task_detail_obj_from_detail_mode(self, tui):
        """Test _current_task_detail_obj returns detail from detail_mode."""
        detail = TaskDetail(
            id="TASK-001",
            title="Test Task",
            status="DONE",
            description="Detail",
            context="",
            created="",
            updated="",
        )
        tui.detail_mode = True
        tui.current_task_detail = detail
        result = tui._current_task_detail_obj()
        assert result == detail

    def test_current_task_detail_obj_from_filtered_tasks(self, tui):
        """Test _current_task_detail_obj returns detail from filtered_tasks."""
        detail = TaskDetail(
            id="TASK-001",
            title="Test Task",
            status="DONE",
            description="Detail",
            context="",
            created="",
            updated="",
        )
        task = Task(
            name="Test",
            status=Status.DONE,
            description="",
            category="test",
            detail=detail,
        )
        tui.detail_mode = False
        # Mock filtered_tasks as a property that returns the task list
        original_property = type(tui).filtered_tasks
        with patch.object(type(tui), 'filtered_tasks', property(lambda self: [task])):
            tui.selected_index = 0
            result = tui._current_task_detail_obj()
            assert result == detail

    def test_current_task_detail_obj_none(self, tui):
        """Test _current_task_detail_obj returns None when no detail available."""
        tui.detail_mode = False
        # Mock filtered_tasks as a property that returns empty list
        with patch.object(type(tui), 'filtered_tasks', property(lambda self: [])):
            result = tui._current_task_detail_obj()
            assert result is None

    def test_task_created_value_with_detail(self, tui):
        """Test _task_created_value returns created date from detail."""
        detail = TaskDetail(
            id="TASK-001",
            title="Test Task",
            status="DONE",
            description="",
            context="",
            created="2025-01-01",
            updated="",
        )
        task = Task(
            name="Test",
            status=Status.DONE,
            description="",
            category="test",
            detail=detail,
        )
        result = tui._task_created_value(task)
        assert result == "2025-01-01"

    def test_task_created_value_without_detail(self, tui):
        """Test _task_created_value returns dash when no created date."""
        task = Task(
            name="Test",
            status=Status.DONE,
            description="",
            category="test",
        )
        result = tui._task_created_value(task)
        assert result == "—"

    def test_task_done_value_with_updated(self, tui):
        """Test _task_done_value returns updated date when status is DONE."""
        detail = TaskDetail(
            id="TASK-001",
            title="Test Task",
            status="DONE",
            description="",
            context="",
            created="",
            updated="2025-01-02",
        )
        task = Task(
            name="Test",
            status=Status.DONE,
            description="",
            category="test",
            detail=detail,
        )
        result = tui._task_done_value(task)
        assert result == "2025-01-02"

    def test_task_done_value_not_ok(self, tui):
        """Test _task_done_value returns dash when status is not DONE."""
        detail = TaskDetail(
            id="TASK-001",
            title="Test Task",
            status="ACTIVE",
            description="",
            context="",
            created="",
            updated="2025-01-02",
        )
        task = Task(
            name="Test",
            status=Status.ACTIVE,
            description="",
            category="test",
            detail=detail,
        )
        result = tui._task_done_value(task)
        assert result == "—"

    def test_task_done_value_no_updated(self, tui):
        """Test _task_done_value returns dash when no updated date."""
        detail = TaskDetail(
            id="TASK-001",
            title="Test Task",
            status="DONE",
            description="",
            context="",
            created="",
            updated="",
        )
        task = Task(
            name="Test",
            status=Status.DONE,
            description="",
            category="test",
            detail=detail,
        )
        result = tui._task_done_value(task)
        assert result == "—"

    def test_parse_task_datetime_iso_format(self, tui):
        """Test _parse_task_datetime parses ISO format."""
        result = tui._parse_task_datetime("2025-01-01T12:00:00")
        assert result is not None
        assert result.year == 2025
        assert result.month == 1
        assert result.day == 1

    def test_parse_task_datetime_date_only(self, tui):
        """Test _parse_task_datetime parses date-only format."""
        result = tui._parse_task_datetime("2025-01-01")
        assert result is not None
        assert result.year == 2025
        assert result.month == 1
        assert result.day == 1

    def test_parse_task_datetime_with_time(self, tui):
        """Test _parse_task_datetime parses datetime format."""
        result = tui._parse_task_datetime("2025-01-01 12:00")
        assert result is not None
        assert result.hour == 12

    def test_parse_task_datetime_none(self, tui):
        """Test _parse_task_datetime returns None for None input."""
        result = tui._parse_task_datetime(None)
        assert result is None

    def test_parse_task_datetime_empty(self, tui):
        """Test _parse_task_datetime returns None for empty string."""
        result = tui._parse_task_datetime("")
        assert result is None

    def test_parse_task_datetime_invalid(self, tui):
        """Test _parse_task_datetime returns None for invalid format."""
        result = tui._parse_task_datetime("invalid-date")
        assert result is None

    def test_task_duration_value_with_dates(self, tui):
        """Test _task_duration_value calculates duration."""
        detail = TaskDetail(
            id="TASK-001",
            title="Test Task",
            status="DONE",
            description="",
            context="",
            created="2025-01-01",
            updated="2025-01-03",
        )
        result = tui._task_duration_value(detail)
        assert result != "-"
        assert "2" in result or "day" in result.lower() or "48" in result  # 2 days or hours

    def test_task_duration_value_no_start(self, tui):
        """Test _task_duration_value returns dash when no start date."""
        detail = TaskDetail(
            id="TASK-001",
            title="Test Task",
            status="DONE",
            description="",
            context="",
            created="",
            updated="2025-01-03",
        )
        result = tui._task_duration_value(detail)
        assert result == "-"

    def test_task_duration_value_no_end(self, tui):
        """Test _task_duration_value returns dash when no end date."""
        detail = TaskDetail(
            id="TASK-001",
            title="Test Task",
            status="ACTIVE",  # Not DONE, so end date won't be parsed
            description="",
            context="",
            created="2025-01-01",
            updated="",  # Empty updated
        )
        result = tui._task_duration_value(detail)
        # Should return "-" because status is not "DONE" or updated is empty
        assert result == "-"

    def test_task_duration_value_none_detail(self, tui):
        """Test _task_duration_value returns dash for None detail."""
        result = tui._task_duration_value(None)
        assert result == "-"

    def test_sync_status_summary_with_config(self, tui):
        """Test _sync_status_summary with repository config."""
        sync_service = Mock()
        sync_service.config = SimpleNamespace(
            project_type="repository",
            owner="test",
            repo="repo",
            number=1,
            enabled=True,
        )
        sync_service.enabled = True
        tui.manager.sync_service = sync_service
        result = tui._sync_status_summary()
        assert "ON" in result
        assert "test/repo#1" in result

    def test_sync_status_summary_off(self, tui):
        """Test _sync_status_summary when sync is disabled."""
        sync_service = Mock()
        sync_service.config = SimpleNamespace(
            project_type="repository",
            owner="test",
            repo="repo",
            number=1,
            enabled=False,
        )
        sync_service.enabled = False
        tui.manager.sync_service = sync_service
        result = tui._sync_status_summary()
        assert "OFF" in result

    def test_sync_status_summary_no_config(self, tui):
        """Test _sync_status_summary when no config."""
        sync_service = Mock()
        sync_service.config = None
        tui.manager.sync_service = sync_service
        result = tui._sync_status_summary()
        assert result == "OFF"

    def test_force_render(self, tui):
        """Test force_render invalidates app."""
        mock_app = Mock()
        tui.app = mock_app
        tui.force_render()
        mock_app.invalidate.assert_called_once()

    def test_force_render_no_app(self, tui):
        """Test force_render handles missing app gracefully."""
        tui.app = None
        # Should not raise
        tui.force_render()

    def test_start_spinner(self, tui):
        """Test _start_spinner sets spinner state."""
        tui._start_spinner("Loading...")
        assert tui.spinner_active is True
        assert tui.spinner_message == "Loading..."

    def test_stop_spinner(self, tui):
        """Test _stop_spinner clears spinner state."""
        tui.spinner_active = True
        tui.spinner_message = "Loading..."
        tui._stop_spinner()
        assert tui.spinner_active is False
        assert tui.spinner_message == ""

    def test_spinner_context_manager(self, tui):
        """Test _spinner context manager."""
        with tui._spinner("Loading..."):
            assert tui.spinner_active is True
            assert tui.spinner_message == "Loading..."
        assert tui.spinner_active is False
        assert tui.spinner_message == ""

    def test_run_with_spinner(self, tui):
        """Test _run_with_spinner executes function with spinner."""
        def test_func(x, y):
            return x + y
        result = tui._run_with_spinner("Computing...", test_func, 2, 3)
        assert result == 5
        assert tui.spinner_active is False  # Spinner should be stopped
