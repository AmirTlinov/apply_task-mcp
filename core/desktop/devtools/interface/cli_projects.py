#!/usr/bin/env python3
"""GitHub Projects CLI commands."""

import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

from core.desktop.devtools.application.task_manager import TaskManager
from core.desktop.devtools.application.context import derive_domain_explicit
from core.desktop.devtools.interface.cli_io import structured_response, structured_error
from core.desktop.devtools.interface.i18n import translate
from core.desktop.devtools.interface.subtask_loader import _load_input_source
from projects_sync import update_projects_enabled, reload_projects_sync, update_project_workers
from config import set_user_token
from core.desktop.devtools.application import projects_status_cache
from util.sync_status import sync_status_fragments

from .projects_integration import (
    _get_sync_service,
    _projects_status_payload,
    _invalidate_projects_status_cache,
)


def cmd_projects_auth(args: argparse.Namespace) -> int:
    """Set or clear GitHub PAT token."""
    if args.unset:
        set_user_token("")
        _invalidate_projects_status_cache()
        return structured_response(
            "projects-auth",
            status="OK",
            message="PAT cleared",
            payload={"token": None},
        )
    if not args.token:
        return structured_error("projects-auth", translate("ERR_TOKEN_OR_UNSET"))
    set_user_token(args.token)
    _invalidate_projects_status_cache()
    return structured_response(
        "projects-auth",
        status="OK",
        message="PAT saved",
        payload={"token": "***"},
    )


def cmd_projects_webhook(args: argparse.Namespace) -> int:
    """Handle GitHub Projects webhook payload."""
    sync_service = _get_sync_service()
    if not sync_service.enabled:
        return structured_error("projects-webhook", "Projects sync disabled (missing token or config)")
    body = _load_input_source(args.payload, "--payload")
    try:
        result = sync_service.handle_webhook(body, args.signature, args.secret)
    except ValueError as exc:
        return structured_error("projects-webhook", str(exc))
    if result and result.get("conflict"):
        return structured_response(
            "projects-webhook",
            status="CONFLICT",
            message="Конфликт: локальные правки новее удалённых",
            payload=result,
        )
    updated = bool(result and result.get("updated"))
    message = "Task updated" if updated else "No matching task"
    return structured_response(
        "projects-webhook",
        status="OK",
        message=message,
        payload=result or {"updated": False},
    )


def cmd_projects_webhook_serve(args: argparse.Namespace) -> int:
    """Serve GitHub Projects webhook HTTP endpoint."""
    sync_service = _get_sync_service()
    if not sync_service.enabled:
        return structured_error("projects-webhook-serve", "Projects sync disabled (missing token or config)")

    secret = args.secret

    class Handler(BaseHTTPRequestHandler):  # pragma: no cover - network entrypoint
        def do_POST(self_inner) -> None:
            length = int(self_inner.headers.get("Content-Length", "0"))
            raw = self_inner.rfile.read(length)
            signature = self_inner.headers.get("X-Hub-Signature-256")
            try:
                result = sync_service.handle_webhook(raw.decode(), signature, secret)
                if result and result.get("conflict"):
                    status = 409
                    payload = {"status": "conflict", **result}
                else:
                    status = 200
                    payload = result or {"updated": False}
            except ValueError as exc:
                status = 400
                payload = {"error": str(exc)}
            except Exception as exc:  # pragma: no cover
                status = 500
                payload = {"error": str(exc)}
            self_inner.send_response(status)
            self_inner.send_header("Content-Type", "application/json")
            self_inner.end_headers()
            self_inner.wfile.write(json.dumps(payload).encode())

        def log_message(self_inner, format: str, *args: str) -> None:
            return

    server = HTTPServer((args.host, args.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
    return 0


def cmd_projects_sync_cli(args: argparse.Namespace) -> int:
    """Manually sync tasks with GitHub Projects."""
    if not args.all:
        return structured_error("projects sync", "Укажи --all для явного подтверждения")
    sync_service = _get_sync_service()
    if not sync_service.enabled:
        status = _projects_status_payload()
        reason = status.get("status_reason") or "Projects sync отключён или не настроен"
        return structured_error("projects sync", reason)
    sync_service.consume_conflicts()
    manager = TaskManager()
    domain = derive_domain_explicit(getattr(args, "domain", ""), getattr(args, "phase", None), getattr(args, "component", None))
    tasks = manager.list_tasks(domain)
    pulled = pushed = 0
    for task in tasks:
        try:
            sync_service.pull_task_fields(task)
            pulled += 1
        except Exception:
            pass
        if sync_service.sync_task(task):
            pushed += 1
    conflicts = sync_service.consume_conflicts()
    payload = {
        "tasks": len(tasks),
        "pull_updates": pulled,
        "push_updates": pushed,
        "conflicts": conflicts,
    }
    conflict_suffix = f", конфликты={len(conflicts)}" if conflicts else ""
    projects_status_cache.invalidate_cache()
    return structured_response(
        "projects sync",
        status="OK",
        message=f"Синхронизация завершена ({pulled} pull / {pushed} push{conflict_suffix})",
        payload=payload,
        summary=f"{pulled} pull / {pushed} push{conflict_suffix}",
    )


def cmd_projects_status(args: argparse.Namespace) -> int:
    """Show GitHub Projects sync status."""
    payload = _projects_status_payload(force_refresh=True)
    fragments = sync_status_fragments(payload, payload["runtime_enabled"], flash=False, filter_flash=False)
    message = " ".join(text for _, text in fragments)
    return structured_response(
        "projects status",
        status="OK",
        message=message,
        payload=payload,
        summary=payload["target_label"],
    )


def cmd_projects_autosync(args: argparse.Namespace) -> int:
    """Enable or disable auto-sync with GitHub Projects."""
    desired = args.state.lower() == "on"
    update_projects_enabled(desired)
    reload_projects_sync()
    state_label = "включён" if desired else "выключен"
    payload = {"auto_sync": desired}
    _invalidate_projects_status_cache()
    return structured_response(
        "projects autosync",
        status="OK",
        message=f"Auto-sync {state_label}",
        payload=payload,
        summary=f"auto-sync {args.state}",
    )


def cmd_projects_workers(args: argparse.Namespace) -> int:
    """Set number of sync workers."""
    target = None if args.count == 0 else args.count
    update_project_workers(target)
    reload_projects_sync()
    label = "auto" if target is None else str(target)
    payload = {"workers": target}
    _invalidate_projects_status_cache()
    return structured_response(
        "projects workers",
        status="OK",
        message=f"Пул синхронизации установлен: {label}",
        payload=payload,
        summary=label,
    )
