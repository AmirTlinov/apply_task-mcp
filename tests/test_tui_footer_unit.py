from types import SimpleNamespace

from core.desktop.devtools.interface.tui_footer import build_footer_text


class DummyTUI(SimpleNamespace):
    def _t(self, key, **kwargs):
        return key

    def _current_description_snippet(self):
        return "Desc"

    def _current_task_detail_obj(self):
        return SimpleNamespace(
            domain="dom/phase",
            phase="phase",
            component="comp",
            created="2024-01-01",
            updated="2024-01-02",
            status="OK",
        )

    def _task_duration_value(self, detail):
        return "1d"

    def get_terminal_width(self):
        return 80


def test_build_footer_text_basic():
    tui = DummyTUI(horizontal_offset=0)
    result = build_footer_text(tui)
    text = "".join(fragment for _, fragment in result)
    assert "DOMAIN" in text
    assert "Duration" in text
    assert "Legend" in text


def test_build_footer_text_with_offset():
    tui = DummyTUI(horizontal_offset=5)
    result = build_footer_text(tui)
    text = "".join(fragment for _, fragment in result)
    assert "OFFSET_LABEL" in text or "Legend" in text
