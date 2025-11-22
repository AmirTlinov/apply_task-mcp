import re
from pathlib import Path
from typing import List, Optional, Tuple

from core.desktop.devtools.interface.i18n import translate


def _sanitize_domain(domain: Optional[str]) -> str:
    if not domain:
        return ""
    candidate = Path(domain.strip("/"))
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(translate("ERR_INVALID_FOLDER"))
    return candidate.as_posix()


def save_last_task(task_id: str, domain: str = "") -> None:
    Path(".last").write_text(f"{task_id}@{domain}", encoding="utf-8")


def get_last_task() -> Tuple[Optional[str], Optional[str]]:
    last = Path(".last")
    if not last.exists():
        return None, None
    raw = last.read_text(encoding="utf-8").strip()
    if "@" in raw:
        tid, domain = raw.split("@", 1)
        return tid or None, domain or None
    return raw or None, None


def normalize_task_id(raw: str) -> str:
    value = raw.strip().upper()
    if re.match(r"^TASK-\d+$", value):
        num = int(value.split("-")[1])
        return f"TASK-{num:03d}"
    if value.isdigit():
        return f"TASK-{int(value):03d}"
    return value


def derive_domain_explicit(domain: Optional[str], phase: Optional[str], component: Optional[str]) -> str:
    """Build domain path from explicit domain or phase/component fallback."""
    if domain:
        return _sanitize_domain(domain)
    parts = []
    if phase:
        parts.append(phase.strip("/"))
    if component:
        parts.append(component.strip("/"))
    if not parts:
        return ""
    return _sanitize_domain("/".join(parts))


def derive_folder_explicit(domain: Optional[str], phase: Optional[str], component: Optional[str]) -> str:
    """Compatibility alias for legacy tests."""
    return derive_domain_explicit(domain, phase, component)


def resolve_task_reference(
    raw_task_id: Optional[str],
    domain: Optional[str],
    phase: Optional[str],
    component: Optional[str],
) -> Tuple[str, str]:
    """
    Return (task_id, domain) with shortcuts:
    '.' / 'last' / '@last' / empty → last task from .last.
    """
    sentinel = (raw_task_id or "").strip()
    use_last = not sentinel or sentinel in (".", "last", "@last")
    if use_last:
        last_id, last_domain = get_last_task()
        if not last_id:
            raise ValueError(translate("ERR_NO_LAST_TASK"))
        resolved_domain = derive_domain_explicit(domain, phase, component) or (last_domain or "")
        return normalize_task_id(last_id), resolved_domain or ""
    resolved_domain = derive_domain_explicit(domain, phase, component)
    return normalize_task_id(sentinel), resolved_domain


def parse_smart_title(title: str) -> Tuple[str, List[str], List[str]]:
    tags = re.findall(r"#(\w+)", title)
    deps = re.findall(r"@(TASK-\d+)", title.upper())
    clean = re.sub(r"#\w+", "", title)
    clean = re.sub(r"@TASK-\d+", "", clean, flags=re.IGNORECASE).strip()
    return clean, [t.lower() for t in tags], deps


__all__ = [
    "save_last_task",
    "get_last_task",
    "normalize_task_id",
    "derive_domain_explicit",
    "derive_folder_explicit",
    "resolve_task_reference",
    "parse_smart_title",
]
