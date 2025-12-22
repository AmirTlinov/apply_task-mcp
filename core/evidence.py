import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_SENSITIVE_KEYWORDS = {
    "token",
    "secret",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "authorization",
    "bearer",
}

_SENSITIVE_PATTERNS = [
    # GitHub tokens
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    # OpenAI-like keys
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    # Generic "BEGIN PRIVATE KEY" blocks (best-effort)
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
    # Authorization headers
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s]+"),
    # Querystring-style secrets
    re.compile(r"(?i)\b((?:token|apikey|api_key|secret|password)\s*=\s*)[^\s&;]+"),
]


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _redact_text(text: str) -> str:
    value = str(text or "")
    if not value:
        return ""
    out = value
    for pattern in _SENSITIVE_PATTERNS:
        out = pattern.sub(lambda m: "<redacted>" if m.lastindex is None else f"{m.group(1)}<redacted>", out)
    return out


def _redact(value: Any, *, depth: int = 6) -> Any:
    if depth <= 0:
        return value
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, list):
        return [_redact(v, depth=depth - 1) for v in value]
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for k, v in value.items():
            key = str(k)
            if any(tok in key.lower() for tok in _SENSITIVE_KEYWORDS):
                out[key] = "<redacted>"
            else:
                out[key] = _redact(v, depth=depth - 1)
        return out
    return value


def _digest_for_check(kind: str, spec: str, outcome: str, preview: str, details: Dict[str, Any]) -> str:
    payload = {"kind": kind, "spec": spec, "outcome": outcome, "preview": preview, "details": details}
    return _sha256_hex(_canonical_json(payload))


def _digest_for_attachment(kind: str, path: str, uri: str, external_uri: str, size: int, meta: Dict[str, Any]) -> str:
    payload = {"kind": kind, "path": path, "uri": uri, "external_uri": external_uri, "size": int(size or 0), "meta": meta}
    return _sha256_hex(_canonical_json(payload))


@dataclass
class VerificationCheck:
    kind: str
    spec: str
    outcome: str
    observed_at: str = ""
    digest: str = ""
    preview: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "kind": self.kind,
            "spec": self.spec,
            "outcome": self.outcome,
            "observed_at": self.observed_at or "",
            "digest": self.digest or "",
            "preview": self.preview or "",
            "details": dict(self.details or {}),
        }
        return {k: v for k, v in payload.items() if v not in ("", {}, None)}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VerificationCheck":
        if not isinstance(data, dict):
            raise ValueError("verification check must be object")
        raw_details = dict(data.get("details", {}) or {})
        details = _redact(raw_details)
        preview = _redact_text(str(data.get("preview", "") or "").strip())
        kind = str(data.get("kind", "") or "").strip()
        spec = str(data.get("spec", "") or "").strip()
        outcome = str(data.get("outcome", "") or "").strip()
        digest = str(data.get("digest", "") or "").strip()
        if not digest and (kind or spec or outcome or preview or details):
            digest = _digest_for_check(kind, spec, outcome, preview, details if isinstance(details, dict) else {})
        return cls(
            kind=kind,
            spec=spec,
            outcome=outcome,
            observed_at=str(data.get("observed_at", "") or "").strip() or _now_iso(),
            digest=digest,
            preview=preview,
            details=details if isinstance(details, dict) else {},
        )


@dataclass
class Attachment:
    kind: str
    path: str = ""
    uri: str = ""
    external_uri: str = ""
    size: int = 0
    digest: str = ""
    observed_at: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "kind": self.kind,
            "path": self.path,
            "uri": self.uri,
            "external_uri": self.external_uri,
            "size": int(self.size or 0),
            "digest": self.digest or "",
            "observed_at": self.observed_at or "",
            "meta": dict(self.meta or {}),
        }
        return {k: v for k, v in payload.items() if v not in ("", 0, {}, None)}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Attachment":
        if not isinstance(data, dict):
            raise ValueError("attachment must be object")
        raw_meta = dict(data.get("meta", {}) or {})
        meta = _redact(raw_meta)
        kind = str(data.get("kind", "") or "").strip()
        path = _redact_text(str(data.get("path", "") or "").strip())
        uri = _redact_text(str(data.get("uri", "") or "").strip())
        external_uri = _redact_text(str(data.get("external_uri", "") or "").strip())
        size = int(data.get("size", 0) or 0)
        digest = str(data.get("digest", "") or "").strip()
        if not digest and (kind or path or uri or external_uri or size or meta):
            digest = _digest_for_attachment(kind, path, uri, external_uri, size, meta if isinstance(meta, dict) else {})
        return cls(
            kind=kind,
            path=path,
            uri=uri,
            external_uri=external_uri,
            size=size,
            digest=digest,
            observed_at=str(data.get("observed_at", "") or "").strip() or _now_iso(),
            meta=meta if isinstance(meta, dict) else {},
        )
