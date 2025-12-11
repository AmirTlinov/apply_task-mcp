from types import SimpleNamespace

from prompt_toolkit.mouse_events import MouseEventType

from core.desktop.devtools.interface.tui_sync_indicator import build_sync_indicator


def test_sync_indicator_empty_without_service():
    tui = SimpleNamespace(manager=SimpleNamespace())
    assert build_sync_indicator(tui) == []


def test_sync_indicator_with_issue_and_tooltip(monkeypatch):
    class Sync:
        enabled = True
        config = SimpleNamespace(enabled=True, owner="o", repo="r", number=1, project_type="repository")

    snapshot = {
        "auto_sync": True,
        "status_reason": "error",
        "last_pull": None,
        "last_push": None,
    }

    calls = {}

    tui = SimpleNamespace(manager=SimpleNamespace(sync_service=Sync()))
    tui.set_status_message = lambda msg, ttl=0: calls.setdefault("msg", msg)
    tui._project_config_snapshot = lambda: snapshot

    frags = build_sync_indicator(tui)
    # first fragment has handler with tooltip
    handler = frags[0][2]
    handler(SimpleNamespace(event_type=MouseEventType.MOUSE_MOVE))
    assert calls["msg"] == "error"


def test_sync_indicator_pull_push_label(monkeypatch):
    class Sync:
        enabled = True
        config = SimpleNamespace(enabled=True, owner="o", repo="r", number=1, project_type="repository")
        last_pull = "P"
        last_push = "Q"

    tui = SimpleNamespace(manager=SimpleNamespace(sync_service=Sync()))
    tui.set_status_message = lambda msg, ttl=0: None
    tui._project_config_snapshot = lambda: {
        "auto_sync": True,
        "status_reason": "",
        "last_pull": "P",
        "last_push": "Q",
    }

    frags = build_sync_indicator(tui, filter_flash=True)
    text = "".join(f[1] for f in frags)
    assert "P" in text and "Q" in text


def test_sync_indicator_fallback_snapshot_and_flash(monkeypatch):
    class Sync:
        enabled = False
        config = SimpleNamespace(enabled=True, owner="o", repo="r", number=1, project_type="repository")

    snapshot = {"auto_sync": True, "status_reason": "", "last_pull": None, "last_push": None}
    monkeypatch.setattr(
        "core.desktop.devtools.interface.tui_sync_indicator.projects_status_cache.projects_status_payload",
        lambda factory, force_refresh=False: snapshot,
    )

    tui = SimpleNamespace(manager=SimpleNamespace(sync_service=Sync()), _last_sync_enabled=True)
    res = build_sync_indicator(tui)
    assert getattr(tui, "_sync_flash_until", 0) > 0
    handler = res[0][2] if len(res[0]) > 2 else None
    if handler:
        assert handler(SimpleNamespace(event_type=MouseEventType.MOUSE_UP)) is NotImplemented


def test_sync_indicator_tooltip_notimplemented(monkeypatch):
    class Sync:
        enabled = True
        config = SimpleNamespace(enabled=True, owner="o", repo="r", number=1, project_type="repository")

    tui = SimpleNamespace(manager=SimpleNamespace(sync_service=Sync()))
    tui.set_status_message = lambda msg, ttl=0: None
    tui._project_config_snapshot = lambda: {
        "auto_sync": True,
        "status_reason": "warn",
        "last_pull": None,
        "last_push": None,
    }

    res = build_sync_indicator(tui)
    handler = res[0][2]
    assert handler(SimpleNamespace(event_type=MouseEventType.MOUSE_UP)) is NotImplemented


def test_sync_indicator_handles_snapshot_error(monkeypatch):
    class Sync:
        enabled = True
        config = SimpleNamespace(enabled=True, owner="o", repo="r", number=1, project_type="repository")

    monkeypatch.setattr(
        "core.desktop.devtools.interface.tui_sync_indicator.projects_status_cache.projects_status_payload",
        lambda factory, force_refresh=False: (_ for _ in ()).throw(RuntimeError("fail")),
    )

    tui = SimpleNamespace(manager=SimpleNamespace(sync_service=Sync()))
    tui.set_status_message = lambda msg, ttl=0: None
    assert build_sync_indicator(tui) == []
