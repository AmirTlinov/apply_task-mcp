import re
import time
from pathlib import Path
from typing import List, Optional

import yaml

from core import SubTask, TaskDetail
from core.task_event import TaskEvent
from core.status import task_status_code


class TaskFileParser:
    SUBTASK_PATTERN = re.compile(r"^-\s*\[(x|X| )\]\s*(.+)$")

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

        task = TaskDetail(
            id=metadata.get("id", ""),
            title=metadata.get("title", ""),
            status=cls._parse_status(metadata.get("status", "FAIL")),
            status_manual=bool(metadata.get("status_manual", False)),
            domain=metadata.get("domain", "") or "",
            phase=metadata.get("phase", "") or "",
            component=metadata.get("component", "") or "",
            parent=metadata.get("parent"),
            priority=metadata.get("priority", "MEDIUM"),
            created=metadata.get("created", ""),
            updated=metadata.get("updated", ""),
            tags=metadata.get("tags", []),
            assignee=metadata.get("assignee", "ai"),
            progress=metadata.get("progress", 0),
            blocked=metadata.get("blocked", False),
            blockers=metadata.get("blockers", []),
            project_item_id=metadata.get("project_item_id"),
            project_draft_id=metadata.get("project_draft_id"),
            project_remote_updated=metadata.get("project_remote_updated"),
            project_issue_number=metadata.get("project_issue_number"),
            depends_on=metadata.get("depends_on", []),
            events=[TaskEvent.from_dict(e) for e in metadata.get("events", [])],
        )
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
        subtask_projects = metadata.get("subtask_project_ids", []) or []
        for idx, sub_id in enumerate(subtask_projects):
            if sub_id and idx < len(task.subtasks):
                task.subtasks[idx].project_item_id = sub_id
        try:
            if not task.status_manual and task.subtasks and task.calculate_progress() == 100 and not task.blocked:
                task.status = "OK"
        except Exception:
            pass
        return task

    @staticmethod
    def _parse_status(raw: str) -> str:
        try:
            return task_status_code(raw or "FAIL")
        except ValueError:
            return "FAIL"

    @classmethod
    def _save_section(cls, task: TaskDetail, section: str, lines: List[str]) -> None:
        content = "\n".join(lines).strip()
        if section == "Описание":
            task.description = content
        elif section == "Контекст":
            task.context = content
        elif section == "Подзадачи":
            stack: list[tuple[int, SubTask]] = []
            for raw_line in lines:
                if not raw_line.strip():
                    continue
                indent = len(raw_line) - len(raw_line.lstrip(" "))
                line = raw_line.strip()
                match = cls.SUBTASK_PATTERN.match(line)
                if match:
                    st = SubTask(match.group(1).lower() == "x", match.group(2))
                    while stack and stack[-1][0] >= indent:
                        stack.pop()
                    if stack:
                        stack[-1][1].children.append(st)
                    else:
                        task.subtasks.append(st)
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
                elif stripped.startswith("Блокеры:"):
                    current.blockers = [b.strip() for b in stripped[len("Блокеры:") :].split(";") if b.strip()]
                elif stripped.startswith("Чекпоинты:"):
                    tokens = stripped[len("Чекпоинты:") :].split(";")
                    for token in tokens:
                        token = token.strip()
                        if token.startswith("Критерии="):
                            current.criteria_confirmed = token.split("=")[1].strip().upper() == "OK"
                        elif token.startswith("Тесты="):
                            current.tests_confirmed = token.split("=")[1].strip().upper() == "OK"
                        elif token.startswith("Блокеры="):
                            current.blockers_resolved = token.split("=")[1].strip().upper() == "OK"
                elif stripped.startswith("Отметки критериев:"):
                    current.criteria_notes = [n.strip() for n in stripped.split(":", 1)[1].split(";") if n.strip()]
                elif stripped.startswith("Отметки тестов:"):
                    current.tests_notes = [n.strip() for n in stripped.split(":", 1)[1].split(";") if n.strip()]
                elif stripped.startswith("Отметки блокеров:"):
                    current.blockers_notes = [n.strip() for n in stripped.split(":", 1)[1].split(";") if n.strip()]
                elif stripped.startswith("Создано:"):
                    current.created_at = stripped.split(":", 1)[1].strip()
                elif stripped.startswith("Завершено:"):
                    current.completed_at = stripped.split(":", 1)[1].strip()
                elif stripped.startswith("Прогресс:"):
                    current.progress_notes = [n.strip() for n in stripped.split(":", 1)[1].split(";") if n.strip()]
                elif stripped.startswith("Начато:"):
                    current.started_at = stripped.split(":", 1)[1].strip()
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
                    else:
                        # Backward compatibility: treat as blocked with reason
                        current.blocked = True
                        current.block_reason = value
        elif section == "Критерии успеха":
            task.success_criteria = cls._parse_list(lines)
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
