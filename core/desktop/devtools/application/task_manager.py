"""Application-level task management service (Tasks contain nested Steps)."""

from __future__ import annotations

import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import yaml

from config import get_cleanup_done_tasks_ttl_seconds
from application.ports import TaskRepository
from application.sync_service import SyncService
from core.desktop.devtools.interface.constants import TIMESTAMP_FORMAT
from core import PlanNode, Step, TaskDetail, TaskNode, ensure_tree_ids, StepEvent
from core.desktop.devtools.application.context import derive_domain_explicit
from core.desktop.devtools.interface.i18n import translate, effective_lang as _effective_lang
from infrastructure.file_repository import FileTaskRepository
from infrastructure.projects_sync_service import ProjectsSyncService
from projects_sync import get_projects_sync
from core.status import normalize_status_code


_AUTO_CLEAN_LAST_RUN: Dict[str, float] = {}
_AUTO_CLEAN_MIN_INTERVAL_SECONDS = 15.0


def current_timestamp() -> str:
    """Returns local time with minute precision for root step metadata."""
    return datetime.now().strftime(TIMESTAMP_FORMAT)


def _flatten_steps(steps: List[Step], prefix: str = "") -> List[Tuple[str, Step]]:
    """Flatten nested steps (iterative, deterministic pre-order).

    Returns list of (canonical_path, Step).
    """
    flat: List[Tuple[str, Step]] = []
    frames: List[Tuple[List[Step], str, int]] = [(list(steps or []), str(prefix or ""), 0)]
    while frames:
        current_steps, current_prefix, idx = frames.pop()
        if idx >= len(current_steps):
            continue
        st = current_steps[idx]
        step_path = f"{current_prefix}.s:{idx}" if current_prefix else f"s:{idx}"
        flat.append((step_path, st))

        # Continue siblings after processing this step's subtree.
        frames.append((current_steps, current_prefix, idx + 1))

        plan = getattr(st, "plan", None)
        tasks = list(getattr(plan, "tasks", []) or []) if plan else []
        # LIFO: push in reverse order so t:0 is processed first.
        for t_idx in reversed(range(len(tasks))):
            task = tasks[t_idx]
            task_steps = list(getattr(task, "steps", []) or [])
            if not task_steps:
                continue
            task_prefix = f"{step_path}.t:{t_idx}"
            frames.append((task_steps, task_prefix, 0))
    return flat


def _find_step_path_by_id(steps: List[Step], step_id: str) -> Optional[str]:
    target = str(step_id or "").strip()
    if not target:
        return None
    for path, st in _flatten_steps(list(steps or [])):
        if str(getattr(st, "id", "") or "") == target:
            return path
    return None


def _find_task_path_by_id(steps: List[Step], node_id: str) -> Optional[str]:
    target = str(node_id or "").strip()
    if not target:
        return None
    for step_path, st in _flatten_steps(list(steps or [])):
        plan = getattr(st, "plan", None)
        tasks = list(getattr(plan, "tasks", []) or []) if plan else []
        for idx, task in enumerate(tasks):
            if str(getattr(task, "id", "") or "") == target:
                return f"{step_path}.t:{idx}"
    return None


def _parse_tree_path(path: str) -> List[Tuple[str, int]]:
    parts_raw = [p for p in str(path or "").split(".") if p.strip() != ""]
    if not parts_raw:
        return []
    segments: List[Tuple[str, int]] = []
    for part in parts_raw:
        if ":" not in part:
            return []
        kind, raw = part.split(":", 1)
        kind = kind.strip().lower()
        if kind not in {"s", "t"}:
            return []
        if raw.strip() == "":
            return []
        try:
            idx = int(raw)
        except ValueError:
            return []
        if idx < 0:
            return []
        segments.append((kind, idx))
    if not segments or segments[0][0] != "s":
        return []
    for prev, cur in zip(segments, segments[1:]):
        if prev[0] == cur[0]:
            return []
    return segments


def _find_step_by_path(steps: List[Step], path: str) -> Tuple[Optional[Step], Optional[object], Optional[int]]:
    segments = _parse_tree_path(path)
    if not segments or segments[-1][0] != "s":
        return None, None, None
    current_steps: List[Step] = list(steps or [])
    parent_node: Optional[object] = None
    current_step: Optional[Step] = None
    for kind, idx in segments:
        if kind == "s":
            if idx < 0 or idx >= len(current_steps):
                return None, None, None
            current_step = current_steps[idx]
            if (kind, idx) == segments[-1]:
                return current_step, parent_node, idx
        else:
            if not current_step:
                return None, None, None
            plan = getattr(current_step, "plan", None)
            if not plan or not getattr(plan, "tasks", None):
                return None, None, None
            tasks = plan.tasks
            if idx < 0 or idx >= len(tasks):
                return None, None, None
            parent_node = tasks[idx]
            current_steps = list(getattr(parent_node, "steps", []) or [])
    return None, None, None


