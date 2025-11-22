#!/usr/bin/env python3
"""
tasks.py — flagship task manager (single-file CLI/TUI).

All tasks live under .tasks/ (one .task file per task).
"""

import argparse
import json
import os
import re
import sys
import time
import subprocess
import shlex
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import requests
import webbrowser
from dataclasses import dataclass
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union, Set
from contextlib import contextmanager

import yaml
import textwrap
from wcwidth import wcwidth
from core import Status, SubTask, TaskDetail
from application.ports import TaskRepository
from infrastructure.file_repository import FileTaskRepository
from infrastructure.task_file_parser import TaskFileParser
from infrastructure.projects_sync_service import ProjectsSyncService
from application.sync_service import SyncService
from util.sync_status import sync_status_fragments

import projects_sync
from projects_sync import (
    get_projects_sync,
    reload_projects_sync,
    update_projects_enabled,
    update_project_target,
    update_project_workers,
    detect_repo_slug,
)
from config import get_user_token, set_user_token, get_user_lang, set_user_lang

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M"
GITHUB_GRAPHQL = "https://api.github.com/graphql"

# Cache for expensive Git Projects metadata lookups, throttled to avoid
# blocking the TUI render loop on every keypress.
_PROJECT_STATUS_CACHE: Optional[Dict[str, Any]] = None
_PROJECT_STATUS_CACHE_TS: float = 0.0
_PROJECT_STATUS_TTL: float = 1.0
_PROJECT_STATUS_LOCK = threading.Lock()
_PROJECT_STATUS_CACHE_TOKEN_PREVIEW: Optional[str] = None

AI_HELP = """apply_task — hardline rules for AI agents

1) Operate only via apply_task. Never edit .tasks/ directly. Track context via .last (TASK@domain).
2) Decompose: root task + nested subtasks (any depth). Every subtask: title ≥20 chars; success_criteria; tests; blockers. Checkpoints only through ok/note/bulk --path.
3) Create tasks: set domain (--domain/-F). Generate subtasks template (apply_task template subtasks --count N > .tmp/subtasks.json), fill criteria/tests/blockers. Create: apply_task create "Title #tags" --domain <d> --description "<what/why/acceptance>" --tests "<proj tests>" --risks "<proj risks>" --subtasks @.tmp/subtasks.json. Add nested: apply_task subtask TASK --add "<title>" --criteria "...;..." --tests "...;..." --blockers "...;..." --parent-path 0.1 (path like 0.1.2).
4) Maintain subtasks: add via subtask; checkpoints via ok --path X.Y (criteria/tests/blockers with notes); complete via subtask --done --path only if all checkpoints OK; note progress with note --path.
5) Statuses: start at fail -> warn when working -> ok only when all subtasks done. Commands: apply_task start/done/fail TASK.
6) Quality gates: diff coverage ≥85%; cyclomatic complexity ≤10; no mocks/stubs in prod; one file = one responsibility; prefer <300 LOC. Before delivery run pytest -q and log executed tests. Always keep explicit blockers/tests/criteria on every node.
7) GitHub Projects: config .apply_task_projects.yaml; token APPLY_TASK_GITHUB_TOKEN|GITHUB_TOKEN. If no token/remote, sync is off; CLI works offline.
Language: reply to user in their language unless asked otherwise. Task text/notes follow user language; code/tests/docs stay in English. Explicit blockers/tests/criteria on every node. No checkpoints — no done.
"""

LANG_PACK = {
    "en": {
        "TITLE": "TITLE",
        "SUBTASKS": "SUBTASKS",
        "SUBTASK_DETAILS": "SUBTASK DETAILS",
        "CRITERIA": "Criteria",
        "TESTS": "Tests",
        "BLOCKERS": "Blockers",
        "DESCRIPTION": "Description",
        "BLOCKERS_HEADER": "Blockers",
        "DOMAIN": "Domain",
        "PHASE": "Phase",
        "COMPONENT": "Component",
        "PARENT": "Parent",
        "DESCRIPTION_MISSING": "No description",
        "PRIORITY": "Priority",
        "PROGRESS": "Progress",
        "COMPLETED_SUFFIX": "done",
        "TAGS": "Tags",
        "STATUS_DONE": "DONE",
        "STATUS_IN_PROGRESS": "IN PROGRESS",
        "STATUS_BACKLOG": "BACKLOG",
        "CHECKPOINT_CRITERIA": "Criteria",
        "CHECKPOINT_TESTS": "Tests",
        "CHECKPOINT_BLOCKERS": "Blockers",
        "LOG_CRITERIA": "Criteria",
        "LOG_TESTS": "Tests",
        "LOG_BLOCKERS": "Blockers",
        "DESCRIPTION_PROMPT": "Description",
        "DOMAIN_LABEL": "Domain",
        "PHASE_LABEL": "Phase",
        "COMPONENT_LABEL": "Component",
        "TAGS_LABEL": "Tags",
        "PARENT_LABEL": "Parent",
        "BLOCKERS_LABEL": "Blockers",
        "LANGUAGE_LABEL": "Language",
        "LANGUAGE_HINT": "Enter — cycle language (en, ru, uk, es, fr, zh, hi, ar)",
        "CLIPBOARD_EMPTY": "Clipboard is empty or unavailable",
        "OPTION_DISABLED": "Option unavailable",
        "VALUE_NOT_AVAILABLE": "n/a",
        "ERR_TASK_NOT_COMPLETE": "Not all subtasks are done",
        "ERR_TASK_NO_CRITERIA_TESTS": "No success criteria/tests at task level",
        "ERR_SUBTASK_NO_CRITERIA": "Subtask {idx} '{title}' has no success criteria",
        "ERR_SUBTASK_NO_TESTS": "Subtask {idx} '{title}' has no tests",
        "ERR_SUBTASK_CHECKPOINTS": "Confirm {items} before completing",
        "STATUS_MESSAGE_PUSH_OK": "Git push completed",
        "STATUS_MESSAGE_PUSH_FAIL": "Push failed: {error}",
        "STATUS_MESSAGE_BOOTSTRAP_ERROR": "Git bootstrap error: {error}",
        "STATUS_MESSAGE_BOOTSTRAP_FAILED": "Git bootstrap failed: {error}",
        "STATUS_MESSAGE_REFRESHED": "Projects cache refreshed",
        "STATUS_MESSAGE_OPTION_DISABLED": "Option unavailable",
        "STATUS_MESSAGE_PAT_MISSING": "PAT missing",
        "STATUS_MESSAGE_PROMPT_PAT": "Enter PAT for validation",
        "STATUS_MESSAGE_RATE_LIMIT": "Rate limit: remaining {remaining}; wait {seconds}s",
        "STATUS_MESSAGE_CLI_UPDATED": "↻ CLI: tasks updated (external change)",
        "STATUS_MESSAGE_AUTO_SYNC": "Auto-sync: {count} tasks",
        "STATUS_MESSAGE_LANG_SET": "Language set to {lang}",
        "STATUS_MESSAGE_PAT_SAVED": "PAT saved",
        "STATUS_MESSAGE_PAT_CLEARED": "PAT cleared",
        "STATUS_MESSAGE_PROJECT_NUMBER_REQUIRED": "Project number must be a positive integer",
        "STATUS_MESSAGE_PROJECT_NUMBER_UPDATED": "Project number updated",
        "STATUS_MESSAGE_POOL_INTEGER": "Pool must be an integer (0=auto)",
        "STATUS_MESSAGE_POOL_UPDATED": "Pool size updated",
        "STATUS_MESSAGE_CHECKPOINTS_REQUIRED": "Checkpoints are not confirmed",
        "STATUS_MESSAGE_PROJECT_OPEN": "Opening GitHub Project → {url}",
        "STATUS_MESSAGE_OPEN_FAILED": "Could not open link: {error}",
        "STATUS_MESSAGE_PROJECT_URL_UNAVAILABLE": "Project URL unavailable",
        "STATUS_MESSAGE_PASTE_PAT": "Paste PAT (leave empty to clear)",
        "STATUS_MESSAGE_SYNC_ON": "Sync enabled",
        "STATUS_MESSAGE_SYNC_OFF": "Sync disabled",
        "STATUS_MESSAGE_VALIDATING": "Validating...",
        "STATUS_MESSAGE_VALIDATE_LABEL": "Validating {label}",
        "ERR_TASK_NOT_FOUND": "Task {task_id} not found",
        "SETTINGS_NAV_HINT": "Up/Down — select, Enter — action, Esc — close",
        "SETTINGS_STATUS_LABEL": "Sync status",
        "SETTINGS_STATUS_HINT": "Current sync availability",
        "SETTINGS_STATUS_ON": "Sync ON",
        "SETTINGS_STATUS_OFF": "Sync OFF ({reason})",
        "SETTINGS_PAT_LABEL": "GitHub PAT",
        "SETTINGS_PAT_SAVED": "Saved (…{preview})",
        "SETTINGS_PAT_ENV": "ENV {env}",
        "SETTINGS_PAT_NOT_SET": "Not set",
        "SETTINGS_PAT_HINT": "Enter — paste new PAT or leave empty to clear",
        "SETTINGS_SYNC_LABEL": "Sync",
        "SETTINGS_SYNC_HINT": "Enter — toggle auto-sync",
        "SETTINGS_SYNC_NO_REMOTE": "Unavailable (no git remote)",
        "SETTINGS_SYNC_DISABLED": "Disabled",
        "SETTINGS_SYNC_NO_PAT": "Unavailable (no PAT)",
        "SETTINGS_SYNC_ENABLED": "Enabled",
        "SETTINGS_PROJECT_LABEL": "GitHub Project",
        "SETTINGS_PROJECT_UNAVAILABLE": "unavailable",
        "SETTINGS_BOOTSTRAP_LABEL": "Initialize git + origin",
        "SETTINGS_BOOTSTRAP_VALUE": "Create repo and push",
        "SETTINGS_BOOTSTRAP_HINT": "Enter — provide URL (https://github.com/owner/repo.git); will run git init/add/push",
        "SETTINGS_PROJECT_URL_LABEL": "Project URL",
        "SETTINGS_PROJECT_URL_HINT": "g — open in browser",
        "SETTINGS_PROJECT_NUMBER_LABEL": "Project number",
        "SETTINGS_PROJECT_NUMBER_HINT": "Enter — update Project v2 number",
        "SETTINGS_POOL_LABEL": "Sync pool",
        "SETTINGS_POOL_HINT": "Enter — set sync pool size (0=auto). Limits API budget.",
        "SETTINGS_LAST_PULL_LABEL": "Last pull/push",
        "SETTINGS_LAST_PULL_HINT": "Updated after successful sync",
        "SETTINGS_RATE_LABEL": "Rate limit",
        "SETTINGS_RATE_HINT": "GitHub remaining/reset (updates after requests)",
        "SETTINGS_REMOTE_LABEL": "Git remote",
        "SETTINGS_REMOTE_HINT": "Used to auto-detect Projects; change via git remote set-url origin",
        "SETTINGS_REFRESH_LABEL": "Reload fields",
        "SETTINGS_REFRESH_VALUE": "Refresh GraphQL cache",
        "SETTINGS_REFRESH_HINT": "Enter — reset Projects fields cache before sync",
        "SETTINGS_REFRESH_DISABLED": "Need selected project and PAT",
        "SETTINGS_VALIDATE_PAT_LABEL": "Validate PAT",
        "SETTINGS_VALIDATE_PAT_HINT": "Enter — validate token via GitHub GraphQL",
        "SETTINGS_VALIDATE_PAT_DISABLED": "Save PAT first",
        "LABEL_SUBTASKS_JSON": "JSON array of subtasks",
        "ERR_INVALID_FOLDER": "Invalid folder path",
        "ERR_NO_LAST_TASK": "No last task: run apply_task show/list/next to set context",
        "ERR_JSON_ARRAY_REQUIRED": "JSON must be an array of objects",
        "ERR_JSON_ELEMENT_OBJECT": "Element {idx} must be an object",
        "ERR_JSON_ELEMENT_TITLE": "Element {idx} is missing 'title'",
        "ERR_JSON_ELEMENT_CRITERIA": "Element {idx}: provide at least one success criterion",
        "ERR_JSON_ELEMENT_TESTS": "Element {idx}: provide tests to verify",
        "ERR_JSON_ELEMENT_BLOCKERS": "Element {idx}: provide blockers/dependencies",
        "ERR_JSON_INVALID": "Invalid JSON: {error}",
        "ERR_JSON_FORMAT_HINT": "Use JSON array. Example: '[{\"title\":\"Design cache rollout >=20 chars\",\"criteria\":[\"hit ratio >80%\"],\"tests\":[\"pytest -k cache\"],\"blockers\":[\"redis downtime\"]}]'\\nReason: {error}",
        "PROMPT_ABORTED": "[X] Aborted",
        "PROMPT_REQUIRED": "[!] Required field",
        "PROMPT_EMPTY_TO_FINISH": "(empty line to finish):",
        "PROMPT_MIN_ITEMS": "[!] Minimum {count} items",
        "PROMPT_SUBTASK_HEADER": "[C] Subtask {index}:",
        "PROMPT_SUBTASK_TITLE_REQ": "  Title (min 20 chars)",
        "PROMPT_SUBTASK_TITLE_SHORT": "  [!] Too short ({length}/20). Add details",
        "PROMPT_SUBTASK_TITLE": "  Title",
        "PROMPT_SUBTASK_CRITERIA": "  Success criteria",
        "PROMPT_SUBTASK_TESTS": "  Tests",
        "PROMPT_SUBTASK_BLOCKERS": "  Blockers/dependencies (required, min 1)",
        "MSG_LIST_BUILT": "Task list built",
        "SUMMARY_TASKS": "{count} tasks",
        "ERR_SHOW_NO_TASK": "No task to show",
        "MSG_TASK_DETAILS": "Task details",
        "ERR_DESCRIPTION_REQUIRED": "Description is required and cannot be empty/TBD",
        "ERR_TESTS_REQUIRED": "Provide tests/success criteria via --tests",
        "ERR_RISKS_REQUIRED": "Add risks via --risks (e.g., 'dep outage;perf regression')",
        "ERR_FLAGSHIP_SUBTASKS": "Subtasks do not meet flagship quality",
        "MSG_VALIDATION_PASSED": "Validation passed",
        "MSG_TASK_CREATED": "Task {task_id} created",
        "ERR_TASKS_DIR_MISSING": ".tasks directory is missing",
        "ERR_FILE_PARSE": "{filename} failed to parse",
        "ERR_FILE_NO_DESC": "{filename} missing description",
        "ERR_FILE_NO_TESTS": "{filename} missing tests/success criteria",
        "MSG_ISSUES_FOUND": "Found {count} issue(s)",
        "MSG_ALL_TASKS_DONE": "All tasks completed{hint}",
        "MSG_NO_TASKS": "No tasks",
        "MSG_RECOMMENDATIONS_READY": "Recommendations prepared{hint}",
        "SUMMARY_RECOMMENDATIONS": "{count} recommendations",
        "MSG_QUICK_DONE": "All tasks done{hint}",
        "SUMMARY_QUICK": "Top-{count} tasks",
        "ERR_TASK_NEEDS_SUBTASKS": "Task must be decomposed into subtasks",
        "ERR_SUBTASKS_MIN": "Not enough subtasks ({count}). Minimum 3 for flagship quality",
        "ERR_SUBTASK_PREFIX": "Subtask {idx}: {issue}",
        "SPINNER_REFRESH_TASKS": "Refreshing tasks",
        "STATUS_TASKS_COUNT": "{count} tasks",
        "STATUS_LOADING": "Loading",
        "STATUS_NO_TASKS": "No tasks",
        "STATUS_NO_DATA": "No data",
        "STATUS_CONTEXT": "Context: {ctx}",
        "STATUS_TASK_NOT_SELECTED": "Task not selected",
        "DESCRIPTION_HEADER": "DESCRIPTION:",
        "MARKS_SUFFIX": " — checkpoints:",
        "OFFSET_LABEL": " | Offset: ",
        "NAV_STATUS_HINT": "q — exit | r — refresh | Enter — details | d — complete | e — edit | g — Git Projects",
        "NAV_CHECKPOINT_HINT": "  Checkpoints: [✓ ✓ ·] = criteria / tests / blockers | ? — hide hint",
        "NAV_ARROWS_HINT": "← collapse/parent · → expand/first child · Enter: card · d: done",
        "NAV_EDIT_HINT": " Enter: save | Esc: cancel",
        "EDIT_TASK_TITLE": "Edit task title",
        "EDIT_TASK_DESCRIPTION": "Edit task description",
        "EDIT_SUBTASK": "Edit subtask",
        "EDIT_CRITERION": "Edit criterion",
        "EDIT_TEST": "Edit test",
        "EDIT_BLOCKER": "Edit blocker",
        "EDIT_PROJECT_NUMBER": "Project number",
        "EDIT_GENERIC": "Edit",
        "BTN_VALIDATE_PAT": "[ Validate PAT (F5) ]",
        "TASK_LIST_EMPTY": "No tasks",
        "TABLE_HEADER_TASK": "Task",
        "TABLE_HEADER_PROGRESS": "%",
        "TABLE_HEADER_SUBTASKS": "Σ",
        "SIDE_EMPTY_TASKS": "No tasks",
        "SIDE_NO_DATA": "No data",
        "STATUS_TASK_NOT_SELECTED": "Task not selected",
        "DUR_DAYS_SUFFIX": "d",
        "DUR_HOURS_SUFFIX": "h",
        "DUR_MINUTES_SUFFIX": "m",
        "DUR_LT_HOUR": "<1h",
        "SETTINGS_UNAVAILABLE": "Settings unavailable",
        "SETTINGS_TITLE": "GITHUB PROJECTS SETTINGS",
        "ERR_PARENT_REQUIRED": "Specify parent: --parent TASK-XXX",
        "GUIDED_ONLY_INTERACTIVE": "[X] Wizard available only in interactive terminal",
        "GUIDED_USE_PARAMS": "  Use: apply_task create with parameters",
        "GUIDED_TITLE": "[>>] WIZARD: Create a flagship-grade task",
        "GUIDED_STEP1": "[DESC] Step 1/5: Basics",
        "GUIDED_TASK_TITLE": "Task title",
        "GUIDED_PARENT_ID": "Parent task ID (e.g., TASK-001)",
        "GUIDED_DESCRIPTION": "Description (not TBD)",
        "GUIDED_DESCRIPTION_TBD": "  [!] Description cannot be 'TBD'",
        "GUIDED_STEP2": "[TAG]  Step 2/5: Context and metadata",
        "GUIDED_CONTEXT": "Additional context",
        "GUIDED_TAGS": "Tags (comma-separated)",
        "GUIDED_STEP3": "[WARN]  Step 3/5: Risks",
        "GUIDED_RISKS": "Project risks",
        "GUIDED_STEP4": "[SUB] Step 4/5: Success criteria and tests",
        "GUIDED_TESTS": "Success criteria / Tests",
        "GUIDED_STEP5": "[TASK] Step 5/5: Subtasks (min 3)",
        "GUIDED_ADD_MORE": "\nAdd another subtask?",
        "GUIDED_VALIDATION": "\n🔍 Flagship validation...",
        "GUIDED_WARN_ISSUES": "[WARN]  Issues detected:",
        "GUIDED_CONTINUE": "\nContinue despite issues?",
        "GUIDED_CANCELLED": "[X] Creation cancelled",
        "GUIDED_SAVING": "\n[SAVE] Creating task...",
        "GUIDED_SUCCESS": "[SUB] SUCCESS: Task {task_id} created",
        "GUIDED_PARENT": "[DEP] Parent: {parent}",
        "GUIDED_SUBTASK_COUNT": "[STAT] Subtasks: {count}",
        "GUIDED_CRITERIA_COUNT": "[SUB] Criteria: {count}",
        "GUIDED_RISKS_COUNT": "[WARN]  Risks: {count}",
        "ERR_STATUS_REQUIRED": "Provide status: OK | WARN | FAIL",
        "ERR_NO_TASK_AND_LAST": "No task ID and no last task",
        "MSG_STATUS_UPDATED": "Status {task_id} updated",
        "ERR_STATUS_NOT_UPDATED": "Status not updated",
        "ERR_SUBTASK_TITLE_MIN": "Subtask must be at least 20 characters with details",
        "ERR_SUBTASK_INDEX": "Invalid subtask index",
        "ERR_TASK_UNAVAILABLE": "Task unavailable",
        "ERR_SUBTASK_NOT_FOUND": "Subtask not found",
        "ERR_TASK_ID_OR_GLOB": "Provide task_id or --glob",
        "ERR_FILTER_REQUIRED": "Provide at least one filter: --tag/--status/--phase or --glob",
        "ERR_TOKEN_OR_UNSET": "Provide --token or --unset",
        "REQ_MIN_SUBTASKS": "At least 3 subtasks",
        "REQ_MIN_TITLE": "Each subtask >=20 chars",
        "REQ_EXPLICIT_CHECKPOINTS": "Explicit success criteria/tests/blockers",
        "REQ_ATOMIC": "Atomic steps without 'and then'",
    },
    "ru": {
        "TITLE": "ЗАГОЛОВОК",
        "SUBTASKS": "ПОДЗАДАЧИ",
        "SUBTASK_DETAILS": "ДЕТАЛИ ПОДЗАДАЧИ",
        "CRITERIA": "Критерии",
        "TESTS": "Тесты",
        "BLOCKERS": "Блокеры",
        "DESCRIPTION": "Описание",
        "BLOCKERS_HEADER": "Блокеры",
        "DOMAIN": "Папка",
        "PHASE": "Фаза",
        "COMPONENT": "Компонент",
        "PARENT": "Родитель",
        "DESCRIPTION_MISSING": "Описание отсутствует",
        "PRIORITY": "Приоритет",
        "PROGRESS": "Прогресс",
        "COMPLETED_SUFFIX": "завершено",
        "TAGS": "Теги",
        "STATUS_DONE": "DONE",
        "STATUS_IN_PROGRESS": "IN PROGRESS",
        "STATUS_BACKLOG": "BACKLOG",
        "CHECKPOINT_CRITERIA": "Критерии",
        "CHECKPOINT_TESTS": "Тесты",
        "CHECKPOINT_BLOCKERS": "Блокеры",
        "LOG_CRITERIA": "Критерии",
        "LOG_TESTS": "Тесты",
        "LOG_BLOCKERS": "Блокеры",
        "DESCRIPTION_PROMPT": "Описание",
        "DOMAIN_LABEL": "Папка",
        "PHASE_LABEL": "Фаза",
        "COMPONENT_LABEL": "Компонент",
        "TAGS_LABEL": "Теги",
        "PARENT_LABEL": "Родитель",
        "BLOCKERS_LABEL": "Блокеры",
        "LANGUAGE_LABEL": "Язык",
        "LANGUAGE_HINT": "Enter — переключить язык (en, ru, uk, es, fr, zh, hi, ar)",
        "CLIPBOARD_EMPTY": "Клипборд пуст или недоступен",
        "OPTION_DISABLED": "Опция недоступна",
        "VALUE_NOT_AVAILABLE": "н/д",
        "ERR_TASK_NOT_COMPLETE": "не все подзадачи выполнены",
        "ERR_TASK_NO_CRITERIA_TESTS": "нет критериев успеха/тестов на уровне задачи",
        "ERR_SUBTASK_NO_CRITERIA": "подзадача {idx} '{title}' не имеет критериев выполнения",
        "ERR_SUBTASK_NO_TESTS": "подзадача {idx} '{title}' не имеет тестов",
        "ERR_SUBTASK_CHECKPOINTS": "Отметь {items} перед завершением",
        "STATUS_MESSAGE_PUSH_OK": "Git пуш завершён",
        "STATUS_MESSAGE_PUSH_FAIL": "Push не удался: {error}",
        "STATUS_MESSAGE_BOOTSTRAP_ERROR": "Git bootstrap ошибка: {error}",
        "STATUS_MESSAGE_BOOTSTRAP_FAILED": "Git bootstrap не удался: {error}",
        "STATUS_MESSAGE_REFRESHED": "Кеш Projects обновлён",
        "STATUS_MESSAGE_OPTION_DISABLED": "Опция недоступна",
        "STATUS_MESSAGE_PAT_MISSING": "PAT отсутствует",
        "STATUS_MESSAGE_PROMPT_PAT": "Введи PAT для проверки",
        "STATUS_MESSAGE_RATE_LIMIT": "Rate limit: осталось {remaining}; ждать {seconds}с",
        "STATUS_MESSAGE_CLI_UPDATED": "↻ CLI: задачи обновлены (внешнее изменение)",
        "STATUS_MESSAGE_AUTO_SYNC": "Auto-sync: {count} задач",
        "STATUS_MESSAGE_LANG_SET": "Язык установлен: {lang}",
        "STATUS_MESSAGE_PAT_SAVED": "PAT сохранён",
        "STATUS_MESSAGE_PAT_CLEARED": "PAT очищен",
        "STATUS_MESSAGE_PROJECT_NUMBER_REQUIRED": "Номер проекта должен быть положительным целым",
        "STATUS_MESSAGE_PROJECT_NUMBER_UPDATED": "Номер проекта обновлён",
        "STATUS_MESSAGE_POOL_INTEGER": "Пул должен быть целым (0=auto)",
        "STATUS_MESSAGE_POOL_UPDATED": "Размер пула обновлён",
        "STATUS_MESSAGE_CHECKPOINTS_REQUIRED": "Чекпоинты не подтверждены",
        "STATUS_MESSAGE_PROJECT_OPEN": "Открываю GitHub Project → {url}",
        "STATUS_MESSAGE_OPEN_FAILED": "Не удалось открыть ссылку: {error}",
        "STATUS_MESSAGE_PROJECT_URL_UNAVAILABLE": "Project URL недоступен",
        "STATUS_MESSAGE_PASTE_PAT": "Вставь PAT (оставь пустым чтобы очистить)",
        "STATUS_MESSAGE_SYNC_ON": "Синхронизация включена",
        "STATUS_MESSAGE_SYNC_OFF": "Синхронизация выключена",
        "STATUS_MESSAGE_VALIDATING": "Проверка...",
        "STATUS_MESSAGE_VALIDATE_LABEL": "Проверка {label}",
        "ERR_TASK_NOT_FOUND": "Задача {task_id} не найдена",
        "SETTINGS_NAV_HINT": "Вверх/вниз — выбор, Enter — действие, Esc — закрыть",
        "SETTINGS_STATUS_LABEL": "Статус sync",
        "SETTINGS_STATUS_HINT": "Текущая доступность синхронизации",
        "SETTINGS_STATUS_ON": "Sync ON",
        "SETTINGS_STATUS_OFF": "Sync OFF ({reason})",
        "SETTINGS_PAT_LABEL": "GitHub PAT",
        "SETTINGS_PAT_SAVED": "Сохранён (…{preview})",
        "SETTINGS_PAT_ENV": "ENV {env}",
        "SETTINGS_PAT_NOT_SET": "Не задан",
        "SETTINGS_PAT_HINT": "Enter — вставь новый PAT или оставь пустым, чтобы очистить",
        "SETTINGS_SYNC_LABEL": "Синхронизация",
        "SETTINGS_SYNC_HINT": "Enter — включить или выключить автоматическую синхронизацию",
        "SETTINGS_SYNC_NO_REMOTE": "Недоступна (нет git remote)",
        "SETTINGS_SYNC_DISABLED": "Выключена",
        "SETTINGS_SYNC_NO_PAT": "Недоступна (нет PAT)",
        "SETTINGS_SYNC_ENABLED": "Включена",
        "SETTINGS_PROJECT_LABEL": "Проект GitHub",
        "SETTINGS_PROJECT_UNAVAILABLE": "недоступно",
        "SETTINGS_BOOTSTRAP_LABEL": "Инициализировать git + origin",
        "SETTINGS_BOOTSTRAP_VALUE": "Создать репо и push",
        "SETTINGS_BOOTSTRAP_HINT": "Enter — ввести URL (https://github.com/owner/repo.git), будет git init/add/push",
        "SETTINGS_PROJECT_URL_LABEL": "Project URL",
        "SETTINGS_PROJECT_URL_HINT": "g — открыть в браузере",
        "SETTINGS_PROJECT_NUMBER_LABEL": "Номер проекта",
        "SETTINGS_PROJECT_NUMBER_HINT": "Enter — обновить номер Project v2",
        "SETTINGS_POOL_LABEL": "Пул потоков",
        "SETTINGS_POOL_HINT": "Enter — задать размер пула sync (0=auto). Ограничивает API бюджет.",
        "SETTINGS_LAST_PULL_LABEL": "Последний pull/push",
        "SETTINGS_LAST_PULL_HINT": "Обновляется после успешной синхронизации",
        "SETTINGS_RATE_LABEL": "Rate limit",
        "SETTINGS_RATE_HINT": "GitHub remaining/reset (обновляется после запросов)",
        "SETTINGS_REMOTE_LABEL": "Git remote",
        "SETTINGS_REMOTE_HINT": "Используется для автоопределения Projects; меняется через git remote set-url origin",
        "SETTINGS_REFRESH_LABEL": "Перечитать поля",
        "SETTINGS_REFRESH_VALUE": "Обновить кеш GraphQL",
        "SETTINGS_REFRESH_HINT": "Enter — сбросить кеш полей Projects перед синхронизацией",
        "SETTINGS_REFRESH_DISABLED": "Нужен выбранный проект и PAT",
        "SETTINGS_VALIDATE_PAT_LABEL": "Проверить PAT",
        "SETTINGS_VALIDATE_PAT_HINT": "Enter — проверить токен через GitHub GraphQL",
        "SETTINGS_VALIDATE_PAT_DISABLED": "Сначала сохрани PAT",
        "LABEL_SUBTASKS_JSON": "JSON массив подзадач",
        "ERR_INVALID_FOLDER": "Недопустимая папка",
        "ERR_NO_LAST_TASK": "Нет последней задачи: вызови apply_task show/list/next для привязки контекста",
        "ERR_JSON_ARRAY_REQUIRED": "JSON должен быть массивом объектов",
        "ERR_JSON_ELEMENT_OBJECT": "Элемент {idx} должен быть объектом",
        "ERR_JSON_ELEMENT_TITLE": "Элемент {idx}: отсутствует 'title'",
        "ERR_JSON_ELEMENT_CRITERIA": "Элемент {idx}: укажи хотя бы один критерий выполнения",
        "ERR_JSON_ELEMENT_TESTS": "Элемент {idx}: укажи тесты для проверки",
        "ERR_JSON_ELEMENT_BLOCKERS": "Элемент {idx}: укажи блокеры/зависимости",
        "ERR_JSON_INVALID": "Невалидный JSON: {error}",
        "ERR_JSON_FORMAT_HINT": "Используй JSON массив. Пример: '[{\"title\":\"Design cache rollout >=20 chars\",\"criteria\":[\"hit ratio >80%\"],\"tests\":[\"pytest -k cache\"],\"blockers\":[\"redis downtime\"]}]'\\nПричина: {error}",
        "PROMPT_ABORTED": "[X] Прервано",
        "PROMPT_REQUIRED": "[!] Обязательное поле",
        "PROMPT_EMPTY_TO_FINISH": "(пустая строка для завершения):",
        "PROMPT_MIN_ITEMS": "[!] Минимум {count} элементов",
        "PROMPT_SUBTASK_HEADER": "[C] Подзадача {index}:",
        "PROMPT_SUBTASK_TITLE_REQ": "  Название (минимум 20 символов)",
        "PROMPT_SUBTASK_TITLE_SHORT": "  [!] Слишком короткое ({length}/20). Добавь детали",
        "PROMPT_SUBTASK_TITLE": "  Название",
        "PROMPT_SUBTASK_CRITERIA": "  Критерии выполнения",
        "PROMPT_SUBTASK_TESTS": "  Тесты для проверки",
        "PROMPT_SUBTASK_BLOCKERS": "  Блокеры/зависимости (обязательны, минимум 1)",
        "MSG_LIST_BUILT": "Список задач сформирован",
        "SUMMARY_TASKS": "{count} задач",
        "ERR_SHOW_NO_TASK": "Нет задачи для показа",
        "MSG_TASK_DETAILS": "Детали задачи",
        "ERR_DESCRIPTION_REQUIRED": "Описание обязательно и не может быть пустым/TBD",
        "ERR_TESTS_REQUIRED": "Укажи тесты/критерии успеха через --tests",
        "ERR_RISKS_REQUIRED": "Добавь риски через --risks (например: 'dep outage;perf regression')",
        "ERR_FLAGSHIP_SUBTASKS": "Подзадачи не соответствуют flagship-качеству",
        "MSG_VALIDATION_PASSED": "Валидация пройдена",
        "MSG_TASK_CREATED": "Задача {task_id} создана",
        "ERR_TASKS_DIR_MISSING": ".tasks каталог отсутствует",
        "ERR_FILE_PARSE": "{filename} не парсится",
        "ERR_FILE_NO_DESC": "{filename} без description",
        "ERR_FILE_NO_TESTS": "{filename} без tests/success_criteria",
        "MSG_ISSUES_FOUND": "Найдено {count} проблем(ы)",
        "MSG_ALL_TASKS_DONE": "Все задачи завершены{hint}",
        "MSG_NO_TASKS": "Нет задач",
        "MSG_RECOMMENDATIONS_READY": "Рекомендации сформированы{hint}",
        "SUMMARY_RECOMMENDATIONS": "{count} рекомендаций",
        "MSG_QUICK_DONE": "Все задачи выполнены{hint}",
        "SUMMARY_QUICK": "Top-{count} задач",
        "ERR_TASK_NEEDS_SUBTASKS": "Задача должна быть декомпозирована на подзадачи",
        "ERR_SUBTASKS_MIN": "Недостаточно подзадач ({count}). Минимум 3 для flagship-качества",
        "ERR_SUBTASK_PREFIX": "Подзадача {idx}: {issue}",
        "SPINNER_REFRESH_TASKS": "Обновление задач",
        "STATUS_TASKS_COUNT": "{count} задач",
        "STATUS_LOADING": "Загрузка",
        "STATUS_NO_TASKS": "Нет задач",
        "STATUS_NO_DATA": "Нет данных",
        "STATUS_CONTEXT": "Контекст: {ctx}",
        "STATUS_TASK_NOT_SELECTED": "Задача не выбрана",
        "DESCRIPTION_HEADER": "ОПИСАНИЕ:",
        "MARKS_SUFFIX": " — отметки:",
        "OFFSET_LABEL": " | Смещение: ",
        "NAV_STATUS_HINT": "q — выход | r — обновить | Enter — детали | d — завершить | e — редактировать | g — Git Projects",
        "NAV_CHECKPOINT_HINT": "  Чекпоинты: [✓ ✓ ·] = критерии / тесты / блокеры | ? — скрыть подсказку",
        "NAV_ARROWS_HINT": "← свернуть/к родителю · → раскрыть/к первому ребёнку · Enter: карточка · d: done",
        "NAV_EDIT_HINT": " Enter: сохранить | Esc: отменить",
        "EDIT_TASK_TITLE": "Редактирование названия задачи",
        "EDIT_TASK_DESCRIPTION": "Редактирование описания задачи",
        "EDIT_SUBTASK": "Редактирование подзадачи",
        "EDIT_CRITERION": "Редактирование критерия",
        "EDIT_TEST": "Редактирование теста",
        "EDIT_BLOCKER": "Редактирование блокера",
        "EDIT_PROJECT_NUMBER": "Номер проекта",
        "EDIT_GENERIC": "Редактирование",
        "BTN_VALIDATE_PAT": "[ Проверить PAT (F5) ]",
        "TASK_LIST_EMPTY": "Нет задач",
        "TABLE_HEADER_TASK": "Задача",
        "TABLE_HEADER_PROGRESS": "%",
        "TABLE_HEADER_SUBTASKS": "Σ",
        "SIDE_EMPTY_TASKS": "Нет задач",
        "SIDE_NO_DATA": "Нет данных",
        "STATUS_TASK_NOT_SELECTED": "Задача не выбрана",
        "DUR_DAYS_SUFFIX": "д",
        "DUR_HOURS_SUFFIX": "ч",
        "DUR_MINUTES_SUFFIX": "м",
        "DUR_LT_HOUR": "<1ч",
        "SETTINGS_UNAVAILABLE": "Настройки недоступны",
        "SETTINGS_TITLE": "НАСТРОЙКИ GITHUB PROJECTS",
        "ERR_PARENT_REQUIRED": "Укажи parent: --parent TASK-XXX",
        "GUIDED_ONLY_INTERACTIVE": "[X] Мастер доступен только в интерактивном терминале",
        "GUIDED_USE_PARAMS": "  Используй: apply_task create с параметрами",
        "GUIDED_TITLE": "[>>] МАСТЕР: Создание задачи flagship-качества",
        "GUIDED_STEP1": "[DESC] Шаг 1/5: Базовая информация",
        "GUIDED_TASK_TITLE": "Название задачи",
        "GUIDED_PARENT_ID": "ID родительской задачи (например: TASK-001)",
        "GUIDED_DESCRIPTION": "Описание (не TBD)",
        "GUIDED_DESCRIPTION_TBD": "  [!] Описание не может быть 'TBD'",
        "GUIDED_STEP2": "[TAG]  Шаг 2/5: Контекст и метаданные",
        "GUIDED_CONTEXT": "Дополнительный контекст",
        "GUIDED_TAGS": "Теги (через запятую)",
        "GUIDED_STEP3": "[WARN]  Шаг 3/5: Риски",
        "GUIDED_RISKS": "Риски проекта",
        "GUIDED_STEP4": "[SUB] Шаг 4/5: Критерии успеха и тесты",
        "GUIDED_TESTS": "Критерии успеха / Тесты",
        "GUIDED_STEP5": "[TASK] Шаг 5/5: Подзадачи (минимум 3)",
        "GUIDED_ADD_MORE": "\nДобавить ещё подзадачу?",
        "GUIDED_VALIDATION": "\n🔍 Валидация flagship-качества...",
        "GUIDED_WARN_ISSUES": "[WARN]  Обнаружены проблемы:",
        "GUIDED_CONTINUE": "\nПродолжить несмотря на проблемы?",
        "GUIDED_CANCELLED": "[X] Создание отменено",
        "GUIDED_SAVING": "\n[SAVE] Создание задачи...",
        "GUIDED_SUCCESS": "[SUB] УСПЕШНО: Создана задача {task_id}",
        "GUIDED_PARENT": "[DEP] Родитель: {parent}",
        "GUIDED_SUBTASK_COUNT": "[STAT] Подзадач: {count}",
        "GUIDED_CRITERIA_COUNT": "[SUB] Критериев: {count}",
        "GUIDED_RISKS_COUNT": "[WARN]  Рисков: {count}",
        "ERR_STATUS_REQUIRED": "Укажи статус: OK | WARN | FAIL",
        "ERR_NO_TASK_AND_LAST": "Не указан ID задачи и нет последней",
        "MSG_STATUS_UPDATED": "Статус {task_id} обновлён",
        "ERR_STATUS_NOT_UPDATED": "Статус не обновлён",
        "ERR_SUBTASK_TITLE_MIN": "Подзадача должна содержать как минимум 20 символов с деталями",
        "ERR_SUBTASK_INDEX": "Неверный индекс подзадачи",
        "ERR_TASK_UNAVAILABLE": "Задача недоступна",
        "ERR_SUBTASK_NOT_FOUND": "Подзадача не найдена",
        "ERR_TASK_ID_OR_GLOB": "Укажи task_id или --glob",
        "ERR_FILTER_REQUIRED": "Укажи хотя бы один фильтр: --tag/--status/--phase или --glob",
        "ERR_TOKEN_OR_UNSET": "Укажи --token или --unset",
        "REQ_MIN_SUBTASKS": "Минимум 3 подзадачи",
        "REQ_MIN_TITLE": "Каждая подзадача >=20 символов",
        "REQ_EXPLICIT_CHECKPOINTS": "Explicit success criteria/tests/blockers",
        "REQ_ATOMIC": "Атомарные действия без 'и затем'",
    },
    "uk": {
        "TITLE": "ЗАГОЛОВОК",
        "SUBTASKS": "ПІДЗАВДАННЯ",
        "SUBTASK_DETAILS": "ДЕТАЛІ ПІДЗАВДАННЯ",
        "CRITERIA": "Критерії",
        "TESTS": "Тести",
        "BLOCKERS": "Блокери",
        "DESCRIPTION": "Опис",
        "BLOCKERS_HEADER": "Блокери",
        "DOMAIN": "Папка",
        "PHASE": "Фаза",
        "COMPONENT": "Компонент",
        "PARENT": "Батько",
        "DESCRIPTION_MISSING": "Опис відсутній",
        "PRIORITY": "Пріоритет",
        "PROGRESS": "Прогрес",
        "COMPLETED_SUFFIX": "завершено",
        "TAGS": "Теги",
        "STATUS_DONE": "DONE",
        "STATUS_IN_PROGRESS": "IN PROGRESS",
        "STATUS_BACKLOG": "BACKLOG",
        "CHECKPOINT_CRITERIA": "Критерії",
        "CHECKPOINT_TESTS": "Тести",
        "CHECKPOINT_BLOCKERS": "Блокери",
        "LOG_CRITERIA": "Критерії",
        "LOG_TESTS": "Тести",
        "LOG_BLOCKERS": "Блокери",
        "DESCRIPTION_PROMPT": "Опис",
        "DOMAIN_LABEL": "Папка",
        "PHASE_LABEL": "Фаза",
        "COMPONENT_LABEL": "Компонент",
        "TAGS_LABEL": "Теги",
        "PARENT_LABEL": "Батько",
        "BLOCKERS_LABEL": "Блокери",
        "LANGUAGE_LABEL": "Мова",
        "LANGUAGE_HINT": "Enter — перемкнути мову (en, ru, uk, es, fr, zh, hi, ar)",
        "CLIPBOARD_EMPTY": "Буфер порожній або недоступний",
        "OPTION_DISABLED": "Опція недоступна",
    },
    "es": {
        "TITLE": "TÍTULO",
        "SUBTASKS": "SUBTAREAS",
        "SUBTASK_DETAILS": "DETALLES DE SUBTAREA",
        "CRITERIA": "Criterios",
        "TESTS": "Pruebas",
        "BLOCKERS": "Bloqueadores",
        "DESCRIPTION": "Descripción",
        "BLOCKERS_HEADER": "Bloqueadores",
        "DESCRIPTION_MISSING": "Sin descripción",
        "PRIORITY": "Prioridad",
        "PROGRESS": "Progreso",
        "COMPLETED_SUFFIX": "completadas",
        "TAGS": "Etiquetas",
        "STATUS_DONE": "DONE",
        "STATUS_IN_PROGRESS": "IN PROGRESS",
        "STATUS_BACKLOG": "BACKLOG",
        "CHECKPOINT_CRITERIA": "Criterios",
        "CHECKPOINT_TESTS": "Pruebas",
        "CHECKPOINT_BLOCKERS": "Bloqueadores",
        "LOG_CRITERIA": "Criterios",
        "LOG_TESTS": "Pruebas",
        "LOG_BLOCKERS": "Bloqueadores",
        "DESCRIPTION_PROMPT": "Descripción",
        "DOMAIN_LABEL": "Dominio",
        "PHASE_LABEL": "Fase",
        "COMPONENT_LABEL": "Componente",
        "TAGS_LABEL": "Etiquetas",
        "PARENT_LABEL": "Padre",
        "BLOCKERS_LABEL": "Bloqueadores",
        "LANGUAGE_LABEL": "Idioma",
        "LANGUAGE_HINT": "Enter — cambiar idioma (en, ru, uk, es, fr, zh, hi, ar)",
        "CLIPBOARD_EMPTY": "Portapapeles vacío o no disponible",
        "OPTION_DISABLED": "Opción no disponible",
    },
    "fr": {
        "TITLE": "TITRE",
        "SUBTASKS": "SOUS-TÂCHES",
        "SUBTASK_DETAILS": "DÉTAILS DE SOUS-TÂCHE",
        "CRITERIA": "Critères",
        "TESTS": "Tests",
        "BLOCKERS": "Bloqueurs",
        "DESCRIPTION": "Description",
        "BLOCKERS_HEADER": "Bloqueurs",
        "DESCRIPTION_MISSING": "Pas de description",
        "PRIORITY": "Priorité",
        "PROGRESS": "Progression",
        "COMPLETED_SUFFIX": "terminées",
        "TAGS": "Tags",
        "STATUS_DONE": "DONE",
        "STATUS_IN_PROGRESS": "IN PROGRESS",
        "STATUS_BACKLOG": "BACKLOG",
        "CHECKPOINT_CRITERIA": "Critères",
        "CHECKPOINT_TESTS": "Tests",
        "CHECKPOINT_BLOCKERS": "Bloqueurs",
        "LOG_CRITERIA": "Critères",
        "LOG_TESTS": "Tests",
        "LOG_BLOCKERS": "Bloqueurs",
        "DESCRIPTION_PROMPT": "Description",
        "DOMAIN_LABEL": "Domaine",
        "PHASE_LABEL": "Phase",
        "COMPONENT_LABEL": "Composant",
        "TAGS_LABEL": "Tags",
        "PARENT_LABEL": "Parent",
        "BLOCKERS_LABEL": "Bloqueurs",
        "LANGUAGE_LABEL": "Langue",
        "LANGUAGE_HINT": "Enter — changer de langue (en, ru, uk, es, fr, zh, hi, ar)",
        "CLIPBOARD_EMPTY": "Presse-papiers vide ou indisponible",
        "OPTION_DISABLED": "Option indisponible",
    },
    "zh": {
        "TITLE": "标题",
        "SUBTASKS": "子任务",
        "SUBTASK_DETAILS": "子任务详情",
        "CRITERIA": "验收标准",
        "TESTS": "测试",
        "BLOCKERS": "阻碍",
        "DESCRIPTION": "描述",
        "BLOCKERS_HEADER": "阻碍",
        "DESCRIPTION_MISSING": "无描述",
        "PRIORITY": "优先级",
        "PROGRESS": "进度",
        "COMPLETED_SUFFIX": "完成",
        "TAGS": "标签",
        "STATUS_DONE": "DONE",
        "STATUS_IN_PROGRESS": "IN PROGRESS",
        "STATUS_BACKLOG": "BACKLOG",
        "CHECKPOINT_CRITERIA": "标准",
        "CHECKPOINT_TESTS": "测试",
        "CHECKPOINT_BLOCKERS": "阻碍",
        "LOG_CRITERIA": "标准",
        "LOG_TESTS": "测试",
        "LOG_BLOCKERS": "阻碍",
        "DESCRIPTION_PROMPT": "描述",
        "DOMAIN_LABEL": "域",
        "PHASE_LABEL": "阶段",
        "COMPONENT_LABEL": "组件",
        "TAGS_LABEL": "标签",
        "PARENT_LABEL": "父级",
        "BLOCKERS_LABEL": "阻碍",
        "LANGUAGE_LABEL": "语言",
        "LANGUAGE_HINT": "Enter — 切换语言 (en, ru, uk, es, fr, zh, hi, ar)",
        "CLIPBOARD_EMPTY": "剪贴板为空或不可用",
        "OPTION_DISABLED": "选项不可用",
    },
    "hi": {
        "TITLE": "शीर्षक",
        "SUBTASKS": "उप-कार्य",
        "SUBTASK_DETAILS": "उप-कार्य विवरण",
        "CRITERIA": "मानदंड",
        "TESTS": "परीक्षण",
        "BLOCKERS": "अवरोधक",
        "DESCRIPTION": "विवरण",
        "BLOCKERS_HEADER": "अवरोधक",
        "DESCRIPTION_MISSING": "विवरण अनुपलब्ध",
        "PRIORITY": "प्राथमिकता",
        "PROGRESS": "प्रगति",
        "COMPLETED_SUFFIX": "पूर्ण",
        "TAGS": "टैग",
        "STATUS_DONE": "DONE",
        "STATUS_IN_PROGRESS": "IN PROGRESS",
        "STATUS_BACKLOG": "BACKLOG",
        "CHECKPOINT_CRITERIA": "मानदंड",
        "CHECKPOINT_TESTS": "परीक्षण",
        "CHECKPOINT_BLOCKERS": "अवरोधक",
        "LOG_CRITERIA": "मानदंड",
        "LOG_TESTS": "परीक्षण",
        "LOG_BLOCKERS": "अवरोधक",
        "DESCRIPTION_PROMPT": "विवरण",
        "DOMAIN_LABEL": "डोमेन",
        "PHASE_LABEL": "चरण",
        "COMPONENT_LABEL": "घटक",
        "TAGS_LABEL": "टैग",
        "PARENT_LABEL": "अभिभावक",
        "BLOCKERS_LABEL": "अवरोधक",
        "LANGUAGE_LABEL": "भाषा",
        "LANGUAGE_HINT": "Enter — भाषा बदलें (en, ru, uk, es, fr, zh, hi, ar)",
        "CLIPBOARD_EMPTY": "क्लिपबोर्ड खाली या अनुपलब्ध",
        "OPTION_DISABLED": "विकल्प अनुपलब्ध",
    },
    "ar": {
        "TITLE": "العنوان",
        "SUBTASKS": "المهام الفرعية",
        "SUBTASK_DETAILS": "تفاصيل المهمة الفرعية",
        "CRITERIA": "المعايير",
        "TESTS": "الاختبارات",
        "BLOCKERS": "العوائق",
        "DESCRIPTION": "الوصف",
        "BLOCKERS_HEADER": "العوائق",
        "DESCRIPTION_MISSING": "لا يوجد وصف",
        "PRIORITY": "الأولوية",
        "PROGRESS": "التقدم",
        "COMPLETED_SUFFIX": "منجز",
        "TAGS": "الوسوم",
        "STATUS_DONE": "DONE",
        "STATUS_IN_PROGRESS": "IN PROGRESS",
        "STATUS_BACKLOG": "BACKLOG",
        "CHECKPOINT_CRITERIA": "المعايير",
        "CHECKPOINT_TESTS": "الاختبارات",
        "CHECKPOINT_BLOCKERS": "العوائق",
        "LOG_CRITERIA": "المعايير",
        "LOG_TESTS": "الاختبارات",
        "LOG_BLOCKERS": "العوائق",
        "DESCRIPTION_PROMPT": "الوصف",
        "DOMAIN_LABEL": "المجلد",
        "PHASE_LABEL": "المرحلة",
        "COMPONENT_LABEL": "المكون",
        "TAGS_LABEL": "الوسوم",
        "PARENT_LABEL": "الوالد",
        "BLOCKERS_LABEL": "العوائق",
        "LANGUAGE_LABEL": "اللغة",
        "LANGUAGE_HINT": "Enter — تغيير اللغة (en، ru، uk، es، fr، zh، hi، ar)",
        "CLIPBOARD_EMPTY": "الحافظة فارغة أو غير متاحة",
        "OPTION_DISABLED": "الخيار غير متاح",
    },
}


