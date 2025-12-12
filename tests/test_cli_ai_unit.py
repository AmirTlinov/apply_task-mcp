"""Unit tests for cli_ai module."""

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.desktop.devtools.interface.cli_ai import (
    AIResponse,
    Suggestion,
    build_context,
    error_response,
    generate_suggestions,
    handle_batch,
    handle_complete,
    handle_context,
    handle_create,
    handle_decompose,
    handle_define,
    handle_progress,
    handle_verify,
    process_intent,
    _get_subtask_by_path,
)
from core.desktop.devtools.application.task_manager import TaskManager
from core.subtask import SubTask


class TestAIResponse:
    """Tests for AIResponse dataclass."""

    def test_to_dict_success(self):
        resp = AIResponse(
            success=True,
            intent="test",
            result={"foo": "bar"},
            context={"task_id": "TASK-001"},
        )
        d = resp.to_dict()
        assert d["success"] is True
        assert d["intent"] == "test"
        assert d["result"] == {"foo": "bar"}
        assert d["error"] is None
        assert "timestamp" in d

    def test_to_dict_with_error(self):
        resp = AIResponse(
            success=False,
            intent="test",
            error_code="TEST_ERROR",
            error_message="Something went wrong",
        )
        d = resp.to_dict()
        assert d["success"] is False
        assert d["error"]["code"] == "TEST_ERROR"
        assert d["error"]["message"] == "Something went wrong"

    def test_to_json(self):
        resp = AIResponse(success=True, intent="test")
        j = resp.to_json()
        parsed = json.loads(j)
        assert parsed["success"] is True
        assert parsed["intent"] == "test"

    def test_suggestions_serialization(self):
        resp = AIResponse(
            success=True,
            intent="test",
            suggestions=[
                Suggestion(action="verify", target="0", reason="test reason"),
            ],
        )
        d = resp.to_dict()
        assert len(d["suggestions"]) == 1
        assert d["suggestions"][0]["action"] == "verify"
        assert d["suggestions"][0]["target"] == "0"


class TestSuggestion:
    """Tests for Suggestion dataclass."""

    def test_to_dict(self):
        s = Suggestion(
            action="decompose",
            target="TASK-001",
            reason="Need more granularity",
            priority="high",
        )
        d = s.to_dict()
        assert d["action"] == "decompose"
        assert d["target"] == "TASK-001"
        assert d["reason"] == "Need more granularity"
        assert d["priority"] == "high"

    def test_default_priority(self):
        s = Suggestion(action="test", target="x", reason="y")
        assert s.priority == "normal"


class TestErrorResponse:
    """Tests for error_response helper."""

    def test_creates_error_response(self):
        resp = error_response("test", "ERR_CODE", "Error message")
        assert resp.success is False
        assert resp.intent == "test"
        assert resp.error_code == "ERR_CODE"
        assert resp.error_message == "Error message"


class TestGetSubtaskByPath:
    """Tests for _get_subtask_by_path helper."""

    def test_flat_path(self):
        subtasks = [
            SimpleNamespace(title="First", children=[]),
            SimpleNamespace(title="Second", children=[]),
        ]
        result = _get_subtask_by_path(subtasks, "1")
        assert result.title == "Second"

    def test_nested_path(self):
        child = SimpleNamespace(title="Child", children=[])
        parent = SimpleNamespace(title="Parent", children=[child])
        subtasks = [parent]
        result = _get_subtask_by_path(subtasks, "0.0")
        assert result.title == "Child"

    def test_invalid_path(self):
        subtasks = [SimpleNamespace(title="Only", children=[])]
        result = _get_subtask_by_path(subtasks, "5")
        assert result is None

    def test_empty_list(self):
        result = _get_subtask_by_path([], "0")
        assert result is None


