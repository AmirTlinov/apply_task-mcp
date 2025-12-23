"""Operation history for undo/redo support.

This is an adapter-level persistence helper used by the intent API and other
interfaces. It is intentionally deterministic and file-backed.
"""

from __future__ import annotations

import json
import hashlib
import shutil
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from core.desktop.devtools.interface.tasks_dir_resolver import (
    get_tasks_dir_for_project,
    resolve_project_root,
)

# History configuration
MAX_HISTORY_SIZE = 100  # Maximum operations to keep
SNAPSHOT_DIR = ".snapshots"
HISTORY_FILE = ".history.json"
AUDIT_FILE = ".audit.json"


@dataclass
class Operation:
    """Single operation in history."""

    id: str
    timestamp: float
    intent: str
    task_id: Optional[str]
    data: Dict[str, Any]
    stream: str = "ops"  # "ops" (undoable writes) or "audit" (preview/read trace)
    effect: str = "write"  # "write" or "read"
    task_file: Optional[str] = None  # relative to tasks_dir (supports domain paths)
    snapshot_id: Optional[str] = None  # Before operation (for undo)
    after_snapshot_id: Optional[str] = None  # After operation (for redo)
    result: Optional[Dict[str, Any]] = None
    undone: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_summary_dict(self) -> Dict[str, Any]:
        """Return a compact, agent-friendly operation summary.

        Delta updates should be lightweight by default; callers can opt into full
        payloads via `to_dict()`.
        """
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "intent": self.intent,
            "task_id": self.task_id,
            "stream": self.stream,
            "effect": self.effect,
            "task_file": self.task_file,
            "undone": self.undone,
            "has_result": self.result is not None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Operation":
        payload = dict(data or {})
        # Migration: legacy history used `step_id/step_file`.
        if "task_id" not in payload and "step_id" in payload:
            payload["task_id"] = payload.pop("step_id")
        if "task_file" not in payload and "step_file" in payload:
            payload["task_file"] = payload.pop("step_file")
        payload.setdefault("stream", "ops")
        payload.setdefault("effect", "write")
        return cls(**payload)


