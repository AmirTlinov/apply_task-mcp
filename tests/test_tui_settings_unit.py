from types import SimpleNamespace

from core.desktop.devtools.interface.tui_settings import build_settings_options


class DummyTui(SimpleNamespace):
    def _t(self, key, **kwargs):
        # simple formatter for tests
        return key.lower().replace("_", "-").format(**kwargs) if kwargs else key.lower().replace("_", "-")


def make_snapshot(**overrides):
    base = {
        "runtime_enabled": True,
        "status_reason": "",
        "token_saved": False,
        "token_preview": "",
        "token_env": "",
        "token_active": False,
        "config_exists": True,
        "config_enabled": True,
        "project_url": "https://example.com",
        "target_label": "proj",
        "target_hint": "hint",
        "number": 1,
        "workers": None,
        "last_pull": "yesterday",
        "last_push": "today",
        "rate_remaining": 42,
        "rate_reset_human": "soon",
        "rate_wait": None,
        "origin_url": "git@example.com/repo.git",
    }
    base.update(overrides)
    return base


def test_build_settings_options_happy_path(monkeypatch):
    snapshot = make_snapshot(token_saved=True, token_preview="abc")
    tui = DummyTui(
        _project_config_snapshot=lambda: snapshot,
        pat_validation_result="ok",
        language="ru",
    )
    options = build_settings_options(tui)
    assert options[0]["label"]  # status row
    assert any(opt["action"] == "validate_pat" for opt in options)
    assert any(opt["action"] == "cycle_lang" for opt in options)


def test_build_settings_options_bootstrap(monkeypatch):
    snapshot = make_snapshot(config_exists=False, status_reason="нет конфигурации")
    tui = DummyTui(_project_config_snapshot=lambda: snapshot, pat_validation_result="", language="en")
    options = build_settings_options(tui)
    actions = [o.get("action") for o in options]
    assert "bootstrap_git" in actions
    # refresh disabled if config missing
    refresh = next(o for o in options if o.get("action") == "refresh_metadata")
    assert refresh["disabled"]


def test_build_settings_options_sync_state(monkeypatch):
    snapshot = make_snapshot(token_active=False, config_enabled=False)
    tui = DummyTui(_project_config_snapshot=lambda: snapshot, pat_validation_result="", language="en")
    options = build_settings_options(tui)
    sync_row = next(o for o in options if o.get("action") == "toggle_sync")
    assert sync_row["disabled"] is False
    validate_row = next(o for o in options if o.get("action") == "validate_pat")
    assert validate_row["disabled"] is True
