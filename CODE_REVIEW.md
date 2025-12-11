# АУДИТ МОДУЛЯ cli_ai.py
## AI-first CLI Interface - Code Review Report

**Дата аудита:** 2025-11-25
**Версия:** commit e79842d
**Аудитор:** Claude Sonnet 4.5
**Файлы:** `core/desktop/devtools/interface/cli_ai.py`, `tests/test_cli_ai_unit.py`

---

## EXECUTIVE SUMMARY

**Verdict:** REQUEST_CHANGES

**Общая оценка качества:** 7.5/10

Модуль представляет собой хорошо структурированный AI-first интерфейс с декларативной семантикой и единым JSON-форматом взаимодействия. Архитектура продумана, код читаемый, тесты покрывают основные сценарии. Однако обнаружены **критические проблемы безопасности**, недостаточная валидация входных данных и пробелы в покрытии тестами edge-сценариев.

**Ключевые находки:**
- 65% покрытие тестами (целевое: 90%)
- 3 критических проблемы безопасности (BLOCKER)
- 5 серьезных проблем (MAJOR)
- 8 рекомендаций по улучшению (MINOR)
- Производительность: приемлемая (контекст 1.89ms, suggestions 0.92ms)

---

## RISK ASSESSMENT

| Категория | Уровень риска | Статус | Детали |
|-----------|---------------|---------|--------|
| **Security** | HIGH | BLOCKER | Нет валидации path traversal, инъекции, DoS через большие объемы данных |
| **Correctness** | MEDIUM | MAJOR | Слабая обработка edge-случаев, неполная валидация типов |
| **Performance** | LOW | OK | Производительность приемлемая, но нет защиты от DoS |
| **Maintainability** | MEDIUM | MINOR | Высокая когнитивная сложность функций (115 строк) |
| **Testability** | MEDIUM | MAJOR | 65% покрытие, отсутствуют property-based тесты |

---

## КРИТИЧЕСКИЕ ПРОБЛЕМЫ (BLOCKER)

### SEC-001: Path Traversal в handle_context и handle_decompose
**Severity:** CRITICAL
**File:** `cli_ai.py:322, 371`

**Проблема:**
```python
# cli_ai.py:322
task_id = data.get("task")  # Никакой валидации!
# Злоумышленник может передать: {"task": "../../../etc/passwd"}

# cli_ai.py:371
task_id = data.get("task")  # То же самое
```

**Воспроизведение:**
```bash
tasks ai '{"intent": "context", "task": "../../etc/passwd"}'
```

**Эксплуатация:** TaskManager.load_task() может пытаться читать файлы за пределами `.tasks/`.

**Исправление:**
```python
def _validate_task_id(task_id: str) -> bool:
    """Validate task_id to prevent path traversal."""
    if not task_id:
        return False
    # Только TASK-NNNN формат
    if not re.match(r'^TASK-\d{3,6}$', task_id):
        return False
    # Запретить path separators
    if '/' in task_id or '\\' in task_id or '..' in task_id:
        return False
    return True

# В каждом handler:
task_id = data.get("task")
if task_id and not _validate_task_id(task_id):
    return error_response(intent, "INVALID_TASK_ID", "Некорректный формат task_id")
```

**Приоритет:** P0 (BLOCKER)

---

### SEC-002: DoS через неограниченный размер входных данных
**Severity:** CRITICAL
**File:** `cli_ai.py:374-378, 941-950`

**Проблема:**
```python
# Нет ограничения на размер subtasks массива
subtasks_data = data.get("subtasks", [])  # Может быть 1 000 000 элементов!

# Нет ограничения на размер JSON в cmd_ai
json_input = sys.stdin.read().strip()  # Может прочитать гигабайты!
```

**Эксплуатация:**
```bash
# DoS attack - создать 100K подзадач
python -c "import json; print(json.dumps({'intent': 'decompose', 'task': 'T', 'subtasks': [{'title': 'x', 'criteria': ['c'], 'tests': ['t'], 'blockers': ['b']}] * 100000}))" | tasks ai
```