@dataclass
class OperationHistory:
    """History manager for undo/redo operations."""

    storage_dir: Path
    operations: List[Operation] = field(default_factory=list)
    current_index: int = -1  # Points to last executed operation
    audit_operations: List[Operation] = field(default_factory=list)

    def __post_init__(self):
        self.storage_dir = Path(self.storage_dir)
        self._ensure_dirs()
        self._load()
        self._load_audit()

    def _ensure_dirs(self) -> None:
        """Ensure storage directories exist."""
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        (self.storage_dir / SNAPSHOT_DIR).mkdir(exist_ok=True)

    @property
    def _history_path(self) -> Path:
        return self.storage_dir / HISTORY_FILE

    @property
    def _audit_path(self) -> Path:
        return self.storage_dir / AUDIT_FILE

    @property
    def _snapshots_dir(self) -> Path:
        return self.storage_dir / SNAPSHOT_DIR

    def _load(self) -> None:
        """Load history from disk."""
        if self._history_path.exists():
            try:
                data = json.loads(self._history_path.read_text(encoding="utf-8"))
                self.operations = [Operation.from_dict(op) for op in data.get("operations", [])]
                self.current_index = data.get("current_index", len(self.operations) - 1)
            except (json.JSONDecodeError, KeyError):
                self.operations = []
                self.current_index = -1

    def _load_audit(self) -> None:
        """Load audit trail from disk (non-undoable preview/read operations)."""
        if self._audit_path.exists():
            try:
                data = json.loads(self._audit_path.read_text(encoding="utf-8"))
                ops = data.get("operations", [])
                if not isinstance(ops, list):
                    raise ValueError("operations must be a list")
                self.audit_operations = [Operation.from_dict(op) for op in ops]
            except Exception:
                self.audit_operations = []

    def _save(self) -> None:
        """Save history to disk."""
        data = {
            "operations": [op.to_dict() for op in self.operations],
            "current_index": self.current_index,
            "updated_at": datetime.now().isoformat(),
        }
        self._history_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    def _save_audit(self) -> None:
        """Save audit trail to disk."""
        data = {
            "operations": [op.to_dict() for op in self.audit_operations],
            "updated_at": datetime.now().isoformat(),
        }
        self._audit_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _generate_id(self, *, stream: str, seq: int) -> str:
        """Generate unique operation ID."""
        return hashlib.sha256(
            f"{stream}:{time.time()}-{seq}".encode()
        ).hexdigest()[:12]

    def _create_snapshot(self, task_file: Path) -> Optional[str]:
        """Create snapshot of task file before modification."""
        if not task_file.exists():
            return None

        # Use nanoseconds for unique IDs (avoid collision in same millisecond)
        snapshot_id = f"{task_file.stem}-{time.time_ns()}"
        snapshot_path = self._snapshots_dir / f"{snapshot_id}.task"
        shutil.copy2(task_file, snapshot_path)
        return snapshot_id

    def _restore_snapshot(self, snapshot_id: str, task_file: Path) -> bool:
        """Restore task file from snapshot."""
        snapshot_path = self._snapshots_dir / f"{snapshot_id}.task"
        if not snapshot_path.exists():
            return False

        shutil.copy2(snapshot_path, task_file)
        return True

    def _cleanup_old_snapshots(self) -> None:
        """Remove snapshots older than history."""
        if len(self.operations) <= MAX_HISTORY_SIZE:
            return

        # Get snapshot IDs still in use (both before and after)
        active_snapshots = set()
        for op in self.operations:
            if op.snapshot_id:
                active_snapshots.add(op.snapshot_id)
            if op.after_snapshot_id:
                active_snapshots.add(op.after_snapshot_id)

        # Remove orphaned snapshots
        for snapshot in self._snapshots_dir.glob("*.task"):
            snapshot_id = snapshot.stem
            if snapshot_id not in active_snapshots:
                snapshot.unlink(missing_ok=True)

    def snapshot(self, task_file: Path) -> Optional[str]:
        """Create a snapshot for a task file (best-effort)."""
        return self._create_snapshot(Path(task_file))

    def record(
        self,
        intent: str,
        task_id: Optional[str],
        data: Dict[str, Any],
        task_file: Optional[Path] = None,
        result: Optional[Dict[str, Any]] = None,
        *,
        stream: str = "ops",
        effect: str = "write",
        before_snapshot_id: Optional[str] = None,
        take_snapshot: bool = True,
    ) -> Operation:
        """Record a new operation.

        Args:
            intent: Operation intent (decompose, define, etc.)
            task_id: Task ID being modified
            data: Original request data
            task_file: Path to task file for snapshot
            result: Operation result

        Returns:
            The recorded operation
        """
        stream_norm = str(stream or "ops").strip().lower()
        if stream_norm not in {"ops", "audit"}:
            stream_norm = "ops"
        if stream_norm == "audit":
            # Audit trail is non-undoable and snapshot-free by design.
            operation = Operation(
                id=self._generate_id(stream="audit", seq=len(self.audit_operations)),
                timestamp=time.time(),
                intent=intent,
                task_id=task_id,
                data=data,
                stream="audit",
                effect=str(effect or "read").strip().lower() or "read",
                task_file=None,
                snapshot_id=None,
                result=result,
                undone=False,
            )
            self.audit_operations.append(operation)
            if len(self.audit_operations) > MAX_HISTORY_SIZE:
                self.audit_operations = self.audit_operations[-MAX_HISTORY_SIZE:]
            self._save_audit()
            return operation

        snapshot_id = before_snapshot_id
        task_file_rel: Optional[str] = None
        if task_file:
            try:
                task_file_rel = str(Path(task_file).resolve().relative_to(self.storage_dir.resolve()))
            except Exception:
                task_file_rel = None
            if take_snapshot and snapshot_id is None:
                snapshot_id = self._create_snapshot(task_file)

        # Truncate any redo history
        if self.current_index < len(self.operations) - 1:
            self.operations = self.operations[:self.current_index + 1]

        # Create operation
        operation = Operation(
            id=self._generate_id(stream="ops", seq=len(self.operations)),
            timestamp=time.time(),
            intent=intent,
            task_id=task_id,
            data=data,
            stream="ops",
            effect=str(effect or "write").strip().lower() or "write",
            task_file=task_file_rel,
            snapshot_id=snapshot_id,
            result=result,
        )

        self.operations.append(operation)
        self.current_index = len(self.operations) - 1

        # Trim old history
        if len(self.operations) > MAX_HISTORY_SIZE:
            self.operations = self.operations[-MAX_HISTORY_SIZE:]
            self.current_index = len(self.operations) - 1

        self._cleanup_old_snapshots()
        self._save()

        return operation

    def _resolve_task_file(self, tasks_dir: Path, operation: Operation) -> Optional[Path]:
        """Resolve task file path for undo/redo, honoring stored domain paths."""
        if operation.task_file:
            base = tasks_dir.resolve()
            candidate = (base / operation.task_file).resolve()
            if not candidate.is_relative_to(base):
                return None
            return candidate
        if not operation.task_id:
            return None
        candidate = tasks_dir / f"{operation.task_id}.task"
        return candidate.resolve()

    def can_undo(self) -> bool:
        """Check if undo is possible."""
        return self.current_index >= 0 and not self.operations[self.current_index].undone

    def can_redo(self) -> bool:
        """Check if redo is possible."""
        return (
            self.current_index < len(self.operations) - 1 or
            (self.current_index >= 0 and self.operations[self.current_index].undone)
        )

    def get_undo_operation(self) -> Optional[Operation]:
        """Get operation to undo (without performing undo)."""
        if not self.can_undo():
            return None
        return self.operations[self.current_index]

    def get_redo_operation(self) -> Optional[Operation]:
        """Get operation to redo (without performing redo)."""
        if not self.can_redo():
            return None
        if self.operations[self.current_index].undone:
            return self.operations[self.current_index]
        return self.operations[self.current_index + 1]

    def undo(self, tasks_dir: Path) -> Tuple[bool, Optional[str], Optional[Operation]]:
        """Undo the last operation.

        Args:
            tasks_dir: Directory containing task files

        Returns:
            Tuple of (success, error_message, undone_operation)
        """
        if not self.can_undo():
            return False, "Нечего отменять", None

        operation = self.operations[self.current_index]

        # Create-like operations: no "before" snapshot (file didn't exist). Undo is deletion with a redo snapshot.
        if operation.snapshot_id is None and operation.task_id and operation.intent in {"create", "scaffold"}:
            task_file = self._resolve_task_file(Path(tasks_dir), operation)
            if not task_file:
                return False, "Не удалось определить файл задачи для undo", None
            if not task_file.exists():
                return False, "Файл задачи для undo не найден", None
            task_file.parent.mkdir(parents=True, exist_ok=True)
            operation.after_snapshot_id = self._create_snapshot(task_file)
            try:
                task_file.unlink()
            except Exception:
                return False, "Не удалось удалить файл задачи для undo", None
            operation.undone = True
            self.current_index -= 1
            self._save()
            return True, None, operation

        # Restore from snapshot
        if operation.snapshot_id and operation.task_id:
            task_file = self._resolve_task_file(tasks_dir, operation)
            if not task_file:
                return False, "Не удалось определить файл шага для undo", None
            task_file.parent.mkdir(parents=True, exist_ok=True)
            # Save current state as "after" snapshot for redo
            if task_file.exists():
                operation.after_snapshot_id = self._create_snapshot(task_file)
            if not self._restore_snapshot(operation.snapshot_id, task_file):
                return False, f"Снимок {operation.snapshot_id} не найден", None

        operation.undone = True
        self.current_index -= 1
        self._save()

        return True, None, operation

    def redo(self, tasks_dir: Path) -> Tuple[bool, Optional[str], Optional[Operation]]:
        """Redo the last undone operation.

        Restores the "after" snapshot created during undo.

        Args:
            steps_dir: Directory containing step files

        Returns:
            Tuple of (success, error_message, operation_to_redo)
        """
        if not self.can_redo():
            return False, "Нечего повторять", None

        # Determine which operation should be redone and advance the pointer.
        # After undo, current_index points to the last *executed* operation and can be -1.
        if self.current_index < 0:
            next_index = 0
        elif self.current_index < len(self.operations) - 1:
            next_index = self.current_index + 1
        else:
            next_index = self.current_index

        operation = self.operations[next_index]
        if not operation.undone:
            return False, "Нечего повторять", None
        self.current_index = next_index

        # Restore from after snapshot
        if operation.after_snapshot_id and operation.task_id:
            task_file = self._resolve_task_file(tasks_dir, operation)
            if not task_file:
                return False, "Не удалось определить файл шага для redo", None
            task_file.parent.mkdir(parents=True, exist_ok=True)
            if not self._restore_snapshot(operation.after_snapshot_id, task_file):
                return False, f"Снимок {operation.after_snapshot_id} не найден", None

        operation.undone = False
        self._save()

        return True, None, operation

    def list_recent(self, limit: int = 10) -> List[Operation]:
        """List recent operations."""
        start = max(0, len(self.operations) - limit)
        return self.operations[start:]

    def list_recent_audit(self, limit: int = 10) -> List[Operation]:
        """List recent audit operations."""
        start = max(0, len(self.audit_operations) - limit)
        return self.audit_operations[start:]

    def clear(self) -> None:
        """Clear all history."""
        self.operations = []
        self.current_index = -1

        # Remove all snapshots
        for snapshot in self._snapshots_dir.glob("*.task"):
            snapshot.unlink(missing_ok=True)

        self._save()
        self.audit_operations = []
        self._save_audit()


