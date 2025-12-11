"""Security tests for path traversal prevention."""

import pytest
from pathlib import Path

from core.desktop.devtools.application.context import normalize_task_id
from infrastructure.file_repository import FileTaskRepository


class TestNormalizeTaskIdSecurity:
    """Test path traversal prevention in normalize_task_id."""

    def test_rejects_dotdot(self):
        with pytest.raises(ValueError, match="forbidden characters"):
            normalize_task_id("../../../etc/passwd")

    def test_rejects_slash(self):
        with pytest.raises(ValueError, match="forbidden characters"):
            normalize_task_id("task/evil")

    def test_rejects_backslash(self):
        with pytest.raises(ValueError, match="forbidden characters"):
            normalize_task_id("task\\evil")

    def test_accepts_valid_task_id(self):
        assert normalize_task_id("TASK-001") == "TASK-001"
        assert normalize_task_id("task-42") == "TASK-042"
        assert normalize_task_id("123") == "TASK-123"
        assert normalize_task_id("MY-CUSTOM-ID") == "MY-CUSTOM-ID"


class TestFileRepositorySecurity:
    """Test path traversal prevention in FileTaskRepository."""

    def test_rejects_dotdot_in_task_id(self, tmp_path):
        repo = FileTaskRepository(tasks_dir=tmp_path)
        with pytest.raises(ValueError, match="path traversal"):
            repo._resolve_path("../../../etc/passwd")

    def test_rejects_slash_in_task_id(self, tmp_path):
        repo = FileTaskRepository(tasks_dir=tmp_path)
        with pytest.raises(ValueError, match="path traversal"):
            repo._resolve_path("task/evil")

    def test_rejects_backslash_in_task_id(self, tmp_path):
        repo = FileTaskRepository(tasks_dir=tmp_path)
        with pytest.raises(ValueError, match="path traversal"):
            repo._resolve_path("task\\evil")

    def test_rejects_dotdot_in_domain(self, tmp_path):
        repo = FileTaskRepository(tasks_dir=tmp_path)
        with pytest.raises(ValueError, match="path traversal"):
            repo._resolve_path("TASK-001", "../../../etc")

    def test_rejects_absolute_domain(self, tmp_path):
        repo = FileTaskRepository(tasks_dir=tmp_path)
        with pytest.raises(ValueError, match="path traversal"):
            repo._resolve_path("TASK-001", "/etc/passwd")

    def test_accepts_valid_paths(self, tmp_path):
        repo = FileTaskRepository(tasks_dir=tmp_path)
        # Should not raise
        path = repo._resolve_path("TASK-001")
        assert path.name == "TASK-001.task"
        assert path.is_relative_to(tmp_path)

    def test_accepts_valid_domain(self, tmp_path):
        repo = FileTaskRepository(tasks_dir=tmp_path)
        path = repo._resolve_path("TASK-001", "backend")
        assert "backend" in str(path)
        assert path.is_relative_to(tmp_path)

    def test_accepts_nested_domain(self, tmp_path):
        repo = FileTaskRepository(tasks_dir=tmp_path)
        # Forward slashes in domain are OK - they create subdirectories
        # Security is ensured by is_relative_to check
        path = repo._resolve_path("TASK-001", "backend/api")
        assert "backend" in str(path)
        assert "api" in str(path)
        assert path.is_relative_to(tmp_path)
