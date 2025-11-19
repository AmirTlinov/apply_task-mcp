#!/usr/bin/env python3
"""
tasks.py — flagship-уровень менеджер задач.

Все задачи хранятся только в каталоге .tasks (по одной задаче в файле .task).
Файл todo.machine.md больше не требуется и нигде не используется.
"""

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.input.ansi_escape_sequences import REVERSE_ANSI_SEQUENCES
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import HSplit, Layout, Window, VSplit
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.controls import FormattedTextControl, BufferControl
from prompt_toolkit.mouse_events import MouseEventType, MouseEvent
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import TextArea


# ============================================================================
# DATA MODELS
# ============================================================================


@dataclass
class SubTask:
    completed: bool
    title: str
    success_criteria: List[str] = field(default_factory=list)
    tests: List[str] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)
    criteria_confirmed: bool = False
    tests_confirmed: bool = False
    blockers_resolved: bool = False
    criteria_notes: List[str] = field(default_factory=list)
    tests_notes: List[str] = field(default_factory=list)
    blockers_notes: List[str] = field(default_factory=list)

    def ready_for_completion(self) -> bool:
        return self.criteria_confirmed and self.tests_confirmed and self.blockers_resolved

    def to_markdown(self) -> str:
        """Сериализация подзадачи в markdown с критериями, тестами, блокерами и чекпоинтами"""
        lines = [f"- [{'x' if self.completed else ' '}] {self.title}"]
        if self.success_criteria:
            lines.append("  - Критерии: " + "; ".join(self.success_criteria))
        if self.tests:
            lines.append("  - Тесты: " + "; ".join(self.tests))
        if self.blockers:
            lines.append("  - Блокеры: " + "; ".join(self.blockers))
        status_tokens = [
            f"Критерии={'OK' if self.criteria_confirmed else 'TODO'}",
            f"Тесты={'OK' if self.tests_confirmed else 'TODO'}",
            f"Блокеры={'OK' if self.blockers_resolved else 'TODO'}",
        ]
        lines.append("  - Чекпоинты: " + "; ".join(status_tokens))
        if self.criteria_notes:
            lines.append("  - Отметки критериев: " + "; ".join(self.criteria_notes))
        if self.tests_notes:
            lines.append("  - Отметки тестов: " + "; ".join(self.tests_notes))
        if self.blockers_notes:
            lines.append("  - Отметки блокеров: " + "; ".join(self.blockers_notes))
        return "\n".join(lines)

    def is_valid_flagship(self) -> Tuple[bool, List[str]]:
        """Проверка flagship-качества подзадачи"""
        issues = []
        if not self.success_criteria:
            issues.append(f"'{self.title}': нет критериев выполнения")
        if not self.tests:
            issues.append(f"'{self.title}': нет тестов для проверки")
        if not self.blockers:
            issues.append(f"'{self.title}': нет блокеров/зависимостей")
        if len(self.title) < 20:
            issues.append(f"'{self.title}': слишком короткое описание (минимум 20 символов)")
        # Проверка атомарности: задача не должна содержать слов типа "и", "затем", "потом"
        atomic_violators = ["и затем", "потом", "после этого", "далее", ", и ", " and then", " then "]
        if any(v in self.title.lower() for v in atomic_violators):
            issues.append(f"'{self.title}': не атомарна (разбей на несколько подзадач)")
        return len(issues) == 0, issues


@dataclass
class TaskDetail:
    id: str
    title: str
    status: str
    domain: str = ""
    phase: str = ""
    component: str = ""
    parent: Optional[str] = None
    priority: str = "MEDIUM"
    created: str = ""
    updated: str = ""
    tags: List[str] = field(default_factory=list)
    assignee: str = "ai"
    progress: int = 0
    blocked: bool = False
    blockers: List[str] = field(default_factory=list)
    description: str = ""
    context: str = ""
    subtasks: List[SubTask] = field(default_factory=list)
    success_criteria: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    next_steps: List[str] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    history: List[str] = field(default_factory=list)

    @property
    def filepath(self) -> Path:
        base = Path(".tasks")
        return (base / self.domain / f"{self.id}.task").resolve() if self.domain else base / f"{self.id}.task"

    def calculate_progress(self) -> int:
        if not self.subtasks:
            return self.progress
        completed = sum(1 for st in self.subtasks if st.completed)
        return int((completed / len(self.subtasks)) * 100)

    def update_status_from_progress(self) -> None:
        prog = self.calculate_progress()
        if self.blocked:
            self.status = "FAIL"
        elif prog == 100:
            self.status = "OK"
        elif prog > 0:
            self.status = "WARN"
        else:
            self.status = "FAIL"

    def to_file_content(self) -> str:
        metadata = {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "domain": self.domain or None,
            "phase": self.phase or None,
            "component": self.component or None,
            "parent": self.parent,
            "priority": self.priority,
            "created": self.created or datetime.now().strftime("%Y-%m-%d"),
            "updated": datetime.now().strftime("%Y-%m-%d"),
            "tags": self.tags,
            "assignee": self.assignee,
            "progress": self.calculate_progress(),
        }
        if self.blocked:
            metadata["blocked"] = True
            metadata["blockers"] = self.blockers

        lines = ["---", yaml.dump(metadata, allow_unicode=True, default_flow_style=False).strip(), "---", ""]
        lines.append(f"# {self.title}\n")

        def add_section(title: str, content: List[str]):
            if content:
                lines.append(f"## {title}")
                lines.extend(content)
                lines.append("")

        if self.description:
            lines.append("## Описание")
            lines.append(self.description)
            lines.append("")
        if self.context:
            lines.append("## Контекст")
            lines.append(self.context)
            lines.append("")
        if self.subtasks:
            lines.append("## Подзадачи")
            lines.extend(st.to_markdown() for st in self.subtasks)
            lines.append("")
        add_section("Текущие проблемы", [f"{i + 1}. {p}" for i, p in enumerate(self.problems)])
        add_section("Следующие шаги", [f"- {s}" for s in self.next_steps])
        add_section("Критерии успеха", [f"- {c}" for c in self.success_criteria])
        add_section("Зависимости", [f"- {d}" for d in self.dependencies])
        add_section("Риски", [f"- {r}" for r in self.risks])
        add_section("История", [f"- {h}" for h in self.history])

        return "\n".join(lines).strip() + "\n"


def _iso_timestamp() -> str:
    """Возвращает ISO-8601 timestamp с UTC."""
    return datetime.now(timezone.utc).isoformat()


def _load_input_source(raw: str, label: str) -> str:
    """Загружает текстовый payload из строки, файла или STDIN."""
    source = (raw or "").strip()
    if not source:
        return source
    if source == "-":
        data = sys.stdin.read()
        if not data.strip():
            raise SubtaskParseError(f"STDIN пуст: передай {label}")
        return data
    if source.startswith("@"):
        path_str = source[1:].strip()
        if not path_str:
            raise SubtaskParseError(f"Укажи путь к {label} после символа '@'")
        file_path = Path(path_str).expanduser()
        if not file_path.exists():
            raise SubtaskParseError(f"Файл не найден: {file_path}")
        return file_path.read_text(encoding="utf-8")
    return source


def _load_subtasks_source(raw: str) -> str:
    return _load_input_source(raw, "JSON массивом подзадач")


def subtask_to_dict(subtask: SubTask) -> Dict[str, Any]:
    """Структурированное представление подзадачи."""
    return {
        "title": subtask.title,
        "completed": subtask.completed,
        "success_criteria": list(subtask.success_criteria),
        "tests": list(subtask.tests),
        "blockers": list(subtask.blockers),
        "criteria_confirmed": subtask.criteria_confirmed,
        "tests_confirmed": subtask.tests_confirmed,
        "blockers_resolved": subtask.blockers_resolved,
        "criteria_notes": list(subtask.criteria_notes),
        "tests_notes": list(subtask.tests_notes),
        "blockers_notes": list(subtask.blockers_notes),
    }


def task_to_dict(task: TaskDetail, include_subtasks: bool = False) -> Dict[str, Any]:
    """Структурированное представление задачи."""
    data: Dict[str, Any] = {
        "id": task.id,
        "title": task.title,
        "status": task.status,
        "progress": task.calculate_progress(),
        "priority": task.priority,
        "domain": task.domain,
        "phase": task.phase,
        "component": task.component,
        "parent": task.parent,
        "tags": list(task.tags),
        "assignee": task.assignee,
        "blocked": task.blocked,
        "blockers": list(task.blockers),
        "description": task.description,
        "context": task.context,
        "success_criteria": list(task.success_criteria),
        "dependencies": list(task.dependencies),
        "next_steps": list(task.next_steps),
        "problems": list(task.problems),
        "risks": list(task.risks),
        "history": list(task.history),
        "subtasks_count": len(task.subtasks),
    }
    if include_subtasks:
        data["subtasks"] = [subtask_to_dict(st) for st in task.subtasks]
    return data


def structured_response(
    command: str,
    *,
    status: str = "OK",
    message: str = "",
    payload: Optional[Dict[str, Any]] = None,
    summary: Optional[str] = None,
    exit_code: int = 0,
) -> int:
    """Единый формат вывода для всех неинтерактивных команд."""
    body: Dict[str, Any] = {
        "command": command,
        "status": status,
        "message": message,
        "timestamp": _iso_timestamp(),
        "payload": payload or {},
    }
    if summary:
        body["summary"] = summary
    print(json.dumps(body, ensure_ascii=False, indent=2))
    return exit_code


def structured_error(command: str, message: str, *, payload: Optional[Dict[str, Any]] = None, status: str = "ERROR") -> int:
    """Сокращение для ошибок."""
    return structured_response(command, status=status, message=message, payload=payload, exit_code=1)


def validation_response(command: str, success: bool, message: str, payload: Optional[Dict[str, Any]] = None) -> int:
    body = payload.copy() if payload else {}
    body["mode"] = "validate-only"
    label = f"{command}.validate"
    status = "OK" if success else "ERROR"
    return structured_response(
        label,
        status=status,
        message=message,
        payload=body,
        summary=message,
        exit_code=0 if success else 1,
    )


class Status(Enum):
    OK = ("OK", "green", "+")
    WARN = ("WARN", "yellow", "~")
    FAIL = ("FAIL", "red", "x")
    UNKNOWN = ("?", "blue", "?")

    @classmethod
    def from_string(cls, value: str) -> "Status":
        val = (value or "").strip().upper()
        for status in cls:
            if status.value[0] == val:
                return status
        return cls.UNKNOWN


@dataclass
class Task:
    name: str
    status: Status
    description: str
    category: str
    completed: bool = False
    task_file: Optional[str] = None
    progress: int = 0
    subtasks_count: int = 0
    subtasks_completed: int = 0
    id: Optional[str] = None
    parent: Optional[str] = None
    detail: Optional[TaskDetail] = None
    domain: str = ""
    phase: str = ""
    component: str = ""
    blocked: bool = False


# ============================================================================
# PERSISTENCE LAYER
# ============================================================================


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
            status=metadata.get("status", "FAIL"),
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
        )

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
        # Автостатус: если все подзадачи выполнены — статус OK (без изменения файла)
        try:
            if task.subtasks and task.calculate_progress() == 100 and not task.blocked:
                task.status = "OK"
        except Exception:
            pass
        return task

    @classmethod
    def _save_section(cls, task: TaskDetail, section: str, lines: List[str]) -> None:
        content = "\n".join(lines).strip()
        if section == "Описание":
            task.description = content
        elif section == "Контекст":
            task.context = content
        elif section == "Подзадачи":
            current_subtask = None
            for line in lines:
                m = cls.SUBTASK_PATTERN.match(line.strip())
                if m:
                    # Новая подзадача
                    if current_subtask:
                        task.subtasks.append(current_subtask)
                    current_subtask = SubTask(m.group(1).lower() == "x", m.group(2))
                elif current_subtask and line.strip().startswith("- "):
                    # Вложенные элементы подзадачи
                    stripped = line.strip()[2:]  # убираем "- "
                    if stripped.startswith("Критерии:"):
                        criteria_text = stripped[9:].strip()
                        current_subtask.success_criteria = [c.strip() for c in criteria_text.split(";") if c.strip()]
                    elif stripped.startswith("Тесты:"):
                        tests_text = stripped[6:].strip()
                        current_subtask.tests = [t.strip() for t in tests_text.split(";") if t.strip()]
                    elif stripped.startswith("Блокеры:"):
                        blockers_text = stripped[8:].strip()
                        current_subtask.blockers = [b.strip() for b in blockers_text.split(";") if b.strip()]
                    elif stripped.startswith("Чекпоинты:"):
                        checkpoints = [token.strip() for token in stripped[11:].strip().split(";") if token.strip()]
                        for token in checkpoints:
                            key, _, value = token.partition("=")
                            key = key.strip().lower()
                            flag = value.strip().lower() in ("ok", "done", "yes", "true", "готово", "готов", "+")
                            if key.startswith("критер"):
                                current_subtask.criteria_confirmed = flag
                            elif key.startswith("тест"):
                                current_subtask.tests_confirmed = flag
                            elif key.startswith("блок"):
                                current_subtask.blockers_resolved = flag
                    elif stripped.startswith("Отметки критериев:"):
                        notes_text = stripped.split(":", 1)[1].strip()
                        current_subtask.criteria_notes = [n.strip() for n in notes_text.split(";") if n.strip()]
                    elif stripped.startswith("Отметки тестов:"):
                        notes_text = stripped.split(":", 1)[1].strip()
                        current_subtask.tests_notes = [n.strip() for n in notes_text.split(";") if n.strip()]
                    elif stripped.startswith("Отметки блокеров:"):
                        notes_text = stripped.split(":", 1)[1].strip()
                        current_subtask.blockers_notes = [n.strip() for n in notes_text.split(";") if n.strip()]
            # Не забыть последнюю подзадачу
            if current_subtask:
                task.subtasks.append(current_subtask)
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


