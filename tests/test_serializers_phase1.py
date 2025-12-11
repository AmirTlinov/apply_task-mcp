"""Tests for Phase 1 serializer enhancements."""

import pytest
from core.subtask import SubTask
from core.task_detail import TaskDetail
from core.desktop.devtools.interface.serializers import subtask_to_dict, task_to_dict


class TestSubtaskSerializerPhase1:
    """Test Phase 1 fields in subtask serialization."""

    def test_full_mode_includes_phase1_fields(self):
        """Full mode should include all Phase 1 fields."""
        subtask = SubTask(
            completed=False,
            title="Test subtask",
            progress_notes=["note1", "note2"],
            started_at="2025-01-01T10:00:00",
            blocked=True,
            block_reason="Waiting for API",
        )

        result = subtask_to_dict(subtask, path="0", compact=False)

        # Verify Phase 1 fields are present
        assert "progress_notes" in result
        assert "started_at" in result
        assert "blocked" in result
        assert "block_reason" in result
        assert "computed_status" in result

        # Verify values
        assert result["progress_notes"] == ["note1", "note2"]
        assert result["started_at"] == "2025-01-01T10:00:00"
        assert result["blocked"] is True
        assert result["block_reason"] == "Waiting for API"
        assert result["computed_status"] == "blocked"

    def test_full_mode_default_values(self):
        """Full mode should use getattr with defaults for backward compatibility."""
        subtask = SubTask(
            completed=False,
            title="Test subtask",
        )

        result = subtask_to_dict(subtask, path="0", compact=False)

        # Verify defaults
        assert result["progress_notes"] == []
        assert result["started_at"] is None
        assert result["blocked"] is False
        assert result["block_reason"] == ""
        assert result["computed_status"] == "pending"

    def test_compact_mode_includes_status(self):
        """Compact mode should include computed_status."""
        subtask = SubTask(
            completed=False,
            title="Test subtask",
            progress_notes=["working on it"],
        )

        result = subtask_to_dict(subtask, path="0", compact=True)

        # Should have status field
        assert "status" in result
        assert result["status"] == "in_progress"

    def test_compact_mode_blocked_flag(self):
        """Compact mode should include blocked flag when true."""
        subtask = SubTask(
            completed=False,
            title="Test subtask",
            blocked=True,
            block_reason="Waiting for review",
        )

        result = subtask_to_dict(subtask, path="0", compact=True)

        # Should have blocked fields
        assert result["blocked"] is True
        assert result["block_reason"] == "Waiting for review"
        assert result["status"] == "blocked"

    def test_compact_mode_blocked_without_reason(self):
        """Compact mode should include blocked flag even without reason."""
        subtask = SubTask(
            completed=False,
            title="Test subtask",
            blocked=True,
            block_reason="",
        )

        result = subtask_to_dict(subtask, path="0", compact=True)

        # Should have blocked flag but not reason
        assert result["blocked"] is True
        assert "block_reason" not in result  # Empty reason not included

    def test_compact_mode_not_blocked(self):
        """Compact mode should not include blocked fields when false."""
        subtask = SubTask(
            completed=False,
            title="Test subtask",
            blocked=False,
        )

        result = subtask_to_dict(subtask, path="0", compact=True)

        # Should not have blocked fields
        assert "blocked" not in result

    def test_computed_status_priority(self):
        """Test computed_status reflects correct priority: completed > blocked > in_progress > pending."""
        # Pending
        subtask = SubTask(completed=False, title="Test")
        assert subtask_to_dict(subtask, compact=True)["status"] == "pending"

        # In progress (has progress_notes)
        subtask = SubTask(completed=False, title="Test", progress_notes=["note"])
        assert subtask_to_dict(subtask, compact=True)["status"] == "in_progress"

        # Blocked (takes priority over in_progress)
        subtask = SubTask(
            completed=False,
            title="Test",
            progress_notes=["note"],
            blocked=True,
        )
        assert subtask_to_dict(subtask, compact=True)["status"] == "blocked"

        # Completed (takes priority over all)
        subtask = SubTask(
            completed=True,
            title="Test",
            progress_notes=["note"],
            blocked=True,
        )
        assert subtask_to_dict(subtask, compact=True)["status"] == "completed"

    def test_backward_compatibility_with_getattr(self):
        """Serializer should handle missing fields gracefully."""
        # Create a minimal subtask (simulating old data)
        subtask = SubTask(completed=False, title="Test")

        # Remove Phase 1 fields to simulate old data
        if hasattr(subtask, "progress_notes"):
            delattr(subtask, "progress_notes")
        if hasattr(subtask, "started_at"):
            delattr(subtask, "started_at")
        if hasattr(subtask, "blocked"):
            delattr(subtask, "blocked")
        if hasattr(subtask, "block_reason"):
            delattr(subtask, "block_reason")

        # Full mode should not crash
        result_full = subtask_to_dict(subtask, compact=False)
        assert result_full["progress_notes"] == []
        assert result_full["started_at"] is None
        assert result_full["blocked"] is False
        assert result_full["block_reason"] == ""

        # Compact mode should not crash
        result_compact = subtask_to_dict(subtask, compact=True)
        assert result_compact["status"] == "pending"

    def test_in_progress_detection_by_started_at(self):
        """Subtask should be in_progress if started_at is set."""
        subtask = SubTask(
            completed=False,
            title="Test",
            started_at="2025-01-01T10:00:00",
        )

        result = subtask_to_dict(subtask, compact=True)
        assert result["status"] == "in_progress"

    def test_in_progress_detection_by_criteria_confirmed(self):
        """Subtask should be in_progress if criteria_confirmed is set."""
        subtask = SubTask(
            completed=False,
            title="Test",
            criteria_confirmed=True,
        )

        result = subtask_to_dict(subtask, compact=True)
        assert result["status"] == "in_progress"

    def test_progress_notes_as_list(self):
        """Progress notes should be serialized as list."""
        subtask = SubTask(
            completed=False,
            title="Test",
            progress_notes=["note1", "note2", "note3"],
        )

        result = subtask_to_dict(subtask, compact=False)
        assert isinstance(result["progress_notes"], list)
        assert len(result["progress_notes"]) == 3
        assert result["progress_notes"] == ["note1", "note2", "note3"]


