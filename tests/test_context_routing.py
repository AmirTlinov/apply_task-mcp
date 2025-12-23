import os
import shutil
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tasks
from tasks import TaskManager, Step, derive_folder_explicit, save_last_task, get_last_task


class ContextRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="tasks_ctx_")
        self.old_cwd = os.getcwd()
        os.chdir(self.tmpdir)

    def tearDown(self):
        os.chdir(self.old_cwd)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_derive_folder_explicit(self):
        self.assertEqual(derive_folder_explicit("", "alpha", "api"), "alpha/api")
        self.assertEqual(derive_folder_explicit("custom", "alpha", "api"), "custom")
        self.assertEqual(derive_folder_explicit("", "", ""), "")

    def test_last_with_folder(self):
        save_last_task("TASK-999", "alpha/api")
        tid, folder = get_last_task()
        self.assertEqual(tid, "TASK-999")
        self.assertEqual(folder, "alpha/api")

    def test_move_glob_and_auto_ok(self):
        manager = TaskManager(Path(".tasks"))
        plan = manager.create_plan("Plan")
        manager.save_task(plan)
        task = manager.create_task("X", parent=plan.id, folder="phase1/api")
        task.success_criteria = ["done"]
        task.steps.append(
            Step(
                completed=True,
                title="Validate API latency target >=20 chars",
                success_criteria=["criterium"],
                tests=["pytest -q"],
                blockers=["perf env"],
                criteria_confirmed=True,
                tests_confirmed=True,
            )
        )
        task.steps.append(
            Step(
                completed=True,
                title="Roll out config safely >=20 chars",
                success_criteria=["criterium"],
                tests=["pytest -m fast"],
                blockers=["ops approval"],
                criteria_confirmed=True,
                tests_confirmed=True,
            )
        )
        manager.save_task(task)

        loaded = manager.load_task(task.id, "phase1/api")
        self.assertEqual(loaded.status, "DONE")

        moved = manager.move_glob("phase1/**/*.task", "phase2/api")
        self.assertEqual(moved, 1)
        relocated = manager.load_task(task.id, "phase2/api")
        self.assertIsNotNone(relocated)
        self.assertEqual(relocated.folder, "phase2/api")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
