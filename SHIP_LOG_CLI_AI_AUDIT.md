# SHIP LOG: cli_ai.py Audit & Fixes

**Дата:** 2025-11-25
**Задача:** Аудит и исправление модуля cli_ai.py
**Статус:** ⚠️ REQUEST_CHANGES - требуются исправления перед мержем

---

## EXECUTIVE SUMMARY

Проведен комплексный аудит модуля `cli_ai.py` - AI-first интерфейса для task management.

**Результаты:**
- ✅ Архитектура: Отличная (9/10)
- ❌ Безопасность: Критические уязвимости (4/10)
- ⚠️ Покрытие тестами: Недостаточное (65%, целевое 90%)
- ✅ Производительность: Приемлемая (~3ms response)

**Общая оценка:** 7.5/10

---

## ЧТО БЫЛО СДЕЛАНО

### 1. Статический анализ
- ✅ Ruff (линтер) - найдено 7 проблем
- ✅ MyPy (опционально) - обнаружены type issues (не блокеры)
- ✅ Анализ сложности кода (18 функций, макс 115 строк)

### 2. Динамическое тестирование
- ✅ Запуск 38 unit tests - все PASSED
- ✅ Coverage analysis - 65% (323 statements, 113 missed)
- ✅ Security testing (injection scenarios)
- ✅ Performance testing (100 subtasks: 1.89ms context, 0.92ms suggestions)

### 3. Аудит безопасности
- ❌ Path traversal уязвимость (task_id не валидируется)
- ❌ DoS через большие входные данные (нет лимитов)
- ⚠️ Потенциальная shell injection в write_activity_marker

### 4. Аудит API контрактов
- ✅ Корректное использование TaskManager API
- ⚠️ Неполная обработка кодов ошибок
- ⚠️ Отсутствует domain routing в некоторых handlers

---

## КРИТИЧЕСКИЕ ПРОБЛЕМЫ (BLOCKER)

### SEC-001: Path Traversal в task_id
**Файлы:** `cli_ai.py:322, 371, 454, 529, 614, 669`

**Что делать:**
1. Создать функцию `_validate_task_id(task_id: str) -> bool`
2. Проверять формат: `^TASK-\d{3,6}$`
3. Запрещать `../`, `./`, `\`, `/` в task_id
4. Добавить во все handlers проверку:
   ```python
   if task_id and not _validate_task_id(task_id):
       return error_response(intent, "INVALID_TASK_ID", ...)
   ```

**Тесты:**
```python
def test_rejects_path_traversal_in_task_id():
    resp = process_intent(manager, {
        "intent": "context",
        "task": "../../etc/passwd"
    })
    assert not resp.success
    assert resp.error_code == "INVALID_TASK_ID"
```

**Приоритет:** P0 (исправить немедленно)

---

### SEC-002: DoS через неограниченный размер входных данных
**Файлы:** `cli_ai.py:374, 947-949`

**Что делать:**
1. Добавить константы лимитов:
   ```python
   MAX_JSON_SIZE = 10 * 1024 * 1024  # 10MB
   MAX_SUBTASKS = 1000
   MAX_ARRAY_ITEMS = 1000
   MAX_STRING_LENGTH = 10000
   MAX_NESTING_DEPTH = 10
   ```

2. Создать `_validate_input_size(data: Dict) -> Tuple[bool, Optional[str]]`
3. Проверять в `cmd_ai` перед парсингом JSON
4. Проверять в `handle_decompose` размер subtasks
5. Проверять в `_parse_path` глубину вложенности

**Тесты:**
```python
def test_rejects_huge_json():
    huge_json = '{"intent": "create", "title": "' + 'A' * 20_000_000 + '"}'
    # Should reject before parsing

def test_rejects_too_many_subtasks():
    resp = process_intent(manager, {
        "intent": "decompose",
        "task": "TASK-001",
        "subtasks": [{"title": "x", ...}] * 10000
    })
    assert not resp.success
    assert "too many" in resp.error_message.lower()
