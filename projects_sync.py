import difflib
import hashlib
import hmac
import json
import logging
import os
import random
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse
from threading import Lock
import hashlib

import requests
import yaml

from config import get_user_token, set_user_token

PROJECT_ROOT = Path(os.environ.get("APPLY_TASK_PROJECT_ROOT") or Path.cwd()).resolve()
GRAPHQL_URL = "https://api.github.com/graphql"
CONFIG_PATH = PROJECT_ROOT / ".apply_task_projects.yaml"
logger = logging.getLogger("apply_task.projects")
_PROJECTS_SYNC: Optional["ProjectsSync"] = None
_PROJECTS_DISABLED_REASON: Optional[str] = None
_REPO_SLUG_CACHE: Optional[Tuple[str, str]] = None
_REPO_ROOT_CACHE: Optional[Path] = None
_SCHEMA_CACHE: Dict[Tuple[str, str, str, int], Dict[str, Any]] = {}
_SCHEMA_CACHE_LOCK = Lock()
SCHEMA_CACHE_PATH = PROJECT_ROOT / ".tasks" / ".projects_schema_cache.yaml"
SCHEMA_CACHE_TTL = int(os.getenv("APPLY_TASK_SCHEMA_TTL_SECONDS", "86400"))
DEFAULT_RESET_BEHAVIOR = os.getenv("APPLY_TASK_RATE_RESET", "auto")  # auto|hard
_RATE_RESET_MODE = DEFAULT_RESET_BEHAVIOR


def _token_digest(token: str) -> str:
    if not token:
        return ""
    return hashlib.sha1(token.encode()).hexdigest()


def _load_project_schema_cache() -> Dict[Tuple[str, str, str, int], Dict[str, Any]]:
    with _SCHEMA_CACHE_LOCK:
        if _SCHEMA_CACHE:
            return dict(_SCHEMA_CACHE)
    if SCHEMA_CACHE_PATH.exists():
        try:
            raw = yaml.safe_load(SCHEMA_CACHE_PATH.read_text()) or {}
            meta = raw.get("__meta__") if isinstance(raw, dict) else {}
            current_digest = _token_digest(get_user_token() or "")
            stored_digest = meta.get("token")
            if stored_digest and current_digest and stored_digest != current_digest:
                SCHEMA_CACHE_PATH.unlink(missing_ok=True)
                return {}
            ttl_override = meta.get("ttl_seconds")
            for key_str, value in raw.items():
                if key_str == "__meta__":
                    continue
                parts = key_str.split("|")
                if len(parts) != 4:
                    continue
                type_, owner, repo, number = parts
                ts = value.get("ts")
                try:
                    ts_val = float(ts) if ts is not None else None
                except Exception:
                    ts_val = None
                ttl_limit = ttl_override if isinstance(ttl_override, (int, float)) else SCHEMA_CACHE_TTL
                if ts_val and time.time() - ts_val > ttl_limit:
                    continue
                with _SCHEMA_CACHE_LOCK:
                    _SCHEMA_CACHE[(type_, owner, repo, int(number))] = value
        except Exception:
            return dict(_SCHEMA_CACHE)
    with _SCHEMA_CACHE_LOCK:
        return dict(_SCHEMA_CACHE)


def _persist_project_schema_cache() -> None:
    with _SCHEMA_CACHE_LOCK:
        if not _SCHEMA_CACHE:
            return
        data = {f"{k[0]}|{k[1]}|{k[2]}|{k[3]}": v for k, v in _SCHEMA_CACHE.items()}
        data["__meta__"] = {
            "token": _token_digest(get_user_token() or ""),
            "ts": time.time(),
            "ttl_seconds": SCHEMA_CACHE_TTL,
        }
    try:
        SCHEMA_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        SCHEMA_CACHE_PATH.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    except Exception:
        pass


