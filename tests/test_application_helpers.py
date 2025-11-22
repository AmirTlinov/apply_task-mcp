import json
from typing import List

import pytest

from core.desktop.devtools.application.context import (
    derive_domain_explicit,
    derive_folder_explicit,
    get_last_task,
    normalize_task_id,
    parse_smart_title,
    resolve_task_reference,
    save_last_task,
)
from core.desktop.devtools.application.recommendations import next_recommendations, quick_overview, suggest_tasks
from core.desktop.devtools.interface.cli_io import structured_error, structured_response, validation_response
from core.task_detail import TaskDetail


def _make_task(task_id: str, *, status: str = "FAIL", priority: str = "MEDIUM", progress: int = 0, blocked: bool = False, deps: List[str] | None = None) -> TaskDetail:
    task = TaskDetail(task_id, task_id, status, priority=priority, progress=progress, blocked=blocked)
    task.dependencies = deps or []
    return task


def test_last_task_roundtrip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    save_last_task("TASK-7", "alpha/beta")
    assert get_last_task() == ("TASK-7", "alpha/beta")
    tid, domain = resolve_task_reference(None, None, None, None)
    assert tid == "TASK-007"
    assert domain == "alpha/beta"


def test_domain_derivation_and_parsing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert derive_domain_explicit("", "phase", "component") == "phase/component"
    assert derive_folder_explicit(None, "p", "c") == "p/c"
    with pytest.raises(ValueError):
        derive_domain_explicit("../bad", None, None)
    title, tags, deps = parse_smart_title("Ship feature #fast @TASK-123 extra")
    assert " ".join(title.split()) == "Ship feature extra"
    assert tags == ["fast"]
    assert deps == ["TASK-123"]
    assert normalize_task_id("5") == "TASK-005"
    assert normalize_task_id("TASK-9") == "TASK-009"


def test_next_recommendations_order_and_remember():
    calls: List[tuple[str, str]] = []
    payload, selected = next_recommendations(
        [
            _make_task("TASK-1", priority="HIGH", progress=10),
            _make_task("TASK-2", priority="LOW", blocked=True, progress=0),
            _make_task("TASK-3", status="OK"),
        ],
        {"domain": "", "phase": "", "component": ""},
        remember=lambda tid, dom: calls.append((tid, dom)),
        serializer=lambda task: {"id": task.id, "blocked": task.blocked},
    )
    assert payload["candidates"][0]["id"] == "TASK-2"  # blocked tasks bubble up
    assert selected and selected.id == "TASK-2"
    assert calls == [("TASK-2", "")]


def test_suggest_tasks_ranking_and_limit():
    payload, ranked = suggest_tasks(
        [
            _make_task("TASK-1", priority="LOW", progress=30, deps=["a"]),
            _make_task("TASK-2", priority="HIGH", progress=50, deps=["a", "b"]),
            _make_task("TASK-3", priority="HIGH", progress=10),
        ],
        {"folder": "", "domain": "", "phase": "", "component": ""},
        serializer=lambda task: {"id": task.id, "deps": len(task.dependencies)},
    )
    assert ranked[0].id == "TASK-3"
    assert [entry["id"] for entry in payload["suggestions"]] == ["TASK-3", "TASK-2", "TASK-1"]


def test_quick_overview_filters_and_remembers():
    calls: List[tuple[str, str]] = []
    payload, top = quick_overview(
        [
            _make_task("TASK-1", priority="LOW", progress=5),
            _make_task("TASK-2", priority="HIGH", progress=1),
            _make_task("TASK-3", status="OK", progress=0),
        ],
        {"folder": "f", "domain": "f", "phase": "", "component": ""},
        remember=lambda tid, dom: calls.append((tid, dom)),
        serializer=lambda task: {"id": task.id, "priority": task.priority},
    )
    assert calls == [("TASK-2", "")]
    assert [entry["id"] for entry in payload["top"]] == ["TASK-2", "TASK-1"]
    assert [t.id for t in top] == ["TASK-2", "TASK-1"]


def test_last_task_missing_raises(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError):
        resolve_task_reference(None, None, None, None)


def test_last_task_no_domain(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    save_last_task("TASK-42", "")
    tid, dom = resolve_task_reference(".", None, None, None)
    assert tid == "TASK-042"
    assert dom == ""


def test_effective_lang_env(monkeypatch):
    from core.desktop.devtools.interface import i18n

    monkeypatch.setenv("APPLY_TASK_LANG", "fr")
    assert i18n.effective_lang() == "fr"
    monkeypatch.delenv("APPLY_TASK_LANG")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    assert i18n.effective_lang("es") == "en"
    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    monkeypatch.setattr(i18n, "get_user_lang", lambda: "zz")
    assert i18n.effective_lang() == "en"


def test_structured_response_variants(capsys):
    rc = structured_response("cmd", message="hello", payload={"a": 1}, summary="done", exit_code=3)
    assert rc == 3
    body = json.loads(capsys.readouterr().out)
    assert body["command"] == "cmd"
    assert body["summary"] == "done"
    assert body["payload"]["a"] == 1
    err_rc = structured_error("cmd", "fail", payload={"x": 2})
    err_body = json.loads(capsys.readouterr().out)
    assert err_rc == 1
    assert err_body["status"] == "ERROR"
    assert err_body["payload"]["x"] == 2
    val_rc = validation_response("cmd", False, "bad", payload={"check": "x"})
    val_body = json.loads(capsys.readouterr().out)
    assert val_rc == 1
    assert val_body["status"] == "ERROR"
    assert val_body["payload"]["mode"] == "validate-only"
