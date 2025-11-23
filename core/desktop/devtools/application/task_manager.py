"""Application-level task management service."""

from __future__ import annotations

import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from application.ports import TaskRepository
from application.sync_service import SyncService
from core.desktop.devtools.interface.constants import TIMESTAMP_FORMAT
from core import TaskDetail, SubTask
from core.desktop.devtools.application.context import derive_domain_explicit
from core.desktop.devtools.interface.i18n import translate, effective_lang as _effective_lang
from infrastructure.file_repository import FileTaskRepository
from infrastructure.projects_sync_service import ProjectsSyncService
from projects_sync import get_projects_sync


def current_timestamp() -> str:
    """Returns local time with minute precision for task metadata."""
    return datetime.now().strftime(TIMESTAMP_FORMAT)


def _flatten_subtasks(subtasks: List[SubTask], prefix: str = "") -> List[Tuple[str, SubTask]]:
    flat: List[Tuple[str, SubTask]] = []
    for idx, st in enumerate(subtasks):
        path = f"{prefix}.{idx}" if prefix else str(idx)
        flat.append((path, st))
        flat.extend(_flatten_subtasks(st.children, path))
    return flat


def _find_subtask_by_path(subtasks: List[SubTask], path: str) -> Tuple[Optional[SubTask], Optional[SubTask], Optional[int]]:
    parts_raw = [p for p in path.split(".") if p.strip() != ""]
    if not parts_raw:
        return None, None, None
    try:
        parts = [int(p) for p in parts_raw]
    except ValueError:
        return None, None, None
    current_list = subtasks
    parent_node = None
    for pos, idx in enumerate(parts):
        if idx < 0 or idx >= len(current_list):
            return None, None, None
        target = current_list[idx]
        if pos == len(parts) - 1:
            return target, parent_node, idx
        parent_node = target
        current_list = target.children
    return None, None, None


def _attach_subtask(subtasks: List[SubTask], parent_path: Optional[str], new_subtask: SubTask) -> bool:
    if not parent_path:
        subtasks.append(new_subtask)
        return True
    parent, _, _ = _find_subtask_by_path(subtasks, parent_path)
    if not parent:
        return False
    parent.children.append(new_subtask)
    return True


