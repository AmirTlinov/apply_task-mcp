import io
import json
from types import SimpleNamespace

import pytest

from core.desktop.devtools.interface import subtask_loader
from core.subtask import SubTask


def test_load_input_source_reads_file(tmp_path):
    path = tmp_path / "data.json"
    path.write_text("{}", encoding="utf-8")
    result = subtask_loader._load_input_source(f"@{path}", "payload")
    assert result == "{}"


def test_load_input_source_empty_stdin_raises(monkeypatch):
    monkeypatch.setattr(subtask_loader.sys, "stdin", io.StringIO(""))
    with pytest.raises(subtask_loader.SubtaskParseError):
        subtask_loader._load_input_source("-", "stdin data")


def test_parse_subtasks_json_bool_and_notes():
    raw = json.dumps(
        [
            {
                "title": "Valid subtask with flags long title 12345",
                "criteria": ["c"],
                "tests": ["t"],
                "blockers": ["b"],
                "criteria_confirmed": "yes",
                "tests_confirmed": "true",
                "blockers_resolved": 1,
                "criteria_notes": ["note1"],
                "tests_notes": "note2",
                "blockers_notes": ["note3", ""],
            }
        ]
    )
    subtasks = subtask_loader.parse_subtasks_json(raw)
    st = subtasks[0]
    assert st.criteria_confirmed and st.tests_confirmed and st.blockers_resolved
    assert st.criteria_notes == ["note1"]
    assert st.tests_notes == ["note2"]
    assert st.blockers_notes == ["note3"]


def test_parse_subtasks_json_requires_lists():
    raw = json.dumps([{"title": "Short but long enough 123456", "criteria": "c", "tests": [], "blockers": ["b"]}])
    with pytest.raises(subtask_loader.SubtaskParseError):
        subtask_loader.parse_subtasks_json(raw)


def test_parse_subtasks_json_missing_title():
    raw = json.dumps([{"criteria": ["c"], "tests": ["t"], "blockers": ["b"]}])
    with pytest.raises(subtask_loader.SubtaskParseError):
        subtask_loader.parse_subtasks_json(raw)


def test_parse_subtasks_json_missing_tests():
    raw = json.dumps([{"title": "Valid title 1234567890", "criteria": ["c"], "tests": [], "blockers": ["b"]}])
    with pytest.raises(subtask_loader.SubtaskParseError):
        subtask_loader.parse_subtasks_json(raw)


def test_load_input_source_requires_path_after_at():
    with pytest.raises(subtask_loader.SubtaskParseError):
        subtask_loader._load_input_source("@", "payload")


def test_load_input_source_missing_file():
    with pytest.raises(subtask_loader.SubtaskParseError):
        subtask_loader._load_input_source("@/no/such/file.json", "payload")


def test_validate_flagship_subtasks_reports_issues(monkeypatch):
    bad = SubTask(False, "too short", [], [], [])
    monkeypatch.setattr(subtask_loader, "_flatten_subtasks", lambda subs: [("0", bad), ("1", bad), ("2", bad)])
    ok, issues = subtask_loader.validate_flagship_subtasks([bad])
    assert ok is False
    assert issues


def test_validate_flagship_subtasks_minimum(monkeypatch):
    flat = [("0", SubTask(False, "Long title long title long", ["c"], ["t"], ["b"]))]
    monkeypatch.setattr(subtask_loader, "_flatten_subtasks", lambda subs: flat[:2])
    ok, issues = subtask_loader.validate_flagship_subtasks([flat[0][1]])
    assert ok is False
    assert issues
