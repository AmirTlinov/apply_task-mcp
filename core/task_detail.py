from dataclasses import dataclass, field
from typing import List, Optional
from pathlib import Path
from datetime import datetime, timezone
import yaml

from .subtask import SubTask


@dataclass
class TaskDetail:
    id: str
    title: str
    status: str
    description: str = ""
    domain: str = ""
    phase: str = ""
    component: str = ""
    parent: Optional[str] = None
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
    dependencies: List[str] = field(default_factory=list)
    next_steps: List[str] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    subtasks: List[SubTask] = field(default_factory=list)
    project_item_id: Optional[str] = None
    project_draft_id: Optional[str] = None
    project_remote_updated: Optional[str] = None
    project_issue_number: Optional[str] = None
    _source_path: Optional[str] = None
    _source_mtime: float = 0.0
    history: List[str] = field(default_factory=list)

    def calculate_progress(self) -> int:
        if not self.subtasks:
            return self.progress
        completed = sum(1 for st in self.subtasks if st.completed)
        return int((completed / len(self.subtasks)) * 100)

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
            "created": self.created or self._now_iso(),
            "updated": self.updated or self._now_iso(),
            "tags": self.tags,
            "assignee": self.assignee or "ai",
            "progress": self.calculate_progress(),
        }
        if self.blocked:
            metadata["blocked"] = True
            metadata["blockers"] = self.blockers
        if self.project_item_id:
            metadata["project_item_id"] = self.project_item_id
        if self.project_draft_id:
            metadata["project_draft_id"] = self.project_draft_id
        if self.project_remote_updated:
            metadata["project_remote_updated"] = self.project_remote_updated
        if self.project_issue_number:
            metadata["project_issue_number"] = self.project_issue_number
        subtask_ids = [st.project_item_id for st in self.subtasks]
        if any(subtask_ids):
            metadata["subtask_project_ids"] = subtask_ids

        lines = ["---", yaml.dump(metadata, allow_unicode=True, default_flow_style=False).strip(), "---", ""]
        lines.append(f"# {self.title}\n")

        def add_section(title: str, content: List[str]) -> None:
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

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()