class TaskManager:
    def __init__(self, tasks_dir: Path = Path(".tasks")):
        self.tasks_dir = tasks_dir
        self.tasks_dir.mkdir(exist_ok=True)
        self.config = self.load_config()

    @staticmethod
    def sanitize_domain(domain: Optional[str]) -> str:
        """Безопасная нормализация подпапки внутри .tasks"""
        if not domain:
            return ""
        candidate = Path(domain.strip("/"))
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError("Недопустимая папка")
        return candidate.as_posix()

    def _all_task_files(self):
        return self.tasks_dir.rglob("TASK-*.task")

    @staticmethod
    def load_config() -> Dict:
        cfg = Path(".apply_taskrc.yaml")
        if cfg.exists():
            try:
                return yaml.safe_load(cfg.read_text()) or {}
            except Exception:
                return {}
        return {}

    def _next_id(self) -> str:
        ids = [int(f.stem.split("-")[1]) for f in self._all_task_files()]
        return f"TASK-{(max(ids) + 1 if ids else 1):03d}"

    def create_task(self, title: str, status: str = "FAIL", priority: str = "MEDIUM", parent: Optional[str] = None, domain: str = "", phase: str = "", component: str = "") -> TaskDetail:
        domain = self.sanitize_domain(domain)
        task = TaskDetail(
            id=self._next_id(),
            title=title,
            status=status,
            domain=domain,
            phase=phase,
            component=component,
            parent=parent,
            priority=priority,
            created=datetime.now().strftime("%Y-%m-%d"),
            updated=datetime.now().strftime("%Y-%m-%d"),
        )
        # НЕ сохраняем здесь - валидация должна пройти первой
        return task

    def save_task(self, task: TaskDetail) -> None:
        task.updated = datetime.now().strftime("%Y-%m-%d")
        prog = task.calculate_progress()
        if prog == 100 and not task.blocked:
            task.status = "OK"
        task.domain = self.sanitize_domain(task.domain)
        task.filepath.parent.mkdir(parents=True, exist_ok=True)
        task.filepath.write_text(task.to_file_content(), encoding="utf-8")

    def _find_file_by_id(self, task_id: str, domain: str = "") -> Optional[Path]:
        if domain:
            candidate = (self.tasks_dir / self.sanitize_domain(domain) / f"{task_id}.task").resolve()
            if candidate.exists():
                return candidate
        for f in self._all_task_files():
            if f.stem == task_id:
                return f
        return None

    def load_task(self, task_id: str, domain: str = "") -> Optional[TaskDetail]:
        file = self._find_file_by_id(task_id, domain)
        if not file:
            return None
        task = TaskFileParser.parse(file)
        if task:
            # если в файле нет domain — берем из пути
            if not task.domain:
                rel = file.parent.relative_to(self.tasks_dir)
                task.domain = "" if str(rel) == "." else rel.as_posix()
            if task.subtasks:
                prog = task.calculate_progress()
                if prog == 100 and not task.blocked and task.status != "OK":
                    task.status = "OK"
                    self.save_task(task)
        return task

    def list_tasks(self, domain: str = "") -> List[TaskDetail]:
        tasks: List[TaskDetail] = []
        selected = self._all_task_files() if not domain else (self.tasks_dir / self.sanitize_domain(domain)).glob("TASK-*.task")
        for file in sorted(selected):
            parsed = TaskFileParser.parse(file)
            if parsed:
                if not parsed.domain:
                    rel = file.parent.relative_to(self.tasks_dir)
                    parsed.domain = "" if str(rel) == "." else rel.as_posix()
                if parsed.subtasks:
                    prog = parsed.calculate_progress()
                    if prog == 100 and not parsed.blocked and parsed.status != "OK":
                        parsed.status = "OK"
                        self.save_task(parsed)
                tasks.append(parsed)
        return tasks

    def update_task_status(self, task_id: str, status: str, domain: str = "") -> Tuple[bool, Optional[Dict[str, str]]]:
        task = self.load_task(task_id, domain)
        if not task:
            return False, {"code": "not_found", "message": f"Задача {task_id} не найдена"}
        # Flagship-проверка перед установкой OK
        if status == "OK":
            if task.subtasks and task.calculate_progress() < 100:
                return False, {"code": "validation", "message": "Нельзя установить OK: не все подзадачи выполнены"}
            if not task.success_criteria:
                return False, {"code": "validation", "message": "Нельзя установить OK: нет критериев успеха/тестов на уровне задачи"}
            # Проверка что все подзадачи имеют критерии и тесты
            for idx, st in enumerate(task.subtasks, 1):
                if not st.success_criteria:
                    return False, {
                        "code": "validation",
                        "message": f"Нельзя установить OK: подзадача {idx} '{st.title}' не имеет критериев выполнения",
                    }
                if not st.tests:
                    return False, {
                        "code": "validation",
                        "message": f"Нельзя установить OK: подзадача {idx} '{st.title}' не имеет тестов",
                    }
            task.progress = 100
        else:
            task.status = status
            if status == "WARN" and task.progress == 0 and task.subtasks:
                task.progress = task.calculate_progress()
            if status == "FAIL" and task.progress == 0 and task.subtasks:
                task.progress = task.calculate_progress()

        task.status = status
        self.save_task(task)
        return True, None

    def add_subtask(self, task_id: str, title: str, domain: str = "", criteria: Optional[List[str]] = None, tests: Optional[List[str]] = None, blockers: Optional[List[str]] = None) -> Tuple[bool, Optional[str]]:
        task = self.load_task(task_id, domain)
        if not task:
            return False, "not_found"
        crit = [c.strip() for c in (criteria or []) if c.strip()]
        tst = [t.strip() for t in (tests or []) if t.strip()]
        bl = [b.strip() for b in (blockers or []) if b.strip()]
        if not crit or not tst or not bl:
            return False, "missing_fields"
        task.subtasks.append(SubTask(False, title, crit, tst, bl))
        task.update_status_from_progress()
        self.save_task(task)
        return True, None

    def set_subtask(self, task_id: str, index: int, completed: bool, domain: str = "") -> Tuple[bool, Optional[str]]:
        task = self.load_task(task_id, domain)
        if not task:
            return False, "not_found"
        if index < 0 or index >= len(task.subtasks):
            return False, "index"
        st = task.subtasks[index]
        if completed and not st.ready_for_completion():
            missing = []
            if not st.criteria_confirmed:
                missing.append("критерии")
            if not st.tests_confirmed:
                missing.append("тесты")
            if not st.blockers_resolved:
                missing.append("блокеры")
            return False, f"Отметь {', '.join(missing)} перед завершением"
        st.completed = completed
        task.update_status_from_progress()
        self.save_task(task)
        return True, None

    def update_subtask_checkpoint(self, task_id: str, index: int, checkpoint: str, value: bool, note: str = "", domain: str = "") -> Tuple[bool, Optional[str]]:
        task = self.load_task(task_id, domain)
        if not task:
            return False, "not_found"
        if index < 0 or index >= len(task.subtasks):
            return False, "index"
        st = task.subtasks[index]
        checkpoint = checkpoint.lower()
        attr_map = {
            "criteria": ("criteria_confirmed", "criteria_notes"),
            "tests": ("tests_confirmed", "tests_notes"),
            "blockers": ("blockers_resolved", "blockers_notes"),
        }
        if checkpoint not in attr_map:
            return False, "unknown_checkpoint"
        flag_attr, notes_attr = attr_map[checkpoint]
        setattr(st, flag_attr, value)
        note = note.strip()
        if note:
            getattr(st, notes_attr).append(note)
        if not value:
            st.completed = False
        task.update_status_from_progress()
        self.save_task(task)
        return True, None

    def add_dependency(self, task_id: str, dep: str, domain: str = "") -> bool:
        task = self.load_task(task_id, domain)
        if not task:
            return False
        task.dependencies.append(dep)
        self.save_task(task)
        return True

    def move_task(self, task_id: str, new_domain: str) -> bool:
        target_domain = self.sanitize_domain(new_domain)
        file = self._find_file_by_id(task_id)
        if not file:
            return False
        detail = TaskFileParser.parse(file)
        if not detail:
            return False
        detail.domain = target_domain
        # remove old file after saving new to avoid loss
        self.save_task(detail)
        if file.exists():
            try:
                file.unlink()
            except Exception:
                pass
        return True

    def move_glob(self, pattern: str, new_domain: str) -> int:
        target_domain = self.sanitize_domain(new_domain)
        matched = 0
        for f in self.tasks_dir.rglob(pattern):
            if f.is_file() and f.stem.startswith("TASK-") and f.suffix == ".task":
                tid = f.stem
                if self.move_task(tid, target_domain):
                    matched += 1
        return matched

    def clean_tasks(self, tag: Optional[str] = None, status: Optional[str] = None, phase: Optional[str] = None, dry_run: bool = False) -> Tuple[List[str], int]:
        base = Path(".tasks")
        if not base.exists():
            return [], 0
        matched: List[str] = []
        removed = 0
        norm_tag = (tag or "").strip().lower()
        norm_status = (status or "").strip().upper()
        norm_phase = (phase or "").strip().lower()

        for file in base.rglob("*.task"):
            detail = TaskFileParser.parse(file)
            if not detail:
                continue
            tags = [t.strip().lower() for t in (detail.tags or [])]
            if norm_tag and norm_tag not in tags:
                continue
            if norm_status and (detail.status or "").upper() != norm_status:
                continue
            if norm_phase and (detail.phase or "").strip().lower() != norm_phase:
                continue
            matched.append(detail.id)
            if not dry_run:
                try:
                    file.unlink()
                    removed += 1
                except OSError:
                    pass
        return matched, removed


def save_last_task(task_id: str, domain: str = "") -> None:
    Path(".last").write_text(f"{task_id}@{domain}", encoding="utf-8")


def get_last_task() -> Tuple[Optional[str], Optional[str]]:
    last = Path(".last")
    if not last.exists():
        return None, None
    raw = last.read_text(encoding="utf-8").strip()
    if "@" in raw:
        tid, domain = raw.split("@", 1)
        return tid or None, domain or None
    return raw or None, None


def resolve_task_reference(
    raw_task_id: Optional[str],
    domain: Optional[str],
    phase: Optional[str],
    component: Optional[str],
) -> Tuple[str, str]:
    """
    Возвращает (task_id, domain) с поддержкой шорткатов:
    '.' / 'last' / '@last' / пустое значение → последняя задача из .last.
    """
    sentinel = (raw_task_id or "").strip()
    use_last = not sentinel or sentinel in (".", "last", "@last")
    if use_last:
        last_id, last_domain = get_last_task()
        if not last_id:
            raise ValueError("Нет последней задачи: вызови apply_task show/list/next для привязки контекста")
        resolved_domain = derive_domain_explicit(domain, phase, component) or (last_domain or "")
        return normalize_task_id(last_id), resolved_domain or ""
    resolved_domain = derive_domain_explicit(domain, phase, component)
    return normalize_task_id(sentinel), resolved_domain


def normalize_task_id(raw: str) -> str:
    value = raw.strip().upper()
    if re.match(r"^TASK-\d+$", value):
        num = int(value.split("-")[1])
        return f"TASK-{num:03d}"
    if value.isdigit():
        return f"TASK-{int(value):03d}"
    return value


def derive_domain_explicit(domain: Optional[str], phase: Optional[str], component: Optional[str]) -> str:
    """При отсутствии явного domain строит путь из phase/component."""
    if domain:
        return TaskManager.sanitize_domain(domain)
    parts = []
    if phase:
        parts.append(phase.strip("/"))
    if component:
        parts.append(component.strip("/"))
    if not parts:
        return ""
    return TaskManager.sanitize_domain("/".join(parts))


def parse_smart_title(title: str) -> Tuple[str, List[str], List[str]]:
    tags = re.findall(r"#(\w+)", title)
    deps = re.findall(r"@(TASK-\d+)", title.upper())
    clean = re.sub(r"#\w+", "", title)
    clean = re.sub(r"@TASK-\d+", "", clean, flags=re.IGNORECASE).strip()
    return clean, [t.lower() for t in tags], deps


CHECKLIST_SECTIONS = [
    (
        "plan",
        ["plan", "break", "шаг"],
        "Plan: break work into atomic steps with measurable outcomes",
        ["step", "milestone", "outcome", "scope", "estimate"],
    ),
    (
        "validation",
        ["test", "lint", "вали", "qa"],
        "Validation plan: tests/linters per step and commit checkpoints",
        ["test", "pytest", "unit", "integration", "lint", "coverage", "commit", "checkpoint"],
    ),
    (
        "risks",
        ["risk", "dependency", "риск", "завис", "блок"],
        "Risk scan: failures, dependencies, bottlenecks",
        ["risk", "dependency", "blocker", "bottleneck", "assumption"],
    ),
    (
        "readiness",
        ["readiness", "ready", "done", "criteria", "dod", "готов", "metric"],
        "Readiness criteria: DoD, coverage/perf metrics, expected behavior",
        ["DoD", "definition", "coverage", "perf", "metric", "acceptance", "criteria"],
    ),
    (
        "execute",
        ["execute", "implement", "исполн", "build"],
        "Execute steps with per-step validation and record results",
        ["implement", "code", "wire", "build", "validate"],
    ),
    (
        "final",
        ["final", "full", "release", "финаль", "итог"],
        "Final verification: full tests/linters, metrics check, release/commit prep",
        ["regression", "full", "release", "report", "metrics", "handoff"],
    ),
]


def validate_subtasks_coverage(subtasks: List[SubTask]) -> Tuple[bool, List[str]]:
    """Checks that all required checklist sections are covered with substantive content."""
    present: Dict[str, SubTask] = {}
    for st in subtasks:
        low = st.title.lower()
        for name, keywords, *_ in CHECKLIST_SECTIONS:
            if any(k in low for k in keywords):
                # first match wins to keep deterministic error reporting
                present.setdefault(name, st)

    missing = [name for name, *_ in CHECKLIST_SECTIONS if name not in present]
    return not missing, missing


def validate_subtasks_quality(subtasks: List[SubTask]) -> Tuple[bool, List[str]]:
    """Проверяет, что каждая подзадача детализирована: есть двоеточия, ключевые блоки, достаточная длина."""
    issues: List[str] = []
    present: Dict[str, SubTask] = {}
    for st in subtasks:
        low = st.title.lower()
        for name, keywords, _, anchors in CHECKLIST_SECTIONS:
            if any(k in low for k in keywords) and any(a in low for a in anchors):
                # сохраняем самую подробную подзадачу для секции
                if name not in present or len(st.title) > len(present[name].title):
                    present[name] = st

    for name, _, desc, anchors in CHECKLIST_SECTIONS:
        st = present.get(name)
        if not st:
            continue
        text = st.title.strip()
        long_enough = len(text) >= 30
        has_colon = ":" in text
        has_any_anchor = any(a.lower() in text.lower() for a in anchors)
        if not (long_enough and has_colon and has_any_anchor):
            issues.append(f"{name}: добавь детали (>=30 символов, включи ':' и ключевые слова из темы)")
    return len(issues) == 0, issues


def validate_subtasks_structure(subtasks: List[SubTask]) -> Tuple[bool, List[str]]:
    """Каждая подзадача должна содержать критерии, тесты и блокеры."""
    issues: List[str] = []
    for idx, st in enumerate(subtasks, 1):
        missing = []
        if not st.success_criteria:
            missing.append("критерии")
        if not st.tests:
            missing.append("тесты")
        if not st.blockers:
            missing.append("блокеры")
        if missing:
            issues.append(f"Подзадача {idx}: добавь {', '.join(missing)}")
    return len(issues) == 0, issues


def validate_flagship_subtasks(subtasks: List[SubTask]) -> Tuple[bool, List[str]]:
    """
    Flagship-валидация: каждая подзадача должна иметь:
    - Критерии выполнения (success_criteria)
    - Тесты для проверки (tests)
    - Блокеры/зависимости и план снятия блокеров
    - Быть атомарной (не содержать составных действий)
    - Минимум 20 символов в описании
    """
    if not subtasks:
        return False, ["Задача должна быть декомпозирована на подзадачи"]

    if len(subtasks) < 3:
        return False, [f"Недостаточно подзадач ({len(subtasks)}). Минимум 3 для flagship-качества"]

    all_issues = []
    for idx, st in enumerate(subtasks, 1):
        valid, issues = st.is_valid_flagship()
        if not valid:
            all_issues.extend([f"Подзадача {idx}: {issue}" for issue in issues])

    return len(all_issues) == 0, all_issues


# ============================================================================
# FLEXIBLE SUBTASK PARSING (JSON ONLY)
# ============================================================================


class SubtaskParseError(Exception):
    """Ошибка парсинга подзадач"""
    pass


def _to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "y", "ok", "done", "ready", "готов", "готово", "+")
    return bool(value)


def parse_subtasks_json(raw: str) -> List[SubTask]:
    """Парсинг подзадач из JSON формата"""
    try:
        data = json.loads(raw)
        if not isinstance(data, list):
            raise SubtaskParseError("JSON должен быть массивом объектов")

        subtasks = []
        for idx, item in enumerate(data, 1):
            if not isinstance(item, dict):
                raise SubtaskParseError(f"Элемент {idx} должен быть объектом")

            title = item.get("title", "")
            if not title:
                raise SubtaskParseError(f"Элемент {idx}: отсутствует 'title'")

            criteria = item.get("criteria", item.get("success_criteria", []))
            tests = item.get("tests", [])
            blockers = item.get("blockers", [])

            if not isinstance(criteria, list):
                criteria = [str(criteria)]
            if not isinstance(tests, list):
                tests = [str(tests)]
            if not isinstance(blockers, list):
                blockers = [str(blockers)]

            if not criteria:
                raise SubtaskParseError(f"Элемент {idx}: укажи хотя бы один критерий выполнения")
            if not tests:
                raise SubtaskParseError(f"Элемент {idx}: укажи тесты для проверки")
            if not blockers:
                raise SubtaskParseError(f"Элемент {idx}: укажи блокеры/зависимости")

            criteria_notes = item.get("criteria_notes", [])
            tests_notes = item.get("tests_notes", [])
            blockers_notes = item.get("blockers_notes", [])
            if not isinstance(criteria_notes, list):
                criteria_notes = [str(criteria_notes)]
            if not isinstance(tests_notes, list):
                tests_notes = [str(tests_notes)]
            if not isinstance(blockers_notes, list):
                blockers_notes = [str(blockers_notes)]

            st = SubTask(
                False,
                title,
                criteria,
                tests,
                blockers,
                criteria_confirmed=_to_bool(item.get("criteria_confirmed", False)),
                tests_confirmed=_to_bool(item.get("tests_confirmed", False)),
                blockers_resolved=_to_bool(item.get("blockers_resolved", False)),
                criteria_notes=[str(n).strip() for n in criteria_notes if str(n).strip()],
                tests_notes=[str(n).strip() for n in tests_notes if str(n).strip()],
                blockers_notes=[str(n).strip() for n in blockers_notes if str(n).strip()],
            )
            subtasks.append(st)

        return subtasks
    except json.JSONDecodeError as e:
        raise SubtaskParseError(f"Невалидный JSON: {e}")


def parse_subtasks_flexible(raw: str) -> List[SubTask]:
    """Парсинг подзадач в единственном поддерживаемом формате: JSON-массив объектов"""
    raw = raw.strip()

    if not raw:
        return []

    try:
        return parse_subtasks_json(raw)
    except SubtaskParseError as e:
        raise SubtaskParseError(
            "Используй JSON массив. Пример: '[{\"title\":\"Design cache rollout >=20 chars\","
            "\"criteria\":[\"hit ratio >80%\"],\"tests\":[\"pytest -k cache\"],\"blockers\":[\"redis downtime\"]}]'\n"
            f"Причина: {e}"
        )


# ============================================================================
# INTERACTIVE HELPERS
# ============================================================================


def is_interactive() -> bool:
    """Проверка что мы в интерактивном TTY режиме"""
    return sys.stdin.isatty() and sys.stdout.isatty()


def prompt(question: str, default: str = "") -> str:
    """Запрос строки от пользователя"""
    if default:
        question = f"{question} [{default}]"
    try:
        response = input(f"{question}: ").strip()
        return response if response else default
    except (EOFError, KeyboardInterrupt):
        print("\n[X] Прервано")
        sys.exit(1)


def prompt_required(question: str) -> str:
    """Запрос обязательной строки"""
    while True:
        response = prompt(question)
        if response:
            return response
        print("  [!] Обязательное поле")


def prompt_list(question: str, min_items: int = 0) -> List[str]:
    """Запрос списка строк (по одной на строку, пустая строка = конец)"""
    print(f"{question} (пустая строка для завершения):")
    items = []
    while True:
        try:
            line = input(f"  {len(items) + 1}. ").strip()
            if not line:
                if len(items) >= min_items:
                    break
                print(f"  [!] Минимум {min_items} элементов")
                continue
            items.append(line)
        except (EOFError, KeyboardInterrupt):
            print("\n[X] Прервано")
            sys.exit(1)
    return items


def confirm(question: str, default: bool = True) -> bool:
    """Запрос подтверждения (y/n)"""
    suffix = " [Y/n]" if default else " [y/N]"
    try:
        response = input(f"{question}{suffix}: ").strip().lower()
        if not response:
            return default
        return response in ('y', 'yes', 'д', 'да')
    except (EOFError, KeyboardInterrupt):
        print("\n[X] Прервано")
        sys.exit(1)


def prompt_subtask_interactive(index: int) -> SubTask:
    """Интерактивное создание подзадачи"""
    print(f"\n[C] Подзадача {index}:")
    title = prompt_required("  Название (минимум 20 символов)")
    while len(title) < 20:
        print(f"  [!] Слишком короткое ({len(title)}/20). Добавь детали")
        title = prompt_required("  Название")

    criteria = prompt_list("  Критерии выполнения", min_items=1)
    tests = prompt_list("  Тесты для проверки", min_items=1)
    blockers = prompt_list("  Блокеры/зависимости (обязательны, минимум 1)", min_items=1)

    return SubTask(False, title, criteria, tests, blockers)


def subtask_flags(st: SubTask) -> Dict[str, bool]:
    return {
        "criteria": st.criteria_confirmed,
        "tests": st.tests_confirmed,
        "blockers": st.blockers_resolved,
    }


def load_template(kind: str, manager: TaskManager) -> Tuple[str, str]:
    cfg = manager.config.get("templates", {})
    tpl = cfg.get(kind, cfg.get("default", {})) or {}
    desc = tpl.get("description", "")
    tests = tpl.get("tests", "")
    if not desc and not tests:
        return "TBD", "acceptance"
    return desc, tests


