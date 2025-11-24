from types import SimpleNamespace

from core.desktop.devtools.interface import tui_actions


def test_activate_settings_option_disabled(monkeypatch):
    messages = []

    class TUI(SimpleNamespace):
        def __init__(self):
            super().__init__(
                settings_selected_index=0,
                edit_buffer=SimpleNamespace(cursor_position=0),
            )

        def _settings_options(self):
            return [{"label": "opt", "value": "", "disabled": True, "disabled_msg": "nope"}]

        def set_status_message(self, msg, ttl=0):
            messages.append(msg)

    tui_actions.activate_settings_option(TUI())
    assert messages == ["nope"]


def test_activate_settings_option_toggle_sync(monkeypatch):
    state = {}
    monkeypatch.setattr(tui_actions, "update_projects_enabled", lambda desired: state.setdefault("desired", desired))

    class TUI(SimpleNamespace):
        def __init__(self):
            super().__init__(settings_selected_index=0, edit_buffer=SimpleNamespace(cursor_position=0))

        def _settings_options(self):
            return [{"label": "toggle", "value": "", "action": "toggle_sync"}]

        def _project_config_snapshot(self):
            return {"config_enabled": False}

        def _t(self, key, **kwargs):
            return key

        def set_status_message(self, msg, ttl=0):
            state["status"] = msg

        def force_render(self):
            state["render"] = True

    tui_actions.activate_settings_option(TUI())
    assert state["desired"] is True and state["render"]


def test_delete_current_item_list():
    calls = {}

    class Manager:
        def delete_task(self, task_id, domain):
            calls["deleted"] = (task_id, domain)

    class TUI(SimpleNamespace):
        def __init__(self):
            super().__init__(filtered_tasks=[SimpleNamespace(id="X", domain="d")], selected_index=0, manager=Manager())

        def load_tasks(self, preserve_selection=False, skip_sync=False):
            calls["loaded"] = (preserve_selection, skip_sync)

    tui_actions.delete_current_item(TUI())
    assert calls["deleted"] == ("X", "d") and calls["loaded"][0] is False


def test_delete_current_item_detail():
    class Manager:
        def __init__(self):
            self.saved = None

        def save_task(self, task):
            self.saved = task

    parent = SimpleNamespace(children=[], title="p")
    child = SimpleNamespace(children=[], title="c")
    parent.children.append(child)
    detail = SimpleNamespace(id="T1", subtasks=[parent])

    class TUI(SimpleNamespace):
        def __init__(self):
            super().__init__(
                detail_mode=True,
                current_task_detail=detail,
                detail_selected_index=0,
                detail_flat_subtasks=[("0.0", child)],
                manager=Manager(),
                task_details_cache={},
            )

        def _selected_subtask_entry(self):
            return ("0.0", child, None, None, None)

        def _rebuild_detail_flat(self):
            self.detail_flat_subtasks = []

        def load_tasks(self, preserve_selection=False, skip_sync=False):
            self.loaded = (preserve_selection, skip_sync)

    tui = TUI()
    tui_actions.delete_current_item(tui)
    assert tui.manager.saved is detail
    assert tui.loaded == (True, True)


def test_activate_settings_option_edit_paths(monkeypatch):
    edits = {}

    class TUI(SimpleNamespace):
        def __init__(self, action):
            super().__init__(
                settings_selected_index=0,
                edit_buffer=SimpleNamespace(cursor_position=0, text=""),
            )
            self._action = action

        def _settings_options(self):
            return [{"label": "opt", "value": "", "action": self._action}]

        def _project_config_snapshot(self):
            return {"number": 7, "workers": 2, "config_enabled": True}

        def _t(self, key, **kwargs):
            return key

        def set_status_message(self, msg, ttl=0):
            edits.setdefault("status", []).append(msg)

        def start_editing(self, ctx, value, idx):
            edits.setdefault("edit", []).append((ctx, value, idx))
            self.edit_buffer.text = value

        def _start_pat_validation(self):
            edits["pat"] = True

        def _cycle_language(self):
            edits["lang"] = True

        def force_render(self):
            edits["render"] = True

    for act in ["edit_pat", "edit_number", "edit_workers", "bootstrap_git", "refresh_metadata", "validate_pat", "cycle_lang"]:
        edits.clear()
        tui_actions.activate_settings_option(TUI(act))
        assert edits  # each action records something
