import time
from threading import Lock
from typing import Any, Dict, Optional


class RateLimiter:
    """Thread-safe rate limiter tuned for GitHub headers."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._next_ts = 0.0
        self.last_remaining: Optional[int] = None
        self.last_reset_epoch: Optional[float] = None
        self.last_wait: float = 0.0

    def acquire(self) -> None:
        while True:
            with self._lock:
                wait = self._next_ts - time.time()
            if wait <= 0:
                return
            time.sleep(min(wait, 2.0))

    def update(self, headers: Dict[str, Any], errors: Optional[Any] = None) -> None:
        remaining = headers.get("X-RateLimit-Remaining") or headers.get("x-ratelimit-remaining")
        reset = headers.get("X-RateLimit-Reset") or headers.get("x-ratelimit-reset")
        retry_after = headers.get("Retry-After") or headers.get("retry-after")
        with self._lock:
            now = time.time()
            if retry_after:
                try:
                    delay = float(retry_after)
                    self._next_ts = max(self._next_ts, now + delay)
                except Exception:
                    pass
            if remaining is not None:
                try:
                    rem = int(remaining)
                    self.last_remaining = rem
                    if rem <= 1:
                        if reset:
                            try:
                                reset_ts = float(reset)
                                if reset_ts > now:
                                    self._next_ts = max(self._next_ts, reset_ts)
                                    self.last_reset_epoch = reset_ts
                            except Exception:
                                self._next_ts = max(self._next_ts, now + 60)
                        else:
                            self._next_ts = max(self._next_ts, now + 60)
                except Exception:
                    pass
            if reset and self.last_reset_epoch is None:
                try:
                    self.last_reset_epoch = float(reset)
                except Exception:
                    pass
            if errors and _looks_like_rate_limit(errors):
                self._next_ts = max(self._next_ts, now + 60)
            self.last_wait = max(0.0, self._next_ts - now)


def _looks_like_rate_limit(errors: Any) -> bool:
    if not errors:
        return False
    for err in errors:
        message = str(err.get("message", "")).lower()
        if "rate limit" in message or "abuse detection" in message:
            return True
    return False