# ============================================================================
# TUI RESPONSIVE LAYOUT
# ============================================================================


@dataclass
class ColumnLayout:
    """Определяет, какие колонки отображать и их ширину"""
    min_width: int
    columns: List[str]
    stat_w: int = 3
    prog_w: int = 7
    subt_w: int = 9

    def has_column(self, name: str) -> bool:
        return name in self.columns

    def calculate_widths(self, term_width: int) -> Dict[str, int]:
        """Рассчитывает динамические ширины для гибких колонок (title)"""
        widths = {
            'stat': self.stat_w,
            'progress': self.prog_w,
            'subtasks': self.subt_w,
        }

        # Подсчёт фиксированных колонок
        fixed_width = sum(widths[col] for col in widths if col in self.columns)
        separators = len(self.columns) + 1  # Количество |

        remaining = max(term_width - fixed_width - separators, 20)

        # Распределяем оставшееся пространство
        if 'title' in self.columns:
            # Заголовок получает всё оставшееся пространство
            widths['title'] = max(20, remaining)

        return widths


class ResponsiveLayoutManager:
    """Управляет адаптивными layout в зависимости от ширины терминала"""

    LAYOUTS = [
        ColumnLayout(min_width=200, columns=['stat', 'title', 'progress', 'subtasks']),
        ColumnLayout(min_width=150, columns=['stat', 'title', 'progress', 'subtasks']),
        ColumnLayout(min_width=120, columns=['stat', 'title', 'progress', 'subtasks']),
        ColumnLayout(min_width=95, columns=['stat', 'title', 'progress', 'subtasks']),
        ColumnLayout(min_width=75, columns=['stat', 'title', 'progress', 'subtasks']),
        ColumnLayout(min_width=55, columns=['stat', 'title', 'progress', 'subtasks']),
        ColumnLayout(min_width=0, columns=['stat', 'progress', 'title', 'subtasks'], stat_w=3, prog_w=6),
    ]

    @classmethod
    def select_layout(cls, term_width: int) -> ColumnLayout:
        """Выбирает подходящий layout для текущей ширины терминала"""
        for layout in cls.LAYOUTS:
            if term_width >= layout.min_width:
                return layout
        return cls.LAYOUTS[-1]


# ============================================================================
# TUI
# ============================================================================

THEMES: Dict[str, Dict[str, str]] = {
    "dark-olive": {
        "": "#d7dfe6",  # без принудительной подложки
        "status.ok": "#9ad974 bold",
        "status.warn": "#e5c07b bold",
        "status.fail": "#e06c75 bold",
        "status.unknown": "#7a7f85",
        "text": "#d7dfe6",
        "text.dim": "#97a0a9",
        "text.dimmer": "#6d717a",
        "selected": "bg:#3c4c35 #eef7dc bold",  # мягкий olive, не черный
        "header": "#ffb347 bold",
        "border": "#4b525a",
        "icon.check": "#9ad974 bold",
        "icon.warn": "#f9ac60 bold",
        "icon.fail": "#ff5156 bold",
    },
    "dark-contrast": {
        "": "#e8eaec",  # без черной подложки
        "status.ok": "#b8f171 bold",
        "status.warn": "#f0c674 bold",
        "status.fail": "#ff6b6b bold",
        "status.unknown": "#8a9097",
        "text": "#e8eaec",
        "text.dim": "#a7b0ba",
        "text.dimmer": "#6f757d",
        "selected": "bg:#23452f #f4ffe8 bold",  # тёмно-зеленая, но не черная
        "header": "#ffb347 bold",
        "border": "#5a6169",
        "icon.check": "#b8f171 bold",
        "icon.warn": "#f9ac60 bold",
        "icon.fail": "#ff5156 bold",
    },
}

DEFAULT_THEME = "dark-olive"


