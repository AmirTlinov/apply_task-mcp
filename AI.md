# apply_task — жёсткие правила для ИИ-агентов

1) Работай только через `apply_task`. Не правь `.tasks/` руками. Держи контекст через `.last` (`TASK@domain`).

2) Декомпозируй требование: один корень-задача + иерархия подзадач (вложенность без ограничений). Каждая подзадача (любого уровня) обязана иметь:
   - `title` ≥ 20 символов, атомарная.
   - `success_criteria` (конкретные проверяемые пункты).
   - `tests` (команды/сuites/данные, как проверять).
   - `blockers` (зависимости, риски, допуски).
   - Чекпоинты отмечаются только через `ok/note/bulk --path`.

3) Создание задач (повелительный сценарий):
   - Вычисли домен (`--domain/-F` обязателен; слои см. [DOMAIN_STRUCTURE.md](DOMAIN_STRUCTURE.md)).
   - Сгенерируй каркас подзадач: `apply_task template subtasks --count N > .tmp/subtasks.json`, дополни критерии/тесты/блокеры.
   - Создай задачу:  
     `apply_task create "Title #tags" --domain <d> --description "<what/why/acceptance>" --tests "<proj tests>" --risks "<proj risks>" --subtasks @.tmp/subtasks.json`
   - Для вложенных уровней используй `apply_task subtask TASK --add "<title>" --criteria "...;..." --tests "...;..." --blockers "...;..." --parent-path 0.1` (индексация 0-базовая, путь вида `0.1.2`).

4) Ведение подзадач:
   - Добавление: `apply_task subtask TASK --add "<title>" ... [--parent-path X.Y]`.
   - Чекпоинты: `apply_task ok TASK --path X.Y --criteria --note "evidence"` (аналогично `--tests/--blockers`).
   - Завершение подзадачи: только если все чекпоинты в OK, `apply_task subtask TASK --done --path X.Y`.
   - Отметки: `apply_task note TASK --path X.Y --note "what changed"`; использовать для фиксации прогресса.

5) Статусы задачи:
   - Начинай с `fail` (backlog), переводи в `warn` только после старта работ, в `ok` — когда все подзадачи завершены.
   - Команды: `apply_task start/done/fail TASK`.

6) Качество реализации (обязательные рамки для всего кода):
   - Покрытие диффа ≥ 85%, цикломатическая сложность ≤ 10, без моков/заглушек в проде.
   - Один файл — одна ответственность; не превышай ~300 строк без причины.
   - Перед сдачей: `pytest -q`; фиксируй ноты с перечислением пройденных тестов.

7) GitHub Projects (если нужен sync):
   - Конфиг `.apply_task_projects.yaml`, токен `APPLY_TASK_GITHUB_TOKEN|GITHUB_TOKEN`.
   - Если нет токена или remote, sync отключён, CLI продолжает работать оффлайн.

Запомни: любое действие — через CLI, с явными критериями/тестами/блокерами на каждом узле дерева. Нет чекпоинтов = нет done.***
