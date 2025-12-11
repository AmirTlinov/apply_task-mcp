#!/usr/bin/env python3
"""Devtools automation CLI commands."""

import argparse
import json
import shlex
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional

from core.desktop.devtools.application.context import normalize_task_id, get_last_task
from core.desktop.devtools.interface.cli_io import structured_response
from .projects_integration import _projects_status_payload


AUTOMATION_TMP = Path(".tmp")


def _ensure_tmp_dir() -> Path:
    """Ensure automation temp directory exists."""
    AUTOMATION_TMP.mkdir(parents=True, exist_ok=True)
    return AUTOMATION_TMP


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    """Write JSON data to file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _automation_subtask_entry(index: int, coverage: int, risks: str, sla: str) -> Dict[str, Any]:
    """Create automation subtask entry."""
    return {
        "title": f"Subtask {index}: plan and validate",
        "criteria": [
            f"Coverage ≥{coverage}%",
            f"SLA {sla}",
            "Risks enumerated and mitigations defined",
        ],
        "tests": [
            f"pytest -q --maxfail=1 --cov=. --cov-report=xml (target ≥{coverage}%)",
            "perf/regression suite with evidence in logs",
        ],
        "blockers": [
            "Dependencies and approvals recorded",
            f"Risks: {risks}",
        ],
    }


def _automation_template_payload(count: int, coverage: int, risks: str, sla: str) -> Dict[str, Any]:
    """Create automation template payload."""
    count = max(3, count)
    subtasks = [_automation_subtask_entry(i + 1, coverage, risks, sla) for i in range(count)]
    return {
        "defaults": {"coverage": coverage, "risks": risks, "sla": sla},
        "usage": "apply_task automation task-create \"Title\" --parent TASK-XXX --description \"...\" --subtasks @.tmp/subtasks.template.json",
        "subtasks": subtasks,
    }


def cmd_automation_task_template(args: argparse.Namespace) -> int:
    """Generate task template for automation."""
    payload = _automation_template_payload(args.count, args.coverage, args.risks, args.sla)
    output_path = Path(args.output or (AUTOMATION_TMP / "subtasks.template.json"))
    _ensure_tmp_dir()
    _write_json(output_path, payload)
    return structured_response(
        "automation.task-template",
        status="OK",
        message=f"Шаблон сохранён: {output_path}",
        payload={"output": str(output_path.resolve()), "count": len(payload["subtasks"]), "defaults": payload["defaults"]},
        summary=str(output_path),
    )


def _resolve_parent(default_parent: Optional[str]) -> Optional[str]:
    """Resolve parent task ID."""
    if default_parent:
        return normalize_task_id(default_parent)
    last_id, _ = get_last_task()
    return normalize_task_id(last_id) if last_id else None


def _load_note(log_path: Path, fallback: str) -> str:
    """Load note from log file."""
    if log_path.exists():
        text = log_path.read_text(encoding="utf-8").strip()
        if text:
            return text[:1000]
    return fallback


def cmd_automation_task_create(args: argparse.Namespace) -> int:
    """Create task via automation (delegates to cli_guided)."""
    from core.desktop.devtools.interface.cli_guided import cmd_automation_task_create as _impl
    return _impl(args)


def cmd_automation_projects_health(args: argparse.Namespace) -> int:
    """Check GitHub Projects health status."""
    payload = _projects_status_payload(force_refresh=True)
    summary = f"target={payload.get('target_label','—')} auto-sync={str(payload.get('auto_sync')).lower()} token={'yes' if payload.get('token_present') else 'no'} rate={payload.get('rate_remaining')}/{payload.get('rate_reset_human') or '-'}"
    return structured_response(
        "automation.projects-health",
        status="OK",
        message=payload.get("status_reason", "") or "Projects status",
        payload=payload,
        summary=summary,
    )


def cmd_automation_health(args: argparse.Namespace) -> int:
    """Run health check (pytest) and save results."""
    _ensure_tmp_dir()
    log_path = Path(args.log or (AUTOMATION_TMP / "health.log"))
    pytest_cmd = args.pytest_cmd.strip()
    result = {"pytest_cmd": pytest_cmd, "rc": 0, "stdout": "", "stderr": ""}
    if pytest_cmd:
        try:
            proc = subprocess.run(shlex.split(pytest_cmd), capture_output=True, text=True)
            result["rc"] = proc.returncode
            result["stdout"] = (proc.stdout or "").strip()
            result["stderr"] = (proc.stderr or "").strip()
        except FileNotFoundError as exc:
            result["rc"] = 1
            result["stderr"] = str(exc)
    _write_json(log_path, result)
    status = "OK" if result["rc"] == 0 else "ERROR"
    return structured_response(
        "automation.health",
        status=status,
        message="pytest выполнен" if pytest_cmd else "pytest пропущен",
        payload={"log": str(log_path.resolve()), **result},
        summary=f"log={log_path} rc={result['rc']}",
        exit_code=0 if status == "OK" else 1,
    )


def cmd_automation_checkpoint(args: argparse.Namespace) -> int:
    """Create checkpoint via automation (delegates to cli_guided)."""
    from core.desktop.devtools.interface.cli_guided import cmd_automation_checkpoint as _impl
    return _impl(args)
