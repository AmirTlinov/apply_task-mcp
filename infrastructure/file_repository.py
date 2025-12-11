import time
from pathlib import Path
from typing import List, Optional, Tuple

from core import TaskDetail
from application.ports import TaskRepository
from infrastructure.task_file_parser import TaskFileParser


class FileTaskRepository(TaskRepository):
    def __init__(self, tasks_dir: Path | None):
        if tasks_dir is None:
            # Use global storage ~/.tasks/<namespace>/ (or APPLY_TASK_TASKS_DIR if set)
            from core.desktop.devtools.interface.tasks_dir_resolver import get_tasks_dir_for_project
            self.tasks_dir = get_tasks_dir_for_project(use_global=True)
        else:
            self.tasks_dir = tasks_dir

    def _resolve_path(self, task_id: str, domain: str = "") -> Path:
        if self.tasks_dir is None:
            raise ValueError("tasks_dir is not set for FileTaskRepository")
        # SEC: Validate task_id against path traversal
        if ".." in task_id or "/" in task_id or "\\" in task_id:
            raise ValueError(f"Invalid task_id: contains path traversal characters: {task_id}")
        if domain and (".." in domain or domain.startswith("/") or "\\" in domain):
            raise ValueError(f"Invalid domain: contains path traversal characters: {domain}")
        base = self.tasks_dir / domain if domain else self.tasks_dir
        resolved = (base / f"{task_id}.task").resolve()
        # SEC: Ensure resolved path is within tasks_dir
        if not resolved.is_relative_to(self.tasks_dir.resolve()):
            raise ValueError(f"Path traversal detected: {resolved} is outside {self.tasks_dir}")
        return resolved

    def _assign_domain(self, task: TaskDetail, path: Path) -> None:
        if not task.domain:
            try:
                rel = path.parent.relative_to(self.tasks_dir)
                task.domain = "" if str(rel) == "." else rel.as_posix()
            except Exception:
                task.domain = ""

    def load(self, task_id: str, domain: str = "") -> Optional[TaskDetail]:
        """Load a task by ID, optionally from a specific domain buffer."""
        path = self._resolve_path(task_id, domain)
        with open("/tmp/debug_mcp.log", "a") as f:
             f.write(f"[_repo_load] task_id={task_id}, domain={domain}, resolved_path={path}\n")
             
        if path.exists():
            task = TaskFileParser.parse(path)
            if task:
                self._assign_domain(task, path)
            return task

        # Fallback: search everywhere
        with open("/tmp/debug_mcp.log", "a") as f:
             f.write(f"[_repo_load] Path not found. Trying rglob for {task_id}.task in {self.tasks_dir}\n")
             
        # Case-insensitive search using rglob is tricky, but we assume exact match for now
        # or use glob pattern
        candidates = list(self.tasks_dir.rglob(f"{task_id}.task"))
        
        with open("/tmp/debug_mcp.log", "a") as f:
             f.write(f"[_repo_load] Candidates found: {candidates}\n")
             
        if not candidates:
            return None
        
        # Iterate through candidates to find the first parsable one
        # This handles cases where duplicates exist and one might be corrupt/invalid
        for candidate in candidates:
             try:
                 task = TaskFileParser.parse(candidate)
                 if task:
                     self._assign_domain(task, candidate)
                     with open("/tmp/debug_mcp.log", "a") as f:
                        f.write(f"[_repo_load] Loaded parsing candidate: {candidate}\n")
                     return task
             except Exception as e:
                 with open("/tmp/debug_mcp.log", "a") as f:
                    f.write(f"[_repo_load] Failed to parse candidate {candidate}: {e}\n")
                 continue
        
        return None

    def save(self, task: TaskDetail) -> None:
        path = self._resolve_path(task.id, task.domain)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(task.to_file_content(), encoding="utf-8")

    def list(self, domain_path: str = "", skip_sync: bool = False) -> List[TaskDetail]:
        root = self.tasks_dir / domain_path if domain_path else self.tasks_dir
        tasks: List[TaskDetail] = []
        for file in root.rglob("TASK-*.task"):
            # Skip snapshots directory
            if ".snapshots" in file.parts:
                continue
            parsed = TaskFileParser.parse(file)
            if parsed:
                self._assign_domain(parsed, file)
                tasks.append(parsed)
        return tasks

    def compute_signature(self) -> int:
        sig = 0
        for f in self.tasks_dir.rglob("TASK-*.task"):
            if ".snapshots" in f.parts:
                continue
            try:
                sig ^= int(f.stat().st_mtime_ns)
            except OSError:
                continue
        return sig if sig else int(time.time_ns())

    def next_id(self) -> str:
        ids = []
        for f in self.tasks_dir.rglob("TASK-*.task"):
            if ".snapshots" in f.parts:
                continue
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
            candidates = [f for f in self.tasks_dir.rglob(f"{task_id}.task") if ".snapshots" not in f.parts]
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

    def move_glob(self, pattern: str, new_domain: str) -> int:
        moved = 0
        for file in self.tasks_dir.rglob("TASK-*.task"):
            if ".snapshots" in file.parts:
                continue
            try:
                rel = file.relative_to(self.tasks_dir)
            except Exception:
                rel = file
            if rel.match(pattern):
                tid = file.stem
                if self.move(tid, new_domain, current_domain=str(rel.parent)) or self.move(tid, new_domain):
                    moved += 1
        return moved

    def delete_glob(self, pattern: str) -> int:
        removed = 0
        for file in self.tasks_dir.rglob("TASK-*.task"):
            if ".snapshots" in file.parts:
                continue
            try:
                rel = file.relative_to(self.tasks_dir)
            except Exception:
                rel = file
            if rel.match(pattern):
                try:
                    file.unlink()
                    removed += 1
                except OSError:
                    continue
        return removed

    def clean_filtered(self, tag: str = "", status: str = "", phase: str = "") -> Tuple[List[str], int]:
        matched: list[str] = []
        removed = 0
        norm_tag = tag.strip().lower() if tag else ""
        norm_status = status.strip().upper() if status else ""
        norm_phase = phase.strip().lower() if phase else ""

        for file in self.tasks_dir.rglob("TASK-*.task"):
            if ".snapshots" in file.parts:
                continue
            parsed = TaskFileParser.parse(file)
            if not parsed:
                continue
            tags = [t.strip().lower() for t in (parsed.tags or [])]
            if norm_tag and norm_tag not in tags:
                continue
            if norm_status and (parsed.status or "").upper() != norm_status:
                continue
            if norm_phase and (parsed.phase or "").strip().lower() != norm_phase:
                continue
            matched.append(parsed.id)
            try:
                file.unlink()
                removed += 1
            except OSError:
                continue
        return matched, removed