def get_global_storage_dir() -> Path:
    """Return canonical global storage root (~/.tasks)."""
    return Path.home() / ".tasks"


def _git_remote_url_from_config(project_dir: Path) -> Optional[str]:
    """Best-effort read of remote origin URL from .git/config (no git subprocess)."""
    git_config = Path(project_dir) / ".git" / "config"
    if not git_config.exists():
        return None
    try:
        content = git_config.read_text(encoding="utf-8")
    except Exception:
        return None

    current_remote: Optional[str] = None
    remotes: Dict[str, str] = {}
    for raw in content.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            current_remote = None
            if line.lower().startswith('[remote "') and line.endswith('"]'):
                current_remote = line[len('[remote "') : -len('"]')]
            continue
        if current_remote and line.startswith("url = "):
            remotes.setdefault(current_remote, line.split("url = ", 1)[1].strip())

    return remotes.get("origin") or (next(iter(remotes.values()), None) if remotes else None)


def _namespace_from_remote_url(url: Optional[str]) -> Optional[str]:
    raw = (url or "").strip()
    if not raw:
        return None

    path = ""
    try:
        if "://" in raw:
            parsed = urlparse(raw)
            path = parsed.path or ""
        elif "@" in raw and ":" in raw:
            # scp-like form: git@github.com:owner/repo.git
            path = raw.split(":", 1)[1]
        else:
            path = raw
    except Exception:
        path = raw

    parts = [p for p in path.strip("/").split("/") if p]
    if len(parts) < 2:
        return None

    owner, repo = parts[-2], parts[-1]
    if repo.endswith(".git"):
        repo = repo[: -len(".git")]
    if not owner or not repo:
        return None
    return f"{owner}_{repo}"


