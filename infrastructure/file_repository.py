import time
from pathlib import Path
from typing import List, Optional

from core import TaskDetail
from application.ports import TaskRepository


class FileTaskRepository(TaskRepository):
    def __init__(self, tasks_dir: Path):
        self.tasks_dir = tasks_dir

    def _resolve_path(self, task_id: str, domain: str = "") -> Path:
        base = self.tasks_dir / domain if domain else self.tasks_dir
        return (base / f"{task_id}.task").resolve()

    def _assign_domain(self, task: TaskDetail, path: Path) -> None:
        if not task.domain:
            try:
                rel = path.parent.relative_to(self.tasks_dir)
                task.domain = "" if str(rel) == "." else rel.as_posix()
            except Exception:
                task.domain = ""

    def load(self, task_id: str, domain: str = "") -> Optional[TaskDetail]:
        from tasks import TaskFileParser  # late import to avoid circular deps
        filepath = self._resolve_path(task_id, domain)
        if not filepath.exists():
            # fallback: search across domains
            candidates = list(self.tasks_dir.rglob(f"{task_id}.task"))
            filepath = candidates[0] if candidates else None
        if not filepath or not Path(filepath).exists():
            return None
        task = TaskFileParser.parse(filepath)
        if task:
            self._assign_domain(task, Path(filepath))
        return task

    def save(self, task: TaskDetail) -> None:
        path = self._resolve_path(task.id, task.domain)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(task.to_file_content(), encoding="utf-8")

    def list(self, domain_path: str = "", skip_sync: bool = False) -> List[TaskDetail]:
        from tasks import TaskFileParser  # late import to avoid circular deps
        root = self.tasks_dir / domain_path if domain_path else self.tasks_dir
        tasks: List[TaskDetail] = []
        for file in root.rglob("TASK-*.task"):
            parsed = TaskFileParser.parse(file)
            if parsed:
                self._assign_domain(parsed, file)
                tasks.append(parsed)
        return tasks

    def compute_signature(self) -> int:
        sig = 0
        for f in self.tasks_dir.rglob("TASK-*.task"):
            try:
                sig ^= int(f.stat().st_mtime_ns)
            except OSError:
                continue
        return sig if sig else int(time.time_ns())

    def next_id(self) -> str:
        ids = []
        for f in self.tasks_dir.rglob("TASK-*.task"):
            try:
                ids.append(int(f.stem.split("-")[1]))
            except (IndexError, ValueError):
                continue
        next_num = (max(ids) + 1) if ids else 1
        return f"TASK-{next_num:03d}"

    def delete(self, task_id: str, domain: str = "") -> bool:
        path = self._resolve_path(task_id, domain)
        candidates = [path]
        if not path.exists():
            candidates = list(self.tasks_dir.rglob(f"{task_id}.task"))
        deleted = False
        for candidate in candidates:
            try:
                candidate.unlink()
                deleted = True
            except OSError:
                continue
        return deleted

    def move(self, task_id: str, new_domain: str, current_domain: str = "") -> bool:
        task = self.load(task_id, current_domain)
        if not task:
            return False
        old_path = Path(task.filepath)
        task.domain = new_domain
        dest_path = self._resolve_path(task_id, new_domain)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        # переписываем с обновленной метой
        dest_path.write_text(task.to_file_content(), encoding="utf-8")
        if old_path.exists() and old_path != dest_path:
            try:
                old_path.unlink()
            except OSError:
                pass
        return True
