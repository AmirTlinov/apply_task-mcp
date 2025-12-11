from types import SimpleNamespace

from core.desktop.devtools.interface.tui_settings_panel import render_settings_panel


def test_settings_panel_renders_options():
    """Test that settings panel renders option labels and values (hints are now in footer)."""
    class Dummy(SimpleNamespace):
        def __init__(self):
            super().__init__(
                settings_selected_index=0,
                settings_view_offset=0,
                footer_height=2,
            )

        def _t(self, key, **kwargs):
            return key

        def _settings_options(self):
            return [
                {"label": "Opt1", "value": "V", "hint": "Hint goes to footer"},
                {"label": "Opt2", "value": "Very long value that will be trimmed"},
            ]

        def get_terminal_width(self):
            return 80

        def get_terminal_height(self):
            return 30

    tui = Dummy()
    text = "".join(part[1] for part in render_settings_panel(tui))
    # Hints moved to footer, so panel should only show labels and values
    assert "Opt1" in text
    assert "Opt2" in text


def test_settings_panel_empty_options():
    class Dummy(SimpleNamespace):
        def __init__(self):
            super().__init__(settings_selected_index=0, settings_view_offset=0, footer_height=2)

        def _t(self, key, **kwargs):
            return key

        def _settings_options(self):
            return []

        def get_terminal_width(self):
            return 80

        def get_terminal_height(self):
            return 20

    tui = Dummy()
    text = "".join(part[1] for part in render_settings_panel(tui))
    assert "SETTINGS_UNAVAILABLE" in text


def test_settings_panel_scroll_hint_and_offsets():
    class Dummy(SimpleNamespace):
        def __init__(self):
            super().__init__(settings_selected_index=4, settings_view_offset=0, footer_height=2)

        def _t(self, key, **kwargs):
            return key

        def _settings_options(self):
            return [{"label": f"Opt{i}", "value": "v"} for i in range(5)]

        def get_terminal_width(self):
            return 80

        def get_terminal_height(self):
            return 15

    tui = Dummy()
    text = "".join(part[1] for part in render_settings_panel(tui))
    assert "SETTINGS_SCROLL_HINT" in text
    assert "Opt4" in text


def test_settings_panel_adjusts_offset_when_selected_before_view():
    class Dummy(SimpleNamespace):
        def __init__(self):
            super().__init__(settings_selected_index=0, settings_view_offset=2, footer_height=2)

        def _t(self, key, **kwargs):
            return key

        def _settings_options(self):
            return [{"label": "A", "value": "1"}, {"label": "B", "value": "2"}, {"label": "C", "value": "3"}]

        def get_terminal_width(self):
            return 80

        def get_terminal_height(self):
            return 20

    tui = Dummy()
    render_settings_panel(tui)
    assert tui.settings_view_offset == 0


def test_settings_panel_truncates_value_on_small_width():
    class Dummy(SimpleNamespace):
        def __init__(self):
            super().__init__(settings_selected_index=0, settings_view_offset=0, footer_height=2)

        def _t(self, key, **kwargs):
            return key

        def _settings_options(self):
            return [{"label": "Opt", "value": "v" * 80}]

        def get_terminal_width(self):
            return 50

        def get_terminal_height(self):
            return 20

    tui = Dummy()
    text = "".join(part[1] for part in render_settings_panel(tui))
    assert "…" in text


def test_settings_panel_resets_view_offset_when_too_high():
    class Dummy(SimpleNamespace):
        def __init__(self):
            super().__init__(settings_selected_index=1, settings_view_offset=5, footer_height=2)

        def _t(self, key, **kwargs):
            return key

        def _settings_options(self):
            return [{"label": f"Opt{i}", "value": "v"} for i in range(10)]

        def get_terminal_width(self):
            return 80

        def get_terminal_height(self):
            return 12  # small to force offset logic

    tui = Dummy()
    render_settings_panel(tui)
    assert tui.settings_view_offset == tui.settings_selected_index
