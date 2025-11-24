from types import SimpleNamespace

from core.desktop.devtools.interface.tui_scroll import apply_scroll_to_formatted, scroll_line_preserve_borders


def test_scroll_line_preserve_borders_offsets():
    class TUI(SimpleNamespace):
        horizontal_offset = 2

        def apply_horizontal_scroll(self, line):
            return line[self.horizontal_offset :]

    tui = TUI()
    assert scroll_line_preserve_borders(tui, "+abcd") == "+cd"
    assert scroll_line_preserve_borders(tui, "plain") == "ain"
    tui.horizontal_offset = 0
    assert scroll_line_preserve_borders(tui, "|x") == "|x"


def test_apply_scroll_to_formatted_no_offset_returns_same():
    class TUI(SimpleNamespace):
        horizontal_offset = 0
    formatted = [("class", "Hello")]
    assert apply_scroll_to_formatted(TUI(), formatted) == formatted


def test_apply_scroll_to_formatted_splits_lines():
    class TUI(SimpleNamespace):
        horizontal_offset = 1

        def apply_horizontal_scroll(self, line):
            return line[self.horizontal_offset :]

    formatted = [("class", "A\nB"), ("class", "C")]
    res = apply_scroll_to_formatted(TUI(), formatted)
    text = "".join(t for _, t in res)
    assert "|" not in text  # no borders
    assert "\n" in text
