#!/usr/bin/env python3
"""Additional unit tests for tui_state module to improve coverage."""

from types import SimpleNamespace

from core.desktop.devtools.interface import tui_state


class TestToggleCollapseSelected:
    """Tests for toggle_collapse_selected function."""

    def test_toggle_collapse_selected_removes_when_collapsed(self):
        """Test toggle_collapse_selected removes task when already collapsed."""
        rendered = {}

        class TUI(SimpleNamespace):
            def __init__(self):
                super().__init__(
                    detail_mode=False,
                    filtered_tasks=[SimpleNamespace(id="A")],
                    selected=0,
                    collapsed_tasks={"A"},  # Already collapsed
                )

            def render(self, force=False):
                rendered["called"] = force

        t = TUI()
        tui_state.toggle_collapse_selected(t)
        assert "A" not in t.collapsed_tasks  # Should be removed
        assert rendered["called"] is True


class TestToggleSubtaskCollapse:
    """Tests for toggle_subtask_collapse function."""

    def test_toggle_subtask_collapse_no_entry(self):
        """Test toggle_subtask_collapse returns early when no entry."""
        class TUI(SimpleNamespace):
            def _selected_subtask_entry(self):
                return None

        t = TUI()
        # Should not raise
        tui_state.toggle_subtask_collapse(t, expand=True)
        tui_state.toggle_subtask_collapse(t, expand=False)

    def test_toggle_subtask_collapse_no_children_expand(self):
        """Test toggle_subtask_collapse with no children and expand=True."""
        rebuilt = {}

        class TUI(SimpleNamespace):
            def __init__(self):
                super().__init__(
                    detail_collapsed=set(),
                    detail_flat_subtasks=[],
                )

            def _selected_subtask_entry(self):
                return ("0", SimpleNamespace(children=[]), None, False, False)  # No children, has_children=False

            def _select_subtask_by_path(self, path):
                rebuilt["select"] = path

            def _rebuild_detail_flat(self, path):
                rebuilt.setdefault("rebuild", []).append(path)

            def _ensure_detail_selection_visible(self, count):
                rebuilt["ensure"] = count

            def force_render(self):
                rebuilt["render"] = True

        t = TUI()
        tui_state.toggle_subtask_collapse(t, expand=True)
        # When has_children=False, the function returns early (line 22-28)
        # So nothing should be called
        assert len(rebuilt) == 0

    def test_toggle_subtask_collapse_no_children_collapse(self):
        """Test toggle_subtask_collapse with no children and expand=False."""
        rebuilt = {}

        class TUI(SimpleNamespace):
            def __init__(self):
                super().__init__(
                    detail_collapsed=set(),
                    detail_flat_subtasks=[],
                )

            def _selected_subtask_entry(self):
                return ("0.1", SimpleNamespace(children=[]), None, False, False)  # No children, nested path

            def _select_subtask_by_path(self, path):
                rebuilt["select"] = path

            def _rebuild_detail_flat(self, path):
                rebuilt.setdefault("rebuild", []).append(path)

            def _ensure_detail_selection_visible(self, count):
                rebuilt["ensure"] = count

            def force_render(self):
                rebuilt["render"] = True

        t = TUI()
        tui_state.toggle_subtask_collapse(t, expand=False)
        # Should select parent path
        assert rebuilt["select"] == "0"
        assert rebuilt["render"] is True

    def test_toggle_subtask_collapse_expand_when_not_collapsed(self):
        """Test toggle_subtask_collapse expand when not collapsed."""
        rebuilt = {}

        class TUI(SimpleNamespace):
            def __init__(self):
                super().__init__(
                    detail_collapsed=set(),
                    detail_flat_subtasks=[],
                )

            def _selected_subtask_entry(self):
                return ("0", SimpleNamespace(children=[1]), None, False, True)  # Not collapsed, has children

            def _select_subtask_by_path(self, path):
                rebuilt["select"] = path

            def _rebuild_detail_flat(self, path):
                rebuilt.setdefault("rebuild", []).append(path)

        t = TUI()
        tui_state.toggle_subtask_collapse(t, expand=True)
        # Should select child_path
        assert rebuilt["select"] == "0.0"
        assert "rebuild" in rebuilt

    def test_toggle_subtask_collapse_collapse_when_collapsed(self):
        """Test toggle_subtask_collapse collapse when already collapsed."""
        rebuilt = {}

        class TUI(SimpleNamespace):
            def __init__(self):
                super().__init__(
                    detail_collapsed={"0"},  # Already collapsed
                    detail_flat_subtasks=[],
                )

            def _selected_subtask_entry(self):
                return ("0.1", SimpleNamespace(children=[1]), None, True, True)  # Collapsed, has children, nested

            def _select_subtask_by_path(self, path):
                rebuilt["select"] = path

            def _rebuild_detail_flat(self, path):
                rebuilt.setdefault("rebuild", []).append(path)

            def _ensure_detail_selection_visible(self, count):
                rebuilt["ensure"] = count

            def force_render(self):
                rebuilt["render"] = True

        t = TUI()
        tui_state.toggle_subtask_collapse(t, expand=False)
        # Should select parent path when already collapsed
        assert rebuilt["select"] == "0"
        assert rebuilt["render"] is True