def get_project_namespace(project_dir: Path) -> str:
    """Derive project namespace from git remote (preferred) or folder name."""
    project_dir = Path(project_dir)
    remote = _git_remote_url_from_config(project_dir)
    ns = _namespace_from_remote_url(remote)
    return ns or project_dir.name


def get_project_tasks_dir(project_dir: Path, *, use_global: bool = True) -> Path:
    """Return canonical tasks directory for a project."""
    project_dir = Path(project_dir)
    if not use_global:
        return project_dir / ".tasks"
    return get_global_storage_dir() / get_project_namespace(project_dir)


def migrate_to_global(project_dir: Path) -> Tuple[bool, str]:
    """Move tasks from <project>/.tasks to ~/.tasks/<namespace>."""
    project_dir = Path(project_dir)
    local_dir = project_dir / ".tasks"
    if not local_dir.exists():
        return False, "Локальная папка .tasks не найдена"

    global_dir = get_project_tasks_dir(project_dir, use_global=True)
    global_dir.mkdir(parents=True, exist_ok=True)

    moved = 0
    for src in sorted(local_dir.rglob("*")):
        if src.is_dir():
            continue
        rel = src.relative_to(local_dir)
        dst = (global_dir / rel)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            # Do not overwrite: keep existing global file.
            continue
        shutil.move(str(src), str(dst))
        moved += 1

    # Best-effort cleanup of empty directories
    try:
        for d in sorted([p for p in local_dir.rglob("*") if p.is_dir()], reverse=True):
            d.rmdir()
        local_dir.rmdir()
    except Exception:
        pass

    return True, f"Перенесено файлов: {moved}"


__all__ = [
    "Operation",
    "OperationHistory",
    "MAX_HISTORY_SIZE",
    "get_global_storage_dir",
    "get_project_namespace",
    "get_project_tasks_dir",
    "migrate_to_global",
]
