# AGENTS playbook

- **Workflow**: веди работу только через `apply_task`; задачи атомарные (<1 день), все чекпоинты фиксируй нотами; `pytest -q` перед сдачей.
- **Коммуникация**: пользователям отвечай по-русски, код/доки — по-английски и по делу (без воды).
- **Архитектура**: hexagonal монолит с вертикальными слайсами; домен = папка `domain/feature`; код раскладывай по слоям `application/domain/infrastructure/interface`. См. `DOMAIN_STRUCTURE.md`.
- **Качество**: покрытие диффа ≥85%, цикломатическая сложность ≤10, без моков/заглушек в проде; один файл — одна ответственность; коммиты в формате Conventional Commits.
- **GitHub Projects**: конфиг `.apply_task_projects.yaml`, токен `APPLY_TASK_GITHUB_TOKEN|GITHUB_TOKEN`; без токена sync отключён, CLI работает оффлайн.
- **Devtools automation** (`automation`): `task-template` → `.tmp/subtasks.template.json`, `task-create` (validate-only по умолчанию, автоген шаблона), `checkpoint` (ноты/ок через лог), `health` (pytest → `.tmp/health.log`), `projects-health` (короткий статус sync).

## Aliases
- `README.md` — что за инструмент и как стартовать.
- `DOMAIN_STRUCTURE.md` — схема доменов и слоёв.
- `SYNTAX.md` — форматы CLI/JSON, обязательные поля.
- `CHANGES.md` — история UX/фич-сдвигов.
- `apply_task help-ai` — правила работы с CLI и чекпоинтами (для ИИ-агентов).
