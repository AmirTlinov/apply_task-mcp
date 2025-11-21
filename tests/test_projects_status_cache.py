import time
from types import SimpleNamespace

import tasks


class DummySync:
    def __init__(self):
        self.ensure_calls = 0
        self.config = SimpleNamespace(
            owner="o",
            repo="r",
            number=1,
            project_type="repo",
            workers=1,
            enabled=True,
        )
        self.project_id = "pid"
        self.last_pull = None
        self.last_push = None
        self.detect_error = None
        self.runtime_disabled_reason = None
        self.enabled = True
        self.token_present = True

    def ensure_metadata(self):
        self.ensure_calls += 1

    def project_url(self):
        return "https://example.com/project"

    def rate_info(self):
        return {"remaining": 10, "reset_epoch": None, "wait": None}


def test_projects_status_payload_cached(monkeypatch):
    sync = DummySync()
    monkeypatch.setattr(tasks, "_get_sync_service", lambda: sync)
    tasks._invalidate_projects_status_cache()

    first = tasks._projects_status_payload()
    second = tasks._projects_status_payload()

    assert sync.ensure_calls == 1
    assert second == first


def test_projects_status_payload_cache_expires(monkeypatch):
    sync = DummySync()
    monkeypatch.setattr(tasks, "_get_sync_service", lambda: sync)
    tasks._invalidate_projects_status_cache()

    tasks._projects_status_payload()
    tasks._PROJECT_STATUS_CACHE_TS = time.time() - (tasks._PROJECT_STATUS_TTL + 0.1)
    tasks._projects_status_payload()

    assert sync.ensure_calls == 2


def test_projects_status_payload_force_refresh(monkeypatch):
    sync = DummySync()
    monkeypatch.setattr(tasks, "_get_sync_service", lambda: sync)
    tasks._invalidate_projects_status_cache()

    tasks._projects_status_payload()
    tasks._projects_status_payload(force_refresh=True)

    assert sync.ensure_calls == 2
