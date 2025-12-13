#!/usr/bin/env python3
"""Core CLI commands."""

import argparse
from pathlib import Path
from typing import List, Optional

from core.desktop.devtools.application.task_manager import TaskManager
from core.desktop.devtools.application.context import (
    derive_domain_explicit,
    save_last_task,
    normalize_task_id,
)
from core.desktop.devtools.application.recommendations import next_recommendations
from core.desktop.devtools.interface.cli_commands import cmd_list as _cmd_list, cmd_show as _cmd_show, cmd_analyze as _cmd_analyze
from core.desktop.devtools.interface.cli_create import cmd_create as _cmd_create, cmd_smart_create as _cmd_smart_create
from core.desktop.devtools.interface.cli_subtask import cmd_subtask as _cmd_subtask
from core.desktop.devtools.interface.cli_checkpoint import cmd_bulk as _cmd_bulk, cmd_checkpoint as _cmd_checkpoint
from core.desktop.devtools.interface.cli_edit import cmd_edit as _cmd_edit
from core.desktop.devtools.interface.cli_io import structured_response, structured_error
from core.desktop.devtools.interface.i18n import translate
from core.desktop.devtools.interface.serializers import task_to_dict
from infrastructure.task_file_parser import TaskFileParser

from .tui_models import CLI_DEPS
from .cli_activity import write_activity_marker
from core.status import task_status_label


def cmd_list(args: argparse.Namespace) -> int:
    """List tasks (delegates to cli_commands)."""
    return _cmd_list(args, CLI_DEPS)


def cmd_show(args: argparse.Namespace) -> int:
    """Show task details (delegates to cli_commands)."""
    return _cmd_show(args, CLI_DEPS)


def cmd_create(args: argparse.Namespace) -> int:
    """Create task (delegates to cli_create)."""
    return _cmd_create(args)


def cmd_smart_create(args: argparse.Namespace) -> int:
    """Smart create task (delegates to cli_create)."""
    return _cmd_smart_create(args)


def cmd_create_guided(args: argparse.Namespace) -> int:
    """Create task via guided mode (delegates to cli_guided)."""
    from core.desktop.devtools.interface.cli_guided import cmd_create_guided as _impl
    return _impl(args)


def cmd_status_set(args: argparse.Namespace) -> int:
    """Set task status (TODO/ACTIVE/DONE)."""
    manager = TaskManager()
    status = args.status.upper()
    task_id = normalize_task_id(args.task_id)
    ok, error = manager.update_task_status(task_id, status, args.domain or "")
    if ok:
        detail = manager.load_task(task_id, args.domain or "")
        payload = {"task": task_to_dict(detail, include_subtasks=True) if detail else {"id": task_id}}
        write_activity_marker(task_id, "status-set", tasks_dir=getattr(manager, "tasks_dir", None))
        status_ui = task_status_label(status)
        return structured_response(
            "status-set",
            status="OK",
            message=f"{task_id} → {status_ui}",
            payload=payload,
            summary=f"{task_id} → {status_ui}",
        )
    payload = {"task_id": args.task_id, "domain": args.domain or "", "status": status}
    return structured_response(
        "status-set",
        status="ERROR",
        message=(error or {}).get("message", "Статус не обновлён"),
        payload=payload,
        exit_code=1,
    )


def cmd_analyze(args: argparse.Namespace) -> int:
    """Analyze tasks (delegates to cli_commands)."""
    return _cmd_analyze(args, CLI_DEPS)


def cmd_next(args: argparse.Namespace) -> int:
    """Get next recommended task."""
    manager = TaskManager()
    domain = derive_domain_explicit(getattr(args, "domain", ""), getattr(args, "phase", None), getattr(args, "component", None))
    filters = {
        "domain": domain or "",
        "phase": getattr(args, "phase", None) or "",
        "component": getattr(args, "component", None) or "",
    }
    tasks = manager.list_tasks(domain, skip_sync=True)
    payload, selected = next_recommendations(tasks, filters, remember=save_last_task, serializer=task_to_dict)
    filter_hint = f" (domain='{filters['domain'] or '-'}', phase='{filters['phase'] or '-'}', component='{filters['component'] or '-'}')"
    if not payload["candidates"]:
        return structured_response(
            "next",
            status="OK",
            message="Все задачи завершены" + filter_hint,
            payload=payload,
            summary="Нет незавершённых задач",
        )
    primary = selected or tasks[0]
    return structured_response(
        "next",
        status="OK",
        message="Рекомендации обновлены" + filter_hint,
        payload=payload,
        summary=f"Выбрано {primary.id}",
    )


