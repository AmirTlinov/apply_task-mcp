from __future__ import annotations

import yaml
from pathlib import Path
from typing import Dict, Any

USER_CONFIG_PATH = Path.home() / ".apply_task_config.yaml"


def _load_config() -> Dict[str, Any]:
    if not USER_CONFIG_PATH.exists():
        return {}
    try:
        return yaml.safe_load(USER_CONFIG_PATH.read_text()) or {}
    except Exception:
        return {}


def _save_config(data: Dict[str, Any]) -> None:
    if not data:
        if USER_CONFIG_PATH.exists():
            USER_CONFIG_PATH.unlink()
        return
    USER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    USER_CONFIG_PATH.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def get_user_token() -> str:
    return _load_config().get("token", "")


def set_user_token(value: str) -> None:
    data = _load_config()
    value = value.strip()
    if value:
        data["token"] = value
    else:
        data.pop("token", None)
    _save_config(data)


def get_user_lang() -> str:
    return _load_config().get("lang", "").strip()


def set_user_lang(value: str) -> None:
    data = _load_config()
    value = (value or "").strip()
    if value:
        data["lang"] = value
    else:
        data.pop("lang", None)
    _save_config(data)
