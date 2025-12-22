"""Deterministic templates and scaffolding for plans/tasks.

Goals:
- Fast, disciplined setup for common work types (feature/bugfix/refactor/migration).
- No magic: explicit template + kind + title, with safe default dry_run=true.
- Reusable across adapters (MCP/TUI/GUI): data lives in application layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from core import Step, TaskDetail
from core.desktop.devtools.application.task_manager import TaskManager, _flatten_steps


@dataclass(frozen=True)
class TemplateStep:
    title: str
    success_criteria: List[str]
    tests: List[str]
    blockers: List[str]


@dataclass(frozen=True)
class TemplateTask:
    contract_data: Dict[str, Any]
    success_criteria: List[str]
    tests: List[str]
    steps: List[TemplateStep]


@dataclass(frozen=True)
class TemplatePlan:
    contract_data: Dict[str, Any]
    plan_doc: str
    plan_steps: List[str]


@dataclass(frozen=True)
class Template:
    template_id: str
    name: str
    description: str
    plan: Optional[TemplatePlan] = None
    task: Optional[TemplateTask] = None

    @property
    def supports(self) -> List[str]:
        kinds: List[str] = []
        if self.plan is not None:
            kinds.append("plan")
        if self.task is not None:
            kinds.append("task")
        return kinds

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.template_id,
            "name": self.name,
            "description": self.description,
            "supports": self.supports,
        }


def _contract_skeleton(*, goal: str) -> Dict[str, Any]:
    return {
        "goal": goal,
        "constraints": [],
        "assumptions": [],
        "non_goals": [],
        "done": [],
        "risks": [],
        "checks": ["pytest -q"],
    }


_TEMPLATES: List[Template] = [
    Template(
        template_id="feature",
        name="Feature delivery",
        description="Vertical slice feature: contract → implement → verify → handoff.",
        plan=TemplatePlan(
            contract_data=_contract_skeleton(goal="<feature goal>"),
            plan_doc="Keep this plan as a strategy doc (not a TODO list). Use tasks/steps for execution.",
            plan_steps=["Contract", "Design", "Implement", "Verify", "Handoff"],
        ),
        task=TemplateTask(
            contract_data=_contract_skeleton(goal="<deliver feature safely>"),
            success_criteria=["All acceptance criteria met", "No regressions; CI green"],
            tests=["pytest -q"],
            steps=[
                TemplateStep(
                    title="Clarify contract and acceptance",
                    success_criteria=["Goal/constraints/non-goals/done/checks are explicit"],
                    tests=["tasks_lint shows no errors"],
                    blockers=["Missing requirements/questions answered"],
                ),
                TemplateStep(
                    title="Implement core behavior",
                    success_criteria=["Behavior implemented end-to-end for the happy path"],
                    tests=["pytest -q -k <focus>"],
                    blockers=["API/DB access", "Dependencies available"],
                ),
                TemplateStep(
                    title="Harden edge cases and invariants",
                    success_criteria=["Edge cases covered; invariants enforced"],
                    tests=["pytest -q"],
                    blockers=["Unknown edge cases enumerated"],
                ),
                TemplateStep(
                    title="Verify and handoff",
                    success_criteria=["Checks passed; docs/notes updated"],
                    tests=["pytest -q"],
                    blockers=["Release checklist agreed"],
                ),
            ],
        ),
    ),
    Template(
        template_id="bugfix",
        name="Bugfix",
        description="Bugfix: reproduce → root cause → fix → regression guard.",
        plan=TemplatePlan(
            contract_data=_contract_skeleton(goal="<fix bug without regressions>"),
            plan_doc="Record reproduction and evidence in steps. Keep contract tight and test-driven.",
            plan_steps=["Reproduce", "Root cause", "Fix", "Guard", "Verify"],
        ),
        task=TemplateTask(
            contract_data=_contract_skeleton(goal="<fix bug>"),
            success_criteria=["Bug reproduced and fixed", "Regression test added", "CI green"],
            tests=["pytest -q"],
            steps=[
                TemplateStep(
                    title="Reproduce and narrow the failing case",
                    success_criteria=["Minimal reproduction exists", "Expected/actual documented"],
                    tests=["pytest -q -k <failing_test>"],
                    blockers=["Access to logs/env"],
                ),
                TemplateStep(
                    title="Identify root cause",
                    success_criteria=["Root cause isolated; fix approach chosen"],
                    tests=["tasks_lint shows no errors"],
                    blockers=["Missing context/data"],
                ),
                TemplateStep(
                    title="Implement fix + add regression test",
                    success_criteria=["Fix implemented and covered by test"],
                    tests=["pytest -q -k <new_test>"],
                    blockers=["Flaky repro stabilized"],
                ),
                TemplateStep(
                    title="Run full verification",
                    success_criteria=["Full suite green"],
                    tests=["pytest -q"],
                    blockers=["CI parity checked"],
                ),
            ],
        ),
    ),
    Template(
        template_id="refactor",
        name="Refactor",
        description="Behavior-preserving refactor: baseline → refactor → harden → verify.",
        plan=TemplatePlan(
            contract_data=_contract_skeleton(goal="<simplify architecture safely>"),
            plan_doc="Stage refactors: (1) tests/observability baseline → (2) refactor → (3) behavior change (if any) → (4) hardening.",
            plan_steps=["Baseline", "Refactor", "Harden", "Verify", "Handoff"],
        ),
        task=TemplateTask(
            contract_data=_contract_skeleton(goal="<refactor safely>"),
            success_criteria=["Behavior preserved", "Complexity reduced", "CI green"],
            tests=["pytest -q"],
            steps=[
                TemplateStep(
                    title="Establish baseline and safety net",
                    success_criteria=["Relevant tests identified; coverage gap noted"],
                    tests=["pytest -q -k <area>"],
                    blockers=["Missing tests"],
                ),
                TemplateStep(
                    title="Refactor (behavior-preserving)",
                    success_criteria=["Architecture simplified without changing behavior"],
                    tests=["pytest -q -k <area>"],
                    blockers=["Implicit coupling identified"],
                ),
                TemplateStep(
                    title="Harden and verify",
                    success_criteria=["Edge cases and docs updated"],
                    tests=["pytest -q"],
                    blockers=["Performance/security considerations reviewed"],
                ),
            ],
        ),
    ),
    Template(
        template_id="migration",
        name="Migration",
        description="Migration: inventory → plan → implement → validate → rollout.",
        plan=TemplatePlan(
            contract_data=_contract_skeleton(goal="<migrate safely>"),
            plan_doc="Write migration strategy here: scope, phases, rollback, data safety, verification.",
            plan_steps=["Inventory", "Plan", "Implement", "Validate", "Rollout"],
        ),
        task=TemplateTask(
            contract_data=_contract_skeleton(goal="<perform migration safely>"),
            success_criteria=["Migration completed safely", "Rollback path defined", "CI green"],
            tests=["pytest -q"],
            steps=[
                TemplateStep(
                    title="Inventory and define migration contract",
                    success_criteria=["Scope/risks/rollback/checks explicit in contract_data"],
                    tests=["tasks_lint shows no errors"],
                    blockers=["Access to data/schema/env"],
                ),
                TemplateStep(
                    title="Implement migration (behavior-preserving where required)",
                    success_criteria=["Migration implemented with safe guards and idempotency where applicable"],
                    tests=["pytest -q -k <area>"],
                    blockers=["Deployment constraints"],
                ),
                TemplateStep(
                    title="Validate on realistic data",
                    success_criteria=["Validation evidence recorded; performance and data integrity checked"],
                    tests=["pytest -q"],
                    blockers=["Test dataset or staging access"],
                ),
                TemplateStep(
                    title="Rollout and handoff",
                    success_criteria=["Rollout steps documented; monitoring/alerts checked"],
                    tests=["pytest -q"],
                    blockers=["Release checklist agreed"],
                ),
            ],
        ),
    ),
]


def list_templates() -> List[Template]:
    return sorted(list(_TEMPLATES), key=lambda t: t.template_id)


def get_template(template_id: str) -> Optional[Template]:
    tid = str(template_id or "").strip().lower()
    if not tid:
        return None
    for t in _TEMPLATES:
        if t.template_id == tid:
            return t
    return None


def build_plan_from_template(manager: TaskManager, template: Template, *, title: str, priority: str = "MEDIUM") -> TaskDetail:
    if template.plan is None:
        raise ValueError(f"template does not support plan: {template.template_id}")
    plan = manager.create_plan(str(title), priority=str(priority or "MEDIUM"))
    plan.contract_data = dict(template.plan.contract_data or {})
    plan.plan_doc = str(template.plan.plan_doc or "")
    plan.plan_steps = list(template.plan.plan_steps or [])
    plan.plan_current = 0
    plan.update_status_from_progress()
    return plan


def build_task_from_template(
    manager: TaskManager,
    template: Template,
    *,
    title: str,
    parent: str,
    priority: str = "MEDIUM",
) -> TaskDetail:
    if template.task is None:
        raise ValueError(f"template does not support task: {template.template_id}")
    task = manager.create_task(str(title), parent=str(parent), priority=str(priority or "MEDIUM"))
    task.contract_data = dict(template.task.contract_data or {})
    task.success_criteria = list(template.task.success_criteria or [])
    task.tests = list(template.task.tests or [])
    task.tests_confirmed = False
    task.tests_auto_confirmed = not bool(task.tests)
    task.criteria_confirmed = False
    task.criteria_auto_confirmed = False

    steps: List[Step] = []
    for st in list(template.task.steps or []):
        step = Step.new(
            st.title,
            criteria=list(st.success_criteria or []),
            tests=list(st.tests or []),
            blockers=list(st.blockers or []),
            created_at=None,
        )
        if step is None:
            raise ValueError(f"invalid template step: {st.title}")
        steps.append(step)
    task.steps = steps
    task.update_status_from_progress()
    return task


def apply_preview_ids(detail: TaskDetail) -> None:
    """Replace volatile ids with deterministic preview ids (dry_run only)."""
    if getattr(detail, "kind", "task") == "task":
        for idx, (_path, step) in enumerate(_flatten_steps(list(getattr(detail, "steps", []) or []))):
            setattr(step, "id", f"STEP-PREVIEW-{idx}")
            plan = getattr(step, "plan", None)
            tasks = list(getattr(plan, "tasks", []) or []) if plan else []
            for t_idx, node in enumerate(tasks):
                setattr(node, "id", f"NODE-PREVIEW-{idx}-{t_idx}")


__all__ = [
    "Template",
    "list_templates",
    "get_template",
    "build_plan_from_template",
    "build_task_from_template",
    "apply_preview_ids",
]