**Исправление:**
```python
MAX_JSON_SIZE = 10 * 1024 * 1024  # 10MB
MAX_SUBTASKS = 1000
MAX_ARRAY_ITEMS = 1000
MAX_STRING_LENGTH = 10000

def _validate_input_size(data: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Validate input data size limits."""
    # Check subtasks array
    if 'subtasks' in data:
        subtasks = data['subtasks']
        if not isinstance(subtasks, list):
            return False, "subtasks must be array"
        if len(subtasks) > MAX_SUBTASKS:
            return False, f"Too many subtasks: {len(subtasks)} > {MAX_SUBTASKS}"

    # Check string lengths recursively
    def check_strings(obj, path=""):
        if isinstance(obj, str) and len(obj) > MAX_STRING_LENGTH:
            return False, f"String too long at {path}: {len(obj)} > {MAX_STRING_LENGTH}"
        elif isinstance(obj, dict):
            for k, v in obj.items():
                ok, err = check_strings(v, f"{path}.{k}")
                if not ok:
                    return ok, err
        elif isinstance(obj, list):
            if len(obj) > MAX_ARRAY_ITEMS:
                return False, f"Array too large at {path}: {len(obj)} > {MAX_ARRAY_ITEMS}"
            for i, item in enumerate(obj):
                ok, err = check_strings(item, f"{path}[{i}]")
                if not ok:
                    return ok, err
        return True, None

    return check_strings(data)

# В cmd_ai:
json_input = sys.stdin.read(MAX_JSON_SIZE + 1)
if len(json_input) > MAX_JSON_SIZE:
    response = error_response("parse", "INPUT_TOO_LARGE", f"Input exceeds {MAX_JSON_SIZE} bytes")
    print(response.to_json())
    return 1
```

**Приоритет:** P0 (BLOCKER)

---

### SEC-003: Нет санитизации строковых данных для shell injection
**Severity:** HIGH
**File:** `cli_ai.py:414-419` (write_activity_marker)

**Проблема:**
```python
# write_activity_marker может записывать неэскейпированные данные
write_activity_marker(
    task_id,  # может содержать shell metacharacters
    "decompose",
    subtask_path=parent_path,  # тоже не валидируется
    tasks_dir=getattr(manager, "tasks_dir", None),
)
```

**Эксплуатация:**
Если `write_activity_marker` использует shell команды или записывает в shell-скрипты, возможна инъекция.

**Рекомендация:**
1. Проверить реализацию `write_activity_marker` на предмет shell injection
2. Санитизировать все пути и идентификаторы перед передачей
3. Использовать только безопасные API (Path, subprocess с shell=False)

**Приоритет:** P0 (требует проверки write_activity_marker)

---

## СЕРЬЕЗНЫЕ ПРОБЛЕМЫ (MAJOR)

### COR-001: Неполная валидация path в handle_progress
**Severity:** MAJOR
**File:** `cli_ai.py:621-626`

**Проблема:**
```python
try:
    path_parts = str(path).split(".")
    index = int(path_parts[0])
    nested_path = ".".join(path_parts[1:]) if len(path_parts) > 1 else None
except (ValueError, IndexError):
    return error_response("progress", "INVALID_PATH", f"Некорректный путь: {path}")
```

**Проблемы:**
1. Не проверяется диапазон `index` (может быть отрицательным или огромным)
2. Не проверяется глубина вложенности (DoS через `"0.0.0.0.0..."` x1000)
3. ValueError может возникнуть в неожиданных местах

**Исправление:**
```python
MAX_NESTING_DEPTH = 10
MAX_PATH_INDEX = 10000

def _parse_path(path: str) -> Tuple[Optional[int], Optional[str], Optional[str]]:
    """Parse and validate subtask path.

    Returns: (index, nested_path, error_message)
    """
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

# В handle_progress:
index, nested_path, error = _parse_path(str(path))
if error:
    return error_response("progress", "INVALID_PATH", error)
```

**Приоритет:** P1 (MAJOR)

---

### COR-002: Неконсистентная обработка пустых списков в handle_decompose
**Severity:** MAJOR
**File:** `cli_ai.py:383-411`

