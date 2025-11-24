import pytest
from core import SubTask, TaskDetail
from core.desktop.devtools.interface import edit_handlers


class DummyManager:
    def __init__(self):
        self.saved = False

    def save_task(self, task):
        self.saved = True


class DummyTui:
    def __init__(self):
        self.edit_context = ""
        self.settings_mode = False
        self.messages = []
        self.canceled = False
        self.rendered = False
        self.project_number = None
        self.bootstrap_target = None
        self.task_details_cache = {}
        self.manager = DummyManager()
        self.current_task_detail = None
        self.detail_flat_subtasks = []
        self.detail_selected_index = 0
        self.detail_selected_path = ""
        self.loaded = False

    def _t(self, key, **kwargs):
        return key

    def set_status_message(self, msg, ttl=None):
        self.messages.append(msg)

    def cancel_edit(self):
        self.canceled = True

    def force_render(self):
        self.rendered = True

    def _set_project_number(self, value: int):
        self.project_number = value

    def _bootstrap_git(self, target: str):
        self.bootstrap_target = target

    def _get_subtask_by_path(self, path: str):
        for p, st, *_ in self.detail_flat_subtasks:
            if p == path:
                return st
        return None

    def load_tasks(self, preserve_selection=False, selected_task_file=None, skip_sync=False):
        self.loaded = True


def make_detail_with_subtask():
    st = SubTask(False, "old", ["a"], ["b"], ["c"])
    detail = TaskDetail(id="T-1", title="Main", status="FAIL")
    detail.subtasks = [st]
    return detail, st


def test_handle_token_sets_message_and_rerenders():
    tui = DummyTui()
    tui.edit_context = "token"
    tui.settings_mode = True
    handled = edit_handlers.handle_token(tui, "abc")
    assert handled
    assert "STATUS_MESSAGE_PAT_SAVED" in tui.messages[-1]
    assert tui.canceled and tui.rendered


def test_handle_project_number_valid_updates_state():
    tui = DummyTui()
    tui.edit_context = "project_number"
    tui.settings_mode = True
    handled = edit_handlers.handle_project_number(tui, "12")
    assert handled
    assert tui.project_number == 12
    assert "STATUS_MESSAGE_PROJECT_NUMBER_UPDATED" in tui.messages[-1]
    assert tui.canceled and tui.rendered


def test_handle_project_number_invalid_shows_error():
    tui = DummyTui()
    tui.edit_context = "project_number"
    handled = edit_handlers.handle_project_number(tui, "abc")
    assert handled
    assert "STATUS_MESSAGE_PROJECT_NUMBER_REQUIRED" in tui.messages[-1]
    assert tui.canceled


def test_handle_project_workers_invalid():
    tui = DummyTui()
    tui.edit_context = "project_workers"
    handled = edit_handlers.handle_project_workers(tui, "-1")
    assert handled
    assert "STATUS_MESSAGE_POOL_INTEGER" in tui.messages[-1]
    assert tui.canceled


def test_handle_project_workers_valid(monkeypatch):
    tui = DummyTui()
    tui.edit_context = "project_workers"
    called = {}

    monkeypatch.setattr(edit_handlers, "update_project_workers", lambda val: called.setdefault("workers", val))
    monkeypatch.setattr(edit_handlers, "reload_projects_sync", lambda: called.setdefault("reloaded", True))

    handled = edit_handlers.handle_project_workers(tui, "3")
    assert handled
    assert called["workers"] == 3
    assert called["reloaded"] is True
    assert "STATUS_MESSAGE_POOL_UPDATED" in tui.messages[-1]
    assert tui.canceled


def test_handle_bootstrap_remote_runs_and_cancels():
    tui = DummyTui()
    tui.edit_context = "bootstrap_remote"
    handled = edit_handlers.handle_bootstrap_remote(tui, "git@x")
    assert handled
    assert tui.bootstrap_target == "git@x"
    assert tui.canceled


def test_handle_task_edit_updates_subtask_and_saves():
    tui = DummyTui()
    detail, st = make_detail_with_subtask()
    tui.current_task_detail = detail
    tui.detail_flat_subtasks = [("0", st, 0, False, False)]
    tui.detail_selected_index = 0
    handled = edit_handlers.handle_task_edit(tui, "subtask_title", "new title", 0)
    assert handled
    assert st.title == "new title"
    assert tui.manager.saved
    assert tui.loaded
    assert tui.canceled


def test_handle_task_edit_updates_criterion_with_selected_path():
    tui = DummyTui()
    detail, st = make_detail_with_subtask()
    tui.current_task_detail = detail
    tui.detail_flat_subtasks = [("0", st, 0, False, False)]
    tui.detail_selected_index = 0
    tui.detail_selected_path = "0"
    handled = edit_handlers.handle_task_edit(tui, "criterion", "c-upd", 0)
    assert handled
    assert st.success_criteria[0] == "c-upd"
    assert tui.manager.saved and tui.loaded and tui.canceled


def test_handle_task_edit_returns_false_without_task():
    tui = DummyTui()
    handled = edit_handlers.handle_task_edit(tui, "task_title", "x", 0)
    assert handled is False


def test_handle_context_mismatch_returns_false():
    tui = DummyTui()
    tui.edit_context = "other"
    assert edit_handlers.handle_token(tui, "x") is False
    tui.edit_context = "other"
    assert edit_handlers.handle_project_workers(tui, "1") is False


def test_handle_task_edit_missing_subtask_safely_exits():
    tui = DummyTui()
    detail, st = make_detail_with_subtask()
    tui.current_task_detail = detail
    tui.detail_flat_subtasks = []  # no path available
    tui.detail_selected_index = 0
    tui.detail_selected_path = ""
    handled = edit_handlers.handle_task_edit(tui, "criterion", "c1", 0)
    assert handled is False


def test_handle_project_number_zero_hits_validation():
    tui = DummyTui()
    tui.edit_context = "project_number"
    handled = edit_handlers.handle_project_number(tui, "0")
    assert handled
    assert "STATUS_MESSAGE_PROJECT_NUMBER_REQUIRED" in tui.messages[-1]


def test_handle_project_number_wrong_context():
    tui = DummyTui()
    tui.edit_context = "other"
    assert edit_handlers.handle_project_number(tui, "5") is False


def test_project_workers_force_render_on_settings(monkeypatch):
    tui = DummyTui()
    tui.edit_context = "project_workers"
    tui.settings_mode = True
    called = {}
    monkeypatch.setattr(edit_handlers, "update_project_workers", lambda val: called.setdefault("workers", val))
    monkeypatch.setattr(edit_handlers, "reload_projects_sync", lambda: called.setdefault("reloaded", True))
    handled = edit_handlers.handle_project_workers(tui, "2")
    assert handled and tui.rendered


def test_handle_task_edit_task_title_updates_cache():
    tui = DummyTui()
    detail, _ = make_detail_with_subtask()
    tui.current_task_detail = detail
    tui.task_details_cache[detail.id] = detail
    handled = edit_handlers.handle_task_edit(tui, "task_title", "New main", None)
    assert handled
    assert tui.task_details_cache[detail.id].title == "New main"


def test_path_helpers_use_selected_path_first():
    tui = DummyTui()
    detail, st = make_detail_with_subtask()
    tui.current_task_detail = detail
    tui.detail_flat_subtasks = [("0", st, 0, False, False)]
    tui.detail_selected_index = 0
    tui.detail_selected_path = "0"
    handled = edit_handlers.handle_task_edit(tui, "subtask_title", "keep", 0)
    assert handled
