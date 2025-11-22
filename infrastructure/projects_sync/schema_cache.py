import hashlib
import time
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Dict, Optional, Tuple

import yaml


class SchemaCache:
    """Token-aware cache for GitHub Projects schema."""

    def __init__(
        self,
        path: Path,
        ttl_seconds: int,
        token_getter: Callable[[], Optional[str]],
        data_ref: Optional[Dict[Tuple[str, str, str, int], Dict[str, Any]]] = None,
        lock: Optional[Lock] = None,
    ) -> None:
        self.path = path
        self.ttl_seconds = ttl_seconds
        self.token_getter = token_getter
        self._data = data_ref if data_ref is not None else {}
        self._lock = lock or Lock()
        self._loaded = False

    def _token_digest(self) -> str:
        token = self.token_getter() or ""
        return hashlib.sha1(token.encode()).hexdigest() if token else ""

    def _key_from_str(self, key_str: str) -> Optional[Tuple[str, str, str, int]]:
        parts = key_str.split("|")
        if len(parts) != 4:
            return None
        try:
            return parts[0], parts[1], parts[2], int(parts[3])
        except Exception:
            return None

    def load(self) -> Dict[Tuple[str, str, str, int], Dict[str, Any]]:
        with self._lock:
            if self._loaded:
                return dict(self._data)
            self._loaded = True
        if not self.path.exists():
            return dict(self._data)
        try:
            raw = yaml.safe_load(self.path.read_text()) or {}
        except Exception:
            return dict(self._data)
        meta = raw.get("__meta__") if isinstance(raw, dict) else {}
        stored_digest = meta.get("token") or ""
        if stored_digest and self._token_digest() and stored_digest != self._token_digest():
            self.path.unlink(missing_ok=True)
            with self._lock:
                self._data.clear()
            return {}
        ttl_override = meta.get("ttl_seconds") if isinstance(meta, dict) else None
        ttl_limit = (
            int(ttl_override)
            if isinstance(ttl_override, (int, float)) and int(ttl_override) > 0
            else self.ttl_seconds
        )
        now = time.time()
        for key_str, value in raw.items():
            if key_str == "__meta__":
                continue
            tuple_key = self._key_from_str(key_str)
            if not tuple_key:
                continue
            ts_val = value.get("ts")
            try:
                ts_val = float(ts_val) if ts_val is not None else None
            except Exception:
                ts_val = None
            if ts_val and now - ts_val > ttl_limit:
                continue
            with self._lock:
                self._data[tuple_key] = value
        return dict(self._data)

    def get(self, key: Tuple[str, str, str, int]) -> Optional[Dict[str, Any]]:
        self.load()
        with self._lock:
            value = self._data.get(key)
            return dict(value) if isinstance(value, dict) else None

    def set(self, key: Tuple[str, str, str, int], value: Dict[str, Any]) -> None:
        self.load()
        with self._lock:
            stored = dict(value)
            stored.setdefault("ts", time.time())
            self._data[key] = stored

    def snapshot(self) -> Dict[Tuple[str, str, str, int], Dict[str, Any]]:
        self.load()
        with self._lock:
            return {k: dict(v) for k, v in self._data.items()}

    def persist(self) -> None:
        self.load()
        with self._lock:
            if not self._data:
                if self.path.exists():
                    self.path.unlink()
                return
            data = {f"{k[0]}|{k[1]}|{k[2]}|{k[3]}": v for k, v in self._data.items()}
            data["__meta__"] = {
                "token": self._token_digest(),
                "ts": time.time(),
                "ttl_seconds": self.ttl_seconds,
            }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
