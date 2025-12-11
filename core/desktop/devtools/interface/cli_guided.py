"""Guided creation flows extracted from tasks_app for lower complexity."""

import json
from pathlib import Path
from typing import List

from core.desktop.devtools.application.context import derive_domain_explicit, normalize_task_id, parse_smart_title, save_last_task
from core.desktop.devtools.application.task_manager import TaskManager
from core.desktop.devtools.interface.serializers import task_to_dict
from core.desktop.devtools.interface.i18n import translate
from core.desktop.devtools.interface.subtask_loader import validate_flagship_subtasks
from core.desktop.devtools.interface.cli_interactive import (
    confirm,
    is_interactive,
    prompt,
    prompt_list,
    prompt_required,
    prompt_subtask_interactive,
)


def cmd_create_guided(args) -> int:
    """Полуинтерактивное создание задачи (шаг-ответ-шаг)"""
    if not is_interactive():
        print(translate("GUIDED_ONLY_INTERACTIVE"))
        print(translate("GUIDED_USE_PARAMS"))
        return 1

    print("=" * 60)
    print(translate("GUIDED_TITLE"))
    print("=" * 60)

    manager = TaskManager()

    # Шаг 1: Базовая информация
    print(f"\n{translate('GUIDED_STEP1')}")
    title = prompt_required(translate("GUIDED_TASK_TITLE"))
    parent = prompt_required(translate("GUIDED_PARENT_ID"))
    parent = normalize_task_id(parent)
    description = prompt_required(translate("GUIDED_DESCRIPTION"))

    # Шаг 2: Контекст и метаданные
    print(f"\n{translate('GUIDED_STEP2')}")
    context = prompt(translate("GUIDED_CONTEXT"), default="")
    tags_str = prompt(translate("GUIDED_TAGS"), default="")
    tags = [t.strip() for t in tags_str.split(",") if t.strip()]

    # Шаг 3: Риски
    print(f"\n{translate('GUIDED_STEP3')}")
    risks = prompt_list(translate("GUIDED_RISKS"), min_items=1)

    # Шаг 4: Критерии успеха / Тесты
    print(f"\n{translate('GUIDED_STEP4')}")
    tests = prompt_list(translate("GUIDED_TESTS"), min_items=1)

    # Шаг 5: Подзадачи
    print(f"\n{translate('GUIDED_STEP5')}")
    subtasks: List = []
    for i in range(3):
        subtasks.append(prompt_subtask_interactive(i + 1))

    while confirm(translate("GUIDED_ADD_MORE"), default=False):
        subtasks.append(prompt_subtask_interactive(len(subtasks) + 1))

    # Валидация
    print(translate("GUIDED_VALIDATION"))
    flagship_ok, flagship_issues = validate_flagship_subtasks(subtasks)
    if not flagship_ok:
        print(translate("GUIDED_WARN_ISSUES"))
        for idx, issue in enumerate(flagship_issues, 1):
            print(f"  {idx}. {issue}")

        if not confirm(translate("GUIDED_CONTINUE"), default=False):
            print(translate("GUIDED_CANCELLED"))
            return 1

    # Создание задачи
    print(translate("GUIDED_SAVING"))
    domain = derive_domain_explicit(
        getattr(args, 'domain', None),
        getattr(args, 'phase', None),
        getattr(args, 'component', None)
    )

    task = manager.create_task(
        title,
        status="FAIL",
        priority=getattr(args, 'priority', "MEDIUM"),
        parent=parent,
        domain=domain,
        phase=getattr(args, 'phase', "") or "",
        component=getattr(args, 'component', "") or "",
    )

    task.description = description
    task.context = context
    task.tags = tags
    task.risks = risks
    task.success_criteria = tests
    task.subtasks = subtasks
    task.update_status_from_progress()

    manager.save_task(task)
    save_last_task(task.id, task.domain)

    print("\n" + "=" * 60)
    print(translate("GUIDED_SUCCESS", task_id=task.id))
    print("=" * 60)
    print(f"[TASK] {task.title}")
    print(translate("GUIDED_PARENT", parent=task.parent))
    print(translate("GUIDED_SUBTASK_COUNT", count=len(task.subtasks)))
    print(translate("GUIDED_CRITERIA_COUNT", count=len(task.success_criteria)))
    print(translate("GUIDED_RISKS_COUNT", count=len(task.risks)))
    print("=" * 60)

    return 0


