import json
import os
from pathlib import Path

import pytest

from core.desktop.devtools.interface import cli_runtime


def test_history_roundtrip_and_invalid_lines(tmp_path, monkeypatch):
    hist_path = tmp_path / "history.jsonl"
    cli_runtime.record_history(["a"], history_path=hist_path)
    cli_runtime.record_history(["b", "--flag"], history_path=hist_path)
    hist_path.write_text(hist_path.read_text() + "broken\n", encoding="utf-8")

    entries = cli_runtime.load_history(history_path=hist_path)

    assert [e["args"] for e in entries] == [["a"], ["b", "--flag"]]
    assert all("timestamp" in e for e in entries)


def test_read_last_pointer_variants(tmp_path):
    pointer = tmp_path / "last"
    assert cli_runtime.read_last_pointer(pointer) == (None, None)
    pointer.write_text("\n", encoding="utf-8")
    assert cli_runtime.read_last_pointer(pointer) == (None, None)
    pointer.write_text("TASK-1@dom", encoding="utf-8")
    assert cli_runtime.read_last_pointer(pointer) == ("TASK-1", "dom")
    pointer.write_text("TASK-2", encoding="utf-8")
    assert cli_runtime.read_last_pointer(pointer) == ("TASK-2", "")


def test_find_tasks_py_precedence_env_and_git(tmp_path, monkeypatch):
    env_task = tmp_path / "env_tasks.py"
    env_task.write_text("# env\n", encoding="utf-8")
    git_root = tmp_path / "git_root"
    git_root.mkdir()
    git_task = git_root / "tasks.py"
    git_task.write_text("# git\n", encoding="utf-8")
    monkeypatch.setenv("APPLY_TASKS_PY", str(env_task))
    monkeypatch.setattr(cli_runtime, "find_git_root", lambda: git_root)
    path, source = cli_runtime.find_tasks_py()
    assert path == env_task.resolve()
    assert source == "env"

    monkeypatch.delenv("APPLY_TASKS_PY", raising=False)
    path_git, source_git = cli_runtime.find_tasks_py()
    assert path_git == git_task.resolve()
    assert source_git == "git"


def test_find_tasks_py_cwd_and_parent(monkeypatch, tmp_path):
    cwd_task = tmp_path / "tasks.py"
    cwd_task.write_text("# cwd\n", encoding="utf-8")
    parent = tmp_path / "parent"
    parent.mkdir()
    monkeypatch.chdir(parent)
    monkeypatch.setattr(cli_runtime, "find_git_root", lambda: None)
    path, source = cli_runtime.find_tasks_py()
    assert path == cwd_task
    assert source == "parent"

    cwd_task.unlink()
    parent_task = tmp_path / "parent_task.py"
    parent_task.write_text("# parent\n", encoding="utf-8")
    # rename to tasks.py so search up parent works
    parent_task.rename(tmp_path / "tasks.py")
    path_parent, source_parent = cli_runtime.find_tasks_py()
    assert source_parent == "parent"


def test_run_tasks_py_missing(monkeypatch, capsys):
    monkeypatch.setattr(cli_runtime, "find_tasks_py", lambda verbose=False: (None, None))
    monkeypatch.setattr(cli_runtime, "find_git_root", lambda: None)

    code, payload = cli_runtime.run_tasks_py(["list"], verbose=True)

    assert code is None
    assert payload["git_root"] is None
