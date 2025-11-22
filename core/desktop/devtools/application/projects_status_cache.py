import os
import time
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Callable, Dict, Optional

from config import get_user_token

CACHE: Optional[Dict[str, Any]] = None
CACHE_TS: float = 0.0
CACHE_TTL: float = 1.0
CACHE_LOCK = Lock()
CACHE_TOKEN_PREVIEW: Optional[str] = None


def invalidate_cache() -> None:
    global CACHE, CACHE_TS, CACHE_TOKEN_PREVIEW
    with CACHE_LOCK:
        CACHE = None
        CACHE_TS = 0.0
        CACHE_TOKEN_PREVIEW = None


def projects_status_payload(sync_service_factory: Callable[[], Any], force_refresh: bool = False) -> Dict[str, Any]:
    global CACHE, CACHE_TS, CACHE_TOKEN_PREVIEW
    current_token = get_user_token()
    current_token_preview = current_token[-4:] if current_token else ""
    now = time.time()
    with CACHE_LOCK:
        if (
            not force_refresh
            and CACHE is not None
            and CACHE_TOKEN_PREVIEW == current_token_preview
            and now - CACHE_TS < CACHE_TTL
        ):
            return dict(CACHE)

    try:
        sync_service = sync_service_factory()
    except Exception as exc:
        payload = _payload_on_error(str(exc), current_token, current_token_preview)
        _store_cache(payload, current_token_preview)
        return payload

    try:
        sync_service.ensure_metadata()
    except Exception:
        pass
    cfg = getattr(sync_service, "config", None)
    owner = (cfg.owner if cfg and cfg.owner else "") if cfg else ""
    repo = (cfg.repo if cfg and cfg.repo else "") if cfg else ""
    number = cfg.number if cfg else None
    project_id = getattr(sync_service, "project_id", None)
    project_url = sync_service.project_url()
    workers = cfg.workers if cfg else None
    token_saved = bool(current_token)
    token_preview = current_token_preview
    env_primary = os.getenv("APPLY_TASK_GITHUB_TOKEN")
    env_secondary = os.getenv("GITHUB_TOKEN") if not env_primary else None
    token_env = "APPLY_TASK_GITHUB_TOKEN" if env_primary else ("GITHUB_TOKEN" if env_secondary else "")
    token_present = getattr(sync_service, "token_present", None)
    if token_present is None:
        token_present = bool(getattr(sync_service, "token", None))
    auto_sync = bool(cfg and cfg.enabled)
    target_label = (
        f"{owner}#{number}" if (cfg and cfg.project_type == "user") else f"{owner}/{repo}#{number}"
        if owner and repo and number
        else "—"
    )
    detect_error = getattr(sync_service, "detect_error", None)
    runtime_reason = getattr(sync_service, "runtime_disabled_reason", None)
    status_reason = detect_error or runtime_reason
    if not status_reason:
        if not cfg:
            status_reason = "нет конфигурации"
        elif not auto_sync:
            status_reason = "auto-sync выключена"
        elif not token_present:
            status_reason = "нет PAT"
    rate = sync_service.rate_info() or {}
    payload = {
        "owner": owner,
        "repo": repo,
        "project_number": number,
        "project_id": project_id,
        "project_url": project_url,
        "workers": workers,
        "rate_remaining": rate.get("remaining"),
        "rate_reset": rate.get("reset_epoch"),
        "rate_reset_human": datetime.fromtimestamp(rate["reset_epoch"], tz=timezone.utc).strftime("%H:%M:%S") if rate.get("reset_epoch") else None,
        "rate_wait": rate.get("wait"),
        "target_label": target_label,
        "target_hint": "Определяется автоматически из git remote origin",
        "auto_sync": auto_sync,
        "runtime_enabled": getattr(sync_service, "enabled", False),
        "runtime_reason": runtime_reason,
        "detect_error": detect_error,
        "status_reason": status_reason or "",
        "last_pull": getattr(sync_service, "last_pull", None),
        "last_push": getattr(sync_service, "last_push", None),
        "token_saved": token_saved,
        "token_preview": token_preview,
        "token_env": token_env,
        "token_present": token_present,
        "runtime_disabled_reason": runtime_reason,
    }
    _store_cache(payload, token_preview)
    return dict(payload)


def _payload_on_error(reason: str, token: Optional[str], token_preview: str) -> Dict[str, Any]:
    return {
        "owner": "",
        "repo": "",
        "project_number": None,
        "project_id": None,
        "project_url": None,
        "workers": None,
        "rate_remaining": None,
        "rate_reset": None,
        "rate_reset_human": None,
        "rate_wait": None,
        "target_label": "—",
        "target_hint": "Git Projects недоступен: " + reason,
        "auto_sync": False,
        "runtime_enabled": False,
        "runtime_reason": reason,
        "detect_error": reason,
        "status_reason": "Git Projects недоступен",
        "last_pull": None,
        "last_push": None,
        "token_saved": bool(token),
        "token_preview": token_preview,
        "token_env": "",
        "token_present": False,
        "runtime_disabled_reason": reason,
    }


def _store_cache(payload: Dict[str, Any], token_preview: str) -> None:
    global CACHE, CACHE_TS, CACHE_TOKEN_PREVIEW
    with CACHE_LOCK:
        CACHE = payload
        CACHE_TS = time.time()
        CACHE_TOKEN_PREVIEW = token_preview
