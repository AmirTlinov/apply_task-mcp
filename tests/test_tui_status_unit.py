from types import SimpleNamespace
import time

from prompt_toolkit.mouse_events import MouseButton, MouseEventType

from core.desktop.devtools.interface.tui_status import build_status_text


def test_build_status_text_basic():
    fragments = {}

    class DummyTUI(SimpleNamespace):
        def __init__(self):
            super().__init__(
                filtered_tasks=[SimpleNamespace(status="OK"), SimpleNamespace(status="WARN"), SimpleNamespace(status="FAIL")],
                domain_filter="",
                phase_filter=None,
                component_filter=None,
                current_filter=SimpleNamespace(value=["ALL"]),
                _filter_flash_until=0,
                spinner_message="",
                status_message="",
                status_message_expires=0,
                detail_mode=False,
                            )

        def _t(self, key, **kwargs):
            return key

        def _sync_indicator_fragments(self, flash=False):
            return [("class", "sync")]

        def _spinner_frame(self):
            return None

        def get_terminal_width(self):
            return 80

        def exit_detail_view(self):
            fragments["back"] = fragments.get("back", 0) + 1

        def open_settings_dialog(self):
            fragments["settings"] = fragments.get("settings", 0) + 1

    tui = DummyTUI()
    result = build_status_text(tui)
    text = "".join(fragment[1] for fragment in result)
    assert "STATUS_TASKS_COUNT" in text
    assert "ALL" in text
    # ensure settings button exists
    assert any("SETTINGS" in frag[1] for frag in result)
    tui.exit_detail_view()
    tui.open_settings_dialog()


def test_build_status_text_filter_flash(monkeypatch):
    translations = {
        "BTN_BACK": "[BACK]",
        "BTN_SETTINGS": "[SETTINGS]",
        "FILTER_ALL": "ALL",
        "FILTER_DONE": "DONE",
        "FILTER_IN_PROGRESS": "IN PROGRESS",
        "FILTER_BACKLOG": "BACKLOG",
        "STATUS_TASKS_COUNT": "{count} tasks",
        "STATUS_LOADING": "Loading",
    }

    class DummyTUI(SimpleNamespace):
        def __init__(self):
            super().__init__(
                filtered_tasks=[],
                domain_filter="",
                phase_filter=None,
                component_filter=None,
                current_filter=SimpleNamespace(value=["WARN"]),
                _filter_flash_until=0,
                spinner_message="",
                status_message="",
                status_message_expires=0,
                detail_mode=False,
            )

        def _t(self, key, **kwargs):
            tpl = translations.get(key, key)
            try:
                return tpl.format(**kwargs)
            except Exception:
                return tpl

        def _sync_indicator_fragments(self, flash=False):
            return [("class", "sync")]

        def _spinner_frame(self):
            return None

        def get_terminal_width(self):
            return 80

        def exit_detail_view(self):
            pass

        def open_settings_dialog(self):
            pass

    tui = DummyTUI()
    result = build_status_text(tui)
    text = "".join(fragment[1] for fragment in result)
    assert "IN PROGRESS" in text
    tui.exit_detail_view()
    tui.open_settings_dialog()


def test_build_status_text_spinner_and_status_message_clear():
    class DummyTUI(SimpleNamespace):
        def __init__(self):
            super().__init__(
                filtered_tasks=[SimpleNamespace(status="OK")],
                domain_filter="",
                phase_filter=None,
                component_filter=None,
                current_filter=None,
                _filter_flash_until=0,
                spinner_message="Loading",
                status_message="stale",
                status_message_expires=0,
                detail_mode=True,
                                _last_filter_value=None,
            )

        def _t(self, key, **kwargs):
            return key

        def _sync_indicator_fragments(self, flash=False):
            return [("class", "sync")]

        def _spinner_frame(self):
            return "*"

        def get_terminal_width(self):
            return 20

        def exit_detail_view(self):
            self.exited = True

        def open_settings_dialog(self):
            self.opened = True

    tui = DummyTUI()
    result = build_status_text(tui)
    # force back button handler
    back_handler = result[0][2]
    back_handler(SimpleNamespace(event_type=MouseEventType.MOUSE_UP, button=MouseButton.LEFT))
    assert tui.status_message == ""
    text = "".join(fragment[1] for fragment in result)
    assert "Loading" in text and "*" in text
    tui.exit_detail_view()
    tui.open_settings_dialog()


