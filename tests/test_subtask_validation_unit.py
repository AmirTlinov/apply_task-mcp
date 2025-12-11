from core.desktop.devtools.interface import subtask_validation
from core import SubTask


def _make(title):
    return SubTask(
        completed=False,
        title=title,
        success_criteria=["c"],
        tests=["t"],
        blockers=["b"],
        criteria_confirmed=True,
        tests_confirmed=True,
        blockers_resolved=True,
    )


def test_validate_subtasks_coverage():
    subtasks = [
        _make("Context why it matters"),
        _make("Criteria: definition of done"),
        _make("Tests: verify and check"),
        _make("Blockers risks dependencies"),
    ]
    ok, issues = subtask_validation.validate_subtasks_coverage(subtasks)
    assert ok


def test_validate_subtasks_quality_missing():
    st = SubTask(False, "criteria accept short", [], [], [])
    ok, issues = subtask_validation.validate_subtasks_quality([st])
    assert not ok and issues


def test_validate_subtasks_structure_depth():
    st_child = _make("Child task long enough")
    st_parent = _make("Parent task long enough")
    st_parent.children.append(st_child)
    ok, issues = subtask_validation.validate_subtasks_structure([st_parent])
    assert ok
