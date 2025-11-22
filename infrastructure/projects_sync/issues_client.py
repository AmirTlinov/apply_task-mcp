import random
import time
from typing import Any, Callable, Dict, Optional

import requests


class IssuesClientError(RuntimeError):
    pass


class IssuesPermissionError(IssuesClientError):
    pass


class IssuesClient:
    def __init__(
        self,
        session: Optional[requests.Session],
        token_provider: Callable[[], Optional[str]],
        rate_limiter,
        timeout: int = 30,
        max_attempts: int = 3,
    ) -> None:
        self.session = session or requests.Session()
        self.token_provider = token_provider
        self.rate_limiter = rate_limiter
        self.timeout = timeout
        self.max_attempts = max_attempts

    def request(self, method: str, url: str, payload: Dict[str, Any]) -> requests.Response:
        token = self.token_provider()
        if not token:
            raise IssuesPermissionError("GitHub token missing")
        headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
        attempt = 0
        delay = 1.0
        while True:
            attempt += 1
            self.rate_limiter.acquire()
            resp = getattr(self.session, method)(url, json=payload, headers=headers, timeout=self.timeout)
            self.rate_limiter.update(resp.headers)
            if resp.status_code in (401, 403):
                raise IssuesPermissionError(f"HTTP {resp.status_code}")
            if resp.status_code < 500 or attempt >= self.max_attempts:
                return resp
            self._sleep(delay)
            delay *= 2

    def _sleep(self, base_delay: float) -> None:
        time.sleep(base_delay + random.uniform(0, base_delay))