def _parse_semicolon_list(raw: Optional[str]) -> List[str]:
    """Parse semicolon-separated list."""
    if not raw:
        return []
    return [item.strip() for item in raw.split(";") if item.strip()]


def cmd_add_subtask(args: argparse.Namespace) -> int:
    """Add subtask to task."""
    manager = TaskManager()
    domain = derive_domain_explicit(getattr(args, "domain", ""), getattr(args, "phase", None), getattr(args, "component", None))
    task_id = normalize_task_id(args.task_id)
    criteria = _parse_semicolon_list(args.criteria)
    tests = _parse_semicolon_list(args.tests)
    blockers = _parse_semicolon_list(args.blockers)
    if not args.subtask or len(args.subtask.strip()) < 20:
        return structured_error("add-subtask", translate("ERR_SUBTASK_TITLE_MIN"))
    ok, err = manager.add_subtask(task_id, args.subtask.strip(), domain, criteria, tests, blockers)
    if ok:
        payload = {"task_id": task_id, "subtask": args.subtask.strip()}
        write_activity_marker(task_id, "add-subtask", tasks_dir=getattr(manager, "tasks_dir", None))
        return structured_response(
            "add-subtask",
            status="OK",
            message=f"Подзадача добавлена в {task_id}",
            payload=payload,
            summary=f"{task_id} +subtask",
        )
    if err == "missing_fields":
        return structured_error(
            "add-subtask",
            "Добавь критерии/тесты/блокеры: --criteria \"...\" --tests \"...\" --blockers \"...\" (через ';')",
            payload={"task_id": task_id},
        )
    return structured_error("add-subtask", f"Задача {task_id} не найдена", payload={"task_id": task_id})


def cmd_add_dependency(args: argparse.Namespace) -> int:
    """Add dependency to task."""
    manager = TaskManager()
    domain = derive_domain_explicit(getattr(args, "domain", ""), getattr(args, "phase", None), getattr(args, "component", None))
    task_id = normalize_task_id(args.task_id)
    ok = manager.add_dependency(task_id, args.dependency, domain)
    if ok:
        payload = {"task_id": task_id, "dependency": args.dependency}
        write_activity_marker(task_id, "add-dep", tasks_dir=getattr(manager, "tasks_dir", None))
        return structured_response(
            "add-dep",
            status="OK",
            message=f"Зависимость добавлена в {task_id}",
            payload=payload,
            summary=f"{task_id} +dep",
        )
    payload = {"task_id": task_id, "dependency": args.dependency}
    return structured_error("add-dep", "Не удалось добавить зависимость", payload=payload)


def cmd_subtask(args: argparse.Namespace) -> int:
    """Subtask command (delegates to cli_subtask)."""
    return _cmd_subtask(args)


def cmd_bulk(args: argparse.Namespace) -> int:
    """Bulk checkpoint command (delegates to cli_checkpoint)."""
    return _cmd_bulk(args)


def cmd_checkpoint(args: argparse.Namespace) -> int:
    """Checkpoint command (delegates to cli_checkpoint)."""
    return _cmd_checkpoint(args)