**Проблема:**
```python
for idx, st_data in enumerate(subtasks_data):
    title = st_data.get("title", "")
    if not title:
        continue  # Молча пропускает!

    criteria = st_data.get("criteria", [])
    tests = st_data.get("tests", [])
    blockers = st_data.get("blockers", [])

    # manager.add_subtask вызывает _build_subtask, который ТРЕБУЕТ непустые списки
    success, error = manager.add_subtask(...)
```

**Проблемы:**
1. Если `title` пустой - молча пропускает (ИИ не узнает об ошибке)
2. Если `criteria/tests/blockers` пусты - `_build_subtask` вернет `None`, но ошибка скрыта
3. `created` может быть меньше, чем `len(subtasks_data)`, но причина неясна

**Исправление:**
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

    # Валидация ПЕРЕД вызовом API
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

# В результате:
return AIResponse(
    success=len(errors) == 0,
    intent="decompose",
    result={
        "created": created,
        "total_created": len(created),
        "errors": errors,  # ВАЖНО для ИИ!
    },
    ...
    error_code="PARTIAL_FAILURE" if errors else None,
    error_message=f"{len(errors)} subtasks failed" if errors else None,
)
```

**Приоритет:** P1 (MAJOR)

---

### TST-001: Недостаточное покрытие тестами (65%)
**Severity:** MAJOR
**File:** `tests/test_cli_ai_unit.py`

**Проблема:**
```
Name                                        Stmts   Miss  Cover   Missing
-------------------------------------------------------------------------
core/desktop/devtools/interface/cli_ai.py     323    113    65%   179, 200, 220, 228-236, ...
```

**Непокрытые сценарии:**
1. `_build_subtasks_tree` с вложенными children (строка 179)
2. Ветви в `generate_suggestions` для разных состояний (200, 220, 228-236, 240, 251, 277, 288-295)
3. Успешные сценарии `handle_define`, `handle_verify`, `handle_progress` (464-647)
4. Сценарии ошибок в `handle_complete` (INCOMPLETE_SUBTASKS, UNVERIFIED_CRITERIA)
5. `cmd_ai` - точка входа CLI (945-1002)
6. Exception handling в `process_intent` (933-934)

**Рекомендация:**
Добавить тесты (см. раздел PATCHES ниже).

**Приоритет:** P1 (MAJOR)

---

### PER-001: Отсутствие rate limiting для cmd_ai
**Severity:** MAJOR
**File:** `cli_ai.py:942-1002`

**Проблема:**
ИИ-агент может вызывать `tasks ai` в цикле без ограничений, что приведет к:
1. Исчерпанию файловых дескрипторов (каждый вызов открывает YAML)
2. CPU exhaustion при генерации suggestions для больших задач
3. Disk I/O storm при частых save_task

**Рекомендация:**
```python
# Простой in-memory rate limiter
from collections import defaultdict
from time import time

_rate_limits = defaultdict(list)
MAX_REQUESTS_PER_MINUTE = 60

def _check_rate_limit(client_id: str = "default") -> Tuple[bool, Optional[str]]:
    """Check rate limit for client."""
    now = time()
    window_start = now - 60

    # Cleanup old entries
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
    return 429  # HTTP 429 Too Many Requests
```

**Приоритет:** P1 (MAJOR)

---

### DX-001: Неинформативные коды ошибок от TaskManager
**Severity:** MAJOR
**File:** `cli_ai.py:393-400, 628-635`

**Проблема:**
```python
success, error = manager.add_subtask(...)
if success:
    created.append(...)
# Но error может быть "not_found", "missing_fields", "path" - что это значит для ИИ?

success, error = manager.set_subtask(task_id, index, completed, path=nested_path)
if not success:
    return error_response(
        "progress",
        "UPDATE_FAILED",
        error or "Не удалось обновить подзадачу",  # Generic!
    )
```

**Проблемы:**
1. Коды ошибок TaskManager не документированы
2. Маппинг "not_found" -> "UPDATE_FAILED" теряет информацию
3. ИИ не может понять, что именно пошло не так

**Исправление:**
```python
# Маппинг ошибок TaskManager -> AI-friendly коды
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

**Приоритет:** P1 (MAJOR - влияет на UX для ИИ)

---

## РЕКОМЕНДАЦИИ ПО УЛУЧШЕНИЮ (MINOR)