class RateLimiter:
    """Глобальный rate-limit с учётом заголовков GitHub."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._next_ts = 0.0
        self.last_remaining: Optional[int] = None
        self.last_reset_epoch: Optional[float] = None
        self.last_wait: float = 0.0

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.time()
                wait = self._next_ts - now
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
                                    if _RATE_RESET_MODE == "hard":
                                        self._next_ts = max(self._next_ts, reset_ts + 5)
                            except Exception:
                                self._next_ts = max(self._next_ts, now + 60)
                        else:
                            if _RATE_RESET_MODE == "hard":
                                self._next_ts = max(self._next_ts, now + 300)
                            else:
                                self._next_ts = max(self._next_ts, now + 60)
                except Exception:
                    pass
            if reset and self.last_reset_epoch is None:
                try:
                    self.last_reset_epoch = float(reset)
                except Exception:
                    pass
            if errors and ProjectsSync._looks_like_rate_limit(errors):
                hard_extra = 120 if _RATE_RESET_MODE == "hard" else 60
                self._next_ts = max(self._next_ts, now + hard_extra)
            self.last_wait = max(0.0, self._next_ts - now)


_RATE_LIMITER = RateLimiter()


class ProjectsSyncPermissionError(RuntimeError):
    """Raised when GitHub denies Projects mutations due to permissions."""


def _read_project_file(path: Optional[Path] = None) -> Dict[str, Any]:
    target = path or CONFIG_PATH
    if not target.exists():
        data = _default_config_data()
        _write_project_file(data, target)
        return data
    try:
        return yaml.safe_load(target.read_text()) or {}
    except Exception:
        return {}


def _write_project_file(data: Dict[str, Any], path: Optional[Path] = None) -> None:
    target = path or CONFIG_PATH
    if not data:
        if target.exists():
            target.unlink()
        return
    target.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def _default_config_data() -> Dict[str, Any]:
    return {
        "project": {
            "type": "repository",
            "owner": "",
            "repo": "",
            "number": None,
            "enabled": True,
        },
        "fields": {
            "status": {
                "name": "Status",
                "options": {
                    "OK": "Done",
                    "WARN": "In progress",
                    "FAIL": "Backlog",
                },
            },
            "progress": {"name": "Progress"},
            "domain": {"name": "Domain"},
            "subtasks": {"name": "Subtasks"},
        },
    }


@dataclass
class FieldConfig:
    name: str
    options: Dict[str, str] = field(default_factory=dict)


@dataclass
class ProjectConfig:
    project_type: str
    owner: str
    number: int
    repo: Optional[str] = None
    workers: Optional[int] = None
    schema_ttl_seconds: Optional[int] = None
    rate_reset_behavior: Optional[str] = None
    runtime_disabled_reason: Optional[str] = None
    fields: Dict[str, FieldConfig] = field(default_factory=dict)
    enabled: bool = True


class ProjectsSync:
    def __init__(self, config_path: Optional[Path] = None) -> None:
        self.config_path = config_path or CONFIG_PATH
        self.detect_error: Optional[str] = None
        self.config: Optional[ProjectConfig] = self._load_config()
        if self.config and self.config.schema_ttl_seconds:
            # применяем кастомный TTL из конфигурации
            global SCHEMA_CACHE_TTL
            SCHEMA_CACHE_TTL = int(self.config.schema_ttl_seconds)
        if self.config and self.config.rate_reset_behavior:
            global _RATE_RESET_MODE
            _RATE_RESET_MODE = self.config.rate_reset_behavior.lower()
        self._runtime_disabled_reason: Optional[str] = self.config.runtime_disabled_reason if self.config else None
        if self.config and (not self.config.number or self.config.number <= 0):
            if self._auto_set_project_number():
                self.config = self._load_config()
        env_token = os.getenv("APPLY_TASK_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")
        saved_token = get_user_token()
        self.token = env_token or saved_token
        # если токен пришёл из окружения, но ещё не сохранён глобально — запомним его для всех проектов
        if env_token and not saved_token:
            try:
                set_user_token(env_token)
            except Exception:
                pass
        self.session = requests.Session()
        self.project_id: Optional[str] = None
        self.project_fields: Dict[str, Dict[str, Any]] = {}
        self.conflicts_dir = PROJECT_ROOT / ".tasks" / ".projects_conflicts"
        self._pending_conflicts: List[Dict[str, Any]] = []
        self._seen_conflicts: Dict[Tuple[str, str], str] = {}
        self.last_pull: Optional[str] = None
        self.last_push: Optional[str] = None
        if self._runtime_disabled_reason:
            self._project_lookup_failed = True
            global _PROJECTS_DISABLED_REASON
            _PROJECTS_DISABLED_REASON = self._runtime_disabled_reason
        self._rate_limiter = _RATE_LIMITER
        self._project_lookup_failed: bool = False
        self._metadata_attempted: bool = False
        self._viewer_login: Optional[str] = None

    # ------------------------------------------------------------------
    # Helpers for conflict detection / reporting
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_timestamp(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            if "T" in text:
                return datetime.fromisoformat(text)
            return datetime.strptime(text, "%Y-%m-%d %H:%M")
        except ValueError:
            return None

    def _local_is_newer(self, local_value: Optional[str], remote_value: Optional[str]) -> bool:
        remote_dt = self._parse_timestamp(remote_value)
        local_dt = self._parse_timestamp(local_value)
        if not remote_dt or not local_dt:
            return False
        if remote_dt.tzinfo:
            remote_dt = remote_dt.astimezone(timezone.utc).replace(tzinfo=None)
        return local_dt > remote_dt

    def _conflict_key(self, task_id: str, remote_updated: Optional[str], new_text: str) -> Tuple[str, str]:
        if remote_updated:
            return task_id, remote_updated
        digest = hashlib.sha1(new_text.encode()).hexdigest()
        return task_id, digest

    def _record_conflict(self, task_id: str, file_path: Path, existing_text: str, new_text: str, reason: str, remote_updated: Optional[str], source: str) -> Dict[str, Any]:
        safe_task = task_id or file_path.stem
        key = self._conflict_key(safe_task, remote_updated, new_text)
        report_path = self._seen_conflicts.get(key)
        if not report_path:
            self.conflicts_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            report_file = self.conflicts_dir / f"{safe_task}-{stamp}.diff"
            diff = "\n".join(
                difflib.unified_diff(
                    (existing_text or "").splitlines(),
                    new_text.splitlines(),
                    fromfile=str(file_path),
                    tofile=f"remote:{source}",
                    lineterm="",
                )
            )
            report_text = (
                f"Задача: {safe_task}\n"
                f"Источник: {source}\n"
                f"Причина: {reason}\n"
                f"Удалённое обновление: {remote_updated or '—'}\n"
                f"--- DIFF ---\n{diff or 'Нет различий'}\n"
            )
            report_file.write_text(report_text, encoding="utf-8")
            report_path = str(report_file)
            self._seen_conflicts[key] = report_path
        info = {
            "task": safe_task,
            "file": str(file_path),
            "diff_path": report_path,
            "remote_updated": remote_updated,
            "source": source,
            "reason": reason,
        }
        self._pending_conflicts.append(info)
        logger.warning("Projects sync conflict for %s (%s). Детали: %s", safe_task, reason, report_path)
        return info

    def consume_conflicts(self) -> List[Dict[str, Any]]:
        pending = list(self._pending_conflicts)
        self._pending_conflicts.clear()
        return pending

    @property
    def enabled(self) -> bool:
        return bool(self.config and self.config.enabled and self.token and not self._runtime_disabled_reason)

    @property
    def runtime_disabled_reason(self) -> Optional[str]:
        return self._runtime_disabled_reason

    def _disable_runtime(self, reason: str, persist: bool = False) -> None:
        if not self._runtime_disabled_reason:
            self._runtime_disabled_reason = reason
            logger.warning("Projects sync disabled: %s", reason)
        self._project_lookup_failed = True
        global _PROJECTS_DISABLED_REASON
        _PROJECTS_DISABLED_REASON = self._runtime_disabled_reason
        if persist and self.config_path.exists():
            data = _read_project_file(self.config_path)
            project = data.get("project") or {}
            project["runtime_disabled_reason"] = reason
            data["project"] = project
            _write_project_file(data, self.config_path)

    def sync_task(self, task) -> bool:
        if not self.enabled:
            return False
        try:
            self._ensure_project_metadata()
        except ProjectsSyncPermissionError:
            return False
        except Exception as exc:  # pragma: no cover - defensive log
            logger.warning("projects sync disabled: %s", exc)
            return False

        changed = False
        body = self._build_body(task)
        try:
            if not getattr(task, "project_item_id", None):
                item_id, draft_id = self._create_draft_issue(task, body)
                if not self.enabled:
                    return False
                if item_id:
                    task.project_item_id = item_id
                    changed = True
                if draft_id:
                    task.project_draft_id = draft_id
                    changed = True
            else:
                self._update_draft_issue(task, body)
            if not self.enabled:
                return False

            if getattr(task, "project_item_id", None):
                self._update_fields(task)
            if not self.enabled:
                return False
            repo_changed = self._ensure_repo_issue(task, body)
        except ProjectsSyncPermissionError:
            return False

        if changed or repo_changed:
            self._persist_metadata(task)
            self.last_push = datetime.now().strftime("%Y-%m-%d %H:%M")
        return changed or repo_changed

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_config(self) -> Optional[ProjectConfig]:
        data = _read_project_file(self.config_path)
        if not data:
            return None
        project = data.get("project") or {}
        fields_cfg = {}
        for alias, cfg in (data.get("fields") or {}).items():
            fields_cfg[alias] = FieldConfig(name=cfg.get("name", alias), options=cfg.get("options", {}))
        stored_type = (project.get("type") or "repository").lower()
        raw_number = project.get("number")
        number = int(raw_number) if isinstance(raw_number, int) and raw_number > 0 else None
        enabled = project.get("enabled", True)
        workers = project.get("workers")
        if isinstance(workers, str) and workers.isdigit():
            workers = int(workers)
        ttl_override = project.get("schema_ttl_seconds")
        if isinstance(ttl_override, str) and ttl_override.isdigit():
            ttl_override = int(ttl_override)
        reset_behavior = (project.get("rate_reset_behavior") or "").lower() or DEFAULT_RESET_BEHAVIOR
        runtime_disabled_reason = project.get("runtime_disabled_reason")
        try:
            owner, repo = detect_repo_slug()
            self.detect_error = None
        except RuntimeError as exc:
            self.detect_error = str(exc)
            owner = ""
            repo = ""
        # если конфиг принадлежит другому репо — сбросим под текущий
        if project.get("owner") and project.get("repo") and (project.get("owner") != owner or project.get("repo") != repo):
            project = {
                "type": "repository",
                "owner": owner,
                "repo": repo,
                "number": None,
                "enabled": True,
                "runtime_disabled_reason": None,
            }
            data["project"] = project
            _write_project_file(data, self.config_path)
            number = None
        if stored_type == "user":
            repo = ""
        # migrate workers => default auto(0) записываем без участия пользователя
        if workers is None and self.config_path.exists():
            project["workers"] = 0
            _write_project_file({"project": project, "fields": data.get("fields") or {}}, self.config_path)
            workers = 0
        return ProjectConfig(
            project_type=stored_type,
            owner=owner,
            number=number or 0,
            repo=repo,
            workers=workers if workers else None,
            schema_ttl_seconds=ttl_override if ttl_override else None,
            rate_reset_behavior=reset_behavior,
            runtime_disabled_reason=runtime_disabled_reason,
            fields=fields_cfg,
            enabled=bool(enabled),
        )

    def _graphql(self, query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
        if self._runtime_disabled_reason:
            raise ProjectsSyncPermissionError(self._runtime_disabled_reason)
        headers = {"Authorization": f"bearer {self.token}", "Accept": "application/vnd.github+json"}
        attempt = 0
        delay = 1.0
        while True:
            attempt += 1
            try:
                self._rate_limiter.acquire()
                response = self.session.post(GRAPHQL_URL, json={"query": query, "variables": variables}, headers=headers, timeout=30)
            except requests.RequestException as exc:
                if attempt >= 3:
                    raise RuntimeError(f"GitHub API network error: {exc}") from exc
                jitter = random.uniform(0, delay)
                time.sleep(delay + jitter)
                delay *= 2
                continue
            if response.status_code >= 500 and attempt < 3:
                jitter = random.uniform(0, delay)
                time.sleep(delay + jitter)
                delay *= 2
                continue
            self._rate_limiter.update(response.headers)
            if response.status_code in (401, 403):
                self._disable_runtime(f"GitHub token lacks Projects access (HTTP {response.status_code})")
                raise ProjectsSyncPermissionError("projects sync disabled due to insufficient permissions")
            if response.status_code >= 400:
                raise RuntimeError(f"GitHub API error: {response.status_code} {response.text}")
            payload = response.json()
            errors = payload.get("errors")
            if errors:
                self._rate_limiter.update(response.headers, errors)
                if self._looks_like_project_not_found(errors):
                    reason = errors[0].get("message", "project not found")
                    self._disable_runtime(reason, persist=True)
                    raise ProjectsSyncPermissionError(reason)
                if self._looks_like_permission_error(errors):
                    reason = errors[0].get("message", "permission denied")
                    self._disable_runtime(reason, persist=True)
                    raise ProjectsSyncPermissionError(reason)
                if self._looks_like_rate_limit(errors) and attempt < 3:
                    jitter = random.uniform(0, delay)
                    time.sleep(delay + jitter)
                    delay *= 2
                    continue
                raise RuntimeError(errors)
            return payload["data"]

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

    @staticmethod
    def _looks_like_project_not_found(errors: Any) -> bool:
        if not errors:
            return False
        for err in errors:
            message = str(err.get("message", "")).lower()
            if "could not resolve to a projectv2" in message:
                return True
        return False

    @staticmethod
    def _looks_like_rate_limit(errors: Any) -> bool:
        if not errors:
            return False
        for err in errors:
            message = str(err.get("message", "")).lower()
            if "rate limit" in message or "abuse detection" in message:
                return True
        return False

    def _ensure_project_metadata(self) -> None:
        if self._runtime_disabled_reason:
            raise ProjectsSyncPermissionError(self._runtime_disabled_reason)
        if self._project_lookup_failed:
            raise ProjectsSyncPermissionError(self._runtime_disabled_reason or "project lookup failed")
        if self.project_id:
            return
        cfg = self.config
        if not cfg:
            raise RuntimeError("projects config missing")
        if cfg.number is None or cfg.number <= 0:
            # попробуем определить/создать, иначе отключаем синхронизацию без спама
            if not self._auto_set_project_number() and not self._auto_create_repo_project():
                self._disable_runtime("Project number not set and auto-detect failed")
                self._project_lookup_failed = True
                raise ProjectsSyncPermissionError("project number missing")
            cfg = self.config
            if not cfg or cfg.number <= 0:
                self._disable_runtime("Project number still missing after auto-detect")
                self._project_lookup_failed = True
                raise ProjectsSyncPermissionError("project number missing")
        cache_key = (cfg.project_type, cfg.owner, cfg.repo or "", int(cfg.number or 0))
        cached_cache = _load_project_schema_cache()
        cached = cached_cache.get(cache_key)
        if cached:
            self.project_id = cached.get("id")
            self.project_fields = self._map_fields(cached.get("fields") or [])
            if self.project_id:
                self._clear_runtime_disable()
                return
        retry = False
        if cfg.project_type == "repository":
            if not cfg.repo:
                raise RuntimeError("repo is required for repository projects")
            query = self._repo_project_query()
            variables = {"owner": cfg.owner, "name": cfg.repo, "number": cfg.number}
            while True:
                try:
                    data = self._graphql(query, variables)
                    break
                except RuntimeError as exc:
                    message = str(exc).lower()
                    not_found = "could not resolve to a projectv2" in message
                    if not retry and self._auto_set_project_number():
                        cfg = self.config
                        if not cfg or not cfg.repo:
                            raise
                        variables["number"] = cfg.number
                        retry = True
                        continue
                    if (not retry and not_found) or (not retry and self._auto_create_repo_project()):
                        if not cfg or not cfg.repo:
                            cfg = self.config
                        if cfg:
                            variables["number"] = cfg.number
                        retry = True
                        continue
                    if not_found:
                        with _SCHEMA_CACHE_LOCK:
                            _SCHEMA_CACHE.pop(cache_key, None)
                        _persist_project_schema_cache()
                        if not self._project_lookup_failed:
                            # сбросим номер чтобы при следующем запуске произошло авто-определение
                            try:
                                _update_project_entry(number=None)
                            except Exception:
                                pass
                            self._disable_runtime(f"Project {cfg.owner}/{cfg.repo}#{variables.get('number')} not found")
                        raise ProjectsSyncPermissionError("project not found")
                raise
            repo_node = (data.get("repository") or {})
            node = repo_node.get("projectV2")
        elif cfg.project_type == "organization":
            query = self._org_project_query()
            variables = {"login": cfg.owner, "number": cfg.number}
            data = self._graphql(query, variables)
            node = (data.get("organization") or {}).get("projectV2")
        else:  # user
            query = self._user_project_query()
            variables = {"login": cfg.owner, "number": cfg.number}
            data = self._graphql(query, variables)
            node = (data.get("user") or {}).get("projectV2")
        if not node:
            raise RuntimeError("project not found")
        self.project_id = node.get("id")
        field_nodes = ((node.get("fields") or {}).get("nodes") or [])
        self.project_fields = self._map_fields(field_nodes)
        if self.project_id:
            with _SCHEMA_CACHE_LOCK:
                _SCHEMA_CACHE[cache_key] = {"id": self.project_id, "fields": field_nodes}
            _persist_project_schema_cache()

    def _repo_project_query(self) -> str:
        return (
            "query($owner:String!,$name:String!,$number:Int!){\n"
            "  repository(owner:$owner,name:$name){\n"
            "    id name\n"
            "    owner{ id __typename login }\n"
            "    projectV2(number:$number){\n"
            "      id title\n"
            "      fields(first:50){\n"
            "        nodes{\n"
            "          __typename\n"
            "          ... on ProjectV2FieldCommon { id name dataType }\n"
            "          ... on ProjectV2SingleSelectField { options { id name } }\n"
            "        }\n"
            "      }\n"
            "    }\n"
            "  }\n"
            "}"
        )

    def _repo_projects_list_query(self) -> str:
        return (
            "query($owner:String!,$name:String!){"
            "  repository(owner:$owner,name:$name){"
            "    projectsV2(first:10){ nodes{ number title } }"
            "  }"
            "}"
        )

    def _user_projects_list_query(self) -> str:
        return (
            "query($login:String!){"
            "  user(login:$login){ projectsV2(first:10){ nodes{ number title } } }"
            "}"
        )
    def _org_project_query(self) -> str:
        return (
            "query($login:String!,$number:Int!){\n"
            "  organization(login:$login){\n"
            "    projectV2(number:$number){\n"
            "      id title\n"
            "      fields(first:50){\n"
            "        nodes{\n"
            "          __typename\n"
            "          ... on ProjectV2FieldCommon { id name dataType }\n"
            "          ... on ProjectV2SingleSelectField { options { id name } }\n"
            "        }\n"
            "      }\n"
            "    }\n"
            "  }\n"
            "}"
        )

    def _user_project_query(self) -> str:
        return (
            "query($login:String!,$number:Int!){\n"
            "  user(login:$login){\n"
            "    projectV2(number:$number){\n"
            "      id title\n"
            "      fields(first:50){\n"
            "        nodes{\n"
            "          __typename\n"
            "          ... on ProjectV2FieldCommon { id name dataType }\n"
            "          ... on ProjectV2SingleSelectField { options { id name } }\n"
            "        }\n"
            "      }\n"
            "    }\n"
            "  }\n"
            "}"
        )

    def _auto_set_project_number(self) -> bool:
        cfg = self.config
        if not cfg:
            return False
        if cfg.project_type == "repository" and cfg.repo:
            nodes = self._list_repo_projects(cfg.owner, cfg.repo)
            if nodes:
                number = nodes[0].get("number")
                if number:
                    update_project_target(int(number))
                    self.config = self._load_config()
                    return True
        nodes = self._list_user_projects(cfg.owner)
        if nodes:
            number = nodes[0].get("number")
            if number:
                _update_project_entry(type="user", repo="", number=int(number))
                self.config = self._load_config()
                return True
        self.detect_error = "GitHub Projects не найдены для указанного владельца"
        return False

    def _list_repo_projects(self, owner: str, repo: str) -> List[Dict[str, Any]]:
        try:
            data = self._graphql(self._repo_projects_list_query(), {"owner": owner, "name": repo})
        except Exception:
            return []
        return (((data.get("repository") or {}).get("projectsV2") or {}).get("nodes") or [])

    def _list_user_projects(self, owner: str) -> List[Dict[str, Any]]:
        try:
            data = self._graphql(self._user_projects_list_query(), {"login": owner})
        except Exception:
            return []
        return (((data.get("user") or {}).get("projectsV2") or {}).get("nodes") or [])

    def project_url(self) -> Optional[str]:
        cfg = self.config
        if not cfg or not cfg.number:
            return None
        num = cfg.number
        if cfg.project_type == "repository" and cfg.owner and cfg.repo:
            return f"https://github.com/{cfg.owner}/{cfg.repo}/projects/{num}"
        if cfg.project_type == "organization" and cfg.owner:
            return f"https://github.com/orgs/{cfg.owner}/projects/{num}"
        if cfg.owner:
            return f"https://github.com/users/{cfg.owner}/projects/{num}"
        return None

    def _log_event(self, event: str, details: str) -> None:
        try:
            self.conflicts_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = self.conflicts_dir / f"{event}-{stamp}.log"
            path.write_text(details, encoding="utf-8")
        except Exception:
            pass

    def _repo_id_query(self) -> str:
        return (
            "query($owner:String!,$name:String!){ repository(owner:$owner,name:$name){ id name owner{ id login } } }"
        )

    def _auto_create_repo_project(self) -> bool:
        cfg = self.config
        if not cfg or cfg.project_type != "repository" or not cfg.repo:
            return False
        try:
            info = self._graphql(self._repo_id_query(), {"owner": cfg.owner, "name": cfg.repo})
            repo = (info.get("repository") or {})
            repo_id = repo.get("id")
            owner_id = (repo.get("owner") or {}).get("id")
            title = f"{repo.get('name') or cfg.repo} backlog"
            mutation = (
                "mutation($owner:ID!,$title:String!){ createProjectV2(input:{ownerId:$owner,title:$title}){ projectV2{ id number title } } }"
            )
            created = self._graphql(mutation, {"owner": owner_id, "title": title})
            project = ((created.get("createProjectV2") or {}).get("projectV2") or {})
            project_id = project.get("id")
            number = project.get("number")
            if project_id and repo_id:
                link = (
                    "mutation($project:ID!,$repo:ID!){ linkProjectV2ToRepository(input:{projectId:$project,repositoryId:$repo}){ projectV2{ id } } }"
                )
                try:
                    self._graphql(link, {"project": project_id, "repo": repo_id})
                    self._log_event("project_link", f"Linked project {project_id} to repo {cfg.owner}/{cfg.repo}")
                except Exception:
                    pass
            if number:
                update_project_target(int(number))
                self.config = self._load_config()
                self._log_event("project_created", f"Created project {project_id or '?'} number={number} for repo {cfg.owner}/{cfg.repo}")
                return True
        except Exception:
            return False
        return False

    def _map_fields(self, nodes: Any) -> Dict[str, Dict[str, Any]]:
        result: Dict[str, Dict[str, Any]] = {}
        cfg_fields = self.config.fields if self.config else {}
        for alias, field_cfg in cfg_fields.items():
            match = next((n for n in nodes if n.get("name") == field_cfg.name), None)
            if not match:
                continue
            entry = {
                "id": match.get("id"),
                "typename": match.get("__typename"),
                "options": {},
                "reverse": {},
            }
            if match.get("__typename") == "ProjectV2SingleSelectField":
                entry["options" ] = {opt.get("name"): opt.get("id") for opt in (match.get("options") or [])}
                reverse = {}
                for status, option_name in field_cfg.options.items():
                    opt_id = entry["options"].get(option_name)
                    if opt_id:
                        reverse[opt_id] = status
                entry["reverse"] = reverse
            result[alias] = entry
        return result

    def _alias_by_field_id(self, field_id: str) -> Optional[str]:
        for alias, info in self.project_fields.items():
            if info.get("id") == field_id:
                return alias
        return None

    def _create_draft_issue(self, task, body: str) -> (Optional[str], Optional[str]):
        mutation = (
            "mutation($projectId:ID!,$title:String!,$body:String!){"
            "  addProjectV2DraftIssue(input:{projectId:$projectId,title:$title,body:$body}){"
            "    projectItem{ id content{ __typename ... on DraftIssue { id } } }"
            "  }"
            "}"
        )
        variables = {"projectId": self.project_id, "title": f"{task.id}: {task.title}", "body": body}
        try:
            data = self._graphql(mutation, variables)["addProjectV2DraftIssue"]
            item = (data or {}).get("projectItem") or {}
            draft = (item.get("content") or {}) if item else {}
            draft_id = draft.get("id") if draft.get("__typename") == "DraftIssue" else None
            return item.get("id"), draft_id
        except ProjectsSyncPermissionError:
            return None, None
        except Exception as exc:  # pragma: no cover - network failure
            logger.warning("GitHub draft creation failed: %s", exc)
            return None, None

    def _ensure_draft_id(self, task) -> Optional[str]:
        if getattr(task, "project_draft_id", None):
            return task.project_draft_id
        query = (
            "query($item:ID!){ node(id:$item){ ... on ProjectV2Item { content { __typename ... on DraftIssue { id } } } } }"
        )
        try:
            data = self._graphql(query, {"item": task.project_item_id})
            node = (data.get("node") or {}).get("content") or {}
            if node.get("__typename") == "DraftIssue":
                task.project_draft_id = node.get("id")
        except Exception as exc:  # pragma: no cover - network failure
            logger.warning("Unable to fetch draft issue id: %s", exc)
        return task.project_draft_id

    def _update_draft_issue(self, task, body: str) -> None:
        draft_id = self._ensure_draft_id(task)
        if not draft_id:
            return
        mutation = (
            "mutation($draftId:ID!,$title:String!,$body:String!){"
            "  updateProjectV2DraftIssue(input:{draftIssueId:$draftId,title:$title,body:$body}){ draftIssue{ id } }"
            "}"
        )
        variables = {
            "draftId": draft_id,
            "title": f"{task.id}: {task.title}",
            "body": body,
        }
        try:
            self._graphql(mutation, variables)
        except ProjectsSyncPermissionError:
            return
        except Exception as exc:  # pragma: no cover
            logger.warning("GitHub draft update failed: %s", exc)

    def _update_fields(self, task) -> None:
        for alias, field in self.project_fields.items():
            field_cfg = self.config.fields.get(alias) if self.config else None
            if not field_cfg:
                continue
            value = None
            if field["typename"] == "ProjectV2SingleSelectField":
                desired = field_cfg.options.get(task.status)
                option_id = field["options"].get(desired)
                if option_id:
                    value = {"singleSelectOptionId": option_id}
            elif field["typename"] == "ProjectV2NumberField":
                if alias == "progress":
                    value = {"number": task.calculate_progress()}
            else:  # text/datetime
                if alias == "domain":
                    value = {"text": task.domain or "-"}
                elif alias == "subtasks":
                    total = len(task.subtasks)
                    completed = sum(1 for st in task.subtasks if st.completed)
                    value = {"text": f"{completed}/{total}" if total else "-"}
            if value is None:
                continue
            mutation = (
                "mutation($projectId:ID!,$itemId:ID!,$fieldId:ID!,$value:ProjectV2FieldValue!){"
                "  updateProjectV2ItemFieldValue(input:{projectId:$projectId,itemId:$itemId,fieldId:$fieldId,value:$value}){ projectV2Item{ id } }"
                "}"
            )
            variables = {
                "projectId": self.project_id,
                "itemId": task.project_item_id,
                "fieldId": field["id"],
                "value": value,
            }
            try:
                self._graphql(mutation, variables)
            except ProjectsSyncPermissionError:
                return
            except Exception as exc:  # pragma: no cover
                logger.warning("Field update failed (%s): %s", alias, exc)

    def _persist_metadata(self, task, remote_updated: Optional[str] = None, source: str = "pull") -> bool:
        try:
            new_path = task.filepath
            old_path = getattr(task, "_source_path", new_path)
            old_mtime = getattr(task, "_source_mtime", None)
            existing_text = ""
            if Path(old_path).exists():
                existing_text = Path(old_path).read_text(encoding="utf-8")
            new_text = task.to_file_content()
            conflict_reason = None
            if remote_updated and self._local_is_newer(getattr(task, "updated", None), remote_updated):
                conflict_reason = "Локальные правки новее удалённых"
            elif existing_text and old_mtime is not None and Path(old_path).exists():
                current_mtime = Path(old_path).stat().st_mtime
                if current_mtime > old_mtime + 1e-6:
                    conflict_reason = "Файл обновлён локально после загрузки"
            if conflict_reason:
                self._record_conflict(getattr(task, "id", Path(old_path).stem), Path(old_path), existing_text, new_text, conflict_reason, remote_updated, source)
                return False
            new_path.parent.mkdir(parents=True, exist_ok=True)
            new_path.write_text(new_text, encoding="utf-8")
            if old_path != new_path and Path(old_path).exists():
                Path(old_path).unlink()
            task._source_path = new_path
            task._source_mtime = new_path.stat().st_mtime
            if remote_updated:
                task.project_remote_updated = remote_updated
            return True
        except Exception as exc:  # pragma: no cover
            logger.warning("Unable to persist project metadata: %s", exc)
        return False

    def _build_body(self, task) -> str:
        lines = [
            f"# {task.id}: {task.title}",
            "",
            f"- Status: {task.status}",
            f"- Domain: {task.domain or '—'}",
            f"- Progress: {task.calculate_progress()}%",
        ]
        if getattr(task, "description", ""):
            lines += ["", "## Description", task.description]
        if task.subtasks:
            lines += ["", "## Subtasks"]
            for sub in task.subtasks:
                mark = "x" if sub.completed else " "
                crit = "✓" if sub.criteria_confirmed else "·"
                tests = "✓" if sub.tests_confirmed else "·"
                blockers = "✓" if sub.blockers_resolved else "·"
                lines.append(f"- [{mark}] {sub.title} [criteria {crit} | tests {tests} | blockers {blockers}]")
        if getattr(task, "success_criteria", None):
            lines += ["", "## Success criteria", *[f"- {item}" for item in task.success_criteria]]
        if getattr(task, "risks", None):
            lines += ["", "## Risks", *[f"- {r}" for r in task.risks]]
        return "\n".join(lines).strip()

    def _ensure_repo_issue(self, task, body: str) -> bool:
        cfg = self.config
        if not cfg or cfg.project_type != "repository" or not cfg.repo:
            return False
        if not self.enabled:
            return False
        headers = {"Authorization": f"token {self.token}", "Accept": "application/vnd.github+json"}
        base_url = f"https://api.github.com/repos/{cfg.owner}/{cfg.repo}/issues"
        payload = {
            "title": f"{task.id}: {task.title}",
            "body": body,
        }
        changed = False
        if not getattr(task, "project_issue_number", None):
            resp = self._issue_request_with_retry("post", base_url, payload, headers)
            if resp.status_code >= 400:
                self._report_issue_error(task, resp)
                return False
            data = resp.json()
            task.project_issue_number = data.get("number")
            changed = True
        else:
            issue_url = f"{base_url}/{task.project_issue_number}"
            payload["state"] = "closed" if task.status == "OK" else "open"
            resp = self._issue_request_with_retry("patch", issue_url, payload, headers)
            if resp.status_code >= 400:
                self._report_issue_error(task, resp)
                return False
        return changed

    def _report_issue_error(self, task, response):
        message = f"GitHub issue error ({response.status_code}): {response.text[:120]}"
        logger.warning(message)
        setattr(task, "_sync_error", message)
        self._record_conflict(task.id, Path(task.filepath), task.to_file_content(), task.to_file_content(), message, None, "issues")

    def _issue_request_with_retry(self, method: str, url: str, payload: Dict[str, Any], headers: Dict[str, str]) -> requests.Response:
        attempt = 0
        delay = 1.0
        while True:
            attempt += 1
            self._rate_limiter.acquire()
            resp = getattr(self.session, method)(url, json=payload, headers=headers, timeout=30)
            self._rate_limiter.update(resp.headers)
            if resp.status_code in (401, 403):
                self._disable_runtime(f"GitHub token lacks repo issue access (HTTP {resp.status_code})")
                raise ProjectsSyncPermissionError("issues sync disabled due to insufficient permissions")
            if resp.status_code < 500 or attempt >= 3:
                if resp.status_code >= 500:
                    logger.warning("issue sync retry #%s failed: %s %s", attempt, resp.status_code, resp.text)
                return resp
            logger.warning("issue sync retry #%s due to %s", attempt, resp.status_code)
            jitter = random.uniform(0, delay)
            time.sleep(delay + jitter)
            delay *= 2

    # ------------------------------------------------------------------
    # Pull from GitHub → local files
    # ------------------------------------------------------------------

    def pull_task_fields(self, task) -> bool:
        if not self.enabled or not getattr(task, "project_item_id", None):
            return False
        try:
            self._ensure_project_metadata()
        except ProjectsSyncPermissionError:
            return False
        except Exception as exc:  # pragma: no cover
            logger.warning("projects metadata unavailable: %s", exc)
            return False
        try:
            data = self._fetch_remote_state(task.project_item_id)
        except ProjectsSyncPermissionError:
            return False
        if not data:
            return False
        updates: Dict[str, Any] = {}
        if data.get("status") and data["status"] != task.status:
            updates["status"] = data["status"]
        if data.get("progress") is not None and data["progress"] != task.progress:
            updates["progress"] = data["progress"]
        if data.get("domain") and data["domain"] != task.domain:
            updates["domain"] = data["domain"]
        if not updates:
            return False
        remote_updated = data.get("remote_updated")
        snapshot = {
            "status": task.status,
            "progress": task.progress,
            "domain": task.domain,
            "project_remote_updated": getattr(task, "project_remote_updated", None),
        }
        for key, value in updates.items():
            setattr(task, key, value)
        task.project_remote_updated = remote_updated or snapshot["project_remote_updated"]
        persisted = self._persist_metadata(task, remote_updated, source="pull")
        if not persisted:
            for key, value in snapshot.items():
                setattr(task, key, value)
            return False
        self.last_pull = datetime.now().strftime("%Y-%m-%d %H:%M")
        return True

    def _fetch_remote_state(self, item_id: str) -> Dict[str, Any]:
        cfg_fields = self.config.fields if self.config else {}
        variables: Dict[str, Any] = {"item": item_id}
        variable_defs = ["$item:ID!"]
        query_sections = []
        if "status" in cfg_fields:
            variables["statusName"] = cfg_fields["status"].name
            variable_defs.append("$statusName:String!")
            query_sections.append(
                "status: fieldValueByName(name:$statusName){ ... on ProjectV2ItemFieldSingleSelectValue { optionId } }"
            )
        if "progress" in cfg_fields:
            variables["progressName"] = cfg_fields["progress"].name
            variable_defs.append("$progressName:String!")
            query_sections.append(
                "progress: fieldValueByName(name:$progressName){ ... on ProjectV2ItemFieldNumberValue { number } }"
            )
        if "domain" in cfg_fields:
            variables["domainName"] = cfg_fields["domain"].name
            variable_defs.append("$domainName:String!")
            query_sections.append(
                "domain: fieldValueByName(name:$domainName){ ... on ProjectV2ItemFieldTextValue { text } }"
            )
        if not query_sections:
            return {}
        query = (
            f"query({','.join(variable_defs)}){{"
            "  node(id:$item){"
            "    ... on ProjectV2Item {"
            f"      {' '.join(query_sections)} updatedAt"
            "    }"
            "  }"
            "}"
        )
        data = self._graphql(query, variables)
        node = data.get("node") or {}
        result: Dict[str, Any] = {}
        if node.get("updatedAt"):
            result["remote_updated"] = node.get("updatedAt")
        status_field = node.get("status")
        if status_field and cfg_fields.get("status"):
            option_id = status_field.get("optionId")
            status = (self.project_fields.get("status") or {}).get("reverse", {}).get(option_id)
            if status:
                result["status"] = status
        progress_field = node.get("progress")
        if progress_field and progress_field.get("number") is not None:
            result["progress"] = int(progress_field.get("number"))
        domain_field = node.get("domain")
        if domain_field and domain_field.get("text"):
            result["domain"] = domain_field.get("text").strip()
        return result

    def _lookup_item_timestamp(self, item_id: str) -> Optional[str]:
        query = "query($item:ID!){ node(id:$item){ ... on ProjectV2Item { updatedAt } } }"
        try:
            data = self._graphql(query, {"item": item_id})
            node = data.get("node") or {}
            return node.get("updatedAt")
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Webhook handling
    # ------------------------------------------------------------------

    def handle_webhook(self, body: str, signature: Optional[str], secret: Optional[str]) -> Optional[str]:
        if not self.enabled:
            return None
        payload_bytes = body.encode()
        if secret:
            if not signature or not signature.startswith("sha256="):
                raise ValueError("signature missing for webhook")
            expected = "sha256=" + hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, signature):
                raise ValueError("invalid signature")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid payload: {exc}")

        item = payload.get("projects_v2_item") or {}
        item_id = item.get("node_id") or item.get("id")
        if not item_id:
            return None

        try:
            self._ensure_project_metadata()
        except Exception as exc:  # pragma: no cover
            logger.warning("projects metadata unavailable: %s", exc)
            return None

        if self.project_id and item.get("project_node_id") and item["project_node_id"] != self.project_id:
            return None

        change = (payload.get("changes") or {}).get("field_value")
        if not change:
            return None
        field_id = change.get("field_node_id")
        alias = self._alias_by_field_id(field_id) if field_id else None
        if not alias:
            return None

        updates: Dict[str, Any] = {}
        if alias == "status":
            option_id = change.get("single_select_option_id") or (change.get("value") or {}).get("singleSelectOptionId")
            status = None
            if option_id:
                status = (self.project_fields.get(alias) or {}).get("reverse", {}).get(option_id)
            if status:
                updates["status"] = status
        elif alias == "progress":
            number = change.get("number")
            if number is None:
                number = (change.get("value") or {}).get("number")
            if number is not None:
                updates["progress"] = int(number)
        elif alias == "domain":
            text = change.get("text") or (change.get("value") or {}).get("text")
            if text:
                updates["domain"] = text.strip()
        if not updates:
            return None
        remote_updated = item.get("updated_at") or payload.get("updated_at")
        if not remote_updated:
            remote_updated = self._lookup_item_timestamp(item_id)
        return self._update_local_metadata(item_id, updates, remote_updated)

    def _update_local_metadata(self, item_id: str, updates: Dict[str, Any], remote_updated: Optional[str] = None) -> Dict[str, Any]:
        tasks_dir = Path(".tasks")
        if not tasks_dir.exists():
            return {}
        for file in tasks_dir.rglob("TASK-*.task"):
            content = file.read_text(encoding="utf-8")
            parts = content.split("---", 2)
            if len(parts) < 3:
                continue
            metadata = yaml.safe_load(parts[1]) or {}
            if metadata.get("project_item_id") != item_id:
                continue
            changed = False
            if "status" in updates and metadata.get("status") != updates["status"]:
                metadata["status"] = updates["status"]
                changed = True
            if "progress" in updates and metadata.get("progress") != updates["progress"]:
                metadata["progress"] = updates["progress"]
                changed = True
            if "domain" in updates and updates["domain"]:
                metadata["domain"] = updates["domain"]
                changed = True
            if changed:
                metadata["project_remote_updated"] = remote_updated or metadata.get("project_remote_updated")
                header = yaml.dump(metadata, allow_unicode=True, default_flow_style=False).strip()
                body = parts[2].lstrip("\n")
                new_text = f"---\n{header}\n---\n{body}"
                if self._local_is_newer(metadata.get("updated"), remote_updated):
                    info = self._record_conflict(metadata.get("id", file.stem), file, content, new_text, "Локальные правки новее удалённых", remote_updated, "webhook")
                    return {"conflict": info}
                file.write_text(new_text, encoding="utf-8")
                return {"updated": str(file)}
            return {}
        return {}


def get_projects_sync() -> ProjectsSync:
    global _PROJECTS_SYNC
    if _PROJECTS_SYNC is None:
        _PROJECTS_SYNC = ProjectsSync()
    elif _PROJECTS_DISABLED_REASON:
        _PROJECTS_SYNC._runtime_disabled_reason = _PROJECTS_DISABLED_REASON
        _PROJECTS_SYNC._project_lookup_failed = True
    return _PROJECTS_SYNC


def reload_projects_sync() -> ProjectsSync:
    global _PROJECTS_SYNC
    _PROJECTS_SYNC = ProjectsSync()
    return _PROJECTS_SYNC


def _update_project_entry(**changes) -> None:
    data = _read_project_file()
    project = data.get("project") or {}
    for key, value in changes.items():
        if value is None:
            project.pop(key, None)
        else:
            project[key] = value
    data["project"] = project
    _write_project_file(data)
    reload_projects_sync()


def update_projects_enabled(enabled: bool) -> bool:
    _update_project_entry(enabled=bool(enabled))
    return bool(enabled)


def update_project_target(number: int) -> None:
    _update_project_entry(number=int(number))


def update_project_workers(workers: Optional[int]) -> None:
    if workers is None:
        _update_project_entry(workers=None)
    else:
        _update_project_entry(workers=int(workers))


def _git_repo_root() -> Path:
    global _REPO_ROOT_CACHE
    if _REPO_ROOT_CACHE and _REPO_ROOT_CACHE.exists():
        return _REPO_ROOT_CACHE
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise RuntimeError("текущая директория не является git-репозиторием") from exc
    root = Path(result.stdout.strip())
    if not root.exists():
        raise RuntimeError("git репозиторий не найден")
    _REPO_ROOT_CACHE = root
    return root


def detect_repo_slug() -> Tuple[str, str]:
    """Возвращает (owner, repo) для текущего git-репозитория."""
    global _REPO_SLUG_CACHE
    if _REPO_SLUG_CACHE:
        return _REPO_SLUG_CACHE
    repo_root: Optional[Path] = None
    env_root = os.environ.get("APPLY_TASK_PROJECT_ROOT")
    if env_root:
        candidate = Path(env_root).resolve()
        if (candidate / ".git").exists():
            repo_root = candidate
    if repo_root is None:
        repo_root = _git_repo_root()
    try:
        result = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            check=True,
            cwd=str(repo_root),
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise RuntimeError("remote.origin.url не настроен") from exc
    url = result.stdout.strip()
    if not url:
        raise RuntimeError("remote.origin.url пустой")
    host = ""
    path = ""
    if url.startswith("git@"):
        try:
            host_part, path = url.split(":", 1)
            host = host_part.split("@", 1)[1]
        except ValueError as exc:
            raise RuntimeError("remote.origin.url имеет неверный формат") from exc
    else:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        path = parsed.path or ""
    host = host.lower()
    if host.endswith("github.com"):
        host = "github.com"
    if host != "github.com":
        raise RuntimeError("remote origin не указывает на github.com")
    path = path.lstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    if not path or "/" not in path:
        raise RuntimeError("remote origin не содержит owner/repo")
    owner, repo = path.split("/", 1)
    owner = owner.strip()
    repo = repo.strip("/")
    if not owner or not repo:
        raise RuntimeError("remote origin некорректен")
    _REPO_SLUG_CACHE = (owner, repo)
    return _REPO_SLUG_CACHE
