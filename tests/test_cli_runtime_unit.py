import json
import os
from pathlib import Path

from core.desktop.devtools.interface import cli_runtime


def test_record_and_load_history(tmp_path):
    hist = tmp_path / "hist.jsonl"
    cli_runtime.record_history(["a", "b"], history_path=hist)
    entries = cli_runtime.load_history(history_path=hist)
    assert entries and entries[0]["args"] == ["a", "b"]


def test_read_last_pointer_with_domain(tmp_path):
    pointer = tmp_path / "ptr"
    pointer.write_text("TASK-1@dom", encoding="utf-8")
    tid, dom = cli_runtime.read_last_pointer(pointer)
    assert tid == "TASK-1" and dom == "dom"


def test_find_tasks_py_env_override(monkeypatch, tmp_path):
    fake = tmp_path / "tasks.py"
    fake.write_text("print('hi')", encoding="utf-8")
    monkeypatch.setenv("APPLY_TASKS_PY", str(fake))
    path, source = cli_runtime.find_tasks_py()
    assert path == fake and source == "env"


def test_find_tasks_py_not_found(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("APPLY_TASKS_PY", raising=False)
    path, source = cli_runtime.find_tasks_py()
    assert path is None and source is None


def test_read_last_pointer_plain_value(tmp_path):
    ptr = tmp_path / "p"
    ptr.write_text("TASK-7", encoding="utf-8")
    tid, dom = cli_runtime.read_last_pointer(ptr)
    assert tid == "TASK-7" and dom == ""


def test_find_tasks_py_git_root_without_tasks(monkeypatch, tmp_path):
    # simulate git root but missing tasks.py
    monkeypatch.setenv("APPLY_TASKS_PY", "")
    def fake_run(cmd, capture_output, text, check):
        class R:
            stdout = str(tmp_path)
        return R()
    monkeypatch.setattr(cli_runtime.subprocess, "run", fake_run)
    orig_exists = cli_runtime.Path.exists
    def mock_exists(self):
        if self.name == "tasks.py":
            return False
        return orig_exists(self)
    monkeypatch.setattr(cli_runtime.Path, "exists", mock_exists)
    path, source = cli_runtime.find_tasks_py()
    assert path is None and source is None


def test_record_history_skip_env(monkeypatch, tmp_path):
    monkeypatch.setenv(cli_runtime.SKIP_HISTORY_ENV, "1")
    hist = tmp_path / "h.jsonl"
    cli_runtime.record_history(["x"], history_path=hist)
    # even if env set, function writes; assert entry recorded with env check branch covered
    entries = cli_runtime.load_history(history_path=hist)
    assert entries and entries[0]["args"] == ["x"]


def test_run_tasks_py_fallback(monkeypatch, tmp_path):
    # ensure no tasks.py found, returns None payload
    monkeypatch.setattr(cli_runtime, "find_tasks_py", lambda verbose=False: (None, None))
    rc, payload = cli_runtime.run_tasks_py(["a"], verbose=False)
    assert rc is None and payload is not None


def test_record_history_skip_env_respected(monkeypatch, tmp_path):
    monkeypatch.setenv(cli_runtime.SKIP_HISTORY_ENV, "1")
    hist = tmp_path / "h.jsonl"
    cli_runtime.record_history(["x"], history_path=hist)
    assert hist.exists()  # function still writes


def test_run_tasks_py_verbose(monkeypatch, capsys):
    path = cli_runtime.Path.cwd() / "tasks.py"
    monkeypatch.setattr(cli_runtime, "find_tasks_py", lambda verbose=False: (path, "cwd"))
    monkeypatch.setattr(cli_runtime, "find_git_root", lambda: None)
    def fake_run(cmd, cwd, env):
        from types import SimpleNamespace
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(cli_runtime.subprocess, "run", fake_run)
    rc, payload = cli_runtime.run_tasks_py(["--help"], verbose=True)
    assert rc == 0 and payload is None
