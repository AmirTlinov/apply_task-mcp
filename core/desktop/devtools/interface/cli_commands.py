from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from core.task_detail import TaskDetail
from core.desktop.devtools.interface.cli_io import structured_error, structured_response


TaskManagerFactory = Callable[[], Any]
Translate = Callable[[str], str]


@dataclass
class CliDeps:
    manager_factory: TaskManagerFactory
    translate: Translate
    derive_domain_explicit: Callable[..., str]
    resolve_task_reference: Callable[..., Any]
    save_last_task: Callable[[str, str], None]
    normalize_task_id: Callable[[str], str]
    task_to_dict: Callable[[TaskDetail, bool], Dict[str, Any]]


def _priority(task: TaskDetail) -> int:
    return {"HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(task.priority, 0)


def cmd_list(args, deps: CliDeps) -> int:
    manager = deps.manager_factory()
    domain = deps.derive_domain_explicit(getattr(args, "domain", ""), getattr(args, "phase", None), getattr(args, "component", None))
    tasks = manager.list_tasks(domain)
    if getattr(args, "status", None):
        tasks = [t for t in tasks if t.status == args.status]
    if getattr(args, "component", None):
        tasks = [t for t in tasks if t.component == args.component]
    if getattr(args, "phase", None):
        tasks = [t for t in tasks if t.phase == args.phase]
    payload = {
        "total": len(tasks),
        "filters": {
            "domain": domain or "",
            "phase": getattr(args, "phase", None) or "",
            "component": getattr(args, "component", None) or "",
            "status": getattr(args, "status", None) or "",
            "progress_details": bool(getattr(args, "progress", False)),
        },
        "tasks": [
            deps.task_to_dict(task, include_subtasks=bool(getattr(args, "progress", False)))
            for task in tasks
        ],
    }
    return structured_response(
        "list",
        status="OK",
        message=deps.translate("MSG_LIST_BUILT"),
        payload=payload,
        summary=deps.translate("SUMMARY_TASKS", count=len(tasks)),
    )


def cmd_show(args, deps: CliDeps) -> int:
    manager = deps.manager_factory()
    task_id = getattr(args, "task_id", None)
    if not task_id:
        try:
            task_id, domain = deps.resolve_task_reference(None, None, None, None)
        except ValueError:
            task_id, domain = None, None
    else:
        domain = deps.derive_domain_explicit(getattr(args, "domain", ""), getattr(args, "phase", None), getattr(args, "component", None)) or ""
    if not task_id:
        return structured_error("show", deps.translate("ERR_SHOW_NO_TASK"))
    detail = manager.load_task(deps.normalize_task_id(task_id), domain or "")
    if not detail:
        return structured_error("show", deps.translate("ERR_TASK_NOT_FOUND", task_id=task_id))
    deps.save_last_task(detail.id, detail.domain)
    payload = {"task": deps.task_to_dict(detail, include_subtasks=True)}
    return structured_response(
        "show",
        status="OK",
        message=deps.translate("MSG_TASK_DETAILS"),
        payload=payload,
        summary=f"{detail.id}: {detail.title}",
    )


def cmd_analyze(args, deps: CliDeps) -> int:
    manager = deps.manager_factory()
    domain = deps.derive_domain_explicit(getattr(args, "domain", ""), getattr(args, "phase", None), getattr(args, "component", None))
    task = manager.load_task(deps.normalize_task_id(args.task_id), domain)
    if not task:
        return structured_error("analyze", f"Задача {args.task_id} не найдена")
    payload = {
        "task": deps.task_to_dict(task, include_subtasks=True),
        "progress": task.calculate_progress(),
        "subtasks_completed": sum(1 for st in task.subtasks if st.completed),
    }
    if not task.subtasks:
        payload["tip"] = "Добавь подзадачи через apply_task subtask TASK --add ..."
    return structured_response(
        "analyze",
        status=task.status,
        message="Анализ завершён",
        payload=payload,
        summary=f"{task.id}: {task.title}",
    )


def cmd_next(args, deps: CliDeps) -> int:
    manager = deps.manager_factory()
    domain = deps.derive_domain_explicit(getattr(args, "domain", ""), getattr(args, "phase", None), getattr(args, "component", None))
    filters = {
        "domain": domain or "",
        "phase": getattr(args, "phase", None) or "",
        "component": getattr(args, "component", None) or "",
    }
    tasks = manager.list_tasks(domain, skip_sync=True)
    candidates = [t for t in tasks if t.status != "OK" and t.calculate_progress() < 100]
    filter_hint = f" (domain='{filters['domain'] or '-'}', phase='{filters['phase'] or '-'}', component='{filters['component'] or '-'}')"
    if not candidates:
        payload = {"filters": filters, "candidates": []}
        return structured_response(
            "next",
            status="OK",
            message="Все задачи завершены" + filter_hint,
            payload=payload,
            summary="Нет незавершённых задач",
        )
    candidates.sort(key=lambda t: (-1 if t.blocked else 0, -_priority(t), t.calculate_progress()))
    top = candidates[:3]
    deps.save_last_task(candidates[0].id, candidates[0].domain)
    payload = {"filters": filters, "candidates": [deps.task_to_dict(t) for t in top], "selected": deps.task_to_dict(candidates[0])}
    return structured_response(
        "next",
        status="OK",
        message="Рекомендации обновлены" + filter_hint,
        payload=payload,
        summary=f"Выбрано {candidates[0].id}",
    )


def cmd_suggest(args, deps: CliDeps) -> int:
    manager = deps.manager_factory()
    folder = getattr(args, "folder", "") or ""
    domain = deps.derive_domain_explicit(getattr(args, "domain", "") or folder, getattr(args, "phase", None), getattr(args, "component", None))
    filters = {
        "folder": folder or "",
        "domain": domain or "",
        "phase": getattr(args, "phase", None) or "",
        "component": getattr(args, "component", None) or "",
    }
    tasks = manager.list_tasks(domain, skip_sync=True)
    active = [t for t in tasks if t.status != "OK"]
    filter_hint = f" (folder='{folder or domain or '-'}', phase='{filters['phase'] or '-'}', component='{filters['component'] or '-'}')"
    if not active:
        payload = {"filters": filters, "suggestions": []}
        return structured_response(
            "suggest",
            status="OK",
            message="Все задачи завершены" + filter_hint,
            payload=payload,
            summary="Нет задач для рекомендации",
        )
    sorted_tasks = sorted(active, key=lambda t: (-_priority(t), t.calculate_progress(), len(t.dependencies)))
    deps.save_last_task(sorted_tasks[0].id, sorted_tasks[0].domain)
    payload = {"filters": filters, "suggestions": [deps.task_to_dict(task) for task in sorted_tasks[:5]]}
    return structured_response(
        "suggest",
        status="OK",
        message="Рекомендации сформированы" + filter_hint,
        payload=payload,
        summary=f"{len(payload['suggestions'])} рекомендаций",
    )


def cmd_quick(args, deps: CliDeps) -> int:
    manager = deps.manager_factory()
    folder = getattr(args, "folder", "") or ""
    domain = deps.derive_domain_explicit(getattr(args, "domain", "") or folder, getattr(args, "phase", None), getattr(args, "component", None))
    filters = {
        "folder": folder or "",
        "domain": domain or "",
        "phase": getattr(args, "phase", None) or "",
        "component": getattr(args, "component", None) or "",
    }
    tasks = [t for t in manager.list_tasks(domain, skip_sync=True) if t.status != "OK"]
    tasks.sort(key=lambda t: (t.priority, t.calculate_progress()))
    filter_hint = f" (folder='{folder or domain or '-'}', phase='{filters['phase'] or '-'}', component='{filters['component'] or '-'}')"
    if not tasks:
        payload = {"filters": filters, "top": []}
        return structured_response(
            "quick",
            status="OK",
            message="Все задачи выполнены" + filter_hint,
            payload=payload,
            summary="Нет задач",
        )
    top = tasks[:3]
    deps.save_last_task(tasks[0].id, tasks[0].domain)
    payload = {"filters": filters, "top": [deps.task_to_dict(task) for task in top]}
    return structured_response(
        "quick",
        status="OK",
        message="Быстрый обзор top-3" + filter_hint,
        payload=payload,
        summary=f"Top-{len(top)} задач",
    )
