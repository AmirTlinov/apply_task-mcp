import time
from types import SimpleNamespace

from core.desktop.devtools.application import projects_status_cache as cache


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
    monkeypatch.setattr(cache, "CACHE_TTL", 1.0)
    cache.invalidate_cache()

    first = cache.projects_status_payload(lambda: sync)
    second = cache.projects_status_payload(lambda: sync)

    assert sync.ensure_calls == 1
    assert second == first


def test_projects_status_payload_cache_expires(monkeypatch):
    sync = DummySync()
    cache.invalidate_cache()

    cache.projects_status_payload(lambda: sync)
    cache.CACHE_TS = time.time() - (cache.CACHE_TTL + 0.1)
    cache.projects_status_payload(lambda: sync)

    assert sync.ensure_calls == 2


def test_projects_status_payload_force_refresh(monkeypatch):
    sync = DummySync()
    cache.invalidate_cache()

    cache.projects_status_payload(lambda: sync)
    cache.projects_status_payload(lambda: sync, force_refresh=True)

    assert sync.ensure_calls == 2
