import pytest

from core.desktop.devtools.interface import subtask_loader


def test_parse_subtasks_json_requires_list():
    with pytest.raises(subtask_loader.SubtaskParseError):
        subtask_loader.parse_subtasks_json("{}")


def test_parse_subtasks_json_missing_fields():
    bad = [{"title": ""}]
    with pytest.raises(subtask_loader.SubtaskParseError):
        subtask_loader.parse_subtasks_json(str(bad))


def test_parse_subtasks_flexible_error_hint():
    with pytest.raises(subtask_loader.SubtaskParseError):
        subtask_loader.parse_subtasks_flexible("not-json")


def test_parse_subtasks_json_missing_lists():
    bad = '[{"title":"x","criteria":"one","tests":"two","blockers":"three"}]'
    subs = subtask_loader.parse_subtasks_json(bad)
    assert len(subs) == 1 and subs[0].success_criteria == ["one"]
