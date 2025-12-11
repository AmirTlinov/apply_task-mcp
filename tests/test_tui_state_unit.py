from types import SimpleNamespace

from core.desktop.devtools.interface import tui_state


def test_toggle_collapse_selected_switches(monkeypatch):
    rendered = {}

    class TUI(SimpleNamespace):
        def __init__(self):
            super().__init__(
                detail_mode=False,
                filtered_tasks=[SimpleNamespace(id="A")],
                selected=0,
                collapsed_tasks=set(),
            )

        def render(self, force=False):
            rendered["called"] = force

    t = TUI()
    tui_state.toggle_collapse_selected(t)
    assert "A" in t.collapsed_tasks and rendered["called"] is True


def test_toggle_collapse_selected_ignored_when_detail_or_empty():
    class T(SimpleNamespace):
        def __init__(self, detail_mode, tasks):
            super().__init__(detail_mode=detail_mode, filtered_tasks=tasks, collapsed_tasks=set(), selected=0)

        def render(self, force=False):
            raise AssertionError("should not render")

    tui_state.toggle_collapse_selected(T(True, [1]))
    tui_state.toggle_collapse_selected(T(False, []))


def test_toggle_subtask_collapse_expand_and_collapse():
    rebuilt = {}

    class TUI(SimpleNamespace):
        def __init__(self):
            super().__init__(
                detail_mode=False,
                detail_flat_subtasks=[("0", SimpleNamespace(children=[1]), None, True, True)],
                detail_collapsed={"0"},
            )

        def _selected_subtask_entry(self):
            return self.detail_flat_subtasks[0]

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
    assert "rebuild" in rebuilt
    tui_state.toggle_subtask_collapse(t, expand=False)
    assert rebuilt["rebuild"]


def test_maybe_reload_updates_signature():
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

        def _t(self, key, **kwargs):
            return key

        def load_tasks(self, **kwargs):
            loaded["called"] = True

        def set_status_message(self, *_, **__):
            loaded["status"] = True

        def show_task_details(self, *_, **__):
            loaded["show"] = True

        def _select_subtask_by_path(self, *_, **__):
            pass

        def get_detail_items_count(self):
            return 0

        def _ensure_detail_selection_visible(self, *_, **__):
            pass

        def _get_subtask_by_path(self, *_, **__):
            return None

        def show_subtask_details(self, *_, **__):
            pass

    tui = TUI()
    tui_state.maybe_reload(tui, now=10.0)
    assert loaded.get("called") is True
    assert tui._last_signature == 1


def test_maybe_reload_skips_when_recent():
    class T(SimpleNamespace):
        def __init__(self):
            super().__init__(_last_check=5.5, _last_signature=0, tasks=[], selected_index=0)

        def compute_signature(self):
            return 0

    tui_state.maybe_reload(T(), now=6.0)


def test_maybe_reload_no_change_when_same_signature():
    called = {}

    class T(SimpleNamespace):
        def __init__(self):
            super().__init__(_last_check=0, _last_signature=1, tasks=[], selected_index=0)

        def compute_signature(self):
            return 1

        def load_tasks(self, **kwargs):
            called["load"] = True

        def _t(self, key, **kwargs):
            return key

    tui_state.maybe_reload(T(), now=10.0)
    assert "load" not in called


def test_maybe_reload_no_prev_detail_paths():
    class T(SimpleNamespace):
        def __init__(self):
            super().__init__(
                _last_check=0,
                _last_signature=0,
                tasks=[SimpleNamespace(id="X", task_file="f")],
                selected_index=0,
                detail_mode=False,
                current_task_detail=None,
                detail_selected_path="",
                            )

        def compute_signature(self):
            return 1

        def load_tasks(self, **kwargs):
            pass

        def set_status_message(self, *_, **__):
            self.set_msg = True

        def _t(self, key, **kwargs):
            return key

    tui = T()
    tui_state.maybe_reload(tui, now=1.0)
    assert getattr(tui, "set_msg", False) is True



