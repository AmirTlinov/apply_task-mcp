"""Unit tests for structured contract data + versioning."""

from pathlib import Path

import pytest

from core import EVENT_CONTRACT_UPDATED, TaskDetail
from core.desktop.devtools.application.task_manager import TaskManager
from core.desktop.devtools.interface.intent_api import handle_contract


@pytest.fixture
def manager(tmp_path: Path) -> TaskManager:
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    return TaskManager(tasks_dir=tasks_dir)


def test_contract_data_roundtrip_via_file(manager: TaskManager):
    plan = TaskDetail(id="PLAN-001", title="Plan", status="TODO", kind="plan")
    plan.contract = "Goal: ship"
    plan.contract_data = {"goal": "ship", "checks": ["pytest -q"]}
    manager.save_task(plan, skip_sync=True)

    reloaded = manager.load_task("PLAN-001", skip_sync=True)
    assert reloaded is not None
    assert reloaded.contract_data == {"goal": "ship", "checks": ["pytest -q"]}


def test_handle_contract_versions_include_structured_data(manager: TaskManager):
    plan = TaskDetail(id="PLAN-001", title="Plan", status="TODO", kind="plan")
    manager.save_task(plan, skip_sync=True)

    payload = {"intent": "contract", "plan": "PLAN-001", "contract_data": {"goal": "v1"}}
    resp = handle_contract(manager, payload)
    assert resp.success is True

    updated = manager.load_task("PLAN-001", skip_sync=True)
    assert updated is not None
    assert updated.contract_data == {"goal": "v1"}
    assert len(updated.contract_versions) == 1
    assert updated.contract_versions[0]["data"] == {"goal": "v1"}

    # Idempotent: same data should not append a new version.
    resp2 = handle_contract(manager, payload)
    assert resp2.success is True
    updated2 = manager.load_task("PLAN-001", skip_sync=True)
    assert updated2 is not None
    assert len(updated2.contract_versions) == 1

    # Contract update event is recorded (best-effort).
    assert any(getattr(e, "event_type", "") == EVENT_CONTRACT_UPDATED for e in (updated2.events or []))