class TaskTrackerTUI:
    @staticmethod
    def get_theme_palette(theme: str) -> Dict[str, str]:
        base = THEMES.get(theme)
        if not base:
            base = THEMES[DEFAULT_THEME]
        return dict(base)  # defensive copy

    @classmethod
    def build_style(cls, theme: str) -> Style:
        palette = cls.get_theme_palette(theme)
        return Style.from_dict(palette)

    def __init__(self, tasks_dir: Path = Path(".tasks"), domain: str = "", phase: str = "", component: str = "", theme: str = DEFAULT_THEME):
        self.tasks_dir = tasks_dir
        self.manager = TaskManager(tasks_dir)
        self.domain_filter = domain
        self.phase_filter = phase
        self.component_filter = component
        self.tasks: List[Task] = []
        self.selected_index = 0
        self.current_filter: Optional[Status] = None
        self.detail_mode = False
        self.current_task_detail: Optional[TaskDetail] = None
        self.current_task: Optional[Task] = None
        self.detail_selected_index = 0
        self.navigation_stack = []
        self.task_details_cache: Dict[str, TaskDetail] = {}
        self._last_signature = None
        self._last_check = 0.0
        self.horizontal_offset = 0  # For horizontal scrolling
        self.theme_name = theme
        self.status_message: str = ""
        self.status_message_expires: float = 0.0
        self.help_visible: bool = False

        # Editing mode
        self.editing_mode = False
        self.edit_buffer = Buffer(multiline=False)
        self.edit_context = None  # 'task_title', 'subtask_title', 'criterion', 'test', 'blocker'
        self.edit_index = None

        self.load_tasks()

        self.style = self.build_style(theme)

        kb = KeyBindings()

        @kb.add("q")
        @kb.add("й")
        @kb.add("c-z")
        def _(event):
            event.app.exit()

        @kb.add("r")
        @kb.add("к")
        def _(event):
            self.load_tasks(preserve_selection=True)

        @kb.add("down")
        @kb.add("j")
        @kb.add("о")
        def _(event):
            self.move_vertical_selection(1)

        @kb.add(Keys.ScrollDown)
        def _(event):
            self.move_vertical_selection(1)

        @kb.add("up")
        @kb.add("k")
        @kb.add("л")
        def _(event):
            self.move_vertical_selection(-1)

        @kb.add(Keys.ScrollUp)
        def _(event):
            self.move_vertical_selection(-1)

        @kb.add("0")
        def _(event):
            self.current_filter = None
            self.selected_index = 0

        @kb.add("1")
        def _(event):
            self.current_filter = Status.OK
            self.selected_index = 0

        @kb.add("2")
        def _(event):
            self.current_filter = Status.WARN
            self.selected_index = 0

        @kb.add("3")
        def _(event):
            self.current_filter = Status.FAIL
            self.selected_index = 0

        @kb.add("?")
        def _(event):
            self.help_visible = not self.help_visible

        @kb.add("enter")
        def _(event):
            if self.editing_mode:
                # В режиме редактирования - сохранить
                self.save_edit()
            elif self.detail_mode and self.current_task_detail:
                # В режиме деталей Enter показывает карточку выбранной подзадачи
                if self.detail_selected_index < len(self.current_task_detail.subtasks):
                    st = self.current_task_detail.subtasks[self.detail_selected_index]
                    self.show_subtask_details(st, self.detail_selected_index)
            else:
                if self.filtered_tasks:
                    self.show_task_details(self.filtered_tasks[self.selected_index])

        @kb.add("escape")
        def _(event):
            if self.editing_mode:
                # В режиме редактирования - отменить
                self.cancel_edit()
            elif self.detail_mode:
                if hasattr(self, "single_subtask_view") and self.single_subtask_view:
                    self.single_subtask_view = None
                    return
                if self.navigation_stack:
                    prev = self.navigation_stack.pop()
                    self.current_task = prev["task"]
                    self.current_task_detail = prev["detail"]
                    self.detail_selected_index = 0
                else:
                    self.detail_mode = False
                    self.current_task = None
                    self.current_task_detail = None
                    self.detail_selected_index = 0
                self.horizontal_offset = 0  # Reset scroll on exit

        @kb.add("delete")
        @kb.add("c-d")
        def _(event):
            """Delete - удалить выбранную задачу или подзадачу"""
            self.delete_current_item()

        @kb.add("d")
        @kb.add("в")
        def _(event):
            """d - переключить выполнение подзадачи"""
            if self.detail_mode and self.current_task_detail:
                self.toggle_subtask_completion()

        @kb.add("e")
        @kb.add("у")
        def _(event):
            """e - редактировать"""
            if not self.editing_mode:
                self.edit_current_item()

        @kb.add("backspace")
        def _(event):
            """Backspace - удалить символ при редактировании"""
            if self.editing_mode and len(self.edit_buffer.text) > 0:
                self.edit_buffer.text = self.edit_buffer.text[:-1]

        # Обработчик для печатных символов в editing mode
        @kb.add("<any>")
        def _(event):
            """Ввод текста в editing mode"""
            if self.editing_mode and event.data and len(event.data) == 1:
                # Только печатные ASCII и основные символы
                if 32 <= ord(event.data) <= 126 or ord(event.data) >= 128:
                    self.edit_buffer.text += event.data

        # Alternative keyboard controls for testing horizontal scroll
        @kb.add("c-left")
        def _(event):
            """Ctrl+Left - scroll content left"""
            self.horizontal_offset = max(0, self.horizontal_offset - 5)

        @kb.add("c-right")
        def _(event):
            """Ctrl+Right - scroll content right"""
            self.horizontal_offset = min(200, self.horizontal_offset + 5)

        @kb.add("[")
        def _(event):
            """[ - scroll content left (alternative)"""
            self.horizontal_offset = max(0, self.horizontal_offset - 3)

        @kb.add("]")
        def _(event):
            """] - scroll content right (alternative)"""
            self.horizontal_offset = min(200, self.horizontal_offset + 3)

        @kb.add("home")
        def _(event):
            """Home - reset scroll"""
            self.horizontal_offset = 0

        # Store reference to self for mouse handler
        tui_self = self

        # Create mouse handler for horizontal scrolling
        def handle_mouse(mouse_event: MouseEvent):
            """Handle mouse events for horizontal scrolling"""
            if mouse_event.event_type == MouseEventType.SCROLL_UP:
                # Shift+Scroll Up = scroll left
                if hasattr(mouse_event, 'modifiers') and mouse_event.modifiers.shift:
                    tui_self.horizontal_offset = max(0, tui_self.horizontal_offset - 3)
                    return None
            elif mouse_event.event_type == MouseEventType.SCROLL_DOWN:
                # Shift+Scroll Down = scroll right
                if hasattr(mouse_event, 'modifiers') and mouse_event.modifiers.shift:
                    tui_self.horizontal_offset = min(200, tui_self.horizontal_offset + 3)
                    return None
            return NotImplemented

        self.status_bar = Window(content=FormattedTextControl(self.get_status_text), height=1)
        self.task_list = Window(content=FormattedTextControl(self.get_task_list_text), always_hide_cursor=True, wrap_lines=False)
        self.side_preview = Window(content=FormattedTextControl(self.get_side_preview_text), always_hide_cursor=True, wrap_lines=True, width=Dimension(weight=2))
        self.detail_view = Window(content=FormattedTextControl(self.get_detail_text), always_hide_cursor=True, wrap_lines=True)
        self.footer = Window(content=FormattedTextControl(self.get_footer_text), height=Dimension(min=2))

        self.normal_body = VSplit(
            [
                Window(content=FormattedTextControl(self.get_task_list_text), always_hide_cursor=True, wrap_lines=False, width=Dimension(weight=3)),
                Window(width=1, char=' '),
                self.side_preview,
            ],
            padding=0,
        )

        # Main content window
        self.main_window = Window(
            content=FormattedTextControl(self.get_body_content),
            always_hide_cursor=True,
            wrap_lines=True,
        )

        root = HSplit([self.status_bar, self.main_window, self.footer])

        self.app = Application(layout=Layout(root), key_bindings=kb, style=self.style, full_screen=True, mouse_support=True, refresh_interval=1.0)

    @staticmethod
    def get_terminal_width() -> int:
        """Get current terminal width, default to 100 if unavailable."""
        try:
            return os.get_terminal_size().columns
        except (AttributeError, ValueError, OSError):
            return 100

    def move_vertical_selection(self, delta: int) -> None:
        """
        Move selected row/panel pointer by `delta`, clamping to available items.

        Works both in list mode (task rows) and detail mode (subtasks/dependencies).
        """
        if self.detail_mode:
            items = self.get_detail_items_count()
            if items <= 0:
                self.detail_selected_index = 0
                return
            self.detail_selected_index = max(0, min(self.detail_selected_index + delta, items - 1))
        else:
            total = len(self.filtered_tasks)
            if total <= 0:
                self.selected_index = 0
                return
            self.selected_index = max(0, min(self.selected_index + delta, total - 1))

    def apply_horizontal_scroll(self, text: str) -> str:
        """Apply horizontal scroll offset to a text line."""
        if self.horizontal_offset == 0:
            return text
        # Skip offset characters from the beginning
        if len(text) <= self.horizontal_offset:
            return ""
        return text[self.horizontal_offset:]

    def scroll_line_preserve_borders(self, line: str) -> str:
        """Scroll a single line of text, preserving leading border."""
        if not line or self.horizontal_offset == 0:
            return line

        # Check if line has table borders (starts with + or |)
        if line.startswith(('+', '|')):
            # Keep first character (left border)
            border_char = line[0]
            content = line[1:]

            # Apply offset to content
            if len(content) > self.horizontal_offset:
                scrolled_content = content[self.horizontal_offset:]
            else:
                scrolled_content = ""

            return border_char + scrolled_content
        else:
            # No border, apply offset normally
            return self.apply_horizontal_scroll(line)

    def apply_scroll_to_formatted(self, formatted_items: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
        """Apply horizontal scroll to formatted text line by line, preserving table structure."""
        if self.horizontal_offset == 0:
            return formatted_items

        result = []
        current_line = []

        for style, text in formatted_items:
            # Split text by newlines
            parts = text.split('\n')

            for i, part in enumerate(parts):
                if i > 0:
                    # We hit a newline - process accumulated line
                    if current_line:
                        # Convert line to plain text
                        line_text = ''.join(t for _, t in current_line)
                        # Scroll it
                        scrolled = self.scroll_line_preserve_borders(line_text)
                        # Add scrolled line
                        if scrolled:
                            result.append(('class:text', scrolled))
                        result.append(('', '\n'))
                        current_line = []

                # Add part to current line (if not empty)
                if part:
                    current_line.append((style, part))

        # Process last line if exists
        if current_line:
            line_text = ''.join(t for _, t in current_line)
            scrolled = self.scroll_line_preserve_borders(line_text)
            if scrolled:
                result.append(('class:text', scrolled))

        return result

    @property
    def filtered_tasks(self) -> List[Task]:
        if not self.current_filter:
            return self.tasks
        return [t for t in self.tasks if t.status == self.current_filter]

    def compute_signature(self) -> int:
        sig = 0
        for f in self.tasks_dir.rglob("TASK-*.task"):
            sig ^= int(f.stat().st_mtime_ns)
        return sig

    def maybe_reload(self):
        if self.detail_mode:
            return
        now = time.time()
        if now - self._last_check < 0.7:
            return
        self._last_check = now
        sig = self.compute_signature()
        if sig != self._last_signature:
            selected = self.tasks[self.selected_index].task_file if self.tasks else None
            self.load_tasks(preserve_selection=True, selected_task_file=selected)
            self._last_signature = sig

    def load_tasks(self, preserve_selection: bool = False, selected_task_file: Optional[str] = None):
        domain_path = derive_domain_explicit(self.domain_filter, self.phase_filter, self.component_filter)
        details = self.manager.list_tasks(domain_path)
        if self.phase_filter:
            details = [d for d in details if d.phase == self.phase_filter]
        if self.component_filter:
            details = [d for d in details if d.component == self.component_filter]
        tasks: List[Task] = []
        for det in details:
            task_file = f".tasks/{det.domain + '/' if det.domain else ''}{det.id}.task"
            calc_progress = det.calculate_progress()
            derived_status = Status.OK if calc_progress == 100 and not det.blocked else Status.from_string(det.status)
            tasks.append(
                Task(
                    id=det.id,
                    name=det.title,
                    status=derived_status,
                    description=(det.description or det.context or "")[:80],
                    category=det.domain or det.priority,
                    completed=derived_status == Status.OK,
                    task_file=task_file,
                    progress=calc_progress,
                    subtasks_count=len(det.subtasks),
                    subtasks_completed=sum(1 for st in det.subtasks if st.completed),
                    parent=det.parent,
                    detail=det,
                    domain=det.domain,
                    phase=det.phase,
                    component=det.component,
                    blocked=det.blocked,
                )
            )
        self.tasks = tasks
        if preserve_selection and selected_task_file:
            for idx, t in enumerate(self.tasks):
                if t.task_file == selected_task_file:
                    self.selected_index = idx
                    break
        else:
            self.selected_index = 0
        self.detail_mode = False
        self.current_task = None
        self.current_task_detail = None
        self._last_signature = self.compute_signature()

    def set_status_message(self, message: str, ttl: float = 4.0) -> None:
        self.status_message = message
        self.status_message_expires = time.time() + ttl

    def get_status_text(self) -> FormattedText:
        items = self.filtered_tasks
        total = len(items)
        ok = sum(1 for t in items if t.status == Status.OK)
        warn = sum(1 for t in items if t.status == Status.WARN)
        fail = sum(1 for t in items if t.status == Status.FAIL)
        ctx = self.domain_filter or derive_domain_explicit("", self.phase_filter, self.component_filter) or "."
        filter_labels = {
            "OK": "ГОТОВЫ",
            "WARN": "В РАБОТЕ",
            "FAIL": "ЗАБЛОКИРОВАНЫ",
        }
        flt = self.current_filter.value[0] if self.current_filter else "ALL"
        flt_display = filter_labels.get(flt, "ВСЕ") if flt != "ALL" else "ВСЕ"

        parts = [
            ("class:text.dim", f" {total} задач | Фильтр: "),
            ("class:header", f"{flt_display}"),
            ("class:text.dim", f" | Контекст: {ctx} | "),
            ("class:icon.check", f"ГОТОВО={ok} "),
            ("class:icon.warn", f"В РАБОТЕ={warn} "),
            ("class:icon.fail", f"БЛОКЕР={fail}"),
        ]
        if self.status_message and time.time() < self.status_message_expires:
            parts.extend([
                ("class:text.dim", " | "),
                ("class:header", self.status_message[:80]),
            ])
        elif self.status_message:
            self.status_message = ""
        return FormattedText(parts)

    def _current_description_snippet(self) -> str:
        detail = self._current_task_detail_obj()
        if not detail:
            return ""
        text = detail.description or detail.context or ""
        text = text.strip()
        if not text:
            return ""
        return ' '.join(text.split())

    def _format_cell(self, content: str, width: int, align: str = 'left') -> str:
        """Форматирует содержимое ячейки с заданной шириной"""
        text = content[:width] if len(content) > width else content
        if align == 'right':
            return text.rjust(width)
        if align == 'center':
            return text.center(width)
        return text.ljust(width)

    def _current_task_detail_obj(self) -> Optional[TaskDetail]:
        if self.detail_mode and self.current_task_detail:
            return self.current_task_detail
        if self.filtered_tasks:
            task = self.filtered_tasks[self.selected_index]
            detail = task.detail
            if (not detail) and task.task_file:
                try:
                    detail = TaskFileParser.parse(Path(task.task_file))
                except Exception:
                    detail = None
            return detail
        return None

    def _get_status_info(self, task: Task) -> Tuple[str, str, str]:
        """Возвращает символ статуса, CSS класс и короткое название"""
        status_char = task.status.value[0].lower()
        if status_char == 'ok':
            return '◉', 'class:icon.check', '[OK]'
        elif status_char == 'warn':
            return '◉', 'class:icon.warn', '[~]'
        elif status_char == 'fail':
            return '◎', 'class:icon.fail', '[X]'
        else:
            return '○', 'class:status.unknown', '?'

    def _apply_scroll(self, text: str) -> str:
        """Применяет горизонтальную прокрутку к тексту"""
        if self.horizontal_offset > 0 and len(text) > self.horizontal_offset:
            return text[self.horizontal_offset:]
        return text if self.horizontal_offset == 0 else ""

    def get_task_list_text(self) -> FormattedText:
        if not self.filtered_tasks:
            empty_width = min(80, self.get_terminal_width() - 4)
            return FormattedText([
                ('class:border', '+' + '-' * empty_width + '+\n'),
                ('class:text.dim', '| ' + 'Нет задач'.ljust(empty_width - 2) + ' |\n'),
                ('class:border', '+' + '-' * empty_width + '+'),
            ])

        result: List[Tuple[str, str]] = []
        term_width = self.get_terminal_width()

        # Выбор адаптивного layout
        layout = ResponsiveLayoutManager.select_layout(term_width)
        widths = layout.calculate_widths(term_width)

        if layout.has_column('progress'):
            max_prog = max((len(f"{t.progress}%") for t in self.filtered_tasks), default=4)
            widths['progress'] = max(max_prog, 2)
        if layout.has_column('subtasks'):
            max_sub = 0
            for t in self.filtered_tasks:
                if t.subtasks_count:
                    max_sub = max(max_sub, len(f"{t.subtasks_completed}/{t.subtasks_count}"))
                else:
                    max_sub = max(max_sub, 1)
            widths['subtasks'] = max(max_sub, 3)

        # Построение header line
        header_parts = []
        for col in layout.columns:
            if col in widths:
                header_parts.append('-' * widths[col])
        header_line = '+' + '+'.join(header_parts) + '+'

        # Рендер заголовка таблицы
        result.append(('class:border', header_line + '\n'))
        result.append(('class:border', '|'))

        column_labels = {
            'stat': ('◉', widths.get('stat', 3)),
            'title': ('Задача', widths.get('title', 20)),
            'progress': ('%', widths.get('progress', 4)),
            'subtasks': ('Σ', widths.get('subtasks', 3)),
        }

        header_align = {
            'stat': 'center',
            'progress': 'center',
            'subtasks': 'center',
        }
        for col in layout.columns:
            if col in column_labels:
                label, width = column_labels[col]
                align = header_align.get(col, 'left')
                result.append(('class:header', self._format_cell(label, width, align=align)))
                result.append(('class:border', '|'))

        result.append(('', '\n'))
        result.append(('class:border', header_line + '\n'))

        # Рендер строк задач
        compact_status_mode = len(layout.columns) <= 3

        for idx, task in enumerate(self.filtered_tasks):
            status_text, status_class, _ = self._get_status_info(task)

            # Подготовка данных для колонок
            cell_data = {}

            if 'stat' in layout.columns:
                if compact_status_mode:
                    marker = status_text if status_class != 'class:status.unknown' else '○'
                    stat_width = widths['stat']
                    marker_text = marker.center(stat_width) if stat_width > 1 else marker
                    cell_data['stat'] = (marker_text, status_class)
                else:
                    cell_data['stat'] = (self._format_cell(status_text, widths['stat'], align='center'), status_class)

            if 'title' in layout.columns:
                title_scrolled = self._apply_scroll(task.name)
                cell_data['title'] = (self._format_cell(title_scrolled, widths['title']), 'class:text')

            if 'progress' in layout.columns:
                prog_text = f"{task.progress}%"
                prog_style = 'class:icon.check' if task.progress >= 100 else 'class:text.dim'
                cell_data['progress'] = (self._format_cell(prog_text, widths['progress'], align='center'), prog_style)

            if 'subtasks' in layout.columns:
                if task.subtasks_count:
                    subt_text = f"{task.subtasks_completed}/{task.subtasks_count}"
                else:
                    subt_text = "—"
                cell_data['subtasks'] = (self._format_cell(subt_text, widths['subtasks'], align='center'), 'class:text.dim')

            # Рендер строки
            if idx == self.selected_index:
                # Выделенная строка - все одним цветом
                line_parts = []
                for col in layout.columns:
                    if col in cell_data:
                        line_parts.append(cell_data[col][0])
                line = '|' + '|'.join(line_parts) + '|'
                result.append(('class:selected', line))
            else:
                # Обычная строка
                result.append(('class:border', '|'))
                for col in layout.columns:
                    if col in cell_data:
                        text, css_class = cell_data[col]
                        result.append((css_class, text))
                        result.append(('class:border', '|'))

            result.append(('', '\n'))

        result.append(('class:border', header_line))
        return FormattedText(result)

    def get_side_preview_text(self) -> FormattedText:
        """Описание выбранной задачи (без подзадач) в правой колонке."""
        if not self.filtered_tasks:
            return FormattedText([
                ('class:border', '+------------------------------+\n'),
                ('class:text.dim', '| Нет задач                   |\n'),
                ('class:border', '+------------------------------+'),
            ])
        idx = min(self.selected_index, len(self.filtered_tasks) - 1)
        task = self.filtered_tasks[idx]
        detail = task.detail
        if not detail and task.task_file:
            try:
                detail = TaskFileParser.parse(Path(task.task_file))
            except Exception:
                detail = None
        if not detail:
            return FormattedText([
                ('class:border', '+------------------------------+\n'),
                ('class:text.dim', '| Нет данных                   |\n'),
                ('class:border', '+------------------------------+'),
            ])

        result = []
        result.append(('class:border', '+------------------------------------------+\n'))
        result.append(('class:border', '| '))
        result.append(('class:header', f'{detail.id} '))
        result.append(('class:text.dim', f'| '))

        # Status text
        if detail.status == 'OK':
            result.append(('class:icon.check', 'OK   '))
        elif detail.status == 'WARN':
            result.append(('class:icon.warn', 'WARN '))
        else:
            result.append(('class:icon.fail', 'FAIL '))

        result.append(('class:text.dim', f'| {detail.priority}'))
        result.append(('class:border', '                   |\n'))
        result.append(('class:border', '+------------------------------------------+\n'))

        # Title
        title_lines = [detail.title[i:i+38] for i in range(0, len(detail.title), 38)]
        for tline in title_lines:
            result.append(('class:border', '| '))
            result.append(('class:text', tline.ljust(40)))
            result.append(('class:border', ' |\n'))

        # Context
        ctx = detail.domain or detail.phase or detail.component
        if ctx:
            result.append(('class:border', '| '))
            result.append(('class:text.dim', f'Контекст: {ctx[:32]}'.ljust(40)))
            result.append(('class:border', ' |\n'))

        # Progress with simple ASCII bar
        prog = detail.calculate_progress()
        bar_width = 30
        filled = int(prog * bar_width / 100)
        bar = '#' * filled + '-' * (bar_width - filled)
        result.append(('class:border', '| '))
        result.append(('class:text.dim', f'{prog:3d}% ['))
        result.append(('class:text.dim', bar[:30]))
        result.append(('class:text.dim', ']'))
        result.append(('class:border', '    |\n'))

        # Description
        if detail.description:
            result.append(('class:border', '+------------------------------------------+\n'))
            desc_lines = detail.description.split('\n')
            for dline in desc_lines[:5]:  # Max 5 lines
                chunks = [dline[i:i+38] for i in range(0, len(dline), 38)]
                for chunk in chunks[:3]:  # Max 3 chunks per line
                    result.append(('class:border', '| '))
                    result.append(('class:text', chunk.ljust(40)))
                    result.append(('class:border', ' |\n'))

        result.append(('class:border', '+------------------------------------------+'))
        return FormattedText(result)

    # -------- detail view (full card in left pane) --------
    def get_detail_text(self) -> FormattedText:
        if not self.current_task_detail:
            return FormattedText([("class:text.dim", "Задача не выбрана")])

        detail = self.current_task_detail
        result = []

        # Get terminal width and calculate adaptive content width
        term_width = self.get_terminal_width()
        # Адаптивная ширина: используем 90-95% ширины терминала
        # Минимум 40, максимум не ограничен для больших экранов
        if term_width < 60:
            content_width = max(40, term_width - 4)  # Маленький экран - минимальный отступ
        elif term_width < 100:
            content_width = term_width - 8  # Средний экран - умеренный отступ
        else:
            # Большой экран - используем 92% ширины, но не более 160 символов для читаемости
            content_width = min(int(term_width * 0.92), 160)

        # Header
        result.append(('class:border', '+' + '='*content_width + '+\n'))
        result.append(('class:border', '| '))
        result.append(('class:header', f'{detail.id} '))
        result.append(('class:text.dim', '| '))

        # Status with color
        status_map = {
            'OK': ('class:icon.check', 'ГОТОВО'),
            'WARN': ('class:icon.warn', 'В РАБОТЕ'),
            'FAIL': ('class:icon.fail', 'БЛОКЕР'),
        }
        status_style, status_label = status_map.get(detail.status, ('class:icon.fail', detail.status))
        result.append((status_style, status_label.ljust(10)))
        result.append(('class:text.dim', f'| Приоритет: {detail.priority:<7}'))
        result.append(('class:text.dim', f'| Прогресс: {detail.calculate_progress():>3}%'))
        padding_needed = content_width - 2 - (len(detail.id) + 3 + len(status_label) + 23)
        if padding_needed > 0:
            result.append(('class:text.dim', ' ' * padding_needed))
        result.append(('class:border', ' |\n'))
        result.append(('class:border', '+' + '='*content_width + '+\n'))

        # Title - wrap if needed, apply horizontal scroll
        title_display = detail.title
        if self.horizontal_offset > 0:
            title_display = title_display[self.horizontal_offset:] if len(title_display) > self.horizontal_offset else ""

        title_text = f'ЗАГОЛОВОК: {title_display}'
        if len(title_text) > content_width:
            # Wrap title
            result.append(('class:border', '| '))
            result.append(('class:header', 'ЗАГОЛОВОК:'.ljust(content_width - 2)))
            result.append(('class:border', ' |\n'))

            title_lines = [title_display[i:i+content_width-4] for i in range(0, len(title_display), content_width-4)]
            for tline in title_lines:
                result.append(('class:border', '| '))
                result.append(('class:text', f'  {tline}'.ljust(content_width - 2)))
                result.append(('class:border', ' |\n'))
        else:
            result.append(('class:border', '| '))
            result.append(('class:text', title_text.ljust(content_width - 2)))
            result.append(('class:border', ' |\n'))
        result.append(('class:border', '+' + '-'*content_width + '+\n'))

        # Metadata
        if detail.domain or detail.phase or detail.component:
            if detail.domain:
                result.append(('class:border', '| '))
                result.append(('class:text.dim', f'Папка: {detail.domain}'[:content_width-2].ljust(content_width - 2)))
                result.append(('class:border', ' |\n'))
            if detail.phase:
                result.append(('class:border', '| '))
                result.append(('class:text.dim', f'Фаза: {detail.phase}'[:content_width-2].ljust(content_width - 2)))
                result.append(('class:border', ' |\n'))
            if detail.component:
                result.append(('class:border', '| '))
                result.append(('class:text.dim', f'Компонент: {detail.component}'[:content_width-2].ljust(content_width - 2)))
                result.append(('class:border', ' |\n'))
            result.append(('class:border', '+' + '-'*content_width + '+\n'))

        if detail.tags:
            result.append(('class:border', '| '))
            tags_text = f'Теги: {", ".join(detail.tags)}'[:content_width-2]
            result.append(('class:text.dim', tags_text.ljust(content_width - 2)))
            result.append(('class:border', ' |\n'))
            result.append(('class:border', '+' + '-'*content_width + '+\n'))

        if detail.parent:
            result.append(('class:border', '| '))
            result.append(('class:text.dim', f'Родитель: {detail.parent}'[:content_width-2].ljust(content_width - 2)))
            result.append(('class:border', ' |\n'))
            result.append(('class:border', '+' + '-'*content_width + '+\n'))

        # Description with horizontal scroll
        if detail.description:
            result.append(('class:border', '| '))
            result.append(('class:header', 'ОПИСАНИЕ:'.ljust(content_width - 2)))
            result.append(('class:border', ' |\n'))
            desc_lines = detail.description.split('\n')
            for dline in desc_lines:
                # Apply horizontal scroll to each line
                if self.horizontal_offset > 0:
                    dline = dline[self.horizontal_offset:] if len(dline) > self.horizontal_offset else ""
                chunks = [dline[i:i+content_width-4] for i in range(0, len(dline), content_width-4)]
                if not chunks:
                    chunks = ['']
                for chunk in chunks:
                    result.append(('class:border', '| '))
                    result.append(('class:text', f'  {chunk}'.ljust(content_width - 2)))
                    result.append(('class:border', ' |\n'))
            result.append(('class:border', '+' + '-'*content_width + '+\n'))

        # Subtasks with horizontal scroll
        if detail.subtasks:
            completed = sum(1 for st in detail.subtasks if st.completed)
            result.append(('class:border', '| '))
            header = f'ПОДЗАДАЧИ ({completed}/{len(detail.subtasks)} завершено):'
            result.append(('class:header', header.ljust(content_width - 2)))
            result.append(('class:border', ' |\n'))
            for i, st in enumerate(detail.subtasks, 1):
                status_mark = '[OK]' if st.completed else '[  ]'
                status_class = 'class:icon.check' if st.completed else 'class:text.dim'
                pointer = '>' if (i - 1) == self.detail_selected_index else ' '
                prefix = f'{pointer} {i}. {status_mark} '
                title_width = content_width - 2 - len(prefix)

                # Apply horizontal scroll to subtask title
                st_title = st.title
                if self.horizontal_offset > 0:
                    st_title = st_title[self.horizontal_offset:] if len(st_title) > self.horizontal_offset else ""

                result.append(('class:border', '| '))
                result.append((status_class, prefix))
                flags = subtask_flags(st)
                glyphs = [
                    ('class:icon.check', '✓') if flags['criteria'] else ('class:text.dim', '·'),
                    ('class:icon.check', '✓') if flags['tests'] else ('class:text.dim', '·'),
                    ('class:icon.check', '✓') if flags['blockers'] else ('class:text.dim', '·'),
                ]
                flag_text = []
                for idx, (cls, symbol) in enumerate(glyphs):
                    flag_text.append((cls, symbol))
                    if idx < 2:
                        flag_text.append(('class:text.dim', ' '))
                flag_width = len('[✓ ✓ ✓]')
                title_width = max(5, content_width - 2 - len(prefix) - flag_width)

                if (i - 1) == self.detail_selected_index:
                    result.append(('class:selected', st_title[:title_width].ljust(title_width)))
                else:
                    result.append(('class:text', st_title[:title_width].ljust(title_width)))

                result.append(('class:text.dim', ' ['))
                for frag in flag_text:
                    result.append(frag)
                result.append(('class:text.dim', ']'))
                result.append(('class:border', ' |\n'))
            result.append(('class:border', '+' + '-'*content_width + '+\n'))

        # Next steps with horizontal scroll
        if detail.next_steps:
            result.append(('class:border', '| '))
            result.append(('class:header', 'СЛЕДУЮЩИЕ ШАГИ:'.ljust(content_width - 2)))
            result.append(('class:border', ' |\n'))
            for step in detail.next_steps:
                if self.horizontal_offset > 0:
                    step = step[self.horizontal_offset:] if len(step) > self.horizontal_offset else ""
                step_text = f'  - {step}'[:content_width-2]
                result.append(('class:border', '| '))
                result.append(('class:text', step_text.ljust(content_width - 2)))
                result.append(('class:border', ' |\n'))
            result.append(('class:border', '+' + '-'*content_width + '+\n'))

        # Dependencies with horizontal scroll
        if detail.dependencies:
            result.append(('class:border', '| '))
            result.append(('class:header', 'ЗАВИСИМОСТИ:'.ljust(content_width - 2)))
            result.append(('class:border', ' |\n'))
            for dep in detail.dependencies:
                if self.horizontal_offset > 0:
                    dep = dep[self.horizontal_offset:] if len(dep) > self.horizontal_offset else ""
                dep_text = f'  - {dep}'[:content_width-2]
                result.append(('class:border', '| '))
                result.append(('class:text', dep_text.ljust(content_width - 2)))
                result.append(('class:border', ' |\n'))
            result.append(('class:border', '+' + '-'*content_width + '+\n'))

        # Success criteria with horizontal scroll
        if detail.success_criteria:
            result.append(('class:border', '| '))
            result.append(('class:header', 'КРИТЕРИИ УСПЕХА:'.ljust(content_width - 2)))
            result.append(('class:border', ' |\n'))
            for sc in detail.success_criteria:
                if self.horizontal_offset > 0:
                    sc = sc[self.horizontal_offset:] if len(sc) > self.horizontal_offset else ""
                sc_text = f'  - {sc}'[:content_width-2]
                result.append(('class:border', '| '))
                result.append(('class:text', sc_text.ljust(content_width - 2)))
                result.append(('class:border', ' |\n'))
            result.append(('class:border', '+' + '-'*content_width + '+\n'))

        # Проблемы
        if detail.problems:
            result.append(('class:border', '| '))
            result.append(('class:icon.fail', 'PROBLEMS:'.ljust(content_width - 2)))
            result.append(('class:border', ' |\n'))
            for prob in detail.problems:
                if self.horizontal_offset > 0:
                    prob = prob[self.horizontal_offset:] if len(prob) > self.horizontal_offset else ""
                prob_text = f'  ! {prob}'[:content_width-2]
                result.append(('class:border', '| '))
                result.append(('class:icon.fail', prob_text.ljust(content_width - 2)))
                result.append(('class:border', ' |\n'))
            result.append(('class:border', '+' + '-'*content_width + '+\n'))

        # Risks with horizontal scroll
        if detail.risks:
            result.append(('class:border', '| '))
            result.append(('class:icon.warn', 'RISKS:'.ljust(content_width - 2)))
            result.append(('class:border', ' |\n'))
            for risk in detail.risks:
                if self.horizontal_offset > 0:
                    risk = risk[self.horizontal_offset:] if len(risk) > self.horizontal_offset else ""
                risk_text = f'  - {risk}'[:content_width-2]
                result.append(('class:border', '| '))
                result.append(('class:text', risk_text.ljust(content_width - 2)))
                result.append(('class:border', ' |\n'))
            result.append(('class:border', '+' + '-'*content_width + '+\n'))

        # Footer
        result.append(('class:border', '+' + '='*content_width + '+'))

        return FormattedText(result)

    def get_detail_items_count(self) -> int:
        if not self.current_task_detail:
            return 0
        return len(self.current_task_detail.subtasks) + len(self.current_task_detail.next_steps) + len(self.current_task_detail.dependencies)

    def show_task_details(self, task: Task):
        self.current_task = task
        self.current_task_detail = task.detail or TaskFileParser.parse(Path(task.task_file))
        self.detail_mode = True
        self.detail_selected_index = 0

    def show_subtask_details(self, subtask: SubTask, index: int):
        """Render a focused view for a single subtask with full details."""
        term_width = self.get_terminal_width()
        # Используем ту же логику, что и в get_detail_text
        if term_width < 60:
            content_width = max(40, term_width - 4)
        elif term_width < 100:
            content_width = term_width - 8
        else:
            content_width = min(int(term_width * 0.92), 160)

        lines: List[Tuple[str, str]] = []
        lines.append(('class:border', '+' + '='*content_width + '+\n'))

        # Header
        status_icon = '[X]' if subtask.completed else '[ ]'
        header = f"SUBTASK {index+1} {status_icon}"
        lines.append(('class:border', '| '))
        lines.append(('class:header', header.ljust(content_width - 2)))
        lines.append(('class:border', ' |\n'))
        lines.append(('class:border', '+' + '-'*content_width + '+\n'))

        # Title
        text = subtask.title
        chunks = [text[i:i+content_width-4] for i in range(0, len(text), content_width-4)] or ['']
        for ch in chunks:
            lines.append(('class:border', '| '))
            lines.append(('class:text', ch.ljust(content_width - 2)))
            lines.append(('class:border', ' |\n'))

        # Checkpoint summary
        lines.append(('class:border', '+' + '-'*content_width + '+\n'))
        def checkpoint_summary(label: str, confirmed: bool) -> str:
            icon = '[OK]' if confirmed else '[  ]'
            return f"{icon} {label}"

        summary = " | ".join([
            checkpoint_summary("Критерии", subtask.criteria_confirmed),
            checkpoint_summary("Тесты", subtask.tests_confirmed),
            checkpoint_summary("Блокеры", subtask.blockers_resolved),
        ])
        lines.append(('class:border', '| '))
        lines.append(('class:label', summary[:content_width - 2].ljust(content_width - 2)))
        lines.append(('class:border', ' |\n'))

        def add_section_header(label: str, confirmed: bool):
            icon = '[OK]' if confirmed else '[  ]'
            style = 'class:status.ok' if confirmed else 'class:status.fail'
            header = f"{icon} {label}"
            lines.append(('class:border', '+' + '-'*content_width + '+\n'))
            lines.append(('class:border', '| '))
            lines.append((style, header.ljust(content_width - 2)))
            lines.append(('class:border', ' |\n'))

        # Критерии выполнения
        if subtask.success_criteria:
            add_section_header("Критерии выполнения", subtask.criteria_confirmed)
            for i, criterion in enumerate(subtask.success_criteria, 1):
                text = f"  {i}. {criterion}"
                chunks = [text[i:i+content_width-4] for i in range(0, len(text), content_width-4)] or ['']
                for ch in chunks:
                    lines.append(('class:border', '| '))
                    lines.append(('class:text', ch.ljust(content_width - 2)))
                    lines.append(('class:border', ' |\n'))

        # Тесты
        if subtask.tests:
            add_section_header("Тесты", subtask.tests_confirmed)
            for i, test in enumerate(subtask.tests, 1):
                text = f"  {i}. {test}"
                chunks = [text[i:i+content_width-4] for i in range(0, len(text), content_width-4)] or ['']
                for ch in chunks:
                    lines.append(('class:border', '| '))
                    lines.append(('class:text', ch.ljust(content_width - 2)))
                    lines.append(('class:border', ' |\n'))

        # Блокеры
        if subtask.blockers:
            add_section_header("Блокеры", subtask.blockers_resolved)
            for i, blocker in enumerate(subtask.blockers, 1):
                text = f"  {i}. {blocker}"
                chunks = [text[i:i+content_width-4] for i in range(0, len(text), content_width-4)] or ['']
                for ch in chunks:
                    lines.append(('class:border', '| '))
                    lines.append(('class:text', ch.ljust(content_width - 2)))
                    lines.append(('class:border', ' |\n'))

        # Evidence logs
        def append_logs(label: str, entries: List[str]):
            if not entries:
                return
            lines.append(('class:border', '+' + '-'*content_width + '+\n'))
            lines.append(('class:border', '| '))
            lines.append(('class:label', f"{label} — отметки:".ljust(content_width - 2)))
            lines.append(('class:border', ' |\n'))
            for entry in entries:
                chunks = [entry[i:i+content_width-4] for i in range(0, len(entry), content_width-4)] or ['']
                for ch in chunks:
                    lines.append(('class:border', '| '))
                    lines.append(('class:text', f"  - {ch}".ljust(content_width - 2)))
                    lines.append(('class:border', ' |\n'))

        append_logs("Критерии", subtask.criteria_notes)
        append_logs("Тесты", subtask.tests_notes)
        append_logs("Блокеры", subtask.blockers_notes)

        lines.append(('class:border', '+' + '='*content_width + '+'))

        self.single_subtask_view = FormattedText(lines)

    def delete_current_item(self):
        """Удалить текущий выбранный элемент (задачу или подзадачу)"""
        if self.detail_mode and self.current_task_detail:
            # В режиме деталей - удаляем подзадачу
            if self.detail_selected_index < len(self.current_task_detail.subtasks):
                subtask = self.current_task_detail.subtasks[self.detail_selected_index]
                # Подтверждение не требуется в TUI - просто удаляем
                del self.current_task_detail.subtasks[self.detail_selected_index]
                self.manager.save_task(self.current_task_detail)
                # Корректируем индекс
                if self.detail_selected_index >= len(self.current_task_detail.subtasks):
                    self.detail_selected_index = max(0, len(self.current_task_detail.subtasks) - 1)
                # Обновляем кеш
                if self.current_task_detail.id in self.task_details_cache:
                    self.task_details_cache[self.current_task_detail.id] = self.current_task_detail
                self.load_tasks(preserve_selection=True)
        else:
            # В списке задач - удаляем задачу
            if self.filtered_tasks:
                task = self.filtered_tasks[self.selected_index]
                # Удаляем файл задачи
                task_file = Path(task.task_file)
                if task_file.exists():
                    task_file.unlink()
                # Корректируем индекс
                if self.selected_index >= len(self.filtered_tasks) - 1:
                    self.selected_index = max(0, len(self.filtered_tasks) - 2)
                self.load_tasks(preserve_selection=False)

    def toggle_subtask_completion(self):
        """Переключить состояние выполнения подзадачи"""
        if self.detail_mode and self.current_task_detail:
            if self.detail_selected_index < len(self.current_task_detail.subtasks):
                desired = not self.current_task_detail.subtasks[self.detail_selected_index].completed
                domain = self.current_task_detail.domain
                ok, msg = self.manager.set_subtask(self.current_task_detail.id, self.detail_selected_index, desired, domain)
                if not ok:
                    self.set_status_message(msg or "Чекпоинты не подтверждены")
                    return
                updated = self.manager.load_task(self.current_task_detail.id, domain)
                if updated:
                    self.current_task_detail = updated
                    self.task_details_cache[self.current_task_detail.id] = updated
                self.load_tasks(preserve_selection=True)

    def start_editing(self, context: str, current_value: str, index: Optional[int] = None):
        """Начать редактирование текста"""
        self.editing_mode = True
        self.edit_context = context
        self.edit_index = index
        self.edit_buffer.text = current_value
        self.edit_buffer.cursor_position = len(current_value)

    def save_edit(self):
        """Сохранить результат редактирования"""
        if not self.editing_mode:
            return

        new_value = self.edit_buffer.text.strip()
        if not new_value:
            # Пустое значение - отменяем
            self.cancel_edit()
            return

        context = self.edit_context
        task = self.current_task_detail

        if context == 'task_title' and task:
            task.title = new_value
            self.manager.save_task(task)
        elif context == 'task_description' and task:
            task.description = new_value
            self.manager.save_task(task)
        elif context == 'subtask_title' and task and self.edit_index is not None:
            if self.edit_index < len(task.subtasks):
                task.subtasks[self.edit_index].title = new_value
                self.manager.save_task(task)
        elif context == 'criterion' and task and self.edit_index is not None:
            if self.detail_selected_index < len(task.subtasks):
                st = task.subtasks[self.detail_selected_index]
                if self.edit_index < len(st.success_criteria):
                    st.success_criteria[self.edit_index] = new_value
                    self.manager.save_task(task)
        elif context == 'test' and task and self.edit_index is not None:
            if self.detail_selected_index < len(task.subtasks):
                st = task.subtasks[self.detail_selected_index]
                if self.edit_index < len(st.tests):
                    st.tests[self.edit_index] = new_value
                    self.manager.save_task(task)
        elif context == 'blocker' and task and self.edit_index is not None:
            if self.detail_selected_index < len(task.subtasks):
                st = task.subtasks[self.detail_selected_index]
                if self.edit_index < len(st.blockers):
                    st.blockers[self.edit_index] = new_value
                    self.manager.save_task(task)

        # Обновляем кеш
        if task and task.id in self.task_details_cache:
            self.task_details_cache[task.id] = task

        self.load_tasks(preserve_selection=True)
        self.cancel_edit()

    def cancel_edit(self):
        """Отменить редактирование"""
        self.editing_mode = False
        self.edit_context = None
        self.edit_index = None
        self.edit_buffer.text = ''

    def edit_current_item(self):
        """Редактировать текущий элемент"""
        if self.detail_mode and self.current_task_detail:
            # В режиме просмотра подзадачи
            if hasattr(self, "single_subtask_view") and self.single_subtask_view:
                # Редактируем название подзадачи
                if self.detail_selected_index < len(self.current_task_detail.subtasks):
                    st = self.current_task_detail.subtasks[self.detail_selected_index]
                    self.start_editing('subtask_title', st.title, self.detail_selected_index)
            else:
                # В списке подзадач - редактируем название подзадачи
                if self.detail_selected_index < len(self.current_task_detail.subtasks):
                    st = self.current_task_detail.subtasks[self.detail_selected_index]
                    self.start_editing('subtask_title', st.title, self.detail_selected_index)
        else:
            # В списке задач - редактируем название задачи
            if self.filtered_tasks:
                task = self.filtered_tasks[self.selected_index]
                task_detail = task.detail or TaskFileParser.parse(Path(task.task_file))
                self.current_task_detail = task_detail
                self.start_editing('task_title', task_detail.title)

    def get_footer_text(self) -> FormattedText:
        scroll_info = f" | Смещение: {self.horizontal_offset}" if self.horizontal_offset > 0 else ""
        if getattr(self, 'help_visible', False):
            return FormattedText([
                ("class:text.dimmer", " q — выход | r — обновить | Enter — детали | d — завершить | e — редактировать"),
                ("", "\n"),
                ("class:text.dim", "  Чекпоинты: [✓ ✓ ·] = критерии / тесты / блокеры | ? — скрыть подсказку"),
            ])
        if self.editing_mode:
            return FormattedText([
                ("class:text.dimmer", " Enter: сохранить | Esc: отменить"),
            ])
        desc = self._current_description_snippet() or "Описание отсутствует"
        detail = self._current_task_detail_obj()
        segments: List[str] = []
        seen: set[str] = set()
        if detail:
            domain = detail.domain or ""
            if domain:
                for part in domain.split('/'):
                    if part:
                        formatted = f"[{part}]"
                        if formatted not in seen:
                            segments.append(formatted)
                            seen.add(formatted)
            for comp in (detail.phase, detail.component):
                if comp:
                    formatted = f"[{comp}]"
                    if formatted not in seen:
                        segments.append(formatted)
                        seen.add(formatted)
        path_display: List[Tuple[str, str]] = []
        if segments:
            for idx, seg in enumerate(segments):
                style = 'class:header' if idx == len(segments) - 1 else 'class:text.dim'
                path_display.append((style, seg))
                if idx < len(segments) - 1:
                    path_display.append(('class:text.dim', '->'))
        else:
            path_display = [('class:text.dim', '-')]
        width = max(20, self.get_terminal_width() - 4)
        lines = []
        current = desc
        while current:
            lines.append(current[:width])
            current = current[width:]
            if len(lines) == 2:
                break
        parts: List[Tuple[str, str]] = []
        parts.append(("class:text.dim", " Домен: "))
        parts.extend(path_display)
        desc_header = " Описание: "
        if lines:
            parts.extend([("", "\n"), ("class:text.dim", desc_header), ("class:text", lines[0])])
            if len(lines) > 1:
                parts.extend([("", "\n"), ("class:text", " " * len(desc_header) + lines[1])])
        else:
            parts.extend([("", "\n"), ("class:text.dim", desc_header), ("class:text", "—")])
        legend = " ◉=OK/В работе | ◎=Блокер | %=прогресс | Σ=подзадачи | ?=подсказки"
        parts.extend([("", "\n"), ("class:text.dimmer", legend + scroll_info)])
        return FormattedText(parts)

    def get_edit_dialog(self) -> FormattedText:
        """Показать диалог редактирования"""
        context_labels = {
            'task_title': 'Редактирование названия задачи',
            'task_description': 'Редактирование описания задачи',
            'subtask_title': 'Редактирование подзадачи',
            'criterion': 'Редактирование критерия',
            'test': 'Редактирование теста',
            'blocker': 'Редактирование блокера',
        }
        label = context_labels.get(self.edit_context, 'Редактирование')

        lines = []
        lines.append(('class:border', '+' + '='*60 + '+\n'))
        lines.append(('class:border', '| '))
        lines.append(('class:header', label.ljust(58)))
        lines.append(('class:border', ' |\n'))
        lines.append(('class:border', '+' + '-'*60 + '+\n'))
        lines.append(('class:border', '| '))
        lines.append(('class:text', self.edit_buffer.text[:58].ljust(58)))
        lines.append(('class:border', ' |\n'))
        lines.append(('class:border', '+' + '='*60 + '+'))

        return FormattedText(lines)

    def get_body_content(self) -> FormattedText:
        """Returns content for main body - either task list or detail view."""
        if self.editing_mode:
            # Показываем диалог редактирования
            return self.get_edit_dialog()
        if self.detail_mode and self.current_task_detail:
            # Если открыт просмотр конкретной подзадачи — показываем его
            if hasattr(self, "single_subtask_view") and self.single_subtask_view:
                return self.single_subtask_view
            return self.get_detail_text()
        self.maybe_reload()

        # Normal mode: show task list with side preview
        # Simple approach: just show task list, preview is handled separately if needed
        # For now, just return task list
        return self.get_task_list_text()

    def get_content_text(self) -> FormattedText:
        """Deprecated - use get_body_content instead."""
        return self.get_body_content()

    def run(self):
        self.app.run()


# ============================================================================
# COMMAND IMPLEMENTATIONS
# ============================================================================


def cmd_tui(args) -> int:
    tui = TaskTrackerTUI(Path(".tasks"), theme=getattr(args, "theme", DEFAULT_THEME))
    tui.run()
    return 0


def cmd_list(args) -> int:
    manager = TaskManager()
    domain = derive_domain_explicit(args.domain, getattr(args, "phase", None), getattr(args, "component", None))
    tasks = manager.list_tasks(domain)
    if args.status:
        tasks = [t for t in tasks if t.status == args.status]
    if args.component:
        tasks = [t for t in tasks if t.component == args.component]
    if args.phase:
        tasks = [t for t in tasks if t.phase == args.phase]
    payload = {
        "total": len(tasks),
        "filters": {
            "domain": domain or "",
            "phase": args.phase or "",
            "component": args.component or "",
            "status": args.status or "",
            "progress_details": bool(args.progress),
        },
        "tasks": [
            task_to_dict(t, include_subtasks=bool(args.progress))
            for t in tasks
        ],
    }
    return structured_response(
        "list",
        status="OK",
        message="Список задач сформирован",
        payload=payload,
        summary=f"{len(tasks)} задач",
    )


def cmd_show(args) -> int:
    last_id, last_domain = get_last_task()
    task_id = normalize_task_id(args.task_id) if args.task_id else last_id
    if not task_id:
        return structured_error("show", "Нет задачи для показа")
    manager = TaskManager()
    domain = derive_domain_explicit(args.domain, getattr(args, "phase", None), getattr(args, "component", None)) or last_domain or ""
    task = manager.load_task(task_id, domain)
    if not task:
        return structured_error("show", f"Задача {task_id} не найдена")
    save_last_task(task.id, task.domain)
    payload = {"task": task_to_dict(task, include_subtasks=True)}
    if task.subtasks:
        payload["subtasks_completed"] = sum(1 for st in task.subtasks if st.completed)
    return structured_response(
        "show",
        status=task.status,
        message="Детали задачи",
        payload=payload,
        summary=f"{task.id}: {task.title}",
    )


def cmd_create(args) -> int:
    manager = TaskManager()
    args.parent = normalize_task_id(args.parent)
    domain = derive_domain_explicit(args.domain, args.phase, args.component)

    def fail(message: str, payload: Optional[Dict[str, Any]] = None) -> int:
        if getattr(args, "validate_only", False):
            return validation_response("create", False, message, payload)
        return structured_error("create", message, payload=payload)

    def success_preview(task: TaskDetail, message: str = "Валидация пройдена") -> int:
        task_snapshot = task_to_dict(task, include_subtasks=True)
        payload = {"task": task_snapshot}
        return validation_response("create", True, message, payload)

    task = manager.create_task(
        args.title,
        status=args.status,
        priority=args.priority,
        parent=args.parent,
        domain=domain,
        phase=args.phase or "",
        component=args.component or "",
    )
    if not args.description or not args.description.strip() or args.description.strip().upper() == "TBD":
        return fail("Описание обязательно и не может быть пустым/TBD")
    task.description = args.description.strip()
    task.context = args.context or ""
    if args.tags:
        task.tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    if args.subtasks:
        try:
            subtasks_payload = _load_subtasks_source(args.subtasks)
            task.subtasks = parse_subtasks_flexible(subtasks_payload)
        except SubtaskParseError as e:
            return fail(f"Ошибка парсинга подзадач: {e}")
    if args.dependencies:
        for dep in args.dependencies.split(","):
            dep = dep.strip()
            if dep:
                task.dependencies.append(dep)
    if args.next_steps:
        for step in args.next_steps.split(";"):
            if step.strip():
                task.next_steps.append(step.strip())
    if args.tests:
        for t in args.tests.split(";"):
            if t.strip():
                task.success_criteria.append(t.strip())
    if not task.success_criteria:
        return fail("Укажи тесты/критерии успеха через --tests")
    if args.risks:
        for r in args.risks.split(";"):
            if r.strip():
                task.risks.append(r.strip())
    if not task.risks:
        return fail("Добавь риски через --risks (например: 'dep outage;perf regression')")

    # Flagship-валидация подзадач
    flagship_ok, flagship_issues = validate_flagship_subtasks(task.subtasks)
    if not flagship_ok:
        payload = {
            "issues": flagship_issues,
            "requirements": [
                "Минимум 3 подзадачи",
                "Каждая подзадача >=20 символов",
                "Explicit success criteria/tests/blockers",
                "Атомарные действия без 'и затем'",
            ],
        }
        return fail("Подзадачи не соответствуют flagship-качеству", payload=payload)

    task.update_status_from_progress()
    if getattr(args, "validate_only", False):
        return success_preview(task)
    manager.save_task(task)
    save_last_task(task.id, task.domain)
    payload = {"task": task_to_dict(task, include_subtasks=True)}
    return structured_response(
        "create",
        status="OK",
        message=f"Задача {task.id} создана",
        payload=payload,
        summary=f"{task.id}: {task.title}",
    )


def cmd_smart_create(args) -> int:
    if not args.parent:
        return structured_error("task", "Укажи parent: --parent TASK-XXX")
    manager = TaskManager()
    title, auto_tags, auto_deps = parse_smart_title(args.title)
    args.parent = normalize_task_id(args.parent)

    def fail(message: str, payload: Optional[Dict[str, Any]] = None) -> int:
        if getattr(args, "validate_only", False):
            return validation_response("task", False, message, payload)
        return structured_error("task", message, payload=payload)

    def success_preview(task: TaskDetail, message: str = "Валидация пройдена") -> int:
        payload = {"task": task_to_dict(task, include_subtasks=True)}
        return validation_response("task", True, message, payload)

    domain = derive_domain_explicit(args.domain, args.phase, args.component)
    task = manager.create_task(
        title,
        status=args.status,
        priority=args.priority,
        parent=args.parent,
        domain=domain,
        phase=args.phase or "",
        component=args.component or "",
    )
    if not args.description or not args.description.strip() or args.description.strip().upper() == "TBD":
        return fail("Описание обязательно и не может быть пустым/TBD")
    task.description = args.description.strip()
    task.context = args.context or ""
    task.tags = [t.strip() for t in args.tags.split(",")] if args.tags else auto_tags
    deps = [d.strip() for d in args.dependencies.split(",")] if args.dependencies else auto_deps
    task.dependencies = deps

    template_desc, template_tests = load_template(task.tags[0] if task.tags else "default", manager)
    if not task.description:
        task.description = template_desc
    if args.tests:
        task.success_criteria = [t.strip() for t in args.tests.split(";") if t.strip()]
    elif template_tests:
        task.success_criteria = [template_tests]
    if not task.success_criteria:
        return fail("Укажи тесты/критерии успеха через --tests")
    if args.risks:
        task.risks = [r.strip() for r in args.risks.split(";") if r.strip()]
    if not task.risks:
        return fail("Добавь риски через --risks (например: 'dep outage;perf regression')")

    if args.subtasks:
        try:
            subtasks_payload = _load_subtasks_source(args.subtasks)
            task.subtasks = parse_subtasks_flexible(subtasks_payload)
        except SubtaskParseError as e:
            return fail(f"Ошибка парсинга подзадач: {e}")

    # Flagship-валидация подзадач
    flagship_ok, flagship_issues = validate_flagship_subtasks(task.subtasks)
    if not flagship_ok:
        payload = {
            "issues": flagship_issues,
            "requirements": [
                "Минимум 3 подзадачи",
                "Каждая подзадача >=20 символов",
                "Explicit success criteria/tests/blockers",
                "Атомарные действия без 'и затем'",
            ],
        }
        return fail("Подзадачи не соответствуют flagship-качеству", payload=payload)

    task.update_status_from_progress()
    if getattr(args, "validate_only", False):
        return success_preview(task)
    manager.save_task(task)
    save_last_task(task.id, task.domain)
    payload = {"task": task_to_dict(task, include_subtasks=True)}
    return structured_response(
        "task",
        status="OK",
        message=f"Задача {task.id} создана",
        payload=payload,
        summary=f"{task.id}: {task.title}",
    )


def cmd_create_guided(args) -> int:
    """Полуинтерактивное создание задачи (шаг-ответ-шаг)"""
    if not is_interactive():
        print("[X] Мастер доступен только в интерактивном терминале")
        print("  Используй: apply_task create с параметрами")
        return 1

    print("=" * 60)
    print("[>>] МАСТЕР: Создание задачи flagship-качества")
    print("=" * 60)

    manager = TaskManager()

    # Шаг 1: Базовая информация
    print("\n[DESC] Шаг 1/5: Базовая информация")
    title = prompt_required("Название задачи")
    parent = prompt_required("ID родительской задачи (например: TASK-001)")
    parent = normalize_task_id(parent)
    description = prompt_required("Описание (не TBD)")
    while description.upper() == "TBD":
        print("  [!] Описание не может быть 'TBD'")
        description = prompt_required("Описание")

    # Шаг 2: Контекст и метаданные
    print("\n[TAG]  Шаг 2/5: Контекст и метаданные")
    context = prompt("Дополнительный контекст", default="")
    tags_str = prompt("Теги (через запятую)", default="")
    tags = [t.strip() for t in tags_str.split(",") if t.strip()]

    # Шаг 3: Риски
    print("\n[WARN]  Шаг 3/5: Риски")
    risks = prompt_list("Риски проекта", min_items=1)

    # Шаг 4: Критерии успеха / Тесты
    print("\n[SUB] Шаг 4/5: Критерии успеха и тесты")
    tests = prompt_list("Критерии успеха / Тесты", min_items=1)

    # Шаг 5: Подзадачи
    print("\n[TASK] Шаг 5/5: Подзадачи (минимум 3)")
    subtasks = []
    for i in range(3):
        subtasks.append(prompt_subtask_interactive(i + 1))

    while confirm("\nДобавить ещё подзадачу?", default=False):
        subtasks.append(prompt_subtask_interactive(len(subtasks) + 1))

    # Валидация
    print("\n🔍 Валидация flagship-качества...")
    flagship_ok, flagship_issues = validate_flagship_subtasks(subtasks)
    if not flagship_ok:
        print("[WARN]  Обнаружены проблемы:")
        for idx, issue in enumerate(flagship_issues, 1):
            print(f"  {idx}. {issue}")

        if not confirm("\nПродолжить несмотря на проблемы?", default=False):
            print("[X] Создание отменено")
            return 1

    # Создание задачи
    print("\n[SAVE] Создание задачи...")
    domain = derive_domain_explicit(
        getattr(args, 'domain', None),
        getattr(args, 'phase', None),
        getattr(args, 'component', None)
    )

    task = manager.create_task(
        title,
        status="FAIL",
        priority=getattr(args, 'priority', "MEDIUM"),
        parent=parent,
        domain=domain,
        phase=getattr(args, 'phase', "") or "",
        component=getattr(args, 'component', "") or "",
    )

    task.description = description
    task.context = context
    task.tags = tags
    task.risks = risks
    task.success_criteria = tests
    task.subtasks = subtasks
    task.update_status_from_progress()

    manager.save_task(task)
    save_last_task(task.id, task.domain)

    print("\n" + "=" * 60)
    print(f"[SUB] УСПЕШНО: Создана задача {task.id}")
    print("=" * 60)
    print(f"[TASK] {task.title}")
    print(f"[DEP] Родитель: {task.parent}")
    print(f"[STAT] Подзадач: {len(task.subtasks)}")
    print(f"[SUB] Критериев: {len(task.success_criteria)}")
    print(f"[WARN]  Рисков: {len(task.risks)}")
    print("=" * 60)

    return 0


def cmd_update(args) -> int:
    # Бекст совместимость: допускаем оба порядка аргументов
    status = None
    task_id = None
    last_id, last_domain = get_last_task()
    for candidate in (args.arg1, args.arg2):
        if candidate and candidate.upper() in ("OK", "WARN", "FAIL"):
            status = candidate.upper()
        elif candidate:
            task_id = normalize_task_id(candidate)

    if status is None:
        return structured_error("update", "Укажи статус: OK | WARN | FAIL")

    if task_id is None:
        task_id = last_id
        if not task_id:
            return structured_error("update", "Не указан ID задачи и нет последней")

    manager = TaskManager()
    domain = derive_domain_explicit(args.domain, args.phase, args.component) or last_domain or ""
    ok, error = manager.update_task_status(task_id, status, domain)
    if ok:
        save_last_task(task_id, domain)
        detail = manager.load_task(task_id, domain)
        payload = {"task": task_to_dict(detail, include_subtasks=True) if detail else {"id": task_id}}
        return structured_response(
            "update",
            status=status,
            message=f"Статус {task_id} обновлён",
            payload=payload,
            summary=f"{task_id} → {status}",
        )

    payload = {"task_id": task_id, "domain": domain}
    if error and error.get("code") == "not_found":
        return structured_error("update", error.get("message", "Задача не найдена"), payload=payload)
    return structured_response(
        "update",
        status="ERROR",
        message=(error or {}).get("message", "Статус не обновлён"),
        payload=payload,
        exit_code=1,
    )


def cmd_analyze(args) -> int:
    manager = TaskManager()
    domain = derive_domain_explicit(args.domain, getattr(args, "phase", None), getattr(args, "component", None))
    task = manager.load_task(normalize_task_id(args.task_id), domain)
    if not task:
        return structured_error("analyze", f"Задача {args.task_id} не найдена")
    payload = {
        "task": task_to_dict(task, include_subtasks=True),
        "progress": task.calculate_progress(),
        "subtasks_completed": sum(1 for st in task.subtasks if st.completed),
    }
    if not task.subtasks:
        payload["tip"] = "Добавь подзадачи через apply_task subtask TASK --add ..."
    return structured_response(
        "analyze",
        status=task.status,
        message="Анализ завершён",
        payload=payload,
        summary=f"{task.id}: {task.title}",
    )


def cmd_next(args) -> int:
    manager = TaskManager()
    domain = derive_domain_explicit(args.domain, getattr(args, "phase", None), getattr(args, "component", None))
    tasks = manager.list_tasks(domain)
    candidates = [t for t in tasks if t.status != "OK" and t.calculate_progress() < 100]
    filter_hint = f" (domain='{domain or '-'}', phase='{args.phase or '-'}', component='{args.component or '-'}')"
    if not candidates:
        payload = {
            "filters": {"domain": domain or "", "phase": args.phase or "", "component": args.component or ""},
            "candidates": [],
        }
        return structured_response(
            "next",
            status="OK",
            message="Все задачи завершены" + filter_hint,
            payload=payload,
            summary="Нет незавершённых задач",
        )
    priority_map = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}

    def score(t: TaskDetail):
        blocked = -100 if t.blocked else 0
        return (blocked, -priority_map.get(t.priority, 0), t.calculate_progress())

    candidates.sort(key=score)
    top = candidates[:3]
    save_last_task(candidates[0].id, candidates[0].domain)
    payload = {
        "filters": {"domain": domain or "", "phase": args.phase or "", "component": args.component or ""},
        "candidates": [task_to_dict(t) for t in top],
        "selected": task_to_dict(candidates[0]),
    }
    return structured_response(
        "next",
        status="OK",
        message="Рекомендации обновлены" + filter_hint,
        payload=payload,
        summary=f"Выбрано {candidates[0].id}",
    )