def test_build_status_text_width_fallback_and_settings_handler():
    class DummyTUI(SimpleNamespace):
        def __init__(self):
            super().__init__(
                filtered_tasks=[],
                domain_filter="",
                phase_filter=None,
                component_filter=None,
                current_filter=None,
                _filter_flash_until=0,
                spinner_message="",
                status_message="",
                status_message_expires=10,
                detail_mode=False,
                                _last_filter_value=None,
            )

        def _t(self, key, **kwargs):
            return key

        def _sync_indicator_fragments(self, flash=False):
            return []

        def _spinner_frame(self):
            return None

        def get_terminal_width(self):
            raise RuntimeError("boom")

        def exit_detail_view(self):
            self.exited = True

        def open_settings_dialog(self):
            self.opened = True

    tui = DummyTUI()
    result = build_status_text(tui)
    settings_handler = result[-1][2]
    settings_handler(SimpleNamespace(event_type=MouseEventType.MOUSE_UP, button=MouseButton.LEFT))
    text = "".join(f[1] for f in result)
    assert "SETTINGS" in text
    tui.exit_detail_view()
    tui.open_settings_dialog()


def test_status_handlers_return_not_implemented():
    class Dummy(SimpleNamespace):
        def exit_detail_view(self):
            self.called = True

        def open_settings_dialog(self):
            self.opened = True

    tui = Dummy(
        filtered_tasks=[],
        domain_filter="",
        phase_filter=None,
        component_filter=None,
        current_filter=None,
        _filter_flash_until=0,
        spinner_message="",
        status_message="",
        status_message_expires=0,
        detail_mode=True,
            )

    def _t(key, **kwargs):
        return key

    tui._t = _t
    tui._sync_indicator_fragments = lambda flash=False: []
    tui._spinner_frame = lambda: None
    tui.get_terminal_width = lambda: 80
    res = build_status_text(tui)
    back_handler = res[0][2]
    assert back_handler(SimpleNamespace(event_type=MouseEventType.MOUSE_UP, button=MouseButton.RIGHT)) is NotImplemented
    settings_handler = res[-1][2]
    assert settings_handler(SimpleNamespace(event_type=MouseEventType.MOUSE_UP, button=MouseButton.RIGHT)) is NotImplemented
    tui.exit_detail_view()
    tui.open_settings_dialog()


def test_status_message_displayed_when_not_expired():
    class DummyTUI(SimpleNamespace):
        def __init__(self):
            super().__init__(
                filtered_tasks=[],
                domain_filter="",
                phase_filter=None,
                component_filter=None,
                current_filter=None,
                _filter_flash_until=0,
                spinner_message="",
                status_message="msg",
                status_message_expires=time.time() + 10,
                detail_mode=False,
                            )

        def _t(self, key, **kwargs):
            return key

        def _sync_indicator_fragments(self, flash=False):
            return []

        def _spinner_frame(self):
            return None

        def get_terminal_width(self):
            return 50

        def exit_detail_view(self):
            pass

        def open_settings_dialog(self):
            pass

    tui = DummyTUI()
    text = "".join(part[1] for part in build_status_text(tui))
    assert "msg" in text
    tui.exit_detail_view()
    tui.open_settings_dialog()