class TaskManager:
    def __init__(
        self,
        tasks_dir: Path = Path(".tasks"),
        repository: Optional[TaskRepository] = None,
        sync_service: Optional[SyncService] = None,
        sync_provider=None,
    ):
        self.tasks_dir = tasks_dir
        self.tasks_dir.mkdir(exist_ok=True)
        self.repo: TaskRepository = repository or FileTaskRepository(tasks_dir)
        # sync_provider оставлен для обратной совместимости; sync_service — основной путь
        provider = sync_provider
        if provider is None:
            tasks_module = sys.modules.get("tasks")
            provider = getattr(tasks_module, "get_projects_sync", get_projects_sync)
        base_sync = sync_service or ProjectsSyncService(provider())
        self.sync_service: SyncService = base_sync
        self.config = self.load_config()
        self.language = _effective_lang()
        self.auto_sync_message = ""
        self.last_sync_error = ""
        synced = self._auto_sync_all()
        if synced:
            self.auto_sync_message = translate("STATUS_MESSAGE_AUTO_SYNC", lang=self.language, count=synced)

    def _t(self, key: str, **kwargs) -> str:
        return translate(key, lang=getattr(self, "language", "en"), **kwargs)

    @staticmethod
    def sanitize_domain(domain: Optional[str]) -> str:
        """Безопасная нормализация подпапки внутри .tasks"""
        if not domain:
            return ""
        candidate = Path(domain.strip("/"))
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError(translate("ERR_INVALID_FOLDER"))
        return candidate.as_posix()

    @staticmethod
    def load_config() -> Dict:
        cfg = Path(".apply_task_projects.yaml")
        if cfg.exists():
            try:
                raw = yaml.safe_load(cfg.read_text()) or {}
            except Exception:
                return {}
            project = (raw.get("project") or {}) if isinstance(raw, dict) else {}
            return {"auto_sync": project.get("enabled", True)}
        return {}

    def _next_id(self) -> str:
        try:
            return self.repo.next_id()
        except Exception:
            ids = []
            for f in self.tasks_dir.rglob("TASK-*.task"):
                try:
                    ids.append(int(f.stem.split("-")[1]))
                except (IndexError, ValueError):
                    continue
            return f"TASK-{(max(ids) + 1 if ids else 1):03d}"

    def create_task(
        self,
        title: str,
        status: str = "FAIL",
        priority: str = "MEDIUM",
        parent: Optional[str] = None,
        domain: str = "",
        phase: str = "",
        component: str = "",
        folder: Optional[str] = None,
    ) -> TaskDetail:
        domain = self.sanitize_domain(folder or domain or derive_domain_explicit("", phase, component))
        now_value = current_timestamp()
        task = TaskDetail(
            id=self._next_id(),
            title=title,
            status=status,
            domain=domain,
            phase=phase,
            component=component,
            parent=parent,
            priority=priority,
            created=now_value,
            updated=now_value,
        )
        # НЕ сохраняем здесь - валидация должна пройти первой
        return task

    def save_task(self, task: TaskDetail) -> None:
        task.updated = current_timestamp()
        prog = task.calculate_progress()
        if prog == 100 and not task.blocked:
            task.status = "OK"
        task.domain = self.sanitize_domain(task.domain)
        self.repo.save(task)
        sync = self.sync_service
        if sync.enabled:
            changed = bool(sync.sync_task(task))
            if getattr(task, "_sync_error", None):
                self._report_sync_error(task._sync_error)
                task._sync_error = None
            if changed:
                self.repo.save(task)

    def load_task(self, task_id: str, domain: str = "") -> Optional[TaskDetail]:
        task = self.repo.load(task_id, domain)
        if not task:
            return None
        if task.subtasks:
            prog = task.calculate_progress()
            if prog == 100 and not task.blocked and task.status != "OK":
                task.status = "OK"
                self.save_task(task)
        sync = self.sync_service
        if sync.enabled and task.project_item_id:
            sync.pull_task_fields(task)
        return task

    def _report_sync_error(self, message: str) -> None:
        logging.getLogger("apply_task.sync").warning(message)
        self.last_sync_error = f"SYNC ERROR: {message[:60]}"

    def list_tasks(self, domain: str = "", skip_sync: bool = False) -> List[TaskDetail]:
        tasks: List[TaskDetail] = self.repo.list(domain, skip_sync=skip_sync)
        for parsed in tasks:
            if parsed.subtasks:
                prog = parsed.calculate_progress()
                if prog == 100 and not parsed.blocked and parsed.status != "OK":
                    parsed.status = "OK"
                    self.save_task(parsed)
            if not skip_sync:
                sync = self.sync_service
                if sync.enabled and parsed.project_item_id:
                    sync.pull_task_fields(parsed)
        return sorted(tasks, key=lambda t: t.id)

    def _auto_sync_all(self) -> int:
        if not self.config.get("auto_sync", True):
            return 0
        base_sync = self.sync_service
        if not base_sync.enabled or getattr(base_sync, "_full_sync_done", False):
            return 0
        setattr(base_sync, "_full_sync_done", True)
        tasks_to_sync: List[Tuple[TaskDetail, Path]] = [(t, Path(t.filepath)) for t in self.repo.list("", skip_sync=True)]
        if not tasks_to_sync:
            return 0

        def worker(entry: Tuple[TaskDetail, Path]) -> bool:
            task, file_path = entry
            sync = self._make_parallel_sync(base_sync)
            changed = sync.sync_task(task) if sync.enabled else False
            if getattr(task, "_sync_error", None):
                self._report_sync_error(task._sync_error)
                task._sync_error = None
            if changed:
                file_path.write_text(task.to_file_content(), encoding="utf-8")
            return changed

        max_workers = self._compute_worker_count(len(tasks_to_sync))
        changed_count = 0
        tasks_module = sys.modules.get("tasks")
        executor_cls = getattr(tasks_module, "ThreadPoolExecutor", ThreadPoolExecutor)
        as_completed_fn = getattr(tasks_module, "as_completed", as_completed)
        with executor_cls(max_workers=max_workers) as executor:
            futures = [executor.submit(worker, t) for t in tasks_to_sync]
            for f in as_completed_fn(futures):
                try:
                    if f.result():
                        changed_count += 1
                except Exception as exc:  # pragma: no cover - safety
                    logging.getLogger("apply_task.sync").warning("Auto-sync worker failed: %s", exc)
        if changed_count:
            base_sync.last_push = datetime.now().strftime(TIMESTAMP_FORMAT)
        return changed_count

    def _make_parallel_sync(self, base_sync):
        """Сохраняем совместимость с тестами: возвращаем клон сервиса."""
        return base_sync.clone() if hasattr(base_sync, "clone") else base_sync

    def _compute_worker_count(self, queue_size: int) -> int:
        env_override = os.getenv("APPLY_TASK_SYNC_WORKERS")
        if env_override and env_override.isdigit():
            value = int(env_override)
            if value > 0:
                return value
        cfg_workers = getattr(self.sync_service.config, "workers", None)
        if isinstance(cfg_workers, int) and cfg_workers > 0:
            return min(cfg_workers, queue_size or 1)
        return min(4, max(1, queue_size))

    def compute_signature(self) -> int:
        return self.repo.compute_signature()

    def update_task_status(self, task_id: str, status: str, domain: str = "") -> Tuple[bool, Optional[Dict[str, str]]]:
        task = self.load_task(task_id, domain)
        if not task:
            return False, {"code": "not_found", "message": self._t("ERR_TASK_NOT_FOUND", task_id=task_id)}
        if status == "OK":
            if task.subtasks and task.calculate_progress() < 100:
                return False, {"code": "validation", "message": self._t("ERR_TASK_NOT_COMPLETE")}
            if not task.success_criteria:
                return False, {"code": "validation", "message": self._t("ERR_TASK_NO_CRITERIA_TESTS")}
            for idx, st in enumerate(task.subtasks, 1):
                if not st.success_criteria:
                    return False, {
                        "code": "validation",
                        "message": self._t("ERR_SUBTASK_NO_CRITERIA").format(idx=idx, title=st.title),
                    }
                if not st.tests:
                    return False, {
                        "code": "validation",
                        "message": self._t("ERR_SUBTASK_NO_TESTS").format(idx=idx, title=st.title),
                    }
            task.progress = 100
        else:
            task.status = status
            if status == "WARN" and task.progress == 0 and task.subtasks:
                task.progress = task.calculate_progress()
            if status == "FAIL" and task.progress == 0 and task.subtasks:
                task.progress = task.calculate_progress()

        task.status = status
        self.save_task(task)
        return True, None

    def add_subtask(
        self,
        task_id: str,
        title: str,
        domain: str = "",
        criteria: Optional[List[str]] = None,
        tests: Optional[List[str]] = None,
        blockers: Optional[List[str]] = None,
        parent_path: Optional[str] = None,
    ) -> Tuple[bool, Optional[str]]:
        task = self.load_task(task_id, domain)
        if not task:
            return False, "not_found"
        crit = [c.strip() for c in (criteria or []) if c.strip()]
        tst = [t.strip() for t in (tests or []) if t.strip()]
        bl = [b.strip() for b in (blockers or []) if b.strip()]
        if not crit or not tst or not bl:
            return False, "missing_fields"
        if parent_path:
            ok = _attach_subtask(task.subtasks, parent_path, SubTask(False, title, crit, tst, bl))
            if not ok:
                return False, "path"
        else:
            task.subtasks.append(SubTask(False, title, crit, tst, bl))
        task.update_status_from_progress()
        self.save_task(task)
        return True, None

    def set_subtask(self, task_id: str, index: int, completed: bool, domain: str = "", path: Optional[str] = None) -> Tuple[bool, Optional[str]]:
        task = self.load_task(task_id, domain)
        if not task:
            return False, "not_found"
        if path:
            st, _, _ = _find_subtask_by_path(task.subtasks, path)
            if not st:
                return False, "index"
        else:
            if index < 0 or index >= len(task.subtasks):
                return False, "index"
            st = task.subtasks[index]
        if completed and not st.ready_for_completion():
            missing = []
            if not st.criteria_confirmed:
                missing.append(self._t("CHECKPOINT_CRITERIA"))
            if not st.tests_confirmed:
                missing.append(self._t("CHECKPOINT_TESTS"))
            if not st.blockers_resolved:
                missing.append(self._t("CHECKPOINT_BLOCKERS"))
            return False, self._t("ERR_SUBTASK_CHECKPOINTS").format(items=", ".join(missing))
        st.completed = completed
        task.update_status_from_progress()
        self.save_task(task)
        return True, None

    def update_subtask_checkpoint(
        self,
        task_id: str,
        index: int,
        checkpoint: str,
        value: bool,
        note: str = "",
        domain: str = "",
        path: Optional[str] = None,
    ) -> Tuple[bool, Optional[str]]:
        task = self.load_task(task_id, domain)
        if not task:
            return False, "not_found"
        if path:
            st, _, _ = _find_subtask_by_path(task.subtasks, path)
            if not st:
                return False, "index"
        else:
            if index < 0 or index >= len(task.subtasks):
                return False, "index"
            st = task.subtasks[index]
        checkpoint = checkpoint.lower()
        attr_map = {
            "criteria": ("criteria_confirmed", "criteria_notes"),
            "tests": ("tests_confirmed", "tests_notes"),
            "blockers": ("blockers_resolved", "blockers_notes"),
        }
        if checkpoint not in attr_map:
            return False, "unknown_checkpoint"
        flag_attr, notes_attr = attr_map[checkpoint]
        setattr(st, flag_attr, value)
        note = note.strip()
        if note:
            getattr(st, notes_attr).append(note)
        if not value:
            st.completed = False
        task.update_status_from_progress()
        self.save_task(task)
        return True, None

    def add_dependency(self, task_id: str, dep: str, domain: str = "") -> bool:
        task = self.load_task(task_id, domain)
        if not task:
            return False
        task.dependencies.append(dep)
        self.save_task(task)
        return True

    def move_task(self, task_id: str, new_domain: str) -> bool:
        target_domain = self.sanitize_domain(new_domain)
        return self.repo.move(task_id, target_domain)

    def move_glob(self, pattern: str, new_domain: str) -> int:
        target_domain = self.sanitize_domain(new_domain)
        return self.repo.move_glob(pattern, target_domain)

    def clean_tasks(self, tag: Optional[str] = None, status: Optional[str] = None, phase: Optional[str] = None, dry_run: bool = False) -> Tuple[List[str], int]:
        if dry_run:
            matched = [
                d.id
                for d in self.repo.list("", skip_sync=True)
                if (not tag or tag.strip().lower() in [t.lower() for t in d.tags])
                and (not status or (d.status or "").upper() == status.strip().upper())
                and (not phase or (d.phase or "").strip().lower() == phase.strip().lower())
            ]
            return matched, 0
        try:
            return self.repo.clean_filtered(tag or "", status or "", phase or "")
        except NotImplementedError:
            matched: List[str] = []
            removed = 0
            norm_tag = (tag or "").strip().lower()
            norm_status = (status or "").strip().upper()
            norm_phase = (phase or "").strip().lower()

            for detail in self.repo.list("", skip_sync=True):
                tags = [t.strip().lower() for t in (detail.tags or [])]
                if norm_tag and norm_tag not in tags:
                    continue
                if norm_status and (detail.status or "").upper() != norm_status:
                    continue
                if norm_phase and (detail.phase or "").strip().lower() != norm_phase:
                    continue
                matched.append(detail.id)
                if self.repo.delete(detail.id, detail.domain):
                    removed += 1
            return matched, removed

    def delete_task(self, task_id: str, domain: str = "") -> bool:
        return self.repo.delete(task_id, domain)
