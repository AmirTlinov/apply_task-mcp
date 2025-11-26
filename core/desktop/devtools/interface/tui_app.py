#!/usr/bin/env python3
"""TUI application - TaskTrackerTUI class and cmd_tui command."""

import os
import re
import subprocess
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union, Set

import webbrowser
from wcwidth import wcwidth

from core import Status, SubTask, TaskDetail
from core.desktop.devtools.application.task_manager import TaskManager, _find_subtask_by_path
from core.desktop.devtools.application.context import derive_domain_explicit, save_last_task
from core.desktop.devtools.interface.constants import LANG_PACK
from core.desktop.devtools.interface.i18n import translate, effective_lang as _effective_lang
from core.desktop.devtools.interface.tui_render import (
    render_detail_text,
    render_detail_text_impl,
    render_task_list_text,
    render_task_list_text_impl,
    render_checkpoint_view,
)
from core.desktop.devtools.interface.edit_handlers import (
    handle_token,
    handle_project_number,
    handle_project_workers,
    handle_bootstrap_remote,
    handle_task_edit,
)
from core.desktop.devtools.interface.tui_mouse import handle_body_mouse
from core.desktop.devtools.interface.tui_settings import build_settings_options
from core.desktop.devtools.interface.tui_navigation import move_vertical_selection
from core.desktop.devtools.interface.tui_focus import focusable_line_indices
from core.desktop.devtools.interface.tui_state import maybe_reload as _maybe_reload_helper, toggle_subtask_collapse
from core.desktop.devtools.interface.tui_settings_panel import render_settings_panel
from core.desktop.devtools.interface.tui_status import build_status_text
from core.desktop.devtools.interface.tui_footer import build_footer_text
from core.desktop.devtools.interface.cli_history import get_project_tasks_dir, resolve_project_root
from core.desktop.devtools.interface.tasks_dir_resolver import get_tasks_dir_for_project
from infrastructure.file_repository import FileTaskRepository
from core.desktop.devtools.interface.tui_loader import (
    load_tasks_with_state,
    apply_context_filters,
    build_task_models,
    select_index_after_load,
)
from core.desktop.devtools.interface.tui_preview import build_side_preview_text
from core.desktop.devtools.interface.tui_actions import activate_settings_option, delete_current_item
from core.desktop.devtools.interface.tui_sync_indicator import build_sync_indicator
from core.desktop.devtools.interface.tui_scroll import (
    apply_scroll_to_formatted as scroll_formatted_helper,
    scroll_line_preserve_borders as scroll_line_helper,
)
from infrastructure.task_file_parser import TaskFileParser
from util.responsive import detail_content_width
from projects_sync import update_project_target
from config import get_user_token, set_user_lang

from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import HSplit, Layout, Window, VSplit
from prompt_toolkit.layout.containers import DynamicContainer
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.mouse_events import MouseEventType, MouseEvent, MouseButton
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import TextArea
from prompt_toolkit.clipboard import InMemoryClipboard
try:  # pragma: no cover - optional dependency
    from prompt_toolkit.clipboard.pyperclip import PyperclipClipboard
except Exception:  # pragma: no cover
    PyperclipClipboard = None

from .tui_models import Task, InteractiveFormattedTextControl
from .tui_themes import DEFAULT_THEME, build_style
from .projects_integration import _get_sync_service, _projects_status_payload, validate_pat_token_http