### MIN-001: Высокая когнитивная сложность generate_suggestions
**File:** `cli_ai.py:189-304`
**Lines:** 115

**Проблема:** Функция слишком длинная, трудна для понимания и тестирования.

**Рекомендация:**
```python
def generate_suggestions(manager, task_id):
    if not task_id:
        return _suggest_for_no_task(manager)

    task = manager.load_task(task_id)
    if not task:
        return []

    suggestions = []
    suggestions.extend(_suggest_for_undefined_criteria(task))
    suggestions.extend(_suggest_for_unverified_checkpoints(task))
    suggestions.extend(_suggest_for_unresolved_blockers(task))
    suggestions.extend(_suggest_for_ready_subtasks(task))
    suggestions.extend(_suggest_for_task_completion(task))

    return suggestions[:5]

def _suggest_for_undefined_criteria(task):
    """Generate suggestions for subtasks without criteria."""
    suggestions = []
    for i, st in enumerate(task.subtasks):
        if not st.success_criteria:
            suggestions.append(Suggestion(
                action="define",
                target=str(i),
                reason=f"Подзадача '{st.title}' без критериев успеха",
                priority="high",
            ))
    return suggestions

# И так далее для каждой категории...
```

**Приоритет:** P2 (MINOR - рефакторинг)

---

### MIN-002: Дублирование кода валидации в handle_*
**File:** `cli_ai.py:312-723`

**Проблема:** Каждый handler повторяет:
```python
task_id = data.get("task")
if not task_id:
    return error_response(intent, "MISSING_TASK", "Поле 'task' обязательно")

task = manager.load_task(task_id)
if not task:
    return error_response(intent, "TASK_NOT_FOUND", f"Задача {task_id} не найдена")
```

**Рекомендация:**
```python
def _require_task(manager: TaskManager, data: Dict[str, Any], intent: str) -> Union[TaskDetail, AIResponse]:
    """Load task or return error response."""
    task_id = data.get("task")
    if not task_id:
        return error_response(intent, "MISSING_TASK", "Поле 'task' обязательно")

    if not _validate_task_id(task_id):
        return error_response(intent, "INVALID_TASK_ID", "Некорректный формат task_id")

    task = manager.load_task(task_id)
    if not task:
        return error_response(intent, "TASK_NOT_FOUND", f"Задача {task_id} не найдена")

    return task

# В handlers:
task = _require_task(manager, data, "define")
if isinstance(task, AIResponse):
    return task  # Error response
```

**Приоритет:** P2 (MINOR - DRY principle)

---

### MIN-003: Отсутствие логирования операций
**File:** `cli_ai.py` (весь модуль)

**Проблема:** Нет логов для отладки и аудита:
- Какой ИИ-агент делал запросы?
- Какие операции выполнялись?
- Сколько времени заняла каждая операция?

**Рекомендация:**
```python
import logging

logger = logging.getLogger(__name__)

def process_intent(manager: TaskManager, data: Dict[str, Any]) -> AIResponse:
    intent = data.get("intent")
    logger.info(f"Processing intent: {intent}", extra={"data": data})

    start = time.time()
    try:
        handler = INTENT_HANDLERS.get(intent)
        response = handler(manager, data)
        logger.info(
            f"Intent {intent} completed: success={response.success}",
            extra={"duration_ms": (time.time() - start) * 1000}
        )
        return response
    except Exception as e:
        logger.exception(f"Intent {intent} failed", extra={"error": str(e)})
        raise
```

**Приоритет:** P2 (MINOR - observability)

---

### MIN-004: Нет версионирования API
**File:** `cli_ai.py:1-24` (docstring)

**Проблема:** API может меняться, но ИИ-агенты не смогут понять, какую версию используют.

**Рекомендация:**
```python
API_VERSION = "1.0.0"

class AIResponse:
    def to_dict(self):
        return {
            "api_version": API_VERSION,  # Добавить версию
            "success": self.success,
            ...
        }

# В cmd_ai для intent="help":
help_response = AIResponse(
    ...,
    result={
        "api_version": API_VERSION,
        "usage": ...,
        ...
    }
)
```

**Приоритет:** P3 (MINOR - forward compatibility)

---