def _parse_semicolon_list(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(";") if item.strip()]


def cmd_add_subtask(args) -> int:
    manager = TaskManager()
    domain = derive_domain_explicit(args.domain, getattr(args, "phase", None), getattr(args, "component", None))
    task_id = normalize_task_id(args.task_id)
    criteria = _parse_semicolon_list(args.criteria)
    tests = _parse_semicolon_list(args.tests)
    blockers = _parse_semicolon_list(args.blockers)
    if not args.subtask or len(args.subtask.strip()) < 20:
        return structured_error("add-subtask", "Подзадача должна содержать как минимум 20 символов с деталями")
    ok, err = manager.add_subtask(task_id, args.subtask.strip(), domain, criteria, tests, blockers)
    if ok:
        payload = {"task_id": task_id, "subtask": args.subtask.strip()}
        return structured_response(
            "add-subtask",
            status="OK",
            message=f"Подзадача добавлена в {task_id}",
            payload=payload,
            summary=f"{task_id} +subtask",
        )
    if err == "missing_fields":
        return structured_error(
            "add-subtask",
            "Добавь критерии/тесты/блокеры: --criteria \"...\" --tests \"...\" --blockers \"...\" (через ';')",
            payload={"task_id": task_id},
        )
    return structured_error("add-subtask", f"Задача {task_id} не найдена", payload={"task_id": task_id})


