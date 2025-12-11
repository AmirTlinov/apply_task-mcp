"""Small edit command extracted from tasks_app to cut complexity."""

from typing import List, Set

from core import TaskEvent, validate_dependencies, build_dependency_graph
from core.desktop.devtools.application.context import derive_domain_explicit, normalize_task_id
from core.desktop.devtools.application.task_manager import TaskManager
from core.desktop.devtools.interface.cli_io import structured_error, structured_response
from core.desktop.devtools.interface.serializers import task_to_dict


def _validate_deps_for_edit(
    task_id: str,
    new_deps: List[str],
    manager: TaskManager,
) -> tuple[bool, str, dict]:
    """Validate dependencies for edit operation.

    Returns:
        (is_valid, error_message, error_payload)
    """
    if not new_deps:
        return True, "", {}

    all_tasks = manager.list_all_tasks()
    existing_ids: Set[str] = {t.id for t in all_tasks}

    # Build graph excluding current task (we're replacing its deps)
    dep_graph = build_dependency_graph([
        (t.id, t.depends_on) for t in all_tasks if t.id != task_id
    ])

    errors, cycle = validate_dependencies(task_id, new_deps, existing_ids, dep_graph)

    if errors:
        return False, "Invalid dependencies", {"errors": [str(e) for e in errors]}
    if cycle:
        return False, "Circular dependency detected", {"cycle": cycle}

    return True, "", {}


def cmd_edit(args) -> int:
    manager = TaskManager()
    domain = derive_domain_explicit(getattr(args, "domain", ""), getattr(args, "phase", None), getattr(args, "component", None))
    task = manager.load_task(normalize_task_id(args.task_id), domain)
    if not task:
        return structured_error("edit", f"Задача {args.task_id} не найдена")
    if getattr(args, "description", None):
        task.description = args.description
    if getattr(args, "context", None):
        task.context = args.context
    if getattr(args, "tags", None):
        task.tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    if getattr(args, "priority", None):
        task.priority = args.priority
    if getattr(args, "phase", None):
        task.phase = args.phase
    if getattr(args, "component", None):
        task.component = args.component
    if getattr(args, "new_domain", None):
        task.domain = args.new_domain

    # Handle --depends-on (replace entire list)
    if getattr(args, "depends_on", None):
        new_deps = [d.strip() for d in args.depends_on.split(",") if d.strip()]
        is_valid, err_msg, err_payload = _validate_deps_for_edit(task.id, new_deps, manager)
        if not is_valid:
            return structured_error("edit", err_msg, payload=err_payload)
        # Track removed and added dependencies
        old_deps = set(task.depends_on)
        new_deps_set = set(new_deps)
        for dep_id in old_deps - new_deps_set:
            task.events.append(TaskEvent.dependency_resolved(dep_id))
        for dep_id in new_deps_set - old_deps:
            task.events.append(TaskEvent.dependency_added(dep_id))
        task.depends_on = new_deps

    # Handle --add-dep (add single dependency)
    if getattr(args, "add_dep", None):
        dep_id = args.add_dep.strip()
        if dep_id and dep_id not in task.depends_on:
            test_deps = task.depends_on + [dep_id]
            is_valid, err_msg, err_payload = _validate_deps_for_edit(task.id, test_deps, manager)
            if not is_valid:
                return structured_error("edit", err_msg, payload=err_payload)
            task.depends_on.append(dep_id)
            task.events.append(TaskEvent.dependency_added(dep_id))

    # Handle --remove-dep (remove single dependency)
    if getattr(args, "remove_dep", None):
        dep_id = args.remove_dep.strip()
        if dep_id in task.depends_on:
            task.depends_on.remove(dep_id)
            task.events.append(TaskEvent.dependency_resolved(dep_id))

    manager.save_task(task)
    payload = {"task": task_to_dict(task, include_subtasks=True)}
    return structured_response(
        "edit",
        status="OK",
        message=f"Задача {task.id} обновлена",
        payload=payload,
        summary=f"{task.id} updated",
    )


__all__ = ["cmd_edit"]
