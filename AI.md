# apply_task — operator rules

- Work only through `apply_task`; `.tasks/` править вручную нельзя. Контекст хранится в `.last` как `TASK@domain`.
- Каждая задача: `--parent --description --tests --risks --subtasks`; минимум 3 подзадачи, ≥20 символов, с критериями/тестами/блокерами. Подтверждение чекпоинтов только с нотами (`apply_task ok/note/bulk`).
- Домены обязательны (`--domain/-F` или `--phase/--component`); раскладка слоёв см. [DOMAIN_STRUCTURE.md](DOMAIN_STRUCTURE.md).
- Качество: покрытие изменённого кода ≥85%, цикломатическая сложность ≤10, без моков/заглушек в проде, один файл — одна ответственность.
- Статусы: FAIL → WARN → OK; `done` доступен только если все подзадачи закрыты.

## Subtasks input
- `--subtasks @file.json` или `--subtasks -` (STDIN), формат — JSON-массив объектов с `title/criteria/tests/blockers`.
- `apply_task template subtasks --count N` — генерация валидного каркаса.

## GitHub Projects
- Конфиг: `.apply_task_projects.yaml`; токен `APPLY_TASK_GITHUB_TOKEN|GITHUB_TOKEN`.
- Без токена sync отключён, CLI работает оффлайн; метаданные задач хранят `project_*` поля.

## Checks
- Перед сдачей: `pytest -q` (минимум покрытие диффа 85%), при работе с задачами — `./apply_task list` для контроля контекста.
