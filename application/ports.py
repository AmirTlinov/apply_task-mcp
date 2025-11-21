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

    def next_id(self) -> str:
        ...

    def delete(self, task_id: str, domain: str = "") -> bool:
        ...

    def move(self, task_id: str, new_domain: str, current_domain: str = "") -> bool:
        ...
