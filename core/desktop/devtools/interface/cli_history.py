"""Operation history for undo/redo support.

Provides a centralized history of operations with snapshots
for rollback capability.
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

from core.desktop.devtools.interface.tasks_dir_resolver import (
    get_project_namespace,
    resolve_project_root,
)

# History configuration
MAX_HISTORY_SIZE = 100  # Maximum operations to keep
SNAPSHOT_DIR = ".snapshots"
HISTORY_FILE = ".history.json"


@dataclass
class Operation:
    """Single operation in history."""

    id: str
    timestamp: float
    intent: str
    task_id: Optional[str]
    data: Dict[str, Any]
    snapshot_id: Optional[str] = None  # Before operation (for undo)
    after_snapshot_id: Optional[str] = None  # After operation (for redo)
    result: Optional[Dict[str, Any]] = None
    undone: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Operation":
        return cls(**data)


@dataclass
class OperationHistory:
    """History manager for undo/redo operations."""

    storage_dir: Path
    operations: List[Operation] = field(default_factory=list)
    current_index: int = -1  # Points to last executed operation

    def __post_init__(self):
        self.storage_dir = Path(self.storage_dir)
        self._ensure_dirs()
        self._load()

    def _ensure_dirs(self) -> None:
        """Ensure storage directories exist."""
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        (self.storage_dir / SNAPSHOT_DIR).mkdir(exist_ok=True)

    @property
    def _history_path(self) -> Path:
        return self.storage_dir / HISTORY_FILE

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

    def _generate_id(self) -> str:
        """Generate unique operation ID."""
        return hashlib.sha256(
            f"{time.time()}-{len(self.operations)}".encode()
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

    def record(
        self,
        intent: str,
        task_id: Optional[str],
        data: Dict[str, Any],
        task_file: Optional[Path] = None,
        result: Optional[Dict[str, Any]] = None,
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
        # Create snapshot if task file provided
        snapshot_id = None
        if task_file:
            snapshot_id = self._create_snapshot(task_file)

        # Truncate any redo history
        if self.current_index < len(self.operations) - 1:
            self.operations = self.operations[:self.current_index + 1]

        # Create operation
        operation = Operation(
            id=self._generate_id(),
            timestamp=time.time(),
            intent=intent,
            task_id=task_id,
            data=data,
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

        # Restore from snapshot
        if operation.snapshot_id and operation.task_id:
            task_file = tasks_dir / f"{operation.task_id}.task"
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
            tasks_dir: Directory containing task files

        Returns:
            Tuple of (success, error_message, operation_to_redo)
        """
        if not self.can_redo():
            return False, "Нечего повторять", None

        if self.operations[self.current_index].undone:
            operation = self.operations[self.current_index]
        else:
            self.current_index += 1
            operation = self.operations[self.current_index]

        # Restore from after snapshot
        if operation.after_snapshot_id and operation.task_id:
            task_file = tasks_dir / f"{operation.task_id}.task"
            if not self._restore_snapshot(operation.after_snapshot_id, task_file):
                return False, f"Снимок {operation.after_snapshot_id} не найден", None

        operation.undone = False
        self._save()

        return True, None, operation

    def list_recent(self, limit: int = 10) -> List[Operation]:
        """List recent operations."""
        start = max(0, len(self.operations) - limit)
        return self.operations[start:]

    def clear(self) -> None:
        """Clear all history."""
        self.operations = []
        self.current_index = -1

        # Remove all snapshots
        for snapshot in self._snapshots_dir.glob("*.task"):
            snapshot.unlink(missing_ok=True)

        self._save()


def get_global_storage_dir() -> Path:
    """Get the global tasks storage directory.

    Returns ~/.tasks as the centralized storage location.
    """
    return Path.home() / ".tasks"


def get_project_tasks_dir(
    project_dir: Optional[Path] = None,
    use_global: bool = True,
) -> Path:
    """Get tasks directory for a project.

    Args:
        project_dir: Project directory (defaults to cwd)
        use_global: If True, use ~/.tasks/<namespace>; else use .tasks/

    Returns:
        Path to tasks directory
    """
    if project_dir is None:
        project_dir = resolve_project_root()

    if not use_global:
        return (project_dir / ".tasks").resolve()

    namespace = get_project_namespace(project_dir)
    return (Path.home() / ".tasks" / namespace).resolve()


def migrate_to_global(project_dir: Optional[Path] = None) -> Tuple[bool, str]:
    """Migrate local .tasks to global storage.

    Args:
        project_dir: Project directory with local .tasks

    Returns:
        Tuple of (success, message)
    """
    if project_dir is None:
        project_dir = Path.cwd()

    local_tasks = project_dir / ".tasks"
    if not local_tasks.exists():
        return False, "Локальная директория .tasks не найдена"

    # Use mocked global root if provided via get_global_storage_dir
    global_root = get_global_storage_dir()
    if not global_root.exists():
        global_root.mkdir(parents=True, exist_ok=True)
    namespace = get_project_namespace(project_dir)
    global_tasks = (global_root / namespace).resolve()
    global_tasks.parent.mkdir(parents=True, exist_ok=True)

    if global_tasks.exists():
        # Merge strategy: keep both, rename conflicts
        for task_file in local_tasks.glob("*.task"):
            dest = global_tasks / task_file.name
            if dest.exists():
                # Rename with timestamp
                new_name = f"{task_file.stem}-migrated-{int(time.time())}.task"
                dest = global_tasks / new_name
            shutil.copy2(task_file, dest)

        # Move config if exists
        local_config = local_tasks / "config.json"
        if local_config.exists():
            global_config = global_tasks / "config.json"
            if not global_config.exists():
                shutil.copy2(local_config, global_config)

        return True, f"Задачи смержены в {global_tasks}"
    else:
        # Simple move
        global_tasks.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(local_tasks, global_tasks)
        return True, f"Задачи перенесены в {global_tasks}"


__all__ = [
    "Operation",
    "OperationHistory",
    "get_global_storage_dir",
    "get_project_namespace",
    "get_project_tasks_dir",
    "migrate_to_global",
    "MAX_HISTORY_SIZE",
]
