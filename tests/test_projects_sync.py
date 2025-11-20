from pathlib import Path
import sys

import pytest
import yaml
import os

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import projects_sync
import tasks


class DummyResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = str(self._payload)
        self.headers = {}

    def json(self):
        return self._payload


class DummySession:
    def __init__(self, post_payload=None):
        self.post_calls = []
        self.patch_calls = []
        self._post_payload = post_payload or {"number": 42}

    def post(self, url, json=None, headers=None, timeout=None):
        self.post_calls.append((url, json, headers))
        return DummyResponse(201, self._post_payload)

    def patch(self, url, json=None, headers=None, timeout=None):
        self.patch_calls.append((url, json, headers))
        return DummyResponse(200, {})


def _sync_with_repo(tmp_path, monkeypatch=None):
    if monkeypatch:
        monkeypatch.setattr(projects_sync, "detect_repo_slug", lambda: ("octo", "demo"))
    cfg = projects_sync.ProjectConfig(project_type="repository", owner="octo", repo="demo", number=1)
    sync = projects_sync.ProjectsSync(config_path=tmp_path / "cfg.yaml")
    sync.config = cfg
    sync.token = "tok"
    return sync


def _write_project_cfg(path, project_type="user"):
    data = {
        "project": {"type": project_type, "owner": "octo", "repo": "demo", "number": 1, "enabled": True},
        "fields": {
            "status": {"name": "Status", "options": {"OK": "Done"}},
            "progress": {"name": "Progress"},
            "domain": {"name": "Domain"},
        },
    }
    if project_type != "repository":
        data["project"].pop("repo", None)
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def test_repo_issue_created(tmp_path, monkeypatch):
    sync = _sync_with_repo(tmp_path, monkeypatch)
    dummy = DummySession(post_payload={"number": 77})
    sync.session = dummy
    task = tasks.TaskDetail(id="TASK-001", title="Test", status="FAIL")

    created = sync._ensure_repo_issue(task, "body")

    assert created is True
    assert task.project_issue_number == 77
    assert dummy.post_calls


def test_repo_issue_updated(tmp_path, monkeypatch):
    sync = _sync_with_repo(tmp_path, monkeypatch)
    dummy = DummySession()
    sync.session = dummy
    task = tasks.TaskDetail(id="TASK-002", title="Done", status="OK", project_issue_number=10)

    created = sync._ensure_repo_issue(task, "body")

    assert created is False  # update doesn't change metadata
    assert dummy.patch_calls
    url, payload, headers = dummy.patch_calls[0]
    assert payload["state"] == "closed"


def test_permission_error_disables_sync(monkeypatch, tmp_path):
    cfg_path = tmp_path / "projects.yaml"
    _write_project_cfg(cfg_path, project_type="user")
    monkeypatch.setattr(projects_sync, "detect_repo_slug", lambda: ("octo", "demo"))
    monkeypatch.setenv("APPLY_TASK_GITHUB_TOKEN", "tok")
    sync = projects_sync.ProjectsSync(config_path=cfg_path)
    sync.project_id = "P"
    sync.project_fields = {"status": {"reverse": {}}, "progress": {}, "domain": {}}
    sync.token = "tok"

    class PermissionResponse:
        status_code = 200
        headers = {}

        def json(self):
            return {"errors": [{"message": "Resource not accessible by integration"}]}

    sync.session.post = lambda *a, **k: PermissionResponse()
    monkeypatch.setattr(sync, "_persist_metadata", lambda *a, **k: True)
    task = tasks.TaskDetail(id="TASK-123", title="Demo", status="FAIL")

    assert sync.sync_task(task) is False
    assert not sync.enabled
    assert "resource not accessible" in (sync.runtime_disabled_reason or "").lower()


def test_fetch_remote_state_parses_text_and_numbers(monkeypatch, tmp_path):
    cfg_path = tmp_path / "projects.yaml"
    _write_project_cfg(cfg_path, project_type="user")
    monkeypatch.setattr(projects_sync, "detect_repo_slug", lambda: ("octo", "demo"))
    monkeypatch.setenv("APPLY_TASK_GITHUB_TOKEN", "tok")
    sync = projects_sync.ProjectsSync(config_path=cfg_path)
    sync.project_fields = {
        "status": {"reverse": {"opt-ok": "OK"}},
        "progress": {},
        "domain": {},
    }

    def fake_graphql(self, query, variables):
        assert "$statusName:String!" in query
        assert variables["statusName"] == "Status"
        assert variables["progressName"] == "Progress"
        assert variables["domainName"] == "Domain"
        return {
            "node": {
                "status": {"optionId": "opt-ok"},
                "progress": {"number": 42},
                "domain": {"text": "desktop/devtools"},
                "updatedAt": "2025-01-01T00:00:00Z",
            }
        }

    sync._graphql = fake_graphql.__get__(sync, projects_sync.ProjectsSync)
    data = sync._fetch_remote_state("ITEM-1")
    assert data["status"] == "OK"
    assert data["progress"] == 42
    assert data["domain"] == "desktop/devtools"
    assert data["remote_updated"] == "2025-01-01T00:00:00Z"


