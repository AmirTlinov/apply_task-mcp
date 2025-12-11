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

    @property
    def last_pull(self):
        return getattr(self._sync, "last_pull", None)

    @property
    def last_push(self):
        return getattr(self._sync, "last_push", None)

    @property
    def project_id(self):
        return getattr(self._sync, "project_id", None)

    def project_url(self):
        return self._sync.project_url() if hasattr(self._sync, "project_url") else None

    @property
    def runtime_disabled_reason(self):
        return getattr(self._sync, "runtime_disabled_reason", None)

    @property
    def detect_error(self):
        return getattr(self._sync, "detect_error", None)

    @property
    def token_present(self) -> bool:
        return bool(getattr(self._sync, "token", None))

    def ensure_metadata(self) -> None:
        if hasattr(self._sync, "_ensure_project_metadata"):
            self._sync._ensure_project_metadata()

    def rate_info(self) -> dict:
        limiter = getattr(self._sync, "_rate_limiter", None)
        if not limiter:
            return {}
        return {
            "remaining": getattr(limiter, "last_remaining", None),
            "reset_epoch": getattr(limiter, "last_reset_epoch", None),
            "wait": getattr(limiter, "last_wait", None),
        }
    @last_push.setter
    def last_push(self, value):
        if hasattr(self._sync, "last_push"):
            try:
                self._sync.last_push = value
            except Exception:
                pass

    def serve_webhook(self, body: str, signature: Optional[str], secret: Optional[str]) -> Dict[str, Any]:
        # alias to handle_webhook for protocol completeness
        return self.handle_webhook(body, signature, secret)

    def handle_webhook(self, body: str, signature: Optional[str], secret: Optional[str]) -> Dict[str, Any]:
        if not hasattr(self._sync, "handle_webhook"):
            raise NotImplementedError
        return self._sync.handle_webhook(body, signature, secret)

    def consume_conflicts(self) -> List[Dict[str, Any]]:
        if hasattr(self._sync, "consume_conflicts"):
            return self._sync.consume_conflicts()
        return []