```

**Приоритет:** P0 (исправить немедленно)

---

### SEC-003: Проверить write_activity_marker на shell injection
**Файлы:** `cli_ai.py:414, 484, 574, 637, 707, 858`

**Что делать:**
1. Проверить реализацию `core/desktop/devtools/interface/cli_activity.py`
2. Убедиться, что не используются shell команды или параметры не эскейпированы
3. Если используется subprocess - только с `shell=False`
4. Санитизировать пути перед передачей

**Приоритет:** P0 (проверить немедленно)

---

## СЕРЬЕЗНЫЕ ПРОБЛЕМЫ (MAJOR)

### COR-001: Неполная валидация path
**Файл:** `cli_ai.py:621-626`

**Что делать:**
Создать robust функцию `_parse_path`:
```python
def _parse_path(path: str) -> Tuple[Optional[int], Optional[str], Optional[str]]:
    """Parse and validate subtask path."""
    MAX_NESTING_DEPTH = 10
    MAX_PATH_INDEX = 10000

    if not path or not isinstance(path, str):
        return None, None, "Path must be non-empty string"

    parts = path.split(".")
    if len(parts) > MAX_NESTING_DEPTH:
        return None, None, f"Path too deep: {len(parts)} > {MAX_NESTING_DEPTH}"

    try:
        indices = [int(p) for p in parts]
    except ValueError:
        return None, None, f"Invalid path format: {path}"

    for idx in indices:
        if idx < 0 or idx > MAX_PATH_INDEX:
            return None, None, f"Index out of range: {idx}"

    index = indices[0]
    nested_path = ".".join(str(i) for i in indices[1:]) if len(indices) > 1 else None

    return index, nested_path, None
```

**Тесты:**
```python
def test_parse_path_rejects_deep_nesting():
    path = ".".join(str(i) for i in range(100))
    index, nested, error = _parse_path(path)
    assert error is not None
    assert "too deep" in error.lower()
```

**Приоритет:** P1

---

### COR-002: Неконсистентная обработка ошибок в handle_decompose
**Файл:** `cli_ai.py:383-411`

**Что делать:**
1. Не пропускать молча ошибки
2. Валидировать `title`, `criteria`, `tests`, `blockers` ПЕРЕД вызовом API
3. Возвращать массив `errors` в результате
4. Устанавливать `error_code="PARTIAL_FAILURE"` если есть ошибки

**Код:**
```python
created = []
errors = []

for idx, st_data in enumerate(subtasks_data):
    title = st_data.get("title", "")
    if not title:
        errors.append({"index": idx, "error": "Missing title"})
        continue

    criteria = st_data.get("criteria", [])
    tests = st_data.get("tests", [])
    blockers = st_data.get("blockers", [])

    # Валидация
    if not criteria:
        errors.append({"index": idx, "title": title, "error": "Missing criteria"})
        continue
    if not tests:
        errors.append({"index": idx, "title": title, "error": "Missing tests"})
        continue
    if not blockers:
        errors.append({"index": idx, "title": title, "error": "Missing blockers"})
        continue

    success, error = manager.add_subtask(...)
    if not success:
        errors.append({"index": idx, "title": title, "error": error})
    else:
        created.append(...)

return AIResponse(
    success=len(errors) == 0,
    intent="decompose",
    result={
        "created": created,
        "total_created": len(created),
        "errors": errors,
    },
    context=ctx,
    suggestions=suggestions,
    error_code="PARTIAL_FAILURE" if errors else None,
    error_message=f"{len(errors)} subtasks failed" if errors else None,
)
```

**Приоритет:** P1

---

### TST-001: Повысить покрытие тестами до 90%
**Файл:** `tests/test_cli_ai_unit.py`

**Непокрытые сценарии (113 missed lines):**

1. **_build_subtasks_tree с children** (строка 179):
```python
def test_build_subtasks_tree_with_nested_children():
    parent_st = SubTask(...)
    child_st = SubTask(...)
    parent_st.children = [child_st]

    tree = _build_subtasks_tree([parent_st])
    assert len(tree) == 1
    assert len(tree[0]["children"]) == 1
