from pathlib import Path

import pytest

from projects_sync import ProjectsSync
from tasks import SubTask


def test_projects_sync_disabled_without_config(tmp_path):
    sync = ProjectsSync(config_path=tmp_path / "missing.yaml")
    assert sync.enabled is False


def test_projects_sync_body_preview(tmp_path, monkeypatch):
    cfg = tmp_path / "projects.yaml"
    cfg.write_text(
        """
project:
  type: repository
  owner: dummy
  repo: demo
  number: 1
fields:
  status:
    name: Status
    options:
      OK: Done
  progress:
    name: Progress
"""
    )
    monkeypatch.setenv("APPLY_TASK_GITHUB_TOKEN", "token")
    sync = ProjectsSync(config_path=cfg)
    assert sync.enabled is True

    task = DummyTask()
    body = sync._build_body(task)
    assert "TASK-001" in body
    assert "Subtasks" in body


class DummyTask:
    id = "TASK-001"
    title = "Demo"
    status = "OK"
    domain = "demo/core"
    description = "Body"
    success_criteria = ["Ship" ]
    risks = ["Latency"]

    def __init__(self) -> None:
        st = SubTask(False, "Alpha")
        st.criteria_confirmed = True
        st.tests_confirmed = False
        st.blockers_resolved = False
        self.subtasks = [st]

    def calculate_progress(self) -> int:
        return 33
