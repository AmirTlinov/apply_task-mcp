from typing import Protocol, Any, Optional, Dict, List
from core import TaskDetail


class SyncService(Protocol):
    enabled: bool
    config: Any

    def sync_task(self, task: TaskDetail) -> bool:
        ...

    def pull_task_fields(self, task: TaskDetail) -> None:
        ...

    def clone(self) -> "SyncService":
        ...

    def handle_webhook(self, body: str, signature: Optional[str], secret: Optional[str]) -> Dict[str, Any]:
        ...

    def consume_conflicts(self) -> List[Dict[str, Any]]:
        ...