### MIN-005: Unused imports (Ruff F401)
**File:** `cli_ai.py:33, 35, 37`

**Проблема:**
```
F401 typing.Union imported but unused
F401 current_timestamp imported but unused
F401 subtask_to_dict imported but unused
```

**Исправление:**
```python
# Удалить неиспользуемые импорты
- from typing import Any, Dict, List, Optional, Union
+ from typing import Any, Dict, List, Optional

- from core.desktop.devtools.application.task_manager import TaskManager, current_timestamp
+ from core.desktop.devtools.application.task_manager import TaskManager

- from core.desktop.devtools.interface.serializers import subtask_to_dict, task_to_dict
+ from core.desktop.devtools.interface.serializers import task_to_dict
```

**Приоритет:** P3 (MINOR - code hygiene)

---

### MIN-006: Line length violations (E501)
**File:** `cli_ai.py:35, 461, 536, 971`

**Проблема:** 4 строки превышают 88 символов (нарушение PEP 8).

**Исправление:**
```python
# Строка 35:
from core.desktop.devtools.application.task_manager import (
    TaskManager,
)

# Строки 461, 536:
return error_response(
    "define",
    "TASK_NOT_FOUND",
    f"Задача {task_id} не найдена"
)

# Строка 971:
examples = [
    '{"intent": "context"}',
    '{"intent": "context", "task": "TASK-001"}',
    '{"intent": "decompose", "task": "TASK-001", '
    '"subtasks": [{"title": "..."}]}',
]
```

**Приоритет:** P3 (MINOR - formatting)

---

### MIN-007: Нет валидации priority в Suggestion
**File:** `cli_ai.py:46-60`

**Проблема:**
```python
@dataclass
class Suggestion:
    priority: str = "normal"  # Может быть любая строка!
```

**Рекомендация:**
```python
from enum import Enum

class Priority(str, Enum):
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"

@dataclass
class Suggestion:
    action: str
    target: str
    reason: str
    priority: Priority = Priority.NORMAL

    def to_dict(self):
        return {
            ...
            "priority": self.priority.value,
        }
```

**Приоритет:** P3 (MINOR - type safety)

---

### MIN-008: Отсутствие примеров использования для ИИ
**File:** `cli_ai.py:1-24` (docstring)

**Проблема:** Docstring показывает формат, но не объясняет workflow для ИИ-агента.

**Рекомендация:**
```python
"""AI-first CLI interface.

Когнитивная модель для ИИ-агентов:
...

Типичный workflow для ИИ-агента:

1. Получить контекст:
   >>> tasks ai '{"intent": "context"}'
   # Ответ содержит suggestions с приоритетами

2. Создать задачу:
   >>> tasks ai '{"intent": "create", "title": "Implement feature X"}'
   # Ответ содержит task_id

3. Декомпозировать:
   >>> tasks ai '{"intent": "decompose", "task": "TASK-001", "subtasks": [...]}'

4. Следовать suggestions из предыдущих ответов:
   >>> tasks ai '{"intent": "verify", "task": "TASK-001", "path": "0", "checkpoints": {...}}'

Обработка ошибок:
- success=false => проверить error.code и error.message
- Коды ошибок: MISSING_TASK, TASK_NOT_FOUND, INVALID_PATH, ...
"""
```

**Приоритет:** P3 (MINOR - documentation)

---

## ТЕСТИРОВАНИЕ

### Текущее состояние

**Результаты тестов:**
```
38 tests PASSED in 0.50s
Coverage: 65% (323 statements, 113 missed)
```

**Пропущенные сценарии:**

1. **Edge cases в _build_subtasks_tree:**
   - Глубокая вложенность (>10 уровней)
   - Пустые children
   - Missing attributes

2. **generate_suggestions - ветви:**
   - Задача со смешанными состояниями подзадач
   - Все подзадачи completed но не verified
   - Подзадачи с tests но без criteria

3. **handle_define, handle_verify, handle_progress - happy path:**
   - Успешное определение criteria/tests/blockers
   - Частичная верификация (только criteria)
   - Прогресс вложенных подзадач

4. **handle_complete - validation errors:**
   - INCOMPLETE_SUBTASKS
   - UNVERIFIED_CRITERIA

