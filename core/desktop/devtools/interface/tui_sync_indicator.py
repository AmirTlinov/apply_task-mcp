"""Sync indicator fragments builder extracted from TaskTrackerTUI."""

import time
from typing import List, Tuple

from prompt_toolkit.mouse_events import MouseEvent, MouseEventType

from util.sync_status import sync_status_fragments
from core.desktop.devtools.application import projects_status_cache


def build_sync_indicator(tui, filter_flash: bool = False) -> List[Tuple[str, str]]:
    sync = getattr(getattr(tui, "manager", None), "sync_service", None)
    if not sync:
        return []
    try:
        cfg = getattr(sync, "config", None)
        if hasattr(tui, "_project_config_snapshot"):
            snapshot = tui._project_config_snapshot()
        else:
            snapshot = projects_status_cache.projects_status_payload(lambda: sync, force_refresh=True)
    except Exception:
        return []
    enabled = bool(sync and getattr(sync, "enabled", False) and cfg and snapshot.get("auto_sync", snapshot.get("config_enabled", False)))
    now = time.time()
    if getattr(tui, "_last_sync_enabled", None) is None:
        tui._last_sync_enabled = enabled
    elif tui._last_sync_enabled and not enabled:
        tui._sync_flash_until = now + 1.0
    tui._last_sync_enabled = enabled

    flash = bool(getattr(tui, "_sync_flash_until", 0) and now < getattr(tui, "_sync_flash_until", 0))
    fragments = sync_status_fragments(snapshot, enabled, flash, filter_flash)

    tooltip = snapshot.get("status_reason")

    def _tooltip_handler(message: str):
        def handler(event: MouseEvent):
            if event.event_type == MouseEventType.MOUSE_MOVE:
                tui.set_status_message(message, ttl=3)
                return None
            return NotImplemented

        return handler

    enriched: List[Tuple[str, str]] = []
    for style, text, *rest in fragments:
        if tooltip:
            enriched.append((style, text, _tooltip_handler(tooltip)))
        else:
            enriched.append((style, text))

    enriched.append(("class:text.dim", " | "))
    return enriched


__all__ = ["build_sync_indicator"]