```

2. **generate_suggestions для разных состояний** (200-295):
```python
def test_suggestions_for_unverified_criteria():
    # Подзадача completed, criteria не confirmed
    ...

def test_suggestions_for_ready_to_complete():
    # Все чекпоинты OK, но not completed
    ...
```

3. **handle_define, handle_verify, handle_progress - happy paths** (464-647):
```python
def test_handle_define_updates_criteria():
    # Создать задачу с подзадачей
    # Обновить criteria через handle_define
    # Проверить что сохранилось

def test_handle_verify_confirms_checkpoints():
    # Verify criteria, tests, blockers
    # Проверить что флаги установлены

def test_handle_progress_with_nested_path():
    # Progress для вложенной подзадачи "0.1.2"
```

4. **handle_complete validation errors** (682-701):
```python
def test_complete_rejects_incomplete_subtasks():
    resp = handle_complete(manager, {
        "intent": "complete",
        "task": task_id,  # task with incomplete subtasks
    })
    assert not resp.success
    assert resp.error_code == "INCOMPLETE_SUBTASKS"

def test_complete_rejects_unverified_criteria():
    # Task с completed subtasks, но criteria не confirmed
```

5. **cmd_ai - CLI entry point** (945-1002):
```python
def test_cmd_ai_reads_from_stdin(monkeypatch, capsys):
    import io
    monkeypatch.setattr('sys.stdin', io.StringIO('{"intent": "context"}'))
    monkeypatch.setattr('sys.stdin.isatty', lambda: False)

    args = SimpleNamespace(json_input=None, tasks_dir=".tasks", ...)
    result = cmd_ai(args)

    assert result == 0
    captured = capsys.readouterr()
    assert "success" in captured.out
```

6. **Exception handling в process_intent** (933-934):
```python
def test_process_intent_handles_exception_in_handler(monkeypatch):
    def raising_handler(manager, data):
        raise ValueError("Test exception")

    monkeypatch.setitem(INTENT_HANDLERS, "test", raising_handler)

    resp = process_intent(manager, {"intent": "test"})
    assert not resp.success
    assert resp.error_code == "INTERNAL_ERROR"
```

**Целевое покрытие:** 90% (290+ statements)

**Приоритет:** P1

---

### PER-001: Добавить rate limiting
**Файл:** `cli_ai.py:942`

**Что делать:**
```python
from collections import defaultdict
from time import time

_rate_limits = defaultdict(list)
MAX_REQUESTS_PER_MINUTE = 60

def _check_rate_limit(client_id: str = "default") -> Tuple[bool, Optional[str]]:
    now = time()
    window_start = now - 60

    _rate_limits[client_id] = [ts for ts in _rate_limits[client_id] if ts > window_start]

    if len(_rate_limits[client_id]) >= MAX_REQUESTS_PER_MINUTE:
        return False, f"Rate limit exceeded: {MAX_REQUESTS_PER_MINUTE}/min"

    _rate_limits[client_id].append(now)
    return True, None

# В cmd_ai:
ok, err = _check_rate_limit()
if not ok:
    response = error_response("rate_limit", "RATE_LIMIT", err)
    print(response.to_json())
    return 429
```

**Приоритет:** P1

---

### DX-001: Улучшить коды ошибок от TaskManager
**Файлы:** `cli_ai.py:393-400, 628-635`

**Что делать:**
```python
ERROR_MAPPING = {
    "not_found": ("TASK_NOT_FOUND", "Задача не найдена"),
    "missing_fields": ("MISSING_REQUIRED_FIELDS", "Отсутствуют обязательные поля"),
    "path": ("INVALID_PARENT_PATH", "Некорректный путь родительской подзадачи"),
}