def _fill_lang_pack_defaults() -> None:
    base = LANG_PACK.get("en", {})
    for lang, values in LANG_PACK.items():
        if lang == "en":
            continue
        for key, val in base.items():
            values.setdefault(key, val)


_fill_lang_pack_defaults()


def translate(key: str, lang: Optional[str] = None, **kwargs) -> str:
    base = LANG_PACK.get("en", {})
    active_lang = lang or get_user_lang() or "en"
    lang_map = LANG_PACK.get(active_lang, base)
    template = lang_map.get(key) or base.get(key, key)
    try:
        return template.format(**kwargs)
    except Exception:
        return template


def _get_sync_service() -> ProjectsSyncService:
    """Factory used outside TaskManager to obtain sync adapter."""
    return ProjectsSyncService(get_projects_sync())


def current_timestamp() -> str:
    """Возвращает локальное время с точностью до минут для метаданных задач."""
    return datetime.now().strftime(TIMESTAMP_FORMAT)


def validate_pat_token_http(token: str, timeout: float = 10.0) -> Tuple[bool, str]:
    if not token:
        return False, "PAT missing"
    query = "query { viewer { login } }"
    headers = {"Authorization": f"bearer {token}", "Accept": "application/vnd.github+json"}
    try:
        resp = requests.post(GITHUB_GRAPHQL, json={"query": query}, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        return False, f"Network unavailable: {exc}"
    if resp.status_code >= 400:
        return False, f"GitHub replied {resp.status_code}: {resp.text[:120]}"
    payload = resp.json()
    if payload.get("errors"):
        err = payload["errors"][0].get("message", "Unknown error")
        return False, err
    login = ((payload.get("data") or {}).get("viewer") or {}).get("login")
    if not login:
        return False, "Response missing viewer"
    return True, f"PAT valid (viewer={login})"


from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.input.ansi_escape_sequences import REVERSE_ANSI_SEQUENCES
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import HSplit, Layout, Window, VSplit
from prompt_toolkit.layout.containers import DynamicContainer
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.controls import FormattedTextControl, BufferControl
from prompt_toolkit.mouse_events import MouseEventType, MouseEvent, MouseButton, MouseModifier
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import TextArea
from prompt_toolkit.clipboard import InMemoryClipboard
try:  # pragma: no cover - optional dependency
    from prompt_toolkit.clipboard.pyperclip import PyperclipClipboard
except Exception:  # pragma: no cover
    PyperclipClipboard = None
from http.server import BaseHTTPRequestHandler, HTTPServer


# ============================================================================
# DATA MODELS
# ============================================================================
# Domain entities SubTask/TaskDetail импортируются из core.


def _iso_timestamp() -> str:
    """Возвращает ISO-8601 timestamp с UTC."""
    return datetime.now(timezone.utc).isoformat()


def _load_input_source(raw: str, label: str) -> str:
    """Load text payload from string, file, or STDIN."""
    source = (raw or "").strip()
    if not source:
        return source
    if source == "-":
        data = sys.stdin.read()
        if not data.strip():
            raise SubtaskParseError(f"STDIN is empty: provide {label}")
        return data
    if source.startswith("@"):
        path_str = source[1:].strip()
        if not path_str:
            raise SubtaskParseError(f"Specify path to {label} after '@'")
        file_path = Path(path_str).expanduser()
        if not file_path.exists():
            raise SubtaskParseError(f"File not found: {file_path}")
        return file_path.read_text(encoding="utf-8")
    return source


def _load_subtasks_source(raw: str) -> str:
    return _load_input_source(raw, translate("LABEL_SUBTASKS_JSON"))


def _flatten_subtasks(subtasks: List[SubTask], prefix: str = "") -> List[Tuple[str, SubTask]]:
    flat: List[Tuple[str, SubTask]] = []
    for idx, st in enumerate(subtasks):
        path = f"{prefix}.{idx}" if prefix else str(idx)
        flat.append((path, st))
        flat.extend(_flatten_subtasks(st.children, path))
    return flat


def _find_subtask_by_path(subtasks: List[SubTask], path: str) -> Tuple[Optional[SubTask], Optional[SubTask], Optional[int]]:
    parts_raw = [p for p in path.split(".") if p.strip() != ""]
    if not parts_raw:
        return None, None, None
    try:
        parts = [int(p) for p in parts_raw]
    except ValueError:
        return None, None, None
    current_list = subtasks
    parent_node = None
    for pos, idx in enumerate(parts):
        if idx < 0 or idx >= len(current_list):
            return None, None, None
        target = current_list[idx]
        if pos == len(parts) - 1:
            return target, parent_node, idx
        parent_node = target
        current_list = target.children
    return None, None, None


def _attach_subtask(subtasks: List[SubTask], parent_path: Optional[str], new_subtask: SubTask) -> bool:
    if not parent_path:
        subtasks.append(new_subtask)
        return True
    parent, _, _ = _find_subtask_by_path(subtasks, parent_path)
    if not parent:
        return False
    parent.children.append(new_subtask)
    return True


def subtask_to_dict(subtask: SubTask) -> Dict[str, Any]:
    """Структурированное представление подзадачи."""
    return {
        "title": subtask.title,
        "completed": subtask.completed,
        "success_criteria": list(subtask.success_criteria),
        "tests": list(subtask.tests),
        "blockers": list(subtask.blockers),
        "criteria_confirmed": subtask.criteria_confirmed,
        "tests_confirmed": subtask.tests_confirmed,
        "blockers_resolved": subtask.blockers_resolved,
        "criteria_notes": list(subtask.criteria_notes),
        "tests_notes": list(subtask.tests_notes),
        "blockers_notes": list(subtask.blockers_notes),
    }


def task_to_dict(task: TaskDetail, include_subtasks: bool = False) -> Dict[str, Any]:
    """Структурированное представление задачи."""
    data: Dict[str, Any] = {
        "id": task.id,
        "title": task.title,
        "status": task.status,
        "progress": task.calculate_progress(),
        "priority": task.priority,
        "domain": task.domain,
        "phase": task.phase,
        "component": task.component,
        "parent": task.parent,
        "tags": list(task.tags),
        "assignee": task.assignee,
        "blocked": task.blocked,
        "blockers": list(task.blockers),
        "description": task.description,
        "context": task.context,
        "success_criteria": list(task.success_criteria),
        "dependencies": list(task.dependencies),
        "next_steps": list(task.next_steps),
        "problems": list(task.problems),
        "risks": list(task.risks),
        "history": list(task.history),
        "subtasks_count": len(task.subtasks),
        "project_remote_updated": task.project_remote_updated,
        "project_issue_number": task.project_issue_number,
    }
    if include_subtasks:
        data["subtasks"] = [subtask_to_dict(st) for st in task.subtasks]
    return data


def structured_response(
    command: str,
    *,
    status: str = "OK",
    message: str = "",
    payload: Optional[Dict[str, Any]] = None,
    summary: Optional[str] = None,
    exit_code: int = 0,
) -> int:
    """Единый формат вывода для всех неинтерактивных команд."""
    body: Dict[str, Any] = {
        "command": command,
        "status": status,
        "message": message,
        "timestamp": _iso_timestamp(),
        "payload": payload or {},
    }
    if summary:
        body["summary"] = summary
    print(json.dumps(body, ensure_ascii=False, indent=2))
    return exit_code


def structured_error(command: str, message: str, *, payload: Optional[Dict[str, Any]] = None, status: str = "ERROR") -> int:
    """Сокращение для ошибок."""
    return structured_response(command, status=status, message=message, payload=payload, exit_code=1)


def validation_response(command: str, success: bool, message: str, payload: Optional[Dict[str, Any]] = None) -> int:
    body = payload.copy() if payload else {}
    body["mode"] = "validate-only"
    label = f"{command}.validate"
    status = "OK" if success else "ERROR"
    return structured_response(
        label,
        status=status,
        message=message,
        payload=body,
        summary=message,
        exit_code=0 if success else 1,
    )


@dataclass
class Task:
    name: str
    status: Status
    description: str
    category: str
    completed: bool = False
    task_file: Optional[str] = None
    progress: int = 0
    subtasks_count: int = 0
    subtasks_completed: int = 0
    id: Optional[str] = None
    parent: Optional[str] = None
    detail: Optional[TaskDetail] = None
    domain: str = ""
    phase: str = ""
    component: str = ""
    blocked: bool = False




class TaskManager:
    def __init__(self, tasks_dir: Path = Path(".tasks"), repository: Optional[TaskRepository] = None, sync_service: Optional[SyncService] = None, sync_provider=None):
        self.tasks_dir = tasks_dir
        self.tasks_dir.mkdir(exist_ok=True)
        self.repo: TaskRepository = repository or FileTaskRepository(tasks_dir)
        # sync_provider оставлен для обратной совместимости; sync_service — основной путь
        base_sync = sync_service or (ProjectsSyncService(sync_provider()) if sync_provider else ProjectsSyncService(get_projects_sync()))
        self.sync_service: SyncService = base_sync
        self.config = self.load_config()
        lang = get_user_lang() or "en"
        self.language = lang if lang in LANG_PACK else "en"
        self.auto_sync_message = ""
        self.last_sync_error = ""
        synced = self._auto_sync_all()
        if synced:
            self.auto_sync_message = translate("STATUS_MESSAGE_AUTO_SYNC", lang=self.language, count=synced)

    def _t(self, key: str, **kwargs) -> str:
        return translate(key, lang=getattr(self, "language", "en"), **kwargs)

    @staticmethod
    def sanitize_domain(domain: Optional[str]) -> str:
        """Безопасная нормализация подпапки внутри .tasks"""
        if not domain:
            return ""
        candidate = Path(domain.strip("/"))
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError(translate("ERR_INVALID_FOLDER"))
        return candidate.as_posix()

    @staticmethod
    def load_config() -> Dict:
        cfg = Path(".apply_task_projects.yaml")
        if cfg.exists():
            try:
                raw = yaml.safe_load(cfg.read_text()) or {}
            except Exception:
                return {}
            project = (raw.get("project") or {}) if isinstance(raw, dict) else {}
            return {"auto_sync": project.get("enabled", True)}
        return {}

    def _next_id(self) -> str:
        try:
            return self.repo.next_id()
        except Exception:
            ids = []
            for f in self.tasks_dir.rglob("TASK-*.task"):
                try:
                    ids.append(int(f.stem.split("-")[1]))
                except (IndexError, ValueError):
                    continue
            return f"TASK-{(max(ids) + 1 if ids else 1):03d}"

    def create_task(self, title: str, status: str = "FAIL", priority: str = "MEDIUM", parent: Optional[str] = None, domain: str = "", phase: str = "", component: str = "", folder: Optional[str] = None) -> TaskDetail:
        domain = self.sanitize_domain(folder or domain or derive_domain_explicit("", phase, component))
        now_value = current_timestamp()
        task = TaskDetail(
            id=self._next_id(),
            title=title,
            status=status,
            domain=domain,
            phase=phase,
            component=component,
            parent=parent,
            priority=priority,
            created=now_value,
            updated=now_value,
        )
        # НЕ сохраняем здесь - валидация должна пройти первой
        return task

    def save_task(self, task: TaskDetail) -> None:
        task.updated = current_timestamp()
        prog = task.calculate_progress()
        if prog == 100 and not task.blocked:
            task.status = "OK"
        task.domain = self.sanitize_domain(task.domain)
        self.repo.save(task)
        sync = self.sync_service
        if sync.enabled:
            changed = bool(sync.sync_task(task))
            if getattr(task, "_sync_error", None):
                self._report_sync_error(task._sync_error)
                task._sync_error = None
            if changed:
                self.repo.save(task)

    def load_task(self, task_id: str, domain: str = "") -> Optional[TaskDetail]:
        task = self.repo.load(task_id, domain)
        if not task:
            return None
        if task.subtasks:
            prog = task.calculate_progress()
            if prog == 100 and not task.blocked and task.status != "OK":
                task.status = "OK"
                self.save_task(task)
        sync = self.sync_service
        if sync.enabled and task.project_item_id:
            sync.pull_task_fields(task)
        return task

    def _report_sync_error(self, message: str) -> None:
        logging.getLogger("apply_task.sync").warning(message)
        self.last_sync_error = f"SYNC ERROR: {message[:60]}"

    def list_tasks(self, domain: str = "", skip_sync: bool = False) -> List[TaskDetail]:
        tasks: List[TaskDetail] = self.repo.list(domain, skip_sync=skip_sync)
        for parsed in tasks:
            if parsed.subtasks:
                prog = parsed.calculate_progress()
                if prog == 100 and not parsed.blocked and parsed.status != "OK":
                    parsed.status = "OK"
                    self.save_task(parsed)
            if not skip_sync:
                sync = self.sync_service
                if sync.enabled and parsed.project_item_id:
                    sync.pull_task_fields(parsed)
        return sorted(tasks, key=lambda t: t.id)

    def _auto_sync_all(self) -> int:
        if not self.config.get("auto_sync", True):
            return 0
        base_sync = self.sync_service
        if not base_sync.enabled or getattr(base_sync, "_full_sync_done", False):
            return 0
        setattr(base_sync, "_full_sync_done", True)
        tasks_to_sync: List[Tuple[TaskDetail, Path]] = [(t, Path(t.filepath)) for t in self.repo.list("", skip_sync=True)]
        if not tasks_to_sync:
            return 0

        def worker(entry: Tuple[TaskDetail, Path]) -> bool:
            task, file_path = entry
            sync = self._make_parallel_sync(base_sync)
            changed = sync.sync_task(task) if sync.enabled else False
            if getattr(task, "_sync_error", None):
                self._report_sync_error(task._sync_error)
                task._sync_error = None
            if changed:
                file_path.write_text(task.to_file_content(), encoding="utf-8")
            return changed

        max_workers = self._compute_worker_count(len(tasks_to_sync))
        changed_count = 0
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(worker, t) for t in tasks_to_sync]
            for f in as_completed(futures):
                try:
                    if f.result():
                        changed_count += 1
                except Exception as exc:  # pragma: no cover - safety
                    logging.getLogger("apply_task.sync").warning("Auto-sync worker failed: %s", exc)
        if changed_count:
            base_sync.last_push = datetime.now().strftime(TIMESTAMP_FORMAT)
        return changed_count

    def _make_parallel_sync(self, base_sync):
        """Сохраняем совместимость с тестами: возвращаем клон сервиса."""
        return base_sync.clone() if hasattr(base_sync, "clone") else base_sync

    def _compute_worker_count(self, queue_size: int) -> int:
        env_override = os.getenv("APPLY_TASK_SYNC_WORKERS")
        if env_override and env_override.isdigit():
            value = int(env_override)
            if value > 0:
                return max(1, min(value, queue_size or value))
        sync = self.sync_service
        cfg_workers = getattr(sync.config, "workers", None) if sync and sync.config else None
        if cfg_workers:
            return max(1, min(int(cfg_workers), queue_size or int(cfg_workers)))
        auto = min(max(2, (os.cpu_count() or 2)), 8)
        if queue_size:
            auto = min(auto, queue_size)
        return max(1, auto)

    def compute_signature(self) -> int:
        return self.repo.compute_signature()

    def update_task_status(self, task_id: str, status: str, domain: str = "") -> Tuple[bool, Optional[Dict[str, str]]]:
        task = self.load_task(task_id, domain)
        if not task:
            return False, {"code": "not_found", "message": self._t("ERR_TASK_NOT_FOUND", task_id=task_id)}
        # Flagship-проверка перед установкой OK
        if status == "OK":
            if task.subtasks and task.calculate_progress() < 100:
                return False, {"code": "validation", "message": self._t("ERR_TASK_NOT_COMPLETE")}
            if not task.success_criteria:
                return False, {"code": "validation", "message": self._t("ERR_TASK_NO_CRITERIA_TESTS")}
            for idx, st in enumerate(task.subtasks, 1):
                if not st.success_criteria:
                    return False, {
                        "code": "validation",
                        "message": self._t("ERR_SUBTASK_NO_CRITERIA").format(idx=idx, title=st.title),
                    }
                if not st.tests:
                    return False, {
                        "code": "validation",
                        "message": self._t("ERR_SUBTASK_NO_TESTS").format(idx=idx, title=st.title),
                    }
            task.progress = 100
        else:
            task.status = status
            if status == "WARN" and task.progress == 0 and task.subtasks:
                task.progress = task.calculate_progress()
            if status == "FAIL" and task.progress == 0 and task.subtasks:
                task.progress = task.calculate_progress()

        task.status = status
        self.save_task(task)
        return True, None

    def add_subtask(self, task_id: str, title: str, domain: str = "", criteria: Optional[List[str]] = None, tests: Optional[List[str]] = None, blockers: Optional[List[str]] = None, parent_path: Optional[str] = None) -> Tuple[bool, Optional[str]]:
        task = self.load_task(task_id, domain)
        if not task:
            return False, "not_found"
        crit = [c.strip() for c in (criteria or []) if c.strip()]
        tst = [t.strip() for t in (tests or []) if t.strip()]
        bl = [b.strip() for b in (blockers or []) if b.strip()]
        if not crit or not tst or not bl:
            return False, "missing_fields"
        if parent_path:
            ok = _attach_subtask(task.subtasks, parent_path, SubTask(False, title, crit, tst, bl))
            if not ok:
                return False, "path"
        else:
            task.subtasks.append(SubTask(False, title, crit, tst, bl))
        task.update_status_from_progress()
        self.save_task(task)
        return True, None

    def set_subtask(self, task_id: str, index: int, completed: bool, domain: str = "", path: Optional[str] = None) -> Tuple[bool, Optional[str]]:
        task = self.load_task(task_id, domain)
        if not task:
            return False, "not_found"
        if path:
            st, _, _ = _find_subtask_by_path(task.subtasks, path)
            if not st:
                return False, "index"
        else:
            if index < 0 or index >= len(task.subtasks):
                return False, "index"
            st = task.subtasks[index]
        if completed and not st.ready_for_completion():
            missing = []
            if not st.criteria_confirmed:
                missing.append(self._t("CHECKPOINT_CRITERIA"))
            if not st.tests_confirmed:
                missing.append(self._t("CHECKPOINT_TESTS"))
            if not st.blockers_resolved:
                missing.append(self._t("CHECKPOINT_BLOCKERS"))
            return False, self._t("ERR_SUBTASK_CHECKPOINTS").format(items=", ".join(missing))
        st.completed = completed
        task.update_status_from_progress()
        self.save_task(task)
        return True, None

    def update_subtask_checkpoint(self, task_id: str, index: int, checkpoint: str, value: bool, note: str = "", domain: str = "", path: Optional[str] = None) -> Tuple[bool, Optional[str]]:
        task = self.load_task(task_id, domain)
        if not task:
            return False, "not_found"
        if path:
            st, _, _ = _find_subtask_by_path(task.subtasks, path)
            if not st:
                return False, "index"
        else:
            if index < 0 or index >= len(task.subtasks):
                return False, "index"
            st = task.subtasks[index]
        checkpoint = checkpoint.lower()
        attr_map = {
            "criteria": ("criteria_confirmed", "criteria_notes"),
            "tests": ("tests_confirmed", "tests_notes"),
            "blockers": ("blockers_resolved", "blockers_notes"),
        }
        if checkpoint not in attr_map:
            return False, "unknown_checkpoint"
        flag_attr, notes_attr = attr_map[checkpoint]
        setattr(st, flag_attr, value)
        note = note.strip()
        if note:
            getattr(st, notes_attr).append(note)
        if not value:
            st.completed = False
        task.update_status_from_progress()
        self.save_task(task)
        return True, None

    def add_dependency(self, task_id: str, dep: str, domain: str = "") -> bool:
        task = self.load_task(task_id, domain)
        if not task:
            return False
        task.dependencies.append(dep)
        self.save_task(task)
        return True

    def move_task(self, task_id: str, new_domain: str) -> bool:
        target_domain = self.sanitize_domain(new_domain)
        return self.repo.move(task_id, target_domain)

    def move_glob(self, pattern: str, new_domain: str) -> int:
        target_domain = self.sanitize_domain(new_domain)
        return self.repo.move_glob(pattern, target_domain)

    def clean_tasks(self, tag: Optional[str] = None, status: Optional[str] = None, phase: Optional[str] = None, dry_run: bool = False) -> Tuple[List[str], int]:
        if dry_run:
            matched = [
                d.id
                for d in self.repo.list("", skip_sync=True)
                if (not tag or tag.strip().lower() in [t.lower() for t in d.tags])
                and (not status or (d.status or "").upper() == status.strip().upper())
                and (not phase or (d.phase or "").strip().lower() == phase.strip().lower())
            ]
            return matched, 0
        try:
            return self.repo.clean_filtered(tag or "", status or "", phase or "")
        except NotImplementedError:
            matched: List[str] = []
            removed = 0
            norm_tag = (tag or "").strip().lower()
            norm_status = (status or "").strip().upper()
            norm_phase = (phase or "").strip().lower()

            for detail in self.repo.list("", skip_sync=True):
                tags = [t.strip().lower() for t in (detail.tags or [])]
                if norm_tag and norm_tag not in tags:
                    continue
                if norm_status and (detail.status or "").upper() != norm_status:
                    continue
                if norm_phase and (detail.phase or "").strip().lower() != norm_phase:
                    continue
                matched.append(detail.id)
                if self.repo.delete(detail.id, detail.domain):
                    removed += 1
            return matched, removed

    def delete_task(self, task_id: str, domain: str = "") -> bool:
        return self.repo.delete(task_id, domain)


