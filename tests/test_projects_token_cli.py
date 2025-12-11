import types
from pathlib import Path

import projects_sync


def test_load_token_uses_gh_cli(monkeypatch, tmp_path):
    # Clear env token sources
    for key in ("APPLY_TASK_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
        monkeypatch.delenv(key, raising=False)

    # Point home to temp directory so no hosts.yml is found
    monkeypatch.setattr(projects_sync.Path, "home", lambda: tmp_path)

    calls = {}

    def fake_set_user_token(value):
        calls["saved"] = value

    monkeypatch.setattr(projects_sync, "set_user_token", fake_set_user_token)
    monkeypatch.setattr(projects_sync, "get_user_token", lambda: "")

    def fake_run(cmd, capture_output, text, check, timeout):
        calls["cmd"] = cmd

        class Result:
            returncode = 0
            stdout = "gh-cli-token\n"

        return Result()

    # Ensure we go through gh binary resolution
    monkeypatch.setattr(projects_sync.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(projects_sync.subprocess, "run", fake_run)

    token = projects_sync._load_token()

    assert token == "gh-cli-token"
    assert calls["cmd"][0].endswith("gh")
    assert calls["saved"] == "gh-cli-token"
