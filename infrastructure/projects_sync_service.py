import projects_sync
from typing import Optional, Dict, Any, List
from core import TaskDetail
from application.sync_service import SyncService


class ProjectsSyncService(SyncService):
    def __init__(self, base: projects_sync.ProjectsSync | None = None):
        self._sync = base or projects_sync.get_projects_sync()

    @property
    def enabled(self) -> bool:
        return self._sync.enabled

    @property
    def config(self):
        return self._sync.config

    def sync_task(self, task: TaskDetail) -> bool:
        return bool(self._sync.sync_task(task))

    def pull_task_fields(self, task: TaskDetail) -> None:
        return self._sync.pull_task_fields(task)

    def clone(self) -> "ProjectsSyncService":
        if hasattr(self._sync, "clone"):
            return ProjectsSyncService(self._sync.clone())
        # fallback: reuse same instance for non-cloneable fakes (tests)
        if not isinstance(self._sync, projects_sync.ProjectsSync):
            return ProjectsSyncService(self._sync)
        clone = projects_sync.ProjectsSync(config_path=projects_sync.CONFIG_PATH)
        clone.token = self._sync.token
        clone.config = self._sync.config
        clone.project_fields = self._sync.project_fields
        return ProjectsSyncService(clone)

    def handle_webhook(self, body: str, signature: Optional[str], secret: Optional[str]) -> Dict[str, Any]:
        if not hasattr(self._sync, "handle_webhook"):
            raise NotImplementedError
        return self._sync.handle_webhook(body, signature, secret)

    def consume_conflicts(self) -> List[Dict[str, Any]]:
        if hasattr(self._sync, "consume_conflicts"):
            return self._sync.consume_conflicts()
        return []