def save_last_task(task_id: str, domain: str = "") -> None:
    Path(".last").write_text(f"{task_id}@{domain}", encoding="utf-8")


def get_last_task() -> Tuple[Optional[str], Optional[str]]:
    last = Path(".last")
    if not last.exists():
        return None, None
    raw = last.read_text(encoding="utf-8").strip()
    if "@" in raw:
        tid, domain = raw.split("@", 1)
        return tid or None, domain or None
    return raw or None, None


def resolve_task_reference(
    raw_task_id: Optional[str],
    domain: Optional[str],
    phase: Optional[str],
    component: Optional[str],
) -> Tuple[str, str]:
    """
    Возвращает (task_id, domain) с поддержкой шорткатов:
    '.' / 'last' / '@last' / пустое значение → последняя задача из .last.
    """
    sentinel = (raw_task_id or "").strip()
    use_last = not sentinel or sentinel in (".", "last", "@last")
    if use_last:
        last_id, last_domain = get_last_task()
        if not last_id:
            raise ValueError(translate("ERR_NO_LAST_TASK"))
        resolved_domain = derive_domain_explicit(domain, phase, component) or (last_domain or "")
        return normalize_task_id(last_id), resolved_domain or ""
    resolved_domain = derive_domain_explicit(domain, phase, component)
    return normalize_task_id(sentinel), resolved_domain


def normalize_task_id(raw: str) -> str:
    value = raw.strip().upper()
    if re.match(r"^TASK-\d+$", value):
        num = int(value.split("-")[1])
        return f"TASK-{num:03d}"
    if value.isdigit():
        return f"TASK-{int(value):03d}"
    return value


def derive_domain_explicit(domain: Optional[str], phase: Optional[str], component: Optional[str]) -> str:
    """При отсутствии явного domain строит путь из phase/component."""
    if domain:
        return TaskManager.sanitize_domain(domain)
    parts = []
    if phase:
        parts.append(phase.strip("/"))
    if component:
        parts.append(component.strip("/"))
    if not parts:
        return ""
    return TaskManager.sanitize_domain("/".join(parts))


def derive_folder_explicit(domain: Optional[str], phase: Optional[str], component: Optional[str]) -> str:
    """Совместимость: alias для derive_domain_explicit (используется в старых тестах)."""
    return derive_domain_explicit(domain, phase, component)


def parse_smart_title(title: str) -> Tuple[str, List[str], List[str]]:
    tags = re.findall(r"#(\w+)", title)
    deps = re.findall(r"@(TASK-\d+)", title.upper())
    clean = re.sub(r"#\w+", "", title)
    clean = re.sub(r"@TASK-\d+", "", clean, flags=re.IGNORECASE).strip()
    return clean, [t.lower() for t in tags], deps


CHECKLIST_SECTIONS = [
    (
        "plan",
        ["plan", "break", "шаг"],
        "Plan: break work into atomic steps with measurable outcomes",
        ["step", "milestone", "outcome", "scope", "estimate"],
    ),
    (
        "validation",
        ["test", "lint", "вали", "qa"],
        "Validation plan: tests/linters per step and commit checkpoints",
        ["test", "pytest", "unit", "integration", "lint", "coverage", "commit", "checkpoint"],
    ),
    (
        "risks",
        ["risk", "dependency", "риск", "завис", "блок"],
        "Risk scan: failures, dependencies, bottlenecks",
        ["risk", "dependency", "blocker", "bottleneck", "assumption"],
    ),
    (
        "readiness",
        ["readiness", "ready", "done", "criteria", "dod", "готов", "metric"],
        "Readiness criteria: DoD, coverage/perf metrics, expected behavior",
        ["DoD", "definition", "coverage", "perf", "metric", "acceptance", "criteria"],
    ),
    (
        "execute",
        ["execute", "implement", "исполн", "build"],
        "Execute steps with per-step validation and record results",
        ["implement", "code", "wire", "build", "validate"],
    ),
    (
        "final",
        ["final", "full", "release", "финаль", "итог"],
        "Final verification: full tests/linters, metrics check, release/commit prep",
        ["regression", "full", "release", "report", "metrics", "handoff"],
    ),
]


def validate_subtasks_coverage(subtasks: List[SubTask]) -> Tuple[bool, List[str]]:
    """Checks that all required checklist sections are covered with substantive content."""
    present: Dict[str, SubTask] = {}
    for st in subtasks:
        low = st.title.lower()
        for name, keywords, *_ in CHECKLIST_SECTIONS:
            if any(k in low for k in keywords):
                # first match wins to keep deterministic error reporting
                present.setdefault(name, st)

    missing = [name for name, *_ in CHECKLIST_SECTIONS if name not in present]
    return not missing, missing


def validate_subtasks_quality(subtasks: List[SubTask]) -> Tuple[bool, List[str]]:
    """Проверяет, что каждая подзадача детализирована: есть двоеточия, ключевые блоки, достаточная длина."""
    issues: List[str] = []
    present: Dict[str, SubTask] = {}
    for _, st in _flatten_subtasks(subtasks):
        low = st.title.lower()
        for name, keywords, _, anchors in CHECKLIST_SECTIONS:
            if any(k in low for k in keywords) and any(a in low for a in anchors):
                # сохраняем самую подробную подзадачу для секции
                if name not in present or len(st.title) > len(present[name].title):
                    present[name] = st

    for name, _, desc, anchors in CHECKLIST_SECTIONS:
        st = present.get(name)
        if not st:
            continue
        text = st.title.strip()
        long_enough = len(text) >= 30
        has_colon = ":" in text
        has_any_anchor = any(a.lower() in text.lower() for a in anchors)
        if not (long_enough and has_colon and has_any_anchor):
            issues.append(f"{name}: добавь детали (>=30 символов, включи ':' и ключевые слова из темы)")
    return len(issues) == 0, issues


def validate_subtasks_structure(subtasks: List[SubTask]) -> Tuple[bool, List[str]]:
    """Каждая подзадача должна содержать критерии, тесты и блокеры."""
    issues: List[str] = []
    for idx, (_, st) in enumerate(_flatten_subtasks(subtasks), 1):
        missing = []
        if not st.success_criteria:
            missing.append("критерии")
        if not st.tests:
            missing.append("тесты")
        if not st.blockers:
            missing.append("блокеры")
        if missing:
            issues.append(f"Подзадача {idx}: добавь {', '.join(missing)}")
    return len(issues) == 0, issues


def validate_flagship_subtasks(subtasks: List[SubTask]) -> Tuple[bool, List[str]]:
    """
    Flagship-валидация: каждая подзадача должна иметь:
    - Критерии выполнения (success_criteria)
    - Тесты для проверки (tests)
    - Блокеры/зависимости и план снятия блокеров
    - Быть атомарной (не содержать составных действий)
    - Минимум 20 символов в описании
    """
    flat = _flatten_subtasks(subtasks)
    if not flat:
        return False, [translate("ERR_TASK_NEEDS_SUBTASKS")]

    if len(flat) < 3:
        return False, [translate("ERR_SUBTASKS_MIN", count=len(flat))]

    all_issues = []
    for idx, (_, st) in enumerate(flat, 1):
        valid, issues = st.is_valid_flagship()
        if not valid:
            all_issues.extend([translate("ERR_SUBTASK_PREFIX", idx=idx, issue=issue) for issue in issues])

    return len(all_issues) == 0, all_issues


# ============================================================================
# FLEXIBLE SUBTASK PARSING (JSON ONLY)
# ============================================================================


class SubtaskParseError(Exception):
    """Ошибка парсинга подзадач"""
    pass


def _to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "y", "ok", "done", "ready", "готов", "готово", "+")
    return bool(value)


def parse_subtasks_json(raw: str) -> List[SubTask]:
    """Парсинг подзадач из JSON формата"""
    try:
        data = json.loads(raw)
        if not isinstance(data, list):
            raise SubtaskParseError(translate("ERR_JSON_ARRAY_REQUIRED"))

        subtasks = []
        for idx, item in enumerate(data, 1):
            if not isinstance(item, dict):
                raise SubtaskParseError(translate("ERR_JSON_ELEMENT_OBJECT", idx=idx))

            title = item.get("title", "")
            if not title:
                raise SubtaskParseError(translate("ERR_JSON_ELEMENT_TITLE", idx=idx))

            criteria = item.get("criteria", item.get("success_criteria", []))
            tests = item.get("tests", [])
            blockers = item.get("blockers", [])

            if not isinstance(criteria, list):
                criteria = [str(criteria)]
            if not isinstance(tests, list):
                tests = [str(tests)]
            if not isinstance(blockers, list):
                blockers = [str(blockers)]

            if not criteria:
                raise SubtaskParseError(translate("ERR_JSON_ELEMENT_CRITERIA", idx=idx))
            if not tests:
                raise SubtaskParseError(translate("ERR_JSON_ELEMENT_TESTS", idx=idx))
            if not blockers:
                raise SubtaskParseError(translate("ERR_JSON_ELEMENT_BLOCKERS", idx=idx))

            criteria_notes = item.get("criteria_notes", [])
            tests_notes = item.get("tests_notes", [])
            blockers_notes = item.get("blockers_notes", [])
            if not isinstance(criteria_notes, list):
                criteria_notes = [str(criteria_notes)]
            if not isinstance(tests_notes, list):
                tests_notes = [str(tests_notes)]
            if not isinstance(blockers_notes, list):
                blockers_notes = [str(blockers_notes)]

            st = SubTask(
                False,
                title,
                criteria,
                tests,
                blockers,
                criteria_confirmed=_to_bool(item.get("criteria_confirmed", False)),
                tests_confirmed=_to_bool(item.get("tests_confirmed", False)),
                blockers_resolved=_to_bool(item.get("blockers_resolved", False)),
                criteria_notes=[str(n).strip() for n in criteria_notes if str(n).strip()],
                tests_notes=[str(n).strip() for n in tests_notes if str(n).strip()],
                blockers_notes=[str(n).strip() for n in blockers_notes if str(n).strip()],
            )
            subtasks.append(st)

        return subtasks
    except json.JSONDecodeError as e:
        raise SubtaskParseError(translate("ERR_JSON_INVALID", error=e))


def parse_subtasks_flexible(raw: str) -> List[SubTask]:
    """Парсинг подзадач в единственном поддерживаемом формате: JSON-массив объектов"""
    raw = raw.strip()

    if not raw:
        return []

    try:
        return parse_subtasks_json(raw)
    except SubtaskParseError as e:
        raise SubtaskParseError(translate("ERR_JSON_FORMAT_HINT", error=e))


# ============================================================================
# INTERACTIVE HELPERS
# ============================================================================


def is_interactive() -> bool:
    """Проверка что мы в интерактивном TTY режиме"""
    return sys.stdin.isatty() and sys.stdout.isatty()


def prompt(question: str, default: str = "") -> str:
    """Запрос строки от пользователя"""
    if default:
        question = f"{question} [{default}]"
    try:
        response = input(f"{question}: ").strip()
        return response if response else default
    except (EOFError, KeyboardInterrupt):
        print(f"\n{translate('PROMPT_ABORTED')}")
        sys.exit(1)


def prompt_required(question: str) -> str:
    """Запрос обязательной строки"""
    while True:
        response = prompt(question)
        if response:
            return response
        print(f"  {translate('PROMPT_REQUIRED')}")


def prompt_list(question: str, min_items: int = 0) -> List[str]:
    """Запрос списка строк (по одной на строку, пустая строка = конец)"""
    print(f"{question} {translate('PROMPT_EMPTY_TO_FINISH')}")
    items = []
    while True:
        try:
            line = input(f"  {len(items) + 1}. ").strip()
            if not line:
                if len(items) >= min_items:
                    break
                print(f"  {translate('PROMPT_MIN_ITEMS', count=min_items)}")
                continue
            items.append(line)
        except (EOFError, KeyboardInterrupt):
            print(f"\n{translate('PROMPT_ABORTED')}")
            sys.exit(1)
    return items


def confirm(question: str, default: bool = True) -> bool:
    """Запрос подтверждения (y/n)"""
    suffix = " [Y/n]" if default else " [y/N]"
    try:
        response = input(f"{question}{suffix}: ").strip().lower()
        if not response:
            return default
        return response in ('y', 'yes', 'д', 'да')
    except (EOFError, KeyboardInterrupt):
        print(f"\n{translate('PROMPT_ABORTED')}")
        sys.exit(1)


def prompt_subtask_interactive(index: int) -> SubTask:
    """Интерактивное создание подзадачи"""
    print(f"\n{translate('PROMPT_SUBTASK_HEADER', index=index)}")
    title = prompt_required(translate("PROMPT_SUBTASK_TITLE_REQ"))
    while len(title) < 20:
        print(translate("PROMPT_SUBTASK_TITLE_SHORT", length=len(title)))
        title = prompt_required(translate("PROMPT_SUBTASK_TITLE"))

    criteria = prompt_list(translate("PROMPT_SUBTASK_CRITERIA"), min_items=1)
    tests = prompt_list(translate("PROMPT_SUBTASK_TESTS"), min_items=1)
    blockers = prompt_list(translate("PROMPT_SUBTASK_BLOCKERS"), min_items=1)

    return SubTask(False, title, criteria, tests, blockers)


def subtask_flags(st: SubTask) -> Dict[str, bool]:
    return {
        "criteria": st.criteria_confirmed,
        "tests": st.tests_confirmed,
        "blockers": st.blockers_resolved,
    }


def load_template(kind: str, manager: TaskManager) -> Tuple[str, str]:
    cfg = manager.config.get("templates", {})
    tpl = cfg.get(kind, cfg.get("default", {})) or {}
    desc = tpl.get("description", "")
    tests = tpl.get("tests", "")
    if not desc and not tests:
        return "TBD", "acceptance"
    return desc, tests


# ============================================================================
# TUI RESPONSIVE LAYOUT
# ============================================================================


@dataclass
class ColumnLayout:
    """Определяет, какие колонки отображать и их ширину"""
    min_width: int
    columns: List[str]
    stat_w: int = 3
    prog_w: int = 6
    subt_w: int = 7
    title_min: int = 16
    notes_w: int = 12
    context_w: int = 12

    def has_column(self, name: str) -> bool:
        return name in self.columns

    def _base_min_widths(self, desired: Optional[Dict[str, int]] = None) -> Dict[str, int]:
        base = {
            'stat': self.stat_w,
            'progress': self.prog_w,
            'subtasks': self.subt_w,
            'title': self.title_min,
            'notes': self.notes_w,
            'context': self.context_w,
        }
        result: Dict[str, int] = {}
        for col in self.columns:
            width = base.get(col, 8)
            if desired and col in desired:
                width = max(width, desired[col])
            result[col] = max(1, width)
        return result

    def required_width(self, desired: Optional[Dict[str, int]] = None) -> int:
        widths = self._base_min_widths(desired)
        return sum(widths.values()) + len(self.columns) + 1

    def calculate_widths(self, term_width: int, desired: Optional[Dict[str, int]] = None) -> Dict[str, int]:
        """Рассчитывает ширины колонок так, чтобы таблица умещалась в терминал."""
        separators = len(self.columns) + 1  # количество вертикальных разделителей
        usable_width = max(len(self.columns), term_width - separators)
        widths = self._base_min_widths(desired)
        min_total = sum(widths.values())

        if min_total <= usable_width:
            remaining = usable_width - min_total
            flex_cols = [c for c in self.columns if c in ('title', 'notes', 'context', 'subtasks')]
            if not flex_cols:
                flex_cols = list(self.columns)
            weights = {col: (3 if col == 'title' else 1) for col in flex_cols}
            total_weight = max(1, sum(weights.values()))
            distributed = 0
            for col in flex_cols:
                share = (remaining * weights[col]) // total_weight
                widths[col] += share
                distributed += share
            leftover = remaining - distributed
            if leftover and flex_cols:
                widths[flex_cols[0]] += leftover
        else:
            deficit = min_total - usable_width
            shrink_order = [c for c in self.columns if c != 'stat'] or list(self.columns)
            min_limits = {'stat': max(2, self.stat_w - 1), 'progress': 2, 'subtasks': 3, 'title': 6, 'notes': 6, 'context': 6}
            while deficit > 0 and shrink_order:
                progressed = False
                for col in shrink_order:
                    limit = min_limits.get(col, 2)
                    if widths[col] > limit:
                        widths[col] -= 1
                        deficit -= 1
                        progressed = True
                        if deficit == 0:
                            break
                if not progressed:
                    break

        total = sum(widths.values()) + separators
        if total > term_width:
            overflow = total - term_width
            min_limits = {'stat': 1, 'progress': 2, 'subtasks': 2, 'title': 4, 'notes': 4, 'context': 4}
            for col in reversed(self.columns):
                reducible = max(0, widths[col] - min_limits.get(col, 1))
                if reducible <= 0:
                    continue
                take = min(reducible, overflow)
                widths[col] -= take
                overflow -= take
                if overflow == 0:
                    break

        return widths


class ResponsiveLayoutManager:
    """Управляет адаптивными layout в зависимости от ширины терминала"""

    LAYOUTS = [
        ColumnLayout(min_width=140, columns=['stat', 'title', 'progress', 'subtasks'], stat_w=4, prog_w=8, subt_w=8, title_min=22),
        ColumnLayout(min_width=110, columns=['stat', 'title', 'progress', 'subtasks'], stat_w=3, prog_w=7, subt_w=7, title_min=18),
        ColumnLayout(min_width=90, columns=['stat', 'title', 'progress', 'subtasks'], stat_w=3, prog_w=6, subt_w=6, title_min=16),
        ColumnLayout(min_width=72, columns=['stat', 'title', 'progress', 'subtasks'], stat_w=2, prog_w=5, subt_w=5, title_min=12),
        ColumnLayout(min_width=56, columns=['stat', 'title', 'progress'], stat_w=2, prog_w=5, title_min=12),
        ColumnLayout(min_width=0, columns=['stat', 'title'], stat_w=2, title_min=10),
    ]

    @classmethod
    def select_layout(cls, term_width: int) -> ColumnLayout:
        """Выбирает подходящий layout для текущей ширины терминала"""
        for layout in cls.LAYOUTS:
            effective_min = max(layout.min_width, layout.required_width())
            if term_width >= effective_min:
                return layout
        return cls.LAYOUTS[-1]


# ============================================================================
# TUI
# ============================================================================

THEMES: Dict[str, Dict[str, str]] = {
    "dark-olive": {
        "": "#d7dfe6",  # без принудительной подложки
        "status.ok": "#9ad974 bold",
        "status.warn": "#e5c07b bold",
        "status.fail": "#e06c75 bold",
        "status.unknown": "#7a7f85",
        "text": "#d7dfe6",
        "text.dim": "#97a0a9",
        "text.dimmer": "#6d717a",
        "text.cont": "#8d95a0",  # заметно темнее для продолжений
        "selected": "bg:#3b3b3b #d7dfe6 bold",  # мягкий серый селект для моно-режима
        "selected.ok": "bg:#3b3b3b #9ad974 bold",
        "selected.warn": "bg:#3b3b3b #f0c674 bold",
        "selected.fail": "bg:#3b3b3b #ff6b6b bold",
        "selected.unknown": "bg:#3b3b3b #e8eaec bold",
        "header": "#ffb347 bold",
        "border": "#4b525a",
        "icon.check": "#9ad974 bold",
        "icon.warn": "#f9ac60 bold",
        "icon.fail": "#ff5156 bold",
    },
    "dark-contrast": {
        "": "#e8eaec",  # без черной подложки
        "status.ok": "#b8f171 bold",
        "status.warn": "#f0c674 bold",
        "status.fail": "#ff6b6b bold",
        "status.unknown": "#8a9097",
        "text": "#e8eaec",
        "text.dim": "#a7b0ba",
        "text.dimmer": "#6f757d",
        "text.cont": "#939aa4",
        "selected": "bg:#3d4047 #e8eaec bold",  # мягкий серый селект для моно-режима
        "selected.ok": "bg:#3d4047 #b8f171 bold",
        "selected.warn": "bg:#3d4047 #f0c674 bold",
        "selected.fail": "bg:#3d4047 #ff6b6b bold",
        "selected.unknown": "bg:#3d4047 #e8eaec bold",
        "header": "#ffb347 bold",
        "border": "#5a6169",
        "icon.check": "#b8f171 bold",
        "icon.warn": "#f9ac60 bold",
        "icon.fail": "#ff5156 bold",
    },
}

DEFAULT_THEME = "dark-olive"


