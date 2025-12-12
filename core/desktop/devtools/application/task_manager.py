"""Application-level task management service."""

from __future__ import annotations

import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

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
from core.status import task_status_code


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


def _validate_subtask_requirements(subtask: SubTask, idx: int, translator) -> Optional[Dict[str, str]]:
    if not subtask.success_criteria:
        return {
            "code": "validation",
            "message": translator("ERR_SUBTASK_NO_CRITERIA").format(idx=idx, title=subtask.title),
        }
    if not subtask.tests:
        return {
            "code": "validation",
            "message": translator("ERR_SUBTASK_NO_TESTS").format(idx=idx, title=subtask.title),
        }
    return None


def _validate_task_ready_for_ok(task: TaskDetail, translator) -> Tuple[bool, Optional[Dict[str, str]]]:
    if task.subtasks and task.calculate_progress() < 100:
        return False, {"code": "validation", "message": translator("ERR_TASK_NOT_COMPLETE")}
    if not task.success_criteria:
        return False, {"code": "validation", "message": translator("ERR_TASK_NO_CRITERIA_TESTS")}
    for idx, st in enumerate(task.subtasks, 1):
        err = _validate_subtask_requirements(st, idx, translator)
        if err:
            return False, err
    return True, None


def _normalized_fields(values: Optional[List[str]]) -> List[str]:
    return [v.strip() for v in (values or []) if v and v.strip()]


def _build_subtask(title: str, criteria, tests, blockers) -> Optional[SubTask]:
    """Build subtask with Normal mode validation.

    Normal mode:
    - criteria: REQUIRED (at least 1 item)
    - tests: optional (auto_confirmed if empty)
    - blockers: optional (auto_resolved if empty)

    Returns None only if criteria is empty.
    """
    crit = _normalized_fields(criteria)
    tst = _normalized_fields(tests)
    bl = _normalized_fields(blockers)

    # Normal mode: only criteria is required
    if not crit:
        return None

    return SubTask(
        completed=False,
        title=title,
        success_criteria=crit,
        tests=tst,
        blockers=bl,
        # Auto-confirmed flags based on whether fields were empty
        criteria_auto_confirmed=False,  # Never auto - criteria always required
        tests_auto_confirmed=not tst,   # Auto-OK if tests empty
        blockers_auto_resolved=not bl,  # Auto-OK if blockers empty
        created_at=current_timestamp(),
    )


def _update_progress_for_status(task: TaskDetail, status: str) -> None:
    task.status = status
    needs_progress = status in {"WARN", "FAIL"}
    if needs_progress and task.progress == 0 and task.subtasks:
        task.progress = task.calculate_progress()


def _auto_sync_allowed(sync_service, config) -> bool:
    if not config.get("auto_sync", True):
        return False
    if not getattr(sync_service, "enabled", False) or getattr(sync_service, "_full_sync_done", False):
        return False
    return True


def _matches_clean(detail: TaskDetail, norm_tag: str, norm_status: str, norm_phase: str) -> bool:
    tags = [t.strip().lower() for t in (detail.tags or [])]
    status_value = (detail.status or "").upper()
    phase_value = (detail.phase or "").strip().lower()
    return (
        (not norm_tag or norm_tag in tags)
        and (not norm_status or status_value == norm_status)
        and (not norm_phase or phase_value == norm_phase)
    )


def _clean_tasks_fallback(repo, matcher) -> Tuple[List[str], int]:
    matched: List[str] = []
    removed = 0
    for detail in repo.list("", skip_sync=True):
        if not matcher(detail):
            continue
        matched.append(detail.id)
        if repo.delete(detail.id, detail.domain):
            removed += 1
    return matched, removed


def _locate_subtask(task: TaskDetail, index: int, path: Optional[str]) -> Tuple[Optional["SubTask"], Optional[str]]:
    if path:
        st, _, _ = _find_subtask_by_path(task.subtasks, path)
        return st, None if st else "index"
    if index < 0 or index >= len(task.subtasks):
        return None, "index"
    return task.subtasks[index], None


