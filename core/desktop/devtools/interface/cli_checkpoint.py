"""Checkpoint and bulk CLI handlers extracted from tasks_app."""

import json
from typing import Any, Dict, List, Optional, Tuple

from core import SubTask
from core.desktop.devtools.application.context import (
    save_last_task,
    resolve_task_reference,
    derive_domain_explicit,
)
from core.desktop.devtools.application.task_manager import TaskManager, _find_subtask_by_path, _flatten_subtasks
from core.desktop.devtools.interface.cli_io import structured_error, structured_response
from core.desktop.devtools.interface.i18n import translate
from core.desktop.devtools.interface.cli_interactive import prompt, is_interactive, subtask_flags
from core.desktop.devtools.interface.serializers import subtask_to_dict, task_to_dict
from core.desktop.devtools.interface.subtask_loader import SubtaskParseError, _load_input_source
from core.desktop.devtools.interface.cli_activity import write_activity_marker


def _parse_bulk_operations(raw: str) -> List[Dict[str, Any]]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SubtaskParseError(f"Невалидный JSON payload для bulk: {exc}") from exc
    if not isinstance(data, list):
        raise SubtaskParseError("Bulk payload должен быть массивом операций")
    cleaned = []
    for item in data:
        if not isinstance(item, dict):
            raise SubtaskParseError("Каждый элемент bulk payload должен быть объектом")
        cleaned.append(item)
    return cleaned


def cmd_bulk(args) -> int:
    manager = TaskManager()
    base_domain = derive_domain_explicit(getattr(args, "domain", ""), getattr(args, "phase", None), getattr(args, "component", None))
    default_task_id: Optional[str] = None
    default_task_domain: str = base_domain
    if getattr(args, "task", None):
        try:
            default_task_id, default_task_domain = resolve_task_reference(
                args.task,
                getattr(args, "domain", None),
                getattr(args, "phase", None),
                getattr(args, "component", None),
            )
        except ValueError as exc:
            return structured_error("bulk", str(exc))
    try:
        raw = _load_input_source(args.input, "bulk JSON payload")
        operations = _parse_bulk_operations(raw)
    except SubtaskParseError as exc:
        return structured_error("bulk", str(exc))
    results = []
    for op in operations:
        raw_task_spec = op.get("task") or op.get("task_id", "")
        op_domain = base_domain
        try:
            if raw_task_spec:
                task_id, op_domain = resolve_task_reference(
                    raw_task_spec,
                    getattr(args, "domain", None),
                    getattr(args, "phase", None),
                    getattr(args, "component", None),
                )
            elif default_task_id:
                task_id = default_task_id
                op_domain = default_task_domain
            else:
                task_id = ""
        except ValueError as exc:
            results.append({"task": raw_task_spec, "status": "ERROR", "message": str(exc)})
            continue
        index = op.get("index")
        if not task_id or not isinstance(index, int):
            results.append({"task": task_id, "index": index, "status": "ERROR", "message": "Укажи task/index"})
            continue
        entry_payload = {"task": task_id, "index": index}
        failed = False
        for checkpoint in ("criteria", "tests", "blockers"):
            spec = op.get(checkpoint)
            if spec is None:
                continue
            done = bool(spec.get("done", True))
            note = spec.get("note", "") or ""
            ok, msg = manager.update_subtask_checkpoint(task_id, index, checkpoint, done, note, op_domain, path=op.get("path"))
            if not ok:
                entry_payload["status"] = "ERROR"
                entry_payload["message"] = msg or f"Не удалось обновить {checkpoint}"
                failed = True
                break
        if failed:
            results.append(entry_payload)
            continue
        if op.get("complete"):
            ok, msg = manager.set_subtask(task_id, index, True, op_domain, path=op.get("path"))
            if not ok:
                entry_payload["status"] = "ERROR"
                entry_payload["message"] = msg or "Не удалось закрыть подзадачу"
                results.append(entry_payload)
                continue
        detail = manager.load_task(task_id, op_domain)
        save_last_task(task_id, op_domain)
        entry_payload["status"] = "OK"
        entry_payload["task_detail"] = task_to_dict(detail, include_subtasks=True) if detail else {"id": task_id}
        if detail and 0 <= index < len(detail.subtasks):
            entry_payload["subtask"] = subtask_to_dict(detail.subtasks[index])
            entry_payload["checkpoint_states"] = {
                "criteria": detail.subtasks[index].criteria_confirmed,
                "tests": detail.subtasks[index].tests_confirmed,
                "blockers": detail.subtasks[index].blockers_resolved,
            }
        results.append(entry_payload)
    # Write activity markers for all successful operations
    for r in results:
        if r.get("status") == "OK":
            write_activity_marker(r.get("task", ""), "bulk", subtask_path=str(r.get("index", "")), tasks_dir=getattr(manager, "tasks_dir", None))
    message = f"Выполнено операций: {sum(1 for r in results if r.get('status') == 'OK')}/{len(results)}"
    return structured_response(
        "bulk",
        status="OK",
        message=message,
        payload={"results": results},
        summary=message,
    )


