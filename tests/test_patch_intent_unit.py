"""Unit tests for patch intent (diff-oriented updates)."""

from core import Step, TaskDetail
from core.desktop.devtools.application.task_manager import TaskManager
from core.desktop.devtools.interface.intent_api import process_intent


def _make_task(task_id: str) -> TaskDetail:
    step = Step(False, "Step", success_criteria=["c"], tests=["t"])
    return TaskDetail(id=task_id, title="Example", status="TODO", steps=[step])


def test_patch_task_detail_sets_description_and_bumps_revision(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    manager.save_task(_make_task("TASK-001"), skip_sync=True)
    before = manager.load_task("TASK-001", skip_sync=True)
    assert before is not None
    expected = int(getattr(before, "revision", 0) or 0)

    resp = process_intent(
        manager,
        {
            "intent": "patch",
            "task": "TASK-001",
            "expected_revision": expected,
            "ops": [{"op": "set", "field": "description", "value": "updated"}],
        },
    )
    assert resp.success is True
    assert resp.meta.get("operation_id")

    after = manager.load_task("TASK-001", skip_sync=True)
    assert after is not None
    assert after.description == "updated"
    assert int(getattr(after, "revision", 0) or 0) > expected


def test_patch_step_appends_blocker(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    manager.save_task(_make_task("TASK-001"), skip_sync=True)
    resp = process_intent(
        manager,
        {
            "intent": "patch",
            "task": "TASK-001",
            "kind": "step",
            "path": "s:0",
            "ops": [{"op": "append", "field": "blockers", "value": "b1"}],
        },
    )
    assert resp.success is True

    after = manager.load_task("TASK-001", skip_sync=True)
    assert after is not None
    assert after.steps[0].blockers == ["b1"]

def test_patch_step_sets_required_checkpoints(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    manager.save_task(_make_task("TASK-001"), skip_sync=True)
    resp = process_intent(
        manager,
        {
            "intent": "patch",
            "task": "TASK-001",
            "kind": "step",
            "path": "s:0",
            "ops": [{"op": "set", "field": "required_checkpoints", "value": ["criteria", "tests", "security"]}],
        },
    )
    assert resp.success is True

    after = manager.load_task("TASK-001", skip_sync=True)
    assert after is not None
    assert after.steps[0].required_checkpoints == ["criteria", "tests", "security"]


def test_patch_task_node_sets_status(tmp_path):
    tasks_dir = tmp_path / ".tasks"
    tasks_dir.mkdir()
    manager = TaskManager(tasks_dir=tasks_dir)

    manager.save_task(_make_task("TASK-001"), skip_sync=True)
    ok, code, node, node_path = manager.add_task_node(task_id="TASK-001", step_path="s:0", title="T1", domain="", status="TODO")
    assert ok is True, code
    assert node_path

    resp = process_intent(
        manager,
        {
            "intent": "patch",
            "task": "TASK-001",
            "kind": "task",
            "path": node_path,
            "ops": [{"op": "set", "field": "status", "value": "DONE"}],
        },
    )
    assert resp.success is True

    after = manager.load_task("TASK-001", skip_sync=True)
    assert after is not None
    patched = after.steps[0].plan.tasks[0]
    assert patched.status == "DONE"
    assert patched.status_manual is True