def cmd_add_dependency(args) -> int:
    manager = TaskManager()
    domain = derive_domain_explicit(args.domain, getattr(args, "phase", None), getattr(args, "component", None))
    task_id = normalize_task_id(args.task_id)
    if manager.add_dependency(task_id, args.dependency, domain):
        payload = {"task_id": task_id, "dependency": args.dependency}
        return structured_response(
            "add-dep",
            status="OK",
            message=f"Зависимость добавлена в {task_id}",
            payload=payload,
            summary=f"{task_id} +dep",
        )
    return structured_error("add-dep", f"Задача {task_id} не найдена", payload={"task_id": task_id})


def cmd_subtask(args) -> int:
    """Управление подзадачами: добавить / отметить выполненной / вернуть"""
    manager = TaskManager()
    task_id = normalize_task_id(args.task_id)
    domain_arg = getattr(args, "domain", "")
    domain = derive_domain_explicit(domain_arg, getattr(args, "phase", None), getattr(args, "component", None))
    actions = [
        ("add", bool(args.add)),
        ("done", args.done is not None),
        ("undo", args.undo is not None),
        ("criteria_done", args.criteria_done is not None),
        ("criteria_undo", args.criteria_undo is not None),
        ("tests_done", args.tests_done is not None),
        ("tests_undo", args.tests_undo is not None),
        ("blockers_done", args.blockers_done is not None),
        ("blockers_undo", args.blockers_undo is not None),
    ]
    active = [name for name, flag in actions if flag]
    if len(active) != 1:
        return structured_error(
            "subtask",
            "Укажи ровно одно действие: --add | --done | --undo | --criteria-done | --tests-done | --blockers-done (и соответствующие --undo)",
            payload={"actions": active},
        )

    action = active[0]

    def _snapshot(index: Optional[int] = None) -> Dict[str, Any]:
        detail = manager.load_task(task_id, domain)
        payload: Dict[str, Any] = {"task_id": task_id}
        if detail:
            payload["task"] = task_to_dict(detail, include_subtasks=True)
            if index is not None and 0 <= index < len(detail.subtasks):
                payload["subtask"] = {"index": index, **subtask_to_dict(detail.subtasks[index])}
        return payload

    if action == "add":
        criteria = _parse_semicolon_list(args.criteria)
        tests = _parse_semicolon_list(args.tests)
        blockers = _parse_semicolon_list(args.blockers)
        if not args.add or len(args.add.strip()) < 20:
            return structured_error("subtask", "Подзадача должна содержать как минимум 20 символов с деталями")
        ok, err = manager.add_subtask(task_id, args.add.strip(), domain, criteria, tests, blockers)
        if ok:
            payload = _snapshot()
            payload["operation"] = "add"
            payload["subtask_title"] = args.add.strip()
            return structured_response(
                "subtask",
                status="OK",
                message=f"Подзадача добавлена в {task_id}",
                payload=payload,
                summary=f"{task_id} +subtask",
            )
        if err == "missing_fields":
            return structured_error(
                "subtask",
                "Добавь критерии/тесты/блокеры: --criteria \"...\" --tests \"...\" --blockers \"...\" (через ';')",
                payload={"task_id": task_id},
            )
        return structured_error("subtask", f"Задача {task_id} не найдена", payload={"task_id": task_id})

    if action == "done":
        ok, msg = manager.set_subtask(task_id, args.done, True, domain)
        if ok:
            payload = _snapshot(args.done)
            payload["operation"] = "done"
            return structured_response(
                "subtask",
                status="OK",
                message=f"Подзадача {args.done} отмечена выполненной в {task_id}",
                payload=payload,
                summary=f"{task_id} subtask#{args.done} DONE",
            )
        if msg == "not_found":
            return structured_error("subtask", f"Задача {task_id} не найдена", payload={"task_id": task_id})
        if msg == "index":
            return structured_error("subtask", "Неверный индекс подзадачи", payload={"task_id": task_id})
        return structured_error("subtask", msg or "Операция не выполнена", payload={"task_id": task_id})

    if action == "undo":
        ok, msg = manager.set_subtask(task_id, args.undo, False, domain)
        if ok:
            payload = _snapshot(args.undo)
            payload["operation"] = "undo"
            return structured_response(
                "subtask",
                status="OK",
                message=f"Подзадача {args.undo} возвращена в работу в {task_id}",
                payload=payload,
                summary=f"{task_id} subtask#{args.undo} UNDO",
            )
        if msg == "not_found":
            return structured_error("subtask", f"Задача {task_id} не найдена", payload={"task_id": task_id})
        if msg == "index":
            return structured_error("subtask", "Неверный индекс подзадачи", payload={"task_id": task_id})
        return structured_error("subtask", msg or "Операция не выполнена", payload={"task_id": task_id})

    note = (args.note or "").strip()
    if action == "criteria_done":
        ok, msg = manager.update_subtask_checkpoint(task_id, args.criteria_done, "criteria", True, note, domain)
    elif action == "criteria_undo":
        ok, msg = manager.update_subtask_checkpoint(task_id, args.criteria_undo, "criteria", False, note, domain)
    elif action == "tests_done":
        ok, msg = manager.update_subtask_checkpoint(task_id, args.tests_done, "tests", True, note, domain)
    elif action == "tests_undo":
        ok, msg = manager.update_subtask_checkpoint(task_id, args.tests_undo, "tests", False, note, domain)
    elif action == "blockers_done":
        ok, msg = manager.update_subtask_checkpoint(task_id, args.blockers_done, "blockers", True, note, domain)
    else:  # blockers_undo
        ok, msg = manager.update_subtask_checkpoint(task_id, args.blockers_undo, "blockers", False, note, domain)

    if ok:
        labels = {
            "criteria_done": "Критерии подтверждены",
            "criteria_undo": "Критерии возвращены в работу",
            "tests_done": "Тесты подтверждены",
            "tests_undo": "Тесты возвращены в работу",
            "blockers_done": "Блокеры сняты",
            "blockers_undo": "Блокеры возвращены",
        }
        index_map = {
            "criteria_done": args.criteria_done,
            "criteria_undo": args.criteria_undo,
            "tests_done": args.tests_done,
            "tests_undo": args.tests_undo,
            "blockers_done": args.blockers_done,
            "blockers_undo": args.blockers_undo,
        }
        payload = _snapshot(index_map.get(action))
        payload["operation"] = action
        if note:
            payload["note"] = note
        return structured_response(
            "subtask",
            status="OK",
            message=labels.get(action, action),
            payload=payload,
            summary=f"{task_id} {labels.get(action, action)}",
        )
    if msg == "not_found":
        return structured_error("subtask", f"Задача {task_id} не найдена", payload={"task_id": task_id})
    if msg == "index":
        return structured_error("subtask", "Неверный индекс подзадачи", payload={"task_id": task_id})
    return structured_error("subtask", msg or "Операция не выполнена", payload={"task_id": task_id})


