def test_empty_state_renders_cta(monkeypatch):
    from core.desktop.devtools.interface import tui_render

    class Stub:
        pass

    tui = Stub()
    tui.tasks = []
    tui.current_filter = None
    tui.task_row_map = []
    tui._t = lambda key, **kwargs: {
        "TASK_LIST_EMPTY": "No tasks",
        "CTA_CREATE_TASK": "Press c",
        "CTA_IMPORT_TASK": "Press g",
        "CTA_DOMAIN_HINT": "Domains",
        "CTA_KEYS_HINT": "Keys",
    }.get(key, key)
    tui.get_terminal_width = lambda: 80
    tui._visible_row_limit = lambda: 10
    tui._format_cell = lambda text, width, align="left": text.ljust(width)[:width]
    tui._apply_scroll = lambda text: text
    tui._get_status_info = lambda task: ("", "", "")
    tui._selection_style_for_status = lambda status: "selected"
    tui.filtered_tasks = []

    ft = tui_render.render_task_list_text_impl(tui)
    text = "".join(fragment[1] for fragment in ft)
    assert "Press c" in text
    assert "Press g" in text
    assert "Domains" in text
    assert "Keys" in text
