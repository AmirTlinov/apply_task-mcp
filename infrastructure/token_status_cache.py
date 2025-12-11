"""Token status cache for deduplicating GitHub Projects warnings.

Session-level caching prevents spamming the same warning multiple times
per CLI session or across rapid consecutive calls.
"""

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Optional

# Cache location
CACHE_DIR = Path(os.environ.get("APPLY_TASK_CACHE_DIR", Path.home() / ".cache" / "apply_task"))
TOKEN_CACHE_FILE = CACHE_DIR / "token_status.json"

# TTL for warning suppression (1 hour = don't spam same warning within an hour)
WARNING_TTL_SECONDS = int(os.environ.get("APPLY_TASK_WARNING_TTL", "3600"))

# Global lock for thread-safe access
_CACHE_LOCK = Lock()

# In-memory session cache (survives within single process)
_SESSION_WARNINGS_SHOWN: set = set()


@dataclass
class TokenStatus:
    """Cached token status with warning deduplication."""

    token_hash: str = ""
    has_projects_scope: bool = False
    warning_key: str = ""
    warning_shown_at: float = 0.0
    last_check: float = 0.0

    def should_show_warning(self, warning_key: str) -> bool:
        """Check if warning should be shown (not shown recently)."""
        # In-memory check first (fastest)
        if warning_key in _SESSION_WARNINGS_SHOWN:
            return False

        # Check if same warning was shown within TTL
        if self.warning_key == warning_key and self.warning_shown_at > 0:
            elapsed = time.time() - self.warning_shown_at
            if elapsed < WARNING_TTL_SECONDS:
                return False

        return True

    def mark_warning_shown(self, warning_key: str) -> None:
        """Mark warning as shown (both in-memory and persistent)."""
        _SESSION_WARNINGS_SHOWN.add(warning_key)
        self.warning_key = warning_key
        self.warning_shown_at = time.time()


def _compute_token_hash(token: str) -> str:
    """Hash token for cache invalidation detection."""
    if not token:
        return ""
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def _ensure_cache_dir() -> None:
    """Create cache directory if needed."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def load_token_status() -> TokenStatus:
    """Load cached token status from disk."""
    with _CACHE_LOCK:
        if not TOKEN_CACHE_FILE.exists():
            return TokenStatus()
        try:
            data = json.loads(TOKEN_CACHE_FILE.read_text())
            return TokenStatus(
                token_hash=data.get("token_hash", ""),
                has_projects_scope=data.get("has_projects_scope", False),
                warning_key=data.get("warning_key", ""),
                warning_shown_at=data.get("warning_shown_at", 0.0),
                last_check=data.get("last_check", 0.0),
            )
        except (json.JSONDecodeError, OSError):
            return TokenStatus()


def save_token_status(status: TokenStatus) -> None:
    """Persist token status to disk."""
    with _CACHE_LOCK:
        try:
            _ensure_cache_dir()
            data = {
                "token_hash": status.token_hash,
                "has_projects_scope": status.has_projects_scope,
                "warning_key": status.warning_key,
                "warning_shown_at": status.warning_shown_at,
                "last_check": status.last_check,
            }
            TOKEN_CACHE_FILE.write_text(json.dumps(data, indent=2))
        except OSError:
            pass  # Best effort, don't fail on cache write errors


def should_show_projects_warning(warning_message: str, token: str = "") -> bool:
    """Check if Projects sync warning should be shown.

    Returns True only if:
    - Warning was not shown in current session (in-memory)
    - Warning was not shown within TTL (persistent cache)
    - Token hasn't changed since last check

    Thread-safe and handles concurrent access.
    """
    # Compute warning key from message (dedupe similar warnings)
    warning_key = hashlib.sha256(warning_message.encode()).hexdigest()[:12]

    # Quick in-memory check
    if warning_key in _SESSION_WARNINGS_SHOWN:
        return False

    status = load_token_status()

    # If token changed, reset cache
    current_hash = _compute_token_hash(token)
    if current_hash and status.token_hash and current_hash != status.token_hash:
        status = TokenStatus(token_hash=current_hash)

    return status.should_show_warning(warning_key)


def mark_projects_warning_shown(warning_message: str, token: str = "") -> None:
    """Mark Projects sync warning as shown.

    Updates both in-memory session cache and persistent disk cache.
    """
    warning_key = hashlib.sha256(warning_message.encode()).hexdigest()[:12]

    # Mark in-memory immediately
    _SESSION_WARNINGS_SHOWN.add(warning_key)

    # Update persistent cache
    status = load_token_status()
    current_hash = _compute_token_hash(token)
    if current_hash:
        status.token_hash = current_hash
    status.mark_warning_shown(warning_key)
    status.last_check = time.time()
    save_token_status(status)


def clear_warning_cache() -> None:
    """Clear all cached warnings (for testing or manual reset)."""
    global _SESSION_WARNINGS_SHOWN
    _SESSION_WARNINGS_SHOWN = set()
    with _CACHE_LOCK:
        try:
            if TOKEN_CACHE_FILE.exists():
                TOKEN_CACHE_FILE.unlink()
        except OSError:
            pass


__all__ = [
    "should_show_projects_warning",
    "mark_projects_warning_shown",
    "clear_warning_cache",
    "load_token_status",
    "save_token_status",
    "TokenStatus",
]