def cmd_checkpoint(args) -> int:
    auto_mode = getattr(args, "auto", False)
    base_note = (getattr(args, "note", "") or "").strip()
    if not auto_mode and not is_interactive():
        return structured_error(
            "checkpoint",
            "Мастер чекпоинтов требует интерактивный терминал (или укажи --auto)",
        )
    try:
        task_id, domain = resolve_task_reference(
            getattr(args, "task_id", None),
            getattr(args, "domain", None),
            getattr(args, "phase", None),
            getattr(args, "component", None),
        )
    except ValueError as exc:
        return structured_error("checkpoint", str(exc))
    manager = TaskManager()
    detail = manager.load_task(task_id, domain)
    if not detail:
        return structured_error("checkpoint", f"Задача {task_id} не найдена")
    if not detail.subtasks:
        return structured_error("checkpoint", f"Задача {task_id} не содержит подзадач")

    def pick_path_and_subtask() -> Tuple[str, int, SubTask]:
        if getattr(args, "path", None):
            path = args.path
            st, _, _ = _find_subtask_by_path(detail.subtasks, path)
            if not st:
                raise ValueError("Неверный путь подзадачи")
            return path, int(path.split(".")[-1] or 0), st
        if args.subtask is not None:
            idx = args.subtask
            if idx < 0:
                raise ValueError("Индекс подзадачи неверный")
            if idx < len(detail.subtasks):
                return str(idx), idx, detail.subtasks[idx]
            raise ValueError("Индекс подзадачи неверный")
        if auto_mode:
            flat = _flatten_subtasks(detail.subtasks)
            for path, st in flat:
                if not st.completed:
                    return path, int(path.split(".")[-1] or 0), st
            return flat[-1][0], int(flat[-1][0].split(".")[-1] or 0), flat[-1][1]
        print("\n[Шаг 1] Выбор подзадачи (формат 0 или 0.1.2)")
        flat = _flatten_subtasks(detail.subtasks)
        for path, st in flat:
            flags = subtask_flags(st)
            glyphs = ''.join(['✓' if flags[k] else '·' for k in ("criteria", "tests", "blockers")])
            print(f"  {path}. [{glyphs}] {'[OK]' if st.completed else '[ ]'} {st.title}")
        while True:
            raw = prompt("Введите путь подзадачи", default="0")
            st, _, _ = _find_subtask_by_path(detail.subtasks, raw)
            if st:
                return raw, int(raw.split(".")[-1] or 0), st
            print("  [!] Недопустимый путь (используй 0.1.2)")

    try:
        path, subtask_index, subtask_obj = pick_path_and_subtask()
    except ValueError as exc:
        return structured_error("checkpoint", str(exc))

    checkpoint_labels = [
        ("criteria", translate("CHECKPOINT_CRITERIA")),
        ("tests", translate("CHECKPOINT_TESTS")),
        ("blockers", translate("CHECKPOINT_BLOCKERS")),
    ]
    operations: List[Dict[str, Any]] = []
    completed = False

    for checkpoint, label in checkpoint_labels:
        st = manager.load_task(task_id, domain)
        if not st:
            return structured_error("checkpoint", translate("ERR_TASK_UNAVAILABLE"))
        target, _, _ = _find_subtask_by_path(st.subtasks, path)
        if not target:
            return structured_error("checkpoint", translate("ERR_SUBTASK_NOT_FOUND"))
        attr_map = {
            "criteria": target.criteria_confirmed,
            "tests": target.tests_confirmed,
            "blockers": target.blockers_resolved,
        }
        if attr_map[checkpoint]:
            operations.append({"checkpoint": checkpoint, "state": "already"})
            continue
        if auto_mode or prompt(f"[{label}] отметить как выполнено?", default="y").lower() in ("y", "yes", "д", "да", ""):
            operations.append({"checkpoint": checkpoint, "state": "done", "note": base_note})
        else:
            operations.append({"checkpoint": checkpoint, "state": "skip"})

    for op in operations:
        if op["state"] == "skip":
            continue
        checkpoint = op["checkpoint"]
        note = op.get("note", "")
        ok, msg = manager.update_subtask_checkpoint(task_id, subtask_index, checkpoint, True, note, domain, path=path)
        if not ok:
            return structured_error("checkpoint", msg or f"Не удалось обновить {checkpoint}")

    if auto_mode or prompt(translate("CHECKPOINT_COMPLETE_PROMPT"), default="y").lower() in ("y", "yes", "д", "да", ""):
        ok, msg = manager.set_subtask(task_id, subtask_index, True, domain, path=path)
        if not ok:
            return structured_error("checkpoint", msg or translate("ERR_SUBTASK_COMPLETE"))
        completed = True

    detail = manager.load_task(task_id, domain)
    save_last_task(task_id, domain)
    write_activity_marker(task_id, "checkpoint", subtask_path=path, tasks_dir=getattr(manager, "tasks_dir", None))
    payload = {
        "task": task_to_dict(detail, include_subtasks=True) if detail else {"id": task_id},
        "subtask_path": path,
        "subtask": subtask_to_dict(subtask_obj),
        "operations": operations,
        "auto": auto_mode,
        "completed": completed,
        "subtask_index": subtask_index,
    }
    return structured_response(
        "checkpoint",
        status="OK",
        message=translate("CHECKPOINT_DONE"),
        payload=payload,
        summary=f"{task_id} subtask {path} checkpoints updated",
    )
