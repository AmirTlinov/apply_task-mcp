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


def test_projects_status_payload_error_and_token(monkeypatch):
    cache.invalidate_cache()
    monkeypatch.setattr(cache, "get_user_token", lambda: "")

    payload = cache.projects_status_payload(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert payload["status_reason"] == "Git Projects недоступен"
    assert payload["token_preview"] == ""

    class SyncWithToken(DummySync):
        def __init__(self):
            super().__init__()
            self.token = "tok"
            self.token_present = None
            self.config.enabled = False

    sync = SyncWithToken()
    cache.invalidate_cache()
    payload2 = cache.projects_status_payload(lambda: sync)
    assert payload2["token_present"] is True
    assert payload2["status_reason"] == "auto-sync выключена"


def test_projects_status_payload_missing_config_and_token(monkeypatch):
    class NoConfigSync:
        def __init__(self):
            self.config = None
            self.enabled = True
            self.token_present = False

        def ensure_metadata(self):
            pass

        def rate_info(self):
            return {}

        def project_url(self):
            return None

    cache.invalidate_cache()
    payload = cache.projects_status_payload(lambda: NoConfigSync())
    assert payload["status_reason"] == "нет конфигурации"

    class NoTokenSync(NoConfigSync):
        def __init__(self):
            super().__init__()
            self.config = type("Cfg", (), {"enabled": True, "owner": "", "repo": "", "number": None, "project_type": "repo", "workers": None})
            self.token_present = False
            self.enabled = True

    cache.invalidate_cache()
    payload2 = cache.projects_status_payload(lambda: NoTokenSync())
    assert payload2["status_reason"] == "нет PAT"
