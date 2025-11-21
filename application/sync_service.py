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

    @property
    def last_pull(self) -> Any:
        ...

    @property
    def last_push(self) -> Any:
        ...

    @property
    def project_id(self) -> Any:
        ...

    def project_url(self) -> Any:
        ...

    @property
    def runtime_disabled_reason(self) -> Any:
        ...

    @property
    def detect_error(self) -> Any:
        ...

    @property
    def token_present(self) -> bool:
        ...

    def ensure_metadata(self) -> None:
        ...

    def rate_info(self) -> Dict[str, Any]:
        ...

    def serve_webhook(self, body: str, signature: Optional[str], secret: Optional[str]) -> Dict[str, Any]:
        ...

    def handle_webhook(self, body: str, signature: Optional[str], secret: Optional[str]) -> Dict[str, Any]:
        ...

    def consume_conflicts(self) -> List[Dict[str, Any]]:
        ...
