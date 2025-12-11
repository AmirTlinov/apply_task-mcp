"""CLI handlers for task creation to keep tasks_app slim."""

from typing import Any, Dict, List, Optional, Set

from core import TaskDetail, TaskEvent, validate_dependencies, build_dependency_graph
from core.desktop.devtools.application.task_manager import TaskManager
from core.desktop.devtools.interface.cli_io import structured_error, structured_response, validation_response
from core.desktop.devtools.interface.i18n import translate
from core.desktop.devtools.interface.serializers import task_to_dict
from core.desktop.devtools.interface.subtask_loader import (
    parse_subtasks_flexible,
    validate_flagship_subtasks,
    SubtaskParseError,
    load_subtasks_source,
)
from core.desktop.devtools.application.context import (
    save_last_task,
    derive_domain_explicit,
    normalize_task_id,
    parse_smart_title,
)
from core.desktop.devtools.interface.templates import load_template


def _fail(args, message: str, payload: Optional[Dict[str, Any]] = None, kind: str = "create") -> int:
    if getattr(args, "validate_only", False):
        return validation_response(kind, False, message, payload)
    return structured_error(kind, message, payload=payload)


def _success_preview(kind: str, task: TaskDetail, message: str = "") -> int:
    task_snapshot = task_to_dict(task, include_subtasks=True)
    payload = {"task": task_snapshot}
    msg = message or translate("MSG_VALIDATION_PASSED")
    return validation_response(kind, True, msg, payload)


def _validate_depends_on(
    task_id: str,
    depends_on: List[str],
    manager: TaskManager,
    args,
    kind: str = "create",
) -> Optional[int]:
    """Validate depends_on list: existence check and cycle detection."""
    if not depends_on:
        return None

    # Get all existing task IDs
    all_tasks = manager.list_all_tasks()
    existing_ids: Set[str] = {t.id for t in all_tasks}

    # Build dependency graph from existing tasks
    dep_graph = build_dependency_graph([(t.id, t.depends_on) for t in all_tasks])

    # Validate
    errors, cycle = validate_dependencies(task_id, depends_on, existing_ids, dep_graph)

    if errors:
        error_msgs = [str(e) for e in errors]
        return _fail(args, translate("ERR_INVALID_DEPS"), payload={"errors": error_msgs}, kind=kind)

    if cycle:
        return _fail(
            args,
            translate("ERR_CIRCULAR_DEP"),
            payload={"cycle": cycle},
            kind=kind,
        )

    return None


def _apply_common_fields(task: TaskDetail, args, manager: TaskManager) -> Optional[int]:
    task.description = (args.description or "").strip()
    if not task.description or task.description.upper() == "TBD":
        return _fail(args, translate("ERR_DESCRIPTION_REQUIRED"))

    task.context = args.context or ""
    if args.tags:
        task.tags = [t.strip() for t in args.tags.split(",") if t.strip()]

    if args.dependencies:
        deps = [dep.strip() for dep in args.dependencies.split(",") if dep.strip()]
        task.dependencies.extend(deps)

    # Handle --depends-on (task-level dependencies)
    if getattr(args, "depends_on", None):
        dep_ids = [d.strip() for d in args.depends_on.split(",") if d.strip()]
        err = _validate_depends_on(task.id, dep_ids, manager, args)
        if err:
            return err
        task.depends_on = dep_ids
        # Add event for each dependency
        for dep_id in dep_ids:
            task.events.append(TaskEvent.dependency_added(dep_id))

    if args.next_steps:
        for step in args.next_steps.split(";"):
            if step.strip():
                task.next_steps.append(step.strip())

    if args.tests:
        for t in args.tests.split(";"):
            if t.strip():
                task.success_criteria.append(t.strip())

    if args.risks:
        for r in args.risks.split(";"):
            if r.strip():
                task.risks.append(r.strip())

    return None