class TestMaybeReload:
    """Tests for maybe_reload function."""

    def test_maybe_reload_no_prev_detail(self):
        """Test maybe_reload when no previous detail."""
        loaded = {}

        class TUI(SimpleNamespace):
            def __init__(self):
                super().__init__(
                    _last_check=0,
                    _last_signature=0,
                    tasks=[SimpleNamespace(id="T", task_file="f")],
                    selected_index=0,
                    detail_mode=False,
                    current_task_detail=None,
                    detail_selected_path="",
                                    )

            def compute_signature(self):
                return 1

            def _t(self, key, **kwargs):
                return key

            def load_tasks(self, **kwargs):
                loaded["called"] = True

            def set_status_message(self, *_, **__):
                loaded["status"] = True

        tui = TUI()
        tui_state.maybe_reload(tui, now=10.0)
        assert loaded.get("called") is True
        assert tui._last_signature == 1
        # Should not try to restore detail
        assert "show" not in loaded

    def test_maybe_reload_restores_detail_without_path(self):
        """Test maybe_reload restores detail without detail_selected_path."""
        loaded = {}

        class TUI(SimpleNamespace):
            def __init__(self):
                detail = SimpleNamespace(id="X", subtasks=[])
                super().__init__(
                    _last_check=0,
                    _last_signature=0,
                    tasks=[SimpleNamespace(id="X", task_file="f")],
                    selected_index=0,
                    detail_mode=True,
                    current_task_detail=detail,
                    detail_selected_path="",  # No path
                                    )

            def compute_signature(self):
                return 1

            def _t(self, key, **kwargs):
                return key

            def load_tasks(self, **kwargs):
                pass

            def set_status_message(self, *_, **__):
                pass

            def show_task_details(self, t):
                loaded["show"] = True

            def _select_subtask_by_path(self, path):
                loaded["select"] = path

            def get_detail_items_count(self):
                return 1

            def _ensure_detail_selection_visible(self, *_, **__):
                pass

            def _get_subtask_by_path(self, *_, **__):
                return None

            def show_subtask_details(self, *_, **__):
                pass

        tui = TUI()
        tui_state.maybe_reload(tui, now=1.0)
        assert loaded.get("show") is True
        # Should not select path if empty
        assert "select" not in loaded

    def test_maybe_reload_restores_detail_task_not_found(self):
        """Test maybe_reload when previous detail task not found."""
        loaded = {}

        class TUI(SimpleNamespace):
            def __init__(self):
                detail = SimpleNamespace(id="X", subtasks=[])
                super().__init__(
                    _last_check=0,
                    _last_signature=0,
                    tasks=[SimpleNamespace(id="Y", task_file="f")],  # Different ID
                    selected_index=0,
                    detail_mode=True,
                    current_task_detail=detail,
                    detail_selected_path="0",
                                    )

            def compute_signature(self):
                return 1

            def _t(self, key, **kwargs):
                return key

            def load_tasks(self, **kwargs):
                pass

            def set_status_message(self, *_, **__):
                pass

            def show_task_details(self, t):
                loaded["show"] = True

            def _select_subtask_by_path(self, path):
                loaded["select"] = path

        tui = TUI()
        tui_state.maybe_reload(tui, now=1.0)
        # Should not show detail if task not found
        assert "show" not in loaded