success, error = manager.add_subtask(...)
if not success:
    code, message = ERROR_MAPPING.get(error, ("UNKNOWN_ERROR", f"Ошибка: {error}"))
    return error_response("decompose", code, message)
```

**Приоритет:** P1

---

## РЕКОМЕНДАЦИИ ПО УЛУЧШЕНИЮ (MINOR)

### MIN-001 - MIN-008

См. полный отчет в CODE_REVIEW.md, секция "РЕКОМЕНДАЦИИ ПО УЛУЧШЕНИЮ".

**Приоритет:** P2-P3 (не блокирует мерж)

---

## ПЛАН ИСПРАВЛЕНИЙ

### Фаза 1: Критические проблемы безопасности (P0)
**ETA:** 2-3 часа

1. [ ] SEC-001: Валидация task_id
   - Создать `_validate_task_id()`
   - Добавить проверки во все handlers
   - Написать тесты (5 сценариев)

2. [ ] SEC-002: Лимиты на размер данных
   - Добавить константы лимитов
   - Создать `_validate_input_size()`
   - Ограничить размер JSON в cmd_ai
   - Написать тесты (3 сценария)

3. [ ] SEC-003: Аудит write_activity_marker
   - Проверить реализацию
   - Санитизировать параметры если нужно

**Критерий завершения:**
- Все security тесты PASSED
- Ruff/mypy без критических ошибок
- Security audit CLEAN

---

### Фаза 2: Серьезные проблемы (P1)
**ETA:** 4-6 часов

1. [ ] COR-001: Улучшить валидацию path
   - Создать `_parse_path()`
   - Заменить в handle_progress, handle_define, handle_verify
   - Тесты (5 сценариев)

2. [ ] COR-002: Обработка ошибок в handle_decompose
   - Валидировать входные данные
   - Возвращать errors в результате
   - Тесты (3 сценария)

3. [ ] TST-001: Повысить покрытие до 90%
   - Добавить 25+ тестов для непокрытых сценариев
   - Интеграционные тесты (full workflow)
   - Property-based тесты (Hypothesis)

4. [ ] PER-001: Rate limiting
   - Реализовать _check_rate_limit()
   - Добавить в cmd_ai
   - Тесты (2 сценария)

5. [ ] DX-001: Коды ошибок TaskManager
   - Создать ERROR_MAPPING
   - Применить во всех handlers
   - Документировать коды

**Критерий завершения:**
- Coverage ≥ 90%
- Все тесты PASSED
- Code review OK

---

### Фаза 3: Улучшения (P2-P3)
**ETA:** 2-4 часа (опционально)

1. [ ] MIN-001: Рефакторинг generate_suggestions
2. [ ] MIN-002: DRY для валидации (_require_task helper)
3. [ ] MIN-003: Добавить логирование
4. [ ] MIN-004: Версионирование API
5. [ ] MIN-005 - MIN-008: Мелкие улучшения

**Критерий завершения:**
- Code quality metrics улучшены
- Maintainability index > 80

---

## КАК ОТКАТИТЬ

Если после исправлений возникли проблемы:

### Сценарий 1: Регрессия в тестах
```bash
# Откат к предыдущему коммиту
git revert HEAD

# Или cherry-pick безопасных изменений
git cherry-pick <commit-hash>
```

### Сценарий 2: Проблемы с производительностью
```bash
# Отключить валидацию временно (ТОЛЬКО для дебага!)
# В cli_ai.py:
ENABLE_VALIDATION = False  # TODO: remove in production

if ENABLE_VALIDATION:
    if not _validate_task_id(task_id):
        return error_response(...)
```

### Сценарий 3: Конфликты с существующими ИИ-агентами
```bash
# Добавить backwards compatibility:
API_VERSION = "2.0.0"

def process_intent(manager, data):
    version = data.get("api_version", "1.0.0")
    if version == "1.0.0":
        # Legacy behavior (без валидации)
        return _process_intent_v1(manager, data)
    else:
        # New behavior (с валидацией)
        return _process_intent_v2(manager, data)
