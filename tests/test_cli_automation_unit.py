#!/usr/bin/env python3
"""Unit tests for cli_automation module."""

import json
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from types import SimpleNamespace

from core.desktop.devtools.interface.cli_automation import (
    AUTOMATION_TMP,
    _ensure_tmp_dir,
    _write_json,
    _automation_subtask_entry,
    _automation_template_payload,
    _resolve_parent,
    _load_note,
    cmd_automation_task_template,
    cmd_automation_projects_health,
    cmd_automation_health,
)


class TestEnsureTmpDir:
    """Tests for _ensure_tmp_dir function."""

    def test_ensure_tmp_dir_creates_directory(self, tmp_path, monkeypatch):
        """Test that _ensure_tmp_dir creates directory."""
        test_tmp = tmp_path / "test_tmp"
        monkeypatch.setattr("core.desktop.devtools.interface.cli_automation.AUTOMATION_TMP", test_tmp)
        result = _ensure_tmp_dir()
        assert result == test_tmp
        assert test_tmp.exists()
        assert test_tmp.is_dir()

    def test_ensure_tmp_dir_idempotent(self, tmp_path, monkeypatch):
        """Test that _ensure_tmp_dir is idempotent."""
        test_tmp = tmp_path / "test_tmp"
        test_tmp.mkdir()
        monkeypatch.setattr("core.desktop.devtools.interface.cli_automation.AUTOMATION_TMP", test_tmp)
        result1 = _ensure_tmp_dir()
        result2 = _ensure_tmp_dir()
        assert result1 == result2 == test_tmp


class TestWriteJson:
    """Tests for _write_json function."""

    def test_write_json_creates_file(self, tmp_path):
        """Test that _write_json creates file."""
        output_file = tmp_path / "output.json"
        data = {"key": "value", "number": 42}
        _write_json(output_file, data)
        assert output_file.exists()
        content = json.loads(output_file.read_text(encoding="utf-8"))
        assert content == data

    def test_write_json_creates_parent_dirs(self, tmp_path):
        """Test that _write_json creates parent directories."""
        output_file = tmp_path / "nested" / "deep" / "output.json"
        data = {"test": "data"}
        _write_json(output_file, data)
        assert output_file.exists()
        assert output_file.parent.exists()

    def test_write_json_handles_unicode(self, tmp_path):
        """Test that _write_json handles unicode correctly."""
        output_file = tmp_path / "unicode.json"
        data = {"text": "Привет мир", "emoji": "🚀"}
        _write_json(output_file, data)
        content = json.loads(output_file.read_text(encoding="utf-8"))
        assert content == data


class TestAutomationSubtaskEntry:
    """Tests for _automation_subtask_entry function."""

    def test_automation_subtask_entry_structure(self):
        """Test that _automation_subtask_entry returns correct structure."""
        entry = _automation_subtask_entry(1, 85, "performance", "p95<=100ms")
        assert isinstance(entry, dict)
        assert "title" in entry
        assert "criteria" in entry
        assert "tests" in entry
        assert "blockers" in entry

    def test_automation_subtask_entry_content(self):
        """Test that _automation_subtask_entry has correct content."""
        entry = _automation_subtask_entry(2, 90, "deps", "sla")
        assert "Subtask 2" in entry["title"]
        assert "Coverage ≥90%" in entry["criteria"]
        assert "deps" in entry["blockers"][1]
        assert "90%" in entry["tests"][0]


class TestAutomationTemplatePayload:
    """Tests for _automation_template_payload function."""

    def test_automation_template_payload_structure(self):
        """Test that _automation_template_payload returns correct structure."""
        payload = _automation_template_payload(5, 80, "risk", "sla")
        assert isinstance(payload, dict)
        assert "defaults" in payload
        assert "usage" in payload
        assert "subtasks" in payload

    def test_automation_template_payload_defaults(self):
        """Test that _automation_template_payload has correct defaults."""
        payload = _automation_template_payload(3, 85, "perf", "p95")
        assert payload["defaults"]["coverage"] == 85
        assert payload["defaults"]["risks"] == "perf"
        assert payload["defaults"]["sla"] == "p95"

    def test_automation_template_payload_minimum_count(self):
        """Test that _automation_template_payload enforces minimum count."""
        payload = _automation_template_payload(1, 80, "r", "s")
        # Should enforce minimum of 3
        assert len(payload["subtasks"]) >= 3

    def test_automation_template_payload_subtasks_count(self):
        """Test that _automation_template_payload creates correct number of subtasks."""
        payload = _automation_template_payload(5, 80, "r", "s")
        assert len(payload["subtasks"]) == 5


class TestResolveParent:
    """Tests for _resolve_parent function."""

    @patch("core.desktop.devtools.interface.cli_automation.normalize_task_id")
    def test_resolve_parent_with_provided(self, mock_normalize):
        """Test _resolve_parent with provided parent."""
        mock_normalize.return_value = "TASK-001"
        result = _resolve_parent("TASK-001")
        assert result == "TASK-001"
        mock_normalize.assert_called_once_with("TASK-001")

    @patch("core.desktop.devtools.interface.cli_automation.get_last_task")
    @patch("core.desktop.devtools.interface.cli_automation.normalize_task_id")
    def test_resolve_parent_from_last_task(self, mock_normalize, mock_get_last):
        """Test _resolve_parent falls back to last task."""
        mock_get_last.return_value = ("TASK-002", "")
        mock_normalize.return_value = "TASK-002"
        result = _resolve_parent(None)
        assert result == "TASK-002"
        mock_get_last.assert_called_once()

    @patch("core.desktop.devtools.interface.cli_automation.get_last_task")
    @patch("core.desktop.devtools.interface.cli_automation.normalize_task_id")
    def test_resolve_parent_no_parent(self, mock_normalize, mock_get_last):
        """Test _resolve_parent when no parent available."""
        mock_get_last.return_value = ("", "")
        mock_normalize.return_value = None
        result = _resolve_parent(None)
        assert result is None


