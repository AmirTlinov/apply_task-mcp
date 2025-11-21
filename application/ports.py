from typing import Protocol, List, Optional
from core import TaskDetail


class TaskRepository(Protocol):
    def load(self, task_id: str, domain: str = "") -> Optional[TaskDetail]:
        ...

    def save(self, task: TaskDetail) -> None:
        ...

    def list(self, domain_path: str = "", skip_sync: bool = False) -> List[TaskDetail]:
        ...

    def compute_signature(self) -> int:
        ...