def cmd_create(args) -> int:
    manager = TaskManager()
    args.parent = normalize_task_id(args.parent)
    domain = derive_domain_explicit(getattr(args, "domain", ""), getattr(args, "phase", None), getattr(args, "component", None))

    task = manager.create_task(
        args.title,
        status=args.status,
        priority=args.priority,
        parent=args.parent,
        domain=domain,
        phase=args.phase or "",
        component=args.component or "",
    )

    # Add created event
    task.events.append(TaskEvent.created())

    # общие поля
    err = _apply_common_fields(task, args, manager)
    if err:
        return err

    if args.subtasks:
        try:
            subtasks_payload = load_subtasks_source(args.subtasks)
            task.subtasks = parse_subtasks_flexible(subtasks_payload)
        except SubtaskParseError as e:
            return _fail(args, str(e))

    # обязательные поля
    if not task.success_criteria:
        return _fail(args, translate("ERR_TESTS_REQUIRED"))
    if not task.risks:
        return _fail(args, translate("ERR_RISKS_REQUIRED"))

    flagship_ok, flagship_issues = validate_flagship_subtasks(task.subtasks)
    if not flagship_ok:
        payload = {
            "issues": flagship_issues,
            "requirements": [
                translate("REQ_MIN_SUBTASKS"),
                translate("REQ_MIN_TITLE"),
                translate("REQ_EXPLICIT_CHECKPOINTS"),
                translate("REQ_ATOMIC"),
            ],
        }
        return _fail(args, translate("ERR_FLAGSHIP_SUBTASKS"), payload=payload)

    task.update_status_from_progress()
    if getattr(args, "validate_only", False):
        return _success_preview("create", task)
    manager.save_task(task)
    save_last_task(task.id, task.domain)
    payload = {"task": task_to_dict(task, include_subtasks=True)}
    return structured_response(
        "create",
        status="OK",
        message=translate("MSG_TASK_CREATED", task_id=task.id),
        payload=payload,
        summary=f"{task.id}: {task.title}",
    )


def cmd_smart_create(args) -> int:
    if not args.parent:
        return structured_error("task", translate("ERR_PARENT_REQUIRED"))
    manager = TaskManager()
    title, auto_tags, auto_deps = parse_smart_title(args.title)
    args.parent = normalize_task_id(args.parent)

    domain = derive_domain_explicit(getattr(args, "domain", ""), getattr(args, "phase", None), getattr(args, "component", None))
    task = manager.create_task(
        title,
        status=args.status,
        priority=args.priority,
        parent=args.parent,
        domain=domain,
        phase=args.phase or "",
        component=args.component or "",
    )

    # Add created event
    task.events.append(TaskEvent.created())

    err = _apply_common_fields(task, args, manager)
    if err:
        return err

    task.tags = [t.strip() for t in args.tags.split(",")] if args.tags else auto_tags
    deps = [d.strip() for d in args.dependencies.split(",")] if args.dependencies else auto_deps
    task.dependencies = deps

    template_desc, template_tests = load_template(task.tags[0] if task.tags else "default", manager)
    if not task.description:
        task.description = template_desc
    if args.tests:
        task.success_criteria = [t.strip() for t in args.tests.split(";") if t.strip()]
    elif template_tests:
        task.success_criteria = [template_tests]
    if not task.success_criteria:
        return _fail(args, translate("ERR_TESTS_REQUIRED"))
    if args.risks:
        task.risks = [r.strip() for r in args.risks.split(";") if r.strip()]
    if not task.risks:
        return _fail(args, translate("ERR_RISKS_REQUIRED"))

    if args.subtasks:
        try:
            subtasks_payload = load_subtasks_source(args.subtasks)
            task.subtasks = parse_subtasks_flexible(subtasks_payload)
        except SubtaskParseError as e:
            return _fail(args, str(e))

    flagship_ok, flagship_issues = validate_flagship_subtasks(task.subtasks)
    if not flagship_ok:
        payload = {
            "issues": flagship_issues,
            "requirements": [
                translate("REQ_MIN_SUBTASKS"),
                translate("REQ_MIN_TITLE"),
                translate("REQ_EXPLICIT_CHECKPOINTS"),
                translate("REQ_ATOMIC"),
            ],
        }
        return _fail(args, translate("ERR_FLAGSHIP_SUBTASKS"), payload=payload)

    task.update_status_from_progress()
    if getattr(args, "validate_only", False):
        return _success_preview("task", task)
    manager.save_task(task)
    save_last_task(task.id, task.domain)
    payload = {"task": task_to_dict(task, include_subtasks=True)}
    return structured_response(
        "task",
        status="OK",
        message=translate("MSG_TASK_CREATED", task_id=task.id),
        payload=payload,
        summary=f"{task.id}: {task.title}",
    )