def cmd_move(args: argparse.Namespace) -> int:
    """Move task to another subfolder."""
    manager = TaskManager()
    if args.glob:
        count = manager.move_glob(args.glob, args.to)
        payload = {"glob": args.glob, "target": args.to, "moved": count}
        return structured_response(
            "move",
            status="OK",
            message=f"Перемещено задач: {count} в {args.to}",
            payload=payload,
            summary=f"{count} задач → {args.to}",
        )
    if not args.task_id:
        return structured_error("move", translate("ERR_TASK_ID_OR_GLOB"))
    task_id = normalize_task_id(args.task_id)
    if manager.move_task(task_id, args.to):
        save_last_task(task_id, args.to)
        payload = {"task_id": task_id, "target": args.to}
        return structured_response(
            "move",
            status="OK",
            message=f"{task_id} перемещена в {args.to}",
            payload=payload,
            summary=f"{task_id} → {args.to}",
        )
    return structured_error("move", f"Не удалось переместить {task_id}", payload={"task_id": task_id, "target": args.to})


def cmd_clean(args: argparse.Namespace) -> int:
    """Clean tasks by filters."""
    if not any([args.tag, args.status, args.phase, args.glob]):
        return structured_error("clean", translate("ERR_FILTER_REQUIRED"))
    manager = TaskManager()
    if args.glob:
        is_dry = args.dry_run
        base = manager.tasks_dir.resolve()
        matched = []
        for detail in manager.repo.list("", skip_sync=True):
            try:
                rel = Path(detail.filepath).resolve().relative_to(base)
            except Exception:
                continue
            if rel.match(args.glob):
                matched.append(detail.id)
        if is_dry:
            payload = {"mode": "dry-run", "matched": matched, "glob": args.glob}
            return structured_response(
                "clean",
                status="OK",
                message=f"Будут удалены {len(matched)} задач(и) по glob",
                payload=payload,
                summary=f"dry-run {len(matched)} задач",
            )
        removed = manager.repo.delete_glob(args.glob)
        payload = {"removed": removed, "matched": matched, "glob": args.glob}
        return structured_response(
            "clean",
            status="OK",
            message=f"Удалено задач: {removed} по glob {args.glob}",
            payload=payload,
            summary=f"Удалено {removed}",
        )
    matched, removed = manager.clean_tasks(tag=args.tag, status=args.status, phase=args.phase, dry_run=args.dry_run)
    if args.dry_run:
        payload = {
            "mode": "dry-run",
            "matched": matched,
            "filters": {"tag": args.tag, "status": args.status, "phase": args.phase},
        }
        return structured_response(
            "clean",
            status="OK",
            message=f"Будут удалены {len(matched)} задач(и)",
            payload=payload,
            summary=f"dry-run {len(matched)} задач",
        )
    payload = {
        "removed": removed,
        "matched": matched,
        "filters": {"tag": args.tag, "status": args.status, "phase": args.phase},
    }
    return structured_response(
        "clean",
        status="OK",
        message=f"Удалено задач: {removed}",
        payload=payload,
        summary=f"Удалено {removed}",
    )


def cmd_edit(args: argparse.Namespace) -> int:
    """Edit task (delegates to cli_edit)."""
    return _cmd_edit(args)


def cmd_lint(args: argparse.Namespace) -> int:
    """Lint tasks."""
    issues: List[str] = []
    tasks_dir = Path(".tasks")
    if not tasks_dir.exists():
        issues.append(".tasks каталог отсутствует")
    else:
        manager = TaskManager()
        for f in tasks_dir.rglob("TASK-*.task"):
            detail = TaskFileParser.parse(f)
            if not detail:
                issues.append(f"{f} не парсится")
                continue
            changed = False
            if not detail.description:
                issues.append(f"{f} без description")
            if not detail.success_criteria:
                issues.append(f"{f} без tests/success_criteria")
            if not detail.parent:
                detail.parent = detail.id
                changed = True
            if args.fix and changed:
                manager.save_task(detail)
    payload = {"issues": issues, "fix": bool(args.fix)}
    if issues:
        return structured_response(
            "lint",
            status="ERROR",
            message=f"Найдено {len(issues)} проблем(ы)",
            payload=payload,
            summary="Lint failed",
            exit_code=1,
        )
    return structured_response(
        "lint",
        status="OK",
        message="Lint OK",
        payload=payload,
        summary="Lint clean",
    )