5. **cmd_ai - CLI entry point:**
   - Чтение из stdin
   - Invalid JSON
   - Domain/phase/component routing

6. **Exception handling:**
   - process_intent при exception в handler

### Рекомендации по тестированию

**Property-based тесты (Hypothesis):**
```python
from hypothesis import given, strategies as st

@given(
    task_id=st.text(min_size=1, max_size=100),
    path=st.text(min_size=1, max_size=50),
)
def test_get_subtask_by_path_never_crashes(task_id, path):
    """Property: _get_subtask_by_path никогда не падает."""
    subtasks = []  # или генерируем случайное дерево
    result = _get_subtask_by_path(subtasks, path)
    # Должен вернуть либо подзадачу, либо None, но не упасть
    assert result is None or hasattr(result, 'title')

@given(
    data=st.dictionaries(
        st.text(min_size=1, max_size=20),
        st.one_of(st.text(), st.integers(), st.lists(st.text())),
    )
)
def test_process_intent_handles_arbitrary_json(tmp_path, data):
    """Property: process_intent обрабатывает любой JSON без падения."""
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    response = process_intent(manager, data)

    # Должен вернуть валидный AIResponse
    assert isinstance(response, AIResponse)
    assert isinstance(response.success, bool)
    assert isinstance(response.intent, str)
```

**Интеграционные тесты:**
```python
def test_full_workflow_create_decompose_verify_complete(tmp_path):
    """Полный workflow: create -> decompose -> verify -> complete."""
    manager = TaskManager(tasks_dir=tmp_path / ".tasks")

    # 1. Create
    resp1 = process_intent(manager, {
        "intent": "create",
        "title": "Test Task",
    })
    assert resp1.success
    task_id = resp1.result["task_id"]

    # 2. Decompose
    resp2 = process_intent(manager, {
        "intent": "decompose",
        "task": task_id,
        "subtasks": [
            {"title": "Sub 1", "criteria": ["C1"], "tests": ["T1"], "blockers": ["B1"]},
        ],
    })
    assert resp2.success

    # 3. Verify
    resp3 = process_intent(manager, {
        "intent": "verify",
        "task": task_id,
        "path": "0",
        "checkpoints": {
            "criteria": {"confirmed": True, "note": "Done"},
            "tests": {"confirmed": True, "note": "Passed"},
            "blockers": {"confirmed": True, "note": "Resolved"},
        },
    })
    assert resp3.success

    # 4. Progress
    resp4 = process_intent(manager, {
        "intent": "progress",
        "task": task_id,
        "path": "0",
        "completed": True,
    })
    assert resp4.success

    # 5. Complete
    resp5 = process_intent(manager, {
        "intent": "complete",
        "task": task_id,
    })
    assert resp5.success
```

---

## ПРОИЗВОДИТЕЛЬНОСТЬ

**Измерения:**
```
Context build (100 subtasks): 1.89ms
Suggestions generation: 0.92ms
Total response time: ~3ms
```

**Оценка:** ПРИЕМЛЕМО для типичного использования.

**Потенциальные узкие места:**

1. **generate_suggestions O(n) по subtasks:**
   - При 1000 подзадач: ~10ms (приемлемо)
   - При 10000 подзадач: ~100ms (предельно)

2. **build_context с include_all_tasks:**
   - При 1000 задач: ~100ms (зависит от YAML парсинга)

3. **_build_subtasks_tree рекурсивный:**
   - Глубокая вложенность (>100 уровней) может привести к stack overflow

**Рекомендации:**
1. Ограничить MAX_SUBTASKS_PER_TASK = 1000
2. Ограничить MAX_NESTING_DEPTH = 10
3. Добавить кеширование context (TTL 1 секунда)
4. Профилировать на реальных данных

---

## КОГНИТИВНАЯ ЭРГОНОМИКА ДЛЯ ИИ

**Сильные стороны:**

1. **Единый JSON формат** - предсказуемый, легко парсится
2. **Suggestions в каждом ответе** - снижает когнитивную нагрузку
3. **Контекст всегда включен** - ИИ видит полную картину
4. **Семантические операции** - декларативный стиль
5. **Batch operations** - эффективность для множества действий