class InteractiveFormattedTextControl(FormattedTextControl):
    def __init__(self, *args, mouse_handler=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._external_mouse_handler = mouse_handler

    def mouse_handler(self, mouse_event: MouseEvent):
        if self._external_mouse_handler:
            result = self._external_mouse_handler(mouse_event)
            if result is not NotImplemented:
                return result
        return super().mouse_handler(mouse_event)


class TaskTrackerTUI:
    SELECTION_STYLE_BY_STATUS: Dict[Status, str] = {
        Status.OK: "selected.ok",
        Status.WARN: "selected.warn",
        Status.FAIL: "selected.fail",
        Status.UNKNOWN: "selected.unknown",
    }
    SPINNER_FRAMES: List[str] = ["⣿", "⡇", "⡏", "⡗", "⡟", "⡧", "⡯", "⡷", "⡿", "⢇", "⢏", "⢗", "⢟", "⢧", "⢯", "⢷", "⢿"]

    @staticmethod
    def get_theme_palette(theme: str) -> Dict[str, str]:
        base = THEMES.get(theme)
        if not base:
            base = THEMES[DEFAULT_THEME]
        return dict(base)  # defensive copy

    @classmethod
    def build_style(cls, theme: str) -> Style:
        palette = cls.get_theme_palette(theme)
        return Style.from_dict(palette)

    def __init__(self, tasks_dir: Path = Path(".tasks"), domain: str = "", phase: str = "", component: str = "", theme: str = DEFAULT_THEME, mono_select: bool = False):
        self.tasks_dir = tasks_dir
        self.manager = TaskManager(tasks_dir)
        self.domain_filter = domain
        self.phase_filter = phase
        self.component_filter = component
        self.tasks: List[Task] = []
        self.selected_index = 0
        self.current_filter: Optional[Status] = None
        self.detail_mode = False
        self.current_task_detail: Optional[TaskDetail] = None
        self.current_task: Optional[Task] = None
        self.detail_selected_index = 0
        self.detail_view_offset: int = 0
        self.navigation_stack = []
        self.task_details_cache: Dict[str, TaskDetail] = {}
        self._last_signature = None
        self._last_check = 0.0
        self.horizontal_offset = 0  # For horizontal scrolling
        self.detail_selected_path: str = ""
        self.theme_name = theme
        self.status_message: str = ""
        self.status_message_expires: float = 0.0
        self.help_visible: bool = False
        self.list_view_offset: int = 0
        self.settings_view_offset: int = 0
        self.footer_height: int = 9  # default footer height for task list
        self.mono_select = mono_select
        self.settings_mode = False
        self.settings_selected_index = 0
        self.task_row_map: List[Tuple[int, int]] = []
        self.subtask_row_map: List[Tuple[int, int]] = []
        self.detail_flat_subtasks: List[Tuple[str, SubTask, int, bool, bool]] = []
        self._last_click_index: Optional[int] = None
        self._last_click_time: float = 0.0
        self._last_subtask_click_index: Optional[int] = None
        self._last_subtask_click_time: float = 0.0
        self.subtask_detail_scroll: int = 0
        self.subtask_detail_cursor: int = 0
        self._subtask_detail_buffer: List[Tuple[str, str]] = []
        self._subtask_detail_total_lines: int = 0
        self._last_rate_wait: float = 0.0
        self.clipboard = self._build_clipboard()
        self.spinner_active = False
        self.spinner_message = ""
        self.spinner_start = 0.0
        saved_lang = get_user_lang() or "en"
        self.language: str = saved_lang if saved_lang in LANG_PACK else "en"
        self.pat_validation_result = ""
        self._last_sync_enabled: Optional[bool] = None
        self._sync_flash_until: float = 0.0
        self._last_filter_value: Optional[str] = None
        self._filter_flash_until: float = 0.0
        if getattr(self.manager, "auto_sync_message", ""):
            self.set_status_message(self.manager.auto_sync_message, ttl=4)
        if getattr(self.manager, "last_sync_error", ""):
            self.set_status_message(self.manager.last_sync_error, ttl=6)
        self.detail_collapsed: Set[str] = set()
        self.collapsed_by_task: Dict[str, Set[str]] = {}

        # Editing mode
        self.editing_mode = False
        self.edit_field = TextArea(multiline=False, scrollbar=False, focusable=True, wrap_lines=False)
        self.edit_field.buffer.on_text_changed += lambda _: self.force_render()
        self.edit_buffer = self.edit_field.buffer
        self.edit_context = None  # 'task_title', 'subtask_title', 'criterion', 'test', 'blocker'
        self.edit_index = None

        self.load_tasks(skip_sync=True)

        self.style = self.build_style(theme)

        kb = KeyBindings()

        @kb.add("q")
        @kb.add("й")
        @kb.add("c-z")
        def _(event):
            event.app.exit()

        @kb.add("r")
        @kb.add("к")
        def _(event):
            self.load_tasks(preserve_selection=True)

        @kb.add("down")
        @kb.add("j")
        @kb.add("о")
        def _(event):
            if self.settings_mode and not self.editing_mode:
                self.move_settings_selection(1)
                return
            self.move_vertical_selection(1)

        @kb.add(Keys.ScrollDown)
        def _(event):
            self.move_vertical_selection(1)

        @kb.add("up")
        @kb.add("k")
        @kb.add("л")
        def _(event):
            if self.settings_mode and not self.editing_mode:
                self.move_settings_selection(-1)
                return
            self.move_vertical_selection(-1)

        @kb.add(Keys.ScrollUp)
        def _(event):
            self.move_vertical_selection(-1)

        @kb.add("1")
        def _(event):
            self.current_filter = None
            self.selected_index = 0

        @kb.add("2")
        def _(event):
            self.current_filter = Status.WARN  # IN PROGRESS
            self.selected_index = 0

        @kb.add("3")
        def _(event):
            self.current_filter = Status.FAIL  # BACKLOG
            self.selected_index = 0

        @kb.add("4")
        def _(event):
            self.current_filter = Status.OK  # DONE
            self.selected_index = 0

        @kb.add("?")
        def _(event):
            self.help_visible = not self.help_visible

        @kb.add("enter")
        def _(event):
            if self.settings_mode and not self.editing_mode:
                self.activate_settings_option()
                return
            if self.editing_mode:
                # В режиме редактирования - сохранить
                self.save_edit()
            elif self.detail_mode and self.current_task_detail:
                # В режиме деталей Enter показывает карточку выбранной подзадачи
                entry = self._selected_subtask_entry()
                if entry:
                    path, _, _, _, _ = entry
                    self.show_subtask_details(path)
            else:
                if self.filtered_tasks:
                    self.show_task_details(self.filtered_tasks[self.selected_index])

        @kb.add("escape", eager=True)
        def _(event):
            if self.editing_mode:
                # В режиме редактирования - отменить
                self.cancel_edit()
            elif self.settings_mode:
                self.close_settings_dialog()
            elif self.detail_mode:
                self.exit_detail_view()

        @kb.add("delete")
        @kb.add("c-d")
        def _(event):
            """Delete - удалить выбранную задачу или подзадачу"""
            self.delete_current_item()

        @kb.add("d")
        @kb.add("в")
        def _(event):
            """d - переключить выполнение подзадачи"""
            if self.detail_mode and self.current_task_detail:
                self.toggle_subtask_completion()

        @kb.add("e")
        @kb.add("у")
        def _(event):
            """e - редактировать"""
            if not self.editing_mode:
                self.edit_current_item()

        @kb.add("g")
        @kb.add("п")
        def _(event):
            """g - открыть GitHub Projects в браузере"""
            self._open_project_url()

        @kb.add("c-v")
        def _(event):
            if self.editing_mode:
                self._paste_from_clipboard()

        @kb.add("s-insert")
        def _(event):
            if self.editing_mode:
                self._paste_from_clipboard()

        @kb.add("f5")
        def _(event):
            if self.editing_mode and self.edit_context == 'token':
                self._validate_edit_buffer_pat()

        # Alternative keyboard controls for testing horizontal scroll
        @kb.add("c-left")
        def _(event):
            """Ctrl+Left - scroll content left"""
            self.horizontal_offset = max(0, self.horizontal_offset - 5)

        @kb.add("c-right")
        def _(event):
            """Ctrl+Right - scroll content right"""
            self.horizontal_offset = min(200, self.horizontal_offset + 5)

        @kb.add("[")
        def _(event):
            """[ - scroll content left (alternative)"""
            self.horizontal_offset = max(0, self.horizontal_offset - 3)

        @kb.add("]")
        def _(event):
            """] - scroll content right (alternative)"""
            self.horizontal_offset = min(200, self.horizontal_offset + 3)

        @kb.add("left")
        def _(event):
            """Left - collapse or go to parent in detail tree"""
            if getattr(self, "single_subtask_view", None):
                self.exit_detail_view()
                return
            if self.detail_mode:
                entry = self._selected_subtask_entry()
                if entry:
                    path, _, _, collapsed, has_children = entry
                    if has_children and not collapsed:
                        self._toggle_collapse_selected(expand=False)
                        return
                    # go one level up in tree if possible
                    if "." in path:
                        parent_path = ".".join(path.split(".")[:-1])
                        self._select_subtask_by_path(parent_path)
                        self._ensure_detail_selection_visible(len(self.detail_flat_subtasks))
                        self.force_render()
                        return
                self.exit_detail_view()
                return
            # в списке задач: поведение как backspace недоступно — оставляем без действия

        @kb.add("right")
        def _(event):
            """Right - expand or go to first child in detail tree"""
            if not self.detail_mode:
                if self.filtered_tasks:
                    self.show_task_details(self.filtered_tasks[self.selected_index])
                return
            if getattr(self, "single_subtask_view", None):
                return
            entry = self._selected_subtask_entry()
            if not entry:
                return
            path, _, _, collapsed, has_children = entry
            if has_children and collapsed:
                self._toggle_collapse_selected(expand=True)
                return
            self.show_subtask_details(path)

        @kb.add("home")
        def _(event):
            """Home - reset scroll"""
            self.horizontal_offset = 0

        self.status_bar = Window(content=FormattedTextControl(self.get_status_text), height=1, always_hide_cursor=True)
        self.task_list = Window(content=FormattedTextControl(self.get_task_list_text), always_hide_cursor=True, wrap_lines=False)
        self.side_preview = Window(content=FormattedTextControl(self.get_side_preview_text), always_hide_cursor=True, wrap_lines=True, width=Dimension(weight=2))
        self.detail_view = Window(content=FormattedTextControl(self.get_detail_text), always_hide_cursor=True, wrap_lines=True)
        footer_control = FormattedTextControl(self.get_footer_text)
        self.footer = Window(content=footer_control, height=Dimension(min=self.footer_height, max=self.footer_height), always_hide_cursor=False)

        self.normal_body = VSplit(
            [
                Window(content=FormattedTextControl(self.get_task_list_text), always_hide_cursor=True, wrap_lines=False, width=Dimension(weight=3)),
                Window(width=1, char=' '),
                self.side_preview,
            ],
            padding=0,
        )

        self.body_control = InteractiveFormattedTextControl(self.get_body_content, show_cursor=False, focusable=False, mouse_handler=self._handle_body_mouse)
        self.main_window = Window(
            content=self.body_control,
            always_hide_cursor=True,
            wrap_lines=True,
        )

        self.body_container = DynamicContainer(self._resolve_body_container)

        root = HSplit([self.status_bar, self.body_container, self.footer])

        self.app = Application(
            layout=Layout(root),
            key_bindings=kb,
            style=self.style,
            full_screen=True,
            mouse_support=True,
            refresh_interval=1.0,
            clipboard=self.clipboard,
        )

    @staticmethod
    def get_terminal_width() -> int:
        """Get current terminal width, default to 100 if unavailable."""
        try:
            return os.get_terminal_size().columns
        except (AttributeError, ValueError, OSError):
            return 100

    @staticmethod
    def get_terminal_height() -> int:
        try:
            return os.get_terminal_size().lines
        except (AttributeError, ValueError, OSError):
            return 40

    def _bootstrap_git(self, remote_url: str) -> None:
        """Инициализация git + origin + первый push (best effort)."""
        remote_url = remote_url.strip()
        repo_root = Path(".").resolve()
        try:
            if not (repo_root / ".git").exists():
                subprocess.run(["git", "init"], cwd=repo_root, check=True, capture_output=True)
            # set default branch main
            subprocess.run(["git", "checkout", "-B", "main"], cwd=repo_root, check=True, capture_output=True)
            # add remote origin (replace if exists)
            existing = subprocess.run(["git", "remote", "get-url", "origin"], cwd=repo_root, capture_output=True, text=True)
            if existing.returncode == 0:
                subprocess.run(["git", "remote", "remove", "origin"], cwd=repo_root, check=True, capture_output=True)
            subprocess.run(["git", "remote", "add", "origin", remote_url], cwd=repo_root, check=True, capture_output=True)
            # add all files
            subprocess.run(["git", "add", "-A"], cwd=repo_root, check=True, capture_output=True)
            # create commit if none
            has_commits = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True)
            if has_commits.returncode != 0:
                subprocess.run(["git", "commit", "-m", "chore: bootstrap repo"], cwd=repo_root, check=True, capture_output=True)
            # push
            push = subprocess.run(["git", "push", "-u", "origin", "main"], cwd=repo_root, capture_output=True, text=True)
            if push.returncode == 0:
                self.set_status_message(self._t("STATUS_MESSAGE_PUSH_OK"), ttl=4)
            else:
                self.set_status_message(self._t("STATUS_MESSAGE_PUSH_FAIL", error=push.stderr[:80]), ttl=6)
        except subprocess.CalledProcessError as exc:
            err_text = exc.stderr.decode()[:80] if exc.stderr else str(exc)
            self.set_status_message(self._t("STATUS_MESSAGE_BOOTSTRAP_ERROR", error=err_text), ttl=6)
        except Exception as exc:  # pragma: no cover - best effort
            self.set_status_message(self._t("STATUS_MESSAGE_BOOTSTRAP_FAILED", error=exc), ttl=6)

    def force_render(self) -> None:
        app = getattr(self, "app", None)
        if app:
            app.invalidate()

    def _start_spinner(self, message: str):
        self.spinner_message = message
        self.spinner_active = True
        self.spinner_start = time.time()
        self.force_render()

    def _stop_spinner(self):
        self.spinner_active = False
        self.spinner_message = ""
        self.force_render()

    @contextmanager
    def _spinner(self, message: str):
        self._start_spinner(message)
        try:
            yield
        finally:
            self._stop_spinner()

    def _run_with_spinner(self, message: str, func, *args, **kwargs):
        with self._spinner(message):
            return func(*args, **kwargs)

    def _set_footer_height(self, lines: int) -> None:
        """Dynamically adjust footer height (impacts visible rows)."""
        lines = max(0, lines)
        self.footer_height = lines
        try:
            self.footer.height = Dimension(min=lines, max=lines)
        except Exception:
            # UI may not be built yet; storing height is enough
            pass
        self.force_render()

    def _visible_row_limit(self) -> int:
        total = self.get_terminal_height()
        usable = total - (self.footer_height + 4)  # status + padding
        return max(5, usable)

    def _ensure_selection_visible(self):
        total = len(self.filtered_tasks)
        visible = self._visible_row_limit()
        if total <= visible:
            self.list_view_offset = 0
            return
        max_offset = max(0, total - visible)
        if self.selected_index < self.list_view_offset:
            self.list_view_offset = self.selected_index
        elif self.selected_index >= self.list_view_offset + visible:
            self.list_view_offset = self.selected_index - visible + 1
        self.list_view_offset = max(0, min(self.list_view_offset, max_offset))

    def _ensure_detail_selection_visible(self, total: int) -> None:
        visible = self._visible_row_limit()
        if total <= visible:
            self.detail_view_offset = 0
            return
        max_offset = max(0, total - visible)
        if self.detail_selected_index < self.detail_view_offset:
            self.detail_view_offset = self.detail_selected_index
        elif self.detail_selected_index >= self.detail_view_offset + visible:
            self.detail_view_offset = self.detail_selected_index - visible + 1
        self.detail_view_offset = max(0, min(self.detail_view_offset, max_offset))

    def _scroll_task_view(self, delta: int) -> None:
        total = len(self.filtered_tasks)
        visible = self._visible_row_limit()
        if total <= visible:
            self.list_view_offset = 0
            self.selected_index = min(self.selected_index, max(0, total - 1))
            return
        max_offset = max(0, total - visible)
        self.list_view_offset = max(0, min(self.list_view_offset + delta, max_offset))
        if total:
            min_visible = self.list_view_offset
            max_visible = min(total - 1, self.list_view_offset + visible - 1)
            if self.selected_index < min_visible:
                self.selected_index = min_visible
            elif self.selected_index > max_visible:
                self.selected_index = max_visible

    @staticmethod
    def _merge_styles(base: str, extra: Optional[str]) -> str:
        if not extra:
            return base
        if not base:
            return extra
        return f"{base} {extra}"

    def _t(self, key: str, **kwargs) -> str:
        return translate(key, lang=getattr(self, "language", "en"), **kwargs)

    def _cycle_language(self) -> None:
        order = list(LANG_PACK.keys())
        current = getattr(self, "language", "en")
        try:
            idx = order.index(current)
        except ValueError:
            idx = 0
        next_lang = order[(idx + 1) % len(order)]
        self.language = next_lang
        set_user_lang(next_lang)
        self.set_status_message(self._t("STATUS_MESSAGE_LANG_SET", lang=next_lang))
        self.force_render()

    def _flatten_detail_subtasks(self, subtasks: List[SubTask], prefix: str = "", level: int = 0) -> List[Tuple[str, SubTask, int, bool, bool]]:
        flat: List[Tuple[str, SubTask, int, bool, bool]] = []
        for idx, st in enumerate(subtasks):
            path = f"{prefix}.{idx}" if prefix else str(idx)
            collapsed = path in self.detail_collapsed
            has_children = bool(st.children)
            flat.append((path, st, level, collapsed, has_children))
            if not collapsed:
                flat.extend(self._flatten_detail_subtasks(st.children, path, level + 1))
        return flat

    def _rebuild_detail_flat(self, selected_path: Optional[str] = None) -> None:
        if not self.current_task_detail:
            self.detail_flat_subtasks = []
            self.detail_selected_index = 0
            self.detail_selected_path = ""
            return
        flat = self._flatten_detail_subtasks(self.current_task_detail.subtasks)
        self.detail_flat_subtasks = flat
        if selected_path:
            probe = selected_path
            while True:
                for idx, (p, _, _, _, _) in enumerate(flat):
                    if p == probe:
                        self.detail_selected_index = idx
                        self.detail_selected_path = p
                        return
                if "." not in probe:
                    break
                probe = ".".join(probe.split(".")[:-1])
        if not flat:
            self.detail_selected_index = 0
            self.detail_selected_path = ""
            return
        self.detail_selected_index = max(0, min(self.detail_selected_index, len(flat) - 1))
        self.detail_selected_path = flat[self.detail_selected_index][0]

    def _selected_subtask_entry(self) -> Optional[Tuple[str, SubTask, int, bool, bool]]:
        if not self.detail_flat_subtasks:
            return None
        idx = max(0, min(self.detail_selected_index, len(self.detail_flat_subtasks) - 1))
        self.detail_selected_index = idx
        path, subtask, level, collapsed, has_children = self.detail_flat_subtasks[idx]
        self.detail_selected_path = path
        return path, subtask, level, collapsed, has_children

    def _select_subtask_by_path(self, path: str) -> None:
        if not self.detail_flat_subtasks:
            self.detail_selected_index = 0
            self.detail_selected_path = ""
            return
        for idx, (p, _, _, _, _) in enumerate(self.detail_flat_subtasks):
            if p == path:
                self.detail_selected_index = idx
                self.detail_selected_path = p
                return
        self.detail_selected_index = max(0, min(self.detail_selected_index, len(self.detail_flat_subtasks) - 1))
        self.detail_selected_path = self.detail_flat_subtasks[self.detail_selected_index][0]

    def _get_subtask_by_path(self, path: str) -> Optional[SubTask]:
        if not self.current_task_detail or not path:
            return None
        st, _, _ = _find_subtask_by_path(self.current_task_detail.subtasks, path)
        return st

    def _toggle_collapse_selected(self, expand: bool) -> None:
        entry = self._selected_subtask_entry()
        if not entry:
            return
        path, st, _, collapsed, has_children = entry
        if not has_children:
            # нет детей – попытка перейти к родителю при сворачивании
            if not expand and "." in path:
                parent_path = ".".join(path.split(".")[:-1])
                self._select_subtask_by_path(parent_path)
                self._ensure_detail_selection_visible(len(self.detail_flat_subtasks))
                self.force_render()
            return
        if expand:
            if collapsed:
                self.detail_collapsed.discard(path)
                self._rebuild_detail_flat(path)
            else:
                # перейти к первому ребёнку
                child_path = f"{path}.0" if st.children else path
                self._select_subtask_by_path(child_path)
                self._rebuild_detail_flat(child_path)
        else:
            if not collapsed:
                self.detail_collapsed.add(path)
                self._rebuild_detail_flat(path)
            elif "." in path:
                parent_path = ".".join(path.split(".")[:-1])
                self._select_subtask_by_path(parent_path)
                self._rebuild_detail_flat(parent_path)
        if self.current_task_detail:
            self.collapsed_by_task[self.current_task_detail.id] = set(self.detail_collapsed)
        self._ensure_detail_selection_visible(len(self.detail_flat_subtasks))
        self.force_render()

    def _ensure_settings_selection_visible(self, total: int) -> None:
        visible = self._visible_row_limit()
        if total <= visible:
            self.settings_view_offset = 0
            return
        max_offset = max(0, total - visible)
        if self.settings_selected_index < self.settings_view_offset:
            self.settings_view_offset = self.settings_selected_index
        elif self.settings_selected_index >= self.settings_view_offset + visible:
            self.settings_view_offset = self.settings_selected_index - visible + 1
        self.settings_view_offset = max(0, min(self.settings_view_offset, max_offset))

    @staticmethod
    def _normalize_status_value(status: Union[Status, str, bool, None]) -> Status:
        if isinstance(status, Status):
            return status
        if isinstance(status, bool):
            return Status.OK if status else Status.FAIL
        if isinstance(status, str):
            return Status.from_string(status)
        return Status.UNKNOWN

    @staticmethod
    def _subtask_status(subtask: SubTask) -> Status:
        return subtask.status_value()

    def _status_indicator(self, status: Union[Status, str, bool, None]) -> Tuple[str, str]:
        status_obj = self._normalize_status_value(status)
        if status_obj == Status.OK:
            return '●', 'class:icon.check'
        if status_obj == Status.WARN:
            return '●', 'class:icon.warn'
        if status_obj == Status.FAIL:
            return '○', 'class:icon.fail'
        return '○', 'class:status.unknown'

    @staticmethod
    def _status_short_label(status: Status) -> str:
        if status == Status.OK:
            return '[OK]'
        if status == Status.WARN:
            return '[~]'
        if status == Status.FAIL:
            return '[X]'
        return '?'

    def _spinner_frame(self) -> str:
        if not self.spinner_active:
            return ''
        elapsed = time.time() - self.spinner_start
        idx = int(elapsed * 8) % len(self.SPINNER_FRAMES)
        return self.SPINNER_FRAMES[idx]

    def _selection_style_for_status(self, status: Union[Status, str, None]) -> str:
        if self.mono_select:
            return 'selected'
        status_obj: Status
        if isinstance(status, Status):
            status_obj = status
        elif isinstance(status, str):
            status_obj = Status.from_string(status)
        else:
            status_obj = Status.UNKNOWN
        return self.SELECTION_STYLE_BY_STATUS.get(status_obj, 'selected')

    def _task_index_from_y(self, y: int) -> Optional[int]:
        for line_no, idx in self.task_row_map:
            if line_no == y:
                return idx
        return None

    def _subtask_index_from_y(self, y: int) -> Optional[int]:
        for line_no, idx in self.subtask_row_map:
            if line_no == y:
                return idx
        return None

    def _handle_task_click(self, idx: int) -> None:
        total = len(self.filtered_tasks)
        if not total:
            return
        idx = max(0, min(idx, total - 1))
        now = time.time()
        double_click = self._last_click_index == idx and (now - self._last_click_time) < 0.4
        self.selected_index = idx
        self._ensure_selection_visible()
        if double_click:
            self._last_click_index = None
            self._last_click_time = 0.0
            self.show_task_details(self.filtered_tasks[idx])
        else:
            self._last_click_index = idx
            self._last_click_time = now

    def _handle_subtask_click(self, idx: int) -> None:
        if not self.current_task_detail or not self.detail_flat_subtasks:
            return
        total = len(self.detail_flat_subtasks)
        idx = max(0, min(idx, total - 1))
        now = time.time()
        double_click = self._last_subtask_click_index == idx and (now - self._last_subtask_click_time) < 0.4
        self.detail_selected_index = idx
        self._selected_subtask_entry()
        if double_click:
            path = self.detail_flat_subtasks[idx][0]
            self._last_subtask_click_index = None
            self._last_subtask_click_time = 0.0
            self.show_subtask_details(path)
        else:
            self._last_subtask_click_index = idx
            self._last_subtask_click_time = now

    def _handle_body_mouse(self, mouse_event: MouseEvent):
        if (
            mouse_event.event_type == MouseEventType.MOUSE_UP
            and mouse_event.button == MouseButton.MIDDLE
            and self.editing_mode
            and self.edit_context == 'token'
        ):
            self._paste_from_clipboard()
            return None
        if getattr(self, "single_subtask_view", None):
            if mouse_event.event_type == MouseEventType.SCROLL_DOWN:
                self.move_vertical_selection(1)
                return None
            if mouse_event.event_type == MouseEventType.SCROLL_UP:
                self.move_vertical_selection(-1)
                return None
            return NotImplemented
        if self.editing_mode:
            return NotImplemented
        if self.settings_mode and not self.editing_mode:
            if mouse_event.event_type == MouseEventType.SCROLL_DOWN:
                self.move_settings_selection(1)
                return None
            if mouse_event.event_type == MouseEventType.SCROLL_UP:
                self.move_settings_selection(-1)
                return None
            if mouse_event.event_type == MouseEventType.MOUSE_UP and mouse_event.button == MouseButton.LEFT:
                self.activate_settings_option()
                return None
            return None
        shift = MouseModifier.SHIFT in mouse_event.modifiers
        vertical_step = 1  # перемещаемся по 1 строке
        horizontal_step = 5
        if mouse_event.event_type == MouseEventType.SCROLL_DOWN:
            if shift:
                self.horizontal_offset = min(200, self.horizontal_offset + horizontal_step)
            else:
                # Скролл колёсиком двигает выделение по одной строке
                self.move_vertical_selection(vertical_step)
            return None
        if mouse_event.event_type == MouseEventType.SCROLL_UP:
            if shift:
                self.horizontal_offset = max(0, self.horizontal_offset - horizontal_step)
            else:
                # Скролл колёсиком двигает выделение по одной строке
                self.move_vertical_selection(-vertical_step)
            return None
        if mouse_event.event_type == MouseEventType.MOUSE_UP and mouse_event.button == MouseButton.LEFT:
            if self.detail_mode and self.current_task_detail and not getattr(self, "single_subtask_view", None):
                idx = self._subtask_index_from_y(mouse_event.position.y)
                if idx is not None and self.detail_flat_subtasks:
                    idx = max(0, min(idx, len(self.detail_flat_subtasks) - 1))
                    path = self.detail_flat_subtasks[idx][0]
                    if self.detail_selected_index == idx:
                        self.show_subtask_details(path)
                    else:
                        self.detail_selected_index = idx
                        self._selected_subtask_entry()
                    return None
            elif not self.detail_mode:
                idx = self._task_index_from_y(mouse_event.position.y)
                if idx is not None:
                    if self.selected_index == idx:
                        self.show_task_details(self.filtered_tasks[idx])
                    else:
                        self.selected_index = idx
                        self._ensure_selection_visible()
                    return None
        return NotImplemented

    def move_vertical_selection(self, delta: int) -> None:
        """
        Move selected row/panel pointer by `delta`, clamping to available items.

        Works both in list mode (task rows) and detail mode (subtasks/dependencies).
        """
        if getattr(self, "single_subtask_view", None):
            total = self._subtask_detail_total_lines or 0
            if total <= 0:
                return
            lines = self._formatted_lines(self._subtask_detail_buffer)
            pinned = min(len(lines), getattr(self, "_subtask_header_lines_count", 0))
            focusables = self._focusable_line_indices(lines)
            if focusables:
                current = self._snap_cursor(self.subtask_detail_cursor, focusables)
                steps = abs(delta)
                direction = 1 if delta > 0 else -1
                for _ in range(steps):
                    if direction > 0:
                        next_candidates = [i for i in focusables if i > current]
                        if not next_candidates:
                            break
                        current = next_candidates[0]
                    else:
                        prev_candidates = [i for i in reversed(focusables) if i < current]
                        if not prev_candidates:
                            break
                        current = prev_candidates[0]
                self.subtask_detail_cursor = current
            # Обеспечиваем видимость курсора в зоне скролла, не скрывая шапку и учитывая индикаторы
            offset = self.subtask_detail_scroll
            for _ in range(2):  # максимум два пересчёта, чтобы учесть изменение индикаторов
                offset, visible_content, _, _, _ = self._calculate_subtask_viewport(
                    total=len(lines), pinned=pinned, desired_offset=offset
                )
                cursor_rel = max(0, self.subtask_detail_cursor - pinned)
                if cursor_rel < offset:
                    offset = cursor_rel
                    continue
                if cursor_rel >= offset + visible_content:
                    offset = cursor_rel - visible_content + 1
                    continue
                break
            self.subtask_detail_scroll = offset
            term_width = self.get_terminal_width()
            content_width = max(40, term_width - 2)
            self._render_single_subtask_view(content_width)
            self.force_render()
            return
        if self.detail_mode:
            if self.current_task_detail and not self.detail_flat_subtasks and self.current_task_detail.subtasks:
                self._rebuild_detail_flat(self.detail_selected_path)
            items = self.get_detail_items_count()
            if items <= 0:
                self.detail_selected_index = 0
                return
            new_index = max(0, min(self.detail_selected_index + delta, items - 1))
            self.detail_selected_index = new_index
            self._selected_subtask_entry()
            # Даже если индекс не изменился (край списка), закрепляем видимость выделения
            self._ensure_detail_selection_visible(items)
        elif self.settings_mode:
            options = self._settings_options()
            total = len(options)
            if total <= 0:
                self.settings_selected_index = 0
                return
            self.settings_selected_index = max(0, min(self.settings_selected_index + delta, total - 1))
            self._ensure_settings_selection_visible(total)
        else:
            total = len(self.filtered_tasks)
            if total <= 0:
                self.selected_index = 0
                return
            self.selected_index = max(0, min(self.selected_index + delta, total - 1))
            self._ensure_selection_visible()
        self.force_render()

    def apply_horizontal_scroll(self, text: str) -> str:
        """Apply horizontal scroll offset to a text line."""
        if self.horizontal_offset == 0:
            return text
        # Skip offset characters from the beginning
        if len(text) <= self.horizontal_offset:
            return ""
        return text[self.horizontal_offset:]

    def scroll_line_preserve_borders(self, line: str) -> str:
        """Scroll a single line of text, preserving leading border."""
        if not line or self.horizontal_offset == 0:
            return line

        # Check if line has table borders (starts with + or |)
        if line.startswith(('+', '|')):
            # Keep first character (left border)
            border_char = line[0]
            content = line[1:]

            # Apply offset to content
            if len(content) > self.horizontal_offset:
                scrolled_content = content[self.horizontal_offset:]
            else:
                scrolled_content = ""

            return border_char + scrolled_content
        else:
            # No border, apply offset normally
            return self.apply_horizontal_scroll(line)

    def apply_scroll_to_formatted(self, formatted_items: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
        """Apply horizontal scroll to formatted text line by line, preserving table structure."""
        if self.horizontal_offset == 0:
            return formatted_items

        result = []
        current_line = []

        for style, text in formatted_items:
            # Split text by newlines
            parts = text.split('\n')

            for i, part in enumerate(parts):
                if i > 0:
                    # We hit a newline - process accumulated line
                    if current_line:
                        # Convert line to plain text
                        line_text = ''.join(t for _, t in current_line)
                        # Scroll it
                        scrolled = self.scroll_line_preserve_borders(line_text)
                        # Add scrolled line
                        if scrolled:
                            result.append(('class:text', scrolled))
                        result.append(('', '\n'))
                        current_line = []

                # Add part to current line (if not empty)
                if part:
                    current_line.append((style, part))

        # Process last line if exists
        if current_line:
            line_text = ''.join(t for _, t in current_line)
            scrolled = self.scroll_line_preserve_borders(line_text)
            if scrolled:
                result.append(('class:text', scrolled))

        return result

    @staticmethod
    def _formatted_lines(items: List[Tuple[str, str]]) -> List[List[Tuple[str, str]]]:
        """
        Разворачивает FormattedText в массив строк без лишних пустых вставок.
        Каждая строка — список (style, text) без символов перевода строки.
        """
        lines: List[List[Tuple[str, str]]] = [[]]
        for style, text in items:
            parts = text.split('\n')
            for idx, part in enumerate(parts):
                if idx > 0:
                    lines.append([])
                lines[-1].append((style, part))
        # убираем возможную пустую финальную строку
        if lines and all(not frag[1] for frag in lines[-1]):
            lines.pop()
        return lines

    @staticmethod
    def _display_width(text: str) -> int:
        """Возвращает печатную ширину текста с учётом ширины символов."""
        width = 0
        for ch in text:
            w = wcwidth(ch)
            if w is None:
                w = 0
            width += max(0, w)
        return width

    def _trim_display(self, text: str, width: int) -> str:
        """Обрезает текст так, чтобы видимая ширина не превышала width."""
        acc = []
        used = 0
        for ch in text:
            w = wcwidth(ch) or 0
            if w < 0:
                w = 0
            if used + w > width:
                break
            acc.append(ch)
            used += w
        return "".join(acc)

    def _pad_display(self, text: str, width: int) -> str:
        """Обрезает и дополняет пробелами до exact width по видимой ширине."""
        trimmed = self._trim_display(text, width)
        trimmed_width = self._display_width(trimmed)
        if trimmed_width < width:
            trimmed += " " * (width - trimmed_width)
        return trimmed

    def _wrap_display(self, text: str, width: int) -> List[str]:
        """Разбивает текст на строки фиксированной видимой ширины."""
        lines: List[str] = []
        current = ""
        used = 0
        for ch in text:
            w = wcwidth(ch) or 0
            if w < 0:
                w = 0
            if used + w > width and current:
                lines.append(self._pad_display(current, width))
                current = ch
                used = w
            else:
                current += ch
                used += w
        lines.append(self._pad_display(current, width))
        return lines

    def _wrap_with_prefix(self, text: str, width: int, prefix: str) -> List[Tuple[str, bool]]:
        """
        Разбивает текст на строки, добавляя префикс только к первой строке,
        последующие строки сдвигаются пробелами на ту же видимую ширину.
        """
        prefix_width = self._display_width(prefix)
        inner_width = max(1, width - prefix_width)
        segments = self._wrap_display(text, inner_width)
        lines: List[Tuple[str, bool]] = []
        for idx, seg in enumerate(segments):
            if idx == 0:
                composed = prefix + seg
            else:
                composed = " " * prefix_width + seg
            # защита от возможного выхода за ширину
            composed = self._pad_display(composed, width)
            lines.append((composed, idx == 0))
        return lines

    @staticmethod
    def _item_style(group_id: int, continuation: bool = False) -> str:
        base = "class:text.cont" if continuation else "class:text"
        return f"{base} class:item-{group_id}"

    @staticmethod
    def _extract_group(line: List[Tuple[str, str]]) -> Optional[int]:
        """Достаёт идентификатор логического элемента из стиля строки, если есть."""
        for style, _ in line:
            if not style:
                continue
            m = re.search(r"item-(\d+)", style)
            if m:
                try:
                    return int(m.group(1))
                except ValueError:
                    continue
        return None

    @staticmethod
    def _focusable_line_indices(lines: List[List[Tuple[str, str]]]) -> List[int]:
        focusable: List[int] = []
        seen_groups: set[int] = set()
        for idx, line in enumerate(lines):
            texts = "".join(text for _, text in line).strip()
            if not texts:
                continue
            # treat pure table borders (ascii or box-drawing) as non-focusable
            border_chars = set("+-=─═│|")
            if texts and all(ch in border_chars for ch in texts):
                continue
            group = TaskTrackerTUI._extract_group(line)
            if group is not None:
                if group in seen_groups:
                    continue
                seen_groups.add(group)
            if any(
                ('header' in (style or '')) or ('label' in (style or '')) or ('status.' in (style or ''))
                for style, _ in line
            ):
                continue
            if texts.startswith('↑') or texts.startswith('↓'):
                continue
            if texts.startswith('○'):
                continue
            focusable.append(idx)
        return focusable

    @staticmethod
    def _snap_cursor(desired: int, focusables: List[int]) -> int:
        if not focusables:
            return max(0, desired)
        if desired in focusables:
            return desired
        # ищем ближайший выше, затем ниже
        above = [i for i in focusables if i < desired]
        below = [i for i in focusables if i > desired]
        if below:
            return below[0]
        if above:
            return above[-1]
        return focusables[0]

    @staticmethod
    def _formatted_line_count(items: List[Tuple[str, str]]) -> int:
        return len(TaskTrackerTUI._formatted_lines(items))

    def _slice_formatted_lines(self, items: List[Tuple[str, str]], start: int, end: int) -> List[Tuple[str, str]]:
        """
        Возвращает отформатированный срез по номеру строк [start, end).
        Сохраняет стили, деля куски по newline.
        """
        lines = self._formatted_lines(items)
        sliced = lines[start:end]
        output: List[Tuple[str, str]] = []
        for i, line in enumerate(sliced):
            output.extend(line)
            if i < len(sliced) - 1:
                output.append(('', '\n'))
        return output

    def _first_focusable_line_index(self) -> int:
        lines = self._formatted_lines(self._subtask_detail_buffer or [])
        focusables = self._focusable_line_indices(lines)
        return focusables[0] if focusables else 0

    def _calculate_subtask_viewport(self, total: int, pinned: int, desired_offset: Optional[int] = None) -> Tuple[int, int, int, int, int]:
        """
        Рассчитывает параметры вьюпорта карточки подзадачи с учётом закреплённой шапки,
        футера и индикаторов скролла.

        Возвращает (offset, visible_content, indicator_top, indicator_bottom, remaining_below)
        """
        avail = max(5, self.get_terminal_height() - self.footer_height - 1)
        scroll_area = max(1, avail - pinned)
        scrollable_total = max(0, total - pinned)
        offset = max(0, desired_offset if desired_offset is not None else self.subtask_detail_scroll)
        max_raw_offset = max(0, scrollable_total - 1)
        offset = min(offset, max_raw_offset)

        indicator_top = 1 if offset > 0 else 0
        visible_content = max(1, scroll_area - indicator_top)
        max_offset = max(0, scrollable_total - visible_content)
        offset = min(offset, max_offset)

        indicator_bottom = 1 if offset + visible_content < scrollable_total else 0
        visible_content = max(1, scroll_area - indicator_top - indicator_bottom)
        max_offset = max(0, scrollable_total - visible_content)
        offset = min(offset, max_offset)

        remaining_below = max(0, scrollable_total - (offset + visible_content))
        return offset, visible_content, indicator_top, indicator_bottom, remaining_below

    def _render_single_subtask_view(self, content_width: int) -> None:
        """Применяет вертикальный скролл к карточке подзадачи."""
        if not getattr(self, "_subtask_detail_buffer", None):
            return
        lines = self._formatted_lines(self._subtask_detail_buffer)
        total = len(lines)
        pinned = min(total, getattr(self, "_subtask_header_lines_count", 0))
        scrollable = lines[pinned:]
        focusables = self._focusable_line_indices(lines)
        if total:
            self.subtask_detail_cursor = self._snap_cursor(self.subtask_detail_cursor, focusables)
        offset, visible_content, indicator_top, indicator_bottom, remaining_below = self._calculate_subtask_viewport(
            total=len(lines),
            pinned=pinned,
        )

        visible_lines = scrollable[offset : offset + visible_content]

        rendered: List[Tuple[str, str]] = []
        # закреплённая шапка всегда сверху
        for idx, line in enumerate(lines[:pinned]):
            global_idx = idx
            highlight = global_idx == self.subtask_detail_cursor and global_idx in focusables
            style_prefix = 'class:selected' if highlight else None
            for frag_style, frag_text in line:
                is_border = frag_style and 'border' in frag_style
                style = self._merge_styles(style_prefix, frag_style) if (highlight and not is_border) else frag_style
                rendered.append((style, frag_text))
            if pinned and idx < pinned - 1:
                rendered.append(('', '\n'))

        if pinned and (indicator_top or visible_lines):
            rendered.append(('', '\n'))

        if indicator_top:
            rendered.extend([
                ('class:border', '| '),
                ('class:text.dim', self._pad_display(f"↑ +{offset}", content_width - 2)),
                ('class:border', ' |\n'),
            ])

        for idx, line in enumerate(visible_lines):
            global_idx = pinned + offset + idx
        # Второй проход: определяем выбранную группу
        visible_meta: List[Tuple[List[Tuple[str, str]], int, Optional[int], bool]] = []
        selected_group: Optional[int] = None
        for idx, line in enumerate(visible_lines):
            global_idx = pinned + offset + idx
            group = self._extract_group(line)
            is_cursor = global_idx == self.subtask_detail_cursor and global_idx in focusables
            if is_cursor:
                selected_group = group
            visible_meta.append((line, global_idx, group, is_cursor))

        for idx, (line, global_idx, group, is_cursor) in enumerate(visible_meta):
            highlight = is_cursor or (selected_group is not None and group == selected_group and group is not None)
            style_prefix = 'class:selected' if highlight else None
            for frag_style, frag_text in line:
                is_border = frag_style and 'border' in frag_style
                style = self._merge_styles(style_prefix, frag_style) if (highlight and not is_border) else frag_style
                rendered.append((style, frag_text))
            if idx < len(visible_meta) - 1:
                rendered.append(('', '\n'))

        if indicator_bottom:
            rendered.extend([
                ('class:border', '| '),
                ('class:text.dim', self._pad_display(f"↓ +{remaining_below}", content_width - 2)),
                ('class:border', ' |\n'),
            ])

        self.single_subtask_view = FormattedText(rendered)

    @property
    def filtered_tasks(self) -> List[Task]:
        if not self.current_filter:
            return self.tasks
        return [t for t in self.tasks if t.status == self.current_filter]

    def compute_signature(self) -> int:
        return self.manager.compute_signature()

    def maybe_reload(self):
        now = time.time()
        if now - self._last_check < 0.7:
            return
        self._last_check = now
        sig = self.compute_signature()
        if sig != self._last_signature:
            selected_task_file = self.tasks[self.selected_index].task_file if self.tasks else None
            prev_detail = self.current_task_detail.id if (self.detail_mode and self.current_task_detail) else None
            prev_detail_path = self.detail_selected_path
            prev_single = getattr(self, "single_subtask_view", None)

            self.load_tasks(preserve_selection=True, selected_task_file=selected_task_file, skip_sync=True)
            self._last_signature = sig
            self.set_status_message(self._t("STATUS_MESSAGE_CLI_UPDATED"), ttl=3)

            if prev_detail:
                for t in self.tasks:
                    if t.id == prev_detail:
                        # reopen detail preserving selection
                        self.show_task_details(t)
                        if prev_detail_path:
                            self._select_subtask_by_path(prev_detail_path)
                        items = self.get_detail_items_count()
                        self._ensure_detail_selection_visible(items)
                        if prev_single and prev_detail_path:
                            st = self._get_subtask_by_path(prev_detail_path)
                            if st:
                                self.show_subtask_details(prev_detail_path)
                        break

    def load_tasks(self, preserve_selection: bool = False, selected_task_file: Optional[str] = None, skip_sync: bool = False):
        with self._spinner(self._t("SPINNER_REFRESH_TASKS")):
            domain_path = derive_domain_explicit(self.domain_filter, self.phase_filter, self.component_filter)
            details = self.manager.list_tasks(domain_path, skip_sync=skip_sync)
        # показываем плашку при достижении rate-limit
        snapshot = _projects_status_payload()
        wait = snapshot.get("rate_wait") or 0
        remaining = snapshot.get("rate_remaining")
        if wait > 0 and wait != self._last_rate_wait:
            message = self._t("STATUS_MESSAGE_RATE_LIMIT", remaining=remaining if remaining is not None else "?", seconds=int(wait))
            self.set_status_message(message, ttl=5)
            self._last_rate_wait = wait
        if self.phase_filter:
            details = [d for d in details if d.phase == self.phase_filter]
        if self.component_filter:
            details = [d for d in details if d.component == self.component_filter]
        tasks: List[Task] = []
        for det in details:
            task_file = f".tasks/{det.domain + '/' if det.domain else ''}{det.id}.task"
            calc_progress = det.calculate_progress()
            derived_status = Status.OK if calc_progress == 100 and not det.blocked else Status.from_string(det.status)
            tasks.append(
                Task(
                    id=det.id,
                    name=det.title,
                    status=derived_status,
                    description=(det.description or det.context or "")[:80],
                    category=det.domain or det.priority,
                    completed=derived_status == Status.OK,
                    task_file=task_file,
                    progress=calc_progress,
                    subtasks_count=len(det.subtasks),
                    subtasks_completed=sum(1 for st in det.subtasks if st.completed),
                    parent=det.parent,
                    detail=det,
                    domain=det.domain,
                    phase=det.phase,
                    component=det.component,
                    blocked=det.blocked,
                )
            )
        self.tasks = tasks
        if preserve_selection and selected_task_file:
            for idx, t in enumerate(self.tasks):
                if t.task_file == selected_task_file:
                    self.selected_index = idx
                    break
        else:
            self.selected_index = 0
        self.detail_mode = False
        self.current_task = None
        self.current_task_detail = None
        self._last_signature = self.compute_signature()
        if self.selected_index >= len(self.filtered_tasks):
            self.selected_index = max(0, len(self.filtered_tasks) - 1)
        self._ensure_selection_visible()

    def set_status_message(self, message: str, ttl: float = 4.0) -> None:
        self.status_message = message
        self.status_message_expires = time.time() + ttl

    def get_status_text(self) -> FormattedText:
        items = self.filtered_tasks
        total = len(items)
        ok = sum(1 for t in items if t.status == Status.OK)
        warn = sum(1 for t in items if t.status == Status.WARN)
        fail = sum(1 for t in items if t.status == Status.FAIL)
        ctx = self.domain_filter or derive_domain_explicit("", self.phase_filter, self.component_filter) or "."
        filter_labels = {
            "OK": "DONE",
            "WARN": "IN PROGRESS",
            "FAIL": "BACKLOG",
        }
        flt = self.current_filter.value[0] if self.current_filter else "ALL"
        flt_display = filter_labels.get(flt, "ALL")
        now = time.time()
        if self._last_filter_value != flt_display:
            self._filter_flash_until = now + 1.0
            self._last_filter_value = flt_display
        filter_flash_active = now < self._filter_flash_until

        def back_handler(event: MouseEvent):
            if event.event_type == MouseEventType.MOUSE_UP and event.button == MouseButton.LEFT:
                self.exit_detail_view()
                return None
            return NotImplemented

        def settings_handler(event: MouseEvent):
            if event.event_type == MouseEventType.MOUSE_UP and event.button == MouseButton.LEFT:
                self.open_settings_dialog()
                return None
            return NotImplemented

        parts: List[Tuple[str, str]] = []
        # back button только в статус-баре
        if self.detail_mode or getattr(self, "single_subtask_view", None):
            parts.append(("class:header.bigicon", "[BACK] ", back_handler))

        parts.extend([
            ("class:text.dim", f"{self._t('STATUS_TASKS_COUNT', count=total)} | "),
            ("class:icon.check", str(ok)),
            ("class:text.dim", "/"),
            ("class:icon.warn", str(warn)),
            ("class:text.dim", "/"),
            ("class:icon.fail", str(fail)),
        ])
        filter_style = 'class:icon.warn' if filter_flash_active else 'class:header'
        parts.extend([
            ("class:text.dim", " | "),
            (filter_style, f"{flt_display}"),
            ("class:text.dim", " | "),
        ])
        parts.extend(self._sync_indicator_fragments(filter_flash_active))
        spinner_frame = self._spinner_frame()
        if spinner_frame:
            parts.extend([
                ("class:text.dim", " | "),
                ("class:header", f"{spinner_frame} {self.spinner_message or self._t('STATUS_LOADING')}"),
            ])
        if self.status_message and time.time() < self.status_message_expires:
            parts.extend([
                ("class:text.dim", " | "),
                ("class:header", self.status_message[:80]),
            ])
        elif self.status_message:
            self.status_message = ""

        # settings button pinned to the far right of the line
        try:
            term_width = self.get_terminal_width()
        except Exception:
            term_width = 120
        current_len = sum(len(text) for _, text, *rest in parts)
        settings_symbol = "[SETTINGS]"
        # 1 space padding before settings
        needed = max(1, term_width - current_len - len(settings_symbol))
        parts.append(("class:text", " " * needed))
        parts.append(("class:header.bigicon", settings_symbol, settings_handler))
        return FormattedText(parts)

    def _current_description_snippet(self) -> str:
        detail = self._current_task_detail_obj()
        if not detail:
            return ""
        text = detail.description or detail.context or ""
        text = text.strip()
        if not text:
            return ""
        return ' '.join(text.split())

    def _format_cell(self, content: str, width: int, align: str = 'left') -> str:
        """Форматирует содержимое ячейки с заданной шириной"""
        text = content[:width] if len(content) > width else content
        if align == 'right':
            return text.rjust(width)
        if align == 'center':
            return text.center(width)
        return text.ljust(width)

    def _get_task_detail(self, task: Task) -> Optional[TaskDetail]:
        detail = task.detail
        if not detail and task.task_file:
            try:
                detail = TaskFileParser.parse(Path(task.task_file))
            except Exception:
                detail = None
        return detail

    def _current_task_detail_obj(self) -> Optional[TaskDetail]:
        if self.detail_mode and self.current_task_detail:
            return self.current_task_detail
        if self.filtered_tasks:
            task = self.filtered_tasks[self.selected_index]
            return self._get_task_detail(task)
        return None

    def _sync_status_summary(self) -> str:
        sync = self.manager.sync_service if hasattr(self, "manager") else _get_sync_service()
        cfg = getattr(sync, "config", None)
        if not cfg:
            return "OFF"
        if cfg.project_type == "repository":
            target = f"{cfg.owner}/{cfg.repo or '-'}#{cfg.number}"
        else:
            target = f"{cfg.project_type}:{cfg.owner}#{cfg.number}"
        prefix = "ON" if sync.enabled else "OFF"
        return f"{prefix} {target}"


    def _sync_indicator_fragments(self, filter_flash: bool = False) -> List[Tuple[str, str]]:
        sync = self.manager.sync_service if hasattr(self, "manager") else _get_sync_service()
        try:
            cfg = getattr(sync, "config", None)
            snapshot = self._project_config_snapshot()
        except Exception:
            return []
        enabled = bool(sync and sync.enabled and cfg and snapshot["config_enabled"])
        now = time.time()
        if self._last_sync_enabled is None:
            self._last_sync_enabled = enabled
        elif self._last_sync_enabled and not enabled:
            self._sync_flash_until = now + 1.0
        self._last_sync_enabled = enabled

        flash = bool(self._sync_flash_until and now < self._sync_flash_until)
        fragments = sync_status_fragments(snapshot, enabled, flash, filter_flash)

        tooltip = None
        if snapshot.get("status_reason"):
            tooltip = f"ПИЗДЕЦ ПЛОХО: {snapshot['status_reason']}"

        def _tooltip_handler(message: str):
            def handler(event: MouseEvent):
                if event.event_type == MouseEventType.MOUSE_MOVE:
                    self.set_status_message(message, ttl=3)
                    return None
                return NotImplemented
            return handler

        enriched: List[Tuple[str, str]] = []
        for style, text, *rest in fragments:
            if tooltip:
                enriched.append((style, text, _tooltip_handler(tooltip)))
            else:
                enriched.append((style, text))

        enriched.append(("class:text.dim", " | "))
        return enriched

    @staticmethod
    def _sync_target_label(cfg) -> str:
        if not cfg:
            return "-"
        if cfg.project_type == "repository":
            return f"{cfg.owner}/{cfg.repo or '-'}#{cfg.number}"
        return f"{cfg.project_type}:{cfg.owner}#{cfg.number}"

    def _task_created_value(self, task: Task) -> str:
        detail = self._get_task_detail(task)
        if detail and detail.created:
            return str(detail.created)
        return "—"

    def _task_done_value(self, task: Task) -> str:
        detail = self._get_task_detail(task)
        if detail and detail.updated and detail.status == "OK":
            return str(detail.updated)
        return "—"

    def _parse_task_datetime(self, value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        raw = str(value)
        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
            try:
                return datetime.strptime(raw[:len(fmt)], fmt)
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None

    def _task_duration_value(self, detail: Optional[TaskDetail]) -> str:
        if not detail:
            return "-"
        start = self._parse_task_datetime(detail.created)
        end = self._parse_task_datetime(detail.updated) if detail.status == "OK" else None
        if not start or not end:
            return "-"
        delta = end - start
        total_minutes = int(delta.total_seconds() // 60)
        days, rem_minutes = divmod(total_minutes, 60 * 24)
        hours, minutes = divmod(rem_minutes, 60)
        parts: List[str] = []
        if days:
            parts.append(f"{days}{self._t('DUR_DAYS_SUFFIX')}")
        if hours:
            parts.append(f"{hours}{self._t('DUR_HOURS_SUFFIX')}")
        if minutes:
            parts.append(f"{minutes}{self._t('DUR_MINUTES_SUFFIX')}")
        if not parts:
            parts.append(self._t("DUR_LT_HOUR"))
        return " ".join(parts)

    def _get_status_info(self, task: Task) -> Tuple[str, str, str]:
        """Возвращает символ статуса, CSS класс и короткое название"""
        symbol, css = self._status_indicator(task.status)
        status_obj = self._normalize_status_value(task.status)
        return symbol, css, self._status_short_label(status_obj)

    def _apply_scroll(self, text: str) -> str:
        """Применяет горизонтальную прокрутку к тексту"""
        if self.horizontal_offset > 0 and len(text) > self.horizontal_offset:
            return text[self.horizontal_offset:]
        return text if self.horizontal_offset == 0 else ""

    def get_task_list_text(self) -> FormattedText:
        term_width = max(1, self.get_terminal_width())
        if not self.filtered_tasks:
            empty_width = min(term_width, max(10, min(80, term_width - 2)))
            self.task_row_map = []
            return FormattedText([
                ('class:border', '+' + '-' * empty_width + '+\n'),
                ('class:text.dim', '| ' + self._t("TASK_LIST_EMPTY").ljust(empty_width - 2) + ' |\n'),
                ('class:border', '+' + '-' * empty_width + '+'),
            ])

        result: List[Tuple[str, str]] = []
        self.task_row_map = []
        line_counter = 0

        # Выбор адаптивного layout
        layout = ResponsiveLayoutManager.select_layout(term_width)
        desired_widths: Dict[str, int] = {}
        if layout.has_column('progress'):
            max_prog = max((len(f"{t.progress}%") for t in self.filtered_tasks), default=4)
            desired_widths['progress'] = max(3, max_prog)
        if layout.has_column('subtasks'):
            max_sub = 0
            for t in self.filtered_tasks:
                if t.subtasks_count:
                    max_sub = max(max_sub, len(f"{t.subtasks_completed}/{t.subtasks_count}"))
                else:
                    max_sub = max(max_sub, 1)
            desired_widths['subtasks'] = max(3, max_sub)

        widths = layout.calculate_widths(term_width, desired_widths)

        # Построение header line
        header_parts = []
        for col in layout.columns:
            if col in widths:
                header_parts.append('-' * widths[col])
        header_line = '+' + '+'.join(header_parts) + '+'
        header_style = 'class:border.dim'

        # Рендер заголовка таблицы
        result.append((header_style, header_line + '\n'))
        line_counter += 1
        result.append((header_style, '|'))

        column_labels = {
            'stat': ('◉', widths.get('stat', 3)),
            'title': (self._t("TABLE_HEADER_TASK"), widths.get('title', 20)),
            'progress': (self._t("TABLE_HEADER_PROGRESS"), widths.get('progress', 4)),
            'subtasks': (self._t("TABLE_HEADER_SUBTASKS"), widths.get('subtasks', 3)),
        }

        header_align = {
            'stat': 'center',
            'progress': 'center',
            'subtasks': 'center',
        }
        for col in layout.columns:
            if col in column_labels:
                label, width = column_labels[col]
                align = header_align.get(col, 'left')
                result.append(('class:header', self._format_cell(label, width, align=align)))
                result.append(('class:border', '|'))

        result.append(('', '\n'))
        line_counter += 1
        result.append((header_style, header_line + '\n'))
        line_counter += 1

        # Рендер строк задач
        compact_status_mode = len(layout.columns) <= 3
        visible_rows = self._visible_row_limit()
        start_idx = min(self.list_view_offset, max(0, len(self.filtered_tasks) - visible_rows))
        end_idx = min(len(self.filtered_tasks), start_idx + visible_rows)

        for idx in range(start_idx, end_idx):
            task = self.filtered_tasks[idx]
            status_text, status_class, _ = self._get_status_info(task)

            # Подготовка данных для колонок
            cell_data = {}

            if 'stat' in layout.columns:
                if compact_status_mode:
                    marker = status_text if status_class != 'class:status.unknown' else '○'
                    stat_width = widths['stat']
                    marker_text = marker.center(stat_width) if stat_width > 1 else marker
                    cell_data['stat'] = (marker_text, status_class)
                else:
                    cell_data['stat'] = (self._format_cell(status_text, widths['stat'], align='center'), status_class)

            if 'title' in layout.columns:
                title_scrolled = self._apply_scroll(task.name)
                cell_data['title'] = (self._format_cell(title_scrolled, widths['title']), 'class:text')

            if 'progress' in layout.columns:
                prog_text = f"{task.progress}%"
                prog_style = 'class:icon.check' if task.progress >= 100 else 'class:text.dim'
                cell_data['progress'] = (self._format_cell(prog_text, widths['progress'], align='center'), prog_style)

            if 'subtasks' in layout.columns:
                if task.subtasks_count:
                    subt_text = f"{task.subtasks_completed}/{task.subtasks_count}"
                else:
                    subt_text = "—"
                cell_data['subtasks'] = (self._format_cell(subt_text, widths['subtasks'], align='center'), 'class:text.dim')


            # Рендер строки
            row_line = line_counter
            style_key = self._selection_style_for_status(task.status)
            selected = idx == self.selected_index
            result.append(('class:border', '|'))
            for col in layout.columns:
                if col in cell_data:
                    text, css_class = cell_data[col]
                    cell_style = f"class:{style_key}" if selected else css_class
                    result.append((cell_style, text))
                    result.append(('class:border', '|'))

            self.task_row_map.append((row_line, idx))
            result.append(('', '\n'))
            line_counter += 1

        result.append((header_style, header_line))

        return FormattedText(result)

    def get_side_preview_text(self) -> FormattedText:
        """Описание выбранной задачи (без подзадач) в правой колонке."""
        if not self.filtered_tasks:
            return FormattedText([
                ('class:border', '+------------------------------+\n'),
                ('class:text.dim', '| ' + self._t("SIDE_EMPTY_TASKS").ljust(26) + ' |\n'),
                ('class:border', '+------------------------------+'),
            ])
        idx = min(self.selected_index, len(self.filtered_tasks) - 1)
        task = self.filtered_tasks[idx]
        detail = task.detail
        if not detail and task.task_file:
            try:
                detail = TaskFileParser.parse(Path(task.task_file))
            except Exception:
                detail = None
        if not detail:
            return FormattedText([
                ('class:border', '+------------------------------+\n'),
                ('class:text.dim', '| ' + self._t("SIDE_NO_DATA").ljust(26) + ' |\n'),
                ('class:border', '+------------------------------+'),
            ])

        result = []
        result.append(('class:border', '+------------------------------------------+\n'))
        result.append(('class:border', '| '))
        result.append(('class:header', f'{detail.id} '))
        result.append(('class:text.dim', f'| '))

        # Status text
        if detail.status == 'OK':
            result.append(('class:icon.check', 'DONE '))
        elif detail.status == 'WARN':
            result.append(('class:icon.warn', 'INPR '))
        else:
            result.append(('class:icon.fail', 'BACK '))

        result.append(('class:text.dim', f'| {detail.priority}'))
        result.append(('class:border', '                   |\n'))
        result.append(('class:border', '+------------------------------------------+\n'))

        # Title
        title_lines = [detail.title[i:i+38] for i in range(0, len(detail.title), 38)]
        for tline in title_lines:
            result.append(('class:border', '| '))
            result.append(('class:text', tline.ljust(40)))
            result.append(('class:border', ' |\n'))

        # Context
        ctx = detail.domain or detail.phase or detail.component
        if ctx:
            result.append(('class:border', '| '))
            result.append(('class:text.dim', self._t("STATUS_CONTEXT", ctx=ctx[:32]).ljust(40)))
            result.append(('class:border', ' |\n'))

        # Progress with simple ASCII bar
        prog = detail.calculate_progress()
        bar_width = 30
        filled = int(prog * bar_width / 100)
        bar = '#' * filled + '-' * (bar_width - filled)
        result.append(('class:border', '| '))
        result.append(('class:text.dim', f'{prog:3d}% ['))
        result.append(('class:text.dim', bar[:30]))
        result.append(('class:text.dim', ']'))
        result.append(('class:border', '    |\n'))

        # Description
        if detail.description:
            result.append(('class:border', '+------------------------------------------+\n'))
            desc_lines = detail.description.split('\n')
            for dline in desc_lines[:5]:  # Max 5 lines
                chunks = [dline[i:i+38] for i in range(0, len(dline), 38)]
                for chunk in chunks[:3]:  # Max 3 chunks per line
                    result.append(('class:border', '| '))
                    result.append(('class:text', chunk.ljust(40)))
                    result.append(('class:border', ' |\n'))

        result.append(('class:border', '+------------------------------------------+'))
        return FormattedText(result)

    # -------- detail view (full card in left pane) --------
    def get_detail_text(self) -> FormattedText:
        if not self.current_task_detail:
            return FormattedText([("class:text.dim", self._t("STATUS_TASK_NOT_SELECTED"))])

        detail = self.current_task_detail
        self.subtask_row_map = []
        result = []

        # Get terminal width and calculate adaptive content width
        term_width = self.get_terminal_width()
        content_width = max(40, term_width - 2)
        compact = self.get_terminal_height() < 32 or content_width < 90

        # Header
        result.append(('class:border', '+' + '='*content_width + '+\n'))
        result.append(('class:border', '| '))
        result.append(('class:header', f'{detail.id} '))
        result.append(('class:text.dim', '| '))

        # Status with color
        status_map = {
            'OK': ('class:icon.check', self._t("STATUS_DONE")),
            'WARN': ('class:icon.warn', self._t("STATUS_IN_PROGRESS")),
            'FAIL': ('class:icon.fail', self._t("STATUS_BACKLOG")),
        }
        status_style, status_label = status_map.get(detail.status, ('class:icon.fail', detail.status))
        result.append((status_style, status_label.ljust(10)))
        result.append(('class:text.dim', f'| {self._t("PRIORITY")}: {detail.priority:<7}'))
        result.append(('class:text.dim', f'| {self._t("PROGRESS")}: {detail.calculate_progress():>3}%'))
        padding_needed = content_width - 2 - (len(detail.id) + 3 + len(status_label) + 23)
        if padding_needed > 0:
            result.append(('class:text.dim', ' ' * padding_needed))
        result.append(('class:border', ' |\n'))
        result.append(('class:border', '+' + '='*content_width + '+\n'))
        # Title - wrap if needed, apply horizontal scroll
        title_display = detail.title
        if self.horizontal_offset > 0:
            title_display = title_display[self.horizontal_offset:] if len(title_display) > self.horizontal_offset else ""

        title_text = f'{self._t("TITLE")}: {title_display}'
        if len(title_text) > content_width:
            # Wrap title
            result.append(('class:border', '| '))
            result.append(('class:header', f'{self._t("TITLE")}:'.ljust(content_width - 2)))
            result.append(('class:border', ' |\n'))

            title_lines = [title_display[i:i+content_width-4] for i in range(0, len(title_display), content_width-4)]
            for tline in title_lines:
                result.append(('class:border', '| '))
                result.append(('class:text', f'  {tline}'.ljust(content_width - 2)))
                result.append(('class:border', ' |\n'))
        else:
            result.append(('class:border', '| '))
            result.append(('class:text', title_text.ljust(content_width - 2)))
            result.append(('class:border', ' |\n'))
        result.append(('class:border', '+' + '-'*content_width + '+\n'))

        # Compact summary: description + task-level blockers (minimal height, balanced)
        if self.get_terminal_height() > 18:
            current_lines = sum(frag[1].count('\n') for frag in result if isinstance(frag, tuple) and len(frag) >= 2)
            remaining = max(0, self.get_terminal_height() - self.footer_height - current_lines - 1)
            budget = max(0, remaining)
            if budget > 0:
                summary: List[Tuple[str, str]] = []
                desc_limit = min(2, budget)
                if detail.description and desc_limit > 0:
                    wrapped = self._wrap_with_prefix(detail.description, content_width - 2, f"{self._t('DESCRIPTION')}: ")
                    for ch, _ in wrapped[:desc_limit]:
                        summary.append(('class:border', '| '))
                        summary.append(('class:text', ch))
                        summary.append(('class:border', ' |\n'))
                    budget -= min(desc_limit, len(wrapped))
                if detail.blockers and budget > 0:
                    wrapped_bl = self._wrap_with_prefix("; ".join(detail.blockers), content_width - 2, f"{self._t('BLOCKERS_HEADER')}: ")
                    for ch, _ in wrapped_bl[:max(1, min(budget, 1))]:
                        summary.append(('class:border', '| '))
                        summary.append(('class:text', ch))
                        summary.append(('class:border', ' |\n'))
                if summary:
                    result.extend(summary)

        if not compact:
            # Metadata
            if detail.domain or detail.phase or detail.component:
                if detail.domain:
                    result.append(('class:border', '| '))
                    result.append(('class:text.dim', f'{self._t("DOMAIN")}: {detail.domain}'[:content_width-2].ljust(content_width - 2)))
                    result.append(('class:border', ' |\n'))
                if detail.phase:
                    result.append(('class:border', '| '))
                    result.append(('class:text.dim', f'{self._t("PHASE")}: {detail.phase}'[:content_width-2].ljust(content_width - 2)))
                    result.append(('class:border', ' |\n'))
                if detail.component:
                    result.append(('class:border', '| '))
                    result.append(('class:text.dim', f'{self._t("COMPONENT")}: {detail.component}'[:content_width-2].ljust(content_width - 2)))
                    result.append(('class:border', ' |\n'))
                result.append(('class:border', '+' + '-'*content_width + '+\n'))

            if detail.tags:
                result.append(('class:border', '| '))
                tags_text = f'{self._t("TAGS")}: {", ".join(detail.tags)}'[:content_width-2]
                result.append(('class:text.dim', tags_text.ljust(content_width - 2)))
                result.append(('class:border', ' |\n'))
                result.append(('class:border', '+' + '-'*content_width + '+\n'))

            if detail.parent:
                result.append(('class:border', '| '))
                result.append(('class:text.dim', f'{self._t("PARENT")}: {detail.parent}'[:content_width-2].ljust(content_width - 2)))
                result.append(('class:border', ' |\n'))
                result.append(('class:border', '+' + '-'*content_width + '+\n'))

            # Description with horizontal scroll
            if detail.description:
                result.append(('class:border', '| '))
                result.append(('class:header', self._t("DESCRIPTION_HEADER").ljust(content_width - 2)))
                result.append(('class:border', ' |\n'))
                desc_lines = detail.description.split('\n')
                for dline in desc_lines:
                    # Apply horizontal scroll to each line
                    if self.horizontal_offset > 0:
                        dline = dline[self.horizontal_offset:] if len(dline) > self.horizontal_offset else ""
                    chunks = [dline[i:i+content_width-4] for i in range(0, len(dline), content_width-4)]
                    if not chunks:
                        chunks = ['']
                    for chunk in chunks:
                        result.append(('class:border', '| '))
                        result.append(('class:text', f'  {chunk}'.ljust(content_width - 2)))
                        result.append(('class:border', ' |\n'))
                result.append(('class:border', '+' + '-'*content_width + '+\n'))

        # Единый список только для подзадач; остальные секции выводим отдельно ниже
        self._rebuild_detail_flat(self.detail_selected_path)
        items: List[Tuple[str, SubTask, int, bool, bool]] = list(self.detail_flat_subtasks)
        aux_sections = {
            "blockers": detail.blockers,
        }

        total_items = len(items)
        # Compute line budget after header/metadata fragments
        used_lines = 0
        for frag in result:
            if isinstance(frag, tuple) and len(frag) >= 2:
                used_lines += frag[1].count('\n')
        # Добавляем 1 строку запаса под нижние панели/обрезку терминала
        list_budget = max(1, self.get_terminal_height() - self.footer_height - used_lines - 3)

        # Initial visible window that honors the budget
        if total_items:
            self.detail_selected_index = max(0, min(self.detail_selected_index, total_items - 1))
            visible = min(total_items, list_budget)

            def _adjust_offset(vis: int) -> int:
                max_offset = max(0, total_items - vis)
                offset = min(self.detail_view_offset, max_offset)
                if self.detail_selected_index < offset:
                    offset = self.detail_selected_index
                elif self.detail_selected_index >= offset + vis:
                    offset = self.detail_selected_index - vis + 1
                return max(0, min(offset, max_offset))

            self.detail_view_offset = _adjust_offset(visible)
            start = self.detail_view_offset
            end = min(total_items, start + visible)
            hidden_above = start
            hidden_below = total_items - end

            # Keep marker rows (↑/↓) inside the same height budget
            while True:
                marker_lines = int(hidden_above > 0) + int(hidden_below > 0)
                if visible + marker_lines <= list_budget:
                    break
                if visible == 1:
                    hidden_above = 0
                    hidden_below = 0
                    break
                visible = max(1, min(total_items, list_budget - marker_lines))
                self.detail_view_offset = _adjust_offset(visible)
                start = self.detail_view_offset
                end = min(total_items, start + visible)
                hidden_above = start
                hidden_below = total_items - end
        else:
            visible = 0
            start = end = 0
            hidden_above = hidden_below = 0
            self.detail_view_offset = 0
        if items:
            self._selected_subtask_entry()

        completed = sum(1 for _, st, _, _, _ in items if st.completed)
        line_counter = 0
        for frag in result:
            if isinstance(frag, tuple) and len(frag) >= 2:
                line_counter += frag[1].count('\n')

        self.subtask_row_map = []
        result.append(('class:border', '| '))
        header = f'{self._t("SUBTASKS")} ({completed}/{len(items)} {self._t("COMPLETED_SUFFIX")})'
        result.append(('class:header', header[: content_width - 2].ljust(content_width - 2)))
        result.append(('class:border', ' |\n'))
        line_counter += 1
        if hidden_above:
            result.append(('class:border', '| '))
            result.append(('class:text.dim', f"↑ +{hidden_above}".ljust(content_width - 2)))
            result.append(('class:border', ' |\n'))
            line_counter += 1

        for global_idx in range(start, end):
            path, st, level, collapsed, has_children = items[global_idx]
            selected = global_idx == self.detail_selected_index
            bg_style = f"class:{self._selection_style_for_status(Status.OK if selected else None)}" if selected else None
            base_border = 'class:border'

            pointer = '>' if selected else ' '
            indent = '  ' * level
            indicator = "▸" if (has_children and collapsed) else ("▾" if has_children else " ")
            base_prefix = f'{indent}{pointer}{indicator} {path} '

            st_title = st.title
            if self.horizontal_offset > 0:
                st_title = st_title[self.horizontal_offset:] if len(st_title) > self.horizontal_offset else ""

            sub_status = self._subtask_status(st)
            symbol, icon_class = self._status_indicator(sub_status)
            if selected:
                icon_class = self._merge_styles(icon_class, bg_style)

            indicator_width = len(symbol) + 1
            prefix_len = len(base_prefix) + indicator_width

            row_line = line_counter
            result.append((base_border, '| '))
            result.append((self._merge_styles('class:text', bg_style), base_prefix))
            result.append((icon_class, f"{symbol} "))
            flags = subtask_flags(st)
            glyphs = [
                ('class:icon.check', '•') if flags['criteria'] else ('class:text.dim', '·'),
                ('class:icon.check', '•') if flags['tests'] else ('class:text.dim', '·'),
                ('class:icon.check', '•') if flags['blockers'] else ('class:text.dim', '·'),
            ]
            flag_text = []
            for idxf, (cls, symbol_f) in enumerate(glyphs):
                flag_text.append((cls, symbol_f))
                if idxf < 2:
                    flag_text.append(('class:text.dim', ' '))
            flag_width = len(' [• • •]')
            title_width = max(5, content_width - 2 - prefix_len - flag_width)
            title_style = self._merge_styles('class:text', bg_style) if selected else 'class:text'
            result.append((title_style, st_title[:title_width].ljust(title_width)))
            bracket_style = self._merge_styles('class:text.dim', bg_style) if selected else 'class:text.dim'
            result.append((bracket_style, ' ['))
            for frag_style, frag_text in flag_text:
                style = self._merge_styles(frag_style, bg_style) if selected else frag_style
                result.append((style, frag_text))
            result.append((bracket_style, ']'))
            result.append((base_border, ' |\n'))
            line_counter += 1
            self.subtask_row_map.append((row_line, global_idx))

        if hidden_below:
            result.append(('class:border', '| '))
            result.append(('class:text.dim', f"↓ +{hidden_below}".ljust(content_width - 2)))
            result.append(('class:border', ' |\n'))
            line_counter += 1

        # Детали выбранной подзадачи (критерии/тесты/блокеры)
        selected_entry = self._selected_subtask_entry() if items else None
        if selected_entry:
            remaining = max(0, self.get_terminal_height() - self.footer_height - line_counter - 1)
            if remaining > 2:
                _, st_sel, _, _, _ = selected_entry
                detail_lines: List[Tuple[str, str]] = []
                detail_lines.append(('class:border', '+' + '-'*content_width + '+\n'))
                detail_lines.append(('class:border', '| '))
                header = f"{self._t('SUBTASK_DETAILS')}: {self.detail_selected_path or ''}"
                detail_lines.append(('class:header', header[: content_width - 2].ljust(content_width - 2)))
                detail_lines.append(('class:border', ' |\n'))

                def _append_block(title: str, rows: List[str]) -> None:
                    if not rows:
                        return
                    detail_lines.append(('class:border', '| '))
                    detail_lines.append(('class:text.dim', f" {title}:".ljust(content_width - 2)))
                    detail_lines.append(('class:border', ' |\n'))
                    for idxr, row in enumerate(rows, 1):
                        prefix = f"  {idxr}. "
                        raw = prefix + row
                        if self.horizontal_offset > 0:
                            raw = raw[self.horizontal_offset:] if len(raw) > self.horizontal_offset else ""
                        for chunk, _ in self._wrap_with_prefix(row, content_width - 2, prefix):
                            detail_lines.append(('class:border', '| '))
                            detail_lines.append(('class:text', chunk))
                            detail_lines.append(('class:border', ' |\n'))

                _append_block(self._t("CRITERIA"), st_sel.success_criteria)
                _append_block(self._t("TESTS"), st_sel.tests)
                _append_block(self._t("BLOCKERS"), st_sel.blockers)

                sliced = self._slice_formatted_lines(detail_lines, 0, remaining)
                result.extend(sliced)
                line_counter += remaining

        # Дополнительные секции: next, deps, success criteria, problems, risks
        section_titles = {
            "blockers": "BLOCKERS",
        }
        for key, entries in aux_sections.items():
            if not entries:
                continue
            result.append(('class:border', '+' + '-'*content_width + '+\n'))
            result.append(('class:border', '| '))
            result.append(('class:header', section_titles.get(key, key).ljust(content_width - 2)))
            result.append(('class:border', ' |\n'))
            for entry in entries:
                text = str(entry)
                if self.horizontal_offset > 0:
                    text = text[self.horizontal_offset:] if len(text) > self.horizontal_offset else ""
                chunks = [text[i:i+content_width-4] for i in range(0, len(text), content_width-4)] or ['']
                for ch in chunks:
                    result.append(('class:border', '| '))
                    result.append(('class:text', f"  - {ch}".ljust(content_width - 2)))
                    result.append(('class:border', ' |\n'))

        result.append(('class:border', '+' + '='*content_width + '+'))

        return FormattedText(result)

    def get_detail_items_count(self) -> int:
        if not self.current_task_detail:
            return 0
        return len(self.detail_flat_subtasks)

    def show_task_details(self, task: Task):
        self.current_task = task
        self.current_task_detail = task.detail or TaskFileParser.parse(Path(task.task_file))
        self.detail_mode = True
        self.detail_selected_index = 0
        self.detail_collapsed = set(self.collapsed_by_task.get(self.current_task_detail.id, set()))
        self._rebuild_detail_flat()
        self.detail_view_offset = 0
        self._set_footer_height(0)

    def show_subtask_details(self, path: str):
        """Render a focused view for a single subtask with full details."""
        if not self.current_task_detail:
            return
        subtask = self._get_subtask_by_path(path)
        if not subtask:
            return
        self._select_subtask_by_path(path)
        self._set_footer_height(0)
        term_width = self.get_terminal_width()
        content_width = max(40, term_width - 2)

        lines: List[Tuple[str, str]] = []
        group_id = 0  # логический идентификатор для многострочных элементов

        def next_group() -> int:
            nonlocal group_id
            group_id += 1
            return group_id
        lines.append(('class:border', '+' + '='*content_width + '+\n'))

        # Header с кнопкой назад
        def back_handler(event: MouseEvent):
            if event.event_type == MouseEventType.MOUSE_UP and event.button == MouseButton.LEFT:
                self.exit_detail_view()
                return None
            return NotImplemented

        sub_status = self._subtask_status(subtask)
        symbol, icon_style = self._status_indicator(sub_status)
        header_label = f"SUBTASK {path}"
        lines.append(('class:border', '| '))
        lines.append((icon_style, f"{symbol} "))
        inner_width = content_width - 2
        remaining = max(0, inner_width - 2)
        lines.append(('class:header', self._pad_display(header_label, remaining)))
        lines.append(('class:border', ' |\n'))
        lines.append(('class:border', '+' + '-'*content_width + '+\n'))
        # фиксируем количество строк шапки для закрепления при скролле (оставляем заголовок и нижнюю границу)
        header_lines = self._formatted_lines(lines)
        self._subtask_header_lines_count = max(1, len(header_lines) - 1)

        # Title
        text = subtask.title
        for ch in self._wrap_display(text, content_width - 2):
            lines.append(('class:border', '| '))
            lines.append(('class:text', ch))
            lines.append(('class:border', ' |\n'))

        # Checkpoint summary
        lines.append(('class:border', '+' + '-'*content_width + '+\n'))

        def append_indicator_row(entries: List[Tuple[str, bool]]):
            inner_width = content_width - 2
            consumed = 0

            lines.append(('class:border', '| '))
            for idx, (label, flag) in enumerate(entries):
                status = Status.OK if flag else Status.FAIL
                symbol, icon_style = self._status_indicator(status)
                parts = [
                    (icon_style, symbol),
                    ('class:text', f' {label}'),
                    ('class:text.dim', ' | ' if idx < len(entries) - 1 else ''),
                ]
                for style, text in parts:
                    if not text:
                        continue
                    available = inner_width - consumed
                    if available <= 0:
                        break
                    chunk = self._trim_display(text, available)
                    consumed += self._display_width(chunk)
                    lines.append((style, chunk))
            if consumed < inner_width:
                lines.append(('class:text', ' ' * (inner_width - consumed)))
            lines.append(('class:border', ' |\n'))

        def add_section_header(label: str, confirmed: bool):
            status = Status.OK if confirmed else Status.FAIL
            symbol, icon_style = self._status_indicator(status)
            style = 'class:status.ok' if confirmed else 'class:status.fail'
            lines.append(('class:border', '+' + '-'*content_width + '+\n'))
            lines.append(('class:border', '| '))
            inner_width = content_width - 2
            label_space = max(0, inner_width - 2)
            lines.append((icon_style, f"{symbol} "))
            lines.append((style, self._pad_display(label, label_space)))
            lines.append(('class:border', ' |\n'))

        # Критерии выполнения
        if subtask.success_criteria:
            add_section_header(self._t("CRITERIA"), subtask.criteria_confirmed)
            for i, criterion in enumerate(subtask.success_criteria, 1):
                prefix = f"  {i}. "
                gid = next_group()
                for ch, is_first in self._wrap_with_prefix(criterion, content_width - 2, prefix):
                    lines.append(('class:border', '| '))
                    style = self._item_style(gid, continuation=not is_first)
                    lines.append((style, ch))
                    lines.append(('class:border', ' |\n'))

        # Тесты
        if subtask.tests:
            add_section_header(self._t("TESTS"), subtask.tests_confirmed)
            for i, test in enumerate(subtask.tests, 1):
                prefix = f"  {i}. "
                gid = next_group()
                for ch, is_first in self._wrap_with_prefix(test, content_width - 2, prefix):
                    lines.append(('class:border', '| '))
                    style = self._item_style(gid, continuation=not is_first)
                    lines.append((style, ch))
                    lines.append(('class:border', ' |\n'))

        # Блокеры
        if subtask.blockers:
            add_section_header(self._t("BLOCKERS"), subtask.blockers_resolved)
            for i, blocker in enumerate(subtask.blockers, 1):
                prefix = f"  {i}. "
                gid = next_group()
                for ch, is_first in self._wrap_with_prefix(blocker, content_width - 2, prefix):
                    lines.append(('class:border', '| '))
                    style = self._item_style(gid, continuation=not is_first)
                    lines.append((style, ch))
                    lines.append(('class:border', ' |\n'))

        # Evidence logs
        def append_logs(label: str, entries: List[str]):
            if not entries:
                return
            lines.append(('class:border', '+' + '-'*content_width + '+\n'))
            lines.append(('class:border', '| '))
            lines.append(('class:label', self._pad_display(f"{label}{self._t('MARKS_SUFFIX')}", content_width - 2)))
            lines.append(('class:border', ' |\n'))
            for entry in entries:
                gid = next_group()
                for ch, is_first in self._wrap_with_prefix(entry, content_width - 2, "  - "):
                    lines.append(('class:border', '| '))
                    style = self._item_style(gid, continuation=not is_first)
                    lines.append((style, ch))
                    lines.append(('class:border', ' |\n'))
        append_logs(self._t("CRITERIA"), subtask.criteria_notes)
        append_logs(self._t("TESTS"), subtask.tests_notes)
        append_logs(self._t("BLOCKERS"), subtask.blockers_notes)

        lines.append(('class:border', '+' + '='*content_width + '+'))

        # сохраняем полный буфер и строим вьюпорт с учетом вертикального скролла
        self._subtask_detail_buffer = lines
        self._subtask_detail_total_lines = self._formatted_line_count(lines)
        self.subtask_detail_scroll = 0
        self.subtask_detail_cursor = self._first_focusable_line_index()
        self._render_single_subtask_view(content_width)

    def delete_current_item(self):
        """Удалить текущий выбранный элемент (задачу или подзадачу)"""
        if self.detail_mode and self.current_task_detail:
            # В режиме деталей - удаляем подзадачу
            entry = self._selected_subtask_entry()
            if entry:
                path, _, _, _, _ = entry
                target, parent, idx = _find_subtask_by_path(self.current_task_detail.subtasks, path)
                if target is None or idx is None:
                    return
                # Подтверждение не требуется в TUI - просто удаляем
                if parent is None:
                    del self.current_task_detail.subtasks[idx]
                else:
                    del parent.children[idx]
                self.manager.save_task(self.current_task_detail)
                self._rebuild_detail_flat()
                if self.detail_selected_index >= len(self.detail_flat_subtasks):
                    self.detail_selected_index = max(0, len(self.detail_flat_subtasks) - 1)
                if self.detail_flat_subtasks:
                    self.detail_selected_path = self.detail_flat_subtasks[self.detail_selected_index][0]
                else:
                    self.detail_selected_path = ""
                # Обновляем кеш
                if self.current_task_detail.id in self.task_details_cache:
                    self.task_details_cache[self.current_task_detail.id] = self.current_task_detail
                self.load_tasks(preserve_selection=True, skip_sync=True)
        else:
            # В списке задач - удаляем задачу
            if self.filtered_tasks:
                task = self.filtered_tasks[self.selected_index]
                self.manager.delete_task(task.id, task.domain)
                # Корректируем индекс
                if self.selected_index >= len(self.filtered_tasks) - 1:
                    self.selected_index = max(0, len(self.filtered_tasks) - 2)
                self.load_tasks(preserve_selection=False, skip_sync=True)

    def toggle_subtask_completion(self):
        """Переключить состояние выполнения подзадачи"""
        if self.detail_mode and self.current_task_detail:
            entry = self._selected_subtask_entry()
            if entry:
                path, st, _, _, _ = entry
                desired = not st.completed
                domain = self.current_task_detail.domain
                ok, msg = self.manager.set_subtask(self.current_task_detail.id, 0, desired, domain, path=path)
                if not ok:
                    self.set_status_message(msg or self._t("STATUS_MESSAGE_CHECKPOINTS_REQUIRED"))
                    return
                updated = self.manager.load_task(self.current_task_detail.id, domain)
                if updated:
                    self.current_task_detail = updated
                    self.task_details_cache[self.current_task_detail.id] = updated
                    self._rebuild_detail_flat(path)
                self.load_tasks(preserve_selection=True)

    def start_editing(self, context: str, current_value: str, index: Optional[int] = None):
        """Начать редактирование текста"""
        self.editing_mode = True
        self.edit_context = context
        self.edit_index = index
        self.edit_buffer.text = current_value
        self.edit_buffer.cursor_position = len(current_value)
        if hasattr(self, "app") and self.app:
            self.app.layout.focus(self.edit_field)

    def save_edit(self):
        """Сохранить результат редактирования"""
        if not self.editing_mode:
            return

        context = self.edit_context
        task = self.current_task_detail
        raw_value = self.edit_buffer.text
        new_value = raw_value.strip()

        if context == 'token':
            set_user_token(new_value)
            if new_value:
                self.set_status_message(self._t("STATUS_MESSAGE_PAT_SAVED"))
            else:
                self.set_status_message(self._t("STATUS_MESSAGE_PAT_CLEARED"))
            self.cancel_edit()
            if self.settings_mode:
                self.force_render()
            return

        if context == 'project_number':
            try:
                number_value = int(new_value)
                if number_value <= 0:
                    raise ValueError
            except ValueError:
                self.set_status_message(self._t("STATUS_MESSAGE_PROJECT_NUMBER_REQUIRED"))
            else:
                self._set_project_number(number_value)
                self.set_status_message(self._t("STATUS_MESSAGE_PROJECT_NUMBER_UPDATED"))
            self.cancel_edit()
            if self.settings_mode:
                self.force_render()
            return
        if context == 'project_workers':
            try:
                workers_value = int(new_value)
                if workers_value < 0:
                    raise ValueError
            except ValueError:
                self.set_status_message(self._t("STATUS_MESSAGE_POOL_INTEGER"))
            else:
                update_project_workers(None if workers_value == 0 else workers_value)
                reload_projects_sync()
                self.set_status_message(self._t("STATUS_MESSAGE_POOL_UPDATED"))
            self.cancel_edit()
            if self.settings_mode:
                self.force_render()
            return
        if context == 'bootstrap_remote':
            self._bootstrap_git(new_value)
            self.cancel_edit()
            return

        if not new_value:
            self.cancel_edit()
            return

        if context == 'task_title' and task:
            task.title = new_value
            self.manager.save_task(task)
        elif context == 'task_description' and task:
            task.description = new_value
            self.manager.save_task(task)
        elif context == 'subtask_title' and task and self.edit_index is not None:
            path = self.detail_selected_path
            if not path and self.edit_index < len(self.detail_flat_subtasks):
                path = self.detail_flat_subtasks[self.edit_index][0]
            st = self._get_subtask_by_path(path) if path else None
            if st:
                st.title = new_value
                self.manager.save_task(task)
        elif context == 'criterion' and task and self.edit_index is not None:
            path = self.detail_selected_path or (self.detail_flat_subtasks[self.detail_selected_index][0] if self.detail_flat_subtasks else "")
            st = self._get_subtask_by_path(path) if path else None
            if st and self.edit_index < len(st.success_criteria):
                st.success_criteria[self.edit_index] = new_value
                self.manager.save_task(task)
        elif context == 'test' and task and self.edit_index is not None:
            path = self.detail_selected_path or (self.detail_flat_subtasks[self.detail_selected_index][0] if self.detail_flat_subtasks else "")
            st = self._get_subtask_by_path(path) if path else None
            if st and self.edit_index < len(st.tests):
                st.tests[self.edit_index] = new_value
                self.manager.save_task(task)
        elif context == 'blocker' and task and self.edit_index is not None:
            path = self.detail_selected_path or (self.detail_flat_subtasks[self.detail_selected_index][0] if self.detail_flat_subtasks else "")
            st = self._get_subtask_by_path(path) if path else None
            if st and self.edit_index < len(st.blockers):
                st.blockers[self.edit_index] = new_value
                self.manager.save_task(task)

        # Обновляем кеш
        if task and task.id in self.task_details_cache:
            self.task_details_cache[task.id] = task

        self.load_tasks(preserve_selection=True)
        self.cancel_edit()

    def cancel_edit(self):
        """Отменить редактирование"""
        self.editing_mode = False
        self.edit_context = None
        self.edit_index = None
        self.edit_buffer.text = ''
        if hasattr(self, "app") and self.app:
            self.app.layout.focus(self.main_window)

    def _build_clipboard(self):
        if PyperclipClipboard:
            try:
                return PyperclipClipboard()
            except Exception:
                pass
        return InMemoryClipboard()

    def _clipboard_text(self) -> str:
        clipboard = getattr(self, "clipboard", None)
        if not clipboard:
            return ""
        try:
            data = clipboard.get_data()
        except Exception:
            return ""
        if not data:
            return self._system_clipboard_fallback()
        text = data.text or ""
        if text:
            return text
        return self._system_clipboard_fallback()

    def _system_clipboard_fallback(self) -> str:
        # Try pyperclip via prompt_toolkit wrapper
        if PyperclipClipboard:
            try:
                clip = PyperclipClipboard()
                data = clip.get_data()
                if data and data.text:
                    return data.text
            except Exception:
                pass
        # Try native commands (pbpaste/wl-paste/xclip)
        commands = [
            ["pbpaste"],
            ["wl-paste", "-n"],
            ["wl-copy", "-o"],
            ["xclip", "-selection", "clipboard", "-out"],
            ["clip.exe"],
        ]
        for cmd in commands:
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=1)
            except Exception:
                continue
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout
        return ""

    def _paste_from_clipboard(self) -> None:
        if not self.editing_mode:
            return
        text = self._clipboard_text()
        if not text:
            self.set_status_message(self._t("CLIPBOARD_EMPTY"), ttl=3)
            return
        buf = self.edit_buffer
        cursor = buf.cursor_position
        buf.text = buf.text[:cursor] + text + buf.text[cursor:]
        buf.cursor_position = cursor + len(text)
        self.force_render()

    def exit_detail_view(self):
        if hasattr(self, "single_subtask_view") and self.single_subtask_view:
            self.single_subtask_view = None
            self.horizontal_offset = 0
            self._set_footer_height(0 if self.detail_mode else 9)
            return
        if not self.detail_mode:
            return
        if self.navigation_stack:
            prev = self.navigation_stack.pop()
            self.current_task = prev["task"]
            self.current_task_detail = prev["detail"]
            self.detail_selected_index = 0
            self.detail_selected_path = ""
        else:
            self.detail_mode = False
            self.current_task = None
            self.current_task_detail = None
            self.detail_selected_index = 0
            self.detail_selected_path = ""
            self.detail_view_offset = 0
            self.horizontal_offset = 0
            self.settings_mode = False
            self._set_footer_height(9)

    def edit_current_item(self):
        """Редактировать текущий элемент"""
        if self.detail_mode and self.current_task_detail:
            # В режиме просмотра подзадачи
            if hasattr(self, "single_subtask_view") and self.single_subtask_view:
                # Редактируем название подзадачи
                entry = self._selected_subtask_entry()
                if entry:
                    _, st, _, _, _ = entry
                    self.start_editing('subtask_title', st.title, self.detail_selected_index)
            else:
                # В списке подзадач - редактируем название подзадачи
                entry = self._selected_subtask_entry()
                if entry:
                    _, st, _, _, _ = entry
                    self.start_editing('subtask_title', st.title, self.detail_selected_index)
        else:
            # В списке задач - редактируем название задачи
            if self.filtered_tasks:
                task = self.filtered_tasks[self.selected_index]
                task_detail = task.detail or TaskFileParser.parse(Path(task.task_file))
                self.current_task_detail = task_detail
                self.start_editing('task_title', task_detail.title)

    def get_footer_text(self) -> FormattedText:
        scroll_info = f"{self._t('OFFSET_LABEL')}{self.horizontal_offset}" if self.horizontal_offset > 0 else ""
        if getattr(self, 'help_visible', False):
            return FormattedText([
                ("class:text.dimmer", self._t("NAV_STATUS_HINT")),
                ("", "\n"),
                ("class:text.dim", self._t("NAV_CHECKPOINT_HINT")),
            ])
        if getattr(self, "single_subtask_view", None):
            return FormattedText([])
        if self.detail_mode and self.current_task_detail:
            return FormattedText([("class:text.dim", self._t("NAV_ARROWS_HINT"))])
        if self.editing_mode:
            return FormattedText([
                ("class:text.dimmer", self._t("NAV_EDIT_HINT")),
            ])
        desc = self._current_description_snippet() or self._t("DESCRIPTION_MISSING")
        detail = self._current_task_detail_obj()
        segments: List[str] = []
        seen: set[str] = set()
        if detail:
            domain = detail.domain or ""
            if domain:
                for part in domain.split('/'):
                    if part:
                        formatted = f"[{part}]"
                        if formatted not in seen:
                            segments.append(formatted)
                            seen.add(formatted)
            for comp in (detail.phase, detail.component):
                if comp:
                    formatted = f"[{comp}]"
                    if formatted not in seen:
                        segments.append(formatted)
                        seen.add(formatted)
        path_text = "->".join(segments) if segments else "-"
        start_time = "-"
        finish_time = "-"
        if detail:
            if getattr(detail, "created", None):
                start_time = str(detail.created)
            if detail.status == "OK" and getattr(detail, "updated", None):
                finish_time = str(detail.updated)
        duration_value = self._task_duration_value(detail)
        table_width = max(60, self.get_terminal_width())
        inner_width = max(30, table_width - 4)

        def add_block(rows: List[str], label: str, value: str, max_lines: int = 1) -> None:
            label_len = len(label)
            available = max(1, inner_width - label_len)
            text_value = value or "—"
            chunks = textwrap.wrap(text_value, available) or ["—"]
            truncated = len(chunks) > max_lines
            chunks = chunks[:max_lines]
            if truncated and chunks:
                tail = chunks[-1]
                if len(tail) >= available:
                    tail = tail[:-1] + "…"
                else:
                    tail = tail + "…"
                chunks[-1] = tail
            for idx in range(max_lines):
                prefix = label if idx == 0 else " " * label_len
                chunk = chunks[idx] if idx < len(chunks) else ""
                row = (prefix + chunk).ljust(inner_width)
                rows.append(row[:inner_width])

        rows: List[str] = []
        add_block(rows, f" {self._t('DOMAIN')}: ", path_text, max_lines=2)
        add_block(rows, " Time: ", f"{start_time} → {finish_time}", max_lines=1)
        add_block(rows, " Duration: ", duration_value, max_lines=1)
        add_block(rows, f" {self._t('DESCRIPTION')}: ", desc, max_lines=2)
        legend_text = "◉=Done/In Progress | ◎=Backlog | %=progress | Σ=subtasks | ?=help" + scroll_info
        add_block(rows, " Legend: ", legend_text, max_lines=1)
        while len(rows) < 7:
            rows.append(" " * inner_width)

        border = "+" + "-" * (inner_width + 2) + "+"
        parts: List[Tuple[str, str]] = []
        parts.append(("class:border", border + "\n"))
        for idx, row in enumerate(rows):
            parts.append(("class:border", "| "))
            parts.append(("class:text", row))
            parts.append(("class:border", " |\n"))
        parts.append(("class:border", border))
        return FormattedText(parts)

    def get_body_content(self) -> FormattedText:
        """Returns content for main body - either task list or detail view."""
        if self.settings_mode:
            return self.get_settings_panel()
        if self.detail_mode and self.current_task_detail:
            # Если открыт просмотр конкретной подзадачи — показываем его
            if hasattr(self, "single_subtask_view") and self.single_subtask_view:
                return self.single_subtask_view
            return self.get_detail_text()
        self.maybe_reload()

        # Normal mode: show task list with side preview
        # Simple approach: just show task list, preview is handled separately if needed
        # For now, just return task list
        return self.get_task_list_text()

    def get_content_text(self) -> FormattedText:
        """Deprecated - use get_body_content instead."""
        return self.get_body_content()

    def close_settings_dialog(self):
        self.settings_mode = False
        self.editing_mode = False
        self._set_footer_height(9 if not self.detail_mode else 0)
        self.force_render()

    def _resolve_body_container(self):
        if self.editing_mode:
            return self._build_edit_container()
        return self.main_window

    def _build_edit_container(self):
        labels = {
            'task_title': self._t("EDIT_TASK_TITLE"),
            'task_description': self._t("EDIT_TASK_DESCRIPTION"),
            'subtask_title': self._t("EDIT_SUBTASK"),
            'criterion': self._t("EDIT_CRITERION"),
            'test': self._t("EDIT_TEST"),
            'blocker': self._t("EDIT_BLOCKER"),
            'token': 'GitHub PAT',
            'project_number': self._t("EDIT_PROJECT_NUMBER"),
        }
        label = labels.get(self.edit_context, self._t("EDIT_GENERIC"))
        width = max(40, self.get_terminal_width() - 4)
        header = Window(
            content=FormattedTextControl([('class:header', f" {label} ".ljust(width))]),
            height=1,
            always_hide_cursor=True,
        )
        self.edit_field.buffer.cursor_position = len(self.edit_field.text)
        children = [header, Window(height=1, char='─'), self.edit_field]

        if self.edit_context == 'token':
            button_text = self._t("BTN_VALIDATE_PAT")

            def fragments():
                return [('class:header', button_text, lambda mouse_event: self._validate_edit_buffer_pat() if (
                    mouse_event.event_type == MouseEventType.MOUSE_UP and mouse_event.button == MouseButton.LEFT
                ) else None)]

            button_control = FormattedTextControl(fragments)
            children.append(Window(height=1, char=' '))
            children.append(Window(content=button_control, height=1, always_hide_cursor=True))

        return HSplit(children, padding=0)

    def get_settings_panel(self) -> FormattedText:
        options = self._settings_options()
        if not options:
            return FormattedText([("class:text.dim", self._t("SETTINGS_UNAVAILABLE"))])
        width = max(70, min(110, self.get_terminal_width() - 4))
        inner_width = max(30, width - 2)
        max_label = max(len(opt["label"]) for opt in options)
        label_width = max(14, min(inner_width - 12, max_label + 2))
        value_width = max(10, inner_width - label_width - 2)
        self.settings_selected_index = min(self.settings_selected_index, len(options) - 1)
        # учитываем высоту хедера/хинтов и футера, оставляем запас под подсказки
        occupied = 8  # рамки и заголовок
        available = self.get_terminal_height() - self.footer_height - occupied
        visible = max(3, available - 3)
        max_offset = max(0, len(options) - visible)
        self.settings_view_offset = max(0, min(self.settings_view_offset, max_offset))
        if self.settings_selected_index < self.settings_view_offset:
            self.settings_view_offset = self.settings_selected_index
        elif self.settings_selected_index >= self.settings_view_offset + visible:
            self.settings_view_offset = self.settings_selected_index - visible + 1
        start = self.settings_view_offset
        end = min(len(options), start + visible)

        lines: List[Tuple[str, str]] = []
        lines.append(('class:border', '+' + '='*width + '+\n'))
        lines.append(('class:border', '| '))
        title = self._t("SETTINGS_TITLE")
        lines.append(('class:header', title.center(width - 2)))
        lines.append(('class:border', ' |\n'))
        lines.append(('class:border', '+' + '-'*width + '+\n'))

        hidden_above = start
        hidden_below = len(options) - end

        for idx in range(start, end):
            option = options[idx]
            prefix = '▸' if idx == self.settings_selected_index else ' '
            label_text = option['label'][:label_width].ljust(label_width)
            value_text = option['value']
            if len(value_text) > value_width:
                value_text = value_text[:max(1, value_width - 1)] + '…'
            row_text = f"{prefix} {label_text}{value_text.ljust(value_width)}"
            style = 'class:selected' if idx == self.settings_selected_index else ('class:text.dim' if option.get('disabled') else 'class:text')
            lines.append(('class:border', '| '))
            lines.append((style, row_text.ljust(inner_width)))
            lines.append(('class:border', ' |\n'))
            if idx == self.settings_selected_index and option.get('hint'):
                hint_lines = textwrap.wrap(option['hint'], width - 6) or ['']
                for hint_line in hint_lines:
                    lines.append(('class:border', '| '))
                    lines.append(('class:text.dim', f"  {hint_line}".ljust(inner_width)))
                    lines.append(('class:border', ' |\n'))

        if hidden_below:
            lines.append(('class:border', '| '))
            lines.append(('class:text.dim', f"↓ +{hidden_below}".ljust(inner_width)))
            lines.append(('class:border', ' |\n'))
        if hidden_above:
            lines.append(('class:border', '| '))
            lines.append(('class:text.dim', f"↑ +{hidden_above}".ljust(inner_width)))
            lines.append(('class:border', ' |\n'))

        lines.append(('class:border', '+' + '-'*width + '+\n'))
        hint = self._t("SETTINGS_NAV_HINT")
        lines.append(('class:border', '| '))
        lines.append(('class:text.dim', hint[:width - 2].ljust(width - 2)))
        lines.append(('class:border', ' |\n'))
        lines.append(('class:border', '+' + '='*width + '+'))
        return FormattedText(lines)

    def _settings_options(self) -> List[Dict[str, Any]]:
        snapshot = self._project_config_snapshot()
        options: List[Dict[str, Any]] = []
        status_reason = snapshot.get("status_reason") or "n/a"
        status_line = self._t("SETTINGS_STATUS_ON") if snapshot.get("runtime_enabled") else self._t("SETTINGS_STATUS_OFF").format(reason=status_reason)
        options.append({
            "label": self._t("SETTINGS_STATUS_LABEL"),
            "value": status_line,
            "hint": snapshot.get("status_reason") or self._t("SETTINGS_STATUS_HINT"),
            "action": None,
        })
        if snapshot['token_saved']:
            pat_value = self._t("SETTINGS_PAT_SAVED").format(preview=snapshot['token_preview'])
        elif snapshot['token_env']:
            pat_value = self._t("SETTINGS_PAT_ENV").format(env=snapshot['token_env'])
        else:
            pat_value = self._t("SETTINGS_PAT_NOT_SET")
        options.append({
            "label": self._t("SETTINGS_PAT_LABEL"),
            "value": pat_value,
            "hint": self._t("SETTINGS_PAT_HINT"),
            "action": "edit_pat",
        })

        if not snapshot['config_exists']:
            sync_value = self._t("SETTINGS_SYNC_NO_REMOTE")
        elif not snapshot['config_enabled']:
            sync_value = self._t("SETTINGS_SYNC_DISABLED")
        elif not snapshot['token_active']:
            sync_value = self._t("SETTINGS_SYNC_NO_PAT")
        else:
            sync_value = self._t("SETTINGS_SYNC_ENABLED")
        options.append({
            "label": self._t("SETTINGS_SYNC_LABEL"),
            "value": sync_value,
            "hint": self._t("SETTINGS_SYNC_HINT"),
            "action": "toggle_sync",
            "disabled": not snapshot['config_exists'],
            "disabled_msg": snapshot['status_reason'] if not snapshot['config_exists'] else "",
        })

        target_value = snapshot['target_label']
        target_hint = snapshot['target_hint']
        if not snapshot['config_exists']:
            target_value = self._t("SETTINGS_PROJECT_UNAVAILABLE")
            target_hint = snapshot['status_reason'] or self._t("STATUS_MESSAGE_PROJECT_URL_UNAVAILABLE")
        elif snapshot['status_reason'] and not snapshot['config_enabled']:
            target_hint = snapshot['status_reason']
        options.append({
            "label": self._t("SETTINGS_PROJECT_LABEL"),
            "value": target_value,
            "hint": snapshot['target_hint'],
            "action": None,
        })

        if not snapshot['config_exists'] or snapshot['status_reason'].lower().startswith("нет конфигурации") or "remote origin" in snapshot['status_reason']:
            options.append({
                "label": self._t("SETTINGS_BOOTSTRAP_LABEL"),
                "value": self._t("SETTINGS_BOOTSTRAP_VALUE"),
                "hint": self._t("SETTINGS_BOOTSTRAP_HINT"),
                "action": "bootstrap_git",
            })

        options.append({
            "label": self._t("SETTINGS_PROJECT_URL_LABEL"),
            "value": snapshot.get("project_url") or self._t("SETTINGS_PROJECT_UNAVAILABLE"),
            "hint": self._t("SETTINGS_PROJECT_URL_HINT"),
            "action": None,
        })

        options.append({
            "label": self._t("SETTINGS_PROJECT_NUMBER_LABEL"),
            "value": str(snapshot['number']) if snapshot['number'] else '—',
            "hint": self._t("SETTINGS_PROJECT_NUMBER_HINT"),
            "action": "edit_number",
        })

        options.append({
            "label": self._t("SETTINGS_POOL_LABEL"),
            "value": str(snapshot.get("workers")) if snapshot.get("workers") else "auto",
            "hint": self._t("SETTINGS_POOL_HINT"),
            "action": "edit_workers",
        })

        options.append({
            "label": self._t("SETTINGS_LAST_PULL_LABEL"),
            "value": f"{snapshot.get('last_pull') or '—'} / {snapshot.get('last_push') or '—'}",
            "hint": self._t("SETTINGS_LAST_PULL_HINT"),
            "action": None,
        })
        rate_value = self._t("VALUE_NOT_AVAILABLE")
        if snapshot.get("rate_remaining") is not None:
            rate_value = f"{snapshot['rate_remaining']} @ {snapshot.get('rate_reset_human') or '—'}"
            if snapshot.get("rate_wait"):
                rate_value = f"{rate_value} wait={int(snapshot['rate_wait'])}s"
        options.append({
            "label": self._t("SETTINGS_RATE_LABEL"),
            "value": rate_value,
            "hint": self._t("SETTINGS_RATE_HINT"),
            "action": None,
        })
        options.append({
            "label": self._t("SETTINGS_REMOTE_LABEL"),
            "value": snapshot.get("origin_url") or self._t("SETTINGS_PAT_NOT_SET"),
            "hint": self._t("SETTINGS_REMOTE_HINT"),
            "action": None,
        })

        options.append({
            "label": self._t("SETTINGS_REFRESH_LABEL"),
            "value": self._t("SETTINGS_REFRESH_VALUE"),
            "hint": self._t("SETTINGS_REFRESH_HINT"),
            "action": "refresh_metadata",
            "disabled": not (snapshot['config_exists'] and snapshot['token_active']),
            "disabled_msg": self._t("SETTINGS_REFRESH_DISABLED"),
        })
        options.append({
            "label": self._t("SETTINGS_VALIDATE_PAT_LABEL"),
            "value": self.pat_validation_result or "GitHub viewer",
            "hint": self._t("SETTINGS_VALIDATE_PAT_HINT"),
            "action": "validate_pat",
            "disabled": not (snapshot['token_saved'] or snapshot['token_env']),
            "disabled_msg": self._t("SETTINGS_VALIDATE_PAT_DISABLED"),
        })
        options.append({
            "label": f"{self._t('LANGUAGE_LABEL')} / Language",
            "value": self.language,
            "hint": self._t("LANGUAGE_HINT"),
            "action": "cycle_lang",
        })
        return options

    def _project_config_snapshot(self) -> Dict[str, Any]:
        try:
            status = _projects_status_payload()
        except Exception as exc:
            return {
                "owner": "",
                "repo": "",
                "number": None,
                "project_url": None,
                "project_id": None,
                "config_exists": False,
                "config_enabled": False,
                "runtime_enabled": False,
                "token_saved": False,
                "token_preview": "",
                "token_env": "",
                "token_active": False,
                "target_label": "—",
                "target_hint": f"Git Projects недоступен: {exc}",
                "status_reason": str(exc),
                "last_pull": None,
                "last_push": None,
                "workers": None,
                "rate_remaining": None,
                "rate_reset_human": None,
                "rate_wait": None,
                "origin_url": self._origin_url(),
            }
        cfg_exists = bool(status["owner"] and (status["project_number"] or status.get("project_id")))
        return {
            "owner": status["owner"],
            "repo": status["repo"],
            "number": status["project_number"] or 1,
            "project_url": status.get("project_url"),
            "project_id": status.get("project_id"),
            "config_exists": cfg_exists,
            "config_enabled": status["auto_sync"],
            "runtime_enabled": status.get("runtime_enabled"),
            "token_saved": status["token_saved"],
            "token_preview": status["token_preview"],
            "token_env": status["token_env"],
            "token_active": status["token_present"],
            "target_label": status["target_label"],
            "target_hint": status["target_hint"],
            "status_reason": status["status_reason"],
            "last_pull": status.get("last_pull"),
            "last_push": status.get("last_push"),
            "workers": status.get("workers"),
            "rate_remaining": status.get("rate_remaining"),
            "rate_reset_human": status.get("rate_reset_human"),
            "rate_wait": status.get("rate_wait"),
            "origin_url": self._origin_url(),
        }

    def _open_project_url(self) -> None:
        snapshot = self._project_config_snapshot()
        url = snapshot.get("project_url")
        if url:
            try:
                webbrowser.open(url)
                self.set_status_message(self._t("STATUS_MESSAGE_PROJECT_OPEN", url=url), ttl=3)
            except Exception as exc:  # pragma: no cover - platform dependent
                self.set_status_message(self._t("STATUS_MESSAGE_OPEN_FAILED", error=exc), ttl=4)
        else:
            self.set_status_message(self._t("STATUS_MESSAGE_PROJECT_URL_UNAVAILABLE"))

    @staticmethod
    def _origin_url() -> str:
        try:
            result = subprocess.run(
                ["git", "config", "--get", "remote.origin.url"],
                capture_output=True,
                text=True,
                check=True,
            )
        except Exception:
            return ""
        return result.stdout.strip()

    def _set_project_number(self, number_value: int) -> None:
        update_project_target(int(number_value))

    def move_settings_selection(self, delta: int) -> None:
        options = self._settings_options()
        total = len(options)
        if not total:
            return
        self.settings_selected_index = max(0, min(self.settings_selected_index + delta, total - 1))
        self._ensure_settings_selection_visible(total)
        self.force_render()

    def activate_settings_option(self):
        options = self._settings_options()
        if not options:
            return
        idx = self.settings_selected_index
        option = options[idx]
        if option.get("disabled"):
            self.set_status_message(option.get("disabled_msg") or self._t("OPTION_DISABLED"))
            return
        action = option.get("action")
        if not action:
            return
        if action == "edit_pat":
            self.set_status_message(self._t("STATUS_MESSAGE_PASTE_PAT"))
            self.start_editing('token', '', None)
            self.edit_buffer.cursor_position = 0
        elif action == "toggle_sync":
            snapshot = self._project_config_snapshot()
            desired = not snapshot['config_enabled']
            update_projects_enabled(desired)
            state = self._t("STATUS_MESSAGE_SYNC_ON") if desired else self._t("STATUS_MESSAGE_SYNC_OFF")
            self.set_status_message(state)
            self.force_render()
        elif action == "edit_number":
            snapshot = self._project_config_snapshot()
            self.start_editing('project_number', str(snapshot['number']), None)
            self.edit_buffer.cursor_position = len(self.edit_buffer.text)
        elif action == "edit_workers":
            snapshot = self._project_config_snapshot()
            current = snapshot.get("workers")
            self.start_editing('project_workers', str(current) if current else "0", None)
            self.edit_buffer.cursor_position = len(self.edit_buffer.text)
        elif action == "bootstrap_git":
            self.start_editing('bootstrap_remote', "https://github.com/owner/repo.git", None)
            self.edit_buffer.cursor_position = 0
        elif action == "refresh_metadata":
            reload_projects_sync()
            self.set_status_message(self._t("STATUS_MESSAGE_REFRESHED"))
            self.force_render()
        elif action == "validate_pat":
            self._start_pat_validation()
        elif action == "cycle_lang":
            self._cycle_language()
        else:
            self.set_status_message(self._t("STATUS_MESSAGE_OPTION_DISABLED"))

    def open_settings_dialog(self):
        self.settings_mode = True
        self.settings_selected_index = 0
        self.editing_mode = False
        self._set_footer_height(0)
        self.force_render()

    def _start_pat_validation(self, token: Optional[str] = None, label: str = "PAT", cache_result: bool = True):
        source_token = token or get_user_token() or os.getenv("APPLY_TASK_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")
        if not source_token:
            self.set_status_message(self._t("STATUS_MESSAGE_PAT_MISSING"))
            return

        if cache_result:
            self.pat_validation_result = self._t("STATUS_MESSAGE_VALIDATING")
        spinner_label = self._t("STATUS_MESSAGE_VALIDATE_LABEL", label=label)
        self._start_spinner(spinner_label)

        def worker():
            try:
                ok, msg = validate_pat_token_http(source_token)
                self.set_status_message(msg, ttl=6)
                if cache_result:
                    self.pat_validation_result = msg
            finally:
                self._stop_spinner()
                self.force_render()

        threading.Thread(target=worker, daemon=True).start()

    def _validate_edit_buffer_pat(self):
        value = self.edit_buffer.text.strip()
        if not value:
            self.set_status_message(self._t("STATUS_MESSAGE_PROMPT_PAT"), ttl=4)
            return
        self._start_pat_validation(token=value, label="PAT (ввод)", cache_result=False)

    def run(self):
        self.app.run()

    def _build_clipboard(self):
        if PyperclipClipboard:
            try:
                return PyperclipClipboard()
            except Exception:
                pass
        return InMemoryClipboard()


# ============================================================================
# COMMAND IMPLEMENTATIONS
# ============================================================================


def cmd_tui(args) -> int:
    tui = TaskTrackerTUI(
        Path(".tasks"),
        theme=getattr(args, "theme", DEFAULT_THEME),
        mono_select=getattr(args, "mono_select", False),
    )
    tui.run()
    return 0


def cmd_list(args) -> int:
    manager = TaskManager()
    domain = derive_domain_explicit(getattr(args, "domain", ""), getattr(args, "phase", None), getattr(args, "component", None))
    tasks = manager.list_tasks(domain)
    if args.status:
        tasks = [t for t in tasks if t.status == args.status]
    if args.component:
        tasks = [t for t in tasks if t.component == args.component]
    if args.phase:
        tasks = [t for t in tasks if t.phase == args.phase]
    payload = {
        "total": len(tasks),
        "filters": {
            "domain": domain or "",
            "phase": args.phase or "",
            "component": args.component or "",
            "status": args.status or "",
            "progress_details": bool(args.progress),
        },
        "tasks": [
            task_to_dict(t, include_subtasks=bool(args.progress))
            for t in tasks
        ],
    }
    return structured_response(
        "list",
        status="OK",
        message=translate("MSG_LIST_BUILT"),
        payload=payload,
        summary=translate("SUMMARY_TASKS", count=len(tasks)),
    )


def cmd_show(args) -> int:
    last_id, last_domain = get_last_task()
    task_id = normalize_task_id(args.task_id) if args.task_id else last_id
    if not task_id:
        return structured_error("show", translate("ERR_SHOW_NO_TASK"))
    manager = TaskManager()
    domain = derive_domain_explicit(getattr(args, "domain", ""), getattr(args, "phase", None), getattr(args, "component", None)) or last_domain or ""
    task = manager.load_task(task_id, domain)
    if not task:
        return structured_error("show", translate("ERR_TASK_NOT_FOUND", task_id=task_id))
    save_last_task(task.id, task.domain)
    payload = {"task": task_to_dict(task, include_subtasks=True)}
    if task.subtasks:
        payload["subtasks_completed"] = sum(1 for st in task.subtasks if st.completed)
    return structured_response(
        "show",
        status=task.status,
        message=translate("MSG_TASK_DETAILS"),
        payload=payload,
        summary=f"{task.id}: {task.title}",
    )


def cmd_create(args) -> int:
    manager = TaskManager()
    args.parent = normalize_task_id(args.parent)
    domain = derive_domain_explicit(getattr(args, "domain", ""), getattr(args, "phase", None), getattr(args, "component", None))

    def fail(message: str, payload: Optional[Dict[str, Any]] = None) -> int:
        if getattr(args, "validate_only", False):
            return validation_response("create", False, message, payload)
        return structured_error("create", message, payload=payload)

    def success_preview(task: TaskDetail, message: str = "") -> int:
        task_snapshot = task_to_dict(task, include_subtasks=True)
        payload = {"task": task_snapshot}
        msg = message or translate("MSG_VALIDATION_PASSED")
        return validation_response("create", True, msg, payload)

    task = manager.create_task(
        args.title,
        status=args.status,
        priority=args.priority,
        parent=args.parent,
        domain=domain,
        phase=args.phase or "",
        component=args.component or "",
    )
    if not args.description or not args.description.strip() or args.description.strip().upper() == "TBD":
        return fail(translate("ERR_DESCRIPTION_REQUIRED"))
    task.description = args.description.strip()
    task.context = args.context or ""
    if args.tags:
        task.tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    if args.subtasks:
        try:
            subtasks_payload = _load_subtasks_source(args.subtasks)
            task.subtasks = parse_subtasks_flexible(subtasks_payload)
        except SubtaskParseError as e:
            return fail(str(e))
    if args.dependencies:
        for dep in args.dependencies.split(","):
            dep = dep.strip()
            if dep:
                task.dependencies.append(dep)
    if args.next_steps:
        for step in args.next_steps.split(";"):
            if step.strip():
                task.next_steps.append(step.strip())
    if args.tests:
        for t in args.tests.split(";"):
            if t.strip():
                task.success_criteria.append(t.strip())
    if not task.success_criteria:
        return fail(translate("ERR_TESTS_REQUIRED"))
    if args.risks:
        for r in args.risks.split(";"):
            if r.strip():
                task.risks.append(r.strip())
    if not task.risks:
        return fail(translate("ERR_RISKS_REQUIRED"))

    # Flagship-валидация подзадач
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
        return fail(translate("ERR_FLAGSHIP_SUBTASKS"), payload=payload)

    task.update_status_from_progress()
    if getattr(args, "validate_only", False):
        return success_preview(task)
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

    def fail(message: str, payload: Optional[Dict[str, Any]] = None) -> int:
        if getattr(args, "validate_only", False):
            return validation_response("task", False, message, payload)
        return structured_error("task", message, payload=payload)

    def success_preview(task: TaskDetail, message: str = "") -> int:
        payload = {"task": task_to_dict(task, include_subtasks=True)}
        msg = message or translate("MSG_VALIDATION_PASSED")
        return validation_response("task", True, msg, payload)

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
    if not args.description or not args.description.strip() or args.description.strip().upper() == "TBD":
        return fail(translate("ERR_DESCRIPTION_REQUIRED"))
    task.description = args.description.strip()
    task.context = args.context or ""
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
        return fail(translate("ERR_TESTS_REQUIRED"))
    if args.risks:
        task.risks = [r.strip() for r in args.risks.split(";") if r.strip()]
    if not task.risks:
        return fail(translate("ERR_RISKS_REQUIRED"))

    if args.subtasks:
        try:
            subtasks_payload = _load_subtasks_source(args.subtasks)
            task.subtasks = parse_subtasks_flexible(subtasks_payload)
        except SubtaskParseError as e:
            return fail(str(e))

    # Flagship-валидация подзадач
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
        return fail(translate("ERR_FLAGSHIP_SUBTASKS"), payload=payload)

    task.update_status_from_progress()
    if getattr(args, "validate_only", False):
        return success_preview(task)
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
    while description.upper() == "TBD":
        print(translate("GUIDED_DESCRIPTION_TBD"))
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
    subtasks = []
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


def cmd_update(args) -> int:
    # Бекст совместимость: допускаем оба порядка аргументов
    status = None
    task_id = None
    last_id, last_domain = get_last_task()
    for candidate in (args.arg1, args.arg2):
        if candidate and candidate.upper() in ("OK", "WARN", "FAIL"):
            status = candidate.upper()
        elif candidate:
            task_id = normalize_task_id(candidate)

    if status is None:
        return structured_error("update", translate("ERR_STATUS_REQUIRED"))

    if task_id is None:
        task_id = last_id
        if not task_id:
            return structured_error("update", translate("ERR_NO_TASK_AND_LAST"))

    manager = TaskManager()
    domain = derive_domain_explicit(getattr(args, "domain", ""), getattr(args, "phase", None), getattr(args, "component", None)) or last_domain or ""
    ok, error = manager.update_task_status(task_id, status, domain)
    if ok:
        save_last_task(task_id, domain)
        detail = manager.load_task(task_id, domain)
        payload = {"task": task_to_dict(detail, include_subtasks=True) if detail else {"id": task_id}}
        return structured_response(
            "update",
            status=status,
            message=translate("MSG_STATUS_UPDATED", task_id=task_id),
            payload=payload,
            summary=f"{task_id} → {status}",
        )

    payload = {"task_id": task_id, "domain": domain}
    if error and error.get("code") == "not_found":
        return structured_error("update", error.get("message", translate("ERR_TASK_NOT_FOUND", task_id=task_id)), payload=payload)
    return structured_response(
        "update",
        status="ERROR",
        message=(error or {}).get("message", translate("ERR_STATUS_NOT_UPDATED")),
        payload=payload,
        exit_code=1,
    )


def cmd_status_set(args) -> int:
    """Единообразная установка статуса (OK/WARN/FAIL) — терминология TUI=CLI."""
    manager = TaskManager()
    status = args.status.upper()
    ok, error = manager.update_task_status(normalize_task_id(args.task_id), status, args.domain or "")
    if ok:
        detail = manager.load_task(normalize_task_id(args.task_id), args.domain or "")
        payload = {"task": task_to_dict(detail, include_subtasks=True) if detail else {"id": normalize_task_id(args.task_id)}}
        return structured_response(
            "status-set",
            status="OK",
            message=f"{normalize_task_id(args.task_id)} → {status}",
            payload=payload,
            summary=f"{normalize_task_id(args.task_id)} → {status}",
        )
    payload = {"task_id": args.task_id, "domain": args.domain or "", "status": status}
    return structured_response(
        "status-set",
        status="ERROR",
        message=(error or {}).get("message", "Статус не обновлён"),
        payload=payload,
        exit_code=1,
    )


def cmd_analyze(args) -> int:
    manager = TaskManager()
    domain = derive_domain_explicit(getattr(args, "domain", ""), getattr(args, "phase", None), getattr(args, "component", None))
    task = manager.load_task(normalize_task_id(args.task_id), domain)
    if not task:
        return structured_error("analyze", f"Задача {args.task_id} не найдена")
    payload = {
        "task": task_to_dict(task, include_subtasks=True),
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


def cmd_next(args) -> int:
    manager = TaskManager()
    domain = derive_domain_explicit(getattr(args, "domain", ""), getattr(args, "phase", None), getattr(args, "component", None))
    tasks = manager.list_tasks(domain, skip_sync=True)
    candidates = [t for t in tasks if t.status != "OK" and t.calculate_progress() < 100]
    filter_hint = f" (domain='{domain or '-'}', phase='{args.phase or '-'}', component='{args.component or '-'}')"
    if not candidates:
        payload = {
            "filters": {"domain": domain or "", "phase": args.phase or "", "component": args.component or ""},
            "candidates": [],
        }
        return structured_response(
            "next",
            status="OK",
            message="Все задачи завершены" + filter_hint,
            payload=payload,
            summary="Нет незавершённых задач",
        )
    priority_map = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}

    def score(t: TaskDetail):
        blocked = -100 if t.blocked else 0
        return (blocked, -priority_map.get(t.priority, 0), t.calculate_progress())

    candidates.sort(key=score)
    top = candidates[:3]
    save_last_task(candidates[0].id, candidates[0].domain)
    payload = {
        "filters": {"domain": domain or "", "phase": args.phase or "", "component": args.component or ""},
        "candidates": [task_to_dict(t) for t in top],
        "selected": task_to_dict(candidates[0]),
    }
    return structured_response(
        "next",
        status="OK",
        message="Рекомендации обновлены" + filter_hint,
        payload=payload,
        summary=f"Выбрано {candidates[0].id}",
    )


def _parse_semicolon_list(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(";") if item.strip()]


def cmd_add_subtask(args) -> int:
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


def cmd_add_dependency(args) -> int:
    manager = TaskManager()
    domain = derive_domain_explicit(getattr(args, "domain", ""), getattr(args, "phase", None), getattr(args, "component", None))
    task_id = normalize_task_id(args.task_id)
    if manager.add_dependency(task_id, args.dependency, domain):
        payload = {"task_id": task_id, "dependency": args.dependency}
        return structured_response(
            "add-dep",
            status="OK",
            message=f"Зависимость добавлена в {task_id}",
            payload=payload,
            summary=f"{task_id} +dep",
        )
    return structured_error("add-dep", f"Задача {task_id} не найдена", payload={"task_id": task_id})


def cmd_subtask(args) -> int:
    """Управление подзадачами: добавить / отметить выполненной / вернуть"""
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
        detail = manager.load_task(task_id, domain)
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

    if action == "done":
        ok, msg = manager.set_subtask(task_id, args.done, True, domain, path=args.path)
        if ok:
            payload = _snapshot(args.done, path=args.path)
            payload["operation"] = "done"
            return structured_response(
                "subtask",
                status="OK",
                message=f"Подзадача {args.path or args.done} отмечена выполненной в {task_id}",
                payload=payload,
                summary=f"{task_id} subtask#{args.path or args.done} DONE",
            )
        if msg == "not_found":
            return structured_error("subtask", f"Задача {task_id} не найдена", payload={"task_id": task_id})
        if msg == "index":
            return structured_error("subtask", translate("ERR_SUBTASK_INDEX"), payload={"task_id": task_id})
        return structured_error("subtask", msg or "Операция не выполнена", payload={"task_id": task_id})

    if action == "undo":
        ok, msg = manager.set_subtask(task_id, args.undo, False, domain, path=args.path)
        if ok:
            payload = _snapshot(args.undo, path=args.path)
            payload["operation"] = "undo"
            return structured_response(
                "subtask",
                status="OK",
                message=f"Подзадача {args.path or args.undo} возвращена в работу в {task_id}",
                payload=payload,
                summary=f"{task_id} subtask#{args.path or args.undo} UNDO",
            )
        if msg == "not_found":
            return structured_error("subtask", f"Задача {task_id} не найдена", payload={"task_id": task_id})
        if msg == "index":
            return structured_error("subtask", translate("ERR_SUBTASK_INDEX"), payload={"task_id": task_id})
        return structured_error("subtask", msg or "Операция не выполнена", payload={"task_id": task_id})

    note = (args.note or "").strip()
    if action == "criteria_done":
        ok, msg = manager.update_subtask_checkpoint(task_id, args.criteria_done, "criteria", True, note, domain, path=args.path)
    elif action == "criteria_undo":
        ok, msg = manager.update_subtask_checkpoint(task_id, args.criteria_undo, "criteria", False, note, domain, path=args.path)
    elif action == "tests_done":
        ok, msg = manager.update_subtask_checkpoint(task_id, args.tests_done, "tests", True, note, domain, path=args.path)
    elif action == "tests_undo":
        ok, msg = manager.update_subtask_checkpoint(task_id, args.tests_undo, "tests", False, note, domain, path=args.path)
    elif action == "blockers_done":
        ok, msg = manager.update_subtask_checkpoint(task_id, args.blockers_done, "blockers", True, note, domain, path=args.path)
    else:  # blockers_undo
        ok, msg = manager.update_subtask_checkpoint(task_id, args.blockers_undo, "blockers", False, note, domain, path=args.path)

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


def cmd_ok(args) -> int:
    manager = TaskManager()
    try:
        task_id, domain = resolve_task_reference(
            getattr(args, "task_id", None),
            getattr(args, "domain", None),
            getattr(args, "phase", None),
            getattr(args, "component", None),
        )
    except ValueError as exc:
        return structured_error("ok", str(exc))
    index = args.index
    checkpoints = [
        ("criteria", args.criteria_note, "criteria_done"),
        ("tests", args.tests_note, "tests_done"),
        ("blockers", args.blockers_note, "blockers_done"),
    ]
    for checkpoint, note, action in checkpoints:
        ok, msg = manager.update_subtask_checkpoint(task_id, index, checkpoint, True, note or "", domain)
        if not ok:
            payload = {"task_id": task_id, "checkpoint": checkpoint, "index": index}
            if msg == "not_found":
                return structured_error("ok", f"Задача {task_id} не найдена", payload=payload)
            if msg == "index":
                return structured_error("ok", translate("ERR_SUBTASK_INDEX"), payload=payload)
            return structured_error("ok", msg or "Не удалось подтвердить чекпоинт", payload=payload)
    ok, msg = manager.set_subtask(task_id, index, True, domain)
    if not ok:
        payload = {"task_id": task_id, "index": index}
        return structured_error("ok", msg or "Не удалось завершить подзадачу", payload=payload)
    detail = manager.load_task(task_id, domain)
    save_last_task(task_id, domain)
    payload = {
        "task": task_to_dict(detail, include_subtasks=True) if detail else {"id": task_id},
        "subtask_index": index,
    }
    if detail and 0 <= index < len(detail.subtasks):
        payload["subtask"] = subtask_to_dict(detail.subtasks[index])
    return structured_response(
        "ok",
        status="OK",
        message=f"Подзадача {index} полностью подтверждена и закрыта",
        payload=payload,
        summary=f"{task_id} subtask#{index} OK",
    )


def cmd_note(args) -> int:
    manager = TaskManager()
    try:
        task_id, domain = resolve_task_reference(
            getattr(args, "task_id", None),
            getattr(args, "domain", None),
            getattr(args, "phase", None),
            getattr(args, "component", None),
        )
    except ValueError as exc:
        return structured_error("note", str(exc))
    value = not args.undo
    ok, msg = manager.update_subtask_checkpoint(task_id, args.index, args.checkpoint, value, args.note or "", domain)
    if ok:
        detail = manager.load_task(task_id, domain)
        save_last_task(task_id, domain)
        payload = {
            "task": task_to_dict(detail, include_subtasks=True) if detail else {"id": task_id},
            "checkpoint": args.checkpoint,
            "index": args.index,
            "state": "DONE" if value else "TODO",
        }
        if detail and 0 <= args.index < len(detail.subtasks):
            payload["subtask"] = subtask_to_dict(detail.subtasks[args.index])
        return structured_response(
            "note",
            status="OK",
            message=f"{args.checkpoint.capitalize()} {'подтверждены' if value else 'сброшены'}",
            payload=payload,
            summary=f"{task_id} {args.checkpoint} idx {args.index}",
        )
    payload = {"task_id": task_id, "checkpoint": args.checkpoint, "index": args.index}
    if msg == "not_found":
        return structured_error("note", f"Задача {task_id} не найдена", payload=payload)
    if msg == "index":
        return structured_error("note", translate("ERR_SUBTASK_INDEX"), payload=payload)
    return structured_error("note", msg or "Операция не выполнена", payload=payload)


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
        note_value = base_note
        confirm_checkpoint = auto_mode
        if not auto_mode:
            print(f"\n[Шаг] {label}: {target.title}")
            print(f"  Текущее состояние: TODO. Подтвердить {label.lower()}?")
            confirm_checkpoint = confirm(f"Подтвердить {label.lower()}?", default=True)
            if not confirm_checkpoint:
                operations.append({"checkpoint": checkpoint, "state": "skipped"})
                continue
            if not note_value:
                note_value = prompt("Комментарий/доказательство", default="")
        if not note_value:
            note_value = f"checkpoint:{checkpoint}"
        ok, msg = manager.update_subtask_checkpoint(task_id, subtask_index, checkpoint, True, note_value, domain, path=path)
        if not ok:
            return structured_error("checkpoint", msg or f"Не удалось подтвердить {label.lower()}")
        operations.append({"checkpoint": checkpoint, "state": "confirmed", "note": note_value})

    detail = manager.load_task(task_id, domain)
    completed = False
    if detail:
        target, _, _ = _find_subtask_by_path(detail.subtasks, path)
        ready = target.ready_for_completion() if target else False
        if ready:
            mark_done = auto_mode
            if not auto_mode:
                mark_done = confirm("Все чекпоинты отмечены. Закрыть подзадачу?", default=True)
            if mark_done:
                ok, msg = manager.set_subtask(task_id, subtask_index, True, domain, path=path)
                if not ok:
                    return structured_error("checkpoint", msg or "Не удалось закрыть подзадачу")
                operations.append({"checkpoint": "done", "state": "completed"})
                completed = True
    detail = manager.load_task(task_id, domain)
    save_last_task(task_id, domain)
    payload = {
        "task": task_to_dict(detail, include_subtasks=True) if detail else {"id": task_id},
        "subtask_index": subtask_index,
        "operations": operations,
        "auto": auto_mode,
        "completed": completed,
    }
    return structured_response(
        "checkpoint",
        status="OK",
        message="Мастер чекпоинтов завершён",
        payload=payload,
        summary=f"{task_id}#{subtask_index} checkpoints",
    )


def cmd_move(args) -> int:
    """Переместить задачу в другую подпапку .tasks"""
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


def cmd_clean(args) -> int:
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


def cmd_projects_auth(args) -> int:
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


def cmd_projects_webhook(args) -> int:
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


def cmd_projects_webhook_serve(args) -> int:
    sync_service = _get_sync_service()
    if not sync_service.enabled:
        return structured_error("projects-webhook-serve", "Projects sync disabled (missing token or config)")

    secret = args.secret

    class Handler(BaseHTTPRequestHandler):  # pragma: no cover - network entrypoint
        def do_POST(self_inner):
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

        def log_message(self_inner, format, *args):
            return

    server = HTTPServer((args.host, args.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
    return 0


def cmd_projects_sync_cli(args) -> int:
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
    _invalidate_projects_status_cache()
    return structured_response(
        "projects sync",
        status="OK",
        message=f"Синхронизация завершена ({pulled} pull / {pushed} push{conflict_suffix})",
        payload=payload,
        summary=f"{pulled} pull / {pushed} push{conflict_suffix}",
    )


def _invalidate_projects_status_cache() -> None:
    global _PROJECT_STATUS_CACHE, _PROJECT_STATUS_CACHE_TS, _PROJECT_STATUS_CACHE_TOKEN_PREVIEW
    with _PROJECT_STATUS_LOCK:
        _PROJECT_STATUS_CACHE = None
        _PROJECT_STATUS_CACHE_TS = 0.0
        _PROJECT_STATUS_CACHE_TOKEN_PREVIEW = None


def _projects_status_payload(force_refresh: bool = False) -> Dict[str, Any]:
    global _PROJECT_STATUS_CACHE, _PROJECT_STATUS_CACHE_TS, _PROJECT_STATUS_CACHE_TOKEN_PREVIEW
    current_token = get_user_token()
    current_token_preview = current_token[-4:] if current_token else ""
    now = time.time()
    with _PROJECT_STATUS_LOCK:
        if (
            not force_refresh
            and _PROJECT_STATUS_CACHE is not None
            and _PROJECT_STATUS_CACHE_TOKEN_PREVIEW == current_token_preview
            and now - _PROJECT_STATUS_CACHE_TS < _PROJECT_STATUS_TTL
        ):
            return dict(_PROJECT_STATUS_CACHE)

    try:
        sync_service = _get_sync_service()
    except Exception as exc:
        payload = {
            "owner": "",
            "repo": "",
            "project_number": None,
            "project_id": None,
            "project_url": None,
            "workers": None,
            "rate_remaining": None,
            "rate_reset": None,
            "rate_reset_human": None,
            "rate_wait": None,
            "target_label": "—",
            "target_hint": "Git Projects недоступен: " + str(exc),
            "auto_sync": False,
            "runtime_enabled": False,
            "runtime_reason": str(exc),
            "detect_error": str(exc),
            "status_reason": "Git Projects недоступен",
            "last_pull": None,
            "last_push": None,
            "token_saved": bool(current_token),
            "token_preview": current_token_preview,
            "token_env": "",
            "token_present": False,
            "runtime_disabled_reason": str(exc),
        }
        with _PROJECT_STATUS_LOCK:
            _PROJECT_STATUS_CACHE = payload
            _PROJECT_STATUS_CACHE_TS = time.time()
            _PROJECT_STATUS_CACHE_TOKEN_PREVIEW = current_token_preview
        return dict(payload)

    try:
        sync_service.ensure_metadata()
    except Exception:
        pass
    cfg = sync_service.config
    owner = (cfg.owner if cfg and cfg.owner else "") if cfg else ""
    repo = (cfg.repo if cfg and cfg.repo else "") if cfg else ""
    number = cfg.number if cfg else None
    project_id = sync_service.project_id
    project_url = sync_service.project_url()
    workers = cfg.workers if cfg else None
    token_saved = bool(current_token)
    token_preview = current_token_preview
    env_primary = os.getenv("APPLY_TASK_GITHUB_TOKEN")
    env_secondary = os.getenv("GITHUB_TOKEN") if not env_primary else None
    token_env = "APPLY_TASK_GITHUB_TOKEN" if env_primary else ("GITHUB_TOKEN" if env_secondary else "")
    token_present = sync_service.token_present
    auto_sync = bool(cfg and cfg.enabled)
    target_label = (
        f"{owner}#{number}" if (cfg and cfg.project_type == "user") else f"{owner}/{repo}#{number}"
        if owner and repo and number
        else "—"
    )
    detect_error = sync_service.detect_error
    runtime_reason = sync_service.runtime_disabled_reason
    status_reason = detect_error or runtime_reason
    if not status_reason:
        if not cfg:
            status_reason = "нет конфигурации"
        elif not auto_sync:
            status_reason = "auto-sync выключена"
        elif not token_present:
            status_reason = "нет PAT"
    rate = sync_service.rate_info() or {}
    payload = {
        "owner": owner,
        "repo": repo,
        "project_number": number,
        "project_id": project_id,
        "project_url": project_url,
        "workers": workers,
        "rate_remaining": rate.get("remaining"),
        "rate_reset": rate.get("reset_epoch"),
        "rate_reset_human": datetime.fromtimestamp(rate["reset_epoch"], tz=timezone.utc).strftime("%H:%M:%S") if rate.get("reset_epoch") else None,
        "rate_wait": rate.get("wait"),
        "target_label": target_label,
        "target_hint": "Определяется автоматически из git remote origin",
        "auto_sync": auto_sync,
        "runtime_enabled": sync_service.enabled,
        "runtime_reason": runtime_reason,
        "detect_error": detect_error,
        "status_reason": status_reason or "",
        "last_pull": sync_service.last_pull,
        "last_push": sync_service.last_push,
        "token_saved": token_saved,
        "token_preview": token_preview,
        "token_env": token_env,
        "token_present": token_present,
        "runtime_disabled_reason": runtime_reason,
    }
    with _PROJECT_STATUS_LOCK:
        _PROJECT_STATUS_CACHE = payload
        _PROJECT_STATUS_CACHE_TS = time.time()
        _PROJECT_STATUS_CACHE_TOKEN_PREVIEW = token_preview
    return dict(payload)


def cmd_projects_status(args) -> int:
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


def cmd_projects_autosync(args) -> int:
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


def cmd_projects_workers(args) -> int:
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


def cmd_edit(args) -> int:
    manager = TaskManager()
    domain = derive_domain_explicit(getattr(args, "domain", ""), getattr(args, "phase", None), getattr(args, "component", None))
    task = manager.load_task(normalize_task_id(args.task_id), domain)
    if not task:
        return structured_error("edit", f"Задача {args.task_id} не найдена")
    if args.description:
        task.description = args.description
    if args.context:
        task.context = args.context
    if args.tags:
        task.tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    if args.priority:
        task.priority = args.priority
    if args.phase:
        task.phase = args.phase
    if args.component:
        task.component = args.component
    if args.new_domain:
        task.domain = args.new_domain
    manager.save_task(task)
    payload = {"task": task_to_dict(task, include_subtasks=True)}
    return structured_response(
        "edit",
        status="OK",
        message=f"Задача {task.id} обновлена",
        payload=payload,
        summary=f"{task.id} updated",
    )


def cmd_lint(args) -> int:
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


def cmd_suggest(args) -> int:
    manager = TaskManager()
    folder = getattr(args, "folder", "") or ""
    domain = derive_domain_explicit(getattr(args, "domain", "") or folder, getattr(args, "phase", None), getattr(args, "component", None))
    tasks = manager.list_tasks(domain, skip_sync=True)
    active = [t for t in tasks if t.status != "OK"]
    filter_hint = f" (folder='{folder or domain or '-'}', phase='{getattr(args, 'phase', None) or '-'}', component='{getattr(args, 'component', None) or '-'}')"
    if not active:
        payload = {
            "filters": {"folder": folder or "", "domain": domain or "", "phase": getattr(args, "phase", None) or "", "component": getattr(args, "component", None) or ""},
            "suggestions": [],
        }
        return structured_response(
            "suggest",
            status="OK",
            message="Все задачи завершены" + filter_hint,
            payload=payload,
            summary="Нет задач для рекомендации",
        )
    priority_map = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}

    def score(t: TaskDetail):
        prog = t.calculate_progress()
        return (-priority_map.get(t.priority, 0), prog, len(t.dependencies))

    sorted_tasks = sorted(active, key=score)
    save_last_task(sorted_tasks[0].id, sorted_tasks[0].domain)
    payload = {
        "filters": {"folder": folder or "", "domain": domain or "", "phase": getattr(args, "phase", None) or "", "component": getattr(args, "component", None) or ""},
        "suggestions": [task_to_dict(task) for task in sorted_tasks[:5]],
    }
    return structured_response(
        "suggest",
        status="OK",
        message="Рекомендации сформированы" + filter_hint,
        payload=payload,
        summary=f"{len(payload['suggestions'])} рекомендаций",
    )


def cmd_quick(args) -> int:
    """Быстрый обзор: топ-3 незавершённых задачи."""
    manager = TaskManager()
    folder = getattr(args, "folder", "") or ""
    domain = derive_domain_explicit(getattr(args, "domain", "") or folder, getattr(args, "phase", None), getattr(args, "component", None))
    tasks = [t for t in manager.list_tasks(domain, skip_sync=True) if t.status != "OK"]
    tasks.sort(key=lambda t: (t.priority, t.calculate_progress()))
    filter_hint = f" (folder='{folder or domain or '-'}', phase='{getattr(args, 'phase', None) or '-'}', component='{getattr(args, 'component', None) or '-'}')"
    if not tasks:
        payload = {
            "filters": {"folder": folder or "", "domain": domain or "", "phase": getattr(args, "phase", None) or "", "component": getattr(args, "component", None) or ""},
            "top": [],
        }
        return structured_response(
            "quick",
            status="OK",
            message="Все задачи выполнены" + filter_hint,
            payload=payload,
            summary="Нет задач",
        )
    top = tasks[:3]
    save_last_task(tasks[0].id, tasks[0].domain)
    payload = {
        "filters": {"folder": folder or "", "domain": domain or "", "phase": getattr(args, "phase", None) or "", "component": getattr(args, "component", None) or ""},
        "top": [task_to_dict(task) for task in top],
    }
    return structured_response(
        "quick",
        status="OK",
        message="Быстрый обзор top-3" + filter_hint,
        payload=payload,
        summary=f"Top-{len(top)} задач",
    )


def _template_subtask_entry(idx: int) -> Dict[str, Any]:
    return {
        "title": f"Результат {idx}: опиши измеримый итог (≥20 символов)",
        "criteria": [
            "Метрики успеха определены и зафиксированы",
            "Доказательства приёмки описаны",
            "Обновлены мониторинг/алерты",
        ],
        "tests": [
            "pytest -q tests/... -k <кейc>",
            "perf или интеграционный прогон",
        ],
        "blockers": [
            "Перечисли approvals/зависимости",
            "Опиши риски и план снятия блокеров",
        ],
    }


def _template_test_matrix() -> List[Dict[str, str]]:
    return [
        {
            "name": "Юнит + интеграция ≥85%",
            "command": "pytest -q --maxfail=1 --cov=src --cov-report=xml",
            "evidence": "coverage.xml ≥85%, отчёт приложен в задачу",
        },
        {
            "name": "Конфигурационный/перф",
            "command": "pytest -q tests/perf -k scenario && python scripts/latency_audit.py",
            "evidence": "p95 ≤ целевой SLO, лог проверки загружен в репозиторий",
        },
        {
            "name": "Регресс + ручная приёмка",
            "command": "pytest -q tests/e2e && ./scripts/manual-checklist.md",
            "evidence": "Чеклист приёмки с таймстемпом и ссылкой на демо",
        },
    ]


def _template_docs_matrix() -> List[Dict[str, str]]:
    return [
        {
            "artifact": "ADR",
            "path": "docs/adr/ADR-<номер>.md",
            "goal": "Зафиксировать выбранную архитектуру и компромиссы hexagonal monolith.",
        },
        {
            "artifact": "Runbook/операционный гайд",
            "path": "docs/runbooks/<feature>.md",
            "goal": "Описать фич-срез, команды запуска и алерты.",
        },
        {
            "artifact": "Changelog/RELNOTES",
            "path": "docs/releases/<date>-<feature>.md",
            "goal": "Протоколировать влияние на пользователей, метрики и тесты.",
        },
    ]


def cmd_template_subtasks(args) -> int:
    count = max(3, args.count)
    template = [_template_subtask_entry(i + 1) for i in range(count)]
    payload = {
        "type": "subtasks",
        "count": count,
        "template": template,
        "tests_template": _template_test_matrix(),
        "documentation_template": _template_docs_matrix(),
        "usage": "apply_task ... --subtasks 'JSON' | --subtasks @file | --subtasks - (всё на русском)",
    }
    return structured_response(
        "template.subtasks",
        status="OK",
        message="Сгенерирован JSON-шаблон подзадач",
        payload=payload,
        summary=f"{count} шаблонов",
    )


# ============================================================================
# Devtools automation helpers
# ============================================================================

AUTOMATION_TMP = Path(".tmp")


def _ensure_tmp_dir() -> Path:
    AUTOMATION_TMP.mkdir(parents=True, exist_ok=True)
    return AUTOMATION_TMP


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _automation_subtask_entry(index: int, coverage: int, risks: str, sla: str) -> Dict[str, Any]:
    return {
        "title": f"Subtask {index}: plan and validate",
        "criteria": [
            f"Coverage ≥{coverage}%",
            f"SLA {sla}",
            "Risks enumerated and mitigations defined",
        ],
        "tests": [
            f"pytest -q --maxfail=1 --cov=. --cov-report=xml (target ≥{coverage}%)",
            "perf/regression suite with evidence in logs",
        ],
        "blockers": [
            "Dependencies and approvals recorded",
            f"Risks: {risks}",
        ],
    }


def _automation_template_payload(count: int, coverage: int, risks: str, sla: str) -> Dict[str, Any]:
    count = max(3, count)
    subtasks = [_automation_subtask_entry(i + 1, coverage, risks, sla) for i in range(count)]
    return {
        "defaults": {"coverage": coverage, "risks": risks, "sla": sla},
        "usage": "apply_task automation task-create \"Title\" --parent TASK-XXX --description \"...\" --subtasks @.tmp/subtasks.template.json",
        "subtasks": subtasks,
    }


def cmd_automation_task_template(args) -> int:
    payload = _automation_template_payload(args.count, args.coverage, args.risks, args.sla)
    output_path = Path(args.output or (AUTOMATION_TMP / "subtasks.template.json"))
    _ensure_tmp_dir()
    _write_json(output_path, payload)
    return structured_response(
        "automation.task-template",
        status="OK",
        message=f"Шаблон сохранён: {output_path}",
        payload={"output": str(output_path.resolve()), "count": len(payload["subtasks"]), "defaults": payload["defaults"]},
        summary=str(output_path),
    )


def _resolve_parent(default_parent: Optional[str]) -> Optional[str]:
    if default_parent:
        return normalize_task_id(default_parent)
    last_id, _ = get_last_task()
    return normalize_task_id(last_id) if last_id else None


def _load_note(log_path: Path, fallback: str) -> str:
    if log_path.exists():
        text = log_path.read_text(encoding="utf-8").strip()
        if text:
            return text[:1000]
    return fallback


def cmd_automation_task_create(args) -> int:
    parent = _resolve_parent(args.parent)
    if not parent:
        return structured_error("automation.task-create", "Не найден parent: укажи --parent или установи .last")
    domain = derive_domain_explicit(getattr(args, "domain", ""), getattr(args, "phase", None), getattr(args, "component", None))
    subtasks_source = args.subtasks or str(AUTOMATION_TMP / "subtasks.template.json")
    subtasks_path = Path(subtasks_source[1:]) if subtasks_source.startswith("@") else Path(subtasks_source)
    if subtasks_source.startswith("@") or subtasks_path.exists():
        if not subtasks_path.exists():
            # автоформирование дефолтного шаблона
            payload = _automation_template_payload(args.count, args.coverage, args.risks, args.sla)
            _ensure_tmp_dir()
            _write_json(subtasks_path, payload)
        resolved_path = subtasks_path
        try:
            payload = json.loads(subtasks_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and isinstance(payload.get("subtasks"), list):
                _ensure_tmp_dir()
                resolved_path = subtasks_path.parent / "subtasks.resolved.json" if subtasks_path.is_file() else (AUTOMATION_TMP / "subtasks.resolved.json")
                _write_json(resolved_path, payload["subtasks"])
        except Exception:
            resolved_path = subtasks_path
        subtasks_arg = f"@{resolved_path}"
    else:
        subtasks_arg = subtasks_source
    desc = args.description or args.title
    create_args = argparse.Namespace(
        title=args.title,
        status=args.status,
        priority=args.priority,
        parent=parent,
        description=desc,
        context=args.context,
        tags=args.tags,
        subtasks=subtasks_arg,
        dependencies=None,
        next_steps=None,
        tests=args.tests,
        risks=args.risks,
        validate_only=not args.apply,
        domain=domain,
        phase=args.phase,
        component=args.component,
    )
    return cmd_create(create_args)


def cmd_automation_projects_health(args) -> int:
    payload = _projects_status_payload(force_refresh=True)
    summary = f"target={payload.get('target_label','—')} auto-sync={str(payload.get('auto_sync')).lower()} token={'yes' if payload.get('token_present') else 'no'} rate={payload.get('rate_remaining')}/{payload.get('rate_reset_human') or '-'}"
    return structured_response(
        "automation.projects-health",
        status="OK",
        message=payload.get("status_reason", "") or "Projects status",
        payload=payload,
        summary=summary,
    )


def cmd_automation_health(args) -> int:
    _ensure_tmp_dir()
    log_path = Path(args.log or (AUTOMATION_TMP / "health.log"))
    pytest_cmd = args.pytest_cmd.strip()
    result = {"pytest_cmd": pytest_cmd, "rc": 0, "stdout": "", "stderr": ""}
    if pytest_cmd:
        try:
            proc = subprocess.run(shlex.split(pytest_cmd), capture_output=True, text=True)
            result["rc"] = proc.returncode
            result["stdout"] = (proc.stdout or "").strip()
            result["stderr"] = (proc.stderr or "").strip()
        except FileNotFoundError as exc:
            result["rc"] = 1
            result["stderr"] = str(exc)
    _write_json(log_path, result)
    status = "OK" if result["rc"] == 0 else "ERROR"
    return structured_response(
        "automation.health",
        status=status,
        message="pytest выполнен" if pytest_cmd else "pytest пропущен",
        payload={"log": str(log_path.resolve()), **result},
        summary=f"log={log_path} rc={result['rc']}",
        exit_code=0 if status == "OK" else 1,
    )


def cmd_automation_checkpoint(args) -> int:
    try:
        task_id, domain = resolve_task_reference(args.task_id, getattr(args, "domain", None), getattr(args, "phase", None), getattr(args, "component", None))
    except ValueError as exc:
        return structured_error("automation.checkpoint", str(exc))
    manager = TaskManager()
    log_path = Path(args.log or (AUTOMATION_TMP / "checkpoint.log"))
    note = args.note or _load_note(log_path, f"log missing: {log_path}")
    payload: Dict[str, Any] = {"task_id": task_id, "index": args.index, "note": note}
    if args.mode == "note":
        ok, msg = manager.update_subtask_checkpoint(task_id, args.index, args.checkpoint, True, note, domain)
        if not ok:
            return structured_error("automation.checkpoint", msg or "Не удалось записать чекпоинт", payload=payload)
        detail = manager.load_task(task_id, domain)
        if detail:
            payload["task"] = task_to_dict(detail, include_subtasks=True)
        return structured_response(
            "automation.checkpoint.note",
            status="OK",
            message=f"Checkpoint {args.checkpoint} обновлён",
            payload=payload,
            summary=f"{task_id}#{args.index} {args.checkpoint}",
        )

    for checkpoint in ("criteria", "tests", "blockers"):
        ok, msg = manager.update_subtask_checkpoint(task_id, args.index, checkpoint, True, note, domain)
        if not ok:
            return structured_error("automation.checkpoint", msg or "Не удалось подтвердить чекпоинты", payload=payload)
    ok, msg = manager.set_subtask(task_id, args.index, True, domain)
    if not ok:
        return structured_error("automation.checkpoint", msg or "Не удалось закрыть подзадачу", payload=payload)
    detail = manager.load_task(task_id, domain)
    save_last_task(task_id, domain)
    if detail:
        payload["task"] = task_to_dict(detail, include_subtasks=True)
    return structured_response(
        "automation.checkpoint",
        status="OK",
        message="Подзадача закрыта через automation",
        payload=payload,
        summary=f"{task_id}#{args.index} ok",
    )


# ============================================================================
# CLI
# ============================================================================


def build_parser() -> argparse.ArgumentParser:
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
    tui_p.add_argument("--theme", choices=list(THEMES.keys()), default=DEFAULT_THEME, help="палитра интерфейса")
    tui_p.add_argument("--mono-select", action="store_true", help="использовать монохромное выделение строк")
    tui_p.set_defaults(func=cmd_tui)

    # list
    lp = sub.add_parser("list", help="Список задач")
    lp.add_argument("--status", choices=["OK", "WARN", "FAIL"])
    lp.add_argument("--progress", action="store_true")
    add_context_args(lp)
    lp.set_defaults(func=cmd_list)

    # show
    sp = sub.add_parser("show", help="Показать задачу")
    sp.add_argument("task_id", nargs="?")
    add_context_args(sp)
    sp.set_defaults(func=cmd_show)

    # create
    cp = sub.add_parser("create", help="Создать задачу")
    cp.add_argument("title")
    cp.add_argument("--status", default="FAIL", choices=["OK", "WARN", "FAIL"])
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
    cp.add_argument("--next-steps", "-n")
    cp.add_argument("--tests", required=True)
    cp.add_argument("--risks", help="semicolon-separated risks", required=True)
    cp.add_argument("--validate-only", action="store_true", help="Проверить payload без записи задачи")
    add_context_args(cp)
    cp.set_defaults(func=cmd_create)

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
    tp.add_argument("--status", default="FAIL", choices=["OK", "WARN", "FAIL"])
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
    tp.add_argument("--next-steps", "-n")
    tp.add_argument("--tests", required=True)
    tp.add_argument("--risks", help="semicolon-separated risks", required=True)
    tp.add_argument("--validate-only", action="store_true", help="Проверить payload без записи задачи")
    add_context_args(tp)
    tp.set_defaults(func=cmd_smart_create)

    # guided (interactive)
    gp = sub.add_parser("guided", help="Интерактивное создание (шаг-ответ-шаг)")
    add_context_args(gp)
    gp.set_defaults(func=cmd_create_guided)

    # update
    up = sub.add_parser(
        "update",
        help="Обновить статус задачи",
        description=(
            "Обновляет статус задачи на OK/WARN/FAIL.\n"
            "Вызовы поддерживают оба порядка: `update TASK-005 OK` или `update OK TASK-005`.\n"
            "Перед переводом в OK убедись, что все подзадачи закрыты и есть доказательства тестов."
        ),
    )
    up.add_argument("arg1")
    up.add_argument("arg2", nargs="?")
    add_context_args(up)
    up.set_defaults(func=cmd_update)

    # analyze
    ap = sub.add_parser("analyze", help="Анализ задачи")
    ap.add_argument("task_id")
    add_context_args(ap)
    ap.set_defaults(func=cmd_analyze)

    # next
    np = sub.add_parser("next", help="Следующая задача")
    add_context_args(np)
    np.set_defaults(func=cmd_next)

    # add-subtask
    asp = sub.add_parser("add-subtask", help="Добавить подзадачу")
    asp.add_argument("task_id")
    asp.add_argument("subtask")
    asp.add_argument("--criteria", required=True, help="Критерии выполнения (через ';')")
    asp.add_argument("--tests", required=True, help="Тесты/проверки (через ';')")
    asp.add_argument("--blockers", required=True, help="Блокеры/зависимости (через ';')")
    add_context_args(asp)
    asp.set_defaults(func=cmd_add_subtask)

    # add-dependency
    adp = sub.add_parser("add-dep", help="Добавить зависимость")
    adp.add_argument("task_id")
    adp.add_argument("dependency")
    add_context_args(adp)
    adp.set_defaults(func=cmd_add_dependency)

    # ok macro
    okp = sub.add_parser("ok", help="Закрыть подзадачу одним махом (criteria/tests/blockers+done)")
    okp.add_argument("task_id")
    okp.add_argument("index", type=int)
    okp.add_argument("--criteria-note")
    okp.add_argument("--tests-note")
    okp.add_argument("--blockers-note")
    okp.add_argument("--path", help="Путь подзадачи (0.1.2) вместо индекса")
    add_context_args(okp)
    okp.set_defaults(func=cmd_ok)

    # alias: apply_task sub ok
    subok = sub.add_parser(
        "sub",
        help="Группа алиасов для подзадач; sub ok == ok",
        description="Короткий алиас: `apply_task sub ok TASK IDX [--criteria-note ... --tests-note ... --blockers-note ...]`",
    )
    subok_sub = subok.add_subparsers(dest="subcommand", required=True)
    subok_ok = subok_sub.add_parser("ok", help="Подтвердить критерии/тесты/блокеры и закрыть подзадачу (алиас ok)")
    subok_ok.add_argument("task_id")
    subok_ok.add_argument("index", type=int)
    subok_ok.add_argument("--criteria-note")
    subok_ok.add_argument("--tests-note")
    subok_ok.add_argument("--blockers-note")
    subok_ok.add_argument("--path", help="Путь подзадачи (0.1.2) вместо индекса")
    add_context_args(subok_ok)
    subok_ok.set_defaults(func=cmd_ok)

    # note macro
    notep = sub.add_parser("note", help="Добавить заметку/подтверждение к чекпоинту")
    notep.add_argument("task_id")
    notep.add_argument("index", type=int)
    notep.add_argument("--checkpoint", choices=["criteria", "tests", "blockers"], required=True)
    notep.add_argument("--note", required=True)
    notep.add_argument("--undo", action="store_true", help="сбросить подтверждение вместо установки")
    notep.add_argument("--path", help="Путь подзадачи (0.1.2) вместо индекса")
    add_context_args(notep)
    notep.set_defaults(func=cmd_note)

    # bulk macro
    blp = sub.add_parser("bulk", help="Выполнить набор чекпоинтов из JSON payload")
    blp.add_argument("--input", "-i", default="-", help="Источник JSON (строка, @file, '-'=STDIN)")
    blp.add_argument("--task", help="task_id по умолчанию для операций без поля task (используй '.'/last для .last)")
    add_context_args(blp)
    blp.set_defaults(func=cmd_bulk)

    webhook = sub.add_parser("projects-webhook", help="Обработать payload GitHub Projects")
    webhook.add_argument("--payload", default="-", help="JSON payload ('-' для STDIN)")
    webhook.add_argument("--signature", help="Значение заголовка X-Hub-Signature-256")
    webhook.add_argument("--secret", help="Shared secret для проверки подписи")
    webhook.set_defaults(func=cmd_projects_webhook)

    webhook_srv = sub.add_parser("projects-webhook-serve", help="HTTP-сервер для GitHub Projects webhook")
    webhook_srv.add_argument("--host", default="0.0.0.0")
    webhook_srv.add_argument("--port", type=int, default=8787)
    webhook_srv.add_argument("--secret", help="Shared secret для проверки подписи")
    webhook_srv.set_defaults(func=cmd_projects_webhook_serve)

    auth = sub.add_parser("projects-auth", help="Сохранить GitHub PAT для Projects sync")
    auth.add_argument("--token", help="PAT со scope project")
    auth.add_argument("--unset", action="store_true", help="Удалить сохранённый PAT")
    auth.set_defaults(func=cmd_projects_auth)

    projects = sub.add_parser("projects", help="Операции с GitHub Projects v2")
    proj_sub = projects.add_subparsers(dest="projects_command")
    proj_sub.required = True
    sync_cmd = proj_sub.add_parser("sync", help="Синхронизировать backlog с Projects v2")
    sync_cmd.add_argument("--all", action="store_true", help="Подтвердить синхронизацию всех задач")
    add_context_args(sync_cmd)
    sync_cmd.set_defaults(func=cmd_projects_sync_cli)
    status_cmd = proj_sub.add_parser("status", help="Показать текущее состояние Projects sync")
    status_cmd.set_defaults(func=cmd_projects_status)
    status_set_cmd = proj_sub.add_parser("status-set", help="Установить статус задачи (OK/WARN/FAIL) — единообразно с TUI")
    status_set_cmd.add_argument("task_id", help="TASK-xxx")
    status_set_cmd.add_argument("status", choices=["OK", "WARN", "FAIL"])
    add_domain_arg(status_set_cmd)
    status_set_cmd.set_defaults(func=cmd_status_set)
    autosync_cmd = proj_sub.add_parser("autosync", help="Включить или выключить auto_sync без редактирования конфигов")
    autosync_cmd.add_argument("state", choices=["on", "off"], help="on/off")
    autosync_cmd.set_defaults(func=cmd_projects_autosync)
    workers_cmd = proj_sub.add_parser("workers", help="Задать размер пула sync (0=auto)")
    workers_cmd.add_argument("count", type=int, help="Количество потоков (0=auto)")
    workers_cmd.set_defaults(func=cmd_projects_workers)

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
    ckp.set_defaults(func=cmd_checkpoint)

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
    stp.set_defaults(func=cmd_subtask)

    # move
    mv = sub.add_parser("move", help="Переместить задачу(и) в подпапку .tasks")
    mv.add_argument("task_id", nargs="?")
    mv.add_argument("--glob", help="glob-шаблон внутри .tasks (пример: 'phase1/*.task')")
    mv.add_argument("--to", required=True, help="целевая подпапка")
    add_domain_arg(mv)
    mv.set_defaults(func=cmd_move)

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
    add_domain_arg(ep)
    ep.set_defaults(func=cmd_edit)

    # clean
    cl = sub.add_parser("clean", help="Удалить задачи по фильтрам")
    cl.add_argument("--tag", help="тег без #")
    cl.add_argument("--status", choices=["OK", "WARN", "FAIL"], help="фильтр по статусу")
    cl.add_argument("--phase", help="фаза/итерация")
    cl.add_argument("--glob", help="glob-шаблон (.tasks relative), например 'phase1/*.task'")
    cl.add_argument("--dry-run", action="store_true", help="только показать задачи без удаления")
    cl.set_defaults(func=cmd_clean)

    # lint
    lp2 = sub.add_parser("lint", help="Проверка .tasks")
    lp2.add_argument("--fix", action="store_true")
    lp2.set_defaults(func=cmd_lint)

    # suggest
    sg = sub.add_parser("suggest", help="Рекомендовать задачи")
    add_context_args(sg)
    sg.set_defaults(func=cmd_suggest)

    # quick
    qp = sub.add_parser("quick", help="Быстрый обзор top-3")
    add_context_args(qp)
    qp.set_defaults(func=cmd_quick)

    # template
    tmp = sub.add_parser("template", help="Генерация шаблонов для автоматизации")
    tmp_sub = tmp.add_subparsers(dest="template_command")
    tmp_sub.required = True
    subt = tmp_sub.add_parser("subtasks", help="Создать JSON с заготовками подзадач")
    subt.add_argument("--count", type=int, default=3, help="Количество подзадач (>=3)")
    subt.set_defaults(func=cmd_template_subtasks)

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
    auto_tmpl.set_defaults(func=cmd_automation_task_template)

    auto_create = auto_sub.add_parser("task-create", help="Обёртка над create с дефолтами и автогенерацией шаблона")
    auto_create.add_argument("title")
    auto_create.add_argument("--parent", help="Если не задан, возьмём .last")
    auto_create.add_argument("--description", "-d", help="По умолчанию совпадает с title")
    auto_create.add_argument("--tests", default="pytest -q")
    auto_create.add_argument("--risks", default="perf;deps")
    auto_create.add_argument("--count", type=int, default=3, help="count для автогенерации шаблона")
    auto_create.add_argument("--coverage", type=int, default=85)
    auto_create.add_argument("--sla", default="p95<=200ms")
    auto_create.add_argument("--subtasks", default=str(AUTOMATION_TMP / "subtasks.template.json"))
    auto_create.add_argument("--status", default="FAIL", choices=["OK", "WARN", "FAIL"])
    auto_create.add_argument("--priority", default="MEDIUM", choices=["LOW", "MEDIUM", "HIGH"])
    auto_create.add_argument("--context")
    auto_create.add_argument("--tags")
    auto_create.add_argument("--apply", action="store_true", help="Создавать задачу вместо validate-only")
    add_context_args(auto_create)
    auto_create.set_defaults(func=cmd_automation_task_create)

    auto_health = auto_sub.add_parser("health", help="Сводная проверка: pytest + лог в .tmp")
    auto_health.add_argument("--pytest-cmd", default="pytest -q")
    auto_health.add_argument("--log", help="Куда писать лог (default: .tmp/health.log)")
    auto_health.set_defaults(func=cmd_automation_health)

    auto_proj = auto_sub.add_parser("projects-health", help="Короткий статус GitHub Projects")
    auto_proj.set_defaults(func=cmd_automation_projects_health)

    auto_ckp = auto_sub.add_parser("checkpoint", help="Быстрое подтверждение чекпоинтов/подзадачи")
    auto_ckp.add_argument("task_id", help="TASK-ID или '.' для последней")
    auto_ckp.add_argument("index", type=int)
    auto_ckp.add_argument("--mode", choices=["ok", "note"], default="ok")
    auto_ckp.add_argument("--checkpoint", choices=["criteria", "tests", "blockers"], default="tests", help="для mode=note")
    auto_ckp.add_argument("--note", help="Явная нота для чекпоинта")
    auto_ckp.add_argument("--log", help="Файл для подтягивания ноты (default: .tmp/checkpoint.log)")
    add_context_args(auto_ckp)
    auto_ckp.set_defaults(func=cmd_automation_checkpoint)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not getattr(args, "command", None):
        parser.print_help()
        return 1
    if args.command == "help":
        parser.print_help()
        print("\nКонтекст: --domain или phase/component формируют путь; .last хранит TASK@domain.")
        print("\nПравила для ИИ-агентов:\n")
        print(AI_HELP.strip())
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
