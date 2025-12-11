#!/usr/bin/env python3
"""GitHub Projects integration functions."""

from typing import Dict, Any, Tuple

import requests

from infrastructure.projects_sync_service import ProjectsSyncService
from projects_sync import get_projects_sync
from core.desktop.devtools.application import projects_status_cache
from core.desktop.devtools.interface.constants import GITHUB_GRAPHQL


def _get_sync_service() -> ProjectsSyncService:
    """Factory used outside TaskManager to obtain sync adapter."""
    return ProjectsSyncService(get_projects_sync())


def validate_pat_token_http(token: str, timeout: float = 10.0) -> Tuple[bool, str]:
    """Validate GitHub PAT token via HTTP."""
    if not token:
        return False, "PAT missing"
    query = "query { viewer { login } }"
    headers = {"Authorization": f"bearer {token}", "Accept": "application/vnd.github+json"}
    try:
        resp = requests.post(GITHUB_GRAPHQL, json={"query": query}, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        return False, f"Network unavailable: {exc}"
    if resp.status_code >= 400:
        return False, f"GitHub replied {resp.status_code}: {resp.text[:120]}"
    payload = resp.json()
    if payload.get("errors"):
        err = payload["errors"][0].get("message", "Unknown error")
        return False, err
    login = ((payload.get("data") or {}).get("viewer") or {}).get("login")
    if not login:
        return False, "Response missing viewer"
    return True, f"PAT valid (viewer={login})"


def _projects_status_payload(force_refresh: bool = False) -> Dict[str, Any]:
    """Get projects status payload with caching."""
    return projects_status_cache.projects_status_payload(_get_sync_service, force_refresh=force_refresh)


def _invalidate_projects_status_cache() -> None:
    """Invalidate projects status cache."""
    projects_status_cache.invalidate_cache()
