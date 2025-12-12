"""CLI parser construction for tasks CLI/TUI."""

import argparse
from pathlib import Path
from typing import Any, Mapping


def build_parser(commands: Any, themes: Mapping[str, Any], default_theme: str, automation_tmp: Path) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="tasks.py — управление задачами (.tasks only)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    def add_domain_arg(sp):
        sp.add_argument(
            "--domain",
            "-F",
            dest="domain",
            help="домен/подпапка внутри .tasks (архитектурный контур)",
        )
        return sp

    def add_context_args(sp):
        add_domain_arg(sp)
        sp.add_argument("--phase", help="фаза/итерация (используется для автопути)")
        sp.add_argument("--component", help="компонент/модуль (используется для автопути)")
        return sp

    sub = parser.add_subparsers(dest="command", help="Команды")

    # tui
    tui_p = sub.add_parser("tui", help="Запустить TUI")
    tui_p.add_argument("--theme", choices=list(themes.keys()), default=default_theme, help="палитра интерфейса")
    tui_p.add_argument("--mono-select", action="store_true", help="использовать монохромное выделение строк")
    tui_p.set_defaults(func=commands.cmd_tui)

    # list
    lp = sub.add_parser("list", help="Список задач")
    lp.add_argument("--status", choices=["TODO", "ACTIVE", "DONE", "FAIL", "WARN", "OK"])
    lp.add_argument("--tag", help="Фильтр по тегу")
    lp.add_argument("--blocked", action="store_true", help="Только заблокированные зависимостями")
    lp.add_argument("--stale", type=int, metavar="DAYS", help="Без активности N дней")
    lp.add_argument("--progress", action="store_true")
    add_context_args(lp)
    lp.set_defaults(func=commands.cmd_list)

    # show
    sp = sub.add_parser("show", help="Показать задачу")
    sp.add_argument("task_id", nargs="?")
    add_context_args(sp)
    sp.set_defaults(func=commands.cmd_show)

    # create
    cp = sub.add_parser("create", help="Создать задачу")
    cp.add_argument("title")
    cp.add_argument("--status", default="TODO", choices=["TODO", "ACTIVE", "DONE", "FAIL", "WARN", "OK"])
    cp.add_argument("--priority", default="MEDIUM", choices=["LOW", "MEDIUM", "HIGH"])
    cp.add_argument("--parent", required=True)
    cp.add_argument("--description", "-d", required=True)
    cp.add_argument("--context", "-c")
    cp.add_argument("--tags", "-t")
    cp.add_argument(
        "--subtasks",
        "-s",
        required=True,
        help="JSON массив подзадач (строкой, --subtasks @file.json или --subtasks - для STDIN; всё на русском)",
    )
    cp.add_argument("--dependencies")
    cp.add_argument("--depends-on", help="ID задач-зависимостей через запятую (TASK-001,TASK-002)")
    cp.add_argument("--next-steps", "-n")
    cp.add_argument("--tests", required=True)
    cp.add_argument("--risks", help="semicolon-separated risks", required=True)
    cp.add_argument("--validate-only", action="store_true", help="Проверить payload без записи задачи")
    add_context_args(cp)
    cp.set_defaults(func=commands.cmd_create)

    # task (smart)
    tp = sub.add_parser(
        "task",
        help="Умное создание (с парсингом #тегов и @зависимостей)",
        description=(
            "Создаёт задачу из заголовка с автоматическим извлечением тегов (#backend) и зависимостей (@TASK-010).\n"
            "Обязательные параметры: --parent, --description, --tests, --risks, --subtasks.\n"
            "Каждая подзадача в JSON должна быть на русском языке и включать критерии/тесты/блокеры."
        ),
    )
    tp.add_argument("title")
    tp.add_argument("--status", default="TODO", choices=["TODO", "ACTIVE", "DONE", "FAIL", "WARN", "OK"])
    tp.add_argument("--priority", default="MEDIUM", choices=["LOW", "MEDIUM", "HIGH"])
    tp.add_argument("--parent", required=True)
    tp.add_argument("--description", "-d", required=True)
    tp.add_argument("--context", "-c")
    tp.add_argument("--tags", "-t")
    tp.add_argument(
        "--subtasks",
        "-s",
        required=True,
        help="JSON массив подзадач (строкой, --subtasks @file.json или --subtasks - для STDIN; всё на русском)",
    )
    tp.add_argument("--dependencies")
    tp.add_argument("--depends-on", help="ID задач-зависимостей через запятую (TASK-001,TASK-002)")
    tp.add_argument("--next-steps", "-n")
    tp.add_argument("--tests", required=True)
    tp.add_argument("--risks", help="semicolon-separated risks", required=True)
    tp.add_argument("--validate-only", action="store_true", help="Проверить payload без записи задачи")
    add_context_args(tp)
    tp.set_defaults(func=commands.cmd_smart_create)

    # guided (interactive)
    gp = sub.add_parser("guided", help="Интерактивное создание (шаг-ответ-шаг)")
    add_context_args(gp)
    gp.set_defaults(func=commands.cmd_create_guided)

    # update
    up = sub.add_parser(
        "update",
        help="Обновить статус задачи",
        description=(
            "Обновляет статус задачи на TODO/ACTIVE/DONE (алиасы: FAIL/WARN/OK).\n"
            "Вызовы поддерживают оба порядка: `update TASK-005 DONE` или `update DONE TASK-005`.\n"
            "Перед переводом в DONE убедись, что все подзадачи закрыты и есть доказательства тестов."
        ),
    )
    up.add_argument("arg1")
    up.add_argument("arg2", nargs="?")
    add_context_args(up)
    up.set_defaults(func=commands.cmd_update)

    # analyze
    ap = sub.add_parser("analyze", help="Анализ задачи")
    ap.add_argument("task_id")
    add_context_args(ap)
    ap.set_defaults(func=commands.cmd_analyze)

    # next
    np = sub.add_parser("next", help="Следующая задача")
    add_context_args(np)
    np.set_defaults(func=commands.cmd_next)

    # add-subtask
    asp = sub.add_parser("add-subtask", help="Добавить подзадачу")
    asp.add_argument("task_id")
    asp.add_argument("subtask")
    asp.add_argument("--criteria", required=True, help="Критерии выполнения (через ';')")
    asp.add_argument("--tests", required=True, help="Тесты/проверки (через ';')")
    asp.add_argument("--blockers", required=True, help="Блокеры/зависимости (через ';')")
    add_context_args(asp)
    asp.set_defaults(func=commands.cmd_add_subtask)

    # add-dependency
    adp = sub.add_parser("add-dep", help="Добавить зависимость")
    adp.add_argument("task_id")
    adp.add_argument("dependency")
    add_context_args(adp)
    adp.set_defaults(func=commands.cmd_add_dependency)

    # ok macro (batch support: ok TASK 0,1,2 or ok TASK --all)
    okp = sub.add_parser("ok", help="Закрыть подзадачу(и) одним махом (criteria/tests/blockers+done)")
    okp.add_argument("task_id")
    okp.add_argument("indices", nargs="?", help="Индекс(ы) подзадач: 0 или 0,1,2")
    okp.add_argument("--all", action="store_true", dest="all_subtasks", help="Завершить все незакрытые подзадачи")
    okp.add_argument("--criteria-note")
    okp.add_argument("--tests-note")
    okp.add_argument("--blockers-note")
    okp.add_argument("--path", help="Путь подзадачи (0.1.2) вместо индекса")
    add_context_args(okp)
    okp.set_defaults(func=commands.cmd_ok)

    # alias: apply_task sub ok
    subok = sub.add_parser(
        "sub",
        help="Группа алиасов для подзадач; sub ok == ok",
        description="Короткий алиас: `apply_task sub ok TASK IDX [--criteria-note ... --tests-note ... --blockers-note ...]`",
    )
    subok_sub = subok.add_subparsers(dest="subcommand", required=True)
    subok_ok = subok_sub.add_parser("ok", help="Подтвердить критерии/тесты/блокеры и закрыть подзадачу (алиас ok)")
    subok_ok.add_argument("task_id")
    subok_ok.add_argument("indices", nargs="?", help="Индекс(ы) подзадач: 0 или 0,1,2")
    subok_ok.add_argument("--all", action="store_true", dest="all_subtasks", help="Завершить все незакрытые подзадачи")
    subok_ok.add_argument("--criteria-note")
    subok_ok.add_argument("--tests-note")
    subok_ok.add_argument("--blockers-note")
    subok_ok.add_argument("--path", help="Путь подзадачи (0.1.2) вместо индекса")
    add_context_args(subok_ok)
    subok_ok.set_defaults(func=commands.cmd_ok)

    # note macro
    notep = sub.add_parser("note", help="Добавить заметку/подтверждение к чекпоинту")
    notep.add_argument("task_id")
    notep.add_argument("index", type=int)
    notep.add_argument("--checkpoint", choices=["criteria", "tests", "blockers"], required=True)
    notep.add_argument("--note", required=True)
    notep.add_argument("--undo", action="store_true", help="сбросить подтверждение вместо установки")
    notep.add_argument("--path", help="Путь подзадачи (0.1.2) вместо индекса")
    add_context_args(notep)
    notep.set_defaults(func=commands.cmd_note)

    # progress-note - add progress note without completion (Phase 1)
    pnote = sub.add_parser("progress-note", help="Добавить progress note к подзадаче (без завершения)")
    pnote.add_argument("task_id", help="Task ID (e.g., TASK-001)")
    pnote.add_argument("path", help="Путь подзадачи (0, 0.1, 0.1.2)")
    pnote.add_argument("note", help="Текст progress note")
    add_context_args(pnote)
    pnote.set_defaults(func=commands.cmd_progress_note)

    # block - block/unblock subtask (Phase 1)
    blockp = sub.add_parser("block", help="Заблокировать/разблокировать подзадачу")
    blockp.add_argument("task_id", help="Task ID (e.g., TASK-001)")
    blockp.add_argument("path", help="Путь подзадачи (0, 0.1, 0.1.2)")
    blockp.add_argument("--reason", "-r", help="Причина блокировки")
    blockp.add_argument("--unblock", "-u", action="store_true", help="Снять блокировку")
    add_context_args(blockp)
    blockp.set_defaults(func=commands.cmd_block)

    # bulk macro
    blp = sub.add_parser("bulk", help="Выполнить набор чекпоинтов из JSON payload")
    blp.add_argument("--input", "-i", default="-", help="Источник JSON (строка, @file, '-'=STDIN)")
    blp.add_argument("--task", help="task_id по умолчанию для операций без поля task (используй '.'/last для .last)")
    add_context_args(blp)
    blp.set_defaults(func=commands.cmd_bulk)

    webhook = sub.add_parser("projects-webhook", help="Обработать payload GitHub Projects")
    webhook.add_argument("--payload", default="-", help="JSON payload ('-' для STDIN)")
    webhook.add_argument("--signature", help="Значение заголовка X-Hub-Signature-256")
    webhook.add_argument("--secret", help="Shared secret для проверки подписи")
    webhook.set_defaults(func=commands.cmd_projects_webhook)

    webhook_srv = sub.add_parser("projects-webhook-serve", help="HTTP-сервер для GitHub Projects webhook")
    webhook_srv.add_argument("--host", default="0.0.0.0")
    webhook_srv.add_argument("--port", type=int, default=8787)
    webhook_srv.add_argument("--secret", help="Shared secret для проверки подписи")
    webhook_srv.set_defaults(func=commands.cmd_projects_webhook_serve)

    auth = sub.add_parser("projects-auth", help="Сохранить GitHub PAT для Projects sync")
    auth.add_argument("--token", help="PAT со scope project")
    auth.add_argument("--unset", action="store_true", help="Удалить сохранённый PAT")
    auth.set_defaults(func=commands.cmd_projects_auth)

    projects = sub.add_parser("projects", help="Операции с GitHub Projects v2")
    proj_sub = projects.add_subparsers(dest="projects_command")
    proj_sub.required = True
    sync_cmd = proj_sub.add_parser("sync", help="Синхронизировать backlog с Projects v2")
    sync_cmd.add_argument("--all", action="store_true", help="Подтвердить синхронизацию всех задач")
    add_context_args(sync_cmd)
    sync_cmd.set_defaults(func=commands.cmd_projects_sync_cli)
    status_cmd = proj_sub.add_parser("status", help="Показать текущее состояние Projects sync")
    status_cmd.set_defaults(func=commands.cmd_projects_status)
    status_set_cmd = proj_sub.add_parser("status-set", help="Установить статус задачи (TODO/ACTIVE/DONE; алиасы OK/WARN/FAIL) — единообразно с TUI")
    status_set_cmd.add_argument("task_id", help="TASK-xxx")
    status_set_cmd.add_argument("status", choices=["TODO", "ACTIVE", "DONE", "FAIL", "WARN", "OK"])
    add_domain_arg(status_set_cmd)
    status_set_cmd.set_defaults(func=commands.cmd_status_set)
    autosync_cmd = proj_sub.add_parser("autosync", help="Включить или выключить auto_sync без редактирования конфигов")
    autosync_cmd.add_argument("state", choices=["on", "off"], help="on/off")
    autosync_cmd.set_defaults(func=commands.cmd_projects_autosync)
    workers_cmd = proj_sub.add_parser("workers", help="Задать размер пула sync (0=auto)")
    workers_cmd.add_argument("count", type=int, help="Количество потоков (0=auto)")
    workers_cmd.set_defaults(func=commands.cmd_projects_workers)

    # checkpoint wizard
    ckp = sub.add_parser(
        "checkpoint",
        help="Пошаговый мастер подтверждения критериев/тестов/блокеров",
        description=(
            "Интерактивно проводит через чекпоинты выбранной подзадачи (критерии → тесты → блокеры).\n"
            "Поддерживает шорткат '.'/last и режим --auto для нефтерминальных сред."
        ),
    )
    ckp.add_argument("task_id", nargs="?", help="TASK-ID или '.' для последней задачи")
    ckp.add_argument("--subtask", type=int, help="Индекс подзадачи (0..n-1)")
    ckp.add_argument("--note", help="Комментарий по умолчанию для чекпоинтов")
    ckp.add_argument("--auto", action="store_true", help="Подтвердить все чекпоинты без вопросов")
    add_context_args(ckp)
    ckp.set_defaults(func=commands.cmd_checkpoint)

    # subtask
    stp = sub.add_parser("subtask", help="Управление подзадачами (add/done/undo)")
    stp.add_argument("task_id")
    stp.add_argument("--add", help="добавить подзадачу с текстом")
    stp.add_argument("--criteria", help="критерии для --add (через ';')")
    stp.add_argument("--tests", help="тесты для --add (через ';')")
    stp.add_argument("--blockers", help="блокеры для --add (через ';')")
    stp.add_argument("--done", type=int, help="отметить выполненной по индексу (0..n-1)")
    stp.add_argument("--undo", type=int, help="вернуть в работу по индексу (0..n-1)")
    stp.add_argument("--criteria-done", type=int, dest="criteria_done", help="подтвердить выполнение критериев (индекс)")
    stp.add_argument("--criteria-undo", type=int, dest="criteria_undo", help="сбросить подтверждение критериев (индекс)")
    stp.add_argument("--tests-done", type=int, dest="tests_done", help="подтвердить тесты (индекс)")
    stp.add_argument("--tests-undo", type=int, dest="tests_undo", help="сбросить подтверждение тестов (индекс)")
    stp.add_argument("--blockers-done", type=int, dest="blockers_done", help="подтвердить снятие блокеров (индекс)")
    stp.add_argument("--blockers-undo", type=int, dest="blockers_undo", help="сбросить подтверждение блокеров (индекс)")
    stp.add_argument("--note", help="описание/доказательство при отметке чекпоинтов")
    stp.add_argument("--path", help="Путь подзадачи (0.1.2). Для плоских индексов оставь пустым.")
    add_context_args(stp)
    stp.set_defaults(func=commands.cmd_subtask)

    # move
    mv = sub.add_parser("move", help="Переместить задачу(и) в подпапку .tasks")
    mv.add_argument("task_id", nargs="?")
    mv.add_argument("--glob", help="glob-шаблон внутри .tasks (пример: 'phase1/*.task')")
    mv.add_argument("--to", required=True, help="целевая подпапка")
    add_domain_arg(mv)
    mv.set_defaults(func=commands.cmd_move)

    # edit
    ep = sub.add_parser(
        "edit",
        help="Редактировать свойства задачи",
        description=(
            "Позволяет менять описание, теги, приоритет, фазу/компонент и т.п.\n"
            "Пример: `apply_task edit TASK-010 --description \"Новая формулировка\" --phase iteration-2`.\n"
            "Изменения описывай на русском языке, чтобы TUI и отчёты оставались консистентны."
        ),
    )
    ep.add_argument("task_id")
    ep.add_argument("--description", "-d")
    ep.add_argument("--context", "-c")
    ep.add_argument("--tags", "-t")
    ep.add_argument("--priority", "-p", choices=["LOW", "MEDIUM", "HIGH"])
    ep.add_argument("--phase", help="новая фаза/итерация")
    ep.add_argument("--component", help="новый компонент/модуль")
    ep.add_argument("--new-domain", help="переместить в подпапку")
    ep.add_argument("--depends-on", help="ID задач-зависимостей через запятую (заменяет список)")
    ep.add_argument("--add-dep", help="Добавить зависимость (один ID)")
    ep.add_argument("--remove-dep", help="Удалить зависимость (один ID)")
    add_domain_arg(ep)
    ep.set_defaults(func=commands.cmd_edit)

    # clean
    cl = sub.add_parser("clean", help="Удалить задачи по фильтрам")
    cl.add_argument("--tag", help="тег без #")
    cl.add_argument("--status", choices=["TODO", "ACTIVE", "DONE", "FAIL", "WARN", "OK"], help="фильтр по статусу")
    cl.add_argument("--phase", help="фаза/итерация")
    cl.add_argument("--glob", help="glob-шаблон (.tasks relative), например 'phase1/*.task'")
    cl.add_argument("--dry-run", action="store_true", help="только показать задачи без удаления")
    cl.set_defaults(func=commands.cmd_clean)

    # lint
    lp2 = sub.add_parser("lint", help="Проверка .tasks")
    lp2.add_argument("--fix", action="store_true")
    lp2.set_defaults(func=commands.cmd_lint)

    # suggest
    sg = sub.add_parser("suggest", help="Рекомендовать задачи")
    add_context_args(sg)
    sg.set_defaults(func=commands.cmd_suggest)

    # quick
    qp = sub.add_parser("quick", help="Быстрый обзор top-3")
    add_context_args(qp)
    qp.set_defaults(func=commands.cmd_quick)

    # template
    tmp = sub.add_parser("template", help="Генерация шаблонов для автоматизации")
    tmp_sub = tmp.add_subparsers(dest="template_command")
    tmp_sub.required = True
    subt = tmp_sub.add_parser("subtasks", help="Создать JSON с заготовками подзадач")
    subt.add_argument("--count", type=int, default=3, help="Количество подзадач (>=3)")
    subt.set_defaults(func=commands.cmd_template_subtasks)

    # automation shortcuts (devtools)
    auto = sub.add_parser("automation", help="Утилиты devtools/automation для быстрой работы")
    auto_sub = auto.add_subparsers(dest="auto_command")
    auto_sub.required = True

    auto_tmpl = auto_sub.add_parser("task-template", help="Сгенерировать шаблон подзадач с дефолтными SLA/coverage")
    auto_tmpl.add_argument("--count", type=int, default=3)
    auto_tmpl.add_argument("--coverage", type=int, default=85)
    auto_tmpl.add_argument("--risks", default="perf;availability")
    auto_tmpl.add_argument("--sla", default="p95<=200ms")
    auto_tmpl.add_argument("--output", help="Путь для сохранения JSON (default: .tmp/subtasks.template.json)")
    auto_tmpl.set_defaults(func=commands.cmd_automation_task_template)

    auto_create = auto_sub.add_parser("task-create", help="Обёртка над create с дефолтами и автогенерацией шаблона")
    auto_create.add_argument("title")
    auto_create.add_argument("--parent", help="Если не задан, возьмём .last")
    auto_create.add_argument("--description", "-d", help="По умолчанию совпадает с title")
    auto_create.add_argument("--tests", default="pytest -q")
    auto_create.add_argument("--risks", default="perf;deps")
    auto_create.add_argument("--count", type=int, default=3, help="count для автогенерации шаблона")
    auto_create.add_argument("--coverage", type=int, default=85)
    auto_create.add_argument("--sla", default="p95<=200ms")
    auto_create.add_argument("--subtasks", default=str(automation_tmp / "subtasks.template.json"))
    auto_create.add_argument("--status", default="TODO", choices=["TODO", "ACTIVE", "DONE", "FAIL", "WARN", "OK"])
    auto_create.add_argument("--priority", default="MEDIUM", choices=["LOW", "MEDIUM", "HIGH"])
    auto_create.add_argument("--context")
    auto_create.add_argument("--tags")
    auto_create.add_argument("--apply", action="store_true", help="Создавать задачу вместо validate-only")
    add_context_args(auto_create)
    auto_create.set_defaults(func=commands.cmd_automation_task_create)

    auto_health = auto_sub.add_parser("health", help="Сводная проверка: pytest + лог в .tmp")
    auto_health.add_argument("--pytest-cmd", default="pytest -q")
    auto_health.add_argument("--log", help="Куда писать лог (default: .tmp/health.log)")
    auto_health.set_defaults(func=commands.cmd_automation_health)

    auto_proj = auto_sub.add_parser("projects-health", help="Короткий статус GitHub Projects")
    auto_proj.set_defaults(func=commands.cmd_automation_projects_health)

    auto_ckp = auto_sub.add_parser("checkpoint", help="Быстрое подтверждение чекпоинтов/подзадачи")
    auto_ckp.add_argument("task_id", help="TASK-ID или '.' для последней")
    auto_ckp.add_argument("index", type=int)
    auto_ckp.add_argument("--mode", choices=["ok", "note"], default="ok")
    auto_ckp.add_argument("--checkpoint", choices=["criteria", "tests", "blockers"], default="tests", help="для mode=note")
    auto_ckp.add_argument("--note", help="Явная нота для чекпоинта")
    auto_ckp.add_argument("--log", help="Файл для подтягивания ноты (default: .tmp/checkpoint.log)")
    add_context_args(auto_ckp)
    auto_ckp.set_defaults(func=commands.cmd_automation_checkpoint)

    # ai (AI-first interface)
    ai_p = sub.add_parser(
        "ai",
        help="AI-first JSON интерфейс для агентов",
        description=(
            "Единый JSON in/out интерфейс для ИИ-агентов.\n"
            "Intents: context, create, decompose, define, verify, progress, complete, batch.\n"
            "Пример: tasks ai '{\"intent\": \"context\"}'"
        ),
    )
    ai_p.add_argument("json_input", nargs="?", help="JSON строка или '-' для STDIN")
    ai_p.add_argument("--global", "-g", dest="use_global", action="store_true", help="Использовать глобальное хранилище ~/.tasks")
    add_context_args(ai_p)
    ai_p.set_defaults(func=commands.cmd_ai)

    # mcp (MCP stdio server)
    mcp_p = sub.add_parser(
        "mcp",
        help="Запустить MCP stdio сервер для AI-ассистентов",
        description=(
            "MCP (Model Context Protocol) stdio сервер.\n"
            "Позволяет Claude Desktop и другим AI-ассистентам работать с задачами.\n\n"
            "Конфигурация Claude Desktop:\n"
            '  {"mcpServers": {"tasks": {"command": "tasks", "args": ["mcp"]}}}'
        ),
    )
    mcp_p.add_argument("--tasks-dir", type=str, help="Директория задач")
    mcp_p.add_argument("--local", action="store_true", help="Использовать локальное .tasks вместо глобального")
    mcp_p.set_defaults(func=commands.cmd_mcp)

    # gui (Tauri desktop application)
    gui_p = sub.add_parser(
        "gui",
        help="Запустить графический интерфейс (Tauri)",
        description=(
            "Запуск Tauri десктоп-приложения для визуальной работы с задачами.\n"
            "Использует веб-интерфейс с React и нативную обёртку Tauri.\n\n"
            "Режимы:\n"
            "  --dev  — режим разработки с hot-reload (pnpm tauri dev)\n"
            "  по умолчанию — запуск скомпилированного приложения"
        ),
    )
    gui_p.add_argument("--dev", action="store_true", help="Режим разработки с hot-reload")
    gui_p.set_defaults(func=commands.cmd_gui)

    return parser
