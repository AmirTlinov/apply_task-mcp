"""Best-effort evidence collectors for verification.

These helpers are adapter-level and MUST be safe-by-default:
- never collect or expose secrets
- never raise (callers treat as optional enrichment)
- keep payloads compact and deterministic (digest-friendly)
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable, List, Mapping, Optional

from core import VerificationCheck


def _is_truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def collect_github_actions_check(env: Mapping[str, str] | None = None) -> Optional[VerificationCheck]:
    env = env or os.environ
    if not _is_truthy(env.get("GITHUB_ACTIONS")):
        return None

    sha = str(env.get("GITHUB_SHA", "") or "").strip()
    repository = str(env.get("GITHUB_REPOSITORY", "") or "").strip()
    run_id = str(env.get("GITHUB_RUN_ID", "") or "").strip()
    server_url = str(env.get("GITHUB_SERVER_URL", "") or "").strip() or "https://github.com"
    workflow = str(env.get("GITHUB_WORKFLOW", "") or "").strip()
    ref = str(env.get("GITHUB_REF_NAME", "") or "").strip() or str(env.get("GITHUB_REF", "") or "").strip()
    run_attempt = str(env.get("GITHUB_RUN_ATTEMPT", "") or "").strip()

    run_url = ""
    if server_url and repository and run_id:
        run_url = f"{server_url.rstrip('/')}/{repository}/actions/runs/{run_id}"

    details = {
        "sha": sha,
        "repository": repository,
        "run_id": run_id,
        "run_url": run_url,
        "workflow": workflow,
        "ref": ref,
        "run_attempt": run_attempt,
    }
    details = {k: v for k, v in details.items() if v}

    short = sha[:12] if sha else ""
    preview = f"github_actions run {run_id} {short}".strip()
    return VerificationCheck.from_dict(
        {
            "kind": "ci",
            "spec": "github_actions",
            "outcome": "info",
            "preview": preview,
            "details": details,
        }
    )


def _run_git(args: List[str], *, cwd: Path, timeout_s: float = 2.0) -> Optional[str]:
    try:
        env = dict(os.environ)
        env["GIT_TERMINAL_PROMPT"] = "0"
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=float(timeout_s),
            env=env,
        )
        if result.returncode != 0:
            return None
        return str(result.stdout or "").strip()
    except Exception:
        return None


def collect_git_check(
    project_root: Path,
    *,
    run_git: Callable[[List[str]], Optional[str]] | None = None,
) -> Optional[VerificationCheck]:
    root = Path(project_root).resolve()
    if not root.exists():
        return None

    runner = run_git or (lambda args: _run_git(args, cwd=root))
    sha = runner(["rev-parse", "HEAD"])
    if not sha:
        return None

    branch = runner(["rev-parse", "--abbrev-ref", "HEAD"]) or "HEAD"
    status = runner(["status", "--porcelain"]) or ""
    dirty = bool(status.strip())
    changed = len([line for line in status.splitlines() if line.strip()])
    describe = runner(["describe", "--always", "--dirty"]) or ""

    details = {
        "sha": sha,
        "branch": branch,
        "dirty": dirty,
        "changed_files": changed,
        "describe": describe,
    }
    preview = f"git {sha[:12]} {'dirty' if dirty else 'clean'}".strip()
    return VerificationCheck.from_dict(
        {
            "kind": "git",
            "spec": "head",
            "outcome": "info",
            "preview": preview,
            "details": details,
        }
    )


def collect_auto_verification_checks(project_root: Path, env: Mapping[str, str] | None = None) -> List[VerificationCheck]:
    checks: List[VerificationCheck] = []
    ci = collect_github_actions_check(env)
    if ci:
        checks.append(ci)
    git = collect_git_check(project_root)
    if git:
        checks.append(git)
    return checks