def cmd_ok(args) -> int:
    manager = TaskManager()
    try:
        task_id, domain = resolve_task_reference(
            getattr(args, "task_id", None),
            getattr(args, "domain", None),
            getattr(args, "phase", None),
            getattr(args, "component", None),
        )
    except ValueError as exc:
        return structured_error("ok", str(exc))
    index = args.index
    checkpoints = [
        ("criteria", args.criteria_note, "criteria_done"),
        ("tests", args.tests_note, "tests_done"),
        ("blockers", args.blockers_note, "blockers_done"),
    ]
    for checkpoint, note, action in checkpoints:
        ok, msg = manager.update_subtask_checkpoint(task_id, index, checkpoint, True, note or "", domain)
        if not ok:
            payload = {"task_id": task_id, "checkpoint": checkpoint, "index": index}
            if msg == "not_found":
                return structured_error("ok", f"Задача {task_id} не найдена", payload=payload)
            if msg == "index":
                return structured_error("ok", "Неверный индекс подзадачи", payload=payload)
            return structured_error("ok", msg or "Не удалось подтвердить чекпоинт", payload=payload)
    ok, msg = manager.set_subtask(task_id, index, True, domain)
    if not ok:
        payload = {"task_id": task_id, "index": index}
        return structured_error("ok", msg or "Не удалось завершить подзадачу", payload=payload)
    detail = manager.load_task(task_id, domain)
    save_last_task(task_id, domain)
    payload = {
        "task": task_to_dict(detail, include_subtasks=True) if detail else {"id": task_id},
        "subtask_index": index,
    }
    if detail and 0 <= index < len(detail.subtasks):
        payload["subtask"] = subtask_to_dict(detail.subtasks[index])
    return structured_response(
        "ok",
        status="OK",
        message=f"Подзадача {index} полностью подтверждена и закрыта",
        payload=payload,
        summary=f"{task_id} subtask#{index} OK",
    )


def cmd_note(args) -> int:
    manager = TaskManager()
    try:
        task_id, domain = resolve_task_reference(
            getattr(args, "task_id", None),
            getattr(args, "domain", None),
            getattr(args, "phase", None),
            getattr(args, "component", None),
        )
    except ValueError as exc:
        return structured_error("note", str(exc))
    value = not args.undo
    ok, msg = manager.update_subtask_checkpoint(task_id, args.index, args.checkpoint, value, args.note or "", domain)
    if ok:
        detail = manager.load_task(task_id, domain)
        save_last_task(task_id, domain)
        payload = {
            "task": task_to_dict(detail, include_subtasks=True) if detail else {"id": task_id},
            "checkpoint": args.checkpoint,
            "index": args.index,
            "state": "DONE" if value else "TODO",
        }
        if detail and 0 <= args.index < len(detail.subtasks):
            payload["subtask"] = subtask_to_dict(detail.subtasks[args.index])
        return structured_response(
            "note",
            status="OK",
            message=f"{args.checkpoint.capitalize()} {'подтверждены' if value else 'сброшены'}",
            payload=payload,
            summary=f"{task_id} {args.checkpoint} idx {args.index}",
        )
    payload = {"task_id": task_id, "checkpoint": args.checkpoint, "index": args.index}
    if msg == "not_found":
        return structured_error("note", f"Задача {task_id} не найдена", payload=payload)
    if msg == "index":
        return structured_error("note", "Неверный индекс подзадачи", payload=payload)
    return structured_error("note", msg or "Операция не выполнена", payload=payload)


def _parse_bulk_operations(raw: str) -> List[Dict[str, Any]]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SubtaskParseError(f"Невалидный JSON payload для bulk: {exc}") from exc
    if not isinstance(data, list):
        raise SubtaskParseError("Bulk payload должен быть массивом операций")
    return data


def cmd_bulk(args) -> int:
    manager = TaskManager()
    base_domain = derive_domain_explicit(args.domain, getattr(args, "phase", None), getattr(args, "component", None))
    default_task_id: Optional[str] = None
    default_task_domain: str = base_domain
    if getattr(args, "task", None):
        try:
            default_task_id, default_task_domain = resolve_task_reference(
                args.task,
                getattr(args, "domain", None),
                getattr(args, "phase", None),
                getattr(args, "component", None),
            )
        except ValueError as exc:
            return structured_error("bulk", str(exc))
    try:
        raw = _load_input_source(args.input, "bulk JSON payload")
        operations = _parse_bulk_operations(raw)
    except SubtaskParseError as exc:
        return structured_error("bulk", str(exc))
    results = []
    for op in operations:
        raw_task_spec = op.get("task") or op.get("task_id", "")
        op_domain = base_domain
        try:
            if raw_task_spec:
                task_id, op_domain = resolve_task_reference(
                    raw_task_spec,
                    getattr(args, "domain", None),
                    getattr(args, "phase", None),
                    getattr(args, "component", None),
                )
            elif default_task_id:
                task_id = default_task_id
                op_domain = default_task_domain
            else:
                task_id = ""
        except ValueError as exc:
            results.append({"task": raw_task_spec, "status": "ERROR", "message": str(exc)})
            continue
        index = op.get("index")
        if not task_id or not isinstance(index, int):
            results.append({"task": task_id, "index": index, "status": "ERROR", "message": "Укажи task/index"})
            continue
        entry_payload = {"task": task_id, "index": index}
        failed = False
        for checkpoint in ("criteria", "tests", "blockers"):
            spec = op.get(checkpoint)
            if spec is None:
                continue
            done = bool(spec.get("done", True))
            note = spec.get("note", "") or ""
            ok, msg = manager.update_subtask_checkpoint(task_id, index, checkpoint, done, note, op_domain)
            if not ok:
                entry_payload["status"] = "ERROR"
                entry_payload["message"] = msg or f"Не удалось обновить {checkpoint}"
                failed = True
                break
        if failed:
            results.append(entry_payload)
            continue
        if op.get("complete"):
            ok, msg = manager.set_subtask(task_id, index, True, op_domain)
            if not ok:
                entry_payload["status"] = "ERROR"
                entry_payload["message"] = msg or "Не удалось закрыть подзадачу"
                results.append(entry_payload)
                continue
        detail = manager.load_task(task_id, op_domain)
        save_last_task(task_id, op_domain)
        entry_payload["status"] = "OK"
        entry_payload["task_detail"] = task_to_dict(detail, include_subtasks=True) if detail else {"id": task_id}
        if detail and 0 <= index < len(detail.subtasks):
            entry_payload["subtask"] = subtask_to_dict(detail.subtasks[index])
            entry_payload["checkpoint_states"] = {
                "criteria": detail.subtasks[index].criteria_confirmed,
                "tests": detail.subtasks[index].tests_confirmed,
                "blockers": detail.subtasks[index].blockers_resolved,
            }
        results.append(entry_payload)
    message = f"Выполнено операций: {sum(1 for r in results if r.get('status') == 'OK')}/{len(results)}"
    return structured_response(
        "bulk",
        status="OK",
        message=message,
        payload={"results": results},
        summary=message,
    )


def cmd_checkpoint(args) -> int:
    auto_mode = getattr(args, "auto", False)
    base_note = (getattr(args, "note", "") or "").strip()
    if not auto_mode and not is_interactive():
        return structured_error(
            "checkpoint",
            "Мастер чекпоинтов требует интерактивный терминал (или укажи --auto)",
        )
    try:
        task_id, domain = resolve_task_reference(
            getattr(args, "task_id", None),
            getattr(args, "domain", None),
            getattr(args, "phase", None),
            getattr(args, "component", None),
        )
    except ValueError as exc:
        return structured_error("checkpoint", str(exc))
    manager = TaskManager()
    detail = manager.load_task(task_id, domain)
    if not detail:
        return structured_error("checkpoint", f"Задача {task_id} не найдена")
    if not detail.subtasks:
        return structured_error("checkpoint", f"Задача {task_id} не содержит подзадач")

    def pick_subtask_index() -> int:
        if args.subtask is not None:
            return args.subtask
        if auto_mode:
            for idx, st in enumerate(detail.subtasks):
                if not st.completed:
                    return idx
            return 0
        print("\n[Шаг 1] Выбор подзадачи")
        for idx, st in enumerate(detail.subtasks):
            flags = subtask_flags(st)
            glyphs = ''.join(['✓' if flags[k] else '·' for k in ("criteria", "tests", "blockers")])
            print(f"  {idx}. [{glyphs}] {'[OK]' if st.completed else '[ ]'} {st.title}")
        while True:
            raw = prompt("Введите индекс подзадачи", default="0")
            try:
                value = int(raw)
            except ValueError:
                print("  [!] Используй целое число")
                continue
            if 0 <= value < len(detail.subtasks):
                return value
            print("  [!] Недопустимый индекс")

    subtask_index = pick_subtask_index()
    if subtask_index < 0 or subtask_index >= len(detail.subtasks):
        return structured_error("checkpoint", "Неверный индекс подзадачи")

    checkpoint_labels = [
        ("criteria", "Критерии"),
        ("tests", "Тесты"),
        ("blockers", "Блокеры"),
    ]
    operations: List[Dict[str, Any]] = []

    for checkpoint, label in checkpoint_labels:
        st = manager.load_task(task_id, domain)
        if not st:
            return structured_error("checkpoint", "Задача недоступна")
        sub = st.subtasks[subtask_index]
        attr_map = {
            "criteria": sub.criteria_confirmed,
            "tests": sub.tests_confirmed,
            "blockers": sub.blockers_resolved,
        }
        if attr_map[checkpoint]:
            operations.append({"checkpoint": checkpoint, "state": "already"})
            continue
        note_value = base_note
        confirm_checkpoint = auto_mode
        if not auto_mode:
            print(f"\n[Шаг] {label}: {sub.title}")
            print(f"  Текущее состояние: TODO. Подтвердить {label.lower()}?")
            confirm_checkpoint = confirm(f"Подтвердить {label.lower()}?", default=True)
            if not confirm_checkpoint:
                operations.append({"checkpoint": checkpoint, "state": "skipped"})
                continue
            if not note_value:
                note_value = prompt("Комментарий/доказательство", default="")
        if not note_value:
            note_value = f"checkpoint:{checkpoint}"
        ok, msg = manager.update_subtask_checkpoint(task_id, subtask_index, checkpoint, True, note_value, domain)
        if not ok:
            return structured_error("checkpoint", msg or f"Не удалось подтвердить {label.lower()}")
        operations.append({"checkpoint": checkpoint, "state": "confirmed", "note": note_value})

    detail = manager.load_task(task_id, domain)
    completed = False
    if detail:
        sub = detail.subtasks[subtask_index]
        ready = sub.ready_for_completion()
        if ready:
            mark_done = auto_mode
            if not auto_mode:
                mark_done = confirm("Все чекпоинты отмечены. Закрыть подзадачу?", default=True)
            if mark_done:
                ok, msg = manager.set_subtask(task_id, subtask_index, True, domain)
                if not ok:
                    return structured_error("checkpoint", msg or "Не удалось закрыть подзадачу")
                operations.append({"checkpoint": "done", "state": "completed"})
                completed = True
    detail = manager.load_task(task_id, domain)
    save_last_task(task_id, domain)
    payload = {
        "task": task_to_dict(detail, include_subtasks=True) if detail else {"id": task_id},
        "subtask_index": subtask_index,
        "operations": operations,
        "auto": auto_mode,
        "completed": completed,
    }
    return structured_response(
        "checkpoint",
        status="OK",
        message="Мастер чекпоинтов завершён",
        payload=payload,
        summary=f"{task_id}#{subtask_index} checkpoints",
    )


def cmd_move(args) -> int:
    """Переместить задачу в другую подпапку .tasks"""
    manager = TaskManager()
    if args.glob:
        count = manager.move_glob(args.glob, args.to)
        payload = {"glob": args.glob, "target": args.to, "moved": count}
        return structured_response(
            "move",
            status="OK",
            message=f"Перемещено задач: {count} в {args.to}",
            payload=payload,
            summary=f"{count} задач → {args.to}",
        )
    if not args.task_id:
        return structured_error("move", "Укажи task_id или --glob")
    task_id = normalize_task_id(args.task_id)
    if manager.move_task(task_id, args.to):
        save_last_task(task_id, args.to)
        payload = {"task_id": task_id, "target": args.to}
        return structured_response(
            "move",
            status="OK",
            message=f"{task_id} перемещена в {args.to}",
            payload=payload,
            summary=f"{task_id} → {args.to}",
        )
    return structured_error("move", f"Не удалось переместить {task_id}", payload={"task_id": task_id, "target": args.to})


def cmd_clean(args) -> int:
    if not any([args.tag, args.status, args.phase]):
        return structured_error("clean", "Укажи хотя бы один фильтр: --tag/--status/--phase")
    manager = TaskManager()
    matched, removed = manager.clean_tasks(tag=args.tag, status=args.status, phase=args.phase, dry_run=args.dry_run)
    if args.dry_run:
        payload = {
            "mode": "dry-run",
            "matched": matched,
            "filters": {"tag": args.tag, "status": args.status, "phase": args.phase},
        }
        return structured_response(
            "clean",
            status="OK",
            message=f"Будут удалены {len(matched)} задач(и)",
            payload=payload,
            summary=f"dry-run {len(matched)} задач",
        )
    payload = {
        "removed": removed,
        "matched": matched,
        "filters": {"tag": args.tag, "status": args.status, "phase": args.phase},
    }
    return structured_response(
        "clean",
        status="OK",
        message=f"Удалено задач: {removed}",
        payload=payload,
        summary=f"Удалено {removed}",
    )


def cmd_edit(args) -> int:
    manager = TaskManager()
    domain = derive_domain_explicit(args.domain, getattr(args, "phase", None), getattr(args, "component", None))
    task = manager.load_task(normalize_task_id(args.task_id), domain)
    if not task:
        return structured_error("edit", f"Задача {args.task_id} не найдена")
    if args.description:
        task.description = args.description
    if args.context:
        task.context = args.context
    if args.tags:
        task.tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    if args.priority:
        task.priority = args.priority
    if args.phase:
        task.phase = args.phase
    if args.component:
        task.component = args.component
    if args.new_domain:
        task.domain = args.new_domain
    manager.save_task(task)
    payload = {"task": task_to_dict(task, include_subtasks=True)}
    return structured_response(
        "edit",
        status="OK",
        message=f"Задача {task.id} обновлена",
        payload=payload,
        summary=f"{task.id} updated",
    )


