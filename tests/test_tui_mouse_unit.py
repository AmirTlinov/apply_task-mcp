from types import SimpleNamespace

from prompt_toolkit.mouse_events import MouseEventType, MouseButton, MouseModifier

from core.desktop.devtools.interface import tui_mouse


def _mouse(event_type, button=MouseButton.LEFT, y=0, modifiers=()):
    return SimpleNamespace(event_type=event_type, button=button, position=SimpleNamespace(y=y), modifiers=modifiers)


def test_middle_paste_triggers_clipboard():
    called = {}

    class TUI:
        editing_mode = True
        edit_context = "token"

        def _paste_from_clipboard(self):
            called["paste"] = True

    tui_mouse.handle_body_mouse(TUI(), _mouse(MouseEventType.MOUSE_UP, MouseButton.MIDDLE))
    assert called.get("paste")


def test_single_subtask_view_scroll():
    moves = []

    class TUI:
        single_subtask_view = True

        def move_vertical_selection(self, delta):
            moves.append(delta)

    tui_mouse.handle_body_mouse(TUI(), _mouse(MouseEventType.SCROLL_DOWN))
    tui_mouse.handle_body_mouse(TUI(), _mouse(MouseEventType.SCROLL_UP))
    assert moves == [1, -1]


def test_settings_mode_scroll_and_click():
    actions = []

    class TUI:
        settings_mode = True
        editing_mode = False

        def move_settings_selection(self, delta):
            actions.append(("move", delta))

        def activate_settings_option(self):
            actions.append(("activate", None))

    tui = TUI()
    tui_mouse.handle_body_mouse(tui, _mouse(MouseEventType.SCROLL_DOWN))
    tui_mouse.handle_body_mouse(tui, _mouse(MouseEventType.MOUSE_UP))
    assert actions == [("move", 1), ("activate", None)]


def test_scroll_with_shift_changes_horizontal():
    class TUI:
        horizontal_offset = 0
        editing_mode = False
        detail_mode = False
        filtered_tasks = []

        def move_vertical_selection(self, delta):
            self.moved = delta

    tui = TUI()
    tui_mouse.handle_body_mouse(
        tui, _mouse(MouseEventType.SCROLL_DOWN, modifiers=(MouseModifier.SHIFT,))
    )
    assert tui.horizontal_offset == 5


def test_detail_click_selects_and_opens():
    calls = []

    class TUI:
        detail_mode = True
        current_task_detail = True
        detail_flat_subtasks = [("0", None), ("1", None)]
        detail_selected_index = None

        def _subtask_index_from_y(self, y):
            return y

        def _selected_subtask_entry(self):
            calls.append("select")

        def show_subtask_details(self, path):
            calls.append(f"open:{path}")

    tui = TUI()
    tui_mouse.handle_body_mouse(tui, _mouse(MouseEventType.MOUSE_UP, y=0))
    tui_mouse.handle_body_mouse(tui, _mouse(MouseEventType.MOUSE_UP, y=0))
    assert calls == ["select", "open:0"]


def test_list_click_selects_and_opens():
    calls = []

    class TUI:
        detail_mode = False
        filtered_tasks = ["A", "B"]
        selected_index = None

        def _task_index_from_y(self, y):
            return y

        def _ensure_selection_visible(self):
            calls.append("ensure")

        def show_task_details(self, task):
            calls.append(f"show:{task}")

    tui = TUI()
    tui_mouse.handle_body_mouse(tui, _mouse(MouseEventType.MOUSE_UP, y=1))
    tui_mouse.handle_body_mouse(tui, _mouse(MouseEventType.MOUSE_UP, y=1))
    assert calls == ["ensure", "show:B"]

