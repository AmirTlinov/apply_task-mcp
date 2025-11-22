import hashlib
import time
from pathlib import Path

import pytest
import requests
import yaml

from infrastructure.projects_sync.graphql_client import (
    GraphQLClient,
    GraphQLPermissionError,
    GraphQLRateLimitError,
)
from infrastructure.projects_sync.issues_client import IssuesClient, IssuesPermissionError
from infrastructure.projects_sync.rate_limiter import RateLimiter
from infrastructure.projects_sync.schema_cache import SchemaCache


class DummyResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}
        self.text = str(self._payload)

    def json(self):
        return self._payload


class FlakySession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append((url, json, headers))
        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp

    def patch(self, url, json=None, headers=None, timeout=None):
        self.calls.append((url, json, headers))
        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


def test_graphql_client_retries_network(monkeypatch):
    limiter = RateLimiter()
    responses = [requests.RequestException("boom"), DummyResponse(200, {"data": {"ok": 1}})]
    session = FlakySession(responses)
    client = GraphQLClient("https://example/graphql", session, lambda: "tok", limiter, timeout=1, max_attempts=2)
    monkeypatch.setattr(GraphQLClient, "_sleep", lambda self, d: None)

    data = client.execute("query", {})

    assert data["ok"] == 1
    assert len(session.calls) == 2


def test_graphql_client_permission_error(monkeypatch):
    limiter = RateLimiter()
    session = FlakySession([DummyResponse(401, {"errors": []})])
    client = GraphQLClient("https://example/graphql", session, lambda: "tok", limiter)
    monkeypatch.setattr(GraphQLClient, "_sleep", lambda self, d: None)

    with pytest.raises(GraphQLPermissionError):
        client.execute("query", {})


def test_graphql_client_rate_limit(monkeypatch):
    limiter = RateLimiter()
    error_payload = {"errors": [{"message": "rate limit"}]}
    session = FlakySession([DummyResponse(200, error_payload), DummyResponse(200, error_payload), DummyResponse(200, error_payload)])
    client = GraphQLClient("https://example/graphql", session, lambda: "tok", limiter, max_attempts=3)
    monkeypatch.setattr(GraphQLClient, "_sleep", lambda self, d: None)

    with pytest.raises(GraphQLRateLimitError):
        client.execute("query", {})


def test_graphql_client_handles_500_and_missing_token(monkeypatch):
    limiter = RateLimiter()
    session = FlakySession([DummyResponse(502, {"errors": []}), DummyResponse(200, {"data": {"ok": 2}})])
    client = GraphQLClient("https://example/graphql", session, lambda: "tok", limiter, max_attempts=2)
    monkeypatch.setattr(GraphQLClient, "_sleep", lambda self, d: None)
    assert client.execute("query", {})["ok"] == 2

    tokenless = GraphQLClient("https://example/graphql", FlakySession([DummyResponse(200, {"data": {}})]), lambda: "", limiter)
    with pytest.raises(GraphQLPermissionError):
        tokenless.execute("query", {})

    # cover _sleep
    client._sleep(0)


def test_issues_client_retries_and_permissions(monkeypatch):
    limiter = RateLimiter()
    responses = [DummyResponse(500), DummyResponse(200, {"ok": True})]
    session = FlakySession(responses)
    client = IssuesClient(session, lambda: "tok", limiter, max_attempts=2)
    monkeypatch.setattr(IssuesClient, "_sleep", lambda self, d: None)

    resp = client.request("post", "https://api.github.com/x", {"a": 1})

    assert resp.status_code == 200
    assert len(session.calls) == 2

    with pytest.raises(IssuesPermissionError):
        IssuesClient(session, lambda: None, limiter).request("post", "https://api.github.com/x", {"a": 1})


def test_schema_cache_roundtrip(tmp_path):
    path = tmp_path / "cache.yaml"
    cache = SchemaCache(path, ttl_seconds=3600, token_getter=lambda: "tok")
    key = ("repository", "o", "r", 1)
    cache.set(key, {"id": "PID", "fields": [{"id": "f"}]})
    cache.persist()

    cache2 = SchemaCache(path, ttl_seconds=3600, token_getter=lambda: "tok")
    loaded = cache2.get(key)
    assert loaded["id"] == "PID"
    assert cache2.snapshot()[key]["fields"][0]["id"] == "f"


def test_schema_cache_ttl_and_token(tmp_path):
    path = tmp_path / "cache.yaml"
    token = "tok"
    digest = hashlib.sha1(token.encode()).hexdigest()
    data = {
        "__meta__": {"token": digest, "ttl_seconds": 1},
        "repository|o|r|1": {"id": "PID", "fields": [], "ts": time.time() - 10},
    }
    path.write_text(yaml.safe_dump(data), encoding="utf-8")

    cache = SchemaCache(path, ttl_seconds=1, token_getter=lambda: token)
    assert cache.get(("repository", "o", "r", 1)) is None

    path.write_text(yaml.safe_dump({"__meta__": {"token": "bad"}, "repository|o|r|1": {"id": "PID"}}), encoding="utf-8")
    cache2 = SchemaCache(path, ttl_seconds=1, token_getter=lambda: token)
    assert cache2.load() == {}
    assert not path.exists()

    bad_key_path = tmp_path / "bad_cache.yaml"
    bad_key_path.write_text(yaml.safe_dump({"__meta__": {"token": digest}, "broken": {"id": "x"}}), encoding="utf-8")
    cache3 = SchemaCache(bad_key_path, ttl_seconds=3600, token_getter=lambda: token)
    assert cache3.load() == {}


def test_rate_limiter_headers_and_retry_after():
    limiter = RateLimiter()
    reset_ts = time.time()
    limiter.update({"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(reset_ts)})
    limiter.acquire()
    assert limiter.last_remaining == 0

    limiter.update({"Retry-After": "0.01"})
    limiter.acquire()
    assert limiter.last_wait >= 0

    limiter.update({}, errors=[{"message": "rate limit exceeded"}])
    limiter._next_ts = time.time()
    limiter.acquire()


def test_rate_limiter_resets_and_invalid_headers():
    limiter = RateLimiter()
    limiter.update({"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(time.time() + 0.001)})
    limiter.update({"X-RateLimit-Remaining": "1", "X-RateLimit-Reset": "NaN"})
    limiter.update({"X-RateLimit-Remaining": "bad"})
    limiter.update({"Retry-After": "oops"})
    limiter.update({}, errors=[{"message": "Abuse detection mechanism triggered"}])
    limiter._next_ts = time.time()
    limiter.acquire()
    limiter.update({"X-RateLimit-Reset": str(time.time())})
    import infrastructure.projects_sync.rate_limiter as rl

    assert rl._looks_like_rate_limit(None) is False
