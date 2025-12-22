"""Read-only linting for Plans/Tasks (preflight engineering discipline).

This module contains pure-ish analysis helpers that can be reused by UI adapters
and the MCP intent API. It does not mutate storage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core import Step, TaskDetail, TaskNode
from core.desktop.devtools.application.plan_hygiene import plan_doc_overlap_reasons, plan_steps_overlap_reasons
from core.desktop.devtools.application.task_manager import _flatten_steps


Severity = str  # "error" | "warning"


@dataclass(frozen=True)
class LintIssue:
    code: str
    severity: Severity
    message: str
    target: Dict[str, Any] = field(default_factory=dict)
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "target": dict(self.target or {}),
            "details": dict(self.details or {}),
        }


@dataclass(frozen=True)
class LintReport:
    item_id: str
    kind: str  # "plan" | "task"
    revision: int
    issues: List[LintIssue]

    def to_dict(self) -> Dict[str, Any]:
        errors = sum(1 for i in self.issues if i.severity == "error")
        warnings = sum(1 for i in self.issues if i.severity != "error")
        return {
            "item_id": self.item_id,
            "kind": self.kind,
            "revision": int(self.revision),
            "summary": {"errors": errors, "warnings": warnings, "total": len(self.issues)},
            "issues": [i.to_dict() for i in self.issues],
        }


def _task_status_map(all_items: List[TaskDetail]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for item in all_items:
        item_id = str(getattr(item, "id", "") or "").strip()
        if not item_id:
            continue
        out[item_id] = str(getattr(item, "status", "") or "").strip().upper()
    return out
def _is_done_task(item: TaskDetail) -> bool:
    status = str(getattr(item, "status", "") or "").strip().upper()
    if status == "DONE":
        return True
    try:
        return int(item.calculate_progress()) == 100 and not bool(getattr(item, "blocked", False))
    except Exception:
        return False


def _validate_depends_on_for_task(task_id: str, depends_on: List[str], all_items: List[TaskDetail]) -> Tuple[Optional[str], Dict[str, Any]]:
    """Pure validation for depends_on using already loaded items (no I/O).

    Returns:
        (error_code, details). error_code is one of:
          - INVALID_DEPENDENCY_ID
          - INVALID_DEPENDENCIES
          - CIRCULAR_DEPENDENCY
        On success: (None, details) with possible unresolved_depends_on.
    """
    raw_deps = [str(d or "").strip().upper() for d in list(depends_on or []) if str(d or "").strip()]
    if not raw_deps:
        return None, {}
    for dep_id in raw_deps:
        if not dep_id.startswith("TASK-") or not dep_id[5:].isdigit():
            return "INVALID_DEPENDENCY_ID", {"got": dep_id, "expected": "TASK-###"}

    tasks_only = [t for t in list(all_items or []) if str(getattr(t, "kind", "task") or "task") == "task"]
    existing_ids = {str(getattr(t, "id", "") or "") for t in tasks_only if str(getattr(t, "id", "") or "").strip()}
    from core import validate_dependencies, build_dependency_graph

    dep_graph = build_dependency_graph(
        [
            (str(getattr(t, "id", "") or ""), list(getattr(t, "depends_on", []) or []))
            for t in tasks_only
            if str(getattr(t, "id", "") or "") and str(getattr(t, "id", "") or "") != str(task_id)
        ]
    )
    errors, cycle = validate_dependencies(str(task_id), raw_deps, existing_ids, dep_graph)
    if errors:
        return "INVALID_DEPENDENCIES", {"errors": [str(e) for e in errors]}
    if cycle:
        return "CIRCULAR_DEPENDENCY", {"cycle": cycle}

    unresolved: List[str] = []
    statuses = {str(getattr(t, "id", "") or ""): str(getattr(t, "status", "") or "") for t in tasks_only}
    by_id = {str(getattr(t, "id", "") or ""): t for t in tasks_only}
    for dep_id in raw_deps:
        item = by_id.get(dep_id)
        if not item:
            unresolved.append(dep_id)
            continue
        if not _is_done_task(item):
            unresolved.append(dep_id)
    details: Dict[str, Any] = {}
    if unresolved:
        details["unresolved_depends_on"] = unresolved
    # Keep both status and computed done flag for clarity.
    details["depends_on_status"] = {d: str(statuses.get(d, "") or "").strip().upper() for d in raw_deps}
    return None, details


def _lint_plan(detail: TaskDetail) -> List[LintIssue]:
    issues: List[LintIssue] = []

    steps = list(getattr(detail, "plan_steps", []) or [])
    current = int(getattr(detail, "plan_current", 0) or 0)
    if not steps:
        issues.append(
            LintIssue(
                code="PLAN_STEPS_EMPTY",
                severity="warning",
                message="План без checklist шагов (plan_steps пуст).",
                target={"kind": "task_detail"},
            )
        )
    if steps and (current < 0 or current > len(steps)):
        issues.append(
            LintIssue(
                code="PLAN_CURRENT_OUT_OF_RANGE",
                severity="error",
                message=f"plan_current вне диапазона: current={current}, total={len(steps)}.",
                target={"kind": "task_detail"},
                details={"current": current, "total": len(steps)},
            )
        )

    doc = str(getattr(detail, "plan_doc", "") or "")
    doc_reasons = plan_doc_overlap_reasons(doc)
    if doc_reasons:
        issues.append(
            LintIssue(
                code="PLAN_DOC_OVERLAP",
                severity="warning",
                message="plan_doc выглядит как смешение артефактов (контракт/шаги/чеклист).",
                target={"kind": "task_detail"},
                details={"reasons": doc_reasons},
            )
        )
    steps_reasons = plan_steps_overlap_reasons(steps)
    if steps_reasons:
        issues.append(
            LintIssue(
                code="PLAN_STEPS_OVERLAP",
                severity="warning",
                message="plan_steps похожи на чеклист/вставленные step_id; лучше держать их как фазы/маршрут.",
                target={"kind": "task_detail"},
                details={"reasons": steps_reasons},
            )
        )

    contract_data = dict(getattr(detail, "contract_data", {}) or {})
    goal = str(contract_data.get("goal", "") or "").strip()
    done = list(contract_data.get("done", []) or []) if isinstance(contract_data.get("done", []), list) else []
    checks = list(contract_data.get("checks", []) or []) if isinstance(contract_data.get("checks", []), list) else []
    if not goal and not str(getattr(detail, "contract", "") or "").strip():
        issues.append(
            LintIssue(
                code="CONTRACT_GOAL_MISSING",
                severity="warning",
                message="Не задана цель (contract_data.goal) и contract пуст: Why в радаре будет слабым.",
                target={"kind": "task_detail"},
            )
        )
    if not done:
        issues.append(
            LintIssue(
                code="DONE_CRITERIA_MISSING",
                severity="warning",
                message="Не заданы критерии готовности (contract_data.done).",
                target={"kind": "task_detail"},
            )
        )
    if not checks:
        issues.append(
            LintIssue(
                code="CHECKS_MISSING",
                severity="warning",
                message="Не заданы проверки (contract_data.checks): How to verify будет неполным.",
                target={"kind": "task_detail"},
            )
        )
    return issues


def _lint_task(detail: TaskDetail, manager, all_items: List[TaskDetail]) -> List[LintIssue]:
    issues: List[LintIssue] = []

    # Root DoD blocks `done` semantics later.
    if not list(getattr(detail, "success_criteria", []) or []):
        issues.append(
            LintIssue(
                code="TASK_SUCCESS_CRITERIA_MISSING",
                severity="error",
                message="У задания нет root success_criteria — это заблокирует финальный done.",
                target={"kind": "task_detail"},
            )
        )

    # Dependencies correctness + blockers.
    depends_on = list(getattr(detail, "depends_on", []) or [])
    if depends_on:
        code, details = _validate_depends_on_for_task(str(detail.id), list(depends_on), all_items)
        if code:
            issues.append(
                LintIssue(
                    code=str(code),
                    severity="error",
                    message="depends_on невалиден (несуществующие задачи или цикл).",
                    target={"kind": "task_detail"},
                    details=dict(details or {}),
                )
            )
        else:
            blocking = list((details or {}).get("unresolved_depends_on", []) or [])
            if blocking:
                issues.append(
                    LintIssue(
                        code="DEPENDS_ON_BLOCKING",
                        severity="warning",
                        message="Есть незавершённые зависимости (depends_on) — задача логически заблокирована.",
                        target={"kind": "task_detail"},
                        details={"unresolved_depends_on": blocking},
                    )
                )

    # Step tree checks.
    for path, step in _flatten_steps(list(getattr(detail, "steps", []) or [])):
        target = {"kind": "step", "path": path, "step_id": str(getattr(step, "id", "") or "")}

        if not list(getattr(step, "success_criteria", []) or []):
            issues.append(
                LintIssue(
                    code="STEP_SUCCESS_CRITERIA_MISSING",
                    severity="error",
                    message="У шага нет success_criteria (критерии обязательны).",
                    target=target,
                )
            )

        tests_list = list(getattr(step, "tests", []) or [])
        if not tests_list and not bool(getattr(step, "tests_auto_confirmed", False)):
            # Defensive: should not happen, but keep stable.
            issues.append(
                LintIssue(
                    code="STEP_TESTS_INCONSISTENT",
                    severity="warning",
                    message="У шага пустые tests без tests_auto_confirmed=true (несогласованное состояние).",
                    target=target,
                )
            )
        if not tests_list:
            issues.append(
                LintIssue(
                    code="STEP_TESTS_MISSING",
                    severity="warning",
                    message="У шага нет tests (проверка выполнения).",
                    target=target,
                )
            )

        blockers_list = list(getattr(step, "blockers", []) or [])
        if not blockers_list:
            issues.append(
                LintIssue(
                    code="STEP_BLOCKERS_MISSING",
                    severity="warning",
                    message="У шага нет blockers (зависимости/риски/ожидания).",
                    target=target,
                )
            )

        title = str(getattr(step, "title", "") or "")
        if len(title.strip()) < 20:
            issues.append(
                LintIssue(
                    code="STEP_TITLE_TOO_SHORT",
                    severity="warning",
                    message="Слишком короткий title у шага (желательно ≥ 20 символов).",
                    target=target,
                    details={"min_len": 20, "len": len(title.strip())},
                )
            )
        atomic_violators = ["и затем", "потом", "после этого", "далее", ", и ", " and then", " then "]
        lowered = title.lower()
        if any(v in lowered for v in atomic_violators):
            issues.append(
                LintIssue(
                    code="STEP_NOT_ATOMIC",
                    severity="warning",
                    message="Шаг выглядит неатомарным (есть маркеры последовательности: 'потом/и затем/then').",
                    target=target,
                )
            )

        # Open checkpoints signal (actionable).
        computed = str(getattr(step, "computed_status", "") or "")
        if computed != "pending":
            if not bool(getattr(step, "criteria_confirmed", False)):
                issues.append(
                    LintIssue(
                        code="CHECKPOINT_CRITERIA_OPEN",
                        severity="warning",
                        message="Чекпоинт criteria не подтверждён.",
                        target=target,
                    )
                )
            if not (bool(getattr(step, "tests_confirmed", False)) or bool(getattr(step, "tests_auto_confirmed", False))):
                issues.append(
                    LintIssue(
                        code="CHECKPOINT_TESTS_OPEN",
                        severity="warning",
                        message="Чекпоинт tests не подтверждён (и не auto).",
                        target=target,
                    )
                )

        # Evidence ("black box") signal: when checkpoints are confirmed but no evidence is recorded.
        try:
            ready = bool(step.ready_for_completion()) or bool(getattr(step, "completed", False))
        except Exception:
            ready = bool(getattr(step, "completed", False))
        if ready:
            has_outcome = bool(str(getattr(step, "verification_outcome", "") or "").strip())
            has_checks = bool(list(getattr(step, "verification_checks", []) or []))
            has_attachments = bool(list(getattr(step, "attachments", []) or []))
            if not (has_outcome or has_checks or has_attachments):
                issues.append(
                    LintIssue(
                        code="EVIDENCE_MISSING",
                        severity="warning",
                        message="Чекпоинты подтверждены, но нет evidence (verification_outcome/checks/attachments).",
                        target=target,
                    )
                )

        # Nested task nodes inside this step plan.
        plan = getattr(step, "plan", None)
        tasks = list(getattr(plan, "tasks", []) or []) if plan else []
        for idx, node in enumerate(tasks):
            node_path = f"{path}.t:{idx}"
            node_target = {"kind": "task_node", "path": node_path, "task_node_id": str(getattr(node, 'id', '') or '')}
            _lint_task_node(node, node_target, issues)

    return issues


def _lint_task_node(node: TaskNode, target: Dict[str, Any], issues: List[LintIssue]) -> None:
    title = str(getattr(node, "title", "") or "").strip()
    if not title:
        issues.append(
            LintIssue(
                code="TASK_NODE_TITLE_MISSING",
                severity="error",
                message="У task node нет title.",
                target=target,
            )
        )
    if not list(getattr(node, "success_criteria", []) or []):
        issues.append(
            LintIssue(
                code="TASK_NODE_SUCCESS_CRITERIA_MISSING",
                severity="warning",
                message="У task node нет success_criteria (желательно для дисциплины).",
                target=target,
            )
        )
    if not list(getattr(node, "tests", []) or []):
        issues.append(
            LintIssue(
                code="TASK_NODE_TESTS_MISSING",
                severity="warning",
                message="У task node нет tests (желательно для дисциплины).",
                target=target,
            )
        )


def lint_item(manager, detail: TaskDetail, all_items: Optional[List[TaskDetail]] = None) -> LintReport:
    all_items = list(all_items or [])
    item_id = str(getattr(detail, "id", "") or "")
    kind = str(getattr(detail, "kind", "task") or "task")
    revision = int(getattr(detail, "revision", 0) or 0)

    issues: List[LintIssue] = []
    if kind == "plan":
        issues.extend(_lint_plan(detail))
    else:
        issues.extend(_lint_task(detail, manager, all_items))
    # Stable ordering: errors first, then warnings, then by code.
    issues_sorted = sorted(issues, key=lambda i: (0 if i.severity == "error" else 1, i.code))
    return LintReport(item_id=item_id, kind=kind, revision=revision, issues=issues_sorted)


__all__ = ["LintIssue", "LintReport", "lint_item"]