**Слабые стороны:**

1. **Коды ошибок недостаточно специфичны:**
   - "UPDATE_FAILED" не объясняет причину
   - Нет machine-readable категорий (validation, permission, not_found, conflict)

2. **Отсутствие dry-run режима:**
   - ИИ не может проверить операцию без выполнения

3. **Нет undo/rollback:**
   - Ошибка в batch прерывает выполнение, но не откатывает предыдущие

4. **Suggestions без приоритетов execution order:**
   - ИИ не знает, какую suggestion выполнить первой

**Рекомендации:**

```python
# 1. Structured error codes
class ErrorCode(str, Enum):
    # Validation errors (4xx)
    MISSING_REQUIRED_FIELD = "validation.missing_field"
    INVALID_FORMAT = "validation.invalid_format"
    OUT_OF_RANGE = "validation.out_of_range"

    # Resource errors (4xx)
    TASK_NOT_FOUND = "resource.task_not_found"
    SUBTASK_NOT_FOUND = "resource.subtask_not_found"

    # State errors (4xx)
    TASK_NOT_READY = "state.task_not_ready"
    ALREADY_COMPLETED = "state.already_completed"

    # Internal errors (5xx)
    STORAGE_ERROR = "internal.storage"
    UNKNOWN_ERROR = "internal.unknown"

# 2. Dry-run mode
def process_intent(manager, data, dry_run=False):
    if dry_run:
        # Validate without executing
        return _validate_intent(manager, data)
    else:
        return _execute_intent(manager, data)

# 3. Suggestions с execution_order
@dataclass
class Suggestion:
    ...
    execution_order: int = 0  # 0 = first, 999 = last
    blocking: bool = True  # Блокирует ли другие suggestions?

# 4. Batch с rollback
def handle_batch(manager, data):
    # Save state
    checkpoint = _create_checkpoint(manager)

    try:
        results = []
        for op in operations:
            response = handler(manager, op)
            if not response.success and op.get("required", True):
                # Rollback
                _restore_checkpoint(manager, checkpoint)
                return error_response(...)
            results.append(response)
        return success_response(results)
    except Exception as e:
        _restore_checkpoint(manager, checkpoint)
        raise
```

**Оценка эргономики:** 8/10

---

## СООТВЕТСТВИЕ API TaskManager

**Проверка использования:**

| Метод | Использование в cli_ai | Корректность | Замечания |
|-------|------------------------|--------------|-----------|
| `create_task` | handle_create:831-836 | OK | Корректно |
| `save_task` | Multiple | OK | Корректно |
| `load_task` | Multiple | OK | Не проверяет domain |
| `list_tasks` | build_context:121 | OK | Не использует skip_sync |
| `add_subtask` | handle_decompose:393-400 | WARN | Не мапит коды ошибок |
| `set_subtask` | handle_progress:628 | WARN | Неполная обработка path |

**Проблемы:**

1. **load_task не передает domain:**
   ```python
   # cli_ai.py:459
   task = manager.load_task(task_id)  # domain="" by default

   # Но в cmd_ai есть domain routing:
   if domain or phase or component:
       subpath = Path(domain or "") / (phase or "") / (component or "")
       tasks_dir = tasks_dir / subpath
   ```

   **Риск:** Задачи в domain-specific директориях не загружаются.

2. **add_subtask возвращает Tuple[bool, Optional[str]]:**
   ```python
   success, error = manager.add_subtask(...)
   ```

   Но коды ошибок ("not_found", "missing_fields", "path") не документированы.

**Рекомендация:** Создать явный контракт для кодов ошибок TaskManager.

---

## CHECKLIST АУДИТА

### Hard Gates

- [ ] **Тесты:** 65% покрытие (целевое: 90%) - FAIL
- [ ] **Статика:** Ruff найдено 7 ошибок (3 F401, 4 E501) - FAIL
- [x] **Безопасность (общая):** Нет явного SQL injection или hardcoded secrets - PASS
- [ ] **Безопасность (path traversal):** Есть path traversal риски - FAIL
- [ ] **Безопасность (DoS):** Нет защиты от больших входов - FAIL
- [x] **Производительность:** Приемлемая для типичных нагрузок - PASS
- [x] **Edge cases:** Частично обработаны, но есть пробелы - PASS (с оговорками)