def _find_task_by_path(steps: List[Step], path: str) -> Tuple[Optional[TaskNode], Optional[PlanNode], Optional[int]]:
    segments = _parse_tree_path(path)
    if not segments or segments[-1][0] != "t":
        return None, None, None
    current_steps: List[Step] = list(steps or [])
    current_step: Optional[Step] = None
    current_plan: Optional[PlanNode] = None
    current_task: Optional[TaskNode] = None
    for kind, idx in segments:
        if kind == "s":
            if idx < 0 or idx >= len(current_steps):
                return None, None, None
            current_step = current_steps[idx]
            current_task = None
            current_plan = getattr(current_step, "plan", None)
        else:
            if not current_step:
                return None, None, None
            current_plan = getattr(current_step, "plan", None)
            if not current_plan or not getattr(current_plan, "tasks", None):
                return None, None, None
            tasks = current_plan.tasks
            if idx < 0 or idx >= len(tasks):
                return None, None, None
            current_task = tasks[idx]
            if (kind, idx) == segments[-1]:
                return current_task, current_plan, idx
            current_steps = list(getattr(current_task, "steps", []) or [])
            current_step = None
            current_plan = None
    return None, None, None


def _attach_step(steps: List[Step], parent_path: Optional[str], new_step: Step) -> bool:
    if not parent_path:
        steps.append(new_step)
        return True
    segments = _parse_tree_path(parent_path)
    if not segments or segments[-1][0] != "t":
        return False
    task_node, _, _ = _find_task_by_path(steps, parent_path)
    if not task_node:
        return False
    task_node.steps.append(new_step)
    return True


def _validate_step_requirements(step: Step, idx: int, translator) -> Optional[Dict[str, str]]:
    if not step.success_criteria:
        return {
            "code": "validation",
            "message": translator("ERR_SUBTASK_NO_CRITERIA").format(idx=idx, title=step.title),
        }
    if not step.tests:
        return {
            "code": "validation",
            "message": translator("ERR_SUBTASK_NO_TESTS").format(idx=idx, title=step.title),
        }
    return None


def _validate_root_step_ready_for_ok(task: TaskDetail, translator) -> Tuple[bool, Optional[Dict[str, str]]]:
    flat = _flatten_steps(list(task.steps or []))
    if flat and task.calculate_progress() < 100:
        return False, {"code": "validation", "message": translator("ERR_TASK_NOT_COMPLETE")}
    if not task.success_criteria:
        return False, {"code": "validation", "message": translator("ERR_TASK_NO_CRITERIA_TESTS")}
    for idx, (_, st) in enumerate(flat, 1):
        err = _validate_step_requirements(st, idx, translator)
        if err:
            return False, err
    return True, None


def _normalized_fields(values: Optional[List[str]]) -> List[str]:
    return [v.strip() for v in (values or []) if v and v.strip()]


def _build_step(title: str, criteria, tests, blockers) -> Optional[Step]:
    return Step.new(
        title,
        criteria=_normalized_fields(criteria),
        tests=_normalized_fields(tests),
        blockers=_normalized_fields(blockers),
        created_at=current_timestamp(),
    )


def _update_progress_for_status(task: TaskDetail, status: str) -> None:
    task.status = status
    needs_progress = status in {"ACTIVE", "TODO"}
    if needs_progress and task.progress == 0 and _flat_steps_count(task):
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


def _clean_steps_fallback(repo, matcher) -> Tuple[List[str], int]:
    matched: List[str] = []
    removed = 0
    for detail in repo.list("", skip_sync=True):
        if not matcher(detail):
            continue
        matched.append(detail.id)
        if repo.delete(detail.id, detail.domain):
            removed += 1
    return matched, removed


def _flat_steps_count(task: TaskDetail) -> int:
    return len(_flatten_steps(list(getattr(task, "steps", []) or [])))


def _locate_step(task: TaskDetail, index: int, path: Optional[str]) -> Tuple[Optional[Step], Optional[str]]:
    if path:
        st, _, _ = _find_step_by_path(task.steps, path)
        return st, None if st else "index"
    if index < 0:
        return None, "index"
    fallback_path = f"s:{index}"
    st, _, _ = _find_step_by_path(task.steps, fallback_path)
    if st:
        return st, None
    if index < 0 or index >= len(task.steps):
        return None, "index"
    return task.steps[index], None