class TestLoadNote:
    """Tests for _load_note function."""

    def test_load_note_from_file(self, tmp_path):
        """Test _load_note loads from file."""
        log_file = tmp_path / "log.txt"
        log_file.write_text("Test note content", encoding="utf-8")
        result = _load_note(log_file, "fallback")
        assert result == "Test note content"

    def test_load_note_fallback_when_file_missing(self, tmp_path):
        """Test _load_note uses fallback when file missing."""
        log_file = tmp_path / "missing.txt"
        result = _load_note(log_file, "fallback text")
        assert result == "fallback text"

    def test_load_note_fallback_when_file_empty(self, tmp_path):
        """Test _load_note uses fallback when file empty."""
        log_file = tmp_path / "empty.txt"
        log_file.write_text("", encoding="utf-8")
        result = _load_note(log_file, "fallback")
        assert result == "fallback"

    def test_load_note_truncates_long_content(self, tmp_path):
        """Test _load_note truncates content longer than 1000 chars."""
        log_file = tmp_path / "long.txt"
        long_content = "x" * 1500
        log_file.write_text(long_content, encoding="utf-8")
        result = _load_note(log_file, "fallback")
        assert len(result) == 1000
        assert result == long_content[:1000]


class TestCmdAutomationTaskTemplate:
    """Tests for cmd_automation_task_template function."""

    def test_cmd_automation_task_template_success(self, tmp_path, monkeypatch):
        """Test successful template generation."""
        output_file = tmp_path / "template.json"
        monkeypatch.setattr("core.desktop.devtools.interface.cli_automation.AUTOMATION_TMP", tmp_path)
        args = SimpleNamespace(
            count=3,
            coverage=85,
            risks="perf",
            sla="p95",
            output=str(output_file),
        )
        result = cmd_automation_task_template(args)
        assert result == 0
        assert output_file.exists()
        data = json.loads(output_file.read_text(encoding="utf-8"))
        assert len(data["subtasks"]) == 3

    def test_cmd_automation_task_template_default_output(self, tmp_path, monkeypatch):
        """Test template generation with default output path."""
        monkeypatch.setattr("core.desktop.devtools.interface.cli_automation.AUTOMATION_TMP", tmp_path)
        args = SimpleNamespace(
            count=4,
            coverage=90,
            risks="deps",
            sla="sla",
            output=None,
        )
        result = cmd_automation_task_template(args)
        assert result == 0
        default_output = tmp_path / "subtasks.template.json"
        assert default_output.exists()


class TestCmdAutomationProjectsHealth:
    """Tests for cmd_automation_projects_health function."""

    @patch("core.desktop.devtools.interface.cli_automation._projects_status_payload")
    def test_cmd_automation_projects_health(self, mock_status):
        """Test projects health command."""
        mock_status.return_value = {
            "target_label": "test-project",
            "auto_sync": True,
            "token_present": True,
            "rate_remaining": 100,
            "rate_reset_human": "2025-01-01",
            "status_reason": "OK",
        }
        args = SimpleNamespace()
        result = cmd_automation_projects_health(args)
        assert result == 0
        mock_status.assert_called_once_with(force_refresh=True)


class TestCmdAutomationHealth:
    """Tests for cmd_automation_health function."""

    @patch("core.desktop.devtools.interface.cli_automation.subprocess.run")
    def test_cmd_automation_health_success(self, mock_run, tmp_path, monkeypatch):
        """Test health command with successful pytest."""
        monkeypatch.setattr("core.desktop.devtools.interface.cli_automation.AUTOMATION_TMP", tmp_path)
        mock_proc = Mock()
        mock_proc.returncode = 0
        mock_proc.stdout = "pytest output"
        mock_proc.stderr = ""
        mock_run.return_value = mock_proc
        args = SimpleNamespace(
            log=None,
            pytest_cmd="pytest -q",
        )
        result = cmd_automation_health(args)
        assert result == 0
        mock_run.assert_called_once()

    @patch("core.desktop.devtools.interface.cli_automation.subprocess.run")
    def test_cmd_automation_health_failure(self, mock_run, tmp_path, monkeypatch):
        """Test health command with failed pytest."""
        monkeypatch.setattr("core.desktop.devtools.interface.cli_automation.AUTOMATION_TMP", tmp_path)
        # Create a mock result object
        from types import SimpleNamespace as NS
        mock_result = NS(returncode=1, stdout="", stderr="test failures")
        mock_run.return_value = mock_result
        args = SimpleNamespace(
            log=None,
            pytest_cmd="pytest -q",
        )
        result = cmd_automation_health(args)
        # Function returns exit_code from structured_response (1 for ERROR)
        assert result == 1
        # Verify that subprocess was called
        mock_run.assert_called_once()
        # Verify log file was created with error result
        log_file = tmp_path / "health.log"
        assert log_file.exists()
        import json
        log_data = json.loads(log_file.read_text(encoding="utf-8"))
        assert log_data["rc"] == 1
        assert log_data["stderr"] == "test failures"

    def test_cmd_automation_health_empty_cmd(self, tmp_path, monkeypatch):
        """Test health command with empty pytest command."""
        monkeypatch.setattr("core.desktop.devtools.interface.cli_automation.AUTOMATION_TMP", tmp_path)
        args = SimpleNamespace(
            log=None,
            pytest_cmd="",
        )
        result = cmd_automation_health(args)
        assert result == 0