def test_status_message_expired_clears():
    class Dummy(SimpleNamespace):
        def __init__(self):
            super().__init__(
                filtered_tasks=[],
                domain_filter="",
                phase_filter=None,
                component_filter=None,
                current_filter=None,
                _filter_flash_until=0,
                spinner_message="",
                status_message="old",
                status_message_expires=time.time() - 1,
                detail_mode=False,
                            )

        def _t(self, key, **kwargs):
            return key

        def _sync_indicator_fragments(self, flash=False):
            return []

        def _spinner_frame(self):
            return None

        def get_terminal_width(self):
            return 50

        def exit_detail_view(self):
            pass

        def open_settings_dialog(self):
            pass

    tui = Dummy()
    build_status_text(tui)
    assert tui.status_message == ""
    tui.exit_detail_view()
    tui.open_settings_dialog()


def test_status_filter_flash_changes_on_new_filter():
    class Dummy(SimpleNamespace):
        def __init__(self):
            super().__init__(
                filtered_tasks=[],
                domain_filter="",
                phase_filter=None,
                component_filter=None,
                current_filter=SimpleNamespace(value=["FAIL"]),
                _filter_flash_until=0,
                spinner_message="",
                status_message="",
                status_message_expires=0,
                detail_mode=False,
                                _last_filter_value="ALL",
            )

        def _t(self, key, **kwargs):
            return key

        def _sync_indicator_fragments(self, flash=False):
            return []

        def _spinner_frame(self):
            return None

        def get_terminal_width(self):
            return 80

        def exit_detail_view(self):
            pass

        def open_settings_dialog(self):
            pass

    tui = Dummy()
    build_status_text(tui)
    assert tui._filter_flash_until > 0
    tui.exit_detail_view()
    tui.open_settings_dialog()


def test_status_message_shown_with_flash_and_spinner():
    class Dummy(SimpleNamespace):
        def __init__(self):
            super().__init__(
                filtered_tasks=[SimpleNamespace(status=SimpleNamespace(name="OK", value=0))],
                domain_filter="",
                phase_filter=None,
                component_filter=None,
                current_filter=None,
                _filter_flash_until=time.time() + 1,
                spinner_message="spin",
                status_message="msg",
                status_message_expires=time.time() + 1,
                detail_mode=False,
                                _last_filter_value="ALL",
            )

        def _t(self, key, **kwargs):
            return key

        def _sync_indicator_fragments(self, flash=False):
            return []

        def _spinner_frame(self):
            return "*"

        def get_terminal_width(self):
            return 200

        def exit_detail_view(self):
            pass

        def open_settings_dialog(self):
            pass

    tui = Dummy()
    text = "".join(t[1] for t in build_status_text(tui))
    assert "spin" in text and "msg" in text and "BACKLOG" not in text
    tui.exit_detail_view()
    tui.open_settings_dialog()


def test_status_filter_flash_sets_flag():
    translations = {
        "BTN_BACK": "[BACK]",
        "BTN_SETTINGS": "[SETTINGS]",
        "FILTER_ALL": "ALL",
        "FILTER_DONE": "DONE",
        "FILTER_IN_PROGRESS": "IN PROGRESS",
        "FILTER_BACKLOG": "BACKLOG",
        "STATUS_TASKS_COUNT": "{count} tasks",
        "STATUS_LOADING": "Loading",
    }

    class Dummy(SimpleNamespace):
        def __init__(self):
            super().__init__(
                filtered_tasks=[],
                domain_filter="",
                phase_filter=None,
                component_filter=None,
                current_filter=SimpleNamespace(value=["WARN"]),
                _filter_flash_until=0,
                spinner_message="",
                status_message="",
                status_message_expires=0,
                detail_mode=True,
                _last_filter_value="ALL",
            )

        def _t(self, key, **kwargs):
            tpl = translations.get(key, key)
            try:
                return tpl.format(**kwargs)
            except Exception:
                return tpl

        def _sync_indicator_fragments(self, flash=False):
            return []

        def _spinner_frame(self):
            return None

        def get_terminal_width(self):
            return 80

        def exit_detail_view(self):
            self.exited = True

        def open_settings_dialog(self):
            self.opened = True

    tui = Dummy()
    res = build_status_text(tui)
    assert tui._filter_flash_until > time.time()
    assert res[0][1] == "[BACK] "
    tui.exit_detail_view()
    tui.open_settings_dialog()
