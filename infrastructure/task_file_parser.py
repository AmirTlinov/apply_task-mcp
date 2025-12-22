import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from datetime import date, datetime

from core import PlanNode, Step, TaskDetail, TaskNode, StepEvent, ensure_tree_ids, Attachment, VerificationCheck
from core.status import normalize_status_code


class TaskFileParser:
    STEP_PATTERN = re.compile(r"^-\s*\[(x|X| )\]\s*(.+)$")
    CURRENT_SCHEMA_VERSION = 9

    @staticmethod
    def _coerce_timestamp(value: Any) -> str:
        """Normalize YAML timestamps to a JSON-safe string.

        YAML loaders may parse ISO-8601 values into datetime/date objects; keep
        the in-memory model stable by storing timestamps as strings.
        """
        if value is None:
            return ""
        if isinstance(value, (datetime, date)):
            try:
                return value.isoformat()
            except Exception:
                return str(value)
        return str(value)

    @staticmethod
    def _coerce_timestamp_opt(value: Any) -> Optional[str]:
        """Normalize YAML timestamps to Optional ISO string.

        Step-level timestamps should preserve absence explicitly as None rather
        than using an empty-string sentinel.
        """
        raw = TaskFileParser._coerce_timestamp(value)
        raw = (raw or "").strip()
        return raw if raw else None

    @classmethod
    def parse(cls, filepath: Path) -> Optional[TaskDetail]:
        if not filepath.exists():
            return None
        content = filepath.read_text(encoding="utf-8")
        parts = content.split("---", 2)
        if len(parts) < 3:
            return None
        metadata = yaml.safe_load(parts[1]) or {}
        body = parts[2].strip()

        try:
            loaded_schema_version = int(metadata.get("schema_version", 1) or 1)
        except Exception:
            loaded_schema_version = 1

        progress = int(metadata.get("progress", 0) or 0)
        blocked = bool(metadata.get("blocked", False))
        try:
            revision = int(metadata.get("revision", 0) or 0)
        except Exception:
            revision = 0
        revision = max(0, revision)
        raw_id = str(metadata.get("id", "") or "")
        raw_kind = str(metadata.get("kind", "") or "").strip().lower()
        kind = raw_kind if raw_kind in {"plan", "task"} else ("plan" if raw_id.startswith("PLAN-") else "task")
        task = TaskDetail(
            # Always load into the current in-memory schema; old files auto-migrate on save.
            schema_version=cls.CURRENT_SCHEMA_VERSION,
            revision=revision,
            id=raw_id,
            kind=kind,
            title=metadata.get("title", ""),
            status=cls._parse_status(metadata.get("status", "TODO"), progress=progress, blocked=blocked),
            status_manual=bool(metadata.get("status_manual", False)),
            domain=metadata.get("domain", "") or "",
            phase=metadata.get("phase", "") or "",
            component=metadata.get("component", "") or "",
            parent=(metadata.get("parent", None) or metadata.get("plan_id", None) or None),
            priority=metadata.get("priority", "MEDIUM"),
            created=cls._coerce_timestamp(metadata.get("created", "")),
            updated=cls._coerce_timestamp(metadata.get("updated", "")),
            tags=metadata.get("tags", []),
            assignee=metadata.get("assignee", "ai"),
            progress=progress,
            blocked=blocked,
            blockers=metadata.get("blockers", []),
            contract_versions=list(metadata.get("contract_versions", []) or []),
            contract_data=(dict(metadata.get("contract_data", {}) or {}) if isinstance(metadata.get("contract_data", {}), dict) else {}),
            plan_steps=list(metadata.get("plan_steps", []) or []),
            plan_current=int(metadata.get("plan_current", 0) or 0),
            project_item_id=metadata.get("project_item_id"),
            project_draft_id=metadata.get("project_draft_id"),
            project_remote_updated=metadata.get("project_remote_updated"),
            project_issue_number=metadata.get("project_issue_number"),
            depends_on=metadata.get("depends_on", []),
            events=[StepEvent.from_dict(e) for e in metadata.get("events", [])],
        )
        attachments = metadata.get("attachments", []) or []
        if isinstance(attachments, list):
            try:
                task.attachments = [Attachment.from_dict(a) for a in attachments if isinstance(a, dict)]
            except Exception:
                task.attachments = []
        metadata_steps = metadata.get("steps", None)
        if isinstance(metadata_steps, list):
            try:
                task.steps = cls._parse_step_tree(metadata_steps)
                setattr(task, "_steps_from_metadata", True)
            except Exception:
                task.steps = []
                setattr(task, "_steps_from_metadata", False)
        else:
            setattr(task, "_steps_from_metadata", False)
        # Keep the original on-disk schema version for debugging/telemetry (optional).
        setattr(task, "_loaded_schema_version", loaded_schema_version)
        source_path = filepath.resolve()
        task._source_path = source_path
        try:
            task._source_mtime = source_path.stat().st_mtime
        except OSError:
            task._source_mtime = time.time()

        section = None
        buffer: List[str] = []

        def flush():
            if section is None:
                return
            cls._save_section(task, section, buffer.copy())

        for line in body.splitlines():
            if line.startswith("## "):
                flush()
                section = line[3:].strip()
                buffer = []
            else:
                buffer.append(line)
        flush()
        # Metadata fields are the canonical source for checkpoints; body sections are best-effort.
        if "success_criteria" in metadata:
            task.success_criteria = [str(v).strip() for v in (metadata.get("success_criteria", []) or []) if str(v).strip()]
        if "tests" in metadata:
            task.tests = [str(v).strip() for v in (metadata.get("tests", []) or []) if str(v).strip()]
        if "criteria_confirmed" in metadata:
            task.criteria_confirmed = bool(metadata.get("criteria_confirmed", False))
        if "tests_confirmed" in metadata:
            task.tests_confirmed = bool(metadata.get("tests_confirmed", False))
        if "criteria_auto_confirmed" in metadata:
            task.criteria_auto_confirmed = bool(metadata.get("criteria_auto_confirmed", False))
        if "tests_auto_confirmed" in metadata:
            task.tests_auto_confirmed = bool(metadata.get("tests_auto_confirmed", False))
        if "criteria_notes" in metadata:
            task.criteria_notes = [str(v).strip() for v in (metadata.get("criteria_notes", []) or []) if str(v).strip()]
        if "tests_notes" in metadata:
            task.tests_notes = [str(v).strip() for v in (metadata.get("tests_notes", []) or []) if str(v).strip()]
        if "security_confirmed" in metadata:
            task.security_confirmed = bool(metadata.get("security_confirmed", False))
        if "perf_confirmed" in metadata:
            task.perf_confirmed = bool(metadata.get("perf_confirmed", False))
        if "docs_confirmed" in metadata:
            task.docs_confirmed = bool(metadata.get("docs_confirmed", False))
        if "security_notes" in metadata:
            task.security_notes = [str(v).strip() for v in (metadata.get("security_notes", []) or []) if str(v).strip()]
        if "perf_notes" in metadata:
            task.perf_notes = [str(v).strip() for v in (metadata.get("perf_notes", []) or []) if str(v).strip()]
        if "docs_notes" in metadata:
            task.docs_notes = [str(v).strip() for v in (metadata.get("docs_notes", []) or []) if str(v).strip()]
        for key in (
            "criteria_evidence_refs",
            "tests_evidence_refs",
            "security_evidence_refs",
            "perf_evidence_refs",
            "docs_evidence_refs",
        ):
            if key in metadata:
                values = metadata.get(key, []) or []
                if isinstance(values, list):
                    setattr(task, key, [str(v).strip() for v in values if str(v).strip()])
        if not getattr(task, "tests", []) and not getattr(task, "tests_confirmed", False):
            task.tests_auto_confirmed = True
        step_project_ids = metadata.get("step_project_ids", []) or []
        for idx, project_id in enumerate(step_project_ids):
            if project_id and idx < len(task.steps):
                task.steps[idx].project_item_id = project_id
        try:
            if not task.status_manual and task.steps and task.calculate_progress() == 100 and not task.blocked:
                task.status = "DONE"
        except Exception:
            pass
        if task.steps:
            changed = ensure_tree_ids(task.steps)
            if changed:
                setattr(task, "_ids_migrated", True)
        return task

    @staticmethod
    def _parse_status(raw: str, *, progress: int, blocked: bool) -> str:
        try:
            return normalize_status_code(raw or "TODO")
        except ValueError:
            if blocked:
                return "TODO"
            if progress >= 100:
                return "DONE"
            if progress > 0:
                return "ACTIVE"
            return "TODO"

    @classmethod
    def _save_section(cls, task: TaskDetail, section: str, lines: List[str]) -> None:
        content = "\n".join(lines).strip()
        if section in {"Контракт", "Contract"}:
            task.contract = content
        elif section in {"План", "Plan"}:
            task.plan_doc = content
        elif section in {"Описание", "Description"}:
            task.description = content
        elif section in {"Контекст", "Context"}:
            task.context = content
        elif section in {"Шаги", "Steps"}:
            if getattr(task, "_steps_from_metadata", False):
                return
            stack: list[tuple[int, Step]] = []

            def _ensure_default_task(parent: Step) -> TaskNode:
                plan = getattr(parent, "plan", None)
                if not plan:
                    plan = PlanNode(tasks=[TaskNode(title=parent.title or "Work")])
                    parent.plan = plan
                if not getattr(plan, "tasks", None):
                    plan.tasks = [TaskNode(title=parent.title or "Work")]
                return plan.tasks[0]

            for raw_line in lines:
                if not raw_line.strip():
                    continue
                indent = len(raw_line) - len(raw_line.lstrip(" "))
                line = raw_line.strip()
                match = cls.STEP_PATTERN.match(line)
                if match:
                    st = Step(match.group(1).lower() == "x", match.group(2))
                    while stack and stack[-1][0] >= indent:
                        stack.pop()
                    if stack:
                        parent_step = stack[-1][1]
                        _ensure_default_task(parent_step).steps.append(st)
                    else:
                        task.steps.append(st)
                    stack.append((indent, st))
                    continue
                if not stack:
                    continue
                current = stack[-1][1]
                if not line.startswith("- "):
                    continue
                stripped = line[2:]
                if stripped.startswith("Критерии:"):
                    current.success_criteria = [c.strip() for c in stripped[len("Критерии:") :].split(";") if c.strip()]
                elif stripped.startswith("Тесты:"):
                    current.tests = [t.strip() for t in stripped[len("Тесты:") :].split(";") if t.strip()]
                    current.tests_auto_confirmed = not current.tests
                elif stripped.startswith("Блокеры:"):
                    current.blockers = [b.strip() for b in stripped[len("Блокеры:") :].split(";") if b.strip()]
                elif stripped.startswith("Чекпоинты:"):
                    tokens = stripped[len("Чекпоинты:") :].split(";")
                    for token in tokens:
                        token = token.strip()
                        if token.startswith("Критерии="):
                            current.criteria_confirmed = token.split("=")[1].strip().upper() == "OK"
                        elif token.startswith("Тесты="):
                            value = token.split("=")[1].strip().upper()
                            current.tests_confirmed = value == "OK"
                            current.tests_auto_confirmed = value == "AUTO"
                        elif token.startswith("Безопасность=") or token.lower().startswith("security="):
                            value = token.split("=", 1)[1].strip().upper()
                            current.security_confirmed = value == "OK"
                        elif token.startswith("Производительность=") or token.lower().startswith("perf="):
                            value = token.split("=", 1)[1].strip().upper()
                            current.perf_confirmed = value == "OK"
                        elif token.startswith("Документация=") or token.lower().startswith("docs="):
                            value = token.split("=", 1)[1].strip().upper()
                            current.docs_confirmed = value == "OK"
                elif stripped.startswith("Отметки критериев:"):
                    current.criteria_notes = [n.strip() for n in stripped.split(":", 1)[1].split(";") if n.strip()]
                elif stripped.startswith("Отметки тестов:"):
                    current.tests_notes = [n.strip() for n in stripped.split(":", 1)[1].split(";") if n.strip()]
                elif stripped.startswith("Отметки безопасности:"):
                    current.security_notes = [n.strip() for n in stripped.split(":", 1)[1].split(";") if n.strip()]
                elif stripped.startswith("Отметки производительности:"):
                    current.perf_notes = [n.strip() for n in stripped.split(":", 1)[1].split(";") if n.strip()]
                elif stripped.startswith("Отметки документации:"):
                    current.docs_notes = [n.strip() for n in stripped.split(":", 1)[1].split(";") if n.strip()]
                elif stripped.startswith("Создано:"):
                    value = stripped.split(":", 1)[1].strip()
                    current.created_at = value or None
                elif stripped.startswith("Завершено:"):
                    value = stripped.split(":", 1)[1].strip()
                    current.completed_at = value or None
                elif stripped.startswith("Прогресс:"):
                    current.progress_notes = [n.strip() for n in stripped.split(":", 1)[1].split(";") if n.strip()]
                elif stripped.startswith("Начато:"):
                    value = stripped.split(":", 1)[1].strip()
                    current.started_at = value or None
                elif stripped.startswith("Заблокировано:"):
                    value = stripped.split(":", 1)[1].strip()
                    parts = value.split(";", 1)
                    first_part = parts[0].strip().lower()
                    # Check if first part is explicit yes/no
                    if first_part in ("да", "yes", "true", "1"):
                        current.blocked = True
                        current.block_reason = parts[1].strip() if len(parts) > 1 else ""
                    elif first_part in ("нет", "no", "false", "0"):
                        current.blocked = False
                        current.block_reason = ""
            # Normal mode: tests are optional; when empty, treat as auto-confirmed.
            def _apply_auto_tests(nodes: List[Step]) -> None:
                for node in nodes:
                    if not node.tests and not node.tests_confirmed:
                        node.tests_auto_confirmed = True
                    plan = getattr(node, "plan", None)
                    if plan and getattr(plan, "tasks", None):
                        for task in plan.tasks:
                            _apply_auto_tests(list(getattr(task, "steps", []) or []))

            _apply_auto_tests(task.steps)
        elif section == "Критерии успеха":
            task.success_criteria = cls._parse_list(lines)
        elif section in {"Тесты", "Tests"}:
            task.tests = cls._parse_list(lines)
            if not task.tests and not task.tests_confirmed:
                task.tests_auto_confirmed = True
        elif section in {"Блокеры", "Blockers"}:
            task.blockers = cls._parse_list(lines)
        elif section in {"Чекпоинты", "Checkpoints"}:
            for raw_line in lines:
                line = raw_line.strip()
                if line.startswith("- "):
                    line = line[2:].strip()
                if not line:
                    continue
                if line.startswith("Критерии=") or line.lower().startswith("criteria="):
                    value = line.split("=", 1)[1].strip().upper()
                    task.criteria_confirmed = value == "OK"
                elif line.startswith("Тесты=") or line.lower().startswith("tests="):
                    value = line.split("=", 1)[1].strip().upper()
                    task.tests_confirmed = value == "OK"
                    task.tests_auto_confirmed = value == "AUTO"
                elif line.startswith("Безопасность=") or line.lower().startswith("security="):
                    value = line.split("=", 1)[1].strip().upper()
                    task.security_confirmed = value == "OK"
                elif line.startswith("Производительность=") or line.lower().startswith("perf="):
                    value = line.split("=", 1)[1].strip().upper()
                    task.perf_confirmed = value == "OK"
                elif line.startswith("Документация=") or line.lower().startswith("docs="):
                    value = line.split("=", 1)[1].strip().upper()
                    task.docs_confirmed = value == "OK"
                elif line.startswith("Отметки критериев:") or line.lower().startswith("criteria notes:"):
                    rhs = line.split(":", 1)[1]
                    task.criteria_notes = [n.strip() for n in rhs.split(";") if n.strip()]
                elif line.startswith("Отметки тестов:") or line.lower().startswith("tests notes:"):
                    rhs = line.split(":", 1)[1]
                    task.tests_notes = [n.strip() for n in rhs.split(";") if n.strip()]
                elif line.startswith("Отметки безопасности:"):
                    rhs = line.split(":", 1)[1]
                    task.security_notes = [n.strip() for n in rhs.split(";") if n.strip()]
                elif line.startswith("Отметки производительности:"):
                    rhs = line.split(":", 1)[1]
                    task.perf_notes = [n.strip() for n in rhs.split(";") if n.strip()]
                elif line.startswith("Отметки документации:"):
                    rhs = line.split(":", 1)[1]
                    task.docs_notes = [n.strip() for n in rhs.split(";") if n.strip()]
        elif section == "Следующие шаги":
            task.next_steps = cls._parse_list(lines)
        elif section == "Зависимости":
            task.dependencies = cls._parse_list(lines)
        elif section == "Текущие проблемы":
            task.problems = cls._parse_numbered(lines)
        elif section == "Риски":
            task.risks = cls._parse_list(lines)
        elif section == "История":
            task.history = cls._parse_list(lines)

    @classmethod
    def _parse_step_tree(cls, nodes: List[Dict[str, Any]]) -> List[Step]:
        return [cls._parse_step_node(node) for node in nodes if isinstance(node, dict)]

    @classmethod
    def _parse_step_node(cls, node: Dict[str, Any]) -> Step:
        title = str(node.get("title", "") or "").strip()
        st = Step(
            completed=bool(node.get("completed", False)),
            title=title or "Untitled step",
            success_criteria=list(node.get("success_criteria", []) or []),
            tests=list(node.get("tests", []) or []),
            blockers=list(node.get("blockers", []) or []),
            id=str(node.get("id", "") or "").strip() or "",
        )
        st.criteria_confirmed = bool(node.get("criteria_confirmed", st.criteria_confirmed))
        st.tests_confirmed = bool(node.get("tests_confirmed", st.tests_confirmed))
        st.criteria_auto_confirmed = bool(node.get("criteria_auto_confirmed", st.criteria_auto_confirmed))
        st.tests_auto_confirmed = bool(node.get("tests_auto_confirmed", st.tests_auto_confirmed))
        if not st.tests and not st.tests_confirmed:
            st.tests_auto_confirmed = True
        st.criteria_notes = list(node.get("criteria_notes", []) or [])
        st.tests_notes = list(node.get("tests_notes", []) or [])
        st.security_confirmed = bool(node.get("security_confirmed", getattr(st, "security_confirmed", False)))
        st.perf_confirmed = bool(node.get("perf_confirmed", getattr(st, "perf_confirmed", False)))
        st.docs_confirmed = bool(node.get("docs_confirmed", getattr(st, "docs_confirmed", False)))
        st.security_notes = list(node.get("security_notes", []) or [])
        st.perf_notes = list(node.get("perf_notes", []) or [])
        st.docs_notes = list(node.get("docs_notes", []) or [])
        for key in (
            "criteria_evidence_refs",
            "tests_evidence_refs",
            "security_evidence_refs",
            "perf_evidence_refs",
            "docs_evidence_refs",
        ):
            values = node.get(key, []) or []
            if isinstance(values, list):
                setattr(st, key, [str(v).strip() for v in values if str(v).strip()])
        req = node.get("required_checkpoints", []) or []
        if isinstance(req, list):
            st.required_checkpoints = [str(v).strip() for v in req if str(v).strip()]
        st.created_at = cls._coerce_timestamp_opt(node.get("created_at", None))
        st.completed_at = cls._coerce_timestamp_opt(node.get("completed_at", None))
        st.progress_notes = list(node.get("progress_notes", []) or [])
        st.started_at = cls._coerce_timestamp_opt(node.get("started_at", None))
        st.blocked = bool(node.get("blocked", False))
        st.block_reason = str(node.get("block_reason", "") or "").strip()
        st.verification_outcome = str(node.get("verification_outcome", "") or "").strip()
        checks_raw = node.get("verification_checks", []) or []
        if isinstance(checks_raw, list):
            try:
                st.verification_checks = [VerificationCheck.from_dict(c) for c in checks_raw if isinstance(c, dict)]
            except Exception:
                st.verification_checks = []
        attachments_raw = node.get("attachments", []) or []
        if isinstance(attachments_raw, list):
            try:
                st.attachments = [Attachment.from_dict(a) for a in attachments_raw if isinstance(a, dict)]
            except Exception:
                st.attachments = []

        plan_raw = node.get("plan", None)
        if isinstance(plan_raw, dict):
            st.plan = cls._parse_plan_node(plan_raw)
        else:
            legacy_children = node.get("steps", None)
            if isinstance(legacy_children, list) and legacy_children:
                legacy_steps = [cls._parse_step_node(ch) for ch in legacy_children if isinstance(ch, dict)]
                if legacy_steps:
                    st.plan = PlanNode(tasks=[TaskNode(title=st.title or "Work", steps=legacy_steps)])
        return st

    @classmethod
    def _parse_plan_node(cls, node: Dict[str, Any]) -> PlanNode:
        tasks_raw = node.get("tasks", [])
        tasks = [cls._parse_task_node(t) for t in tasks_raw if isinstance(t, dict)]
        attachments_raw = node.get("attachments", []) or []
        attachments: List[Attachment] = []
        if isinstance(attachments_raw, list):
            try:
                attachments = [Attachment.from_dict(a) for a in attachments_raw if isinstance(a, dict)]
            except Exception:
                attachments = []
        plan = PlanNode(
            title=str(node.get("title", "") or ""),
            doc=str(node.get("doc", "") or ""),
            attachments=attachments,
            success_criteria=list(node.get("success_criteria", []) or []),
            tests=list(node.get("tests", []) or []),
            blockers=list(node.get("blockers", []) or []),
            criteria_confirmed=bool(node.get("criteria_confirmed", False)),
            tests_confirmed=bool(node.get("tests_confirmed", False)),
            criteria_auto_confirmed=bool(node.get("criteria_auto_confirmed", False)),
            tests_auto_confirmed=bool(node.get("tests_auto_confirmed", False)),
            criteria_notes=list(node.get("criteria_notes", []) or []),
            tests_notes=list(node.get("tests_notes", []) or []),
            security_confirmed=bool(node.get("security_confirmed", False)),
            perf_confirmed=bool(node.get("perf_confirmed", False)),
            docs_confirmed=bool(node.get("docs_confirmed", False)),
            security_notes=list(node.get("security_notes", []) or []),
            perf_notes=list(node.get("perf_notes", []) or []),
            docs_notes=list(node.get("docs_notes", []) or []),
            criteria_evidence_refs=list(node.get("criteria_evidence_refs", []) or []),
            tests_evidence_refs=list(node.get("tests_evidence_refs", []) or []),
            security_evidence_refs=list(node.get("security_evidence_refs", []) or []),
            perf_evidence_refs=list(node.get("perf_evidence_refs", []) or []),
            docs_evidence_refs=list(node.get("docs_evidence_refs", []) or []),
            steps=list(node.get("steps", []) or []),
            current=int(node.get("current", 0) or 0),
            tasks=tasks,
        )
        if not getattr(plan, "tests", []) and not getattr(plan, "tests_confirmed", False):
            plan.tests_auto_confirmed = True
        return plan

    @classmethod
    def _parse_task_node(cls, node: Dict[str, Any]) -> TaskNode:
        title = str(node.get("title", "") or "").strip() or "Untitled task"
        status = cls._parse_status(str(node.get("status", "TODO")), progress=0, blocked=bool(node.get("blocked", False)))
        attachments_raw = node.get("attachments", []) or []
        attachments: List[Attachment] = []
        if isinstance(attachments_raw, list):
            try:
                attachments = [Attachment.from_dict(a) for a in attachments_raw if isinstance(a, dict)]
            except Exception:
                attachments = []
        task = TaskNode(
            title=title,
            status=status,
            priority=str(node.get("priority", "MEDIUM") or "MEDIUM"),
            description=str(node.get("description", "") or ""),
            context=str(node.get("context", "") or ""),
            attachments=attachments,
            success_criteria=list(node.get("success_criteria", []) or []),
            tests=list(node.get("tests", []) or []),
            criteria_confirmed=bool(node.get("criteria_confirmed", False)),
            tests_confirmed=bool(node.get("tests_confirmed", False)),
            criteria_auto_confirmed=bool(node.get("criteria_auto_confirmed", False)),
            tests_auto_confirmed=bool(node.get("tests_auto_confirmed", False)),
            criteria_notes=list(node.get("criteria_notes", []) or []),
            tests_notes=list(node.get("tests_notes", []) or []),
            security_confirmed=bool(node.get("security_confirmed", False)),
            perf_confirmed=bool(node.get("perf_confirmed", False)),
            docs_confirmed=bool(node.get("docs_confirmed", False)),
            security_notes=list(node.get("security_notes", []) or []),
            perf_notes=list(node.get("perf_notes", []) or []),
            docs_notes=list(node.get("docs_notes", []) or []),
            criteria_evidence_refs=list(node.get("criteria_evidence_refs", []) or []),
            tests_evidence_refs=list(node.get("tests_evidence_refs", []) or []),
            security_evidence_refs=list(node.get("security_evidence_refs", []) or []),
            perf_evidence_refs=list(node.get("perf_evidence_refs", []) or []),
            docs_evidence_refs=list(node.get("docs_evidence_refs", []) or []),
            dependencies=list(node.get("dependencies", []) or []),
            next_steps=list(node.get("next_steps", []) or []),
            problems=list(node.get("problems", []) or []),
            risks=list(node.get("risks", []) or []),
            blocked=bool(node.get("blocked", False)),
            blockers=list(node.get("blockers", []) or []),
            status_manual=bool(node.get("status_manual", False)),
            id=str(node.get("id", "") or "").strip() or "",
        )
        if not getattr(task, "tests", []) and not getattr(task, "tests_confirmed", False):
            task.tests_auto_confirmed = True
        steps_raw = node.get("steps", []) or []
        task.steps = [cls._parse_step_node(st) for st in steps_raw if isinstance(st, dict)]
        return task

    @staticmethod
    def _parse_list(lines: List[str]) -> List[str]:
        out = []
        for line in lines:
            line = line.strip()
            if line.startswith("- "):
                out.append(line[2:])
        return out

    @staticmethod
    def _parse_numbered(lines: List[str]) -> List[str]:
        out = []
        for line in lines:
            line = line.strip()
            m = re.match(r"^\d+\.\s+(.*)", line)
            if m:
                out.append(m.group(1))
        return out


__all__ = ["TaskFileParser"]