class TestTaskSerializerPhase1:
    """Test task serialization with Phase 1 subtasks."""

    def test_task_with_phase1_subtasks_compact(self):
        """Task with Phase 1 subtasks should serialize correctly in compact mode."""
        task = TaskDetail(
            id="TEST-1",
            title="Test task",
            status="in_progress",
        )
        task.subtasks = [
            SubTask(
                completed=False,
                title="Subtask 1",
                blocked=True,
                block_reason="Waiting",
            ),
            SubTask(
                completed=False,
                title="Subtask 2",
                progress_notes=["note"],
            ),
        ]

        result = task_to_dict(task, include_subtasks=True, compact=True)

        assert len(result["subtasks"]) == 2

        # First subtask should be blocked
        assert result["subtasks"][0]["status"] == "blocked"
        assert result["subtasks"][0]["blocked"] is True
        assert result["subtasks"][0]["block_reason"] == "Waiting"

        # Second subtask should be in_progress
        assert result["subtasks"][1]["status"] == "in_progress"

    def test_task_with_phase1_subtasks_full(self):
        """Task with Phase 1 subtasks should serialize correctly in full mode."""
        task = TaskDetail(
            id="TEST-1",
            title="Test task",
            status="in_progress",
        )
        task.subtasks = [
            SubTask(
                completed=False,
                title="Subtask 1",
                progress_notes=["note1", "note2"],
                started_at="2025-01-01T10:00:00",
                blocked=True,
                block_reason="Blocked",
            ),
        ]

        result = task_to_dict(task, include_subtasks=True, compact=False)

        subtask_data = result["subtasks"][0]
        assert subtask_data["progress_notes"] == ["note1", "note2"]
        assert subtask_data["started_at"] == "2025-01-01T10:00:00"
        assert subtask_data["blocked"] is True
        assert subtask_data["block_reason"] == "Blocked"
        assert subtask_data["computed_status"] == "blocked"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
