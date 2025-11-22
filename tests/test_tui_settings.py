from pathlib import Path
import sys
import time

import yaml
import pytest
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tasks
import projects_sync
import config
from config import set_user_token


@pytest.fixture
def projects_env(monkeypatch, tmp_path):
    cfg_path = tmp_path / "projects.yaml"
    user_cfg = tmp_path / "user_config.yaml"
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    monkeypatch.setattr(projects_sync, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(projects_sync, "detect_repo_slug", lambda: ("octo", "apply_task"))
    projects_sync._PROJECTS_SYNC = None
    monkeypatch.setattr(config, "USER_CONFIG_PATH", user_cfg)
    monkeypatch.delenv("APPLY_TASK_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    return cfg_path, tasks_dir


def _write_project_cfg(cfg_path, **kwargs):
    project_section = {
        "type": kwargs.get("type", "repository"),
        "owner": kwargs.get("owner", "org"),
        "repo": kwargs.get("repo", "repo"),
        "number": kwargs.get("number", 1),
        "enabled": kwargs.get("enabled", True),
    }
    cfg_path.write_text(yaml.safe_dump({"project": project_section}, allow_unicode=True), encoding="utf-8")


def _plain_text(text):
    parts = []
    for frag in text:
        if len(frag) >= 2:
            parts.append(frag[1])
    return ''.join(parts)


def test_settings_options_reflect_config_state(projects_env):
    cfg_path, tasks_dir = projects_env
    set_user_token("tok_example_1234")
    _write_project_cfg(cfg_path, owner="octo", repo="apply_task", number=7)
    projects_sync.reload_projects_sync()

    tui = tasks.TaskTrackerTUI(tasks_dir=tasks_dir, theme=tasks.DEFAULT_THEME)
    options = tui._settings_options()

    pat_entry = next(opt for opt in options if opt["action"] == "edit_pat")
    assert "…1234" in pat_entry["value"]

    sync_entry = next(opt for opt in options if opt["action"] == "toggle_sync")
    assert "Enabled" in sync_entry["value"]

    target_entry = next(opt for opt in options if opt["label"] == "GitHub Project")
    assert "octo/apply_task#7" in target_entry["value"]
    assert target_entry["hint"]


def test_save_edit_updates_number(projects_env):
    cfg_path, tasks_dir = projects_env
    _write_project_cfg(cfg_path, owner="old-owner", repo="apply_task", number=3)
    projects_sync.reload_projects_sync()

    tui = tasks.TaskTrackerTUI(tasks_dir=tasks_dir, theme=tasks.DEFAULT_THEME)
    tui.settings_mode = True
    tui.start_editing('project_number', '3', None)
    tui.edit_buffer.text = "5"
    tui.save_edit()

    saved = yaml.safe_load(cfg_path.read_text())
    assert saved["project"]["number"] == 5


def test_status_bar_shows_sync(monkeypatch, tmp_path):
    cfg_path = tmp_path / ".apply_task_projects.yaml"
    _write_project_cfg(cfg_path, owner="octo", repo="apply", number=7)
    monkeypatch.setattr(projects_sync, "CONFIG_PATH", cfg_path)
    dummy_sync = projects_sync.ProjectsSync(config_path=cfg_path)
    dummy_sync.token = "tok"
    dummy_sync.config.enabled = True

    monkeypatch.setattr(tasks, "get_projects_sync", lambda: dummy_sync)

    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    tui = tasks.TaskTrackerTUI(tasks_dir=tasks_dir, theme=tasks.DEFAULT_THEME)
    text = tui.get_status_text()
    plain = _plain_text(text)
    assert "Git Projects" in plain


def test_status_bar_spinner(monkeypatch, tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    tui = tasks.TaskTrackerTUI(tasks_dir=tasks_dir, theme=tasks.DEFAULT_THEME)
    tui.spinner_active = True
    tui.spinner_message = "Loading"
    tui.spinner_start = time.time()
    text = tui.get_status_text()
    plain = _plain_text(text)
    assert "Loading" in plain


def test_validate_pat_token(monkeypatch, tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    tui = tasks.TaskTrackerTUI(tasks_dir=tasks_dir, theme=tasks.DEFAULT_THEME)
    monkeypatch.setattr(tasks, "get_user_token", lambda: "tok")
    called = {}

    def fake_validate(token):
        called["token"] = token
        return True, "PAT valid (viewer=tester)"

    monkeypatch.setattr(tasks, "validate_pat_token_http", fake_validate)
    tui._start_pat_validation()
    for _ in range(500):
        if not tui.spinner_active:
            break
        time.sleep(0.01)
    assert called["token"] == "tok"
    assert "PAT valid" in tui.status_message
    assert "PAT valid" in tui.pat_validation_result


def test_status_shows_last_times_and_hotkey(monkeypatch, tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()

    class DummySync:
        def __init__(self):
            self.config = projects_sync.ProjectConfig(project_type="repository", owner="octo", repo="demo", number=7, enabled=True)
            self.token = "tok"
            self.project_id = "PID"
            self.last_pull = "2025-11-19 10:00"
            self.last_push = "2025-11-19 11:00"
            self.project_fields = {}
            self.runtime_disabled_reason = None
            self.detect_error = None

        @property
        def enabled(self):
            return True

        def project_url(self):
            return "https://github.com/octo/demo/projects/7"

        def _ensure_project_metadata(self):
            return None

    dummy = DummySync()
    monkeypatch.setattr(tasks, "get_projects_sync", lambda: dummy)
    monkeypatch.setattr(tasks, "get_user_token", lambda: "tok")
    opened = {}
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.setdefault("url", url))

    tui = tasks.TaskTrackerTUI(tasks_dir=tasks_dir, theme=tasks.DEFAULT_THEME)
    text = tui.get_status_text()
    plain = _plain_text(text)
    assert "pull=2025-11-19 10:00" in plain
    assert "push=2025-11-19 11:00" in plain

    tui._open_project_url()
    assert opened["url"] == "https://github.com/octo/demo/projects/7"