def _subtask_completion_blockers(subtask: SubTask, translator) -> Optional[str]:
    """Check what's blocking subtask completion.

    Normal mode logic:
    - criteria: must be explicitly confirmed
    - tests: OK if confirmed OR auto_confirmed (empty at creation)
    - blockers: OK if resolved OR auto_resolved (empty at creation)
    """
    if subtask.ready_for_completion():
        return None
    missing = []
    if not subtask.criteria_confirmed:
        missing.append(translator("CHECKPOINT_CRITERIA"))
    # Account for auto_confirmed flags (Normal mode)
    if not (subtask.tests_confirmed or subtask.tests_auto_confirmed):
        missing.append(translator("CHECKPOINT_TESTS"))
    if not (subtask.blockers_resolved or subtask.blockers_auto_resolved):
        missing.append(translator("CHECKPOINT_BLOCKERS"))
    return translator("ERR_SUBTASK_CHECKPOINTS").format(items=", ".join(missing)) if missing else None


class TaskManager:
    def __init__(
        self,
        tasks_dir: Optional[Path] = None,
        repository: Optional[TaskRepository] = None,
        sync_service: Optional[SyncService] = None,
        sync_provider=None,
        auto_sync: bool = True,
    ):
        if tasks_dir is None:
            from core.desktop.devtools.interface.tasks_dir_resolver import get_tasks_dir_for_project
            # Prefer global; if it doesn't exist and local .tasks exists/needed, resolver will fall back
            self.tasks_dir = get_tasks_dir_for_project(use_global=True)
            self.tasks_dir.mkdir(parents=True, exist_ok=True)
        else:
            self.tasks_dir = tasks_dir
        self.tasks_dir.mkdir(exist_ok=True)
        self.repo: TaskRepository = repository or FileTaskRepository(self.tasks_dir)
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
        # Track tasks known to AI for detecting external changes
        self._known_tasks: Set[str] = set()
        # Store task snapshots for change detection: {task_id: (subtasks_count, progress, hash)}
        self._task_snapshots: Dict[str, Tuple[int, int, str]] = {}
        if auto_sync:
            synced = self._auto_sync_all()
            if synced:
                self.auto_sync_message = translate(
                    "STATUS_MESSAGE_AUTO_SYNC",
                    lang=self.language,
                    count=synced,
                )

    def _t(self, key: str, **kwargs) -> str:
        return translate(key, lang=getattr(self, "language", "en"), **kwargs)

    def _task_hash(self, task: TaskDetail) -> str:
        """Create a simple hash of task state for change detection."""
        # Hash based on key mutable fields
        parts = [
            task.title,
            task.status,
            str(getattr(task, "status_manual", False)),
            str(len(task.subtasks)),
            str(sum(1 for s in task.subtasks if s.completed)),
        ]
        # Add subtask titles and completion states
        for st in task.subtasks:
            parts.append(f"{st.title}:{st.completed}")
        return "|".join(parts)

    def track_task(self, task_id: str, task: Optional[TaskDetail] = None) -> None:
        """Register task as known to AI and snapshot its state."""
        self._known_tasks.add(task_id)
        if task:
            self._task_snapshots[task_id] = (
                len(task.subtasks),
                task.calculate_progress(),
                self._task_hash(task),
            )

    def check_external_changes(self) -> Dict[str, List[Dict[str, Any]]]:
        """Check for tasks created/modified/deleted externally (by user via TUI/CLI).

        Returns dict with keys: created_by_user, modified_by_user, deleted_by_user
        """
        result: Dict[str, List[Dict[str, Any]]] = {
            "created_by_user": [],
            "modified_by_user": [],
            "deleted_by_user": [],
        }

        current_tasks = {t.id: t for t in self.list_tasks()}
        current_ids = set(current_tasks.keys())

        # Deleted: was known but no longer exists
        deleted = self._known_tasks - current_ids
        for task_id in deleted:
            result["deleted_by_user"].append({"id": task_id})
            self._known_tasks.discard(task_id)
            self._task_snapshots.pop(task_id, None)

        # Created: exists but was not known (and AI didn't just create it)
        created = current_ids - self._known_tasks
        for task_id in created:
            task = current_tasks[task_id]
            result["created_by_user"].append({
                "id": task_id,
                "title": task.title,
                "subtasks_count": len(task.subtasks),
            })
            # Now track it
            self.track_task(task_id, task)

        # Modified: known task but state changed
        for task_id in self._known_tasks & current_ids:
            task = current_tasks[task_id]
            old_snapshot = self._task_snapshots.get(task_id)
            new_hash = self._task_hash(task)

            if old_snapshot and old_snapshot[2] != new_hash:
                old_subtasks, old_progress, _ = old_snapshot
                new_progress = task.calculate_progress()
                change_info: Dict[str, Any] = {
                    "id": task_id,
                    "title": task.title,
                }
                # Add specific changes
                if old_subtasks != len(task.subtasks):
                    change_info["subtasks_changed"] = f"{old_subtasks} -> {len(task.subtasks)}"
                if old_progress != new_progress:
                    change_info["progress_changed"] = f"{old_progress}% -> {new_progress}%"
                    # Determine if user marked something completed
                    if new_progress > old_progress:
                        change_info["user_completed_items"] = True
                result["modified_by_user"].append(change_info)
            # Update snapshot
            self._task_snapshots[task_id] = (
                len(task.subtasks),
                task.calculate_progress(),
                new_hash,
            )

        return result

    def get_and_clear_external_changes(self) -> Dict[str, List[Dict[str, Any]]]:
        """Get pending external change notifications."""
        changes = self.check_external_changes()
        # Filter out empty lists
        return {k: v for k, v in changes.items() if v}

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
        try:
            status = task_status_code(status or "FAIL")
        except ValueError:
            status = "FAIL"
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

    def save_task(self, task: TaskDetail, skip_sync: bool = False) -> None:
        task.updated = current_timestamp()
        prog = task.calculate_progress()
        task.progress = prog
        if not getattr(task, "status_manual", False) and prog == 100 and not task.blocked:
            task.status = "OK"
        task.domain = self.sanitize_domain(task.domain)
        self.repo.save(task)
        if not skip_sync:
            sync = self.sync_service
            if sync.enabled:
                changed = bool(sync.sync_task(task))
                if getattr(task, "_sync_error", None):
                    self._report_sync_error(task._sync_error)
                    task._sync_error = None
                if changed:
                    self.repo.save(task)

    def load_task(self, task_id: str, domain: str = "", skip_sync: bool = False) -> Optional[TaskDetail]:
        task = self.repo.load(task_id, domain)
        if not task:
            return None
        if task.subtasks:
            prog = task.calculate_progress()
            if not getattr(task, "status_manual", False) and prog == 100 and not task.blocked and task.status != "OK":
                task.status = "OK"
                self.save_task(task)
        if not skip_sync:
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
                if not getattr(parsed, "status_manual", False) and prog == 100 and not parsed.blocked and parsed.status != "OK":
                    parsed.status = "OK"
                    self.save_task(parsed, skip_sync=skip_sync)
            if not skip_sync:
                sync = self.sync_service
                if sync.enabled and parsed.project_item_id:
                    sync.pull_task_fields(parsed)
        return sorted(tasks, key=lambda t: t.id)

    def list_all_tasks(self, skip_sync: bool = True) -> List[TaskDetail]:
        """List all tasks across domains within current namespace."""
        return self.list_tasks("", skip_sync=skip_sync)

    def _auto_sync_all(self) -> int:
        base_sync = self.sync_service
        if not _auto_sync_allowed(base_sync, self.config):
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

    def _make_parallel_sync(self, base_sync: Any) -> Any:
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

    def update_task_status(self, task_id: str, status: str, domain: str = "", force: bool = False) -> Tuple[bool, Optional[Dict[str, str]]]:
        # skip_sync=True чтобы не перезаписать локальные изменения данными из GitHub
        task = self.load_task(task_id, domain, skip_sync=True)
        if not task:
            return False, {"code": "not_found", "message": self._t("ERR_TASK_NOT_FOUND", task_id=task_id)}
        try:
            status_code = task_status_code(status)
        except ValueError:
            return False, {"code": "invalid_status", "message": self._t("ERR_STATUS_REQUIRED")}

        if status_code == "OK":
            if not force:
                ok, error = _validate_task_ready_for_ok(task, self._t)
                if not ok:
                    return False, error
                task.progress = 100
                task.status_manual = False
            else:
                # keep actual progress/subtasks, but mark status as explicitly set
                task.progress = task.calculate_progress()
                task.status_manual = True
            task.status = status_code
        else:
            task.status_manual = False
            _update_progress_for_status(task, status_code)
            # When reopening task, ensure status change is not immediately auto-overridden by 100% progress.
            if task.subtasks and (force or task.calculate_progress() == 100):
                for st in task.subtasks:
                    st.completed = False
                    st.completed_at = None
        # skip_sync=True чтобы sync не перезаписал локальные изменения
        self.save_task(task, skip_sync=True)
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
        new_subtask = _build_subtask(title, criteria, tests, blockers)
        if not new_subtask:
            return False, "missing_fields"
        if not _attach_subtask(task.subtasks, parent_path, new_subtask):
            return False, "path"
        task.update_status_from_progress()
        self.save_task(task)
        return True, None

    def set_subtask(self, task_id: str, index: int, completed: bool, domain: str = "", path: Optional[str] = None, force: bool = False) -> Tuple[bool, Optional[str]]:
        # skip_sync=True чтобы не перезаписать локальные изменения данными из GitHub
        task = self.load_task(task_id, domain, skip_sync=True)
        if not task:
            return False, "not_found"
        st, error = _locate_subtask(task, index, path)
        if error:
            return False, error
        if completed and not force:
            blocker_msg = _subtask_completion_blockers(st, self._t)
            if blocker_msg:
                return False, blocker_msg
        st.completed = completed
        # Phase 1: Auto-set started_at when marking complete (if not already set)
        if completed and not st.started_at:
            st.started_at = current_timestamp()
        # Set completed_at timestamp when marking as done
        if completed:
            st.completed_at = current_timestamp()
        else:
            st.completed_at = None  # Clear when reopening
        task.update_status_from_progress()
        # skip_sync=True чтобы sync не перезаписал локальные изменения
        self.save_task(task, skip_sync=True)
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
        # skip_sync=True чтобы не перезаписать локальные изменения данными из GitHub
        task = self.load_task(task_id, domain, skip_sync=True)
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
        # Phase 1: Auto-set started_at when confirming checkpoint (indicates work started)
        if value and not st.started_at:
            st.started_at = current_timestamp()
        note = note.strip()
        if note:
            getattr(st, notes_attr).append(note)
        if not value:
            st.completed = False
        task.update_status_from_progress()
        # skip_sync=True чтобы sync не перезаписал локальные изменения
        self.save_task(task, skip_sync=True)
        return True, None

    def add_dependency(self, task_id: str, dep: str, domain: str = "") -> bool:
        """Add dependency to a task.

        - If `dep` looks like a TASK-ID, it is treated as blocking `depends_on`
          with existence/cycle validation.
        - Otherwise it is treated as a soft/freeform dependency and stored in
          `dependencies` (legacy/external refs).
        """
        import re
        from core import TaskEvent, validate_dependencies, build_dependency_graph
        from core.desktop.devtools.application.context import normalize_task_id

        task = self.load_task(task_id, domain)
        if not task:
            return False

        raw = (dep or "").strip()
        upper = raw.upper()
        is_task_id = bool(re.match(r"^TASK-\\d+$", upper) or upper.isdigit())

        if not is_task_id:
            if raw and raw not in task.dependencies:
                task.dependencies.append(raw)
                self.save_task(task)
            return True

        dep_id = normalize_task_id(raw)
        if dep_id == task.id:
            return False

        all_tasks = self.list_all_tasks()
        existing_ids = {t.id for t in all_tasks}
        dep_graph = build_dependency_graph([(t.id, t.depends_on) for t in all_tasks])
        errors, cycle = validate_dependencies(task.id, [dep_id], existing_ids, dep_graph)
        if errors:
            return False
        if cycle:
            return False

        if dep_id not in task.depends_on:
            task.depends_on.append(dep_id)
            task.events.append(TaskEvent.dependency_added(dep_id))
            self.save_task(task)

        return True

    def move_task(self, task_id: str, new_domain: str) -> bool:
        target_domain = self.sanitize_domain(new_domain)
        return self.repo.move(task_id, target_domain)

    def move_glob(self, pattern: str, new_domain: str) -> int:
        target_domain = self.sanitize_domain(new_domain)
        return self.repo.move_glob(pattern, target_domain)

    def clean_tasks(self, tag: Optional[str] = None, status: Optional[str] = None, phase: Optional[str] = None, dry_run: bool = False) -> Tuple[List[str], int]:
        norm_tag = (tag or "").strip().lower()
        norm_status = (status or "").strip().upper()
        norm_phase = (phase or "").strip().lower()

        if dry_run:
            matched = [d.id for d in self.repo.list("", skip_sync=True) if _matches_clean(d, norm_tag, norm_status, norm_phase)]
            return matched, 0

        try:
            return self.repo.clean_filtered(norm_tag, norm_status, norm_phase)
        except NotImplementedError:
            matcher = lambda d: _matches_clean(d, norm_tag, norm_status, norm_phase)
            return _clean_tasks_fallback(self.repo, matcher)

    def delete_task(self, task_id: str, domain: str = "") -> bool:
        return self.repo.delete(task_id, domain)