def cmd_lint(args) -> int:
    issues: List[str] = []
    tasks_dir = Path(".tasks")
    if not tasks_dir.exists():
        issues.append(".tasks каталог отсутствует")
    else:
        manager = TaskManager()
        for f in tasks_dir.rglob("TASK-*.task"):
            detail = TaskFileParser.parse(f)
            if not detail:
                issues.append(f"{f} не парсится")
                continue
            changed = False
            if not detail.description:
                issues.append(f"{f} без description")
            if not detail.success_criteria:
                issues.append(f"{f} без tests/success_criteria")
            if not detail.parent:
                detail.parent = detail.id
                changed = True
            if args.fix and changed:
                manager.save_task(detail)
    payload = {"issues": issues, "fix": bool(args.fix)}
    if issues:
        return structured_response(
            "lint",
            status="ERROR",
            message=f"Найдено {len(issues)} проблем(ы)",
            payload=payload,
            summary="Lint failed",
            exit_code=1,
        )
    return structured_response(
        "lint",
        status="OK",
        message="Lint OK",
        payload=payload,
        summary="Lint clean",
    )


def cmd_suggest(args) -> int:
    manager = TaskManager()
    domain = derive_domain_explicit(args.domain, getattr(args, "phase", None), getattr(args, "component", None))
    tasks = manager.list_tasks(domain)
    active = [t for t in tasks if t.status != "OK"]
    filter_hint = f" (domain='{domain or '-'}', phase='{args.phase or '-'}', component='{args.component or '-'}')"
    if not active:
        payload = {
            "filters": {"domain": domain or "", "phase": args.phase or "", "component": args.component or ""},
            "suggestions": [],
        }
        return structured_response(
            "suggest",
            status="OK",
            message="Все задачи завершены" + filter_hint,
            payload=payload,
            summary="Нет задач для рекомендации",
        )
    priority_map = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}

    def score(t: TaskDetail):
        prog = t.calculate_progress()
        return (-priority_map.get(t.priority, 0), prog, len(t.dependencies))

    sorted_tasks = sorted(active, key=score)
    save_last_task(sorted_tasks[0].id, sorted_tasks[0].domain)
    payload = {
        "filters": {"domain": domain or "", "phase": args.phase or "", "component": args.component or ""},
        "suggestions": [task_to_dict(task) for task in sorted_tasks[:5]],
    }
    return structured_response(
        "suggest",
        status="OK",
        message="Рекомендации сформированы" + filter_hint,
        payload=payload,
        summary=f"{len(payload['suggestions'])} рекомендаций",
    )


def cmd_quick(args) -> int:
    """Быстрый обзор: топ-3 незавершённых задачи."""
    manager = TaskManager()
    domain = derive_domain_explicit(args.domain, getattr(args, "phase", None), getattr(args, "component", None))
    tasks = [t for t in manager.list_tasks(domain) if t.status != "OK"]
    tasks.sort(key=lambda t: (t.priority, t.calculate_progress()))
    filter_hint = f" (domain='{domain or '-'}', phase='{args.phase or '-'}', component='{args.component or '-'}')"
    if not tasks:
        payload = {
            "filters": {"domain": domain or "", "phase": args.phase or "", "component": args.component or ""},
            "top": [],
        }
        return structured_response(
            "quick",
            status="OK",
            message="Все задачи выполнены" + filter_hint,
            payload=payload,
            summary="Нет задач",
        )
    top = tasks[:3]
    save_last_task(tasks[0].id, tasks[0].domain)
    payload = {
        "filters": {"domain": domain or "", "phase": args.phase or "", "component": args.component or ""},
        "top": [task_to_dict(task) for task in top],
    }
    return structured_response(
        "quick",
        status="OK",
        message="Быстрый обзор top-3" + filter_hint,
        payload=payload,
        summary=f"Top-{len(top)} задач",
    )


def _template_subtask_entry(idx: int) -> Dict[str, Any]:
    return {
        "title": f"Результат {idx}: опиши измеримый итог (≥20 символов)",
        "criteria": [
            "Метрики успеха определены и зафиксированы",
            "Доказательства приёмки описаны",
            "Обновлены мониторинг/алерты",
        ],
        "tests": [
            "pytest -q tests/... -k <кейc>",
            "perf или интеграционный прогон",
        ],
        "blockers": [
            "Перечисли approvals/зависимости",
            "Опиши риски и план снятия блокеров",
        ],
    }


def _template_test_matrix() -> List[Dict[str, str]]:
    return [
        {
            "name": "Юнит + интеграция ≥85%",
            "command": "pytest -q --maxfail=1 --cov=src --cov-report=xml",
            "evidence": "coverage.xml ≥85%, отчёт приложен в задачу",
        },
        {
            "name": "Конфигурационный/перф",
            "command": "pytest -q tests/perf -k scenario && python scripts/latency_audit.py",
            "evidence": "p95 ≤ целевой SLO, лог проверки загружен в репозиторий",
        },
        {
            "name": "Регресс + ручная приёмка",
            "command": "pytest -q tests/e2e && ./scripts/manual-checklist.md",
            "evidence": "Чеклист приёмки с таймстемпом и ссылкой на демо",
        },
    ]


def _template_docs_matrix() -> List[Dict[str, str]]:
    return [
        {
            "artifact": "ADR",
            "path": "docs/adr/ADR-<номер>.md",
            "goal": "Зафиксировать выбранную архитектуру и компромиссы hexagonal monolith.",
        },
        {
            "artifact": "Runbook/операционный гайд",
            "path": "docs/runbooks/<feature>.md",
            "goal": "Описать фич-срез, команды запуска и алерты.",
        },
        {
            "artifact": "Changelog/RELNOTES",
            "path": "docs/releases/<date>-<feature>.md",
            "goal": "Протоколировать влияние на пользователей, метрики и тесты.",
        },
    ]


def cmd_template_subtasks(args) -> int:
    count = max(3, args.count)
    template = [_template_subtask_entry(i + 1) for i in range(count)]
    payload = {
        "type": "subtasks",
        "count": count,
        "template": template,
        "tests_template": _template_test_matrix(),
        "documentation_template": _template_docs_matrix(),
        "usage": "apply_task ... --subtasks 'JSON' | --subtasks @file | --subtasks - (всё на русском)",
    }
    return structured_response(
        "template.subtasks",
        status="OK",
        message="Сгенерирован JSON-шаблон подзадач",
        payload=payload,
        summary=f"{count} шаблонов",
    )


# ============================================================================
# CLI
# ============================================================================


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="tasks.py — управление задачами (.tasks only)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    def add_domain_arg(sp):
        sp.add_argument(
            "--domain",
            "-F",
            dest="domain",
            help="домен/подпапка внутри .tasks (архитектурный контур)",
        )
        return sp

    def add_context_args(sp):
        add_domain_arg(sp)
        sp.add_argument("--phase", help="фаза/итерация (используется для автопути)")
        sp.add_argument("--component", help="компонент/модуль (используется для автопути)")
        return sp

    sub = parser.add_subparsers(dest="command", help="Команды")

    # tui
    tui_p = sub.add_parser("tui", help="Запустить TUI")
    tui_p.add_argument("--theme", choices=list(THEMES.keys()), default=DEFAULT_THEME, help="палитра интерфейса")
    tui_p.set_defaults(func=cmd_tui)

    # list
    lp = sub.add_parser("list", help="Список задач")
    lp.add_argument("--status", choices=["OK", "WARN", "FAIL"])
    lp.add_argument("--progress", action="store_true")
    add_context_args(lp)
    lp.set_defaults(func=cmd_list)

    # show
    sp = sub.add_parser("show", help="Показать задачу")
    sp.add_argument("task_id", nargs="?")
    add_context_args(sp)
    sp.set_defaults(func=cmd_show)

    # create
    cp = sub.add_parser("create", help="Создать задачу")
    cp.add_argument("title")
    cp.add_argument("--status", default="FAIL", choices=["OK", "WARN", "FAIL"])
    cp.add_argument("--priority", default="MEDIUM", choices=["LOW", "MEDIUM", "HIGH"])
    cp.add_argument("--parent", required=True)
    cp.add_argument("--description", "-d", required=True)
    cp.add_argument("--context", "-c")
    cp.add_argument("--tags", "-t")
    cp.add_argument(
        "--subtasks",
        "-s",
        required=True,
        help="JSON массив подзадач (строкой, --subtasks @file.json или --subtasks - для STDIN; всё на русском)",
    )
    cp.add_argument("--dependencies")
    cp.add_argument("--next-steps", "-n")
    cp.add_argument("--tests", required=True)
    cp.add_argument("--risks", help="semicolon-separated risks", required=True)
    cp.add_argument("--validate-only", action="store_true", help="Проверить payload без записи задачи")
    add_context_args(cp)
    cp.set_defaults(func=cmd_create)

    # task (smart)
    tp = sub.add_parser(
        "task",
        help="Умное создание (с парсингом #тегов и @зависимостей)",
        description=(
            "Создаёт задачу из заголовка с автоматическим извлечением тегов (#backend) и зависимостей (@TASK-010).\n"
            "Обязательные параметры: --parent, --description, --tests, --risks, --subtasks.\n"
            "Каждая подзадача в JSON должна быть на русском языке и включать критерии/тесты/блокеры."
        ),
    )
    tp.add_argument("title")
    tp.add_argument("--status", default="FAIL", choices=["OK", "WARN", "FAIL"])
    tp.add_argument("--priority", default="MEDIUM", choices=["LOW", "MEDIUM", "HIGH"])
    tp.add_argument("--parent", required=True)
    tp.add_argument("--description", "-d", required=True)
    tp.add_argument("--context", "-c")
    tp.add_argument("--tags", "-t")
    tp.add_argument(
        "--subtasks",
        "-s",
        required=True,
        help="JSON массив подзадач (строкой, --subtasks @file.json или --subtasks - для STDIN; всё на русском)",
    )
    tp.add_argument("--dependencies")
    tp.add_argument("--next-steps", "-n")
    tp.add_argument("--tests", required=True)
    tp.add_argument("--risks", help="semicolon-separated risks", required=True)
    tp.add_argument("--validate-only", action="store_true", help="Проверить payload без записи задачи")
    add_context_args(tp)
    tp.set_defaults(func=cmd_smart_create)

    # guided (interactive)
    gp = sub.add_parser("guided", help="Интерактивное создание (шаг-ответ-шаг)")
    add_context_args(gp)
    gp.set_defaults(func=cmd_create_guided)

    # update
    up = sub.add_parser(
        "update",
        help="Обновить статус задачи",
        description=(
            "Обновляет статус задачи на OK/WARN/FAIL.\n"
            "Вызовы поддерживают оба порядка: `update TASK-005 OK` или `update OK TASK-005`.\n"
            "Перед переводом в OK убедись, что все подзадачи закрыты и есть доказательства тестов."
        ),
    )
    up.add_argument("arg1")
    up.add_argument("arg2", nargs="?")
    add_context_args(up)
    up.set_defaults(func=cmd_update)

    # analyze
    ap = sub.add_parser("analyze", help="Анализ задачи")
    ap.add_argument("task_id")
    add_context_args(ap)
    ap.set_defaults(func=cmd_analyze)

    # next
    np = sub.add_parser("next", help="Следующая задача")
    add_context_args(np)
    np.set_defaults(func=cmd_next)

    # add-subtask
    asp = sub.add_parser("add-subtask", help="Добавить подзадачу")
    asp.add_argument("task_id")
    asp.add_argument("subtask")
    asp.add_argument("--criteria", required=True, help="Критерии выполнения (через ';')")
    asp.add_argument("--tests", required=True, help="Тесты/проверки (через ';')")
    asp.add_argument("--blockers", required=True, help="Блокеры/зависимости (через ';')")
    add_context_args(asp)
    asp.set_defaults(func=cmd_add_subtask)

    # add-dependency
    adp = sub.add_parser("add-dep", help="Добавить зависимость")
    adp.add_argument("task_id")
    adp.add_argument("dependency")
    add_context_args(adp)
    adp.set_defaults(func=cmd_add_dependency)

    # ok macro
    okp = sub.add_parser("ok", help="Закрыть подзадачу одним махом (criteria/tests/blockers+done)")
    okp.add_argument("task_id")
    okp.add_argument("index", type=int)
    okp.add_argument("--criteria-note")
    okp.add_argument("--tests-note")
    okp.add_argument("--blockers-note")
    add_context_args(okp)
    okp.set_defaults(func=cmd_ok)

    # note macro
    notep = sub.add_parser("note", help="Добавить заметку/подтверждение к чекпоинту")
    notep.add_argument("task_id")
    notep.add_argument("index", type=int)
    notep.add_argument("--checkpoint", choices=["criteria", "tests", "blockers"], required=True)
    notep.add_argument("--note", required=True)
    notep.add_argument("--undo", action="store_true", help="сбросить подтверждение вместо установки")
    add_context_args(notep)
    notep.set_defaults(func=cmd_note)

    # bulk macro
    blp = sub.add_parser("bulk", help="Выполнить набор чекпоинтов из JSON payload")
    blp.add_argument("--input", "-i", default="-", help="Источник JSON (строка, @file, '-'=STDIN)")
    blp.add_argument("--task", help="task_id по умолчанию для операций без поля task (используй '.'/last для .last)")
    add_context_args(blp)
    blp.set_defaults(func=cmd_bulk)

    # checkpoint wizard
    ckp = sub.add_parser(
        "checkpoint",
        help="Пошаговый мастер подтверждения критериев/тестов/блокеров",
        description=(
            "Интерактивно проводит через чекпоинты выбранной подзадачи (критерии → тесты → блокеры).\n"
            "Поддерживает шорткат '.'/last и режим --auto для нефтерминальных сред."
        ),
    )
    ckp.add_argument("task_id", nargs="?", help="TASK-ID или '.' для последней задачи")
    ckp.add_argument("--subtask", type=int, help="Индекс подзадачи (0..n-1)")
    ckp.add_argument("--note", help="Комментарий по умолчанию для чекпоинтов")
    ckp.add_argument("--auto", action="store_true", help="Подтвердить все чекпоинты без вопросов")
    add_context_args(ckp)
    ckp.set_defaults(func=cmd_checkpoint)

    # subtask
    stp = sub.add_parser("subtask", help="Управление подзадачами (add/done/undo)")
    stp.add_argument("task_id")
    stp.add_argument("--add", help="добавить подзадачу с текстом")
    stp.add_argument("--criteria", help="критерии для --add (через ';')")
    stp.add_argument("--tests", help="тесты для --add (через ';')")
    stp.add_argument("--blockers", help="блокеры для --add (через ';')")
    stp.add_argument("--done", type=int, help="отметить выполненной по индексу (0..n-1)")
    stp.add_argument("--undo", type=int, help="вернуть в работу по индексу (0..n-1)")
    stp.add_argument("--criteria-done", type=int, dest="criteria_done", help="подтвердить выполнение критериев (индекс)")
    stp.add_argument("--criteria-undo", type=int, dest="criteria_undo", help="сбросить подтверждение критериев (индекс)")
    stp.add_argument("--tests-done", type=int, dest="tests_done", help="подтвердить тесты (индекс)")
    stp.add_argument("--tests-undo", type=int, dest="tests_undo", help="сбросить подтверждение тестов (индекс)")
    stp.add_argument("--blockers-done", type=int, dest="blockers_done", help="подтвердить снятие блокеров (индекс)")
    stp.add_argument("--blockers-undo", type=int, dest="blockers_undo", help="сбросить подтверждение блокеров (индекс)")
    stp.add_argument("--note", help="описание/доказательство при отметке чекпоинтов")
    add_context_args(stp)
    stp.set_defaults(func=cmd_subtask)

    # move
    mv = sub.add_parser("move", help="Переместить задачу(и) в подпапку .tasks")
    mv.add_argument("task_id", nargs="?")
    mv.add_argument("--glob", help="glob-шаблон внутри .tasks (пример: 'phase1/*.task')")
    mv.add_argument("--to", required=True, help="целевая подпапка")
    add_domain_arg(mv)
    mv.set_defaults(func=cmd_move)

    # edit
    ep = sub.add_parser(
        "edit",
        help="Редактировать свойства задачи",
        description=(
            "Позволяет менять описание, теги, приоритет, фазу/компонент и т.п.\n"
            "Пример: `apply_task edit TASK-010 --description \"Новая формулировка\" --phase iteration-2`.\n"
            "Изменения описывай на русском языке, чтобы TUI и отчёты оставались консистентны."
        ),
    )
    ep.add_argument("task_id")
    ep.add_argument("--description", "-d")
    ep.add_argument("--context", "-c")
    ep.add_argument("--tags", "-t")
    ep.add_argument("--priority", "-p", choices=["LOW", "MEDIUM", "HIGH"])
    ep.add_argument("--phase", help="новая фаза/итерация")
    ep.add_argument("--component", help="новый компонент/модуль")
    ep.add_argument("--new-domain", help="переместить в подпапку")
    add_domain_arg(ep)
    ep.set_defaults(func=cmd_edit)

    # clean
    cl = sub.add_parser("clean", help="Удалить задачи по фильтрам")
    cl.add_argument("--tag", help="тег без #")
    cl.add_argument("--status", choices=["OK", "WARN", "FAIL"], help="фильтр по статусу")
    cl.add_argument("--phase", help="фаза/итерация")
    cl.add_argument("--dry-run", action="store_true", help="только показать задачи без удаления")
    cl.set_defaults(func=cmd_clean)

    # lint
    lp2 = sub.add_parser("lint", help="Проверка .tasks")
    lp2.add_argument("--fix", action="store_true")
    lp2.set_defaults(func=cmd_lint)

    # suggest
    sg = sub.add_parser("suggest", help="Рекомендовать задачи")
    add_context_args(sg)
    sg.set_defaults(func=cmd_suggest)

    # quick
    qp = sub.add_parser("quick", help="Быстрый обзор top-3")
    add_context_args(qp)
    qp.set_defaults(func=cmd_quick)

    # template
    tmp = sub.add_parser("template", help="Генерация шаблонов для автоматизации")
    tmp_sub = tmp.add_subparsers(dest="template_command")
    tmp_sub.required = True
    subt = tmp_sub.add_parser("subtasks", help="Создать JSON с заготовками подзадач")
    subt.add_argument("--count", type=int, default=3, help="Количество подзадач (>=3)")
    subt.set_defaults(func=cmd_template_subtasks)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not getattr(args, "command", None):
        parser.print_help()
        return 1
    if args.command == "help":
        parser.print_help()
        print("\nКонтекст: --domain или phase/component формируют путь; .last хранит TASK@domain.")
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