class TestBuildContext:
    """Tests for build_context function."""

    def test_empty_tasks_dir(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        ctx = build_context(manager)
        assert ctx["total_tasks"] == 0
        assert ctx["by_status"] == {"OK": 0, "WARN": 0, "FAIL": 0}

    def test_include_all_tasks(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        ctx = build_context(manager, include_all_tasks=True)
        assert "tasks" in ctx
        assert isinstance(ctx["tasks"], list)


class TestProcessIntent:
    """Tests for process_intent router."""

    def test_missing_intent(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        resp = process_intent(manager, {})
        assert resp.success is False
        assert resp.error_code == "MISSING_INTENT"

    def test_unknown_intent(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        resp = process_intent(manager, {"intent": "unknown_intent"})
        assert resp.success is False
        assert resp.error_code == "UNKNOWN_INTENT"
        assert "unknown_intent" in resp.error_message

    def test_context_intent(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        resp = process_intent(manager, {"intent": "context"})
        assert resp.success is True
        assert resp.intent == "context"


class TestHandleContext:
    """Tests for handle_context intent."""

    def test_basic_context(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        resp = handle_context(manager, {"intent": "context"})
        assert resp.success is True
        assert "snapshot" in resp.result

    def test_context_with_task_id(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        # Create a task first
        task = manager.create_task(title="Test Task", priority="MEDIUM")
        task.description = "Test"
        manager.save_task(task)

        resp = handle_context(manager, {"intent": "context", "task": task.id})
        assert resp.success is True
        assert "current_task" in resp.context


class TestHandleDecompose:
    """Tests for handle_decompose intent."""

    def test_missing_task(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        resp = handle_decompose(manager, {"intent": "decompose"})
        assert resp.success is False
        assert resp.error_code == "MISSING_TASK"

    def test_missing_subtasks(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        resp = handle_decompose(manager, {"intent": "decompose", "task": "TASK-001"})
        assert resp.success is False
        assert resp.error_code == "MISSING_SUBTASKS"

    def test_successful_decompose(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        # Create task first
        task = manager.create_task(title="Test Task", priority="MEDIUM")
        task.description = "Test"
        manager.save_task(task)

        resp = handle_decompose(
            manager,
            {
                "intent": "decompose",
                "task": task.id,
                "subtasks": [
                    {
                        "title": "Subtask 1",
                        "criteria": ["Criterion A"],
                        "tests": ["test_a"],
                        "blockers": ["none"],
                    },
                    {
                        "title": "Subtask 2",
                        "criteria": ["Criterion B"],
                        "tests": ["test_b"],
                        "blockers": ["Depends on Subtask 1"],
                    },
                ],
            },
        )
        assert resp.success is True
        assert resp.result["total_created"] == 2


class TestHandleDefine:
    """Tests for handle_define intent."""

    def test_missing_task(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        resp = handle_define(manager, {"intent": "define"})
        assert resp.success is False
        assert resp.error_code == "MISSING_TASK"

    def test_missing_path(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        resp = handle_define(manager, {"intent": "define", "task": "TASK-001"})
        assert resp.success is False
        assert resp.error_code == "MISSING_PATH"

    def test_task_not_found(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        resp = handle_define(
            manager, {"intent": "define", "task": "TASK-999", "path": "0"}
        )
        assert resp.success is False
        assert resp.error_code == "TASK_NOT_FOUND"

    def test_define_updates_title(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        task = manager.create_task(title="Test", priority="MEDIUM")
        task.subtasks = [SubTask(completed=False, title="Old title")]
        manager.save_task(task)

        resp = handle_define(
            manager,
            {
                "intent": "define",
                "task": task.id,
                "path": "0",
                "title": "New title",
            },
        )
        assert resp.success is True
        assert resp.result["updated"]["title"] == "New title"
        reloaded = manager.load_task(task.id)
        assert reloaded is not None
        assert reloaded.subtasks[0].title == "New title"


class TestHandleVerify:
    """Tests for handle_verify intent."""

    def test_missing_task(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        resp = handle_verify(manager, {"intent": "verify"})
        assert resp.success is False
        assert resp.error_code == "MISSING_TASK"

    def test_missing_path(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        resp = handle_verify(manager, {"intent": "verify", "task": "TASK-001"})
        assert resp.success is False
        assert resp.error_code == "MISSING_PATH"


class TestHandleProgress:
    """Tests for handle_progress intent."""

    def test_missing_task(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        resp = handle_progress(manager, {"intent": "progress"})
        assert resp.success is False
        assert resp.error_code == "MISSING_TASK"

    def test_missing_path(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        resp = handle_progress(manager, {"intent": "progress", "task": "TASK-001"})
        assert resp.success is False
        assert resp.error_code == "MISSING_PATH"


class TestHandleComplete:
    """Tests for handle_complete intent."""

    def test_missing_task(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        resp = handle_complete(manager, {"intent": "complete"})
        assert resp.success is False
        assert resp.error_code == "MISSING_TASK"

    def test_task_not_found(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        resp = handle_complete(manager, {"intent": "complete", "task": "TASK-999"})
        assert resp.success is False
        assert resp.error_code == "TASK_NOT_FOUND"


class TestHandleCreate:
    """Tests for handle_create intent."""

    def test_missing_title(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        resp = handle_create(manager, {"intent": "create"})
        assert resp.success is False
        assert resp.error_code == "MISSING_TITLE"

    def test_successful_create(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        resp = handle_create(
            manager,
            {
                "intent": "create",
                "title": "New Task",
                "description": "Test task",
            },
        )
        assert resp.success is True
        assert "task_id" in resp.result

    def test_create_with_subtasks(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        resp = handle_create(
            manager,
            {
                "intent": "create",
                "title": "Task with Subtasks",
                "subtasks": [
                    {"title": "Sub 1", "criteria": ["C1"], "tests": ["T1"], "blockers": ["B1"]},
                    {"title": "Sub 2", "criteria": ["C2"], "tests": ["T2"], "blockers": ["B2"]},
                ],
            },
        )
        assert resp.success is True
        assert resp.result["subtasks_created"] == 2


class TestHandleBatch:
    """Tests for handle_batch intent."""

    def test_missing_operations(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        resp = handle_batch(manager, {"intent": "batch"})
        assert resp.success is False
        assert resp.error_code == "MISSING_OPERATIONS"

    def test_batch_with_task_default(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        # Create task first
        task = manager.create_task(title="Test", priority="MEDIUM")
        task.description = "Test"
        manager.save_task(task)

        resp = handle_batch(
            manager,
            {
                "intent": "batch",
                "task": task.id,
                "operations": [
                    {"intent": "context"},
                ],
            },
        )
        assert resp.success is True
        assert resp.result["completed"] == 1

    def test_batch_stops_on_error(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        resp = handle_batch(
            manager,
            {
                "intent": "batch",
                "operations": [
                    {"intent": "context"},  # Success
                    {"intent": "progress", "task": "TASK-999", "path": "0"},  # Fail
                    {"intent": "context"},  # Should not run
                ],
            },
        )
        assert resp.success is False
        assert resp.result["completed"] == 1  # Only first succeeded


class TestGenerateSuggestions:
    """Tests for generate_suggestions function."""

    def test_suggestions_without_task(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        suggestions = generate_suggestions(manager)
        # Should suggest creating new task when no tasks exist
        assert len(suggestions) > 0

    def test_suggestions_for_task_with_unresolved_blockers(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        # Create task with subtask that has blockers
        task = manager.create_task(title="Test", priority="MEDIUM")
        task.description = "Test"
        manager.save_task(task)
        manager.add_subtask(
            task.id,
            "Subtask with blockers",
            criteria=["C1"],
            tests=["T1"],
            blockers=["Blocker"],
        )

        suggestions = generate_suggestions(manager, task.id)
        # Should suggest resolving blockers
        actions = [s.action for s in suggestions]
        assert "resolve" in actions


# ═══════════════════════════════════════════════════════════════════════════════
# SECURITY TESTS
# ═══════════════════════════════════════════════════════════════════════════════


from core.desktop.devtools.interface.cli_ai import (
    validate_task_id,
    validate_path,
    validate_string,
    validate_array,
    validate_subtasks_data,
    MAX_NESTING_DEPTH,
    MAX_STRING_LENGTH,
    MAX_ARRAY_LENGTH,
)


class TestValidateTaskId:
    """Security tests for task_id validation."""

    def test_valid_task_id(self):
        assert validate_task_id("TASK-001") is None
        assert validate_task_id("task_123") is None
        assert validate_task_id("MyTask-42") is None
        assert validate_task_id("a" * 64) is None

    def test_empty_task_id(self):
        err = validate_task_id("")
        assert err is not None
        assert "пустой" in err

    def test_non_string_task_id(self):
        err = validate_task_id(123)
        assert err is not None
        assert "строкой" in err

    def test_too_long_task_id(self):
        err = validate_task_id("a" * 65)
        assert err is not None
        assert "длинный" in err

    def test_path_traversal_dotdot(self):
        err = validate_task_id("../../../etc/passwd")
        assert err is not None
        assert "недопустимые" in err

    def test_path_traversal_slash(self):
        err = validate_task_id("task/evil")
        assert err is not None
        assert "недопустимые" in err

    def test_path_traversal_backslash(self):
        err = validate_task_id("task\\evil")
        assert err is not None
        assert "недопустимые" in err

    def test_invalid_characters(self):
        err = validate_task_id("task@evil!")
        assert err is not None
        assert "только буквы" in err


class TestValidatePath:
    """Security tests for subtask path validation."""

    def test_valid_paths(self):
        assert validate_path("0") is None
        assert validate_path("0.1") is None
        assert validate_path("0.1.2.3") is None
        assert validate_path("99.99.99") is None

    def test_none_path(self):
        err = validate_path(None)
        assert err is not None
        assert "не указан" in err

    def test_too_long_path(self):
        err = validate_path("0." * 100)
        assert err is not None

    def test_invalid_format(self):
        err = validate_path("abc")
        assert err is not None
        assert "формате" in err

    def test_path_traversal(self):
        err = validate_path("../0")
        assert err is not None

    def test_too_deep_nesting(self):
        deep_path = ".".join(str(i) for i in range(MAX_NESTING_DEPTH + 2))
        err = validate_path(deep_path)
        assert err is not None
        assert "глубокий" in err


class TestValidateString:
    """Security tests for string validation."""

    def test_valid_string(self):
        assert validate_string("Hello", "field") is None
        assert validate_string(None, "field") is None
        assert validate_string("a" * MAX_STRING_LENGTH, "field") is None

    def test_non_string(self):
        err = validate_string(123, "field")
        assert err is not None
        assert "строкой" in err

    def test_too_long_string(self):
        err = validate_string("a" * (MAX_STRING_LENGTH + 1), "field")
        assert err is not None
        assert "длинный" in err

    def test_custom_max_length(self):
        err = validate_string("abcdef", "field", max_length=5)
        assert err is not None
        assert "5" in err


class TestValidateArray:
    """Security tests for array validation."""

    def test_valid_array(self):
        assert validate_array(["a", "b", "c"], "field") is None
        assert validate_array(None, "field") is None
        assert validate_array([], "field") is None

    def test_non_array(self):
        err = validate_array("not_array", "field")
        assert err is not None
        assert "массивом" in err

    def test_too_long_array(self):
        err = validate_array(["a"] * (MAX_ARRAY_LENGTH + 1), "field")
        assert err is not None
        assert "длинный" in err


class TestValidateSubtasksData:
    """Security tests for subtasks structure validation."""

    def test_valid_subtasks(self):
        subtasks = [
            {"title": "Task 1", "criteria": ["c1"], "tests": ["t1"], "blockers": []},
            {"title": "Task 2", "criteria": ["c2"], "tests": [], "blockers": ["b1"]},
        ]
        assert validate_subtasks_data(subtasks) is None

    def test_non_dict_subtask(self):
        subtasks = ["not_a_dict"]
        err = validate_subtasks_data(subtasks)
        assert err is not None
        assert "объектом" in err

    def test_too_many_subtasks(self):
        subtasks = [{"title": f"Task {i}", "criteria": [], "tests": [], "blockers": []} for i in range(1001)]
        err = validate_subtasks_data(subtasks)
        assert err is not None
        assert "много" in err

    def test_deeply_nested_subtasks(self):
        # Create deeply nested structure
        subtask = {"title": "Deep", "criteria": [], "tests": [], "blockers": [], "children": []}
        current = subtask
        for i in range(MAX_NESTING_DEPTH + 2):
            child = {"title": f"Child {i}", "criteria": [], "tests": [], "blockers": [], "children": []}
            current["children"] = [child]
            current = child
        err = validate_subtasks_data([subtask])
        assert err is not None
        assert "вложенность" in err


class TestSecurityInHandlers:
    """Integration security tests for handlers."""

    def test_decompose_rejects_path_traversal_task_id(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        resp = handle_decompose(
            manager,
            {
                "intent": "decompose",
                "task": "../../../etc/passwd",
                "subtasks": [{"title": "Evil", "criteria": [], "tests": [], "blockers": []}],
            },
        )
        assert resp.success is False
        assert resp.error_code == "INVALID_TASK_ID"

    def test_define_rejects_invalid_path(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        task = manager.create_task(title="Test", priority="MEDIUM")
        task.description = "Test"
        manager.save_task(task)

        resp = handle_define(
            manager,
            {
                "intent": "define",
                "task": task.id,
                "path": "abc.def",  # Invalid path format
                "criteria": ["test"],
            },
        )
        assert resp.success is False
        assert resp.error_code == "INVALID_PATH"

    def test_verify_rejects_path_traversal_task_id(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        resp = handle_verify(
            manager,
            {
                "intent": "verify",
                "task": "task/../../secret",
                "path": "0",
                "checkpoints": {"criteria": {"confirmed": True}},
            },
        )
        assert resp.success is False
        assert resp.error_code == "INVALID_TASK_ID"

    def test_progress_rejects_invalid_task_id(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        resp = handle_progress(
            manager,
            {
                "intent": "progress",
                "task": "task\\..\\evil",
                "path": "0",
                "completed": True,
            },
        )
        assert resp.success is False
        assert resp.error_code == "INVALID_TASK_ID"

    def test_complete_rejects_invalid_status(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        task = manager.create_task(title="Test", priority="MEDIUM")
        task.description = "Test"
        manager.save_task(task)

        resp = handle_complete(
            manager,
            {
                "intent": "complete",
                "task": task.id,
                "status": "EVIL",  # Invalid status
            },
        )
        assert resp.success is False
        assert resp.error_code == "INVALID_STATUS"

    def test_create_rejects_invalid_priority(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        resp = handle_create(
            manager,
            {
                "intent": "create",
                "title": "Test Task",
                "priority": "EVIL_PRIORITY",
            },
        )
        assert resp.success is False
        assert resp.error_code == "INVALID_PRIORITY"

    def test_create_rejects_too_long_title(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        resp = handle_create(
            manager,
            {
                "intent": "create",
                "title": "a" * 501,  # Too long
            },
        )
        assert resp.success is False
        assert resp.error_code == "INVALID_TITLE"


# ═══════════════════════════════════════════════════════════════════════════════
# DRY-RUN TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestDryRunMode:
    """Tests for dry_run mode."""

    def test_dry_run_decompose_valid(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        task = manager.create_task(title="Test", priority="MEDIUM")
        task.description = "Test"
        manager.save_task(task)

        resp = process_intent(
            manager,
            {
                "intent": "decompose",
                "task": task.id,
                "subtasks": [{"title": "Sub", "criteria": [], "tests": [], "blockers": []}],
                "dry_run": True,
            },
        )
        assert resp.success is True
        assert resp.result["dry_run"] is True
        assert resp.result["would_execute"] is True
        assert resp.result["validation"]["subtasks_to_create"] == 1

    def test_dry_run_decompose_invalid_task(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        resp = process_intent(
            manager,
            {
                "intent": "decompose",
                "task": "NONEXISTENT",
                "subtasks": [{"title": "Sub"}],
                "dry_run": True,
            },
        )
        assert resp.success is True  # Dry-run always succeeds
        assert resp.result["dry_run"] is True
        assert resp.result["would_execute"] is False
        assert "не найдена" in resp.result["reason"]

    def test_dry_run_create_valid(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        resp = process_intent(
            manager,
            {
                "intent": "create",
                "title": "New Task",
                "dry_run": True,
            },
        )
        assert resp.success is True
        assert resp.result["dry_run"] is True
        assert resp.result["would_execute"] is True

    def test_dry_run_create_invalid_title(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        resp = process_intent(
            manager,
            {
                "intent": "create",
                "title": "",  # Empty title
                "dry_run": True,
            },
        )
        assert resp.success is True
        assert resp.result["dry_run"] is True
        assert resp.result["would_execute"] is False

    def test_dry_run_does_not_modify(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        task = manager.create_task(title="Test", priority="MEDIUM")
        task.description = "Test"
        manager.save_task(task)
        original_subtasks = len(task.subtasks)

        resp = process_intent(
            manager,
            {
                "intent": "decompose",
                "task": task.id,
                "subtasks": [{"title": "Sub1"}, {"title": "Sub2"}],
                "dry_run": True,
            },
        )
        assert resp.success is True

        # Reload and verify no changes
        task_reloaded = manager.load_task(task.id)
        assert len(task_reloaded.subtasks) == original_subtasks


# ═══════════════════════════════════════════════════════════════════════════════
# UNDO/REDO TESTS
# ═══════════════════════════════════════════════════════════════════════════════


from core.desktop.devtools.interface.cli_ai import (
    handle_undo,
    handle_redo,
    handle_history,
    handle_storage_info,
    handle_migrate,
    _get_history,
)


class TestUndoRedo:
    """Tests for undo/redo functionality."""

    def test_undo_nothing_to_undo(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        resp = handle_undo(manager, {"intent": "undo"})
        assert resp.success is False
        assert resp.error_code == "NOTHING_TO_UNDO"

    def test_redo_nothing_to_redo(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        resp = handle_redo(manager, {"intent": "redo"})
        assert resp.success is False
        assert resp.error_code == "NOTHING_TO_REDO"

    def test_history_empty(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        resp = handle_history(manager, {"intent": "history"})
        assert resp.success is True
        assert resp.result["operations"] == []
        assert resp.result["total"] == 0

    def test_history_with_limit(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        # Record some operations
        history = _get_history(tasks_dir)
        for i in range(5):
            history.record(intent=f"op_{i}", task_id=None, data={})

        resp = handle_history(manager, {"intent": "history", "limit": 3})
        assert resp.success is True
        assert len(resp.result["operations"]) == 3
        assert resp.result["total"] == 5


class TestStorageInfo:
    """Tests for storage info intent."""

    def test_storage_info(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        resp = handle_storage_info(manager, {"intent": "storage"})
        assert resp.success is True
        assert "global_storage" in resp.result
        assert "local_storage" in resp.result
        assert "current_storage" in resp.result


class TestMigrate:
    """Tests for migrate intent."""

    def test_migrate_no_local_tasks(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        resp = handle_migrate(manager, {"intent": "migrate", "project_dir": str(tmp_path / "nonexistent")})
        assert resp.success is False
        assert resp.error_code == "NO_LOCAL_TASKS"

    def test_migrate_dry_run(self, tmp_path):
        # Create local .tasks
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        local_tasks = project_dir / ".tasks"
        local_tasks.mkdir()
        (local_tasks / "TASK-001.task").write_text("task content")

        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        resp = handle_migrate(
            manager,
            {"intent": "migrate", "project_dir": str(project_dir), "dry_run": True},
        )
        assert resp.success is True
        assert resp.result["dry_run"] is True
        assert resp.result["would_migrate"]["task_count"] == 1


class TestIdempotency:
    """Tests for idempotency support."""

    def test_idempotency_cache_returns_cached(self, tmp_path):
        from core.desktop.devtools.interface.cli_ai import (
            clear_idempotency_cache,
            _check_idempotency,
            _store_idempotency,
        )
        clear_idempotency_cache()

        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        # First call - should execute
        resp1 = process_intent(
            manager,
            {"intent": "create", "title": "Test", "idempotency_key": "test-key-1"},
        )
        assert resp1.success is True
        assert resp1.cached is False

        # Second call with same key - should return cached
        resp2 = process_intent(
            manager,
            {"intent": "create", "title": "Different", "idempotency_key": "test-key-1"},
        )
        assert resp2.success is True
        assert resp2.cached is True

        clear_idempotency_cache()

    def test_idempotency_different_keys_execute(self, tmp_path):
        from core.desktop.devtools.interface.cli_ai import clear_idempotency_cache
        clear_idempotency_cache()

        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        resp1 = process_intent(
            manager,
            {"intent": "create", "title": "Task1", "idempotency_key": "key-a"},
        )
        resp2 = process_intent(
            manager,
            {"intent": "create", "title": "Task2", "idempotency_key": "key-b"},
        )

        assert resp1.success is True
        assert resp2.success is True
        assert resp1.cached is False
        assert resp2.cached is False

        clear_idempotency_cache()

    def test_clear_idempotency_cache(self, tmp_path):
        from core.desktop.devtools.interface.cli_ai import clear_idempotency_cache

        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        process_intent(
            manager,
            {"intent": "create", "title": "Test", "idempotency_key": "to-clear"},
        )

        count = clear_idempotency_cache()
        assert count >= 1


class TestAtomicBatch:
    """Tests for atomic batch operations."""

    def test_atomic_batch_rollback_on_failure(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        # Create task
        task = manager.create_task(title="Test Task", status="FAIL")
        manager.save_task(task)
        task_id = task.id

        # Read original content
        task_file = tasks_dir / f"{task_id}.task"
        original_content = task_file.read_text()

        # Atomic batch with failure
        resp = handle_batch(
            manager,
            {
                "intent": "batch",
                "task": task_id,
                "atomic": True,
                "operations": [
                    {"intent": "decompose", "subtasks": [{"title": "Sub1"}]},
                    {"intent": "verify", "path": "999"},  # Will fail
                ],
            },
        )

        assert resp.success is False
        assert resp.result["rolled_back"] is True

        # File should be restored
        restored_content = task_file.read_text()
        assert restored_content == original_content

    def test_non_atomic_batch_keeps_partial(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        # Create task
        task = manager.create_task(title="Test Task", status="FAIL")
        manager.save_task(task)
        task_id = task.id

        # Non-atomic batch with failure
        resp = handle_batch(
            manager,
            {
                "intent": "batch",
                "task": task_id,
                "atomic": False,
                "operations": [
                    {"intent": "decompose", "subtasks": [{
                        "title": "Sub1",
                        "criteria": ["Done"],
                        "tests": ["test"],
                        "blockers": ["none"],
                    }]},
                    {"intent": "verify", "path": "999"},  # Will fail
                ],
            },
        )

        assert resp.success is False
        assert resp.result["rolled_back"] is False
        assert resp.result["completed"] == 1  # First op succeeded
        # Verify first operation reported success
        assert resp.result["operations"][0]["success"] is True

    def test_atomic_batch_success(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        # Create task
        task = manager.create_task(title="Test Task", status="FAIL")
        manager.save_task(task)
        task_id = task.id

        # Atomic batch - all succeed (subtasks need criteria, tests, blockers)
        resp = handle_batch(
            manager,
            {
                "intent": "batch",
                "task": task_id,
                "atomic": True,
                "operations": [
                    {"intent": "decompose", "subtasks": [{
                        "title": "Sub1",
                        "criteria": ["Done"],
                        "tests": ["test"],
                        "blockers": ["none"],
                    }]},
                    {"intent": "context"},
                ],
            },
        )

        assert resp.success is True
        assert resp.result["rolled_back"] is False
        assert resp.result["completed"] == 2


class TestMetaContext:
    """Tests for meta context in responses."""

    def test_meta_included_in_response(self, tmp_path):
        from core.desktop.devtools.interface.cli_ai import build_meta, Meta

        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        # Create task
        task = manager.create_task(title="Test", status="FAIL")
        manager.save_task(task)

        meta = build_meta(manager, task.id)

        assert meta.task_id == task.id
        assert meta.task_status == "TODO"
        assert meta.task_status_code == "FAIL"
        assert isinstance(meta, Meta)

    def test_meta_to_dict(self):
        from core.desktop.devtools.interface.cli_ai import Meta

        meta = Meta(
            task_id="T-1",
            task_status="TODO",
            task_status_code="FAIL",
            task_progress=50,
            subtasks_total=4,
            subtasks_completed=2,
            pending_verifications=1,
            unresolved_blockers=0,
            next_action_hint="verify criteria",
        )
        d = meta.to_dict()

        assert d["task_id"] == "T-1"
        assert d["task_progress"] == 50
        assert d["subtasks"]["total"] == 4
        assert d["subtasks"]["completed"] == 2
        assert d["next_action_hint"] == "verify criteria"

    def test_process_intent_adds_meta(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        tasks_dir.mkdir()
        manager = TaskManager(tasks_dir=tasks_dir)

        # Create task
        task = manager.create_task(title="Test", status="FAIL")
        manager.save_task(task)

        resp = process_intent(
            manager,
            {"intent": "decompose", "task": task.id, "subtasks": [{
                "title": "Sub",
                "criteria": ["Done"],
                "tests": ["test"],
                "blockers": ["none"],
            }]},
        )

        assert resp.success is True
        assert resp.meta is not None
        assert resp.meta.task_id == task.id


class TestErrorDetail:
    """Tests for ErrorDetail in responses."""

    def test_error_response_includes_detail(self):
        from core.desktop.devtools.interface.cli_ai import ErrorDetail

        resp = error_response(
            "test",
            "TEST_ERROR",
            "Test message",
            field="some_field",
            expected="string",
            got="number",
            recovery_action="retry",
            recovery_hint={"fixed": True},
        )

        assert resp.success is False
        assert resp.error is not None
        assert resp.error.code == "TEST_ERROR"
        assert resp.error.field == "some_field"
        assert resp.error.expected == "string"
        assert resp.error.recovery_action == "retry"

    def test_error_detail_to_dict(self):
        from core.desktop.devtools.interface.cli_ai import ErrorDetail

        err = ErrorDetail(
            code="TEST",
            message="Test",
            recoverable=True,
            field="x",
            expected="int",
            got="str",
            recovery_action="fix",
            recovery_hint={"value": 1},
        )
        d = err.to_dict()

        assert d["code"] == "TEST"
        assert d["field"] == "x"
        assert d["recovery"]["action"] == "fix"
        assert d["recovery"]["hint"] == {"value": 1}
