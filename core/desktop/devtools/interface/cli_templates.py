#!/usr/bin/env python3
"""Template CLI commands."""

import argparse
from typing import Dict, Any, List

from core.desktop.devtools.interface.cli_io import structured_response


def _template_subtask_entry(idx: int) -> Dict[str, Any]:
    """Create template subtask entry."""
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
    """Create test matrix template."""
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
    """Create documentation matrix template."""
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


def cmd_template_subtasks(args: argparse.Namespace) -> int:
    """Generate subtasks template."""
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