class TaskTrackerTUI:
    SELECTION_STYLE_BY_STATUS: Dict[Status, str] = {
        Status.OK: "selected.ok",
        Status.WARN: "selected.warn",
        Status.FAIL: "selected.fail",
        Status.UNKNOWN: "selected.unknown",
    }
    SPINNER_FRAMES: List[str] = ["⣿", "⡇", "⡏", "⡗", "⡟", "⡧", "⡯", "⡷", "⡿", "⢇", "⢏", "⢗", "⢟", "⢧", "⢯", "⢷", "⢿"]

    @staticmethod
    def get_theme_palette(theme: str) -> Dict[str, str]:
        from .tui_themes import get_theme_palette as _get_theme_palette
        return _get_theme_palette(theme)

    @classmethod
    def build_style(cls, theme: str) -> Style:
        return build_style(theme)

    def __init__(self, tasks_dir: Optional[Path] = None, domain: str = "", phase: str = "", component: str = "", theme: str = DEFAULT_THEME, mono_select: bool = False):
        # Single-mode storage: always use global namespace directory for this project.
        if tasks_dir:
            resolved_dir = Path(tasks_dir).expanduser()
        else:
            resolved_dir = get_tasks_dir_for_project(use_global=True)
        resolved_dir.mkdir(parents=True, exist_ok=True)
        self.tasks_dir = resolved_dir
        self.global_storage_used = True
        # Project picker state
        self.project_mode: bool = True
        self.projects_root: Path = Path.home() / ".tasks"
        self.current_project_path: Optional[Path] = None

        self.manager = TaskManager(self.tasks_dir)
        self.domain_filter = domain
        self.phase_filter = phase
        self.component_filter = component
        self.tasks: List[Task] = []
        self.selected_index = 0
        self.current_filter: Optional[Status] = None
        self.detail_mode = False
        self.current_task_detail: Optional[TaskDetail] = None
        self.current_task: Optional[Task] = None
        self.detail_selected_index = 0
        self.detail_view_offset: int = 0
        self.navigation_stack = []
        self.task_details_cache: Dict[str, TaskDetail] = {}
        self._last_signature = None
        self._last_check = 0.0
        self.horizontal_offset = 0  # For horizontal scrolling
        self.detail_selected_path: str = ""
        self.theme_name = theme
        self.status_message: str = ""
        self.status_message_expires: float = 0.0
        self.help_visible: bool = False
        self.list_view_offset: int = 0
        self.settings_view_offset: int = 0
        self.footer_height: int = 9  # default footer height for task list
        self.mono_select = mono_select
        self.settings_mode = False
        self.settings_selected_index = 0
        self.task_row_map: List[Tuple[int, int]] = []
        self.subtask_row_map: List[Tuple[int, int]] = []
        self.detail_flat_subtasks: List[Tuple[str, SubTask, int, bool, bool]] = []
        self._last_click_index: Optional[int] = None
        self._last_click_time: float = 0.0
        self._last_subtask_click_index: Optional[int] = None
        self._last_subtask_click_time: float = 0.0
        self.subtask_detail_scroll: int = 0
        self.subtask_detail_cursor: int = 0
        self._subtask_detail_buffer: List[Tuple[str, str]] = []
        self._subtask_detail_total_lines: int = 0
        self._last_rate_wait: float = 0.0
        self.clipboard = self._build_clipboard()
        self.spinner_active = False
        self.spinner_message = ""
        self.spinner_start = 0.0
        self.language = _effective_lang()
        self.pat_validation_result = ""
        self._last_sync_enabled: Optional[bool] = None
        self._sync_flash_until: float = 0.0
        self._last_filter_value: Optional[str] = None
        self._filter_flash_until: float = 0.0
        # CLI activity tracking
        self._cli_activity_task_id: Optional[str] = None
        self._cli_activity_subtask_path: Optional[str] = None
        self._cli_activity_command: Optional[str] = None
        self._cli_activity_expires: float = 0.0
        if getattr(self.manager, "auto_sync_message", ""):
            self.set_status_message(self.manager.auto_sync_message, ttl=4)
        if getattr(self.manager, "last_sync_error", ""):
            self.set_status_message(self.manager.last_sync_error, ttl=6)
        self.detail_collapsed: Set[str] = set()
        self.collapsed_by_task: Dict[str, Set[str]] = {}

        # Editing mode
        self.editing_mode = False
        self.edit_field = TextArea(multiline=False, scrollbar=False, focusable=True, wrap_lines=False)
        self.edit_field.buffer.on_text_changed += lambda _: self.force_render()
        self.edit_buffer = self.edit_field.buffer
        self.edit_context = None  # 'task_title', 'subtask_title', 'criterion', 'test', 'blocker'
        self.edit_index = None

        self.edit_index = None

        # Checkpoint mode
        self.checkpoint_mode = False
        self.checkpoint_selected_index = 0

        # Initial screen: project picker
        self.load_projects()

        self.style = self.build_style(theme)

        kb = KeyBindings()
        kb.timeout = 0

        @kb.add("q")
        @kb.add("й")
        @kb.add("c-z")
        def _(event):
            event.app.exit()

        @kb.add("r")
        @kb.add("к")
        def _(event):
            if self.project_mode:
                self.load_projects()
            else:
                self.load_tasks(preserve_selection=True)

        @kb.add("p")
        @kb.add("з")
        def _(event):
            if not self.project_mode and not self.detail_mode:
                self.return_to_projects()
                return

        @kb.add("down")
        @kb.add("j")
        @kb.add("о")
        def _(event):
            if self.settings_mode and not self.editing_mode:
                self.move_settings_selection(1)
                return
            self.move_vertical_selection(1)

        @kb.add(Keys.ScrollDown)
        def _(event):
            self.move_vertical_selection(1)

        @kb.add("up")
        @kb.add("k")
        @kb.add("л")
        def _(event):
            if self.settings_mode and not self.editing_mode:
                self.move_settings_selection(-1)
                return
            self.move_vertical_selection(-1)

        @kb.add(Keys.ScrollUp)
        def _(event):
            self.move_vertical_selection(-1)

        @kb.add("1")
        def _(event):
            self.current_filter = None
            self.selected_index = 0

        @kb.add("2")
        def _(event):
            self.current_filter = Status.WARN  # IN PROGRESS
            self.selected_index = 0

        @kb.add("3")
        def _(event):
            self.current_filter = Status.FAIL  # BACKLOG
            self.selected_index = 0

        @kb.add("4")
        def _(event):
            self.current_filter = Status.OK  # DONE
            self.selected_index = 0

        @kb.add("?")
        def _(event):
            self.help_visible = not self.help_visible

        @kb.add("enter")
        def _(event):
            """Enter - раскрыть/свернуть или войти в детали"""
            if self.settings_mode and not self.editing_mode:
                self.activate_settings_option()
                return
            if self.editing_mode:
                # В режиме редактирования - сохранить
                self.save_edit()
            elif self.detail_mode and self.current_task_detail:
                # В режиме деталей Enter раскрывает/сворачивает подзадачу
                entry = self._selected_subtask_entry()
                if entry:
                    path, _, _, collapsed, has_children = entry
                    if has_children:
                        # Toggle collapse state
                        self._toggle_collapse_selected()
                    else:
                        # Для листовых подзадач показываем карточку
                        self.show_subtask_details(path)
            else:
                if self.filtered_tasks:
                    self.show_task_details(self.filtered_tasks[self.selected_index])

        @kb.add("escape", eager=True)
        def _(event):
            if self.editing_mode:
                # В режиме редактирования - отменить
                self.cancel_edit()
            elif self.settings_mode:
                self.close_settings_dialog()
            elif self.checkpoint_mode:
                # checkpoint_mode проверяем ДО detail_mode, т.к. checkpoint внутри detail
                self.exit_checkpoint_mode()
            elif self.detail_mode:
                self.exit_detail_view()
            elif not self.project_mode:
                self.return_to_projects()

        @kb.add("delete")
        @kb.add("c-d")
        @kb.add("x")
        @kb.add("ч")
        def _(event):
            """Delete/x - удалить выбранную задачу или подзадачу"""
            self.delete_current_item()

        @kb.add("d")
        @kb.add("в")
        def _(event):
            """d - показать детали (карточку) подзадачи"""
            if self.detail_mode and self.current_task_detail:
                entry = self._selected_subtask_entry()
                if entry:
                    path, _, _, _, _ = entry
                    self.show_subtask_details(path)

        @kb.add("e")
        @kb.add("у")
        def _(event):
            """e - редактировать"""
            if not self.editing_mode:
                self.edit_current_item()

        @kb.add("g")
        @kb.add("п")
        def _(event):
            """g - открыть GitHub Projects в браузере"""
            self._open_project_url()

        @kb.add("c-v")
        def _(event):
            if self.editing_mode:
                self._paste_from_clipboard()

        @kb.add("s-insert")
        def _(event):
            if self.editing_mode:
                self._paste_from_clipboard()

        @kb.add("f5")
        def _(event):
            if self.editing_mode and self.edit_context == 'token':
                self._validate_edit_buffer_pat()

        # Alternative keyboard controls for testing horizontal scroll
        @kb.add("c-left")
        def _(event):
            """Ctrl+Left - scroll content left"""
            self.horizontal_offset = max(0, self.horizontal_offset - 5)

        @kb.add("c-right")
        def _(event):
            """Ctrl+Right - scroll content right"""
            self.horizontal_offset = min(200, self.horizontal_offset + 5)

        @kb.add("[")
        def _(event):
            """[ - scroll content left (alternative)"""
            self.horizontal_offset = max(0, self.horizontal_offset - 3)

        @kb.add("]")
        def _(event):
            """] - scroll content right (alternative)"""
            self.horizontal_offset = min(200, self.horizontal_offset + 3)

        @kb.add("left")
        def _(event):
            """Left - collapse or go to parent in detail tree, or prev checkpoint/exit"""
            if self.checkpoint_mode:
                if self.checkpoint_selected_index == 0:
                    # На первом чекпоинте - выйти из режима
                    self.exit_checkpoint_mode()
                else:
                    self.move_checkpoint_selection(-1)
                return
            if self.detail_mode:
                entry = self._selected_subtask_entry()
                if entry:
                    path, _, _, collapsed, has_children = entry
                    if has_children and not collapsed:
                        self._toggle_collapse_selected(expand=False)
                        return
                    # go one level up in tree if possible
                    if "." in path:
                        parent_path = ".".join(path.split(".")[:-1])
                        self._select_subtask_by_path(parent_path)
                        self._ensure_detail_selection_visible(len(self.detail_flat_subtasks))
                        self.force_render()
                        return
                self.exit_detail_view()
                return
            if not self.project_mode:
                self.return_to_projects()
                return
            # в списке проектов/задач без project_mode: оставляем без действия

        @kb.add("right")
        def _(event):
            """Right - expand or go to first child in detail tree, or next checkpoint"""
            if self.checkpoint_mode:
                self.move_checkpoint_selection(1)
                return
            if not self.detail_mode:
                if self.filtered_tasks:
                    self.show_task_details(self.filtered_tasks[self.selected_index])
                return
            entry = self._selected_subtask_entry()
            if not entry:
                return
            path, _, _, collapsed, has_children = entry
            if has_children and collapsed:
                self._toggle_collapse_selected(expand=True)
                return
            self.show_subtask_details(path)

        @kb.add("c")
        @kb.add("с")
        def _(event):
            """c - открыть режим чекпоинтов"""
            if self.detail_mode and self.current_task_detail:
                self.enter_checkpoint_mode()

        @kb.add("space")
        def _(event):
            """Space - переключить выполнение задачи/подзадачи или чекпоинта"""
            if self.checkpoint_mode:
                self.toggle_checkpoint_state()
            elif self.detail_mode and self.current_task_detail:
                self.toggle_subtask_completion()
            else:
                # В списке задач - переключить статус задачи
                self.toggle_task_completion()

        @kb.add("home")
        def _(event):
            """Home - reset scroll"""
            self.horizontal_offset = 0

        self.status_bar = Window(content=FormattedTextControl(self.get_status_text), height=1, always_hide_cursor=True)
        self.task_list = Window(content=FormattedTextControl(self.get_task_list_text), always_hide_cursor=True, wrap_lines=False)
        self.side_preview = Window(content=FormattedTextControl(self.get_side_preview_text), always_hide_cursor=True, wrap_lines=True, width=Dimension(weight=2))
        self.detail_view = Window(content=FormattedTextControl(self.get_detail_text), always_hide_cursor=True, wrap_lines=True)
        footer_control = FormattedTextControl(self.get_footer_text)
        self.footer = Window(content=footer_control, height=Dimension(min=self.footer_height, max=self.footer_height), always_hide_cursor=False)

        self.normal_body = VSplit(
            [
                Window(content=FormattedTextControl(self.get_task_list_text), always_hide_cursor=True, wrap_lines=False, width=Dimension(weight=3)),
                Window(width=1, char=' '),
                self.side_preview,
            ],
            padding=0,
        )

        self.body_control = InteractiveFormattedTextControl(self.get_body_content, show_cursor=False, focusable=False, mouse_handler=self._handle_body_mouse)
        self.main_window = Window(
            content=self.body_control,
            always_hide_cursor=True,
            wrap_lines=True,
        )

        self.body_container = DynamicContainer(self._resolve_body_container)

        root = HSplit([self.status_bar, self.body_container, self.footer])

        self.app = Application(
            layout=Layout(root),
            key_bindings=kb,
            style=self.style,
            full_screen=True,
            mouse_support=True,
            refresh_interval=1.0,
            clipboard=self.clipboard,
        )

    @staticmethod
    def get_terminal_width() -> int:
        """Get current terminal width, default to 100 if unavailable."""
        try:
            return os.get_terminal_size().columns
        except (AttributeError, ValueError, OSError):
            return 100

    @staticmethod
    def get_terminal_height() -> int:
        try:
            return os.get_terminal_size().lines
        except (AttributeError, ValueError, OSError):
            return 40

    def _bootstrap_git(self, remote_url: str) -> None:
        """Инициализация git + origin + первый push (best effort)."""
        remote_url = remote_url.strip()
        repo_root = Path(".").resolve()
        try:
            if not (repo_root / ".git").exists():
                subprocess.run(["git", "init"], cwd=repo_root, check=True, capture_output=True)
            # set default branch main
            subprocess.run(["git", "checkout", "-B", "main"], cwd=repo_root, check=True, capture_output=True)
            # add remote origin (replace if exists)
            existing = subprocess.run(["git", "remote", "get-url", "origin"], cwd=repo_root, capture_output=True, text=True)
            if existing.returncode == 0:
                subprocess.run(["git", "remote", "remove", "origin"], cwd=repo_root, check=True, capture_output=True)
            subprocess.run(["git", "remote", "add", "origin", remote_url], cwd=repo_root, check=True, capture_output=True)
            # add all files
            subprocess.run(["git", "add", "-A"], cwd=repo_root, check=True, capture_output=True)
            # create commit if none
            has_commits = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True)
            if has_commits.returncode != 0:
                subprocess.run(["git", "commit", "-m", "chore: bootstrap repo"], cwd=repo_root, check=True, capture_output=True)
            # push
            push = subprocess.run(["git", "push", "-u", "origin", "main"], cwd=repo_root, capture_output=True, text=True)
            if push.returncode == 0:
                self.set_status_message(self._t("STATUS_MESSAGE_PUSH_OK"), ttl=4)
            else:
                self.set_status_message(self._t("STATUS_MESSAGE_PUSH_FAIL", error=push.stderr[:80]), ttl=6)
        except subprocess.CalledProcessError as exc:
            err_text = exc.stderr.decode()[:80] if exc.stderr else str(exc)
            self.set_status_message(self._t("STATUS_MESSAGE_BOOTSTRAP_ERROR", error=err_text), ttl=6)
        except Exception as exc:  # pragma: no cover - best effort
            self.set_status_message(self._t("STATUS_MESSAGE_BOOTSTRAP_FAILED", error=exc), ttl=6)

    def force_render(self) -> None:
        app = getattr(self, "app", None)
        if app:
            app.invalidate()

    def _start_spinner(self, message: str):
        self.spinner_message = message
        self.spinner_active = True
        self.spinner_start = time.time()
        self.force_render()

    def _stop_spinner(self):
        self.spinner_active = False
        self.spinner_message = ""
        self.force_render()

    @contextmanager
    def _spinner(self, message: str):
        self._start_spinner(message)
        try:
            yield
        finally:
            self._stop_spinner()

    def _run_with_spinner(self, message: str, func, *args, **kwargs):
        with self._spinner(message):
            return func(*args, **kwargs)

    def _set_footer_height(self, lines: int) -> None:
        """Dynamically adjust footer height (impacts visible rows)."""
        lines = max(0, lines)
        self.footer_height = lines
        try:
            self.footer.height = Dimension(min=lines, max=lines)
        except Exception:
            # UI may not be built yet; storing height is enough
            pass
        self.force_render()

    def _visible_row_limit(self) -> int:
        total = self.get_terminal_height()
        usable = total - (self.footer_height + 4)  # status + padding
        return max(5, usable)

    def _ensure_selection_visible(self):
        total = len(self.filtered_tasks)
        visible = self._visible_row_limit()
        if total <= visible:
            self.list_view_offset = 0
            return
        max_offset = max(0, total - visible)
        if self.selected_index < self.list_view_offset:
            self.list_view_offset = self.selected_index
        elif self.selected_index >= self.list_view_offset + visible:
            self.list_view_offset = self.selected_index - visible + 1
        self.list_view_offset = max(0, min(self.list_view_offset, max_offset))

    def _ensure_detail_selection_visible(self, total: int) -> None:
        visible = self._visible_row_limit()
        if total <= visible:
            self.detail_view_offset = 0
            return
        max_offset = max(0, total - visible)
        if self.detail_selected_index < self.detail_view_offset:
            self.detail_view_offset = self.detail_selected_index
        elif self.detail_selected_index >= self.detail_view_offset + visible:
            self.detail_view_offset = self.detail_selected_index - visible + 1
        self.detail_view_offset = max(0, min(self.detail_view_offset, max_offset))

    def _scroll_task_view(self, delta: int) -> None:
        total = len(self.filtered_tasks)
        visible = self._visible_row_limit()
        if total <= visible:
            self.list_view_offset = 0
            self.selected_index = min(self.selected_index, max(0, total - 1))
            return
        max_offset = max(0, total - visible)
        self.list_view_offset = max(0, min(self.list_view_offset + delta, max_offset))
        if total:
            min_visible = self.list_view_offset
            max_visible = min(total - 1, self.list_view_offset + visible - 1)
            if self.selected_index < min_visible:
                self.selected_index = min_visible
            elif self.selected_index > max_visible:
                self.selected_index = max_visible

    @staticmethod
    def _merge_styles(base: str, extra: Optional[str]) -> str:
        if not extra:
            return base
        if not base:
            return extra
        return f"{base} {extra}"

    def _t(self, key: str, **kwargs) -> str:
        return translate(key, lang=getattr(self, "language", "en"), **kwargs)

    def _cycle_language(self) -> None:
        order = list(LANG_PACK.keys())
        current = getattr(self, "language", "en")
        try:
            idx = order.index(current)
        except ValueError:
            idx = 0
        next_lang = order[(idx + 1) % len(order)]
        self.language = next_lang
        set_user_lang(next_lang)
        self.set_status_message(self._t("STATUS_MESSAGE_LANG_SET", language=next_lang))
        self.force_render()

    def _flatten_detail_subtasks(self, subtasks: List[SubTask], prefix: str = "", level: int = 0) -> List[Tuple[str, SubTask, int, bool, bool]]:
        flat: List[Tuple[str, SubTask, int, bool, bool]] = []
        for idx, st in enumerate(subtasks):
            path = f"{prefix}.{idx}" if prefix else str(idx)
            collapsed = path in self.detail_collapsed
            has_children = bool(st.children)
            flat.append((path, st, level, collapsed, has_children))
            if not collapsed:
                flat.extend(self._flatten_detail_subtasks(st.children, path, level + 1))
        return flat

    def _rebuild_detail_flat(self, selected_path: Optional[str] = None) -> None:
        if not self.current_task_detail:
            self.detail_flat_subtasks = []
            self.detail_selected_index = 0
            self.detail_selected_path = ""
            return
        flat = self._flatten_detail_subtasks(self.current_task_detail.subtasks)
        self.detail_flat_subtasks = flat
        if selected_path:
            probe = selected_path
            while True:
                for idx, (p, _, _, _, _) in enumerate(flat):
                    if p == probe:
                        self.detail_selected_index = idx
                        self.detail_selected_path = p
                        return
                if "." not in probe:
                    break
                probe = ".".join(probe.split(".")[:-1])
        if not flat:
            self.detail_selected_index = 0
            self.detail_selected_path = ""
            return
        self.detail_selected_index = max(0, min(self.detail_selected_index, len(flat) - 1))
        self.detail_selected_path = flat[self.detail_selected_index][0]

    def _selected_subtask_entry(self) -> Optional[Tuple[str, SubTask, int, bool, bool]]:
        if not self.detail_flat_subtasks:
            return None
        idx = max(0, min(self.detail_selected_index, len(self.detail_flat_subtasks) - 1))
        self.detail_selected_index = idx
        path, subtask, level, collapsed, has_children = self.detail_flat_subtasks[idx]
        self.detail_selected_path = path
        return path, subtask, level, collapsed, has_children

    def _select_subtask_by_path(self, path: str) -> None:
        if not self.detail_flat_subtasks:
            self.detail_selected_index = 0
            self.detail_selected_path = ""
            return
        for idx, (p, _, _, _, _) in enumerate(self.detail_flat_subtasks):
            if p == path:
                self.detail_selected_index = idx
                self.detail_selected_path = p
                return
        self.detail_selected_index = max(0, min(self.detail_selected_index, len(self.detail_flat_subtasks) - 1))
        self.detail_selected_path = self.detail_flat_subtasks[self.detail_selected_index][0]

    def _get_subtask_by_path(self, path: str) -> Optional[SubTask]:
        if not self.current_task_detail or not path:
            return None
        st, _, _ = _find_subtask_by_path(self.current_task_detail.subtasks, path)
        return st

    def _get_root_task_context(self) -> tuple[str, str, str]:
        """Get root task ID, domain, and path prefix for nested navigation.
        
        Returns (root_task_id, root_domain, path_prefix).
        When inside a subtask via navigation_stack, returns the actual root task info.
        """
        if not self.navigation_stack:
            # We're at root level
            task_id = self.current_task_detail.id if self.current_task_detail else ""
            domain = self.current_task_detail.domain if self.current_task_detail else ""
            return task_id, domain, ""
        
        # Get root from bottom of navigation stack
        root_context = self.navigation_stack[0]
        root_detail = root_context.get("detail")
        if not root_detail:
            # Fallback
            task_id = self.current_task_detail.id if self.current_task_detail else ""
            domain = self.current_task_detail.domain if self.current_task_detail else ""
            return task_id, domain, ""
        
        root_task_id = root_detail.id
        root_domain = root_detail.domain or ""
        
        # Build path prefix from navigation stack
        # Each entry in stack has "selected_path" which is the path we entered
        path_parts = []
        for ctx in self.navigation_stack:
            selected_path = ctx.get("selected_path", "")
            if selected_path:
                path_parts.append(selected_path)
        
        path_prefix = ".".join(path_parts) if path_parts else ""
        return root_task_id, root_domain, path_prefix

    def _toggle_collapse_selected(self, expand: bool) -> None:
        from core.desktop.devtools.interface.tui_state import toggle_subtask_collapse

        toggle_subtask_collapse(self, expand)
        if self.current_task_detail:
            self.collapsed_by_task[self.current_task_detail.id] = set(self.detail_collapsed)
        self._ensure_detail_selection_visible(len(self.detail_flat_subtasks))
        self.force_render()

    def _ensure_settings_selection_visible(self, total: int) -> None:
        visible = self._visible_row_limit()
        if total <= visible:
            self.settings_view_offset = 0
            return

    # ---------- Project selection ----------

    def load_projects(self) -> None:
        """Load list of projects from global storage and show in list view."""
        self.project_mode = True
        self.detail_mode = False
        self.current_task_detail = None
        self.current_task = None
        self.navigation_stack = []

        projects: List[Task] = []
        root = self.projects_root
        if root.exists():
            for path in sorted([p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")]):
                repo = FileTaskRepository(path)
                tasks = repo.list("", skip_sync=True)
                if not tasks:
                    continue
                total = len(tasks)
                ok_count = sum(1 for t in tasks if str(t.status).upper() == "OK")
                warn_count = sum(1 for t in tasks if str(t.status).upper() == "WARN")
                fail_count = total - ok_count - warn_count
                avg_progress = int(sum(t.calculate_progress() for t in tasks) / total) if total else 0
                status = Status.FAIL if fail_count else (Status.WARN if warn_count else Status.OK)
                projects.append(
                    Task(
                        id=path.name,
                        name=path.name,
                        status=status,
                        description="",
                        category="project",
                        completed=status == Status.OK,
                        task_file=str(path),
                        progress=avg_progress,
                        subtasks_count=total,
                        subtasks_completed=ok_count,
                        parent=None,
                        detail=None,
                        domain="",
                        phase="",
                        component="",
                        blocked=False,
                    )
                )
        self.tasks = projects
        self.selected_index = 0
        self.current_filter = None
        self._ensure_selection_visible()
        self.force_render()

    def _enter_project(self, project_task: Task) -> None:
        """Switch from project picker to tasks of the selected project."""
        path_raw = getattr(project_task, "task_file", None)
        if not path_raw:
            # skip if no path (e.g., dummy tasks in tests)
            return
        path = Path(path_raw)
        if not path.exists():
            self.set_status_message(self._t("STATUS_NO_TASKS"))
            return
        self.current_project_path = path
        self.tasks_dir = path
        self.manager = TaskManager(self.tasks_dir)
        self.project_mode = False
        self.load_tasks(skip_sync=True)
        self.set_status_message(f"Проект: {project_task.name}", ttl=2)

    def return_to_projects(self):
        """Return to project picker view."""
        self.load_projects()
        max_offset = max(0, total - visible)
        if self.settings_selected_index < self.settings_view_offset:
            self.settings_view_offset = self.settings_selected_index
        elif self.settings_selected_index >= self.settings_view_offset + visible:
            self.settings_view_offset = self.settings_selected_index - visible + 1
        self.settings_view_offset = max(0, min(self.settings_view_offset, max_offset))

    @staticmethod
    def _normalize_status_value(status: Union[Status, str, bool, None]) -> Status:
        if isinstance(status, Status):
            return status
        if isinstance(status, bool):
            return Status.OK if status else Status.FAIL
        if isinstance(status, str):
            return Status.from_string(status)
        return Status.UNKNOWN

    @staticmethod
    def _subtask_status(subtask: SubTask) -> Status:
        return subtask.status_value()

    def _status_indicator(self, status: Union[Status, str, bool, None]) -> Tuple[str, str]:
        status_obj = self._normalize_status_value(status)
        if status_obj == Status.OK:
            return '●', 'class:icon.check'
        if status_obj == Status.WARN:
            return '●', 'class:icon.warn'
        if status_obj == Status.FAIL:
            return '○', 'class:icon.fail'
        return '○', 'class:status.unknown'

    @staticmethod
    def _status_short_label(status: Status) -> str:
        if status == Status.OK:
            return '[OK]'
        if status == Status.WARN:
            return '[~]'
        if status == Status.FAIL:
            return '[X]'
        return '?'

    def _spinner_frame(self) -> str:
        if not self.spinner_active:
            return ''
        elapsed = time.time() - self.spinner_start
        idx = int(elapsed * 8) % len(self.SPINNER_FRAMES)
        return self.SPINNER_FRAMES[idx]

    def _selection_style_for_status(self, status: Union[Status, str, None]) -> str:
        if self.mono_select:
            return 'selected'
        status_obj: Status
        if isinstance(status, Status):
            status_obj = status
        elif isinstance(status, str):
            status_obj = Status.from_string(status)
        else:
            status_obj = Status.UNKNOWN
        return self.SELECTION_STYLE_BY_STATUS.get(status_obj, 'selected')

    def _task_index_from_y(self, y: int) -> Optional[int]:
        for line_no, idx in self.task_row_map:
            if line_no == y:
                return idx
        return None

    def _subtask_index_from_y(self, y: int) -> Optional[int]:
        for line_no, idx in self.subtask_row_map:
            if line_no == y:
                return idx
        return None

    def _handle_task_click(self, idx: int) -> None:
        total = len(self.filtered_tasks)
        if not total:
            return
        idx = max(0, min(idx, total - 1))
        now = time.time()
        double_click = self._last_click_index == idx and (now - self._last_click_time) < 0.4
        self.selected_index = idx
        self._ensure_selection_visible()
        if double_click:
            self._last_click_index = None
            self._last_click_time = 0.0
            self.show_task_details(self.filtered_tasks[idx])
        else:
            self._last_click_index = idx
            self._last_click_time = now

    def _handle_subtask_click(self, idx: int) -> None:
        if not self.current_task_detail or not self.detail_flat_subtasks:
            return
        total = len(self.detail_flat_subtasks)
        idx = max(0, min(idx, total - 1))
        now = time.time()
        double_click = self._last_subtask_click_index == idx and (now - self._last_subtask_click_time) < 0.4
        self.detail_selected_index = idx
        self._selected_subtask_entry()
        if double_click:
            path = self.detail_flat_subtasks[idx][0]
            self._last_subtask_click_index = None
            self._last_subtask_click_time = 0.0
            self.show_subtask_details(path)
        else:
            self._last_subtask_click_index = idx
            self._last_subtask_click_time = now

    def _handle_body_mouse(self, mouse_event: MouseEvent):
        return handle_body_mouse(self, mouse_event)

    def move_vertical_selection(self, delta: int) -> None:
        if self.checkpoint_mode:
            self.move_checkpoint_selection(delta)
            return
        move_vertical_selection(self, delta)

    def apply_horizontal_scroll(self, text: str) -> str:
        """Apply horizontal scroll offset to a text line."""
        if self.horizontal_offset == 0:
            return text
        # Skip offset characters from the beginning
        if len(text) <= self.horizontal_offset:
            return ""
        return text[self.horizontal_offset:]

    def scroll_line_preserve_borders(self, line: str) -> str:
        return scroll_line_helper(self, line)

    def apply_scroll_to_formatted(self, formatted_items: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
        return scroll_formatted_helper(self, formatted_items)

    @staticmethod
    def _formatted_lines(items: List[Tuple[str, str]]) -> List[List[Tuple[str, str]]]:
        """
        Разворачивает FormattedText в массив строк без лишних пустых вставок.
        Каждая строка — список (style, text) без символов перевода строки.
        """
        lines: List[List[Tuple[str, str]]] = [[]]
        for style, text in items:
            parts = text.split('\n')
            for idx, part in enumerate(parts):
                if idx > 0:
                    lines.append([])
                if part or idx < len(parts) - 1:
                    lines[-1].append((style, part))
        # убираем возможную пустую финальную строку
        if lines and all(not frag[1] for frag in lines[-1]):
            lines.pop()
        return lines

    @staticmethod
    def _display_width(text: str) -> int:
        """Возвращает печатную ширину текста с учётом ширины символов."""
        width = 0
        for ch in text:
            w = wcwidth(ch)
            if w is None:
                w = 0
            width += max(0, w)
        return width

    def _trim_display(self, text: str, width: int) -> str:
        """Обрезает текст так, чтобы видимая ширина не превышала width."""
        acc = []
        used = 0
        for ch in text:
            w = wcwidth(ch) or 0
            if w < 0:
                w = 0
            if used + w > width:
                break
            acc.append(ch)
            used += w
        return "".join(acc)

    def _pad_display(self, text: str, width: int) -> str:
        """Обрезает и дополняет пробелами до exact width по видимой ширине."""
        trimmed = self._trim_display(text, width)
        trimmed_width = self._display_width(trimmed)
        if trimmed_width < width:
            trimmed += " " * (width - trimmed_width)
        return trimmed

    def _wrap_display(self, text: str, width: int) -> List[str]:
        """Разбивает текст на строки фиксированной видимой ширины."""
        lines: List[str] = []
        current = ""
        used = 0
        for ch in text:
            w = wcwidth(ch) or 0
            if w < 0:
                w = 0
            if used + w > width and current:
                lines.append(self._pad_display(current, width))
                current = ch
                used = w
            else:
                current += ch
                used += w
        lines.append(self._pad_display(current, width))
        return lines

    def _wrap_with_prefix(self, text: str, width: int, prefix: str) -> List[Tuple[str, bool]]:
        """
        Разбивает текст на строки, добавляя префикс только к первой строке,
        последующие строки сдвигаются пробелами на ту же видимую ширину.
        """
        prefix_width = self._display_width(prefix)
        inner_width = max(1, width - prefix_width)
        segments = self._wrap_display(text, inner_width)
        lines: List[Tuple[str, bool]] = []
        for idx, seg in enumerate(segments):
            if idx == 0:
                composed = prefix + seg
            else:
                composed = " " * prefix_width + seg
            # защита от возможного выхода за ширину
            composed = self._pad_display(composed, width)
            lines.append((composed, idx == 0))
        return lines

    @staticmethod
    def _item_style(group_id: int, continuation: bool = False) -> str:
        base = "class:text.cont" if continuation else "class:text"
        return f"{base} class:item-{group_id}"

    @staticmethod
    def _extract_group(line: List[Tuple[str, str]]) -> Optional[int]:
        """Достаёт идентификатор логического элемента из стиля строки, если есть."""
        for style, _ in line:
            if not style:
                continue
            m = re.search(r"item-(\d+)", style)
            if m:
                try:
                    return int(m.group(1))
                except ValueError:
                    continue
        return None

    @staticmethod
    def _focusable_line_indices(lines: List[List[Tuple[str, str]]]) -> List[int]:
        return focusable_line_indices(lines, TaskTrackerTUI._extract_group)

    @staticmethod
    def _snap_cursor(desired: int, focusables: List[int]) -> int:
        if not focusables:
            return max(0, desired)
        if desired in focusables:
            return desired
        # ищем ближайший выше, затем ниже
        above = [i for i in focusables if i < desired]
        below = [i for i in focusables if i > desired]
        if below:
            return below[0]
        if above:
            return above[-1]
        return focusables[0]

    @staticmethod
    def _formatted_line_count(items: List[Tuple[str, str]]) -> int:
        return len(TaskTrackerTUI._formatted_lines(items))

    def _slice_formatted_lines(self, items: List[Tuple[str, str]], start: int, end: int) -> List[Tuple[str, str]]:
        """
        Возвращает отформатированный срез по номеру строк [start, end).
        Сохраняет стили, деля куски по newline.
        """
        lines = self._formatted_lines(items)
        sliced = lines[start:end]
        output: List[Tuple[str, str]] = []
        for i, line in enumerate(sliced):
            output.extend(line)
            if i < len(sliced) - 1:
                output.append(('', '\n'))
        return output

    def _first_focusable_line_index(self) -> int:
        lines = self._formatted_lines(self._subtask_detail_buffer or [])
        focusables = self._focusable_line_indices(lines)
        return focusables[0] if focusables else 0

    def _calculate_subtask_viewport(self, total: int, pinned: int, desired_offset: Optional[int] = None) -> Tuple[int, int, int, int, int]:
        """
        Рассчитывает параметры вьюпорта карточки подзадачи с учётом закреплённой шапки,
        футера и индикаторов скролла.

        Возвращает (offset, visible_content, indicator_top, indicator_bottom, remaining_below)
        """
        avail = max(5, self.get_terminal_height() - self.footer_height - 1)
        scroll_area = max(1, avail - pinned)
        scrollable_total = max(0, total - pinned)
        offset = max(0, desired_offset if desired_offset is not None else self.subtask_detail_scroll)
        max_raw_offset = max(0, scrollable_total - 1)
        offset = min(offset, max_raw_offset)

        indicator_top = 1 if offset > 0 else 0
        visible_content = max(1, scroll_area - indicator_top)
        max_offset = max(0, scrollable_total - visible_content)
        offset = min(offset, max_offset)

        indicator_bottom = 1 if offset + visible_content < scrollable_total else 0
        visible_content = max(1, scroll_area - indicator_top - indicator_bottom)
        max_offset = max(0, scrollable_total - visible_content)
        offset = min(offset, max_offset)

        remaining_below = max(0, scrollable_total - (offset + visible_content))
        return offset, visible_content, indicator_top, indicator_bottom, remaining_below

    @property
    def filtered_tasks(self) -> List[Task]:
        if not self.current_filter:
            return self.tasks
        return [t for t in self.tasks if t.status == self.current_filter]

    def compute_signature(self) -> int:
        if getattr(self, "project_mode", False):
            return 0
        return self.manager.compute_signature()

    def maybe_reload(self):
        if getattr(self, "project_mode", False) and not self.detail_mode:
            return
        _maybe_reload_helper(self)

    def load_tasks(self, preserve_selection: bool = False, selected_task_file: Optional[str] = None, skip_sync: bool = False):
        with self._spinner(self._t("SPINNER_REFRESH_TASKS")):
            domain_path = derive_domain_explicit(self.domain_filter, self.phase_filter, self.component_filter)
            details = self.manager.list_tasks(domain_path, skip_sync=skip_sync)

        snapshot = _projects_status_payload()
        wait = snapshot.get("rate_wait") or 0
        remaining = snapshot.get("rate_remaining")
        if wait > 0 and wait != self._last_rate_wait:
            message = self._t("STATUS_MESSAGE_RATE_LIMIT", remaining=remaining if remaining is not None else "?", seconds=int(wait))
            self.set_status_message(message, ttl=5)
            self._last_rate_wait = wait

        details = apply_context_filters(details, self.phase_filter, self.component_filter)

        def _task_factory(det, derived_status, calc_progress, subtasks_completed):
            task_file = f".tasks/{det.domain + '/' if det.domain else ''}{det.id}.task"
            return Task(
                id=det.id,
                name=det.title,
                status=derived_status,
                description=(det.description or det.context or "")[:80],
                category=det.domain or det.priority,
                completed=derived_status == Status.OK,
                task_file=task_file,
                progress=calc_progress,
                subtasks_count=len(det.subtasks),
                subtasks_completed=subtasks_completed,
                parent=det.parent,
                detail=det,
                domain=det.domain,
                phase=det.phase,
                component=det.component,
                blocked=det.blocked,
            )

        self.tasks = build_task_models(details, _task_factory)
        self.selected_index = select_index_after_load(self.tasks, preserve_selection, selected_task_file or "")
        self.detail_mode = False
        self.current_task = None
        self.current_task_detail = None
        self._last_signature = self.compute_signature()
        if self.selected_index >= len(self.filtered_tasks):
            self.selected_index = max(0, len(self.filtered_tasks) - 1)
        self._ensure_selection_visible()

    def _update_tasks_list_silent(self, skip_sync: bool = False):
        """Update tasks list without resetting view state (detail_mode, current_task, etc.)
        
        Use this when you need to refresh the task list after a modification
        but want to keep the user in their current view (e.g., detail mode).
        """
        # Preserve current state
        saved_detail_mode = self.detail_mode
        saved_current_task = self.current_task
        saved_current_task_detail = self.current_task_detail
        saved_selected_index = self.selected_index
        
        domain_path = derive_domain_explicit(self.domain_filter, self.phase_filter, self.component_filter)
        details = self.manager.list_tasks(domain_path, skip_sync=skip_sync)
        details = apply_context_filters(details, self.phase_filter, self.component_filter)

        def _task_factory(det, derived_status, calc_progress, subtasks_completed):
            task_file = f".tasks/{det.domain + '/' if det.domain else ''}{det.id}.task"
            return Task(
                id=det.id,
                name=det.title,
                status=derived_status,
                description=(det.description or det.context or "")[:80],
                category=det.domain or det.priority,
                completed=derived_status == Status.OK,
                task_file=task_file,
                progress=calc_progress,
                subtasks_count=len(det.subtasks),
                subtasks_completed=subtasks_completed,
                parent=det.parent,
                detail=det,
                domain=det.domain,
                phase=det.phase,
                component=det.component,
                blocked=det.blocked,
            )

        self.tasks = build_task_models(details, _task_factory)
        
        # Restore view state
        self.detail_mode = saved_detail_mode
        self.current_task = saved_current_task
        self.current_task_detail = saved_current_task_detail
        self.selected_index = saved_selected_index
        
        # Update cache for current task if it was refreshed
        if saved_current_task_detail:
            for task in self.tasks:
                if task.id == saved_current_task_detail.id:
                    self.current_task = task
                    # Re-fetch detail to get latest data
                    updated_detail = self.manager.load_task(task.id, task.domain or "", skip_sync=True)
                    if updated_detail:
                        self.current_task_detail = updated_detail
                        self.task_details_cache[task.id] = updated_detail
                    break
        
        self._last_signature = self.compute_signature()
        if self.selected_index >= len(self.filtered_tasks):
            self.selected_index = max(0, len(self.filtered_tasks) - 1)

    def set_status_message(self, message: str, ttl: float = 4.0) -> None:
        self.status_message = message
        self.status_message_expires = time.time() + ttl

    def get_status_text(self) -> FormattedText:
        return build_status_text(self)

    def _current_description_snippet(self) -> str:
        detail = self._current_task_detail_obj()
        if not detail:
            return ""
        text = detail.description or detail.context or ""
        text = text.strip()
        if not text:
            return ""
        return ' '.join(text.split())

    def _detail_content_width(self, term_width: Optional[int] = None) -> int:
        """Адаптивная ширина контента для detail/подзадач."""
        tw = term_width or self.get_terminal_width()
        return detail_content_width(tw)

    def _format_cell(self, content: str, width: int, align: str = 'left') -> str:
        """Форматирует содержимое ячейки с заданной шириной"""
        text = content[:width] if len(content) > width else content
        if align == 'right':
            return text.rjust(width)
        if align == 'center':
            return text.center(width)
        return text.ljust(width)

    def _get_task_detail(self, task: Task) -> Optional[TaskDetail]:
        detail = task.detail
        if not detail and task.task_file:
            try:
                detail = TaskFileParser.parse(Path(task.task_file))
            except Exception:
                detail = None
        return detail

    def _current_task_detail_obj(self) -> Optional[TaskDetail]:
        if self.detail_mode and self.current_task_detail:
            return self.current_task_detail
        if self.filtered_tasks:
            task = self.filtered_tasks[self.selected_index]
            return self._get_task_detail(task)
        return None

    def _sync_status_summary(self) -> str:
        sync = self.manager.sync_service if hasattr(self, "manager") else _get_sync_service()
        cfg = getattr(sync, "config", None)
        if not cfg:
            return "OFF"
        if cfg.project_type == "repository":
            target = f"{cfg.owner}/{cfg.repo or '-'}#{cfg.number}"
        else:
            target = f"{cfg.project_type}:{cfg.owner}#{cfg.number}"
        prefix = "ON" if sync.enabled else "OFF"
        return f"{prefix} {target}"


    def _sync_indicator_fragments(self, filter_flash: bool = False) -> List[Tuple[str, str]]:
        return build_sync_indicator(self, filter_flash)

    @staticmethod
    def _sync_target_label(cfg) -> str:
        if not cfg:
            return "-"
        if cfg.project_type == "repository":
            return f"{cfg.owner}/{cfg.repo or '-'}#{cfg.number}"
        return f"{cfg.project_type}:{cfg.owner}#{cfg.number}"

    def _task_created_value(self, task: Task) -> str:
        detail = self._get_task_detail(task)
        if detail and detail.created:
            return str(detail.created)
        return "—"

    def _task_done_value(self, task: Task) -> str:
        detail = self._get_task_detail(task)
        if detail and detail.updated and detail.status == "OK":
            return str(detail.updated)
        return "—"

    def _parse_task_datetime(self, value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        raw = str(value)
        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
            try:
                return datetime.strptime(raw[:len(fmt)], fmt)
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None

    def _task_duration_value(self, detail: Optional[TaskDetail]) -> str:
        if not detail:
            return "-"
        start = self._parse_task_datetime(detail.created)
        end = self._parse_task_datetime(detail.updated) if detail.status == "OK" else None
        if not start or not end:
            return "-"
        delta = end - start
        total_minutes = int(delta.total_seconds() // 60)
        days, rem_minutes = divmod(total_minutes, 60 * 24)
        hours, minutes = divmod(rem_minutes, 60)
        parts: List[str] = []
        if days:
            parts.append(f"{days}{self._t('DUR_DAYS_SUFFIX')}")
        if hours:
            parts.append(f"{hours}{self._t('DUR_HOURS_SUFFIX')}")
        if minutes:
            parts.append(f"{minutes}{self._t('DUR_MINUTES_SUFFIX')}")
        if not parts:
            parts.append(self._t("DUR_LT_HOUR"))
        return " ".join(parts)

    def _get_status_info(self, task: Task) -> Tuple[str, str, str]:
        """Возвращает символ статуса, CSS класс и короткое название"""
        symbol, css = self._status_indicator(task.status)
        status_obj = self._normalize_status_value(task.status)
        return symbol, css, self._status_short_label(status_obj)

    def _apply_scroll(self, text: str) -> str:
        """Применяет горизонтальную прокрутку к тексту"""
        if self.horizontal_offset > 0 and len(text) > self.horizontal_offset:
            return text[self.horizontal_offset:]
        return text if self.horizontal_offset == 0 else ""

    def get_task_list_text(self) -> FormattedText:
        return render_task_list_text(self)

    def _render_task_list_text_impl(self) -> FormattedText:
        return render_task_list_text_impl(self)

    def get_side_preview_text(self) -> FormattedText:
        return build_side_preview_text(self)

    # -------- detail view (full card in left pane) --------
    def get_detail_text(self) -> FormattedText:
        return render_detail_text(self)

    def _render_detail_text_impl(self) -> FormattedText:
        return render_detail_text_impl(self)

    def get_detail_items_count(self) -> int:
        if not self.current_task_detail:
            return 0
        return len(self.detail_flat_subtasks)

    def show_task_details(self, task: Task):
        if self.project_mode:
            self._enter_project(task)
            return
        self.current_task = task
        self.current_task_detail = task.detail or TaskFileParser.parse(Path(task.task_file))
        self.detail_mode = True
        self.detail_selected_index = 0
        self.detail_collapsed = set(self.collapsed_by_task.get(self.current_task_detail.id, set()))
        self._rebuild_detail_flat()
        self.detail_view_offset = 0
        self._set_footer_height(0)

    def show_subtask_details(self, path: str):
        """Enter into subtask as if it were a task (infinite nesting)."""
        if not self.current_task_detail:
            return
        subtask = self._get_subtask_by_path(path)
        if not subtask:
            return
        
        # Save current context to navigation stack
        self.navigation_stack.append({
            "task": self.current_task,
            "detail": self.current_task_detail,
            "selected_index": self.detail_selected_index,
            "selected_path": self.detail_selected_path,
        })
        
        # Convert subtask to task detail
        from core.task_detail import subtask_to_task_detail
        parent_id = self.current_task_detail.id
        new_detail = subtask_to_task_detail(subtask, parent_id, path)
        
        # Set as current task detail
        self.current_task_detail = new_detail
        self.detail_mode = True
        self.detail_selected_index = 0
        self.detail_selected_path = ""
        self._rebuild_detail_flat()
        self.detail_view_offset = 0
        self._set_footer_height(0)

    def delete_current_item(self):
        """Удалить текущий выбранный элемент (задачу или подзадачу)"""
        delete_current_item(self)

    def toggle_subtask_completion(self):
        """Переключить состояние выполнения подзадачи"""
        if self.detail_mode and self.current_task_detail:
            entry = self._selected_subtask_entry()
            if entry:
                path, st, _, _, _ = entry
                desired = not st.completed
                
                # Get root task context for nested navigation
                root_task_id, root_domain, path_prefix = self._get_root_task_context()
                
                # Build full path from root
                if path_prefix:
                    full_path = f"{path_prefix}.{path}"
                else:
                    full_path = path
                
                # force=True - пользователь может отметить без проверки чекпоинтов
                ok, msg = self.manager.set_subtask(root_task_id, 0, desired, root_domain, path=full_path, force=True)
                if not ok:
                    self.set_status_message(msg or self._t("STATUS_MESSAGE_CHECKPOINTS_REQUIRED"))
                    return
                
                # Reload root task and update current view
                updated_root = self.manager.load_task(root_task_id, root_domain, skip_sync=True)
                if updated_root:
                    # Update cache
                    self.task_details_cache[root_task_id] = updated_root
                    
                    # If we're at root level, update current_task_detail directly
                    if not self.navigation_stack:
                        self.current_task_detail = updated_root
                    else:
                        # We're inside nested subtask - rebuild current view from updated root
                        # Navigate to the current subtask in the updated tree
                        from core.task_detail import subtask_to_task_detail
                        subtask, _, _ = _find_subtask_by_path(updated_root.subtasks, path_prefix)
                        if subtask:
                            new_detail = subtask_to_task_detail(subtask, root_task_id, path_prefix)
                            new_detail.domain = root_domain
                            self.current_task_detail = new_detail
                    
                    self._rebuild_detail_flat(path)
                
                # Update tasks list without resetting view state
                self._update_tasks_list_silent(skip_sync=True)
                self.force_render()

    def toggle_task_completion(self):
        """Переключить статус задачи между ACTIVE и OK"""
        if not self.filtered_tasks or self.selected_index >= len(self.filtered_tasks):
            return
        if self.project_mode:
            self._enter_project(self.filtered_tasks[self.selected_index])
            return
        task = self.filtered_tasks[self.selected_index]
        domain = getattr(task, "domain", "")
        # Toggle: OK -> ACTIVE, anything else -> OK
        # task.status is Status enum, not string!
        new_status = "ACTIVE" if task.status == Status.OK else "OK"
        # force=True - пользователь может отметить без проверки подзадач
        ok, error = self.manager.update_task_status(task.id, new_status, domain, force=True)
        if not ok:
            msg = error.get("message", self._t("ERR_UPDATE_FAILED")) if error else self._t("ERR_UPDATE_FAILED")
            self.set_status_message(msg)
            return
        # skip_sync=True чтобы pull_task_fields не перезаписал локальные изменения
        self.load_tasks(preserve_selection=True, skip_sync=True)
        self.set_status_message(self._t("MSG_STATUS_UPDATED", task_id=task.id))
        self.force_render()

    def start_editing(self, context: str, current_value: str, index: Optional[int] = None):
        """Начать редактирование текста"""
        self.editing_mode = True
        self.edit_context = context
        self.edit_index = index
        self.edit_buffer.text = current_value
        self.edit_buffer.cursor_position = len(current_value)
        if hasattr(self, "app") and self.app:
            self.app.layout.focus(self.edit_field)

    def save_edit(self):
        """Сохранить результат редактирования"""
        if not self.editing_mode:
            return

        context = self.edit_context
        task = self.current_task_detail
        raw_value = self.edit_buffer.text
        new_value = raw_value.strip()

        if handle_token(self, new_value):
            return
        if handle_project_number(self, new_value):
            return
        if handle_project_workers(self, new_value):
            return
        if handle_bootstrap_remote(self, new_value):
            return

        if not new_value:
            self.cancel_edit()
            return

        if handle_task_edit(self, context or "", new_value, self.edit_index):
            return

        self.cancel_edit()

    def cancel_edit(self):
        """Отменить редактирование"""
        self.editing_mode = False
        self.edit_context = None
        self.edit_index = None
        self.edit_buffer.text = ''
        if hasattr(self, "app") and self.app:
            self.app.layout.focus(self.main_window)

    def _build_clipboard(self):
        if PyperclipClipboard:
            try:
                return PyperclipClipboard()
            except Exception:
                pass
        return InMemoryClipboard()

    def _clipboard_text(self) -> str:
        clipboard = getattr(self, "clipboard", None)
        if not clipboard:
            return ""
        try:
            data = clipboard.get_data()
        except Exception:
            return ""
        if not data:
            return self._system_clipboard_fallback()
        text = data.text or ""
        if text:
            return text
        return self._system_clipboard_fallback()

    def _system_clipboard_fallback(self) -> str:
        # Try pyperclip via prompt_toolkit wrapper
        if PyperclipClipboard:
            try:
                clip = PyperclipClipboard()
                data = clip.get_data()
                if data and data.text:
                    return data.text
            except Exception:
                pass
        # Try native commands (pbpaste/wl-paste/xclip)
        commands = [
            ["pbpaste"],
            ["wl-paste", "-n"],
            ["wl-copy", "-o"],
            ["xclip", "-selection", "clipboard", "-out"],
            ["clip.exe"],
        ]
        for cmd in commands:
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=1)
            except Exception:
                continue
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout
        return ""

    def _paste_from_clipboard(self) -> None:
        if not self.editing_mode:
            return
        text = self._clipboard_text()
        if not text:
            self.set_status_message(self._t("CLIPBOARD_EMPTY"), ttl=3)
            return
        buf = self.edit_buffer
        cursor = buf.cursor_position
        buf.text = buf.text[:cursor] + text + buf.text[cursor:]
        buf.cursor_position = cursor + len(text)
        self.force_render()

    def exit_detail_view(self):
        if not self.detail_mode:
            return
        if self.navigation_stack:
            prev = self.navigation_stack.pop()
            self.current_task = prev["task"]
            self.current_task_detail = prev["detail"]
            self.detail_selected_index = prev.get("selected_index", 0)
            self.detail_selected_path = prev.get("selected_path", "")
            self._rebuild_detail_flat()
        else:
            self.detail_mode = False
            self.current_task = None
            self.current_task_detail = None
            self.detail_selected_index = 0
            self.detail_selected_path = ""
            self.detail_view_offset = 0
            self.horizontal_offset = 0
            self.settings_mode = False
            self._set_footer_height(9)

    def edit_current_item(self):
        """Редактировать текущий элемент"""
        if self.detail_mode and self.current_task_detail:
            # В списке подзадач - редактируем название подзадачи
            entry = self._selected_subtask_entry()
            if entry:
                _, st, _, _, _ = entry
                self.start_editing('subtask_title', st.title, self.detail_selected_index)
        else:
            # В списке задач - редактируем название задачи
            if self.filtered_tasks:
                task = self.filtered_tasks[self.selected_index]
                task_detail = task.detail or TaskFileParser.parse(Path(task.task_file))
                self.current_task_detail = task_detail
                self.start_editing('task_title', task_detail.title)

    def get_footer_text(self) -> FormattedText:
        if getattr(self, "help_visible", False):
            return FormattedText([
                ("class:text.dimmer", self._t("NAV_STATUS_HINT")),
                ("", "\n"),
                ("class:text.dim", self._t("NAV_CHECKPOINT_HINT")),
            ])
        if getattr(self, "settings_mode", False):
            options = self._settings_options()
            idx = getattr(self, "settings_selected_index", 0)
            if options and 0 <= idx < len(options):
                hint = options[idx].get("hint", "")
                if hint:
                    return FormattedText([("class:text.dim", hint)])
            return FormattedText([("class:text.dimmer", self._t("NAV_SETTINGS_HINT", default="↑↓ navigate • Enter select • Esc close"))])
        if self.detail_mode and self.current_task_detail:
            return FormattedText([("class:text.dim", self._t("NAV_ARROWS_HINT"))])
        if self.editing_mode:
            return FormattedText([
                ("class:text.dimmer", self._t("NAV_EDIT_HINT")),
            ])
        return build_footer_text(self)

    def get_body_content(self) -> FormattedText:
        if self.settings_mode:
            return render_settings_panel(self)
        if self.checkpoint_mode:
            return render_checkpoint_view(self)
        if self.detail_mode and self.current_task_detail:
            return self.get_detail_text()
        self.maybe_reload()

        # Normal mode: show task list with side preview
        # Simple approach: just show task list, preview is handled separately if needed
        # For now, just return task list
        return self.get_task_list_text()

    def get_content_text(self) -> FormattedText:
        """Deprecated - use get_body_content instead."""
        return self.get_body_content()

    def close_settings_dialog(self):
        self.settings_mode = False
        self.editing_mode = False
        self._set_footer_height(9 if not self.detail_mode else 0)
        self.force_render()

    def _resolve_body_container(self):
        if self.editing_mode:
            return self._build_edit_container()
        return self.main_window

    def _build_edit_container(self):
        labels = {
            'task_title': self._t("EDIT_TASK_TITLE"),
            'task_description': self._t("EDIT_TASK_DESCRIPTION"),
            'subtask_title': self._t("EDIT_SUBTASK"),
            'criterion': self._t("EDIT_CRITERION"),
            'test': self._t("EDIT_TEST"),
            'blocker': self._t("EDIT_BLOCKER"),
            'token': 'GitHub PAT',
            'project_number': self._t("EDIT_PROJECT_NUMBER"),
        }
        label = labels.get(self.edit_context, self._t("EDIT_GENERIC"))
        width = max(40, self.get_terminal_width() - 4)
        header = Window(
            content=FormattedTextControl([('class:header', f" {label} ".ljust(width))]),
            height=1,
            always_hide_cursor=True,
        )
        self.edit_field.buffer.cursor_position = len(self.edit_field.text)
        children = [header, Window(height=1, char='─'), self.edit_field]

        if self.edit_context == 'token':
            button_text = self._t("BTN_VALIDATE_PAT")

            def fragments():
                return [('class:header', button_text, lambda mouse_event: self._validate_edit_buffer_pat() if (
                    mouse_event.event_type == MouseEventType.MOUSE_UP and mouse_event.button == MouseButton.LEFT
                ) else None)]

            button_control = FormattedTextControl(fragments)
            children.append(Window(height=1, char=' '))
            children.append(Window(content=button_control, height=1, always_hide_cursor=True))

        return HSplit(children, padding=0)

    def get_settings_panel(self) -> FormattedText:
        return render_settings_panel(self)

    def _settings_options(self) -> List[Dict[str, Any]]:
        return build_settings_options(self)

    def _project_config_snapshot(self) -> Dict[str, Any]:
        try:
            status = _projects_status_payload(force_refresh=True)
        except Exception as exc:
            return {
                "owner": "",
                "repo": "",
                "number": None,
                "project_url": None,
                "project_id": None,
                "config_exists": False,
                "config_enabled": False,
                "runtime_enabled": False,
                "token_saved": False,
                "token_preview": "",
                "token_env": "",
                "token_active": False,
                "target_label": "—",
                "target_hint": f"Git Projects недоступен: {exc}",
                "status_reason": str(exc),
                "last_pull": None,
                "last_push": None,
                "workers": None,
                "rate_remaining": None,
                "rate_reset_human": None,
                "rate_wait": None,
                "origin_url": self._origin_url(),
            }
        cfg_exists = bool(status["owner"] and (status["project_number"] or status.get("project_id")))
        return {
            "owner": status["owner"],
            "repo": status["repo"],
            "number": status["project_number"] or 1,
            "project_url": status.get("project_url"),
            "project_id": status.get("project_id"),
            "config_exists": cfg_exists,
            "config_enabled": status["auto_sync"],
            "runtime_enabled": status.get("runtime_enabled"),
            "token_saved": status["token_saved"],
            "token_preview": status["token_preview"],
            "token_env": status["token_env"],
            "token_active": status["token_present"],
            "target_label": status["target_label"],
            "target_hint": status["target_hint"],
            "status_reason": status["status_reason"],
            "last_pull": status.get("last_pull"),
            "last_push": status.get("last_push"),
            "workers": status.get("workers"),
            "rate_remaining": status.get("rate_remaining"),
            "rate_reset_human": status.get("rate_reset_human"),
            "rate_wait": status.get("rate_wait"),
            "origin_url": self._origin_url(),
        }

    def _open_project_url(self) -> None:
        snapshot = self._project_config_snapshot()
        url = snapshot.get("project_url")
        if url:
            try:
                webbrowser.open(url)
                self.set_status_message(self._t("STATUS_MESSAGE_PROJECT_OPEN", url=url), ttl=3)
            except Exception as exc:  # pragma: no cover - platform dependent
                self.set_status_message(self._t("STATUS_MESSAGE_OPEN_FAILED", error=exc), ttl=4)
        else:
            self.set_status_message(self._t("STATUS_MESSAGE_PROJECT_URL_UNAVAILABLE"))

    @staticmethod
    def _origin_url() -> str:
        try:
            result = subprocess.run(
                ["git", "config", "--get", "remote.origin.url"],
                capture_output=True,
                text=True,
                check=True,
            )
        except Exception:
            return ""
        return result.stdout.strip()

    def _set_project_number(self, number_value: int) -> None:
        update_project_target(int(number_value))

    def move_settings_selection(self, delta: int) -> None:
        options = self._settings_options()
        total = len(options)
        if not total:
            return
        self.settings_selected_index = max(0, min(self.settings_selected_index + delta, total - 1))
        self._ensure_settings_selection_visible(total)
        self.force_render()

    def activate_settings_option(self):
        activate_settings_option(self)

    def open_settings_dialog(self):
        self.settings_mode = True
        self.settings_selected_index = 0
        self.editing_mode = False
        self._set_footer_height(0)
        self.force_render()

    def _start_pat_validation(self, token: Optional[str] = None, label: str = "PAT", cache_result: bool = True):
        source_token = token or get_user_token() or os.getenv("APPLY_TASK_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")
        if not source_token:
            self.set_status_message(self._t("STATUS_MESSAGE_PAT_MISSING"))
            return

        if cache_result:
            self.pat_validation_result = self._t("STATUS_MESSAGE_VALIDATING")
        spinner_label = self._t("STATUS_MESSAGE_VALIDATE_LABEL", label=label)
        self._start_spinner(spinner_label)

        def worker():
            try:
                ok, msg = validate_pat_token_http(source_token)
                self.set_status_message(msg, ttl=6)
                if cache_result:
                    self.pat_validation_result = msg
            finally:
                self._stop_spinner()
                self.force_render()

        threading.Thread(target=worker, daemon=True).start()

    def _validate_edit_buffer_pat(self):
        value = self.edit_buffer.text.strip()
        if not value:
            self.set_status_message(self._t("STATUS_MESSAGE_PROMPT_PAT"), ttl=4)
            return
        self._start_pat_validation(token=value, label="PAT (ввод)", cache_result=False)

    def run(self):
        self.app.run()

    def _build_clipboard(self):
        if PyperclipClipboard:
            try:
                return PyperclipClipboard()
            except Exception:
                pass
        return InMemoryClipboard()

    def enter_checkpoint_mode(self):
        self.checkpoint_mode = True
        self.checkpoint_selected_index = 0
        self._set_footer_height(0)
        self.force_render()

    def exit_checkpoint_mode(self):
        self.checkpoint_mode = False
        self.force_render()

    def toggle_checkpoint_state(self):
        if not self.current_task_detail or not getattr(self, "detail_selected_path", ""):
            return
        path = self.detail_selected_path
        subtask = self._get_subtask_by_path(path)
        if not subtask:
            return
        
        checkpoints = ["criteria", "tests", "blockers"]
        if 0 <= self.checkpoint_selected_index < len(checkpoints):
            key = checkpoints[self.checkpoint_selected_index]
            current = False
            if key == "criteria":
                current = subtask.criteria_confirmed
                subtask.criteria_confirmed = not current
            elif key == "tests":
                current = subtask.tests_confirmed
                subtask.tests_confirmed = not current
            elif key == "blockers":
                current = subtask.blockers_resolved
                subtask.blockers_resolved = not current
            
            # Get root task context for nested navigation
            root_task_id, root_domain, path_prefix = self._get_root_task_context()
            
            # Build full path from root
            if path_prefix:
                full_path = f"{path_prefix}.{path}"
            else:
                full_path = path
            
            # Save changes
            try:
                top_level_index = int(full_path.split(".")[0])
                self.manager.update_subtask_checkpoint(
                    root_task_id,
                    top_level_index,
                    key,
                    not current,
                    "", # note
                    root_domain,
                    path=full_path
                )
                # Reload root task to get updated state
                updated_root = self.manager.load_task(root_task_id, root_domain, skip_sync=True)
                if updated_root:
                    # Update cache
                    self.task_details_cache[root_task_id] = updated_root
                    
                    # If we're at root level, update current_task_detail directly
                    if not self.navigation_stack:
                        self.current_task_detail = updated_root
                    else:
                        # We're inside nested subtask - rebuild current view from updated root
                        from core.task_detail import subtask_to_task_detail
                        nested_subtask, _, _ = _find_subtask_by_path(updated_root.subtasks, path_prefix)
                        if nested_subtask:
                            new_detail = subtask_to_task_detail(nested_subtask, root_task_id, path_prefix)
                            new_detail.domain = root_domain
                            self.current_task_detail = new_detail
                    
                    self._rebuild_detail_flat(path)
                
                # Update tasks list without resetting view state
                self._update_tasks_list_silent(skip_sync=True)
                save_last_task(root_task_id, root_domain)
                self.force_render()
            except (ValueError, IndexError):
                pass

    def move_checkpoint_selection(self, delta: int):
        self.checkpoint_selected_index = max(0, min(self.checkpoint_selected_index + delta, 2))
        self.force_render()

def cmd_tui(args) -> int:
    tasks_dir_arg = Path.cwd() / ".tasks"  # unused but kept for interface; constructor ignores and uses global default
    tui = TaskTrackerTUI(
        tasks_dir=None,  # force internal resolver to pick global project storage
        theme=getattr(args, "theme", DEFAULT_THEME),
        mono_select=getattr(args, "mono_select", False),
    )
    tui.run()
    return 0
