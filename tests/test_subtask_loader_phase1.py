import json

import pytest

from core.desktop.devtools.interface import subtask_loader
from core.subtask import SubTask


def test_load_progress_notes():
    """Test loading progress_notes as a list."""
    raw = json.dumps(
        [
            {
                "title": "Subtask with progress notes long title",
                "criteria": ["c"],
                "tests": ["t"],
                "blockers": ["b"],
                "progress_notes": ["Started implementation", "Fixed bug #123"],
            }
        ]
    )
    subtasks = subtask_loader.parse_subtasks_json(raw)
    st = subtasks[0]
    assert st.progress_notes == ["Started implementation", "Fixed bug #123"]


def test_load_started_at():
    """Test loading started_at as datetime string."""
    raw = json.dumps(
        [
            {
                "title": "Subtask with started_at timestamp",
                "criteria": ["c"],
                "tests": ["t"],
                "blockers": ["b"],
                "started_at": "2025-12-11T10:30:00Z",
            }
        ]
    )
    subtasks = subtask_loader.parse_subtasks_json(raw)
    st = subtasks[0]
    assert st.started_at == "2025-12-11T10:30:00Z"


def test_load_blocked_true():
    """Test loading blocked=True."""
    raw = json.dumps(
        [
            {
                "title": "Blocked subtask with long title",
                "criteria": ["c"],
                "tests": ["t"],
                "blockers": ["b"],
                "blocked": True,
            }
        ]
    )
    subtasks = subtask_loader.parse_subtasks_json(raw)
    st = subtasks[0]
    assert st.blocked is True


def test_load_blocked_false():
    """Test loading blocked=False."""
    raw = json.dumps(
        [
            {
                "title": "Not blocked subtask long title",
                "criteria": ["c"],
                "tests": ["t"],
                "blockers": ["b"],
                "blocked": False,
            }
        ]
    )
    subtasks = subtask_loader.parse_subtasks_json(raw)
    st = subtasks[0]
    assert st.blocked is False


def test_load_block_reason():
    """Test loading block_reason string."""
    raw = json.dumps(
        [
            {
                "title": "Subtask with block reason long title",
                "criteria": ["c"],
                "tests": ["t"],
                "blockers": ["b"],
                "block_reason": "Waiting for API response",
            }
        ]
    )
    subtasks = subtask_loader.parse_subtasks_json(raw)
    st = subtasks[0]
    assert st.block_reason == "Waiting for API response"


def test_load_all_phase1_fields():
    """Test loading all Phase 1 fields together."""
    raw = json.dumps(
        [
            {
                "title": "Complete Phase 1 subtask with all new fields",
                "criteria": ["c"],
                "tests": ["t"],
                "blockers": ["b"],
                "progress_notes": ["Note 1", "Note 2", "Note 3"],
                "started_at": "2025-12-11T14:00:00Z",
                "blocked": True,
                "block_reason": "External dependency unavailable",
            }
        ]
    )
    subtasks = subtask_loader.parse_subtasks_json(raw)
    st = subtasks[0]
    assert st.progress_notes == ["Note 1", "Note 2", "Note 3"]
    assert st.started_at == "2025-12-11T14:00:00Z"
    assert st.blocked is True
    assert st.block_reason == "External dependency unavailable"


def test_load_missing_phase1_fields():
    """Test default values when Phase 1 fields are missing."""
    raw = json.dumps(
        [
            {
                "title": "Subtask without Phase 1 fields",
                "criteria": ["c"],
                "tests": ["t"],
                "blockers": ["b"],
            }
        ]
    )
    subtasks = subtask_loader.parse_subtasks_json(raw)
    st = subtasks[0]
    assert st.progress_notes == []
    assert st.started_at is None
    assert st.blocked is False
    assert st.block_reason == ""


def test_load_progress_notes_single_string():
    """Test coercing single string to list for progress_notes."""
    raw = json.dumps(
        [
            {
                "title": "Subtask with progress note as string",
                "criteria": ["c"],
                "tests": ["t"],
                "blockers": ["b"],
                "progress_notes": "Single note as string",
            }
        ]
    )
    subtasks = subtask_loader.parse_subtasks_json(raw)
    st = subtasks[0]
    assert st.progress_notes == ["Single note as string"]


def test_load_blocked_string_variants():
    """Test _to_bool coercion for blocked field with string variants."""
    # Test "true" string
    raw = json.dumps(
        [
            {
                "title": "Subtask with blocked as true string",
                "criteria": ["c"],
                "tests": ["t"],
                "blockers": ["b"],
                "blocked": "true",
            }
        ]
    )
    subtasks = subtask_loader.parse_subtasks_json(raw)
    assert subtasks[0].blocked is True

    # Test "1" string
    raw = json.dumps(
        [
            {
                "title": "Subtask with blocked as 1 string",
                "criteria": ["c"],
                "tests": ["t"],
                "blockers": ["b"],
                "blocked": "1",
            }
        ]
    )
    subtasks = subtask_loader.parse_subtasks_json(raw)
    assert subtasks[0].blocked is True

    # Test "false" string
    raw = json.dumps(
        [
            {
                "title": "Subtask with blocked as false string",
                "criteria": ["c"],
                "tests": ["t"],
                "blockers": ["b"],
                "blocked": "false",
            }
        ]
    )
    subtasks = subtask_loader.parse_subtasks_json(raw)
    assert subtasks[0].blocked is False


def test_load_started_at_empty_string():
    """Test started_at with empty string defaults to None."""
    raw = json.dumps(
        [
            {
                "title": "Subtask with empty started_at string",
                "criteria": ["c"],
                "tests": ["t"],
                "blockers": ["b"],
                "started_at": "",
            }
        ]
    )
    subtasks = subtask_loader.parse_subtasks_json(raw)
    st = subtasks[0]
    assert st.started_at is None


def test_load_block_reason_empty_string():
    """Test block_reason with empty string."""
    raw = json.dumps(
        [
            {
                "title": "Subtask with empty block_reason string",
                "criteria": ["c"],
                "tests": ["t"],
                "blockers": ["b"],
                "block_reason": "",
            }
        ]
    )
    subtasks = subtask_loader.parse_subtasks_json(raw)
    st = subtasks[0]
    assert st.block_reason == ""


def test_load_progress_notes_with_empty_strings():
    """Test progress_notes filters out empty strings."""
    raw = json.dumps(
        [
            {
                "title": "Subtask with progress notes containing empty strings",
                "criteria": ["c"],
                "tests": ["t"],
                "blockers": ["b"],
                "progress_notes": ["Valid note", "", "  ", "Another note"],
            }
        ]
    )
    subtasks = subtask_loader.parse_subtasks_json(raw)
    st = subtasks[0]
    assert st.progress_notes == ["Valid note", "Another note"]


def test_load_progress_notes_strips_whitespace():
    """Test progress_notes strips whitespace from notes."""
    raw = json.dumps(
        [
            {
                "title": "Subtask with progress notes with whitespace",
                "criteria": ["c"],
                "tests": ["t"],
                "blockers": ["b"],
                "progress_notes": ["  Note with spaces  ", "\tTabbed note\t"],
            }
        ]
    )
    subtasks = subtask_loader.parse_subtasks_json(raw)
    st = subtasks[0]
    assert st.progress_notes == ["Note with spaces", "Tabbed note"]
