from types import SimpleNamespace

from prompt_toolkit.mouse_events import MouseEvent, MouseEventType, MouseButton

from tasks import TaskTrackerTUI


def test_sync_indicator_shows_failure_and_tooltip(tmp_path, monkeypatch):
    tui = TaskTrackerTUI(tasks_dir=tmp_path / ".tasks")
    dummy_sync = SimpleNamespace(
        enabled=False,
        config=SimpleNamespace(project_type="repository", owner="o", repo="r", number=1),
    )
    tui.manager.sync_service = dummy_sync

    def fake_snapshot():
        return {
            "config_enabled": False,
            "status_reason": "remote.origin.url не настроен",
            "target_hint": "n/a",
            "target_label": "—",
            "token_saved": False,
            "token_preview": "",
            "token_env": "",
            "token_active": False,
            "config_exists": False,
            "runtime_enabled": False,
            "last_pull": None,
            "last_push": None,
        }

    monkeypatch.setattr(tui, "_project_config_snapshot", fake_snapshot)
    fragments = tui._sync_indicator_fragments()
    style, text, handler = fragments[0]

    assert "status.fail" in style
    assert text.endswith("!")
    assert callable(handler)

    # hover triggers tooltip update (should not raise)
    event = MouseEvent(position=None, event_type=MouseEventType.MOUSE_MOVE, button=MouseButton.LEFT, modifiers=())
    handler(event)
