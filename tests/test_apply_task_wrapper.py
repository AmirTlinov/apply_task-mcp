import json
import subprocess
import types
from pathlib import Path

import pytest


@pytest.fixture
def apply_task_mod():
    script_path = Path(__file__).resolve().parents[1] / "apply_task"
    module = types.ModuleType("apply_task_tested")
    module.__file__ = str(script_path)
    exec(compile(script_path.read_text(), str(script_path), "exec"), module.__dict__)
    return module


def test_history_helpers_roundtrip(tmp_path, monkeypatch, apply_task_mod):
    monkeypatch.setattr(apply_task_mod, "HISTORY_PATH", tmp_path / "history.jsonl")
    apply_task_mod.record_history(["cmd1"])
    apply_task_mod.record_history(["cmd2", "--flag"])
    apply_task_mod.HISTORY_PATH.write_text(apply_task_mod.HISTORY_PATH.read_text() + "oops\n", encoding="utf-8")

    entries = apply_task_mod.load_history()

    assert [e["args"] for e in entries] == [["cmd1"], ["cmd2", "--flag"]]
    assert all("timestamp" in e for e in entries)


def test_read_last_pointer_states(tmp_path, monkeypatch, apply_task_mod):
    pointer = tmp_path / "last"
    monkeypatch.setattr(apply_task_mod, "LAST_POINTER", pointer)

    assert apply_task_mod.read_last_pointer() == (None, None)

    pointer.write_text("\n", encoding="utf-8")
    assert apply_task_mod.read_last_pointer() == (None, None)

    pointer.write_text("TASK-9@desk", encoding="utf-8")
    assert apply_task_mod.read_last_pointer() == ("TASK-9", "desk")

    pointer.write_text("TASK-42", encoding="utf-8")
    assert apply_task_mod.read_last_pointer() == ("TASK-42", "")


def test_find_tasks_py_precedence(monkeypatch, tmp_path, apply_task_mod):
    env_task = tmp_path / "env_tasks.py"
    env_task.write_text("# env\n", encoding="utf-8")
    git_root = tmp_path / "git_root"
    git_root.mkdir()
    git_task = git_root / "tasks.py"
    git_task.write_text("# git\n", encoding="utf-8")

    monkeypatch.setenv("APPLY_TASKS_PY", str(env_task))
    monkeypatch.setattr(apply_task_mod, "find_git_root", lambda: git_root)
    path, source = apply_task_mod.find_tasks_py()
    assert path == env_task.resolve()
    assert source == "env"

    monkeypatch.delenv("APPLY_TASKS_PY", raising=False)
    path_git, source_git = apply_task_mod.find_tasks_py()
    assert path_git == git_task.resolve()
    assert source_git == "git"


def test_find_tasks_py_walks_parents(monkeypatch, tmp_path, apply_task_mod):
    parent = tmp_path / "root"
    parent.mkdir()
    parent_task = parent / "tasks.py"
    parent_task.write_text("# parent\n", encoding="utf-8")
    child = parent / "child"
    child.mkdir()

    monkeypatch.chdir(child)
    monkeypatch.setattr(apply_task_mod, "find_git_root", lambda: None)
    monkeypatch.delenv("APPLY_TASKS_PY", raising=False)

    path, source = apply_task_mod.find_tasks_py()
    assert path == parent_task.resolve()
    assert source == "parent"


def test_run_tasks_py_missing(monkeypatch, capsys, tmp_path, apply_task_mod):
    monkeypatch.setattr(apply_task_mod, "find_tasks_py", lambda verbose=False: (None, None))
    monkeypatch.setattr(apply_task_mod, "find_git_root", lambda: None)

    exit_code = apply_task_mod.run_tasks_py(["list"])
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert exit_code == 1
    assert payload["status"] == "ERROR"
    assert payload["payload"]["git_root"] is None


def test_run_tasks_py_forwards_env(monkeypatch, tmp_path, capsys, apply_task_mod):
    fake_task = tmp_path / "tasks.py"
    fake_task.write_text("#!/usr/bin/env python3\nprint('ok')\n", encoding="utf-8")

    calls = {}

    def fake_run(cmd, cwd=None, env=None):
        calls["cmd"] = cmd
        calls["cwd"] = cwd
        calls["env"] = env

        class Result:
            returncode = 7

        return Result()

    monkeypatch.setattr(apply_task_mod, "find_tasks_py", lambda verbose=False: (fake_task, "env"))
    monkeypatch.setattr(apply_task_mod, "find_git_root", lambda: tmp_path)
    monkeypatch.setattr(apply_task_mod.subprocess, "run", fake_run)

    exit_code = apply_task_mod.run_tasks_py(["list"], verbose=True)
    captured = capsys.readouterr()

    assert "using env" in captured.err
    expected_first = apply_task_mod.sys.executable or "python3"
    assert calls["cmd"][:2] == [expected_first, str(fake_task)]
    assert calls["cwd"] == tmp_path
    assert calls["env"]["APPLY_TASK_PROJECT_ROOT"] == str(tmp_path)
    assert exit_code == 7


