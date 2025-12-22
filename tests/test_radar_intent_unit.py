"""Unit tests for radar intent handler."""

from pathlib import Path

import pytest

from core import Attachment, Step, TaskDetail, VerificationCheck
from core.desktop.devtools.application.task_manager import TaskManager
from core.desktop.devtools.interface.intent_api import handle_radar


@pytest.fixture
def manager(tmp_path: Path) -> TaskManager:
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    return TaskManager(tasks_dir=tasks_dir)


def test_handle_radar_plan_compact_snapshot(manager: TaskManager):
    plan = TaskDetail(id="PLAN-001", title="Plan", status="TODO", kind="plan")
    plan.contract = "Goal: ship\nDone: tests green"
    plan.plan_steps = ["Design", "Implement", "Verify"]
    plan.plan_current = 1
    manager.save_task(plan)

    resp = handle_radar(manager, {"intent": "radar", "plan": "PLAN-001", "limit": 1})
    assert resp.success is True
    result = resp.result
    assert result["focus"]["id"] == "PLAN-001"
    assert result["now"]["kind"] == "plan_step"
    assert result["why"]["plan_id"] == "PLAN-001"
    assert isinstance(result["next"], list)


def test_handle_radar_task_includes_now_verify_and_deps(manager: TaskManager):
    plan = TaskDetail(id="PLAN-001", title="Plan", status="TODO", kind="plan")
    plan.contract = "Goal: ship"
    manager.save_task(plan)

    dep = TaskDetail(id="TASK-002", title="Dep", status="TODO", parent="PLAN-001")
    manager.save_task(dep)

    step1 = Step.new("Step 1", criteria=["c1"], tests=["t1"])
    assert step1 is not None
    step1.verification_outcome = "pass"
    step1.verification_checks = [
        VerificationCheck(kind="ci", spec="pytest -q -k radar", outcome="pass", observed_at="2025-12-22T00:00:00+00:00")
    ]
    step1.attachments = [Attachment(kind="cmd_output", uri="stdout", size=1, observed_at="2025-12-22T00:00:01+00:00")]
    step2 = Step.new("Step 2", criteria=["c2"], tests=[])
    assert step2 is not None
    task = TaskDetail(
        id="TASK-001",
        title="Task",
        status="ACTIVE",
        parent="PLAN-001",
        steps=[step1, step2],
        depends_on=["TASK-002"],
    )
    manager.save_task(task)

    resp = handle_radar(manager, {"intent": "radar", "task": "TASK-001", "limit": 2})
    assert resp.success is True
    result = resp.result
    assert result["focus"]["id"] == "TASK-001"
    assert result["now"]["kind"] == "step"
    assert "verify" in result
    assert "open_checkpoints" in result
    evidence = result["verify"]["evidence"]
    assert evidence["verification_outcome"] == "pass"
    assert evidence["checks"]["count"] == 1
    assert evidence["checks"]["kinds"]["ci"] == 1
    assert evidence["checks"]["last_observed_at"] == "2025-12-22T00:00:00+00:00"
    assert evidence["attachments"]["count"] == 1
    assert evidence["attachments"]["kinds"]["cmd_output"] == 1
    assert evidence["attachments"]["last_observed_at"] == "2025-12-22T00:00:01+00:00"
    assert result["blockers"]["depends_on"] == ["TASK-002"]
    assert result["blockers"]["unresolved_depends_on"] == ["TASK-002"]


def test_handle_radar_task_open_checkpoints_include_extended(manager: TaskManager):
    step1 = Step.new("Step 1", criteria=["c1"], tests=["t1"])
    assert step1 is not None
    step1.criteria_confirmed = True
    step1.tests_confirmed = True
    step1.required_checkpoints = ["criteria", "tests", "security"]
    task = TaskDetail(id="TASK-001", title="Task", status="ACTIVE", steps=[step1])
    manager.save_task(task)

    resp = handle_radar(manager, {"intent": "radar", "task": "TASK-001", "limit": 1})
    assert resp.success is True
    result = resp.result
    assert "security" in list(result.get("open_checkpoints") or [])
