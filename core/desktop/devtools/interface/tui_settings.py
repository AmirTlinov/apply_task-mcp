"""Settings panel helpers for TaskTrackerTUI."""

from typing import Any, Dict, List


def _status_option(tui, snapshot):
    status_reason = snapshot.get("status_reason") or "n/a"
    status_line = tui._t("SETTINGS_STATUS_ON") if snapshot.get("runtime_enabled") else tui._t("SETTINGS_STATUS_OFF").format(reason=status_reason)
    return {
        "label": tui._t("SETTINGS_STATUS_LABEL"),
        "value": status_line,
        "hint": snapshot.get("status_reason") or tui._t("SETTINGS_STATUS_HINT"),
        "action": None,
    }


def _pat_option(tui, snapshot):
    if snapshot["token_saved"]:
        pat_value = tui._t("SETTINGS_PAT_SAVED").format(preview=snapshot["token_preview"])
    elif snapshot["token_env"]:
        pat_value = tui._t("SETTINGS_PAT_ENV").format(env=snapshot["token_env"])
    else:
        pat_value = tui._t("SETTINGS_PAT_NOT_SET")
    return {
        "label": tui._t("SETTINGS_PAT_LABEL"),
        "value": pat_value,
        "hint": tui._t("SETTINGS_PAT_HINT"),
        "action": "edit_pat",
    }


def _sync_option(tui, snapshot):
    if not snapshot["config_exists"]:
        sync_value = tui._t("SETTINGS_SYNC_NO_REMOTE")
    elif not snapshot["config_enabled"]:
        sync_value = tui._t("SETTINGS_SYNC_DISABLED")
    elif not snapshot["token_active"]:
        sync_value = tui._t("SETTINGS_SYNC_NO_PAT")
    else:
        sync_value = tui._t("SETTINGS_SYNC_ENABLED")
    return {
        "label": tui._t("SETTINGS_SYNC_LABEL"),
        "value": sync_value,
        "hint": tui._t("SETTINGS_SYNC_HINT"),
        "action": "toggle_sync",
        "disabled": not snapshot["config_exists"],
        "disabled_msg": snapshot.get("status_reason") if not snapshot["config_exists"] else "",
    }


def _target_option(tui, snapshot):
    target_value = snapshot["target_label"]
    target_hint = snapshot["target_hint"]
    if not snapshot["config_exists"]:
        target_value = tui._t("SETTINGS_PROJECT_UNAVAILABLE")
        target_hint = snapshot["status_reason"] or tui._t("STATUS_MESSAGE_PROJECT_URL_UNAVAILABLE")
    elif snapshot["status_reason"] and not snapshot["config_enabled"]:
        target_hint = snapshot["status_reason"]
    return {
        "label": tui._t("SETTINGS_PROJECT_LABEL"),
        "value": target_value,
        "hint": target_hint,
        "action": None,
    }


def _maybe_bootstrap_option(tui, snapshot):
    status_reason_lower = snapshot["status_reason"].lower() if snapshot.get("status_reason") else ""
    if (
        not snapshot["config_exists"]
        or status_reason_lower.startswith("нет конфигурации")
        or "remote origin" in status_reason_lower
    ):
        return {
            "label": tui._t("SETTINGS_BOOTSTRAP_LABEL"),
            "value": tui._t("SETTINGS_BOOTSTRAP_VALUE"),
            "hint": tui._t("SETTINGS_BOOTSTRAP_HINT"),
            "action": "bootstrap_git",
        }
    return None


def _project_url_option(tui, snapshot):
    return {
        "label": tui._t("SETTINGS_PROJECT_URL_LABEL"),
        "value": snapshot.get("project_url") or tui._t("SETTINGS_PROJECT_UNAVAILABLE"),
        "hint": tui._t("SETTINGS_PROJECT_URL_HINT"),
        "action": None,
    }


