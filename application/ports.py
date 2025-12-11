from typing import Protocol, List, Optional, Tuple
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

    def move_glob(self, pattern: str, new_domain: str) -> int:
        ...

    def delete_glob(self, pattern: str) -> int:
        ...

    def clean_filtered(self, tag: str = "", status: str = "", phase: str = "") -> Tuple[List[str], int]:
        ...