def cmd_automation_task_create(args) -> int:
    from core.desktop.devtools.interface.cli_automation import _automation_template_payload, _ensure_tmp_dir, _load_note, _resolve_parent, _write_json

    parent = _resolve_parent(args.parent)
    if not parent:
        return 1
    domain = derive_domain_explicit(getattr(args, "domain", ""), getattr(args, "phase", None), getattr(args, "component", None))
    subtasks_source = args.subtasks or str(Path(".tmp") / "subtasks.template.json")
    subtasks_path = Path(subtasks_source[1:]) if subtasks_source.startswith("@") else Path(subtasks_source)
    resolved_path = None
    if subtasks_source.startswith("@") or subtasks_path.exists():
        if not subtasks_path.exists():
            payload = _automation_template_payload(args.count, args.coverage, args.risks, args.sla)
            _ensure_tmp_dir()
            _write_json(subtasks_path, payload)
        raw_text = subtasks_path.read_text(encoding="utf-8")
        try:
            payload = json.loads(raw_text)
            if isinstance(payload, dict) and "subtasks" in payload:
                resolved_path = subtasks_path.parent / "subtasks.resolved.json"
                _write_json(resolved_path, payload["subtasks"])
                raw_text = json.dumps(payload["subtasks"], ensure_ascii=False)
        except Exception:
            pass
        subtasks = raw_text
    else:
        payload = _automation_template_payload(args.count, args.coverage, args.risks, args.sla)
        _ensure_tmp_dir()
        _write_json(subtasks_path, payload)
        subtasks = json.dumps(payload["subtasks"], ensure_ascii=False)

    note = _load_note(Path(getattr(args, "note", None) or ".tmp/automation-note.log"), "")
    title, tags, deps = parse_smart_title(args.title)
    auto_args = [
        "create",
        "--title",
        title,
        "--parent",
        parent,
        "--description",
        args.description,
        "--subtasks",
        subtasks,
        "--tests",
        args.tests,
        "--risks",
        args.risks,
        "--note",
        note,
    ]
    if getattr(args, "validate_only", False):
        auto_args.append("--validate-only")
    if getattr(args, "domain", None):
        auto_args += ["--domain", domain]
    if getattr(args, "phase", None):
        auto_args += ["--phase", args.phase]
    if getattr(args, "component", None):
        auto_args += ["--component", args.component]
    for tag in tags:
        auto_args += ["--tag", tag]
    for dep in deps:
        auto_args += ["--dependency", dep]

    if getattr(args, "dry_run", False):
        safe_args = [str(x) for x in auto_args if x is not None]
        print("DRY RUN:", "apply_task", " ".join(safe_args))
        return 0

    from core.desktop.devtools.interface.cli_io import structured_response

    payload = {
        "parent": parent,
        "domain": domain,
        "subtasks": subtasks if resolved_path is None else f"@{resolved_path}",
    }
    return structured_response(
        "automation.task-create.validate",
        status="OK",
        message="automation task-create validated",
        payload=payload,
        summary=parent,
    )


def cmd_automation_checkpoint(args) -> int:
    from core.desktop.devtools.interface.cli_io import structured_response, structured_error
    from core.desktop.devtools.interface.cli_automation import _load_note

    task_id = getattr(args, "task_id", None)
    index = int(getattr(args, "index", 0))
    domain = derive_domain_explicit(getattr(args, "domain", None), getattr(args, "phase", None), getattr(args, "component", None))
    mode = getattr(args, "mode", "note")
    note = getattr(args, "note", None) or _load_note(Path(getattr(args, "log", "")) if getattr(args, "log", None) else Path(".tmp/health.log"), "")

    manager = TaskManager(tasks_dir=Path(getattr(args, "tasks_dir", ".tasks")))
    payload = {"task_id": task_id, "index": index, "mode": mode, "note": note}

    if mode == "note":
        ok, msg = manager.update_subtask_checkpoint(task_id, index, getattr(args, "checkpoint", "criteria"), True, note, domain)
        if not ok:
            return structured_error("automation.checkpoint", msg or "checkpoint failed", payload=payload)
    else:
        for checkpoint in ("criteria", "tests", "blockers"):
            ok, msg = manager.update_subtask_checkpoint(task_id, index, checkpoint, True, note, domain)
            if not ok:
                return structured_error("automation.checkpoint", msg or "checkpoint failed", payload=payload)
        ok, msg = manager.set_subtask(task_id, index, True, domain)
        if not ok:
            return structured_error("automation.checkpoint", msg or "complete failed", payload=payload)
    detail = manager.load_task(task_id, domain)
    if detail:
        payload["task"] = task_to_dict(detail, include_subtasks=True)
    return structured_response(
        "automation.checkpoint.validate",
        status="OK",
        message="automation checkpoint processed",
        payload=payload,
        summary=str(task_id),
    )


__all__ = ["cmd_create_guided", "cmd_automation_task_create", "cmd_automation_checkpoint"]