def _step_completion_blockers(step: Step, translator) -> Optional[str]:
    """Check what's blocking nested step completion.

    Normal mode logic:
    - criteria: must be explicitly confirmed
    - tests: OK if confirmed OR auto_confirmed (empty at creation)
    """
    if getattr(step, "blocked", False):
        reason = str(getattr(step, "block_reason", "") or "").strip()
        return f"Шаг заблокирован{': ' + reason if reason else ''}"
    if step.ready_for_completion():
        return None
    missing = []
    if not step.criteria_confirmed:
        missing.append(translator("CHECKPOINT_CRITERIA"))
    # Account for auto_confirmed flags (Normal mode)
    if not (step.tests_confirmed or step.tests_auto_confirmed):
        missing.append(translator("CHECKPOINT_TESTS"))
    return translator("ERR_SUBTASK_CHECKPOINTS").format(items=", ".join(missing)) if missing else None


class TaskManager:
    def __init__(
        self,
        tasks_dir: Optional[Path] = None,
        repository: Optional[TaskRepository] = None,
        sync_service: Optional[SyncService] = None,
        sync_provider=None,
        auto_sync: bool = True,
        use_global: bool = True,
    ):
        if tasks_dir is None:
            from core.desktop.devtools.interface.tasks_dir_resolver import get_tasks_dir_for_project
            # Prefer global; resolver is side-effect free by default (no mkdir).
            self.tasks_dir = get_tasks_dir_for_project(use_global=use_global, create=False)
        else:
            self.tasks_dir = tasks_dir
        self.repo: TaskRepository = repository or FileTaskRepository(self.tasks_dir)
        try:
            from core.desktop.devtools.application.store_migration import migrate_plan_task_layout
            migrate_plan_task_layout(self.tasks_dir)
        except Exception:
            # Migration is best-effort; do not block normal operations.
            pass
        provider_factory = sync_provider or get_projects_sync
        base_sync = sync_service or ProjectsSyncService(provider_factory())
        self.sync_service: SyncService = base_sync
        self.config = self.load_config()
        self.language = _effective_lang()
        self.auto_sync_message = ""
        self.last_sync_error = ""
        # Track tasks known to AI for detecting external changes
        self._known_tasks: Set[str] = set()
        # Store task snapshots for change detection: {task_id: (steps_count, progress, hash)}
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
        """Create a simple hash of root task state for change detection."""
        # Hash based on key mutable fields
        flat_steps = _flatten_steps(list(getattr(task, "steps", []) or []))
        parts = [
            task.title,
            task.status,
            str(getattr(task, "status_manual", False)),
            str(len(flat_steps)),
            str(sum(1 for _, s in flat_steps if s.completed)),
        ]
        # Add nested step titles and completion states
        for _, st in flat_steps:
            parts.append(f"{st.title}:{st.completed}")
        return "|".join(parts)

    def track_task(self, task_id: str, task: Optional[TaskDetail] = None) -> None:
        """Register task as known to AI and snapshot its state."""
        self._known_tasks.add(task_id)
        if task:
            self._task_snapshots[task_id] = (
                _flat_steps_count(task),
                task.calculate_progress(),
                self._task_hash(task),
            )

    def check_external_changes(self) -> Dict[str, List[Dict[str, Any]]]:
        """Check for tasks created/modified/deleted externally (e.g., via TUI/GUI).

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
                "steps_count": _flat_steps_count(task),
            })
            # Now track it
            self.track_task(task_id, task)

        # Modified: known task but state changed
        for task_id in self._known_tasks & current_ids:
            task = current_tasks[task_id]
            old_snapshot = self._task_snapshots.get(task_id)
            new_hash = self._task_hash(task)

            if old_snapshot and old_snapshot[2] != new_hash:
                old_steps, old_progress, _ = old_snapshot
                new_progress = task.calculate_progress()
                change_info: Dict[str, Any] = {
                    "id": task_id,
                    "title": task.title,
                }
                # Add specific changes
                if old_steps != _flat_steps_count(task):
                    change_info["steps_changed"] = f"{old_steps} -> {_flat_steps_count(task)}"
                if old_progress != new_progress:
                    change_info["progress_changed"] = f"{old_progress}% -> {new_progress}%"
                    # Determine if user marked something completed
                    if new_progress > old_progress:
                        change_info["user_completed_items"] = True
                result["modified_by_user"].append(change_info)
            # Update snapshot
            self._task_snapshots[task_id] = (
                _flat_steps_count(task),
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
        """Безопасная нормализация подпапки внутри .steps"""
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

    def _next_plan_id(self) -> str:
        try:
            return self.repo.next_plan_id()
        except Exception:
            ids = []
            for f in self.tasks_dir.rglob("PLAN-*.task"):
                try:
                    ids.append(int(f.stem.split("-")[1]))
                except (IndexError, ValueError):
                    continue
            return f"PLAN-{(max(ids) + 1 if ids else 1):03d}"

    def create_plan(
        self,
        title: str,
        *,
        status: str = "TODO",
        priority: str = "MEDIUM",
        domain: str = "",
        phase: str = "",
        component: str = "",
        folder: Optional[str] = None,
    ) -> TaskDetail:
        """Create a new Plan (PLAN-###, kind='plan').

        A plan stores contract + plan checklist and owns tasks via `parent=PLAN-###`.
        """
        try:
            status = normalize_status_code(status or "TODO")
        except ValueError:
            status = "TODO"
        domain = self.sanitize_domain(folder or domain or derive_domain_explicit("", phase, component))
        now_value = current_timestamp()
        plan = TaskDetail(
            id=self._next_plan_id(),
            title=title,
            status=status,
            kind="plan",
            domain=domain,
            phase=phase,
            component=component,
            parent=None,
            priority=priority,
            created=now_value,
            updated=now_value,
        )
        return plan

    def create_task(
        self,
        title: str,
        *,
        parent: str,
        status: str = "TODO",
        priority: str = "MEDIUM",
        domain: str = "",
        phase: str = "",
        component: str = "",
        folder: Optional[str] = None,
    ) -> TaskDetail:
        try:
            status = normalize_status_code(status or "TODO")
        except ValueError:
            status = "TODO"
        parent = str(parent or "").strip().upper()
        if not parent:
            raise ValueError("parent plan id is required")
        if not parent.startswith("PLAN-"):
            raise ValueError("parent must be a plan id (PLAN-###)")
        parent_plan = None
        try:
            candidates = self.repo.list("", skip_sync=True)
        except Exception:
            candidates = []
        for candidate in candidates:
            if getattr(candidate, "id", None) == parent:
                parent_plan = candidate
                break
        if not parent_plan:
            raise ValueError(f"parent plan not found: {parent}")
        if getattr(parent_plan, "kind", "task") != "plan":
            raise ValueError(f"parent is not a plan: {parent}")
        domain = self.sanitize_domain(folder or domain or derive_domain_explicit("", phase, component))
        now_value = current_timestamp()
        task = TaskDetail(
            id=self._next_id(),
            title=title,
            status=status,
            kind="task",
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
        if task.steps:
            ensure_tree_ids(task.steps)
        task.updated = current_timestamp()
        prog = task.calculate_progress()
        task.progress = prog
        if not getattr(task, "status_manual", False) and prog == 100 and not task.blocked:
            task.status = "DONE"
        task.domain = self.sanitize_domain(task.domain)
        self.repo.save(task)
        if not skip_sync:
            sync = self.sync_service
            if sync.enabled:
                changed = bool(sync.sync_step(task))
                if getattr(task, "_sync_error", None):
                    self._report_sync_error(task._sync_error)
                    task._sync_error = None
                if changed:
                    self.repo.save(task)

    def load_task(self, task_id: str, domain: str = "", skip_sync: bool = False) -> Optional[TaskDetail]:
        task = self.repo.load(task_id, domain)
        if not task:
            return None
        if task.steps:
            migrated = ensure_tree_ids(task.steps)
            if migrated:
                self.repo.save(task)
        if task.steps:
            prog = task.calculate_progress()
            if not getattr(task, "status_manual", False) and prog == 100 and not task.blocked and task.status != "DONE":
                task.status = "DONE"
                self.save_task(task)
        if not skip_sync:
            sync = self.sync_service
            if sync.enabled and task.project_item_id:
                sync.pull_step_fields(task)
        return task

    @staticmethod
    def find_step_path_by_id(task: TaskDetail, step_id: str) -> Optional[str]:
        return _find_step_path_by_id(list(getattr(task, "steps", []) or []), step_id)

    @staticmethod
    def find_task_node_path_by_id(task: TaskDetail, node_id: str) -> Optional[str]:
        return _find_task_path_by_id(list(getattr(task, "steps", []) or []), node_id)

    def _report_sync_error(self, message: str) -> None:
        logging.getLogger("apply_task.sync").warning(message)
        self.last_sync_error = f"SYNC ERROR: {message[:60]}"

    @staticmethod
    def _task_last_activity_epoch(task: TaskDetail) -> Optional[float]:
        raw = (getattr(task, "updated", "") or "").strip()
        if raw:
            try:
                dt = datetime.strptime(raw, TIMESTAMP_FORMAT)
                return dt.timestamp()
            except ValueError:
                pass
            try:
                return datetime.fromisoformat(raw).timestamp()
            except ValueError:
                pass
        try:
            src_mtime = float(getattr(task, "_source_mtime", 0.0) or 0.0)
            if src_mtime > 0:
                return src_mtime
        except Exception:
            pass
        try:
            return task.filepath.stat().st_mtime
        except Exception:
            return None

    def _maybe_auto_clean_done_tasks(self) -> None:
        ttl_seconds = get_cleanup_done_tasks_ttl_seconds()
        if ttl_seconds <= 0:
            return

        tasks_dir = getattr(self, "tasks_dir", None)
        if not tasks_dir:
            return

        key = str(Path(tasks_dir).resolve())
        now = time.time()
        last = _AUTO_CLEAN_LAST_RUN.get(key, 0.0)
        if now - last < _AUTO_CLEAN_MIN_INTERVAL_SECONDS:
            return
        _AUTO_CLEAN_LAST_RUN[key] = now

        try:
            self._auto_clean_done_tasks(ttl_seconds=ttl_seconds, now=now)
        except Exception as exc:  # pragma: no cover - safety
            logging.getLogger("apply_task.cleanup").warning("Auto-clean failed: %s", exc)

    def _auto_clean_done_tasks(self, ttl_seconds: int, now: float) -> int:
        """Move old DONE tasks into .trash so they don't accumulate in UI (TUI/GUI) or MCP.

        Safety rules:
        - Never touch tasks referenced by depends_on of remaining (not-cleaned) tasks.
        - If timestamp is missing/unparseable, skip (no silent data loss).
        """
        if ttl_seconds <= 0:
            return 0

        tasks = self.repo.list("", skip_sync=True)
        if not tasks:
            return 0

        candidates: List[TaskDetail] = []
        for task in tasks:
            if (task.status or "").upper() != "DONE":
                continue
            last_ts = self._task_last_activity_epoch(task)
            if last_ts is None:
                continue
            if now - last_ts >= float(ttl_seconds):
                candidates.append(task)

        if not candidates:
            return 0

        candidate_ids = {t.id for t in candidates}
        protected_ids: Set[str] = set()
        for task in tasks:
            if task.id in candidate_ids:
                continue
            for dep in getattr(task, "depends_on", []) or []:
                dep_id = (dep or "").strip()
                if dep_id:
                    protected_ids.add(dep_id)

        moved = 0
        for task in candidates:
            if task.id in protected_ids:
                continue
            if self._move_task_to_trash(task):
                moved += 1
        return moved

    def _move_task_to_trash(self, task: TaskDetail) -> bool:
        try:
            src = task.filepath
            if not src.exists():
                return False
            safe_domain = ""
            try:
                safe_domain = self.sanitize_domain(getattr(task, "domain", "") or "")
            except Exception:
                safe_domain = ""
            trash_root = self.tasks_dir / ".trash"
            dest_dir = trash_root / safe_domain if safe_domain else trash_root
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / src.name
            src.replace(dest)
            return True
        except Exception:
            return False

    def list_tasks(self, domain: str = "", skip_sync: bool = False) -> List[TaskDetail]:
        self._maybe_auto_clean_done_tasks()
        tasks: List[TaskDetail] = self.repo.list(domain, skip_sync=skip_sync)
        for parsed in tasks:
            if parsed.steps:
                prog = parsed.calculate_progress()
                if not getattr(parsed, "status_manual", False) and prog == 100 and not parsed.blocked and parsed.status != "DONE":
                    parsed.status = "DONE"
                    self.save_task(parsed, skip_sync=skip_sync)
            if not skip_sync:
                sync = self.sync_service
                if sync.enabled and parsed.project_item_id:
                    sync.pull_step_fields(parsed)
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
            changed = sync.sync_step(task) if sync.enabled else False
            if getattr(task, "_sync_error", None):
                self._report_sync_error(task._sync_error)
                task._sync_error = None
            if changed:
                file_path.write_text(task.to_file_content(), encoding="utf-8")
            return changed

        max_workers = self._compute_worker_count(len(tasks_to_sync))
        changed_count = 0
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(worker, t) for t in tasks_to_sync]
            for f in as_completed(futures):
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
            normalized_status = normalize_status_code(status)
        except ValueError:
            return False, {"code": "invalid_status", "message": self._t("ERR_STATUS_REQUIRED")}

        if getattr(task, "kind", "task") == "plan":
            # Plans do not use step-level validation; they are completed via plan checklist progress.
            plan_steps = list(getattr(task, "plan_steps", []) or [])
            plan_current = int(getattr(task, "plan_current", 0) or 0)
            if normalized_status == "DONE" and not force and plan_steps and plan_current < len(plan_steps):
                return False, {"code": "validation", "message": "План не завершён: закрой все пункты плана (plan_current) или используй force."}
            task.status = normalized_status
            task.status_manual = bool(force)
            task.update_status_from_progress()
            self.save_task(task, skip_sync=True)
            return True, None

        if normalized_status == "DONE":
            if not force:
                ok, error = _validate_root_step_ready_for_ok(task, self._t)
                if not ok:
                    return False, error
                task.progress = 100
                task.status_manual = False
            else:
                # keep actual progress/steps, but mark status as explicitly set
                task.progress = task.calculate_progress()
                task.status_manual = True
            task.status = normalized_status
        else:
            task.status_manual = False
            _update_progress_for_status(task, normalized_status)
            # When reopening step, ensure status change is not immediately auto-overridden by 100% progress.
            if _flat_steps_count(task) and (force or task.calculate_progress() == 100):
                for _, st in _flatten_steps(list(task.steps or [])):
                    st.completed = False
                    st.completed_at = None
        # skip_sync=True чтобы sync не перезаписал локальные изменения
        self.save_task(task, skip_sync=True)
        return True, None

    def add_step(
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
        new_step = _build_step(title, criteria, tests, blockers)
        if not new_step:
            return False, "missing_fields"
        if not _attach_step(task.steps, parent_path, new_step):
            return False, "path"
        task.update_status_from_progress()
        self.save_task(task)
        return True, None

    def add_task_node(
        self,
        task_id: str,
        *,
        step_path: str,
        title: str,
        status: Optional[str] = None,
        priority: Optional[str] = None,
        description: str = "",
        context: str = "",
        success_criteria: Optional[List[str]] = None,
        tests: Optional[List[str]] = None,
        dependencies: Optional[List[str]] = None,
        next_steps: Optional[List[str]] = None,
        problems: Optional[List[str]] = None,
        risks: Optional[List[str]] = None,
        blocked: Optional[bool] = None,
        blockers: Optional[List[str]] = None,
        status_manual: Optional[bool] = None,
        domain: str = "",
    ) -> Tuple[bool, Optional[str], Optional[TaskNode], Optional[str]]:
        task = self.load_task(task_id, domain)
        if not task:
            return False, "not_found", None, None
        title_value = str(title or "").strip()
        if not title_value:
            return False, "missing_title", None, None
        step, _, _ = _find_step_by_path(task.steps, step_path)
        if not step:
            return False, "path", None, None
        plan = step.ensure_plan()
        status_value = str(status or "TODO").strip().upper()
        if status is not None:
            try:
                status_value = normalize_status_code(status_value)
            except ValueError:
                return False, "invalid_status", None, None
        priority_value = str(priority or "MEDIUM").strip().upper() or "MEDIUM"
        if priority is not None and priority_value not in {"LOW", "MEDIUM", "HIGH"}:
            return False, "invalid_priority", None, None
        tests_list = _normalized_fields(tests)
        task_node = TaskNode(
            title=title_value,
            status=status_value,
            priority=priority_value,
            description=str(description or "").strip(),
            context=str(context or "").strip(),
            success_criteria=_normalized_fields(success_criteria),
            tests=tests_list,
            tests_auto_confirmed=not tests_list,
            dependencies=_normalized_fields(dependencies),
            next_steps=_normalized_fields(next_steps),
            problems=_normalized_fields(problems),
            risks=_normalized_fields(risks),
            blocked=bool(blocked) if blocked is not None else False,
            blockers=_normalized_fields(blockers),
            status_manual=bool(status_manual) if status_manual is not None else False,
        )
        plan.tasks.append(task_node)
        task.update_status_from_progress()
        self.save_task(task)
        task_path = f"{step_path}.t:{len(plan.tasks) - 1}"
        return True, None, task_node, task_path

    def update_task_node(
        self,
        task_id: str,
        *,
        path: str,
        title: Optional[str] = None,
        status: Optional[str] = None,
        priority: Optional[str] = None,
        description: Optional[str] = None,
        context: Optional[str] = None,
        success_criteria: Optional[List[str]] = None,
        tests: Optional[List[str]] = None,
        dependencies: Optional[List[str]] = None,
        next_steps: Optional[List[str]] = None,
        problems: Optional[List[str]] = None,
        risks: Optional[List[str]] = None,
        blocked: Optional[bool] = None,
        blockers: Optional[List[str]] = None,
        status_manual: Optional[bool] = None,
        domain: str = "",
    ) -> Tuple[bool, Optional[str], Optional[TaskNode]]:
        task = self.load_task(task_id, domain)
        if not task:
            return False, "not_found", None
        task_node, _, _ = _find_task_by_path(task.steps, path)
        if not task_node:
            return False, "path", None
        if title is not None:
            title_value = str(title or "").strip()
            if not title_value:
                return False, "missing_title", None
            task_node.title = title_value
        if status is not None:
            try:
                normalized = normalize_status_code(str(status))
            except ValueError:
                return False, "invalid_status", None
            task_node.status = normalized
            task_node.status_manual = True
        if status_manual is not None:
            task_node.status_manual = bool(status_manual)
        if priority is not None:
            task_node.priority = str(priority or "").strip().upper() or task_node.priority
        if description is not None:
            task_node.description = str(description or "").strip()
        if context is not None:
            task_node.context = str(context or "").strip()
        if success_criteria is not None:
            task_node.success_criteria = _normalized_fields(success_criteria)
            task_node.criteria_confirmed = False
            task_node.criteria_auto_confirmed = False
        if tests is not None:
            normalized_tests = _normalized_fields(tests)
            task_node.tests = normalized_tests
            task_node.tests_confirmed = False
            task_node.tests_auto_confirmed = not normalized_tests
        if dependencies is not None:
            task_node.dependencies = _normalized_fields(dependencies)
        if next_steps is not None:
            task_node.next_steps = _normalized_fields(next_steps)
        if problems is not None:
            task_node.problems = _normalized_fields(problems)
        if risks is not None:
            task_node.risks = _normalized_fields(risks)
        if blocked is not None:
            task_node.blocked = bool(blocked)
        if blockers is not None:
            task_node.blockers = _normalized_fields(blockers)
        task.update_status_from_progress()
        self.save_task(task)
        return True, None, task_node

    def delete_task_node(
        self,
        task_id: str,
        *,
        path: str,
        domain: str = "",
    ) -> Tuple[bool, Optional[str], Optional[TaskNode]]:
        task = self.load_task(task_id, domain)
        if not task:
            return False, "not_found", None
        task_node, plan, idx = _find_task_by_path(task.steps, path)
        if not task_node or plan is None or idx is None:
            return False, "path", None
        deleted = plan.tasks.pop(idx)
        task.update_status_from_progress()
        self.save_task(task)
        return True, None, deleted

    def set_step_completed(self, task_id: str, index: int, completed: bool, domain: str = "", path: Optional[str] = None, force: bool = False) -> Tuple[bool, Optional[str]]:
        # skip_sync=True чтобы не перезаписать локальные изменения данными из GitHub
        task = self.load_task(task_id, domain, skip_sync=True)
        if not task:
            return False, "not_found"
        st, error = _locate_step(task, index, path)
        if error:
            return False, error
        if completed and not force:
            blocker_msg = _step_completion_blockers(st, self._t)
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

    def add_step_progress_note(
        self,
        task_id: str,
        *,
        path: str,
        note: str,
        domain: str = "",
    ) -> Tuple[bool, Optional[str], Optional[Step]]:
        """Append a progress note to a nested step (does not mark it complete)."""
        note_value = str(note or "").strip()
        if not note_value:
            return False, "missing_note", None

        # skip_sync=True to avoid GitHub sync overwriting local note.
        task = self.load_task(task_id, domain, skip_sync=True)
        if not task:
            return False, "not_found", None

        target, error = _locate_step(task, 0, path)
        if error:
            return False, error, None

        target.progress_notes.append(note_value)
        if not target.started_at:
            target.started_at = current_timestamp()
        task.update_status_from_progress()
        self.save_task(task, skip_sync=True)
        return True, None, target

    def set_step_blocked(
        self,
        task_id: str,
        *,
        path: str,
        blocked: bool,
        reason: str = "",
        domain: str = "",
    ) -> Tuple[bool, Optional[str], Optional[Step]]:
        """Block/unblock a nested step by path."""
        # skip_sync=True to avoid GitHub sync overwriting local state.
        task = self.load_task(task_id, domain, skip_sync=True)
        if not task:
            return False, "not_found", None

        target, error = _locate_step(task, 0, path)
        if error:
            return False, error, None

        target.blocked = bool(blocked)
        target.block_reason = str(reason or "").strip() if blocked else ""
        task.update_status_from_progress()
        self.save_task(task, skip_sync=True)
        return True, None, target

    def delete_step_node(
        self,
        task_id: str,
        *,
        path: str,
        domain: str = "",
    ) -> Tuple[bool, Optional[str], Optional[Step]]:
        """Delete a nested step by path and persist the root step."""
        # skip_sync=True to avoid sync overwriting local delete.
        task = self.load_task(task_id, domain, skip_sync=True)
        if not task:
            return False, "not_found", None

        target, parent, idx = _find_step_by_path(task.steps, path)
        if target is None or idx is None:
            return False, "path", None

        if parent is None:
            deleted = task.steps.pop(idx)
        else:
            deleted = parent.steps.pop(idx)

        task.update_status_from_progress()
        self.save_task(task, skip_sync=True)
        return True, None, deleted

    def update_step_checkpoint(
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
            st, _, _ = _find_step_by_path(task.steps, path)
            if not st:
                return False, "index"
        else:
            if index < 0 or index >= len(task.steps):
                return False, "index"
            st = task.steps[index]
        checkpoint = checkpoint.lower()
        attr_map = {
            "criteria": ("criteria_confirmed", "criteria_notes"),
            "tests": ("tests_confirmed", "tests_notes"),
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
        if value:
            try:
                task.events.append(StepEvent.checkpoint(checkpoint, path or str(index), note=note))
            except Exception:
                pass
        if not value:
            st.completed = False
        task.update_status_from_progress()
        # skip_sync=True чтобы sync не перезаписал локальные изменения
        self.save_task(task, skip_sync=True)
        return True, None

    def update_checkpoint(
        self,
        task_id: str,
        *,
        kind: str,
        checkpoint: str,
        value: bool,
        note: str = "",
        domain: str = "",
        path: Optional[str] = None,
    ) -> Tuple[bool, Optional[str]]:
        """Update checkpoint flags for any checkpointable node.

        kind:
        - task_detail: root TaskDetail itself (PLAN-### or TASK-### file)
        - step: nested Step by `path`
        - plan: nested PlanNode by `path` (path points to owning Step)
        - task: nested TaskNode by `path`
        """
        target_kind = str(kind or "").strip().lower()
        if target_kind == "step":
            return self.update_step_checkpoint(task_id, 0, checkpoint, value, note, domain, path=path)

        task = self.load_task(task_id, domain, skip_sync=True)
        if not task:
            return False, "not_found"

        target: object
        if target_kind == "task_detail":
            target = task
        elif target_kind == "plan":
            if not path:
                return False, "path"
            st, _, _ = _find_step_by_path(task.steps, path)
            plan = getattr(st, "plan", None) if st else None
            if not plan:
                return False, "path"
            target = plan
        elif target_kind == "task":
            if not path:
                return False, "path"
            task_node, _, _ = _find_task_by_path(task.steps, path)
            if not task_node:
                return False, "path"
            target = task_node
        else:
            return False, "unknown_target"

        checkpoint = str(checkpoint or "").strip().lower()
        attr_map = {
            "criteria": ("criteria_confirmed", "criteria_notes"),
            "tests": ("tests_confirmed", "tests_notes"),
        }
        if checkpoint not in attr_map:
            return False, "unknown_checkpoint"
        flag_attr, notes_attr = attr_map[checkpoint]
        setattr(target, flag_attr, bool(value))
        note = str(note or "").strip()
        if note:
            getattr(target, notes_attr).append(note)

        # Normalize auto-confirmed tests across node types (Normal mode semantics).
        if checkpoint == "tests":
            tests_list = list(getattr(target, "tests", []) or [])
            if not tests_list and not getattr(target, "tests_confirmed", False):
                setattr(target, "tests_auto_confirmed", True)
            elif tests_list:
                setattr(target, "tests_auto_confirmed", False)

        task.update_status_from_progress()
        self.save_task(task, skip_sync=True)
        return True, None

    def update_step_fields(
        self,
        task_id: str,
        *,
        path: str,
        title: Optional[str] = None,
        criteria: Optional[List[str]] = None,
        tests: Optional[List[str]] = None,
        blockers: Optional[List[str]] = None,
        domain: str = "",
    ) -> Tuple[bool, Optional[str], Optional[Step]]:
        """Update nested step fields (title/criteria/tests/blockers) by path."""
        task = self.load_task(task_id, domain, skip_sync=True)
        if not task:
            return False, "not_found", None
        target, _, _ = _find_step_by_path(task.steps, path)
        if not target:
            return False, "path", None

        if title is not None:
            new_title = str(title or "").strip()
            if not new_title:
                return False, "missing_title", None
            target.title = new_title

        if criteria is not None:
            normalized = _normalized_fields(criteria)
            if not normalized:
                return False, "missing_criteria", None
            target.success_criteria = normalized
            target.criteria_confirmed = False
            target.criteria_auto_confirmed = False

        if tests is not None:
            normalized = _normalized_fields(tests)
            target.tests = normalized
            # Normal mode: auto-confirm when empty.
            if not normalized and not target.tests_confirmed:
                target.tests_auto_confirmed = True
            elif normalized:
                target.tests_auto_confirmed = False
                target.tests_confirmed = False

        if blockers is not None:
            target.blockers = _normalized_fields(blockers)

        # Any field change invalidates completion and recomputes status.
        target.completed = False
        target.completed_at = None
        task.update_status_from_progress()
        self.save_task(task, skip_sync=True)
        return True, None, target

    def add_dependency(self, task_id: str, dep: str, domain: str = "") -> bool:
        """Add dependency to a task.

        - If `dep` looks like a TASK-ID, it is treated as blocking `depends_on`
          with existence/cycle validation.
        - Otherwise it is treated as a soft/freeform dependency and stored in
          `dependencies` (legacy/external refs).
        """
        import re
        from core import StepEvent, validate_dependencies, build_dependency_graph
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
            task.events.append(StepEvent.dependency_added(dep_id))
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
        if norm_status:
            try:
                norm_status = normalize_status_code(norm_status)
            except ValueError:
                pass

        if dry_run:
            matched = [d.id for d in self.repo.list("", skip_sync=True) if _matches_clean(d, norm_tag, norm_status, norm_phase)]
            return matched, 0

        try:
            return self.repo.clean_filtered(norm_tag, norm_status, norm_phase)
        except NotImplementedError:
            matcher = lambda d: _matches_clean(d, norm_tag, norm_status, norm_phase)
            return _clean_steps_fallback(self.repo, matcher)

    def delete_task(self, task_id: str, domain: str = "") -> bool:
        return self.repo.delete(task_id, domain)