def _project_number_option(tui, snapshot):
    return {
        "label": tui._t("SETTINGS_PROJECT_NUMBER_LABEL"),
        "value": str(snapshot["number"]) if snapshot["number"] else "—",
        "hint": tui._t("SETTINGS_PROJECT_NUMBER_HINT"),
        "action": "edit_number",
    }


def _workers_option(tui, snapshot):
    return {
        "label": tui._t("SETTINGS_POOL_LABEL"),
        "value": str(snapshot.get("workers")) if snapshot.get("workers") else "auto",
        "hint": tui._t("SETTINGS_POOL_HINT"),
        "action": "edit_workers",
    }


def _last_pull_option(tui, snapshot):
    return {
        "label": tui._t("SETTINGS_LAST_PULL_LABEL"),
        "value": f"{snapshot.get('last_pull') or '—'} / {snapshot.get('last_push') or '—'}",
        "hint": tui._t("SETTINGS_LAST_PULL_HINT"),
        "action": None,
    }


def _rate_option(tui, snapshot):
    rate_value = tui._t("VALUE_NOT_AVAILABLE")
    if snapshot.get("rate_remaining") is not None:
        rate_value = f"{snapshot['rate_remaining']} @ {snapshot.get('rate_reset_human') or '—'}"
        if snapshot.get("rate_wait"):
            rate_value = f"{rate_value} wait={int(snapshot['rate_wait'])}s"
    return {
        "label": tui._t("SETTINGS_RATE_LABEL"),
        "value": rate_value,
        "hint": tui._t("SETTINGS_RATE_HINT"),
        "action": None,
    }


def _remote_option(tui, snapshot):
    return {
        "label": tui._t("SETTINGS_REMOTE_LABEL"),
        "value": snapshot.get("origin_url") or tui._t("SETTINGS_PAT_NOT_SET"),
        "hint": tui._t("SETTINGS_REMOTE_HINT"),
        "action": None,
    }


def _refresh_option(tui, snapshot):
    return {
        "label": tui._t("SETTINGS_REFRESH_LABEL"),
        "value": tui._t("SETTINGS_REFRESH_VALUE"),
        "hint": tui._t("SETTINGS_REFRESH_HINT"),
        "action": "refresh_metadata",
        "disabled": not (snapshot["config_exists"] and snapshot["token_active"]),
        "disabled_msg": tui._t("SETTINGS_REFRESH_DISABLED"),
    }


def _validate_pat_option(tui, snapshot):
    return {
        "label": tui._t("SETTINGS_VALIDATE_PAT_LABEL"),
        "value": tui.pat_validation_result or "GitHub viewer",
        "hint": tui._t("SETTINGS_VALIDATE_PAT_HINT"),
        "action": "validate_pat",
        "disabled": not (snapshot["token_saved"] or snapshot["token_env"]),
        "disabled_msg": tui._t("SETTINGS_VALIDATE_PAT_DISABLED"),
    }


def _language_option(tui):
    return {
        "label": f"{tui._t('LANGUAGE_LABEL')} / Language",
        "value": tui.language,
        "hint": tui._t("LANGUAGE_HINT"),
        "action": "cycle_lang",
    }


def build_settings_options(tui) -> List[Dict[str, Any]]:
    """Return settings options list based on current snapshot."""
    snapshot = tui._project_config_snapshot()
    options: List[Dict[str, Any]] = [
        _status_option(tui, snapshot),
        _pat_option(tui, snapshot),
        _sync_option(tui, snapshot),
        _target_option(tui, snapshot),
    ]

    bootstrap = _maybe_bootstrap_option(tui, snapshot)
    if bootstrap:
        options.append(bootstrap)

    options.extend(
        [
            _project_url_option(tui, snapshot),
            _project_number_option(tui, snapshot),
            _workers_option(tui, snapshot),
            _last_pull_option(tui, snapshot),
            _rate_option(tui, snapshot),
            _remote_option(tui, snapshot),
            _refresh_option(tui, snapshot),
            _validate_pat_option(tui, snapshot),
            _language_option(tui),
        ]
    )
    return options


__all__ = ["build_settings_options"]
