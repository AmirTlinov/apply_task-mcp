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


def test_scroll_with_shift_up_clamps_left():
    class TUI:
        horizontal_offset = 2
        editing_mode = False
        detail_mode = False
        filtered_tasks = []

        def move_vertical_selection(self, delta):
            raise AssertionError("vertical move should not happen")

    tui = TUI()
    tui_mouse.handle_body_mouse(
        tui, _mouse(MouseEventType.SCROLL_UP, modifiers=(MouseModifier.SHIFT,))
    )
    assert tui.horizontal_offset == 0


def test_settings_mode_none_when_editing():
    res = tui_mouse._handle_settings_mode(SimpleNamespace(settings_mode=True, editing_mode=True), _mouse(MouseEventType.SCROLL_DOWN))
    assert res is None


def test_handle_scroll_non_scroll_event():
    res = tui_mouse._handle_scroll(SimpleNamespace(), _mouse(MouseEventType.MOUSE_UP))
    assert res is False


def test_detail_click_without_detail_returns_true():
    res = tui_mouse._handle_detail_click(
        SimpleNamespace(detail_mode=True, current_task_detail=None),
        _mouse(MouseEventType.MOUSE_UP, y=0),
    )
    assert res is True


def test_handle_body_mouse_editing_mode_returns_notimplemented():
    res = tui_mouse.handle_body_mouse(SimpleNamespace(editing_mode=True), _mouse(MouseEventType.MOUSE_UP))
    assert res is NotImplemented


def test_handle_body_mouse_no_match_returns_notimplemented(monkeypatch):
    # monkeypatch settings handler to return sentinel to hit line 111
    monkeypatch.setattr(tui_mouse, "_handle_settings_mode", lambda tui, ev: "sentinel")
    res = tui_mouse.handle_body_mouse(SimpleNamespace(editing_mode=False), _mouse(MouseEventType.MOUSE_MOVE))
    assert res is NotImplemented


def test_scroll_up_without_shift_moves_vertical():
    class TUI:
        def __init__(self):
            self.moved = 0
            self.editing_mode = False
            self.detail_mode = False
            self.filtered_tasks = [1]

        def move_vertical_selection(self, delta):
            self.moved = delta

    tui = TUI()
    tui_mouse.handle_body_mouse(tui, _mouse(MouseEventType.SCROLL_UP))
    assert tui.moved == -1


def test_scroll_down_without_shift_moves_vertical():
    class TUI:
        def __init__(self):
            self.moved = 0
            self.editing_mode = False
            self.detail_mode = False
            self.filtered_tasks = [1]

        def move_vertical_selection(self, delta):
            self.moved = delta

    tui = TUI()
    tui_mouse.handle_body_mouse(tui, _mouse(MouseEventType.SCROLL_DOWN))
    assert tui.moved == 1


def test_settings_mode_scroll_up_branch():
    actions = []

    class TUI:
        settings_mode = True
        editing_mode = False

        def move_settings_selection(self, delta):
            actions.append(delta)

    tui = TUI()
    tui_mouse._handle_settings_mode(tui, _mouse(MouseEventType.SCROLL_UP))
    assert actions == [-1]


def test_detail_click_no_index_returns_true():
    class TUI:
        detail_mode = True
        current_task_detail = True
        detail_flat_subtasks = None

        def _subtask_index_from_y(self, y):
            return None

    res = tui_mouse._handle_detail_click(TUI(), _mouse(MouseEventType.MOUSE_UP, y=0))
    assert res is True


def test_list_click_ignored_when_detail_mode():
    res = tui_mouse._handle_list_click(SimpleNamespace(detail_mode=True), _mouse(MouseEventType.MOUSE_UP, y=0))
    assert res is None


def test_handle_body_mouse_falls_through_to_notimplemented():
    tui = SimpleNamespace(detail_mode=False, editing_mode=False, settings_mode=False)
    res = tui_mouse.handle_body_mouse(tui, _mouse(MouseEventType.MOUSE_MOVE))
    assert res is NotImplemented


def test_list_click_none_index_returns_true():
    class TUI:
        detail_mode = False
        def _task_index_from_y(self, y):
            return None
    res = tui_mouse._handle_list_click(TUI(), _mouse(MouseEventType.MOUSE_UP))
    assert res is True


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