```

---

## MIGRATION NOTES

### Для ИИ-агентов использующих cli_ai:

**Breaking changes (после исправлений):**

1. **task_id теперь валидируется:**
   ```json
   ❌ {"intent": "context", "task": "../../etc/passwd"}
   ✅ {"intent": "context", "task": "TASK-001"}
   ```

2. **Лимиты на размер данных:**
   - Максимум 1000 подзадач в decompose
   - Максимум 10MB JSON
   - Максимум 10 уровней вложенности path

3. **Ошибки теперь более специфичны:**
   ```json
   {
     "error": {
       "code": "INVALID_TASK_ID",  // Вместо generic "UPDATE_FAILED"
       "message": "Некорректный формат task_id"
     }
   }
   ```

4. **handle_decompose возвращает errors:**
   ```json
   {
     "result": {
       "created": [...],
       "errors": [  // НОВОЕ!
         {"index": 2, "title": "...", "error": "Missing criteria"}
       ]
     }
   }
   ```

### Для разработчиков:

1. Все новые handlers должны использовать `_validate_task_id()`
2. Все path операции должны использовать `_parse_path()`
3. Все ошибки TaskManager должны мапиться через ERROR_MAPPING
4. Новый код должен иметь ≥90% покрытие тестами

---

## РИСКИ

### Технические риски

1. **Изменение behavior может сломать существующие ИИ-агенты**
   - Митигация: Добавить API версионирование
   - Митигация: Backwards compatibility режим

2. **Строгая валидация может отклонять легитимные запросы**
   - Митигация: Логировать rejected requests
   - Митигация: Мониторинг false positives

3. **Rate limiting может блокировать быстрые workflows**
   - Митигация: Увеличить лимит до 120/min
   - Митигация: Batch operations для объединения запросов

### Бизнес риски

1. **Задержка релиза из-за объема исправлений**
   - Митигация: Phased rollout (P0 -> P1 -> P2)
   - Митигация: Feature flag для новой валидации

2. **Cognitive load на ИИ-агентов из-за новых error codes**
   - Митигация: Подробная документация с примерами
   - Митигация: Автоматические suggestions для исправления

---

## МЕТРИКИ УСПЕХА

### До исправлений
- Coverage: 65%
- Security issues: 3 critical
- Ruff errors: 7
- Code quality: 7.5/10

### После исправлений (целевые)
- Coverage: ≥90%
- Security issues: 0 critical, 0 high
- Ruff errors: 0
- Code quality: ≥9/10

### Мониторинг после деплоя
- Response time: <5ms p99 (было ~3ms)
- Error rate: <1% (rejection из-за валидации)
- False positive rate: <0.1% (легитимные запросы отклонены)

---

## RELATED DOCUMENTS

- [CODE_REVIEW.md](./CODE_REVIEW.md) - Полный отчет аудита
- [core/desktop/devtools/interface/cli_ai.py](./core/desktop/devtools/interface/cli_ai.py) - Исходный код
- [tests/test_cli_ai_unit.py](./tests/test_cli_ai_unit.py) - Тесты

---

## CHANGELOG

### 2025-11-25 - Initial Audit
- Проведен комплексный аудит
- Обнаружено 3 critical, 5 major, 8 minor issues
- Создан план исправлений

### TBD - Phase 1 Fixes (P0)
- [ ] SEC-001 fixed
- [ ] SEC-002 fixed
- [ ] SEC-003 verified

### TBD - Phase 2 Fixes (P1)
- [ ] Coverage increased to 90%
- [ ] All major issues resolved

---

**Next Steps:**
1. Approve this ship log
2. Create tickets for each issue (SEC-001, SEC-002, ...)
3. Assign to developer(s)
4. Start Phase 1 fixes
5. Review & merge after P0+P1 complete

**Sign-off required from:**
- [ ] Security team (for SEC-001, SEC-002, SEC-003)
- [ ] Tech lead (for architecture changes)
- [ ] QA (for test coverage)