def test_auto_switches_to_user_projects(monkeypatch, tmp_path):
    cfg_path = tmp_path / ".apply_task_projects.yaml"
    _write_project_cfg(cfg_path, project_type="repository")
    cfg_text = yaml.safe_dump({"project": {"enabled": True}}, allow_unicode=True)
    cfg_path.write_text(cfg_text, encoding="utf-8")

    monkeypatch.setattr(projects_sync, "CONFIG_PATH", cfg_path)
    projects_sync._PROJECTS_SYNC = None
    monkeypatch.setattr(projects_sync, "detect_repo_slug", lambda: ("octo", "demo"))

    def fake_graphql(self, query, variables):
        if "repository(" in query and "projectsV2(first:20)" in query:
            return {"repository": {"projectsV2": {"nodes": []}}}
        if "user(" in query and "projectsV2(first:20)" in query:
            return {"user": {"projectsV2": {"nodes": [{"number": 14}]}}}
        if "projectV2" in query:
            return {"user": {"projectV2": {"id": "ID", "fields": {"nodes": []}}}}
        return {}

    monkeypatch.setattr(projects_sync.ProjectsSync, "_graphql", fake_graphql, raising=False)
    sync = projects_sync.ProjectsSync(config_path=cfg_path)
    assert sync.config.project_type == "user"
    assert sync.config.number == 14


def test_permission_signatures_detected():
    errors = [{"message": "Could not resolve to a ProjectV2 with the number 1."}]
    assert projects_sync.ProjectsSync._looks_like_permission_error(errors)
    errors = [{"message": "Apps are not permitted to access this resource"}]
    assert projects_sync.ProjectsSync._looks_like_permission_error(errors)


def test_graphql_schema_cache(monkeypatch, tmp_path):
    cfg_path = tmp_path / ".apply_task_projects.yaml"
    _write_project_cfg(cfg_path, project_type="repository")
    monkeypatch.setattr(projects_sync, "CONFIG_PATH", cfg_path)
    cache_path = tmp_path / ".tasks" / ".projects_schema_cache.yaml"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.exists():
        cache_path.unlink()
    monkeypatch.setattr(projects_sync, "SCHEMA_CACHE_PATH", cache_path)
    projects_sync._PROJECTS_SYNC = None
    projects_sync._SCHEMA_CACHE.clear()
    monkeypatch.setattr(projects_sync, "detect_repo_slug", lambda: ("octo", "demo"))

    calls = {"count": 0}

    def fake_graphql(self, query, variables):
        calls["count"] += 1
        return {"repository": {"projectV2": {"id": "PID", "fields": {"nodes": []}}}}

    monkeypatch.setattr(projects_sync.ProjectsSync, "_graphql", fake_graphql, raising=False)
    sync1 = projects_sync.ProjectsSync(config_path=cfg_path)
    sync1._ensure_project_metadata()
    sync2 = projects_sync.ProjectsSync(config_path=cfg_path)
    sync2._ensure_project_metadata()
    assert calls["count"] == 1


def test_schema_cache_persisted(monkeypatch, tmp_path):
    cfg_path = tmp_path / ".apply_task_projects.yaml"
    _write_project_cfg(cfg_path, project_type="repository")
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    cache_path = tasks_dir / ".projects_schema_cache.yaml"
    monkeypatch.setattr(projects_sync, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(projects_sync, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(projects_sync, "SCHEMA_CACHE_PATH", cache_path)
    projects_sync._SCHEMA_CACHE.clear()
    monkeypatch.setattr(projects_sync, "detect_repo_slug", lambda: ("octo", "demo"))

    calls = {"count": 0}

    def fake_graphql(self, query, variables):
        calls["count"] += 1
        return {"repository": {"projectV2": {"id": "PID", "fields": {"nodes": []}}}}

    monkeypatch.setattr(projects_sync.ProjectsSync, "_graphql", fake_graphql, raising=False)
    sync1 = projects_sync.ProjectsSync(config_path=cfg_path)
    sync1._ensure_project_metadata()
    assert cache_path.exists()
    projects_sync._SCHEMA_CACHE.clear()
    sync2 = projects_sync.ProjectsSync(config_path=cfg_path)
    sync2._ensure_project_metadata()
    assert calls["count"] == 1
    projects_sync._SCHEMA_CACHE.clear()
