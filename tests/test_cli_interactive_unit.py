from types import SimpleNamespace

import pytest

from core.desktop.devtools.interface import cli_interactive
from core.subtask import SubTask


def test_is_interactive(monkeypatch):
    monkeypatch.setattr(cli_interactive.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(cli_interactive.sys, "stdout", SimpleNamespace(isatty=lambda: False))
    assert not cli_interactive.is_interactive()
    monkeypatch.setattr(cli_interactive.sys, "stdout", SimpleNamespace(isatty=lambda: True))
    assert cli_interactive.is_interactive()


def test_prompt_uses_default(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a, **k: "")
    assert cli_interactive.prompt("Q", default="D") == "D"


def test_prompt_required_retries(monkeypatch):
    answers = iter(["", "value"])
    monkeypatch.setattr(cli_interactive, "prompt", lambda *a, **k: next(answers))
    assert cli_interactive.prompt_required("q") == "value"


def test_prompt_list_min_items(monkeypatch):
    inputs = iter(["one", "two", ""])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(inputs))
    items = cli_interactive.prompt_list("Enter", min_items=2)
    assert items == ["one", "two"]


def test_prompt_list_requires_more(monkeypatch, capsys):
    inputs = iter(["", "one", ""])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(inputs))
    items = cli_interactive.prompt_list("Enter", min_items=1)
    captured = capsys.readouterr()
    assert "Minimum" in captured.out or "минимум" in captured.out
    assert items == ["one"]


def test_confirm_yes(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a, **k: "y")
    assert cli_interactive.confirm("ok?", default=False) is True


def test_confirm_default(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a, **k: "")
    assert cli_interactive.confirm("ok?", default=True) is True


def test_confirm_interrupt(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a, **k: (_ for _ in ()).throw(EOFError()))
    with pytest.raises(SystemExit):
        cli_interactive.confirm("stop", default=False)


def test_prompt_subtask_interactive(monkeypatch):
    monkeypatch.setattr(cli_interactive, "prompt_required", lambda *a, **k: "A sufficiently long subtask title")
    lists = iter([["c1"], ["t1"], ["b1"]])
    monkeypatch.setattr(cli_interactive, "prompt_list", lambda *a, **k: next(lists))
    st = cli_interactive.prompt_subtask_interactive(1)
    assert isinstance(st, SubTask)
    assert st.title.startswith("A sufficiently")
    assert st.success_criteria == ["c1"]


def test_prompt_subtask_interactive_requires_long_title(monkeypatch):
    titles = iter(["short", "A longer subtask title that is valid"])
    monkeypatch.setattr(cli_interactive, "prompt_required", lambda *a, **k: next(titles))
    lists = iter([["c1"], ["t1"], ["b1"]])
    monkeypatch.setattr(cli_interactive, "prompt_list", lambda *a, **k: next(lists))
    st = cli_interactive.prompt_subtask_interactive(2)
    assert st.title.startswith("A longer")


def test_subtask_flags():
    st = SubTask(False, "title", [], [], [], True, False, True)
    flags = cli_interactive.subtask_flags(st)
    assert flags == {"criteria": True, "tests": False, "blockers": True}


def test_prompt_handles_interrupt(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(SystemExit):
        cli_interactive.prompt("q")
