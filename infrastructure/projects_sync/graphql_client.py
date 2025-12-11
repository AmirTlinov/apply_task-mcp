import random
import time
from typing import Any, Callable, Dict, Optional

import requests


class GraphQLClientError(RuntimeError):
    pass


class GraphQLPermissionError(GraphQLClientError):
    pass


class GraphQLRateLimitError(GraphQLClientError):
    pass


class GraphQLClient:
    def __init__(
        self,
        endpoint: str,
        session: Optional[requests.Session],
        token_provider: Callable[[], Optional[str]],
        rate_limiter,
        timeout: int = 30,
        max_attempts: int = 3,
    ) -> None:
        self.endpoint = endpoint
        self.session = session or requests.Session()
        self.token_provider = token_provider
        self.rate_limiter = rate_limiter
        self.timeout = timeout
        self.max_attempts = max_attempts

    def execute(self, query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
        token = self.token_provider()
        if not token:
            raise GraphQLPermissionError("GitHub token missing")
        headers = {"Authorization": f"bearer {token}", "Accept": "application/vnd.github+json"}
        attempt = 0
        delay = 1.0
        while True:
            attempt += 1
            try:
                self.rate_limiter.acquire()
                response = self.session.post(
                    self.endpoint, json={"query": query, "variables": variables}, headers=headers, timeout=self.timeout
                )
            except requests.RequestException as exc:
                if attempt >= self.max_attempts:
                    raise GraphQLClientError(f"GitHub API network error: {exc}") from exc
                self._sleep(delay)
                delay *= 2
                continue
            if response.status_code >= 500 and attempt < self.max_attempts:
                self.rate_limiter.update(response.headers)
                self._sleep(delay)
                delay *= 2
                continue
            self.rate_limiter.update(response.headers)
            if response.status_code in (401, 403):
                raise GraphQLPermissionError(f"HTTP {response.status_code}")
            if response.status_code >= 400:
                raise GraphQLClientError(f"GitHub API error: {response.status_code} {response.text}")
            payload = response.json()
            errors = payload.get("errors")
            if errors:
                self.rate_limiter.update(response.headers, errors)
                if self._looks_like_rate_limit(errors):
                    if attempt < self.max_attempts:
                        self._sleep(delay)
                        delay *= 2
                        continue
                    raise GraphQLRateLimitError(errors)
                if self._looks_like_permission_error(errors):
                    raise GraphQLPermissionError(errors)
                raise GraphQLClientError(errors)
            return payload["data"]

    def _sleep(self, base_delay: float) -> None:
        time.sleep(base_delay + random.uniform(0, base_delay))

    @staticmethod
    def _looks_like_rate_limit(errors: Any) -> bool:
        if not errors:
            return False
        for err in errors:
            message = str(err.get("message", "")).lower()
            if "rate limit" in message or "abuse detection" in message:
                return True
        return False

    @staticmethod
    def _looks_like_permission_error(errors: Any) -> bool:
        if not errors:
            return False
        keywords = (
            "resource not accessible",
            "must have push access",
            "forbidden",
            "access denied",
            "insufficient",
            "scope",
            "scopes",
            "could not resolve to a projectv2",
            "could not resolve to a repository",
            "not a member",
            "does not have permission",
            "must enable repository projects",
            "must enable organization projects",
            "apps are not permitted",
            "could not resolve to a node",
        )
        for err in errors:
            message = str(err.get("message", "")).lower()
            if any(key in message for key in keywords):
                return True
        return False
