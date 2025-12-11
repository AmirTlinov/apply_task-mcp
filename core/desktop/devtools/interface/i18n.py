import os
from typing import Optional

from config import get_user_lang
from core.desktop.devtools.interface.constants import LANG_PACK


def _fill_lang_pack_defaults(base_lang: str = "en") -> None:
    """Backfill missing translations with English defaults."""
    base = LANG_PACK.get(base_lang, {})
    for lang, values in LANG_PACK.items():
        if lang == base_lang:
            continue
        for key, val in base.items():
            values.setdefault(key, val)


_fill_lang_pack_defaults()


def effective_lang(preferred: Optional[str] = None) -> str:
    """Resolve active language with env and test overrides."""
    env_lang = os.getenv("APPLY_TASK_LANG")
    if env_lang:
        return env_lang
    if os.getenv("PYTEST_CURRENT_TEST"):
        return "en"
    candidate = preferred or get_user_lang()
    return candidate if candidate in LANG_PACK else "en"


def translate(key: str, lang: Optional[str] = None, **kwargs) -> str:
    """Translate key using LANG_PACK with graceful fallback."""
    base = LANG_PACK.get("en", {})
    active_lang = effective_lang(lang)
    lang_map = LANG_PACK.get(active_lang, base)
    template = lang_map.get(key) or base.get(key, key)
    try:
        return template.format(**kwargs)
    except Exception:
        return template


__all__ = ["effective_lang", "translate"]
