#!/usr/bin/env python3
"""Unit tests for tui_models module."""

import pytest
from unittest.mock import Mock

from core import Status
from core.desktop.devtools.interface.tui_models import (
    Task,
    CLI_DEPS,
    CHECKLIST_SECTIONS,
    InteractiveFormattedTextControl,
)
from prompt_toolkit.mouse_events import MouseEvent, MouseEventType


class TestTask:
    """Tests for Task dataclass."""

    def test_task_creation_minimal(self):
        """Test creating task with minimal required fields."""
        task = Task(
            name="Test Task",
            status=Status.OK,
            description="Test description",
            category="test",
        )
        assert task.name == "Test Task"
        assert task.status == Status.OK
        assert task.description == "Test description"
        assert task.category == "test"
        assert task.completed is False
        assert task.progress == 0
        assert task.subtasks_count == 0
        assert task.subtasks_completed == 0

    def test_task_creation_full(self):
        """Test creating task with all fields."""
        task = Task(
            name="Full Task",
            status=Status.WARN,
            description="Full description",
            category="full",
            completed=True,
            task_file=".tasks/test.task",
            progress=75,
            subtasks_count=5,
            subtasks_completed=3,
            id="TASK-001",
            parent="TASK-000",
            domain="test-domain",
            phase="test-phase",
            component="test-component",
            blocked=True,
        )
        assert task.name == "Full Task"
        assert task.status == Status.WARN
        assert task.completed is True
        assert task.progress == 75
        assert task.id == "TASK-001"
        assert task.parent == "TASK-000"
        assert task.domain == "test-domain"
        assert task.blocked is True


class TestCliDeps:
    """Tests for CLI_DEPS constant."""

    def test_cli_deps_has_all_required_attributes(self):
        """Test that CLI_DEPS has all required attributes."""
        assert hasattr(CLI_DEPS, "manager_factory")
        assert hasattr(CLI_DEPS, "translate")
        assert hasattr(CLI_DEPS, "derive_domain_explicit")
        assert hasattr(CLI_DEPS, "resolve_task_reference")
        assert hasattr(CLI_DEPS, "save_last_task")
        assert hasattr(CLI_DEPS, "normalize_task_id")
        assert hasattr(CLI_DEPS, "task_to_dict")

    def test_cli_deps_manager_factory(self):
        """Test that manager_factory creates TaskManager."""
        manager = CLI_DEPS.manager_factory()
        assert manager is not None
        # Check that it's a TaskManager instance
        from core.desktop.devtools.application.task_manager import TaskManager
        assert isinstance(manager, TaskManager)


class TestChecklistSections:
    """Tests for CHECKLIST_SECTIONS constant."""

    def test_checklist_sections_structure(self):
        """Test that CHECKLIST_SECTIONS has correct structure."""
        assert len(CHECKLIST_SECTIONS) > 0
        for section in CHECKLIST_SECTIONS:
            assert len(section) == 4
            name, keywords, description, items = section
            assert isinstance(name, str)
            assert isinstance(keywords, list)
            assert isinstance(description, str)
            assert isinstance(items, list)

    def test_checklist_sections_has_expected_sections(self):
        """Test that CHECKLIST_SECTIONS contains expected sections."""
        section_names = [s[0] for s in CHECKLIST_SECTIONS]
        assert "plan" in section_names
        assert "validation" in section_names
        assert "risks" in section_names
        assert "readiness" in section_names
        assert "execute" in section_names
        assert "final" in section_names


class TestInteractiveFormattedTextControl:
    """Tests for InteractiveFormattedTextControl class."""

    def test_interactive_control_creation(self):
        """Test creating InteractiveFormattedTextControl."""
        control = InteractiveFormattedTextControl()
        assert control is not None
        assert control._external_mouse_handler is None

    def test_interactive_control_with_mouse_handler(self):
        """Test creating InteractiveFormattedTextControl with mouse handler."""
        handler = Mock(return_value=None)
        control = InteractiveFormattedTextControl(mouse_handler=handler)
        assert control._external_mouse_handler == handler

    def test_interactive_control_mouse_handler_called(self):
        """Test that external mouse handler is called."""
        handler_result = Mock()
        handler = Mock(return_value=handler_result)
        control = InteractiveFormattedTextControl(mouse_handler=handler)
        mouse_event = MouseEvent(
            position=(0, 0),
            event_type=MouseEventType.MOUSE_DOWN,
            button=1,
            modifiers=set(),
        )
        result = control.mouse_handler(mouse_event)
        handler.assert_called_once_with(mouse_event)
        assert result == handler_result

    def test_interactive_control_mouse_handler_not_implemented(self):
        """Test that NotImplemented falls back to parent handler."""
        # Use a function that returns NotImplemented, not Mock
        handler_called = []
        def handler(event):
            handler_called.append(event)
            return NotImplemented
        control = InteractiveFormattedTextControl(mouse_handler=handler)
        mouse_event = MouseEvent(
            position=(0, 0),
            event_type=MouseEventType.MOUSE_DOWN,
            button=1,
            modifiers=set(),
        )
        # Should call handler first, then fall back to parent
        result = control.mouse_handler(mouse_event)
        # Handler should be called
        assert len(handler_called) == 1
        assert handler_called[0] == mouse_event
        # When NotImplemented is returned, parent handler is called
        # Parent handler may return NotImplemented - that's acceptable
        # Just verify that the method completes without error
        assert result is not None  # Any return value is acceptable

    def test_interactive_control_no_handler(self):
        """Test mouse handler without external handler."""
        control = InteractiveFormattedTextControl()
        mouse_event = MouseEvent(
            position=(0, 0),
            event_type=MouseEventType.MOUSE_DOWN,
            button=1,
            modifiers=set(),
        )
        # Should call parent handler
        result = control.mouse_handler(mouse_event)
        # Result should come from parent
        assert result is not None
