import json
import os
import importlib.util
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import unittest

import pytest
import yaml


@pytest.fixture
def isolated_tasks(tmp_path):
    root = Path(__file__).resolve().parents[1]
    tasks_dir = root / ".tasks"
    backup_dir = None
    if tasks_dir.exists():
        backup_dir = tmp_path / "tasks_backup"
        shutil.copytree(tasks_dir, backup_dir)
        shutil.rmtree(tasks_dir)
    tasks_dir.mkdir(exist_ok=True)

    last_file = root / ".last"
    last_backup = None
    if last_file.exists():
        last_backup = tmp_path / "last_backup"
        last_backup.write_text(last_file.read_text())
        last_file.unlink()

    # Set env variable to force CLI to use local .tasks/
    old_env = os.environ.get("APPLY_TASK_TASKS_DIR")
    os.environ["APPLY_TASK_TASKS_DIR"] = str(tasks_dir)

    try:
        yield tasks_dir
    finally:
        # Restore env
        if old_env is not None:
            os.environ["APPLY_TASK_TASKS_DIR"] = old_env
        else:
            os.environ.pop("APPLY_TASK_TASKS_DIR", None)

        if tasks_dir.exists():
            shutil.rmtree(tasks_dir)
        if backup_dir and backup_dir.exists():
            shutil.copytree(backup_dir, tasks_dir)
        elif not tasks_dir.exists():
            tasks_dir.mkdir(exist_ok=True)

        if last_backup and last_backup.exists():
            last_file.write_text(last_backup.read_text())
        elif last_file.exists():
            last_file.unlink()


def _run_apply_cmd(args):
    root = Path(__file__).resolve().parents[1]
    script = root / "apply_task"
    return subprocess.run([sys.executable, str(script)] + args, cwd=root, capture_output=True, text=True)


def _read_last_task_ref():
    root = Path(__file__).resolve().parents[1]
    last_file = root / ".last"
    if not last_file.exists():
        return None, ""
    data = last_file.read_text().strip()
    if not data:
        return None, ""
    parts = data.split("@", 1)
    task_id = parts[0]
    folder = parts[1] if len(parts) > 1 else ""
    return task_id, folder


def _task_file(task_id: str, folder: str = "") -> Path:
    # Use env variable if set (by isolated_tasks fixture)
    env_dir = os.environ.get("APPLY_TASK_TASKS_DIR")
    if env_dir:
        base = Path(env_dir)
    else:
        root = Path(__file__).resolve().parents[1]
        base = root / ".tasks"
    if folder:
        base = base / folder
    return base / f"{task_id}.task"


def _json_body(result):
    output = (result.stdout or "").strip()
    if not output:
        raise AssertionError(f"Пустой stdout, stderr={result.stderr}")
    return json.loads(output)