def test_main_unknown_command_requires_metadata(monkeypatch, tmp_path, capsys, apply_task_mod):
    monkeypatch.setattr(apply_task_mod, "HISTORY_PATH", tmp_path / "history.jsonl")
    monkeypatch.delenv(apply_task_mod.SKIP_HISTORY_ENV, raising=False)

    exit_code = apply_task_mod.main(["MyTitle"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert "parent" in payload["message"]
    assert apply_task_mod.HISTORY_PATH.exists()


def test_main_routing_show_and_status(monkeypatch, apply_task_mod):
    calls = []

    def fake_run(args, verbose=False):
        calls.append((args, verbose))
        return 0

    monkeypatch.setenv(apply_task_mod.SKIP_HISTORY_ENV, "1")
    monkeypatch.setattr(apply_task_mod, "run_tasks_py", fake_run)

    apply_task_mod.main(["show", "123", "--extra"])
    apply_task_mod.main(["done", "TASK-9"])
    apply_task_mod.main(["start", "456", "--dry-run"])
    apply_task_mod.main(["fail"])

    assert calls[0][0][1] == "TASK-123"
    assert calls[1][0][:3] == ["update", "TASK-9", "OK"]
    assert calls[2][0][:3] == ["update", "TASK-456", "WARN"]
    assert calls[3][0][:2] == ["update", "FAIL"]
    assert all(not verbose for _, verbose in calls)


def test_main_history_payload(monkeypatch, tmp_path, capsys, apply_task_mod):
    monkeypatch.setattr(apply_task_mod, "HISTORY_PATH", tmp_path / "history.jsonl")
    history_entries = [
        {"timestamp": "t1", "args": ["list"]},
        {"timestamp": "t2", "args": ["ok"]},
    ]
    apply_task_mod.HISTORY_PATH.write_text("\n".join(json.dumps(e) for e in history_entries), encoding="utf-8")

    pointer = tmp_path / "last"
    pointer.write_text("TASK-8@dom", encoding="utf-8")
    monkeypatch.setattr(apply_task_mod, "LAST_POINTER", pointer)
    monkeypatch.setenv(apply_task_mod.SKIP_HISTORY_ENV, "1")

    exit_code = apply_task_mod.main(["history", "1"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["payload"]["entries"][0]["cli"].startswith("apply_task")
    assert payload["payload"]["last_task"] == {"id": "TASK-8", "domain": "dom"}
    assert payload["summary"].startswith("1 ")


def test_find_git_root_handles_success_and_failure(monkeypatch, tmp_path, apply_task_mod):
    class FakeResult:
        def __init__(self, out: str) -> None:
            self.stdout = out

    def fake_run_success(cmd, capture_output, text, check):
        assert cmd == ['git', 'rev-parse', '--show-toplevel']
        return FakeResult(str(tmp_path))

    def fake_run_fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0])

    monkeypatch.setattr(apply_task_mod.subprocess, "run", fake_run_success)
    assert apply_task_mod.find_git_root() == tmp_path

    monkeypatch.setattr(apply_task_mod.subprocess, "run", fake_run_fail)
    assert apply_task_mod.find_git_root() is None


def test_load_history_handles_missing_file(monkeypatch, tmp_path, apply_task_mod):
    monkeypatch.setattr(apply_task_mod, "HISTORY_PATH", tmp_path / "nope.jsonl")
    assert apply_task_mod.load_history() == []


def test_find_tasks_py_uses_cwd(monkeypatch, tmp_path, apply_task_mod):
    cwd_task = tmp_path / "tasks.py"
    cwd_task.write_text("# cwd\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(apply_task_mod, "find_git_root", lambda: None)
    monkeypatch.delenv("APPLY_TASKS_PY", raising=False)

    path, source = apply_task_mod.find_tasks_py()
    assert path == cwd_task
    assert source == "cwd"


def test_find_tasks_py_script_fallback(monkeypatch, tmp_path, apply_task_mod):
    workdir = tmp_path / "work"
    workdir.mkdir()
    fake_git = tmp_path / "git_root"
    fake_git.mkdir()
    monkeypatch.chdir(workdir)
    monkeypatch.setattr(apply_task_mod, "find_git_root", lambda: fake_git)
    monkeypatch.delenv("APPLY_TASKS_PY", raising=False)

    path, source = apply_task_mod.find_tasks_py()
    assert source == "script"
    assert path.name == "tasks.py"


def test_main_no_args_and_verbose(monkeypatch, capsys, apply_task_mod):
    exit_code = apply_task_mod.main([])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["message"] == "Не указана команда"

    calls = []

    def fake_run(args, verbose=False):
        calls.append((args, verbose))
        return 0

    monkeypatch.setenv(apply_task_mod.SKIP_HISTORY_ENV, "1")
    monkeypatch.setattr(apply_task_mod, "run_tasks_py", fake_run)
    apply_task_mod.main(["list", "--verbose", "-v"])

    assert calls[0][1] is True


def test_main_unknown_command_with_required_flags(monkeypatch, apply_task_mod):
    calls = []

    def fake_run(args, verbose=False):
        calls.append((args, verbose))
        return 0

    monkeypatch.setenv(apply_task_mod.SKIP_HISTORY_ENV, "1")
    monkeypatch.setattr(apply_task_mod, "run_tasks_py", fake_run)

    exit_code = apply_task_mod.main([
        "MyTask",
        "--parent", "TASK-1",
        "--tests", "unit",
        "--risks", "perf",
        "--subtasks", "[]",
        "--description", "demo",
    ])

    assert exit_code == 0
    assert calls[0][0][:2] == ["task", "MyTask"]
    assert calls[0][1] is False


def test_main_show_without_id(monkeypatch, apply_task_mod):
    calls = []
    monkeypatch.setenv(apply_task_mod.SKIP_HISTORY_ENV, "1")
    monkeypatch.setattr(apply_task_mod, "run_tasks_py", lambda args, verbose=False: calls.append((args, verbose)) or 0)

    apply_task_mod.main(["show"])

    assert calls[0][0] == ["show"]
    assert calls[0][1] is False


def test_history_with_invalid_limit(monkeypatch, tmp_path, capsys, apply_task_mod):
    monkeypatch.setattr(apply_task_mod, "HISTORY_PATH", tmp_path / "history.jsonl")
    apply_task_mod.HISTORY_PATH.write_text(json.dumps({"timestamp": "t", "args": ["list"]}), encoding="utf-8")
    monkeypatch.setattr(apply_task_mod, "LAST_POINTER", tmp_path / "none")
    monkeypatch.setenv(apply_task_mod.SKIP_HISTORY_ENV, "1")

    exit_code = apply_task_mod.main(["history", "abc"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["payload"]["limit"] == 5


def test_replay_errors_and_success(monkeypatch, tmp_path, capsys, apply_task_mod):
    monkeypatch.setattr(apply_task_mod, "HISTORY_PATH", tmp_path / "history.jsonl")
    apply_task_mod.HISTORY_PATH.write_text("", encoding="utf-8")
    monkeypatch.setenv(apply_task_mod.SKIP_HISTORY_ENV, "1")
    monkeypatch.setattr(apply_task_mod, "load_history", lambda: [{"args": ["list"]}])

    missing_arg_code = apply_task_mod.main(["replay"])
    payload_missing = json.loads(capsys.readouterr().out)
    assert missing_arg_code == 1
    assert payload_missing["command"] == "replay"

    out_of_range_code = apply_task_mod.main(["replay", "5"])
    payload_range = json.loads(capsys.readouterr().out)
    assert out_of_range_code == 1
    assert payload_range["payload"]["order"] == 5

    def fake_run(cmd, cwd=None, env=None):
        return types.SimpleNamespace(returncode=9)

    monkeypatch.setattr(apply_task_mod.subprocess, "run", fake_run)
    ok_code = apply_task_mod.main(["replay", "1"])
    assert ok_code == 9


def test_which_error_branch(monkeypatch, capsys, apply_task_mod):
    monkeypatch.setenv(apply_task_mod.SKIP_HISTORY_ENV, "1")
    monkeypatch.setattr(apply_task_mod, "find_tasks_py", lambda verbose=False: (None, None))
    monkeypatch.setattr(apply_task_mod, "find_git_root", lambda: Path("/tmp/fake"))

    exit_code = apply_task_mod.main(["which"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["command"] == "which"
    assert payload["status"] == "ERROR"


def test_help_command(monkeypatch, capsys, apply_task_mod):
    monkeypatch.setenv(apply_task_mod.SKIP_HISTORY_ENV, "1")
    exit_code = apply_task_mod.main(["help"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["command"] == "help"
    assert payload["payload"]["overview"].startswith("apply_task")