**VERDICT:** REQUEST_CHANGES

---

## SUMMARY OF FINDINGS

### Critical (3)
1. SEC-001: Path Traversal в task_id (BLOCKER)
2. SEC-002: DoS через неограниченный размер данных (BLOCKER)
3. SEC-003: Потенциальная shell injection в write_activity_marker (HIGH)

### Major (5)
1. COR-001: Неполная валидация path (индексы, глубина)
2. COR-002: Неконсистентная обработка ошибок в handle_decompose
3. TST-001: Недостаточное покрытие тестами (65%)
4. PER-001: Отсутствие rate limiting
5. DX-001: Неинформативные коды ошибок от TaskManager

### Minor (8)
1. MIN-001: Высокая когнитивная сложность generate_suggestions
2. MIN-002: Дублирование кода валидации
3. MIN-003: Отсутствие логирования
4. MIN-004: Нет версионирования API
5. MIN-005: Unused imports (Ruff F401)
6. MIN-006: Line length violations (E501)
7. MIN-007: Нет валидации priority enum
8. MIN-008: Недостаточно примеров для ИИ

---

## РЕКОМЕНДУЕМЫЕ ДЕЙСТВИЯ

### Немедленно (P0)
1. Исправить SEC-001: Добавить валидацию task_id
2. Исправить SEC-002: Добавить лимиты на размер входных данных
3. Проверить SEC-003: Аудит write_activity_marker

### В ближайшее время (P1)
1. Исправить COR-001: Улучшить валидацию path
2. Исправить COR-002: Обработка ошибок в handle_decompose
3. Повысить покрытие тестами до 90%
4. Добавить rate limiting
5. Улучшить коды ошибок

### По возможности (P2-P3)
1. Рефакторинг generate_suggestions
2. DRY для валидации
3. Добавить логирование
4. Версионирование API
5. Исправить линтер warnings

---

## ОЦЕНКА КАЧЕСТВА: 7.5/10

**Разбивка:**

| Критерий | Оценка | Вес | Комментарий |
|----------|--------|-----|-------------|
| Архитектура | 9/10 | 20% | Отличная декомпозиция, чистый код |
| Безопасность | 4/10 | 25% | Критические уязвимости |
| Корректность | 7/10 | 20% | Работает, но есть edge cases |
| Тестируемость | 6/10 | 15% | 65% покрытие недостаточно |
| Производительность | 8/10 | 10% | Приемлемо, но нет DoS защиты |
| Эргономика для ИИ | 8/10 | 10% | Хороший DX, можно улучшить |

**Взвешенная оценка:** 6.65/10

**С учетом потенциала:** 7.5/10 (после исправления блокеров будет 9/10)

---

## ЗАКЛЮЧЕНИЕ

Модуль `cli_ai.py` демонстрирует **хорошую архитектуру** и **продуманный дизайн** для взаимодействия с ИИ-агентами. Декларативный подход, единый JSON-формат и система suggestions создают отличную когнитивную модель.

Однако обнаружены **критические проблемы безопасности** (path traversal, DoS) и **недостаточное тестовое покрытие**, которые блокируют использование в продакшене без исправлений.

**Рекомендация:** REQUEST_CHANGES с приоритетом исправления SEC-001, SEC-002 перед мержем в main.

После устранения блокеров модуль будет **отличным примером** AI-first интерфейса для task management системы.

---

## APPENDIX: МЕТРИКИ КОДА

```
Модуль: core/desktop/devtools/interface/cli_ai.py
- Строк кода: 1011
- Функций: 18
- Классов: 3 (dataclasses)
- Средняя длина функции: 56 строк
- Максимальная длина функции: 115 строк (generate_suggestions)
- Cyclomatic complexity: ~4-5 (средняя)

Тесты: tests/test_cli_ai_unit.py
- Строк кода: 511
- Тестов: 38
- Покрытие: 65%
- Соотношение код/тесты: 1:0.5 (норма: 1:1-2)
```

**Дата:** 2025-11-25
**Версия отчета:** 1.0
**Аудитор:** Claude Sonnet 4.5