class ApplyTaskResolveTests(unittest.TestCase):
    def setUp(self):
        # Загружаем apply_task как модуль
        root = Path(__file__).resolve().parents[1]
        script_path = root / "apply_task"
        code = script_path.read_text()
        module = importlib.util.module_from_spec(importlib.util.spec_from_loader("apply_task_mod", loader=None))
        exec(compile(code, str(script_path), "exec"), module.__dict__)
        self.mod = module
        self.orig_env = os.environ.copy()
        self.history_dir = Path(tempfile.mkdtemp(prefix="apply_history"))
        self.history_file = self.history_dir / "history.jsonl"
        os.environ["APPLY_TASK_HISTORY"] = str(self.history_file)
        self.repo_root = root
        self.tasks_dir = self.repo_root / ".tasks"
        self.last_file = self.repo_root / ".last"
        self._sandbox_dir = Path(tempfile.mkdtemp(prefix="tasks_sandbox"))
        self._tasks_backup = self._sandbox_dir / "tasks"
        if self.tasks_dir.exists():
            shutil.copytree(self.tasks_dir, self._tasks_backup)
            shutil.rmtree(self.tasks_dir)
        self.tasks_dir.mkdir(exist_ok=True)
        # Set env variable to force CLI to use local .tasks/
        os.environ["APPLY_TASK_TASKS_DIR"] = str(self.tasks_dir)
        self._last_backup = None
        if self.last_file.exists():
            self._last_backup = self._sandbox_dir / "last"
            self._last_backup.write_text(self.last_file.read_text())
            self.last_file.unlink()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.orig_env)
        if self.history_dir.exists():
            shutil.rmtree(self.history_dir)
        if self.tasks_dir.exists():
            shutil.rmtree(self.tasks_dir)
        if self._tasks_backup.exists():
            shutil.copytree(self._tasks_backup, self.tasks_dir)
        else:
            self.tasks_dir.mkdir(exist_ok=True)
        if self._last_backup and self._last_backup.exists():
            self.last_file.write_text(self._last_backup.read_text())
        elif self.last_file.exists():
            self.last_file.unlink()
        if self._sandbox_dir.exists():
            shutil.rmtree(self._sandbox_dir)

    def _run_apply(self, args, stdin_data=None):
        root = Path(__file__).resolve().parents[1]
        script = root / "apply_task"
        result = subprocess.run(
            [sys.executable, str(script)] + args,
            cwd=root,
            input=stdin_data,
            capture_output=True,
            text=True,
        )
        return result

    def _read_last_task_id(self):
        root = Path(__file__).resolve().parents[1]
        last_file = root / ".last"
        if not last_file.exists():
            return None
        data = last_file.read_text().strip()
        return data.split("@", 1)[0] if data else None

    def _read_task_metadata(self, task_id):
        task_file = self._tasks_root() / f"{task_id}.task"
        content = task_file.read_text()
        parts = content.split("---")
        meta = yaml.safe_load(parts[1]) if len(parts) > 1 else {}
        return meta, content

    def _tasks_root(self):
        # Use env variable if set (by isolated_tasks fixture)
        env_dir = os.environ.get("APPLY_TASK_TASKS_DIR")
        if env_dir:
            return Path(env_dir)
        return Path(__file__).resolve().parents[1] / ".tasks"

    def _task_files(self):
        return list(self._tasks_root().glob("TASK-*.task"))

    def _flagship_subtasks_payload(self):
        entries = [
            {
                "title": "Design milestone roadmap with owners and acceptance gates",
                "criteria": [
                    "milestones defined",
                    "owners assigned",
                    "acceptance gates captured",
                ],
                "tests": ["review milestone doc", "owner signoff"],
                "blockers": ["scope creep"],
            },
            {
                "title": "Validate solution with unit+integration coverage >=85 percent",
                "criteria": [
                    "coverage >=85%",
                    "linters executed",
                    "integration plan documented",
                ],
                "tests": ["pytest -q", "lint run"],
                "blockers": ["flaky tests"],
            },
            {
                "title": "Assess risks and define mitigations plus rollback path",
                "criteria": ["risk list created", "mitigations documented"],
                "tests": ["risk review"],
                "blockers": ["dependency outage", "perf regression"],
            },
            {
                "title": "Prove readiness with DoD/SLO metrics and acceptance evidence",
                "criteria": ["DoD >=85%", "p95 <=100ms", "acceptance recorded"],
                "tests": ["perf suite", "acceptance demo"],
                "blockers": ["unmet SLO"],
            },
            {
                "title": "Execute implementation, wire integrations, validate each increment",
                "criteria": ["integrations wired", "validation log filled", "step demo"],
                "tests": ["step check"],
                "blockers": ["integration gaps"],
            },
            {
                "title": "Run regression, lint, metrics review and release signoff",
                "criteria": ["regression suite green", "lint clean", "release approved"],
                "tests": ["full suite", "lint", "release checklist"],
                "blockers": ["release blocker"],
            },
        ]
        return json.dumps(entries, ensure_ascii=False)

    def _create_flagship_task(self, title="MacroFlow"):
        subtasks = self._flagship_subtasks_payload()
        result = self._run_apply([
            title,
            "--parent", "ROOT",
            "--tests", "unit",
            "--risks", "deps",
            "--subtasks", subtasks,
            "--description", "macro test"
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        body = _json_body(result)
        return body["payload"]["task"]["id"]

    def _write_stub_tasks_py(self):
        stub = self._sandbox_dir / "stub_tasks.py"
        stub.write_text(
            """#!/usr/bin/env python3\nimport json, sys\nbody = {\"command\": \"stub\", \"status\": \"OK\", \"message\": \"stub runner\", \"payload\": {\"argv\": sys.argv[1:]}}\nprint(json.dumps(body))\n""",
            encoding="utf-8",
        )
        stub.chmod(0o755)
        return stub

    def test_find_tasks_py_env_override(self):
        tasks_path = Path(__file__).resolve().parents[1] / "tasks.py"
        os.environ["APPLY_TASKS_PY"] = str(tasks_path)
        path, source = self.mod.find_tasks_py()
        self.assertEqual(path, tasks_path.resolve())
        self.assertEqual(source, "env")

    def test_explain_source(self):
        msg = self.mod.explain_source("script", Path("/tmp/x/tasks.py"))
        self.assertIn("script", msg)
        self.assertIn("/tmp/x/tasks.py", msg)

    def test_projects_commands_forwarded(self):
        stub = self._write_stub_tasks_py()
        os.environ["APPLY_TASKS_PY"] = str(stub)
        self.addCleanup(lambda: os.environ.pop("APPLY_TASKS_PY", None))
        scenarios = [
            (["projects", "sync", "--all"], ["projects", "sync", "--all"]),
            (["projects-auth", "--unset"], ["projects-auth", "--unset"]),
            (["projects-webhook", "--payload", "payload.json"], ["projects-webhook", "--payload", "payload.json"]),
            (
                ["projects-webhook-serve", "--host", "127.0.0.1", "--port", "9180"],
                ["projects-webhook-serve", "--host", "127.0.0.1", "--port", "9180"],
            ),
        ]
        for args, expected in scenarios:
            result = self._run_apply(args)
            self.assertEqual(result.returncode, 0, result.stderr)
            body = _json_body(result)
            self.assertEqual(body["status"], "OK")
            self.assertEqual(body["payload"]["argv"], expected)

    def test_projects_autosync_toggle(self):
        cfg_path = self.repo_root / ".apply_task_projects.yaml"
        assert cfg_path.exists()
        result = self._run_apply(["projects", "autosync", "off"])
        self.assertEqual(result.returncode, 0, result.stderr)
        body = _json_body(result)
        self.assertFalse(body["payload"]["auto_sync"])
        data = yaml.safe_load(cfg_path.read_text())
        self.assertFalse((data.get("project") or {}).get("enabled", True))
        result = self._run_apply(["projects", "autosync", "on"])
        self.assertEqual(result.returncode, 0, result.stderr)
        body = _json_body(result)
        self.assertTrue(body["payload"]["auto_sync"])
        data = yaml.safe_load(cfg_path.read_text())
        self.assertTrue((data.get("project") or {}).get("enabled", False))

    def test_projects_status_command(self):
        result = self._run_apply(["projects", "status"])
        self.assertEqual(result.returncode, 0, result.stderr)
        body = _json_body(result)
        payload = body["payload"]
        self.assertIn("target_label", payload)
        self.assertIn("auto_sync", payload)

    def test_create_requires_tests(self):
        result = self._run_apply(["MyTitle", "--parent", "TASK-001"])
        self.assertNotEqual(result.returncode, 0)
        body = _json_body(result)
        self.assertEqual(body["status"], "ERROR")
        self.assertIn("tests", body["message"])

    def test_suggest_command_available(self):
        result = self._run_apply(["suggest"])
        self.assertEqual(result.returncode, 0)
        body = _json_body(result)
        self.assertEqual(body["command"], "suggest")
        self.assertEqual(body["status"], "OK")

    def test_create_requires_subtasks_coverage(self):
        invalid_subtasks = json.dumps([
            {
                "title": "Too small plan subtask for coverage",
                "criteria": ["defined"],
                "tests": ["review"],
                "blockers": ["none"]
            }
        ], ensure_ascii=False)
        result = self._run_apply([
            "MyTitle",
            "--parent", "TASK-001",
            "--tests", "unit",
            "--risks", "dep",
            "--subtasks", invalid_subtasks,
            "--description", "desc"
        ])
        self.assertNotEqual(result.returncode, 0)
        body = _json_body(result)
        self.assertEqual(body["status"], "ERROR")
        self.assertIn("flagship", body["message"])

    def test_create_with_json_subtasks(self):
        subtasks = self._flagship_subtasks_payload()
        result = self._run_apply([
            "JsonTitle",
            "--parent", "TASK-003",
            "--tests", "unit;integration",
            "--risks", "perf;availability",
            "--subtasks", subtasks,
            "--description", "JSON payload"
        ])
        self.assertEqual(result.returncode, 0)
        body = _json_body(result)
        self.assertEqual(body["status"], "OK")
        self.assertIn("task", body["payload"])
        self.assertGreaterEqual(body["payload"]["task"]["subtasks_count"], 3)

    def test_create_with_subtasks_file_reference(self):
        payload = self._flagship_subtasks_payload()
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as fd:
            fd.write(payload)
            path = fd.name
        try:
            result = self._run_apply([
                "FileRef",
                "--parent", "TASK-004",
                "--tests", "unit",
                "--risks", "deps",
                "--subtasks", f"@{path}",
                "--description", "file input"
            ])
        finally:
            os.unlink(path)
        self.assertEqual(result.returncode, 0)
        body = _json_body(result)
        self.assertEqual(body["status"], "OK")

    def test_create_with_subtasks_from_stdin(self):
        payload = self._flagship_subtasks_payload()
        result = self._run_apply([
            "StdinRef",
            "--parent", "TASK-005",
            "--tests", "unit",
            "--risks", "deps",
            "--subtasks", "-",
            "--description", "stdin input"
        ], stdin_data=payload)
        self.assertEqual(result.returncode, 0)
        body = _json_body(result)
        self.assertEqual(body["status"], "OK")

    @pytest.mark.usefixtures("isolated_tasks")
    def test_create_validate_only_does_not_persist(self):
        subtasks = self._flagship_subtasks_payload()
        result = self._run_apply([
            "create",
            "ValOnly",
            "--parent", "TASK-010",
            "--tests", "unit",
            "--risks", "deps",
            "--subtasks", subtasks,
            "--description", "validation",
            "--validate-only",
        ])
        self.assertEqual(result.returncode, 0)
        body = _json_body(result)
        self.assertEqual(body["command"], "create.validate")
        self.assertEqual(body["status"], "OK")
        self.assertEqual(len(self._task_files()), 0)

    @pytest.mark.usefixtures("isolated_tasks")
    def test_create_validate_only_reports_errors(self):
        invalid_subtasks = json.dumps([
            {
                "title": "Too short",
                "criteria": ["metric"],
                "tests": ["unit"],
                "blockers": ["risk"],
            }
        ], ensure_ascii=False)
        result = self._run_apply([
            "create",
            "ValOnlyFail",
            "--parent", "TASK-011",
            "--tests", "unit",
            "--risks", "deps",
            "--subtasks", invalid_subtasks,
            "--description", "validation",
            "--validate-only",
        ])
        self.assertNotEqual(result.returncode, 0)
        body = _json_body(result)
        self.assertEqual(body["command"], "create.validate")
        self.assertEqual(body["status"], "ERROR")
        self.assertIn("issues", body["payload"])
        self.assertEqual(len(self._task_files()), 0)

    def test_ok_requires_subtasks_completed(self):
        # create task with subtasks (incomplete), then try mark OK -> expect failure
        subtasks = self._flagship_subtasks_payload()
        create_res = self._run_apply([
            "OkTest",
            "--parent", "TASK-002",
            "--tests", "unit",
            "--risks", "dep",
            "--subtasks", subtasks,
            "--description", "desc"
        ])
        self.assertEqual(create_res.returncode, 0)
        ok_res = self._run_apply(["done"])
        self.assertNotEqual(ok_res.returncode, 0)
        body = _json_body(ok_res)
        self.assertIn("Not all subtasks", body["message"])
        self.assertEqual(body["status"], "ERROR")
        self.assertNotIn("not found", body["message"])

    def test_done_unknown_task_reports_not_found_only(self):
        res = self._run_apply(["done", "999"])
        self.assertNotEqual(res.returncode, 0)
        body = _json_body(res)
        self.assertIn("not found", body["message"])
        self.assertEqual(body["status"], "ERROR")

    def test_invalid_subtasks_format_is_rejected(self):
        result = self._run_apply([
            "LegacyTitle",
            "--parent", "TASK-004",
            "--tests", "unit",
            "--risks", "deps",
            "--subtasks", "plan: unsupported format",
            "--description", "legacy input"
        ])
        self.assertNotEqual(result.returncode, 0)
        body = _json_body(result)
        self.assertIn("JSON", body["message"])
        self.assertEqual(body["status"], "ERROR")

    @pytest.mark.usefixtures("isolated_tasks")
    def test_task_validate_only_preview(self):
        subtasks = self._flagship_subtasks_payload()
        result = self._run_apply([
            "task",
            "Smart preview #feature",
            "--parent", "ROOT",
            "--tests", "unit",
            "--risks", "deps",
            "--subtasks", subtasks,
            "--description", "desc",
            "--validate-only",
        ])
        self.assertEqual(result.returncode, 0)
        body = _json_body(result)
        self.assertEqual(body["command"], "task.validate")
        self.assertEqual(body["status"], "OK")
        self.assertEqual(len(self._task_files()), 0)

    def test_macro_ok_closes_subtask(self):
        task_id = self._create_flagship_task("MacroOK")
        result = self._run_apply([
            "ok", task_id, "0",
            "--criteria-note", "confirmed criteria",
            "--tests-note", "tests evidence",
            "--blockers-note", "blockers cleared"
        ])
        self.assertEqual(result.returncode, 0)
        body = _json_body(result)
        self.assertEqual(body["status"], "OK")
        meta, _ = self._read_task_metadata(task_id)
        self.assertEqual(meta["progress"], 16)

    def test_macro_note_appends_evidence(self):
        task_id = self._create_flagship_task("MacroNote")
        result = self._run_apply([
            "note", task_id, "1",
            "--checkpoint", "criteria",
            "--note", "criteria evidence"
        ])
        self.assertEqual(result.returncode, 0)
        meta, content = self._read_task_metadata(task_id)
        self.assertIn("criteria evidence", content)

    def test_macro_bulk_executes_operations(self):
        task_id = self._create_flagship_task("MacroBulk")
        bulk_payload = json.dumps([
            {
                "task": task_id,
                "index": 2,
                "criteria": {"done": True, "note": "bulk criteria"},
                "tests": {"done": True, "note": "bulk tests"},
                "blockers": {"done": True, "note": "bulk blockers"},
                "complete": True
            }
        ], ensure_ascii=False)
        result = self._run_apply(["bulk", "--input", "-"], stdin_data=bulk_payload)
        self.assertEqual(result.returncode, 0)
        body = _json_body(result)
        self.assertEqual(body["status"], "OK")
        meta, _ = self._read_task_metadata(task_id)
        self.assertGreaterEqual(meta["progress"], 16)
        self.assertIn("subtask", body["payload"]["results"][0])

    def test_macro_shortcuts_use_last_task(self):
        task_id = self._create_flagship_task("MacroLast")
        ok_result = self._run_apply(["ok", ".", "0"])
        self.assertEqual(ok_result.returncode, 0)
        ok_body = _json_body(ok_result)
        self.assertEqual(ok_body["status"], "OK")
        self.assertTrue(ok_body["payload"]["subtask"]["completed"])

        note_result = self._run_apply([
            "note", ".", "1",
            "--checkpoint", "tests",
            "--note", "autoevidence"
        ])
        self.assertEqual(note_result.returncode, 0)
        note_body = _json_body(note_result)
        self.assertEqual(note_body["payload"]["subtask"]["tests_confirmed"], True)

    def test_bulk_infers_task_from_argument(self):
        self._create_flagship_task("MacroBulkDefault")
        bulk_payload = json.dumps([
            {
                "index": 0,
                "criteria": {"done": True, "note": "crit"},
                "tests": {"done": True, "note": "tests"},
                "blockers": {"done": True, "note": "blocks"},
                "complete": True
            }
        ], ensure_ascii=False)
        result = self._run_apply(["bulk", "--task", ".", "--input", "-"], stdin_data=bulk_payload)
        self.assertEqual(result.returncode, 0)
        body = _json_body(result)
        self.assertEqual(body["status"], "OK")
        self.assertEqual(body["payload"]["results"][0]["status"], "OK")
        self.assertIn("subtask", body["payload"]["results"][0])
        self.assertIn("checkpoint_states", body["payload"]["results"][0])

    def test_history_and_replay(self):
        hist_empty = self._run_apply(["history"])
        self.assertEqual(hist_empty.returncode, 0)
        body = _json_body(hist_empty)
        self.assertEqual(body["payload"]["entries"], [])
        self.assertIn("last_task", body["payload"])

        run_list = self._run_apply(["list"])
        self.assertEqual(run_list.returncode, 0)

        history_after = self._run_apply(["history", "1"])
        body_hist = _json_body(history_after)
        self.assertEqual(len(body_hist["payload"]["entries"]), 1)
        self.assertIn("list", body_hist["payload"]["entries"][0]["args"])
        self.assertIn("cli", body_hist["payload"]["entries"][0])

        replay_res = self._run_apply(["replay", "1"])
        self.assertEqual(replay_res.returncode, 0)

    def test_template_subtasks_generator(self):
        result = self._run_apply(["template", "subtasks", "--count", "4"])
        self.assertEqual(result.returncode, 0)
        body = _json_body(result)
        self.assertEqual(body["command"], "template.subtasks")
        self.assertEqual(body["status"], "OK")
        self.assertEqual(len(body["payload"]["template"]), 4)
        self.assertGreaterEqual(len(body["payload"]["tests_template"]), 1)
        self.assertGreaterEqual(len(body["payload"]["documentation_template"]), 1)

    @pytest.mark.usefixtures("isolated_tasks")
    def test_checkpoint_auto_completes_subtask(self):
        task_id = self._create_flagship_task("WizardFlow")
        result = self._run_apply([
            "checkpoint", task_id, "--subtask", "0", "--auto", "--note", "wizard"
        ])
        self.assertEqual(result.returncode, 0)
        body = _json_body(result)
        self.assertEqual(body["command"], "checkpoint")
        self.assertTrue(body["payload"]["completed"])
        self.assertGreaterEqual(len(body["payload"]["operations"]), 3)
        meta, _ = self._read_task_metadata(task_id)
        self.assertGreaterEqual(meta["progress"], 16)


@pytest.mark.usefixtures("isolated_tasks")
def test_sub_ok_alias():
    task_id = "TASK-ALIAS"
    now = datetime.now(timezone.utc).isoformat()
    task_file = _task_file(task_id)
    task_file.parent.mkdir(parents=True, exist_ok=True)
    task_file.write_text(
        "\n".join(
            [
                "---",
                f"id: {task_id}",
                "title: Alias test",
                "status: TODO",
                "priority: MEDIUM",
                f"created: {now}",
                f"updated: {now}",
                "assignee: ai",
                "---",
                "## Подзадачи",
                "- [ ] Demo subtask",
                "  - Критерии: crit1",
                "  - Тесты: test1",
                "  - Блокеры: block1",
            ]
        ),
        encoding="utf-8",
    )

    res = _run_apply_cmd(
        [
            "sub",
            "ok",
            task_id,
            "0",
            "--criteria-note",
            "cli criteria",
            "--tests-note",
            "cli tests",
            "--blockers-note",
            "cli blockers",
        ]
    )
    assert res.returncode == 0, res.stderr
    body = _json_body(res)
    assert body["command"] == "ok"
    assert body["status"] == "OK"
    st = body["payload"]["subtask"]
    assert st["criteria_confirmed"]
    assert st["tests_confirmed"]
    assert st["blockers_resolved"]
    assert st["completed"]


@pytest.mark.usefixtures("isolated_tasks")
def test_e2e_create_subtask_done_and_clean_with_filters():
    subtasks = json.dumps([
        {
            "title": "Plan e2e manual scenario >=20 chars",
            "criteria": ["scope locked"],
            "tests": ["doc"],
            "blockers": ["stakeholder signoff"]
        },
        {
            "title": "Execute e2e scenario actions across features",
            "criteria": ["commands executed"],
            "tests": ["manual"],
            "blockers": ["env ready"]
        },
        {
            "title": "Verify outputs and capture decision log",
            "criteria": ["log ready"],
            "tests": ["review"],
            "blockers": ["decision owners available"]
        }
    ], ensure_ascii=False)

    create_res = _run_apply_cmd([
        "E2E Flow #manual",
        "--parent", "TASK-010",
        "--tests", "manual",
        "--risks", "human",
        "--phase", "manual-phase",
        "--subtasks", subtasks,
        "--description", "E2E JSON flow"
    ])
    assert create_res.returncode == 0, create_res.stdout + create_res.stderr
    task_id, folder = _read_last_task_ref()
    assert task_id is not None

    assert _run_apply_cmd(["start", task_id]).returncode == 0
    for idx in range(3):
        assert _run_apply_cmd([
            "subtask", task_id, "--criteria-done", str(idx), "--note", f"criteria evidence {idx}"
        ]).returncode == 0
        assert _run_apply_cmd([
            "subtask", task_id, "--tests-done", str(idx), "--note", f"pytest evidence {idx}"
        ]).returncode == 0
        assert _run_apply_cmd([
            "subtask", task_id, "--blockers-done", str(idx), "--note", f"blocker cleared {idx}"
        ]).returncode == 0
        assert _run_apply_cmd(["subtask", task_id, "--done", str(idx)]).returncode == 0
    assert _run_apply_cmd(["done", task_id]).returncode == 0

    meta = yaml.safe_load(_task_file(task_id, folder).read_text().split("---")[1])
    assert meta.get("status") == "DONE"

    dry_run_res = _run_apply_cmd(["clean", "--tag", "manual", "--dry-run"])
    assert dry_run_res.returncode == 0
    dry_payload = _json_body(dry_run_res)
    assert task_id in dry_payload["payload"]["matched"]

    clean_res = _run_apply_cmd(["clean", "--tag", "manual", "--status", "DONE", "--phase", "manual-phase"])
    assert clean_res.returncode == 0
    clean_payload = _json_body(clean_res)
    assert clean_payload["payload"]["removed"] >= 1
    assert not _task_file(task_id, folder).exists()



if __name__ == "__main__":
    unittest.main()
