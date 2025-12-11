"""CLI handler for subtask operations to keep tasks_app slimmer."""

from typing import Any, Dict, List, Optional

from core import TaskDetail
from core.desktop.devtools.application.task_manager import TaskManager, _find_subtask_by_path
from core.desktop.devtools.interface.cli_io import structured_response, structured_error
from core.desktop.devtools.interface.i18n import translate
from core.desktop.devtools.interface.serializers import subtask_to_dict, task_to_dict
from core.desktop.devtools.application.context import derive_domain_explicit, normalize_task_id
from core.desktop.devtools.interface.cli_activity import write_activity_marker


def _parse_semicolon_list(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(";") if item.strip()]


def cmd_subtask(args) -> int:
    """Manage subtasks: add / mark done / undo checkpoints."""
    manager = TaskManager()
    task_id = normalize_task_id(args.task_id)
    domain_arg = getattr(args, "domain", "")
    domain = derive_domain_explicit(domain_arg, getattr(args, "phase", None), getattr(args, "component", None))
    actions = [
        ("add", bool(args.add)),
        ("done", args.done is not None),
        ("undo", args.undo is not None),
        ("criteria_done", args.criteria_done is not None),
        ("criteria_undo", args.criteria_undo is not None),
        ("tests_done", args.tests_done is not None),
        ("tests_undo", args.tests_undo is not None),
        ("blockers_done", args.blockers_done is not None),
        ("blockers_undo", args.blockers_undo is not None),
    ]
    active = [name for name, flag in actions if flag]
    if len(active) != 1:
        return structured_error(
            "subtask",
            "Укажи ровно одно действие: --add | --done | --undo | --criteria-done | --tests-done | --blockers-done (и соответствующие --undo)",
            payload={"actions": active},
        )

    action = active[0]

    def _snapshot(index: Optional[int] = None, path: Optional[str] = None) -> Dict[str, Any]:
        detail: Optional[TaskDetail] = manager.load_task(task_id, domain)
        payload: Dict[str, Any] = {"task_id": task_id}
        if detail:
            payload["task"] = task_to_dict(detail, include_subtasks=True)
            if path:
                payload["path"] = path
                target, _, _ = _find_subtask_by_path(detail.subtasks, path)
                if target:
                    payload["subtask"] = {"path": path, **subtask_to_dict(target)}
            if index is not None and 0 <= index < len(detail.subtasks) and "subtask" not in payload:
                payload["subtask"] = {"index": index, **subtask_to_dict(detail.subtasks[index])}
        return payload

    if action == "add":
        criteria = _parse_semicolon_list(args.criteria)
        tests = _parse_semicolon_list(args.tests)
        blockers = _parse_semicolon_list(args.blockers)
        if not args.add or len(args.add.strip()) < 20:
            return structured_error("subtask", translate("ERR_SUBTASK_TITLE_MIN"))
        ok, err = manager.add_subtask(task_id, args.add.strip(), domain, criteria, tests, blockers, parent_path=args.path)
        if ok:
            payload = _snapshot(path=args.path)
            payload["operation"] = "add"
            payload["subtask_title"] = args.add.strip()
            write_activity_marker(task_id, "subtask-add", subtask_path=args.path, tasks_dir=getattr(manager, "tasks_dir", None))
            return structured_response(
                "subtask",
                status="OK",
                message=f"Subtask added to {task_id}",
                payload=payload,
                summary=f"{task_id} +subtask",
            )
        if err == "missing_fields":
            return structured_error(
                "subtask",
                "Add criteria/tests/blockers: --criteria \"...\" --tests \"...\" --blockers \"...\" (semicolon-separated)",
                payload={"task_id": task_id},
            )
        return structured_error("subtask", translate("ERR_TASK_NOT_FOUND", task_id=task_id), payload={"task_id": task_id})

    if action in {"done", "undo"}:
        target_idx = args.done if action == "done" else args.undo
        desired = action == "done"
        ok, msg = manager.set_subtask(task_id, target_idx, desired, domain, path=args.path)
        if ok:
            payload = _snapshot(target_idx, path=args.path)
            payload["operation"] = action
            summary_suffix = "DONE" if desired else "UNDO"
            message = f"Подзадача {args.path or target_idx} " + ("отмечена выполненной" if desired else "возвращена в работу") + f" в {task_id}"
            write_activity_marker(task_id, f"subtask-{action}", subtask_path=args.path or str(target_idx), tasks_dir=getattr(manager, "tasks_dir", None))
            return structured_response(
                "subtask",
                status="OK",
                message=message,
                payload=payload,
                summary=f"{task_id} subtask#{args.path or target_idx} {summary_suffix}",
            )
        if msg == "not_found":
            return structured_error("subtask", f"Задача {task_id} не найдена", payload={"task_id": task_id})
        if msg == "index":
            return structured_error("subtask", translate("ERR_SUBTASK_INDEX"), payload={"task_id": task_id})
        return structured_error("subtask", msg or "Операция не выполнена", payload={"task_id": task_id})

    note = (args.note or "").strip()
    if action.startswith("criteria"):
        ok, msg = manager.update_subtask_checkpoint(task_id, args.criteria_done if "done" in action else args.criteria_undo, "criteria", action.endswith("done"), note, domain, path=args.path)
    elif action.startswith("tests"):
        ok, msg = manager.update_subtask_checkpoint(task_id, args.tests_done if "done" in action else args.tests_undo, "tests", action.endswith("done"), note, domain, path=args.path)
    else:  # blockers*
        ok, msg = manager.update_subtask_checkpoint(task_id, args.blockers_done if "done" in action else args.blockers_undo, "blockers", action.endswith("done"), note, domain, path=args.path)

    if ok:
        labels = {
            "criteria_done": "Критерии подтверждены",
            "criteria_undo": "Критерии возвращены в работу",
            "tests_done": "Тесты подтверждены",
            "tests_undo": "Тесты возвращены в работу",
            "blockers_done": "Блокеры сняты",
            "blockers_undo": "Блокеры возвращены",
        }
        index_map = {
            "criteria_done": args.criteria_done,
            "criteria_undo": args.criteria_undo,
            "tests_done": args.tests_done,
            "tests_undo": args.tests_undo,
            "blockers_done": args.blockers_done,
            "blockers_undo": args.blockers_undo,
        }
        payload = _snapshot(index_map.get(action), path=args.path)
        payload["operation"] = action
        if note:
            payload["note"] = note
        write_activity_marker(task_id, f"subtask-{action}", subtask_path=args.path or str(index_map.get(action, 0)), tasks_dir=getattr(manager, "tasks_dir", None))
        return structured_response(
            "subtask",
            status="OK",
            message=labels.get(action, action),
            payload=payload,
            summary=f"{task_id} {labels.get(action, action)}",
        )

    if msg == "not_found":
        return structured_error("subtask", f"Задача {task_id} не найдена", payload={"task_id": task_id})
    if msg == "index":
        return structured_error("subtask", translate("ERR_SUBTASK_INDEX"), payload={"task_id": task_id})
    return structured_error("subtask", msg or "Операция не выполнена", payload={"task_id": task_id})
