from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import yaml

from .step import Step, _flatten_step_tree
from .evidence import Attachment, VerificationCheck
from .step_event import StepEvent


@dataclass
class TaskDetail:
    id: str
    title: str
    status: str
    kind: Literal["plan", "task"] = "task"
    schema_version: int = 8
    revision: int = 0  # Monotonic storage revision (optimistic concurrency / etag-like)
    status_manual: bool = False  # True when status was explicitly set by user and should not be auto-recalculated
    description: str = ""
    domain: str = ""
    phase: str = ""
    component: str = ""
    parent: Optional[str] = None  # Parent id (PLAN-###) or None/ROOT for top-level plans
    contract: str = ""
    contract_versions: List[Dict[str, Any]] = field(default_factory=list)
    contract_data: Dict[str, Any] = field(default_factory=dict)
    plan_doc: str = ""
    plan_steps: List[str] = field(default_factory=list)
    plan_current: int = 0
    priority: str = "MEDIUM"
    created: str = ""
    updated: str = ""
    tags: List[str] = field(default_factory=list)
    assignee: str = ""
    progress: int = 0
    blocked: bool = False
    blockers: List[str] = field(default_factory=list)
    context: str = ""
    success_criteria: List[str] = field(default_factory=list)
    tests: List[str] = field(default_factory=list)
    criteria_confirmed: bool = False
    tests_confirmed: bool = False
    criteria_auto_confirmed: bool = False  # Never auto - criteria always required
    tests_auto_confirmed: bool = False     # Auto-OK if tests[] was empty
    criteria_notes: List[str] = field(default_factory=list)
    tests_notes: List[str] = field(default_factory=list)
    security_confirmed: bool = False
    perf_confirmed: bool = False
    docs_confirmed: bool = False
    security_notes: List[str] = field(default_factory=list)
    perf_notes: List[str] = field(default_factory=list)
    docs_notes: List[str] = field(default_factory=list)
    criteria_evidence_refs: List[str] = field(default_factory=list)
    tests_evidence_refs: List[str] = field(default_factory=list)
    security_evidence_refs: List[str] = field(default_factory=list)
    perf_evidence_refs: List[str] = field(default_factory=list)
    docs_evidence_refs: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    next_steps: List[str] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    steps: List[Step] = field(default_factory=list)
    project_item_id: Optional[str] = None
    project_draft_id: Optional[str] = None
    project_remote_updated: Optional[str] = None
    project_issue_number: Optional[str] = None
    _source_path: Optional[str] = None
    _source_mtime: float = 0.0
    history: List[str] = field(default_factory=list)  # Legacy text history
    events: List[StepEvent] = field(default_factory=list)  # Structured event log
    depends_on: List[str] = field(default_factory=list)  # Task IDs this task depends on
    attachments: List[Attachment] = field(default_factory=list)

    def calculate_progress(self) -> int:
        if getattr(self, "kind", "task") == "plan":
            steps = list(getattr(self, "plan_steps", []) or [])
            if not steps:
                return int(self.progress or 0)
            current = int(getattr(self, "plan_current", 0) or 0)
            current = max(0, min(current, len(steps)))
            return int((current / len(steps)) * 100)

        flat = _flatten_step_tree(list(self.steps or []))
        if not flat:
            return self.progress
        completed = sum(1 for st in flat if st.completed)
        return int((completed / len(flat)) * 100)

    @property
    def folder(self) -> str:
        return self.domain

    @folder.setter
    def folder(self, value: str) -> None:
        self.domain = value

    @property
    def filepath(self) -> Path:
        if self._source_path:
            return Path(self._source_path)
        base = Path(".tasks")
        return (base / self.domain / f"{self.id}.task").resolve() if self.domain else base / f"{self.id}.task"

    def update_status_from_progress(self) -> None:
        if self.status_manual:
            return
        prog = self.calculate_progress()
        self.progress = prog
        if self.blocked:
            self.status = "TODO"
        elif prog == 100:
            self.status = "DONE"
        elif prog > 0:
            self.status = "ACTIVE"
        else:
            self.status = "TODO"

    def to_file_content(self) -> str:
        metadata = {
            "schema_version": int(getattr(self, "schema_version", 6) or 6),
            "revision": int(getattr(self, "revision", 0) or 0),
            "id": self.id,
            "kind": getattr(self, "kind", "task"),
            "title": self.title,
            "status": self.status,
            "domain": self.domain or None,
            "phase": self.phase or None,
            "component": self.component or None,
            "parent": (self.parent or None),
            "priority": self.priority,
            "created": self.created or self._now_iso(),
            "updated": self.updated or self._now_iso(),
            "tags": self.tags,
            "assignee": self.assignee or "ai",
            "progress": self.calculate_progress(),
        }
        if self.blocked:
            metadata["blocked"] = True
        if self.blockers:
            metadata["blockers"] = self.blockers
        if self.success_criteria:
            metadata["success_criteria"] = list(self.success_criteria)
        if self.tests:
            metadata["tests"] = list(self.tests)
        if self.criteria_confirmed:
            metadata["criteria_confirmed"] = True
        if self.tests_confirmed:
            metadata["tests_confirmed"] = True
        if self.criteria_auto_confirmed:
            metadata["criteria_auto_confirmed"] = True
        if self.tests_auto_confirmed:
            metadata["tests_auto_confirmed"] = True
        if self.criteria_notes:
            metadata["criteria_notes"] = list(self.criteria_notes)
        if self.tests_notes:
            metadata["tests_notes"] = list(self.tests_notes)
        if self.security_confirmed:
            metadata["security_confirmed"] = True
        if self.perf_confirmed:
            metadata["perf_confirmed"] = True
        if self.docs_confirmed:
            metadata["docs_confirmed"] = True
        if self.security_notes:
            metadata["security_notes"] = list(self.security_notes)
        if self.perf_notes:
            metadata["perf_notes"] = list(self.perf_notes)
        if self.docs_notes:
            metadata["docs_notes"] = list(self.docs_notes)
        if self.criteria_evidence_refs:
            metadata["criteria_evidence_refs"] = list(self.criteria_evidence_refs)
        if self.tests_evidence_refs:
            metadata["tests_evidence_refs"] = list(self.tests_evidence_refs)
        if self.security_evidence_refs:
            metadata["security_evidence_refs"] = list(self.security_evidence_refs)
        if self.perf_evidence_refs:
            metadata["perf_evidence_refs"] = list(self.perf_evidence_refs)
        if self.docs_evidence_refs:
            metadata["docs_evidence_refs"] = list(self.docs_evidence_refs)
        if self.project_item_id:
            metadata["project_item_id"] = self.project_item_id
        if self.project_draft_id:
            metadata["project_draft_id"] = self.project_draft_id
        if self.project_remote_updated:
            metadata["project_remote_updated"] = self.project_remote_updated
        if self.project_issue_number:
            metadata["project_issue_number"] = self.project_issue_number
        step_project_ids = [st.project_item_id for st in self.steps]
        if any(step_project_ids):
            metadata["step_project_ids"] = step_project_ids
        if self.status_manual:
            metadata["status_manual"] = True
        if self.depends_on:
            metadata["depends_on"] = self.depends_on
        if self.events:
            metadata["events"] = [e.to_dict() for e in self.events]
        if self.attachments:
            metadata["attachments"] = [a.to_dict() for a in list(self.attachments or [])]
        if self.contract_versions:
            metadata["contract_versions"] = self.contract_versions
        if self.contract_data:
            metadata["contract_data"] = dict(self.contract_data)
        if self.plan_steps:
            metadata["plan_steps"] = self.plan_steps
            metadata["plan_current"] = int(getattr(self, "plan_current", 0) or 0)
        if self.steps:
            def dump_plan(plan) -> Dict[str, Any]:
                return {
                    "title": getattr(plan, "title", "") or "",
                    "doc": getattr(plan, "doc", "") or "",
                    "attachments": [a.to_dict() for a in list(getattr(plan, "attachments", []) or [])],
                    "success_criteria": list(getattr(plan, "success_criteria", []) or []),
                    "tests": list(getattr(plan, "tests", []) or []),
                    "blockers": list(getattr(plan, "blockers", []) or []),
                    "criteria_confirmed": bool(getattr(plan, "criteria_confirmed", False)),
                    "tests_confirmed": bool(getattr(plan, "tests_confirmed", False)),
                    "criteria_auto_confirmed": bool(getattr(plan, "criteria_auto_confirmed", False)),
                    "tests_auto_confirmed": bool(getattr(plan, "tests_auto_confirmed", False)),
                    "criteria_notes": list(getattr(plan, "criteria_notes", []) or []),
                    "tests_notes": list(getattr(plan, "tests_notes", []) or []),
                    "security_confirmed": bool(getattr(plan, "security_confirmed", False)),
                    "perf_confirmed": bool(getattr(plan, "perf_confirmed", False)),
                    "docs_confirmed": bool(getattr(plan, "docs_confirmed", False)),
                    "security_notes": list(getattr(plan, "security_notes", []) or []),
                    "perf_notes": list(getattr(plan, "perf_notes", []) or []),
                    "docs_notes": list(getattr(plan, "docs_notes", []) or []),
                    "criteria_evidence_refs": list(getattr(plan, "criteria_evidence_refs", []) or []),
                    "tests_evidence_refs": list(getattr(plan, "tests_evidence_refs", []) or []),
                    "security_evidence_refs": list(getattr(plan, "security_evidence_refs", []) or []),
                    "perf_evidence_refs": list(getattr(plan, "perf_evidence_refs", []) or []),
                    "docs_evidence_refs": list(getattr(plan, "docs_evidence_refs", []) or []),
                    "steps": list(getattr(plan, "steps", []) or []),
                    "current": int(getattr(plan, "current", 0) or 0),
                    "tasks": [dump_task(t) for t in list(getattr(plan, "tasks", []) or [])],
                }

            def plan_has_content(plan) -> bool:
                if plan is None:
                    return False
                if list(getattr(plan, "tasks", []) or []):
                    return True
                if str(getattr(plan, "title", "") or "").strip():
                    return True
                if str(getattr(plan, "doc", "") or "").strip():
                    return True
                if list(getattr(plan, "success_criteria", []) or []):
                    return True
                if list(getattr(plan, "tests", []) or []):
                    return True
                if list(getattr(plan, "blockers", []) or []):
                    return True
                if bool(getattr(plan, "criteria_confirmed", False)) or bool(getattr(plan, "tests_confirmed", False)):
                    return True
                if bool(getattr(plan, "security_confirmed", False)) or bool(getattr(plan, "perf_confirmed", False)) or bool(getattr(plan, "docs_confirmed", False)):
                    return True
                if list(getattr(plan, "criteria_notes", []) or []):
                    return True
                if list(getattr(plan, "tests_notes", []) or []):
                    return True
                if list(getattr(plan, "security_notes", []) or []):
                    return True
                if list(getattr(plan, "perf_notes", []) or []):
                    return True
                if list(getattr(plan, "docs_notes", []) or []):
                    return True
                if list(getattr(plan, "criteria_evidence_refs", []) or []):
                    return True
                if list(getattr(plan, "tests_evidence_refs", []) or []):
                    return True
                if list(getattr(plan, "security_evidence_refs", []) or []):
                    return True
                if list(getattr(plan, "perf_evidence_refs", []) or []):
                    return True
                if list(getattr(plan, "docs_evidence_refs", []) or []):
                    return True
                if list(getattr(plan, "steps", []) or []):
                    return True
                if int(getattr(plan, "current", 0) or 0) != 0:
                    return True
                return False

            def dump_task(task) -> Dict[str, Any]:
                return {
                    "id": getattr(task, "id", "") or "",
                    "title": getattr(task, "title", "") or "",
                    "status": getattr(task, "status", "TODO") or "TODO",
                    "priority": getattr(task, "priority", "MEDIUM") or "MEDIUM",
                    "description": getattr(task, "description", "") or "",
                    "context": getattr(task, "context", "") or "",
                    "attachments": [a.to_dict() for a in list(getattr(task, "attachments", []) or [])],
                    "success_criteria": list(getattr(task, "success_criteria", []) or []),
                    "tests": list(getattr(task, "tests", []) or []),
                    "criteria_confirmed": bool(getattr(task, "criteria_confirmed", False)),
                    "tests_confirmed": bool(getattr(task, "tests_confirmed", False)),
                    "criteria_auto_confirmed": bool(getattr(task, "criteria_auto_confirmed", False)),
                    "tests_auto_confirmed": bool(getattr(task, "tests_auto_confirmed", False)),
                    "criteria_notes": list(getattr(task, "criteria_notes", []) or []),
                    "tests_notes": list(getattr(task, "tests_notes", []) or []),
                    "security_confirmed": bool(getattr(task, "security_confirmed", False)),
                    "perf_confirmed": bool(getattr(task, "perf_confirmed", False)),
                    "docs_confirmed": bool(getattr(task, "docs_confirmed", False)),
                    "security_notes": list(getattr(task, "security_notes", []) or []),
                    "perf_notes": list(getattr(task, "perf_notes", []) or []),
                    "docs_notes": list(getattr(task, "docs_notes", []) or []),
                    "criteria_evidence_refs": list(getattr(task, "criteria_evidence_refs", []) or []),
                    "tests_evidence_refs": list(getattr(task, "tests_evidence_refs", []) or []),
                    "security_evidence_refs": list(getattr(task, "security_evidence_refs", []) or []),
                    "perf_evidence_refs": list(getattr(task, "perf_evidence_refs", []) or []),
                    "docs_evidence_refs": list(getattr(task, "docs_evidence_refs", []) or []),
                    "dependencies": list(getattr(task, "dependencies", []) or []),
                    "next_steps": list(getattr(task, "next_steps", []) or []),
                    "problems": list(getattr(task, "problems", []) or []),
                    "risks": list(getattr(task, "risks", []) or []),
                    "blocked": bool(getattr(task, "blocked", False)),
                    "blockers": list(getattr(task, "blockers", []) or []),
                    "steps": [dump_step(st) for st in list(getattr(task, "steps", []) or [])],
                    "status_manual": bool(getattr(task, "status_manual", False)),
                }

            def dump_step(st: Step) -> Dict[str, Any]:
                data: Dict[str, Any] = {
                    "id": getattr(st, "id", "") or "",
                    "title": st.title,
                    "completed": st.completed,
                    "success_criteria": list(st.success_criteria),
                    "tests": list(st.tests),
                    "blockers": list(st.blockers),
                    "attachments": [a.to_dict() for a in list(getattr(st, "attachments", []) or [])],
                    "verification_checks": [c.to_dict() for c in list(getattr(st, "verification_checks", []) or [])],
                    "verification_outcome": str(getattr(st, "verification_outcome", "") or ""),
                    "criteria_confirmed": st.criteria_confirmed,
                    "tests_confirmed": st.tests_confirmed,
                    "criteria_auto_confirmed": getattr(st, "criteria_auto_confirmed", False),
                    "tests_auto_confirmed": getattr(st, "tests_auto_confirmed", False),
                    "criteria_notes": list(st.criteria_notes),
                    "tests_notes": list(st.tests_notes),
                    "security_confirmed": bool(getattr(st, "security_confirmed", False)),
                    "perf_confirmed": bool(getattr(st, "perf_confirmed", False)),
                    "docs_confirmed": bool(getattr(st, "docs_confirmed", False)),
                    "security_notes": list(getattr(st, "security_notes", []) or []),
                    "perf_notes": list(getattr(st, "perf_notes", []) or []),
                    "docs_notes": list(getattr(st, "docs_notes", []) or []),
                    "criteria_evidence_refs": list(getattr(st, "criteria_evidence_refs", []) or []),
                    "tests_evidence_refs": list(getattr(st, "tests_evidence_refs", []) or []),
                    "security_evidence_refs": list(getattr(st, "security_evidence_refs", []) or []),
                    "perf_evidence_refs": list(getattr(st, "perf_evidence_refs", []) or []),
                    "docs_evidence_refs": list(getattr(st, "docs_evidence_refs", []) or []),
                    "required_checkpoints": list(getattr(st, "required_checkpoints", []) or []),
                    "created_at": getattr(st, "created_at", None),
                    "completed_at": getattr(st, "completed_at", None),
                    "progress_notes": list(getattr(st, "progress_notes", [])),
                    "started_at": getattr(st, "started_at", None),
                    "blocked": getattr(st, "blocked", False),
                    "block_reason": getattr(st, "block_reason", ""),
                }
                plan = getattr(st, "plan", None)
                if plan_has_content(plan):
                    data["plan"] = dump_plan(plan)
                return data

            metadata["steps"] = [dump_step(st) for st in self.steps]

        header = yaml.safe_dump(
            metadata,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        ).strip()
        lines = ["---", header, "---", ""]
        lines.append(f"# {self.title}\n")

        def add_section(title: str, content: List[str]) -> None:
            if content:
                lines.append(f"## {title}")
                lines.extend(content)
                lines.append("")

        if self.contract:
            lines.append("## Контракт")
            lines.append(self.contract)
            lines.append("")
        if self.plan_doc:
            lines.append("## План")
            lines.append(self.plan_doc)
            lines.append("")
        if self.description:
            lines.append("## Описание")
            lines.append(self.description)
            lines.append("")
        if self.context:
            lines.append("## Контекст")
            lines.append(self.context)
            lines.append("")
        if self.steps:
            lines.append("## Шаги")

            def dump_step(st: Step, indent: int = 0):
                pad = "  " * indent
                lines.append(f"{pad}- [{'x' if st.completed else ' '}] {st.title}")
                pad_detail = pad + "  "
                if st.success_criteria:
                    lines.append(f"{pad_detail}- Критерии: " + "; ".join(st.success_criteria))
                if st.tests:
                    lines.append(f"{pad_detail}- Тесты: " + "; ".join(st.tests))
                if st.blockers:
                    lines.append(f"{pad_detail}- Блокеры: " + "; ".join(st.blockers))
                tests_value = "OK" if st.tests_confirmed else ("AUTO" if st.tests_auto_confirmed else "TODO")
                status_tokens = [f"Критерии={'OK' if st.criteria_confirmed else 'TODO'}", f"Тесты={tests_value}"]
                if getattr(st, "security_confirmed", False) or list(getattr(st, "security_notes", []) or []):
                    status_tokens.append(f"Безопасность={'OK' if getattr(st, 'security_confirmed', False) else 'TODO'}")
                if getattr(st, "perf_confirmed", False) or list(getattr(st, "perf_notes", []) or []):
                    status_tokens.append(f"Производительность={'OK' if getattr(st, 'perf_confirmed', False) else 'TODO'}")
                if getattr(st, "docs_confirmed", False) or list(getattr(st, "docs_notes", []) or []):
                    status_tokens.append(f"Документация={'OK' if getattr(st, 'docs_confirmed', False) else 'TODO'}")
                lines.append(f"{pad_detail}- Чекпоинты: " + "; ".join(status_tokens))
                if st.criteria_notes:
                    lines.append(f"{pad_detail}- Отметки критериев: " + "; ".join(st.criteria_notes))
                if st.tests_notes:
                    lines.append(f"{pad_detail}- Отметки тестов: " + "; ".join(st.tests_notes))
                if list(getattr(st, "security_notes", []) or []):
                    lines.append(f"{pad_detail}- Отметки безопасности: " + "; ".join(list(getattr(st, "security_notes", []) or [])))
                if list(getattr(st, "perf_notes", []) or []):
                    lines.append(f"{pad_detail}- Отметки производительности: " + "; ".join(list(getattr(st, "perf_notes", []) or [])))
                if list(getattr(st, "docs_notes", []) or []):
                    lines.append(f"{pad_detail}- Отметки документации: " + "; ".join(list(getattr(st, "docs_notes", []) or [])))
                # Phase 1 fields
                if st.created_at:
                    lines.append(f"{pad_detail}- Создано: {st.created_at}")
                if st.completed_at:
                    lines.append(f"{pad_detail}- Завершено: {st.completed_at}")
                if st.progress_notes:
                    lines.append(f"{pad_detail}- Прогресс: " + "; ".join(st.progress_notes))
                if st.started_at:
                    lines.append(f"{pad_detail}- Начато: {st.started_at}")
                if st.blocked or st.block_reason:
                    block_value = "да" if st.blocked else "нет"
                    if st.block_reason:
                        lines.append(f"{pad_detail}- Заблокировано: {block_value}; {st.block_reason}")
                    else:
                        lines.append(f"{pad_detail}- Заблокировано: {block_value}")

                plan = getattr(st, "plan", None)
                if plan and getattr(plan, "tasks", None):
                    lines.append(f"{pad_detail}- План:")
                    for task in plan.tasks:
                        task_label = f"{task.title}".strip() or "Untitled task"
                        lines.append(f"{pad_detail}  - [TASK] {task_label} ({getattr(task, 'status', 'TODO')})")
                        for child in list(getattr(task, "steps", []) or []):
                            dump_step(child, indent + 2)

            for st in self.steps:
                dump_step(st, 0)
            lines.append("")
        add_section("Текущие проблемы", [f"{i + 1}. {p}" for i, p in enumerate(self.problems)])
        add_section("Следующие шаги", [f"- {s}" for s in self.next_steps])
        add_section("Критерии успеха", [f"- {c}" for c in self.success_criteria])
        add_section("Тесты", [f"- {t}" for t in getattr(self, "tests", []) or []])
        add_section("Блокеры", [f"- {b}" for b in self.blockers])
        checkpoint_lines: List[str] = []
        if self.success_criteria or getattr(self, "tests", None) is not None:
            tests_value = "OK" if self.tests_confirmed else ("AUTO" if self.tests_auto_confirmed else "TODO")
            checkpoint_lines.append(f"- Критерии={'OK' if self.criteria_confirmed else 'TODO'}")
            checkpoint_lines.append(f"- Тесты={tests_value}")
            if self.security_confirmed or self.security_notes:
                checkpoint_lines.append(f"- Безопасность={'OK' if self.security_confirmed else 'TODO'}")
            if self.perf_confirmed or self.perf_notes:
                checkpoint_lines.append(f"- Производительность={'OK' if self.perf_confirmed else 'TODO'}")
            if self.docs_confirmed or self.docs_notes:
                checkpoint_lines.append(f"- Документация={'OK' if self.docs_confirmed else 'TODO'}")
            if self.criteria_notes:
                checkpoint_lines.append("- Отметки критериев: " + "; ".join(self.criteria_notes))
            if self.tests_notes:
                checkpoint_lines.append("- Отметки тестов: " + "; ".join(self.tests_notes))
            if self.security_notes:
                checkpoint_lines.append("- Отметки безопасности: " + "; ".join(self.security_notes))
            if self.perf_notes:
                checkpoint_lines.append("- Отметки производительности: " + "; ".join(self.perf_notes))
            if self.docs_notes:
                checkpoint_lines.append("- Отметки документации: " + "; ".join(self.docs_notes))
        add_section("Чекпоинты", checkpoint_lines)
        add_section("Зависимости", [f"- {d}" for d in self.dependencies])
        add_section("Риски", [f"- {r}" for r in self.risks])
        add_section("История", [f"- {h}" for h in self.history])

        return "\n".join(lines).strip() + "\n"

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()
