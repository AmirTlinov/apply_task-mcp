from types import SimpleNamespace

from core.desktop.devtools.interface import tui_navigation


def test_move_vertical_selection_list_mode():
    calls = {}

    class TUI:
        filtered_tasks = [1, 2, 3]
        selected_index = 1
        detail_mode = False
        settings_mode = False

        def _ensure_selection_visible(self):
            calls["visible"] = True

        def force_render(self):
            calls["render"] = True

    tui_navigation.move_vertical_selection(TUI(), 1)
    assert calls["visible"] and calls["render"]


def test_move_vertical_selection_settings_mode():
    class TUI:
        settings_mode = True
        settings_selected_index = 0
        def __init__(self):
            self._calls = {}

        def _settings_options(self):
            return ["a", "b", "c"]

        def _ensure_settings_selection_visible(self, total):
            self._calls["visible"] = total

        def force_render(self):
            self._calls["render"] = True

    tui = TUI()
    tui_navigation.move_vertical_selection(tui, 1)
    assert tui.settings_selected_index == 1
    assert tui._calls["visible"] == 3 and tui._calls["render"]


def test_move_vertical_selection_detail_mode_rebuild(monkeypatch):
    calls = {}

    class TUI:
        detail_mode = True
        detail_flat_subtasks = []
        current_task_detail = SimpleNamespace(subtasks=[1])
        detail_selected_path = ""
        detail_selected_index = 0

        def _rebuild_detail_flat(self, path):
            self.detail_flat_subtasks = [(0, None)]
            calls["rebuilt"] = True

        def get_detail_items_count(self):
            return 1

        def _selected_subtask_entry(self):
            calls["selected"] = True

        def _ensure_detail_selection_visible(self, items):
            calls["visible"] = items

        def force_render(self):
            calls["render"] = True

    tui_navigation.move_vertical_selection(TUI(), 0)
    assert calls["rebuilt"] and calls["selected"] and calls["render"]


def test_move_vertical_selection_detail_mode_no_items():
    class TUI:
        detail_mode = True
        detail_selected_index = 5

        def _rebuild_detail_flat(self, path):
            raise AssertionError("should not rebuild")

        def get_detail_items_count(self):
            return 0

        def force_render(self):  # pragma: no cover - should not happen
            raise AssertionError("render not expected")

    tui = TUI()
    tui_navigation.move_vertical_selection(tui, 1)
    assert tui.detail_selected_index == 0


def test_move_vertical_selection_settings_empty_options():
    class TUI:
        settings_mode = True
        settings_selected_index = 3

        def _settings_options(self):
            return []

        def force_render(self):  # pragma: no cover - should not happen
            raise AssertionError("render not expected")

    tui = TUI()
    tui_navigation.move_vertical_selection(tui, 1)
    assert tui.settings_selected_index == 0


def test_move_vertical_selection_list_empty():
    class TUI:
        filtered_tasks = []
        selected_index = 7
        detail_mode = False
        settings_mode = False

        def force_render(self):  # pragma: no cover - should not happen
            raise AssertionError("render not expected")

    tui = TUI()
    tui_navigation.move_vertical_selection(tui, 1)
    assert tui.selected_index == 0
