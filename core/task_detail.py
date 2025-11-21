from dataclasses import dataclass, field
from typing import List, Optional
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

    def calculate_progress(self) -> int:
        if not self.subtasks:
            return 0
        completed = sum(1 for st in self.subtasks if st.completed)
        return int((completed / len(self.subtasks)) * 100)
