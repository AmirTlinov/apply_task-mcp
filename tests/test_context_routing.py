import os
import shutil
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tasks
from tasks import TaskManager, SubTask, derive_folder_explicit, save_last_task, get_last_task


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
        task = manager.create_task("X", parent="TASK-001", folder="phase1/api")
        task.subtasks.append(SubTask(True, "Validate API latency target >=20 chars", ["criterium"], ["pytest -q"], ["perf env"], True, True, True))
        task.subtasks.append(SubTask(True, "Roll out config safely >=20 chars", ["criterium"], ["pytest -m fast"], ["ops approval"], True, True, True))
        manager.save_task(task)

        loaded = manager.load_task(task.id, "phase1/api")
        self.assertEqual(loaded.status, "OK")

        moved = manager.move_glob("phase1/**/*.task", "phase2/api")
        self.assertEqual(moved, 1)
        relocated = manager.load_task(task.id, "phase2/api")
        self.assertIsNotNone(relocated)
        self.assertEqual(relocated.folder, "phase2/api")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
