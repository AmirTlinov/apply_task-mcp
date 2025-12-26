#!/usr/bin/env python3
"""TUI application - TaskTrackerTUI class and cmd_tui command."""

import json
import os
import re
import shlex
from types import SimpleNamespace
import subprocess
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union, Set

import webbrowser
from wcwidth import wcwidth

from core import Status, Step, TaskDetail
from core.desktop.devtools.application.task_manager import TaskManager, _find_step_by_path, _find_task_by_path
from core.desktop.devtools.application.context import derive_domain_explicit, get_last_task, save_last_task
from core.desktop.devtools.application.plan_semantics import is_plan_task as _is_plan_task, normalize_tag as _normalize_tag
from core.desktop.devtools.application.namespace_display import parse_namespace as _parse_namespace
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
    handle_create_plan,
    handle_create_task,
    handle_task_edit,
)
from core.desktop.devtools.interface.tui_mouse import handle_body_mouse
from core.desktop.devtools.interface.tui_settings import build_settings_options
from core.desktop.devtools.interface.tui_navigation import move_vertical_selection
from core.desktop.devtools.interface.tui_focus import focusable_line_indices
from core.desktop.devtools.interface.tui_state import (
    maybe_reload as _maybe_reload_helper,
    toggle_subtask_collapse,
    collapse_subtask_descendants,
    expand_subtask_descendants,
)
from core.desktop.devtools.interface.tui_detail_tree import (
    DetailNodeEntry,
    DetailNodeStats,
    canonical_path as _detail_canonical_path,
    node_kind as _detail_node_kind,
)
from core.desktop.devtools.interface.tui_settings_panel import render_settings_panel
from core.desktop.devtools.interface.tui_status import build_status_text
from core.desktop.devtools.interface.tui_footer import build_footer_text
from core.desktop.devtools.interface.intent_api import handle_handoff
from core.desktop.devtools.interface.tasks_dir_resolver import (
    get_tasks_dir_for_project,
    migrate_legacy_github_namespaces,
)
from infrastructure.file_repository import FileTaskRepository
from core.desktop.devtools.interface.tui_loader import (
    apply_context_filters,
    build_task_models,
    select_index_after_load,
)
from core.desktop.devtools.interface.tui_preview import build_side_preview_text
from core.desktop.devtools.interface.tui_confirm import render_confirm_dialog
from core.desktop.devtools.interface.tui_list_editor import render_list_editor_dialog
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
from prompt_toolkit.filters import Condition
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
from .tui_clipboard import ClipboardMixin
from .tui_checkpoint import CheckpointMixin
from .tui_editing import EditingMixin
from .tui_display import DisplayMixin

DETAIL_TABS: Tuple[str, ...] = ("radar", "overview", "plan", "contract", "notes", "meta")


class TaskTrackerTUI(ClipboardMixin, CheckpointMixin, EditingMixin, DisplayMixin):
    SELECTION_STYLE_BY_STATUS: Dict[Status, str] = {
        Status.DONE: "selected.ok",
        Status.ACTIVE: "selected.warn",
        Status.TODO: "selected.fail",
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

    def __init__(
        self,
        tasks_dir: Optional[Path] = None,
        domain: str = "",
        phase: str = "",
        component: str = "",
        theme: str = DEFAULT_THEME,
        mono_select: bool = False,
        projects_root: Optional[Path] = None,
        *,
        use_global: bool = True,
    ):
        # Storage selection:
        # - global (~/.tasks/<namespace>) is the canonical default
        # - local (<project>/.tasks) is a portable/legacy mode (no project picker)
        self.global_storage_used = bool(use_global)
        if tasks_dir:
            resolved_dir = Path(tasks_dir).expanduser()
        else:
            resolved_dir = get_tasks_dir_for_project(use_global=self.global_storage_used)
        self.tasks_dir = resolved_dir

        # Project picker state
        self.project_mode: bool = True
        self.projects_root: Path = (Path(projects_root).expanduser().resolve() if projects_root else Path.home() / ".tasks")
        self.current_project_path: Optional[Path] = None
        # Within-project navigation: Plans → Tasks → Subtasks (infinite nesting).
        self.project_section: str = "tasks"  # plans|tasks (set to plans on project entry)
        self.plan_filter_id: Optional[str] = None
        self.plan_filter_title: Optional[str] = None
        self._pending_select_plan_id: Optional[str] = None
        self._pending_create_parent_id: Optional[str] = None
        self._detail_source_section: str = ""
        self._section_selected_task_file: Dict[str, str] = {}
        self._section_cache: Dict[str, List[Task]] = {}
        self.last_project_index: int = 0
        self.last_project_id: Optional[str] = None
        self.last_project_name: Optional[str] = None
        # Cached project picker snapshot to avoid blocking UI on return.
        self._projects_cache: List[Task] = []
        self._projects_refresh_generation: int = 0
        self._projects_refresh_in_flight: bool = False
        self._projects_cache_fingerprint: Tuple[Any, ...] = tuple()

        # Respect last pointer for default context when explicit filters absent
        last_task, last_domain = get_last_task()
        self.domain_filter = domain or (last_domain or "")
        self.phase_filter = phase
        self.component_filter = component
        self.manager = TaskManager(self.tasks_dir)
        self.tasks: List[Task] = []
        self._filtered_tasks_cache_key: Optional[Tuple[Any, ...]] = None
        self._filtered_tasks_cache: List[Task] = []
        self._filtered_tasks_metrics: Dict[str, int] = {}
        self.selected_index = 0
        self.current_filter: Optional[Status] = None
        self.detail_mode = False
        self.current_task_detail: Optional[TaskDetail] = None
        self.current_task: Optional[Task] = None
        self.detail_selected_index = 0
        self.detail_view_offset: int = 0
        self.detail_plan_tasks: List[TaskDetail] = []
        self.detail_selected_task_id: Optional[str] = None
        # Plan detail performance: cache list of tasks for the current plan to avoid
        # full disk scans/parsing on every render/scroll.
        self._detail_plan_tasks_cache_key: Optional[Tuple[str, str]] = None  # (plan_id, domain)
        self._detail_plan_tasks_dirty: bool = True
        self._detail_plan_rows_cache: List[Dict[str, int]] = []
        self._detail_plan_rows_cache_key: Optional[Tuple[str, str]] = None
        self._detail_plan_summary_cache: Optional[Dict[str, int]] = None
        # Cached step-tree counts for TASK rows in Plan detail: key -> (fingerprint, total, done)
        self._detail_task_step_counts_cache: Dict[Tuple[str, str], Tuple[Tuple[Any, ...], int, int]] = {}
        self.navigation_stack = []
        # Radar is the primary "cockpit" screen in detail view.
        self.detail_tab: str = "radar"
        self.detail_tab_scroll_offsets: Dict[str, int] = {"radar": 0, "notes": 0, "plan": 0, "contract": 0, "meta": 0}
        self.task_details_cache: Dict[str, TaskDetail] = {}
        # Cached radar payload to keep rendering snappy (linting can be expensive).
        self._radar_cache_focus_id: str = ""
        self._radar_cache_payload: Optional[Dict[str, Any]] = None
        self._radar_cache_error: str = ""
        self._radar_cache_at: float = 0.0
        self._last_signature = None
        self._last_check = 0.0
        self.horizontal_offset = 0  # For horizontal scrolling
        self.detail_selected_path: str = ""
        self.theme_name = theme
        self.status_message: str = ""
        self.status_message_expires: float = 0.0
        self.help_visible: bool = False
        # List search/filter (projects + tasks)
        self.search_query: str = ""
        self.search_mode: bool = False
        # Modal confirmation dialog (delete/force actions)
        self.confirm_mode: bool = False
        self.confirm_title: str = ""
        self.confirm_lines: List[str] = []
        self._confirm_on_yes = None
        self._confirm_on_no = None
        # List editor (task/subtask lists)
        self.list_editor_mode: bool = False
        self.list_editor_stage: str = "menu"  # menu | list
        self.list_editor_selected_index: int = 0
        self.list_editor_view_offset: int = 0
        self.list_editor_target: Optional[Dict[str, Any]] = None
        self.list_editor_pending_action: Optional[str] = None  # add | edit
        self.list_view_offset: int = 0
        self.settings_view_offset: int = 0
        self.footer_height: int = 4  # compact boxed footer (2 rows + borders)
        self.mono_select = mono_select
        self.settings_mode = False
        self.settings_selected_index = 0
        self.task_row_map: List[Tuple[int, int]] = []
        self.subtask_row_map: List[Tuple[int, int]] = []
        self.detail_flat_subtasks: List[DetailNodeEntry] = []
        self.detail_stats_by_key: Dict[str, DetailNodeStats] = {}
        self.detail_flat_dirty: bool = True
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
        if getattr(self.manager, "auto_sync_message", ""):
            self.set_status_message(self.manager.auto_sync_message, ttl=4)
        if getattr(self.manager, "last_sync_error", ""):
            self.set_status_message(self.manager.last_sync_error, ttl=6)
        self.detail_collapsed: Set[str] = set()
        self.collapsed_by_task: Dict[str, Set[str]] = {}

        # Editing mode
        self.editing_mode = False
        self._editing_multiline: bool = False
        # Shared editor widget: single-line or multi-line behavior is driven by context + keybindings.
        self.edit_field = TextArea(multiline=True, scrollbar=True, focusable=True, wrap_lines=True)
        self.edit_field.buffer.on_text_changed += lambda _: self.force_render()
        self.edit_buffer = self.edit_field.buffer
        self.edit_context = None  # 'task_title', 'subtask_title', 'criterion', 'test', 'blocker'
        self.edit_index = None

        self.edit_index = None

        # Checkpoint mode
        self.checkpoint_mode = False
        self.checkpoint_selected_index = 0

        # Help overlay: remember which footer height to restore to after closing help.
        self._footer_height_after_help: Optional[int] = None

        # Initial screen: auto-enter current project if possible, otherwise project picker
        if self.global_storage_used:
            self._auto_enter_default_project()
        else:
            self._auto_enter_local_project()

        self.style = self.build_style(theme)

        kb = KeyBindings()
        kb.timeout = 0
        search_input_allowed = Condition(
            lambda: (
                not getattr(self, "editing_mode", False)
                and not getattr(self, "detail_mode", False)
                and not getattr(self, "checkpoint_mode", False)
                and not getattr(self, "settings_mode", False)
            )
        )
        search_input_active = search_input_allowed & Condition(lambda: getattr(self, "search_mode", False))
        confirm_active = Condition(lambda: getattr(self, "confirm_mode", False))
        detail_tab_allowed = Condition(
            lambda: (
                getattr(self, "detail_mode", False)
                and getattr(self, "current_task_detail", None)
                and not getattr(self, "editing_mode", False)
                and not getattr(self, "checkpoint_mode", False)
                and not getattr(self, "settings_mode", False)
                and not getattr(self, "confirm_mode", False)
                and not getattr(self, "list_editor_mode", False)
            )
        )
        checkpoint_open_allowed = detail_tab_allowed & Condition(
            lambda: getattr(self, "detail_tab", "overview") == "overview"
        )
        radar_tab_allowed = detail_tab_allowed & Condition(
            lambda: getattr(self, "detail_tab", "radar") == "radar"
        )
        list_editor_active = Condition(
            lambda: (
                getattr(self, "list_editor_mode", False)
                and not getattr(self, "editing_mode", False)
                and not getattr(self, "confirm_mode", False)
                and not getattr(self, "settings_mode", False)
                and not getattr(self, "checkpoint_mode", False)
            )
        )
        section_toggle_allowed = Condition(
            lambda: (
                not getattr(self, "project_mode", False)
                and not getattr(self, "detail_mode", False)
                and not getattr(self, "editing_mode", False)
                and not getattr(self, "confirm_mode", False)
                and not getattr(self, "settings_mode", False)
                and not getattr(self, "checkpoint_mode", False)
                and not getattr(self, "list_editor_mode", False)
                and not getattr(self, "search_mode", False)
            )
        )
        not_editing = Condition(lambda: not getattr(self, "editing_mode", False))
        editing_active = Condition(lambda: bool(getattr(self, "editing_mode", False)))
        editing_multiline_active = editing_active & Condition(lambda: bool(getattr(self, "_editing_multiline", False)))
        editing_singleline_active = editing_active & Condition(lambda: not bool(getattr(self, "_editing_multiline", False)))

        @kb.add(Keys.Any, eager=True, filter=list_editor_active)
        def _(event):
            """Modal swallow for list editor."""
            return

        @kb.add("q", filter=not_editing)
        @kb.add("й", filter=not_editing)
        @kb.add("c-z", filter=not_editing)
        def _(event):
            event.app.exit()

        @kb.add("r", filter=not_editing)
        @kb.add("к", filter=not_editing)
        def _(event):
            # In plan detail view, refresh plan tasks list (expensive but explicit).
            if self.detail_mode and self.current_task_detail and getattr(self.current_task_detail, "kind", "task") == "plan":
                self._invalidate_plan_detail_tasks_cache()
                try:
                    self._plan_detail_tasks()
                except Exception:
                    pass
                self.force_render()
                return
            if self.project_mode:
                self.load_projects()
            else:
                if hasattr(self, "load_current_list"):
                    self.load_current_list(preserve_selection=True, skip_sync=True)
                else:  # pragma: no cover - legacy fallback
                    self.load_tasks(preserve_selection=True)

        @kb.add("c", filter=search_input_allowed)
        @kb.add("с", filter=search_input_allowed)
        def _(event):
            """c - create plan/task (context-aware)."""
            if getattr(self, "project_mode", False):
                return
            section = getattr(self, "project_section", "tasks") or "tasks"
            if section == "plans":
                self.start_editing("create_plan_title", "", None)
                return
            plan_parent = getattr(self, "plan_filter_id", None)
            if plan_parent:
                self._pending_create_parent_id = str(plan_parent)
                self.start_editing("create_task_title", "", None)
                return
            # No plan selected: creating a task would become a new plan anyway. Do it explicitly.
            self.toggle_project_section()
            self.start_editing("create_plan_title", "", None)

        @kb.add("p", filter=not_editing)
        @kb.add("з", filter=not_editing)
        def _(event):
            if not self.project_mode and not self.detail_mode:
                self.return_to_projects_fast()
                return

        @kb.add("down", filter=not_editing)
        @kb.add("j", filter=not_editing)
        @kb.add("о", filter=not_editing)
        def _(event):
            if self.settings_mode and not self.editing_mode:
                self.move_settings_selection(1)
                return
            if getattr(self, "list_editor_mode", False) and not self.editing_mode:
                self.move_list_editor_selection(1)
                return
            self.move_vertical_selection(1)

        @kb.add("down", eager=True, filter=list_editor_active)
        @kb.add("j", eager=True, filter=list_editor_active)
        @kb.add("о", eager=True, filter=list_editor_active)
        def _(event):
            self.move_list_editor_selection(1)

        @kb.add(Keys.ScrollDown, filter=not_editing)
        def _(event):
            self.move_vertical_selection(1)

        @kb.add("up", filter=not_editing)
        @kb.add("k", filter=not_editing)
        @kb.add("л", filter=not_editing)
        def _(event):
            if self.settings_mode and not self.editing_mode:
                self.move_settings_selection(-1)
                return
            if getattr(self, "list_editor_mode", False) and not self.editing_mode:
                self.move_list_editor_selection(-1)
                return
            self.move_vertical_selection(-1)

        @kb.add("up", eager=True, filter=list_editor_active)
        @kb.add("k", eager=True, filter=list_editor_active)
        @kb.add("л", eager=True, filter=list_editor_active)
        def _(event):
            self.move_list_editor_selection(-1)

        @kb.add(Keys.ScrollUp, filter=not_editing)
        def _(event):
            self.move_vertical_selection(-1)

        @kb.add("1", filter=not_editing)
        def _(event):
            self.current_filter = None
            self.selected_index = 0

        @kb.add("2", filter=not_editing)
        def _(event):
            self.current_filter = Status.ACTIVE  # ACTIVE
            self.selected_index = 0

        @kb.add("3", filter=not_editing)
        def _(event):
            self.current_filter = Status.TODO  # TODO
            self.selected_index = 0

        @kb.add("4", filter=not_editing)
        def _(event):
            self.current_filter = Status.DONE  # DONE
            self.selected_index = 0

        @kb.add("?", filter=not_editing)
        def _(event):
            if not getattr(self, "help_visible", False):
                self._footer_height_after_help = int(getattr(self, "footer_height", 0) or 0)
                self.help_visible = True
                # Ensure help is visible even in detail views (footer is usually hidden there).
                self._set_footer_height(12)
                return
            # Close help.
            self.help_visible = False
            restore = self._footer_height_default_for_mode()
            if self._footer_height_after_help is not None:
                restore = int(self._footer_height_after_help)
            self._footer_height_after_help = None
            self._set_footer_height(restore)

        @kb.add("H", filter=not_editing)
        def _(event):
            """H - export handoff snapshot for the current task/plan."""
            if getattr(self, "project_mode", False) or getattr(self, "search_mode", False):
                return
            if getattr(self, "confirm_mode", False) or getattr(self, "settings_mode", False) or getattr(self, "checkpoint_mode", False):
                return
            if getattr(self, "list_editor_mode", False) or getattr(self, "editing_mode", False):
                return
            self.export_handoff()

        @kb.add(":", filter=not_editing)
        def _(event):
            """Command palette (single-line)."""
            if getattr(self, "project_mode", False):
                return
            if getattr(self, "confirm_mode", False) or getattr(self, "settings_mode", False) or getattr(self, "checkpoint_mode", False):
                return
            if getattr(self, "list_editor_mode", False):
                return
            self.start_editing("command_palette", "", None)

        @kb.add("tab", filter=section_toggle_allowed)
        def _(event):
            """Toggle Plans/Tasks within the current project."""
            self.toggle_project_section()

        @kb.add("tab", filter=detail_tab_allowed)
        def _(event):
            self.cycle_detail_tab(1)

        @kb.add("c", filter=radar_tab_allowed)
        @kb.add("с", filter=radar_tab_allowed)
        def _(event):
            """c - copy current radar next suggestion (JSON)."""
            self.copy_radar_next()

        @kb.add("t", filter=not_editing)
        @kb.add("е", filter=not_editing)
        def _(event):
            """t - перейти к задачам выбранного плана / сбросить фильтр задач."""
            if getattr(self, "project_mode", False):
                return
            if getattr(self, "confirm_mode", False) or getattr(self, "editing_mode", False):
                return
            if getattr(self, "detail_mode", False) and getattr(self, "current_task_detail", None):
                detail = self.current_task_detail
                if self._is_plan_detail(detail) or getattr(self, "_detail_source_section", "") == "plans":
                    self.open_tasks_for_plan(detail)
                return
            if getattr(self, "project_section", "tasks") == "plans" and not getattr(self, "detail_mode", False):
                if self.filtered_tasks:
                    task = self.filtered_tasks[self.selected_index]
                    det = task.detail
                    if not det and task.task_file:
                        try:
                            det = TaskFileParser.parse(Path(task.task_file))
                        except Exception:
                            det = None
                    if det:
                        self.open_tasks_for_plan(det)
                return
            # In tasks view, 't' clears plan filter if active.
            if getattr(self, "plan_filter_id", None):
                self.plan_filter_id = None
                self.plan_filter_title = None
                self.load_current_list(preserve_selection=True, skip_sync=True)
                self.force_render()

        @kb.add("/", filter=search_input_allowed)
        def _(event):
            """Enter search input mode (type to filter projects/tasks)."""
            self.search_mode = True
            self.force_render()

        @kb.add("c-u", filter=search_input_allowed)
        def _(event):
            """Clear search query."""
            if not getattr(self, "search_query", "") and not getattr(self, "search_mode", False):
                return
            self.search_query = ""
            self.search_mode = False
            self._clamp_selection_to_filtered()
            self.force_render()

        @kb.add("backspace", eager=True, filter=search_input_active)
        @kb.add("c-h", eager=True, filter=search_input_active)
        def _(event):
            if self.search_query:
                self.search_query = self.search_query[:-1]
                self._clamp_selection_to_filtered()
            self.force_render()

        @kb.add(Keys.Any, eager=True, filter=search_input_active)
        def _(event):
            """Type-to-filter when search_mode is active."""
            key = event.key_sequence[0].key if event.key_sequence else None
            if not isinstance(key, str):
                return
            # Ignore special keys (they arrive as multi-char tokens like 'up', 'enter', etc.)
            if len(key) != 1:
                return
            if not key.isprintable():
                return
            self.search_query += key
            self._clamp_selection_to_filtered()
            self.force_render()

        @kb.add("c-s", filter=editing_multiline_active)
        def _(event):
            """Ctrl+S - save multiline edits."""
            self.save_edit()

        @kb.add("enter", filter=editing_singleline_active)
        def _(event):
            """Enter - save single-line edits."""
            self.save_edit()

        @kb.add("enter", filter=not_editing)
        def _(event):
            """Enter - раскрыть/свернуть или войти в детали"""
            if getattr(self, "confirm_mode", False):
                self._confirm_accept()
                return
            if getattr(self, "search_mode", False) and not self.editing_mode:
                self.search_mode = False
                self.force_render()
                return
            if self.settings_mode and not self.editing_mode:
                self.activate_settings_option()
                return
            if getattr(self, "list_editor_mode", False) and not self.editing_mode:
                self.activate_list_editor()
                return
            if self.detail_mode and self.current_task_detail:
                current_tab = getattr(self, "detail_tab", "radar") or "radar"
                if current_tab == "radar":
                    self.execute_radar_next()
                    return
                if current_tab != "overview":
                    return
                if getattr(self.current_task_detail, "kind", "task") == "plan":
                    self._open_selected_plan_task_detail()
                    return
                # В режиме деталей Enter раскрывает/сворачивает подзадачу
                entry = self._selected_subtask_entry()
                if entry:
                    path = entry.key
                    collapsed = entry.collapsed
                    has_children = entry.has_children
                    if has_children:
                        # Toggle collapse state
                        self._toggle_collapse_selected(expand=collapsed)
                    else:
                        # Для листовых подзадач показываем карточку
                        self.show_subtask_details(path)
            else:
                if self.filtered_tasks:
                    self.show_task_details(self.filtered_tasks[self.selected_index])

        @kb.add("enter", eager=True, filter=list_editor_active)
        def _(event):
            self.activate_list_editor()

        @kb.add("space", eager=True, filter=list_editor_active)
        def _(event):
            self._list_editor_toggle_plan_steps_current()

        @kb.add("escape", eager=True)
        def _(event):
            if getattr(self, "help_visible", False):
                self.help_visible = False
                restore = self._footer_height_default_for_mode()
                if self._footer_height_after_help is not None:
                    restore = int(self._footer_height_after_help)
                self._footer_height_after_help = None
                self._set_footer_height(restore)
                self.force_render()
                return
            if getattr(self, "confirm_mode", False):
                self._confirm_cancel()
                return
            if getattr(self, "search_mode", False) and not self.editing_mode:
                self.search_mode = False
                self.force_render()
                return
            if self.editing_mode:
                # В режиме редактирования - отменить
                self.cancel_edit()
            elif self.settings_mode:
                self.close_settings_dialog()
            elif getattr(self, "list_editor_mode", False):
                self.exit_list_editor()
            elif self.checkpoint_mode:
                # checkpoint_mode проверяем ДО detail_mode, т.к. checkpoint внутри detail
                self.exit_checkpoint_mode()
            elif self.detail_mode:
                self.exit_detail_view()
            elif not self.project_mode:
                self.navigate_back()

        @kb.add("delete", filter=not_editing)
        @kb.add("c-d", filter=not_editing)
        @kb.add("x", filter=not_editing)
        @kb.add("ч", filter=not_editing)
        def _(event):
            """Delete/x - удалить выбранную задачу или подзадачу"""
            if getattr(self, "list_editor_mode", False) and not self.editing_mode:
                self.confirm_delete_list_editor_item()
                return
            if getattr(self, "detail_mode", False) and getattr(self, "detail_tab", "overview") != "overview":
                return
            self.confirm_delete_current_item()

        @kb.add("delete", eager=True, filter=list_editor_active)
        @kb.add("c-d", eager=True, filter=list_editor_active)
        @kb.add("x", eager=True, filter=list_editor_active)
        @kb.add("ч", eager=True, filter=list_editor_active)
        def _(event):
            self.confirm_delete_list_editor_item()

        @kb.add("y", filter=confirm_active)
        @kb.add("н", filter=confirm_active)  # RU layout for 'y'
        def _(event):
            self._confirm_accept()

        @kb.add("n", filter=confirm_active)
        @kb.add("т", filter=confirm_active)  # RU layout for 'n'
        def _(event):
            self._confirm_cancel()

        @kb.add("d", filter=not_editing)
        @kb.add("в", filter=not_editing)
        def _(event):
            """d - показать детали (карточку) подзадачи"""
            if self.detail_mode and self.current_task_detail:
                if getattr(self, "detail_tab", "overview") != "overview":
                    return
                if getattr(self.current_task_detail, "kind", "task") == "plan":
                    self._open_selected_plan_task_detail()
                    return
                entry = self._selected_subtask_entry()
                if entry:
                    self.show_subtask_details(entry.key)

        @kb.add("e", filter=not_editing)
        @kb.add("у", filter=not_editing)
        def _(event):
            """e - редактировать"""
            if getattr(self, "list_editor_mode", False) and not self.editing_mode:
                self.edit_list_editor_item()
                return
            if getattr(self, "detail_mode", False) and getattr(self, "current_task_detail", None):
                tab = getattr(self, "detail_tab", "overview") or "overview"
                detail = self.current_task_detail
                if tab == "overview":
                    self.edit_current_item()
                    return
                if tab == "notes":
                    self.start_editing("task_description", str(getattr(detail, "description", "") or ""), None)
                    return
                if tab == "plan":
                    self.start_editing("task_plan_doc", str(getattr(detail, "plan_doc", "") or ""), None)
                    return
                if tab == "contract":
                    self.start_editing("task_contract", str(getattr(detail, "contract", "") or ""), None)
                    return
                if tab == "meta":
                    self.open_list_editor()
                    return
            if not self.editing_mode:
                self.edit_current_item()

        @kb.add("E", filter=not_editing)
        @kb.add("У", filter=not_editing)
        def _(event):
            """Shift+E - edit task context (Notes tab)."""
            if getattr(self, "detail_mode", False) and getattr(self, "current_task_detail", None):
                if getattr(self, "detail_tab", "overview") == "notes":
                    detail = self.current_task_detail
                    self.start_editing("task_context", str(getattr(detail, "context", "") or ""), None)

        @kb.add("e", eager=True, filter=list_editor_active)
        @kb.add("у", eager=True, filter=list_editor_active)
        def _(event):
            self.edit_list_editor_item()

        @kb.add("l", filter=not_editing)
        @kb.add("д", filter=not_editing)
        def _(event):
            """l - open list editor for task/subtask lists."""
            if getattr(self, "confirm_mode", False) or getattr(self, "settings_mode", False) or getattr(self, "checkpoint_mode", False):
                return
            if self.editing_mode:
                return
            if getattr(self, "detail_mode", False) and getattr(self, "current_task_detail", None):
                self.open_list_editor()

        @kb.add("a", filter=not_editing)
        @kb.add("ф", filter=not_editing)
        def _(event):
            """a - add list item (list editor mode only)."""
            if getattr(self, "list_editor_mode", False) and not self.editing_mode:
                self.add_list_editor_item()

        @kb.add("a", eager=True, filter=list_editor_active)
        @kb.add("ф", eager=True, filter=list_editor_active)
        def _(event):
            self.add_list_editor_item()

        @kb.add("g", filter=not_editing)
        @kb.add("п", filter=not_editing)
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
        @kb.add("c-left", filter=not_editing)
        def _(event):
            """Ctrl+Left - scroll content left"""
            self.horizontal_offset = max(0, self.horizontal_offset - 5)

        @kb.add("c-right", filter=not_editing)
        def _(event):
            """Ctrl+Right - scroll content right"""
            self.horizontal_offset = min(200, self.horizontal_offset + 5)

        @kb.add("[", filter=not_editing)
        def _(event):
            """[ - scroll content left (alternative)"""
            self.horizontal_offset = max(0, self.horizontal_offset - 3)

        @kb.add("]", filter=not_editing)
        def _(event):
            """] - scroll content right (alternative)"""
            self.horizontal_offset = min(200, self.horizontal_offset + 3)

        @kb.add("left", filter=not_editing)
        def _(event):
            """Left - collapse or go to parent in detail tree, or prev checkpoint/exit"""
            if self.checkpoint_mode:
                if self.checkpoint_selected_index == 0:
                    # На первом чекпоинте - выйти из режима
                    self.exit_checkpoint_mode()
                else:
                    self.move_checkpoint_selection(-1)
                return
            if getattr(self, "list_editor_mode", False):
                return
            if self.detail_mode:
                if getattr(self, "detail_tab", "overview") != "overview":
                    self.cycle_detail_tab(-1)
                    return
                entry = self._selected_subtask_entry()
                if entry:
                    path = entry.key
                    collapsed = entry.collapsed
                    has_children = entry.has_children
                    if has_children and not collapsed:
                        self._toggle_collapse_selected(expand=False)
                        return
                    # go one level up in tree if possible
                    parent_key = getattr(entry, "parent_key", None)
                    if parent_key:
                        self._select_step_by_path(parent_key)
                        self.force_render()
                        return
                self.exit_detail_view()
                return
            if not self.project_mode:
                self.return_to_projects_fast()
                return
            # в списке проектов/задач без project_mode: оставляем без действия

        @kb.add("right", filter=not_editing)
        def _(event):
            """Right - expand or go to first child in detail tree, or next checkpoint"""
            if self.checkpoint_mode:
                self.move_checkpoint_selection(1)
                return
            if getattr(self, "list_editor_mode", False):
                return
            if not self.detail_mode:
                if self.filtered_tasks:
                    self.show_task_details(self.filtered_tasks[self.selected_index])
                return
            if getattr(self, "detail_tab", "overview") != "overview":
                self.cycle_detail_tab(1)
                return
            if self.current_task_detail and getattr(self.current_task_detail, "kind", "task") == "plan":
                self._open_selected_plan_task_detail()
                return
            entry = self._selected_subtask_entry()
            if not entry:
                return
            path = entry.key
            collapsed = entry.collapsed
            has_children = entry.has_children
            if has_children and collapsed:
                self._toggle_collapse_selected(expand=True)
                return
            self.show_subtask_details(path)

        @kb.add("z", filter=not_editing)
        @kb.add("я", filter=not_editing)
        def _(event):
            """z - collapse all descendants in detail tree (keep current node visible)."""
            if getattr(self, "list_editor_mode", False) or getattr(self, "checkpoint_mode", False):
                return
            if not self.detail_mode or getattr(self, "detail_tab", "overview") != "overview":
                return
            if self.current_task_detail and getattr(self.current_task_detail, "kind", "task") == "plan":
                return
            collapse_subtask_descendants(self)

        @kb.add("Z", filter=not_editing)
        @kb.add("Я", filter=not_editing)
        def _(event):
            """Shift+Z - expand current node and all descendants in detail tree."""
            if getattr(self, "list_editor_mode", False) or getattr(self, "checkpoint_mode", False):
                return
            if not self.detail_mode or getattr(self, "detail_tab", "overview") != "overview":
                return
            if self.current_task_detail and getattr(self.current_task_detail, "kind", "task") == "plan":
                return
            expand_subtask_descendants(self)

        @kb.add("c", filter=checkpoint_open_allowed)
        @kb.add("с", filter=checkpoint_open_allowed)
        def _(event):
            """c - открыть режим чекпоинтов"""
            self.enter_checkpoint_mode()

        @kb.add("space", filter=not_editing)
        def _(event):
            """Space - переключить выполнение задачи/подзадачи или чекпоинта"""
            if getattr(self, "search_mode", False) and not self.editing_mode:
                # In search input mode, space is a query character.
                self.search_query += " "
                self._clamp_selection_to_filtered()
                self.force_render()
                return
            if getattr(self, "list_editor_mode", False) and not self.editing_mode:
                return
            if self.checkpoint_mode:
                self.toggle_checkpoint_state()
            elif self.detail_mode and self.current_task_detail:
                if getattr(self, "detail_tab", "overview") != "overview":
                    return
                self.toggle_subtask_completion()
            else:
                # В списке задач - переключить статус задачи
                self.toggle_task_completion()

        @kb.add("!", filter=not_editing)
        def _(event):
            """Explicit force completion (never default)."""
            if getattr(self, "search_mode", False) or self.editing_mode:
                return
            if getattr(self, "detail_mode", False) and getattr(self, "detail_tab", "overview") != "overview":
                return
            self.confirm_force_complete_current()

        @kb.add("home", filter=not_editing)
        def _(event):
            """Home - reset scroll"""
            self.horizontal_offset = 0

        self.status_bar = Window(content=FormattedTextControl(self.get_status_text), height=1, always_hide_cursor=True)
        self.task_list = Window(content=FormattedTextControl(self.get_task_list_text), always_hide_cursor=True, wrap_lines=False)
        self.side_preview = Window(content=FormattedTextControl(self.get_side_preview_text), always_hide_cursor=True, wrap_lines=True, width=Dimension(weight=2))
        self.detail_view = Window(content=FormattedTextControl(self.get_detail_text), always_hide_cursor=True, wrap_lines=True)
        self.footer_control = InteractiveFormattedTextControl(
            self.get_footer_text,
            show_cursor=False,
            focusable=False,
            mouse_handler=self._handle_footer_mouse,
        )
        self.footer = Window(content=self.footer_control, height=Dimension(min=self.footer_height, max=self.footer_height), always_hide_cursor=True)

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
        # Make Esc responsive: prompt_toolkit defaults ttimeoutlen=0.5s to disambiguate
        # between a standalone Escape and ANSI key sequences (arrows, etc.).
        # Allow override for slow terminals/SSH sessions.
        try:
            self.app.ttimeoutlen = max(0.0, float(os.getenv("APPLY_TASK_TUI_TTIMEOUTLEN", "0.05")))
        except Exception:
            self.app.ttimeoutlen = 0.05

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
        if getattr(self, "help_visible", False):
            # While help is visible, the footer must stay tall enough to render it.
            # Store the desired post-help height whenever callers try to resize.
            if lines != 12:
                self._footer_height_after_help = int(lines)
            lines = 12
        self.footer_height = lines
        try:
            self.footer.height = Dimension(min=lines, max=lines)
        except Exception:
            # UI may not be built yet; storing height is enough
            pass
        self.force_render()

    def _footer_height_default_for_mode(self) -> int:
        """Compute the default footer height for the current UI mode (help excluded)."""
        if getattr(self, "confirm_mode", False):
            return 0
        if getattr(self, "settings_mode", False):
            return 0
        if getattr(self, "checkpoint_mode", False):
            return 0
        if getattr(self, "list_editor_mode", False):
            return 0
        if getattr(self, "detail_mode", False):
            return 4
        return 4

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

    @staticmethod
    def _display_subtask_path(path: str) -> str:
        """Human-friendly 1-based subtask path for UI (internal paths stay 0-based)."""
        raw = str(path or "")
        if not raw:
            return ""
        parts: List[str] = []
        for segment in raw.split("."):
            if ":" not in segment:
                parts.append(segment)
                continue
            kind, raw_idx = segment.split(":", 1)
            try:
                idx = int(raw_idx)
                label = str(idx + 1)
            except Exception:
                label = raw_idx
            if kind == "t":
                parts.append(f"T{label}")
            else:
                parts.append(label)
        return ".".join(parts)

    def _rebuild_detail_flat(self, selected_path: Optional[str] = None) -> None:
        if not self.current_task_detail:
            self.detail_flat_subtasks = []
            self.detail_stats_by_key = {}
            self.detail_selected_index = 0
            self.detail_selected_path = ""
            self.detail_flat_dirty = False
            return
        selected_path = str(selected_path or "")
        if selected_path:
            kind = _detail_node_kind(selected_path)
            if kind != "step":
                canonical = _detail_canonical_path(selected_path, kind)
                if ".t:" in canonical:
                    canonical = canonical.split(".t:", 1)[0]
                selected_path = canonical
        # TUI drill-down model: Task details show only one level (Steps).
        # Deeper levels are reached via show_subtask_details() / plan detail view.
        steps = list(getattr(self.current_task_detail, "steps", []) or [])
        flat: List[DetailNodeEntry] = []
        stats: Dict[str, DetailNodeStats] = {}
        for idx, step in enumerate(steps):
            key = f"s:{idx}"
            plan = getattr(step, "plan", None)
            plan_tasks = list(getattr(plan, "tasks", []) or []) if plan else []
            total = len(plan_tasks)
            done = sum(1 for t in plan_tasks if getattr(t, "is_done", lambda: False)())
            if getattr(step, "completed", False):
                progress = 100
            else:
                progress = int((done / total) * 100) if total else 0
            status = step.status_value() if hasattr(step, "status_value") else Status.TODO

            flat.append(
                DetailNodeEntry(
                    key=key,
                    kind="step",
                    node=step,
                    level=0,
                    # No inline expansion/collapse: Enter/→ always drills down.
                    collapsed=False,
                    has_children=False,
                    parent_key=None,
                )
            )
            stats[key] = DetailNodeStats(
                progress=progress,
                children_done=done,
                children_total=total,
                status=status,
            )

        self.detail_flat_subtasks = flat
        self.detail_stats_by_key = stats

        if selected_path:
            for idx, entry in enumerate(flat):
                if entry.key == selected_path:
                    self.detail_selected_index = idx
                    self.detail_selected_path = entry.key
                    return
        if not flat:
            self.detail_selected_index = 0
            self.detail_selected_path = ""
            self.detail_flat_dirty = False
            return
        self.detail_selected_index = max(0, min(self.detail_selected_index, len(flat) - 1))
        self.detail_selected_path = flat[self.detail_selected_index].key
        self.detail_flat_dirty = False

    def _ensure_detail_flat(self, selected_path: Optional[str] = None) -> None:
        """Rebuild detail flat list only when it is marked dirty."""
        if self.detail_flat_dirty or not self.detail_flat_subtasks:
            self._rebuild_detail_flat(selected_path)
            return
        if selected_path:
            self._select_step_by_path(selected_path)

    def _selected_subtask_entry(self) -> Optional[DetailNodeEntry]:
        if not self.detail_flat_subtasks:
            return None
        idx = max(0, min(self.detail_selected_index, len(self.detail_flat_subtasks) - 1))
        self.detail_selected_index = idx
        entry = self.detail_flat_subtasks[idx]
        self.detail_selected_path = entry.key
        return entry

    def _select_step_by_path(self, path: str) -> None:
        if not self.detail_flat_subtasks:
            self.detail_selected_index = 0
            self.detail_selected_path = ""
            return
        probe = str(path or "")
        for idx, entry in enumerate(self.detail_flat_subtasks):
            if entry.key == probe:
                self.detail_selected_index = idx
                self.detail_selected_path = entry.key
                return
        self.detail_selected_index = max(0, min(self.detail_selected_index, len(self.detail_flat_subtasks) - 1))
        self.detail_selected_path = self.detail_flat_subtasks[self.detail_selected_index].key

    # Back-compat: tui_state may call this helper name.
    def _select_subtask_by_path(self, path: str) -> None:
        self._select_step_by_path(path)

    def _get_step_by_path(self, path: str) -> Optional[Step]:
        if not self.current_task_detail or not path:
            return None
        kind = _detail_node_kind(path)
        if kind != "step":
            return None
        st, _, _ = _find_step_by_path(self.current_task_detail.steps, str(path))
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
        # Each entry in stack has "entered_path" (canonical) which is the path we entered
        path_parts = []
        for ctx in self.navigation_stack:
            entered = ctx.get("entered_path", ctx.get("selected_path", ""))
            if entered:
                path_parts.append(str(entered))

        path_prefix = ".".join(path_parts) if path_parts else ""
        return root_task_id, root_domain, path_prefix

    def _step_to_task_detail(self, step: Step, root_task_id: str, path_prefix: str) -> TaskDetail:
        """Project a nested Step into a TaskDetail-like view model for nested navigation."""
        status = "DONE" if step.completed else ("ACTIVE" if step.ready_for_completion() else "TODO")
        plan = getattr(step, "plan", None)
        if plan and getattr(plan, "tasks", None) is not None:
            title = str(getattr(plan, "title", "") or "") or step.title
            detail = TaskDetail(
                id=root_task_id,
                title=title,
                status=status,
                kind="plan",
                parent=getattr(self.current_task_detail, "parent", None) if self.current_task_detail else None,
            )
            detail.plan_doc = str(getattr(plan, "doc", "") or "")
            detail.plan_steps = list(getattr(plan, "steps", []) or [])
            detail.plan_current = int(getattr(plan, "current", 0) or 0)
            detail.success_criteria = list(getattr(plan, "success_criteria", []) or [])
            detail.tests = list(getattr(plan, "tests", []) or [])
            detail.blockers = list(getattr(plan, "blockers", []) or [])
            detail.criteria_confirmed = bool(getattr(plan, "criteria_confirmed", False))
            detail.tests_confirmed = bool(getattr(plan, "tests_confirmed", False))
            detail.criteria_auto_confirmed = bool(getattr(plan, "criteria_auto_confirmed", False))
            detail.tests_auto_confirmed = bool(getattr(plan, "tests_auto_confirmed", False))
            detail.criteria_notes = list(getattr(plan, "criteria_notes", []) or [])
            detail.tests_notes = list(getattr(plan, "tests_notes", []) or [])
            setattr(detail, "_embedded_plan_tasks", list(getattr(plan, "tasks", []) or []))
            setattr(detail, "_nested_path_prefix", path_prefix)
            return detail

        detail = TaskDetail(
            id=root_task_id,
            title=step.title,
            status=status,
            kind=getattr(self.current_task_detail, "kind", "task") if self.current_task_detail else "task",
            parent=getattr(self.current_task_detail, "parent", None) if self.current_task_detail else None,
        )
        detail.steps = []
        # Surface step fields in task-level meta sections for convenience.
        detail.success_criteria = list(step.success_criteria or [])
        detail.tests = list(getattr(step, "tests", []) or [])
        detail.blockers = list(step.blockers or [])
        detail.criteria_confirmed = bool(getattr(step, "criteria_confirmed", False))
        detail.tests_confirmed = bool(getattr(step, "tests_confirmed", False))
        detail.criteria_auto_confirmed = bool(getattr(step, "criteria_auto_confirmed", False))
        detail.tests_auto_confirmed = bool(getattr(step, "tests_auto_confirmed", False))
        detail.criteria_notes = list(getattr(step, "criteria_notes", []) or [])
        detail.tests_notes = list(getattr(step, "tests_notes", []) or [])
        setattr(detail, "_nested_path_prefix", path_prefix)
        return detail

    def _task_node_to_task_detail(self, node, root_task_id: str, path_prefix: str) -> TaskDetail:
        status_raw = str(getattr(node, "status", "") or "TODO").strip().upper() or "TODO"
        detail = TaskDetail(
            id=root_task_id,
            title=str(getattr(node, "title", "") or ""),
            status=status_raw,
            kind="task",
            parent=getattr(self.current_task_detail, "parent", None) if self.current_task_detail else None,
        )
        detail.steps = list(getattr(node, "steps", []) or [])
        detail.description = str(getattr(node, "description", "") or "")
        detail.context = str(getattr(node, "context", "") or "")
        detail.success_criteria = list(getattr(node, "success_criteria", []) or [])
        detail.tests = list(getattr(node, "tests", []) or [])
        detail.criteria_confirmed = bool(getattr(node, "criteria_confirmed", False))
        detail.tests_confirmed = bool(getattr(node, "tests_confirmed", False))
        detail.criteria_auto_confirmed = bool(getattr(node, "criteria_auto_confirmed", False))
        detail.tests_auto_confirmed = bool(getattr(node, "tests_auto_confirmed", False))
        detail.criteria_notes = list(getattr(node, "criteria_notes", []) or [])
        detail.tests_notes = list(getattr(node, "tests_notes", []) or [])
        detail.dependencies = list(getattr(node, "dependencies", []) or [])
        detail.next_steps = list(getattr(node, "next_steps", []) or [])
        detail.problems = list(getattr(node, "problems", []) or [])
        detail.risks = list(getattr(node, "risks", []) or [])
        detail.blocked = bool(getattr(node, "blocked", False))
        detail.blockers = list(getattr(node, "blockers", []) or [])
        setattr(detail, "_nested_path_prefix", path_prefix)
        return detail

    def _derive_nested_detail(self, root_detail: TaskDetail, root_task_id: str, path_prefix: str) -> Optional[TaskDetail]:
        if not path_prefix:
            return root_detail
        last_segment = path_prefix.split(".")[-1]
        if last_segment.startswith("t:"):
            task_node, _, _ = _find_task_by_path(root_detail.steps, path_prefix)
            if task_node:
                return self._task_node_to_task_detail(task_node, root_task_id, path_prefix)
            return None
        step, _, _ = _find_step_by_path(root_detail.steps, path_prefix)
        if step:
            return self._step_to_task_detail(step, root_task_id, path_prefix)
        return None

    def _toggle_collapse_selected(self, expand: bool) -> None:

        toggle_subtask_collapse(self, expand)
        if self.current_task_detail:
            self.collapsed_by_task[self.current_task_detail.id] = set(self.detail_collapsed)
        self.force_render()

    def _ensure_settings_selection_visible(self, total: int) -> None:
        visible = self._visible_row_limit()
        if total <= visible:
            self.settings_view_offset = 0
            return

    def _auto_enter_default_project(self) -> None:
        """Always start with project picker; preselect current project namespace."""
        default_dir = self.tasks_dir.resolve()
        self.manager = TaskManager(default_dir)
        self.project_mode = True
        self.detail_mode = False
        self.current_task_detail = None
        self.current_task = None
        self.navigation_stack = []
        self.load_projects()

        for idx, project in enumerate(self.tasks):
            path_raw = getattr(project, "task_file", None)
            if path_raw and Path(path_raw).resolve() == default_dir:
                self.selected_index = idx
                break
        self._ensure_selection_visible()
        if not self.tasks:
            self.set_status_message(self._t("STATUS_MESSAGE_NO_TASKS", fallback="Нет проектов в хранилище ~/.tasks"), ttl=4)
        self.force_render()

    def _auto_enter_local_project(self) -> None:
        """Start directly inside the current project when using local storage."""
        self.project_mode = False
        self.detail_mode = False
        self.current_task_detail = None
        self.current_task = None
        self.navigation_stack = []
        self.current_project_path = self.tasks_dir.resolve()
        self.manager = TaskManager(self.tasks_dir)
        # Default entry point inside a project is the Plans view (contract → plan → tasks).
        self.project_section = "plans"
        self.plan_filter_id = None
        self.plan_filter_title = None
        self.load_plans(skip_sync=True)
        self.set_status_message(self._t("STATUS_MESSAGE_LOCAL_MODE"), ttl=2)
        self.force_render()

    # ---------- Project selection ----------

    def load_projects(self) -> None:
        """Load list of projects from global storage and show in list view."""
        self.project_mode = True
        self.detail_mode = False
        self.current_task_detail = None
        self.current_task = None
        self.navigation_stack = []

        projects = self._build_projects_list()
        self.tasks = list(projects)
        self._projects_cache = list(projects)
        self._projects_cache_fingerprint = tuple(
            (
                getattr(p, "name", ""),
                getattr(getattr(p, "status", None), "value", ("",))[0],
                int(getattr(p, "progress", 0) or 0),
                int(getattr(p, "children_count", 0) or 0),
                int(getattr(p, "children_completed", 0) or 0),
            )
            for p in projects
        )
        self.selected_index = 0
        self.current_filter = None
        self._ensure_selection_visible()
        self.force_render()

    @staticmethod
    def _normalize_tag(value: str) -> str:
        return _normalize_tag(value)

    def _is_plan_detail(self, detail: "TaskDetail") -> bool:
        try:
            return _is_plan_task(detail)
        except Exception:
            return False

    def _section_key(self) -> str:
        section = getattr(self, "project_section", "tasks") or "tasks"
        if section == "tasks":
            plan_id = (getattr(self, "plan_filter_id", None) or "").strip()
            if plan_id:
                return f"tasks:{plan_id}"
        return section

    def load_plans(self, preserve_selection: bool = False, selected_task_file: Optional[str] = None, skip_sync: bool = False) -> None:
        """Load plans for the current project.

        Plans are the project entry points that hold user intent (contract) and plan (doc/steps),
        typically represented as top-level tasks (parent is empty/ROOT). Tasks tagged with `plan`
        are also treated as plans.
        """
        with self._spinner(self._t("SPINNER_REFRESH_PLANS", fallback=self._t("SPINNER_REFRESH_TASKS"))):
            details = self.manager.list_tasks("", skip_sync=skip_sync)
        plan_counts = self._plan_task_counts(details)
        seen: Set[str] = set()
        plans: List["TaskDetail"] = []
        for det in details:
            if not self._is_plan_detail(det):
                continue
            det_id = str(getattr(det, "id", "") or "")
            if det_id and det_id in seen:
                continue
            plans.append(det)
            if det_id:
                seen.add(det_id)
        details = plans

        def _task_factory(det, derived_status, calc_progress, _steps_completed, _steps_total):
            task_file = f".tasks/{det.domain + '/' if det.domain else ''}{det.id}.task"
            snippet = (det.contract or det.description or det.context or "")[:80]
            total, done = plan_counts.get(str(getattr(det, "id", "") or ""), (0, 0))
            return Task(
                id=det.id,
                name=det.title,
                status=derived_status,
                description=snippet,
                category="plan",
                completed=derived_status == Status.DONE,
                task_file=task_file,
                progress=calc_progress,
                children_count=total,
                children_completed=done,
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
        self._remember_last_plan_selection()
        self._section_cache["plans"] = list(self.tasks)

    def _remember_last_plan_selection(self) -> None:
        """Select plan that the user navigated from (one-shot)."""
        plan_id = str(getattr(self, "_pending_select_plan_id", "") or "").strip()
        if not plan_id:
            return
        try:
            items = self.filtered_tasks
            for idx, task in enumerate(items):
                if str(getattr(task, "id", "") or "") == plan_id:
                    self.selected_index = idx
                    self._ensure_selection_visible()
                    break
        finally:
            self._pending_select_plan_id = None

    def load_current_list(self, preserve_selection: bool = False, selected_task_file: Optional[str] = None, skip_sync: bool = False) -> None:
        """Reload current list view (plans or tasks) without changing navigation."""
        if getattr(self, "project_mode", False):
            self.load_projects()
            return
        section = getattr(self, "project_section", "tasks") or "tasks"
        if section == "plans":
            self.load_plans(preserve_selection=preserve_selection, selected_task_file=selected_task_file, skip_sync=skip_sync)
            return
        self.load_tasks(preserve_selection=preserve_selection, selected_task_file=selected_task_file, skip_sync=skip_sync, plan_parent=getattr(self, "plan_filter_id", None))

    def toggle_project_section(self) -> None:
        """Toggle between Plans and Tasks within a project (list view only)."""
        if getattr(self, "project_mode", False) or getattr(self, "detail_mode", False):
            return
        current_key = self._section_key()
        selected_task_file = self.tasks[self.selected_index].task_file if self.tasks and 0 <= self.selected_index < len(self.tasks) else ""
        if selected_task_file:
            self._section_selected_task_file[current_key] = selected_task_file

        current = getattr(self, "project_section", "tasks") or "tasks"
        if current == "plans":
            self.project_section = "tasks"
            self.plan_filter_id = None
            self.plan_filter_title = None
            target_key = "tasks"
        else:
            # Remember which plan we came from when leaving a filtered tasks list.
            if getattr(self, "plan_filter_id", None):
                self._pending_select_plan_id = str(getattr(self, "plan_filter_id", "") or "").strip() or None
            self.project_section = "plans"
            self.plan_filter_id = None
            self.plan_filter_title = None
            target_key = "plans"

        restore_file = self._section_selected_task_file.get(target_key, "")
        cache_map = getattr(self, "_section_cache", None) or {}
        if target_key in cache_map:
            cached = list(cache_map.get(target_key) or [])
            self.tasks = cached
            self.selected_index = select_index_after_load(self.tasks, bool(restore_file), restore_file)
            self.detail_mode = False
            self.current_task = None
            self.current_task_detail = None
            if self.selected_index >= len(self.filtered_tasks):
                self.selected_index = max(0, len(self.filtered_tasks) - 1)
            self._ensure_selection_visible()
            self._remember_last_plan_selection()
            self.force_render()
            return

        self.load_current_list(preserve_selection=bool(restore_file), selected_task_file=restore_file, skip_sync=True)
        self.force_render()

    def open_tasks_for_plan(self, plan_detail: "TaskDetail") -> None:
        """Switch to Tasks view filtered by parent==plan.id (from a plan card)."""
        if not plan_detail:
            return
        self.navigation_stack = []
        self.detail_mode = False
        self.current_task = None
        self.current_task_detail = None
        self._set_footer_height(self._footer_height_default_for_mode())
        self.project_section = "tasks"
        self.plan_filter_id = plan_detail.id
        self.plan_filter_title = plan_detail.title
        restore_key = self._section_key()
        restore_file = self._section_selected_task_file.get(restore_key, "")
        cache_map = getattr(self, "_section_cache", None) or {}
        if restore_key in cache_map:
            cached = list(cache_map.get(restore_key) or [])
            self.tasks = cached
            self.selected_index = select_index_after_load(self.tasks, bool(restore_file), restore_file)
            self.detail_mode = False
            self.current_task = None
            self.current_task_detail = None
            if self.selected_index >= len(self.filtered_tasks):
                self.selected_index = max(0, len(self.filtered_tasks) - 1)
            self._ensure_selection_visible()
            self.force_render()
        else:
            self.load_current_list(preserve_selection=bool(restore_file), selected_task_file=restore_file, skip_sync=True)
        self.set_status_message(self._t("STATUS_MESSAGE_PLAN_TASKS", title=plan_detail.title, fallback=f"Tasks for plan: {plan_detail.title}"), ttl=3)
        self.force_render()

    def _build_projects_list(self) -> List[Task]:
        projects: List[Task] = []
        root = self.projects_root
        # Normalize legacy GitHub namespaces to avoid duplicated projects in the picker.
        try:
            migrate_legacy_github_namespaces(root)
        except Exception:
            pass
        def _has_tasks_dir(p: Path) -> bool:
            """A project namespace exists only if it contains at least one task file."""
            try:
                for f in p.rglob("TASK-*.task"):
                    if ".snapshots" in f.parts or ".trash" in f.parts:
                        continue
                    return True
            except Exception:
                return False
            return False

        if root.exists():
            paths = sorted([p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")])
            entries: list[tuple[Path, str, str]] = []
            display_counts: dict[str, int] = {}
            for path in paths:
                if not _has_tasks_dir(path):
                    continue
                repo, owner = self._project_display_parts(path.name)
                base = repo or path.name
                key = base.lower()
                display_counts[key] = display_counts.get(key, 0) + 1
                entries.append((path, base, owner))

            for path, base, owner in entries:
                display = base
                key = base.lower()
                if display_counts.get(key, 0) > 1:
                    qualifier = owner or (self._t("PROJECT_QUALIFIER_LOCAL", fallback="local") if path.name == base else path.name)
                    if qualifier:
                        display = f"{base} ({qualifier})"

                repo = FileTaskRepository(path)
                tasks = repo.list("", skip_sync=True)
                total = len(tasks)
                done_count = sum(1 for t in tasks if str(t.status).upper() == "DONE")
                active_count = sum(1 for t in tasks if str(t.status).upper() == "ACTIVE")
                todo_count = total - done_count - active_count
                avg_progress = int(sum(t.calculate_progress() for t in tasks) / total) if total else 0
                if total == 0:
                    status = Status.UNKNOWN
                else:
                    status = Status.TODO if todo_count else (Status.ACTIVE if active_count else Status.DONE)
                projects.append(
                    Task(
                        id=path.name,
                        name=display,
                        status=status,
                        description="",
                        category="project",
                        completed=status == Status.DONE,
                        task_file=str(path),
                        progress=avg_progress,
                        children_count=total,
                        children_completed=done_count,
                        parent=None,
                        detail=None,
                        domain="",
                        phase="",
                        component="",
                        blocked=False,
                    )
                )
        return projects

    @staticmethod
    def _project_display_parts(namespace: str) -> tuple[str, str]:
        """Return (repo_display, owner) for a namespace, without GitHub prefix noise."""
        parsed = _parse_namespace(namespace)
        return parsed.repo, parsed.owner

    def _enter_project(self, project_task: Task) -> None:
        """Switch from project picker to tasks of the selected project."""
        self.last_project_index = self.selected_index
        self.last_project_id = project_task.id
        self.last_project_name = project_task.name
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
        # Clear within-project caches; they are scoped to a single namespace directory.
        self._section_cache.clear()
        self._section_selected_task_file.clear()
        # Reset filters when entering new project - old filters may not exist in this project
        self.domain_filter = ""
        self.phase_filter = ""
        self.component_filter = ""
        self.current_filter = None
        self.project_mode = False
        # Default entry point inside a project is the Plans view (contract → plan → tasks).
        self.project_section = "plans"
        self.plan_filter_id = None
        self.plan_filter_title = None
        self.load_plans(skip_sync=True)
        self.set_status_message(f"Проект: {project_task.name}", ttl=2)

    def return_to_projects(self):
        """Return to project picker view."""
        if not getattr(self, "global_storage_used", True):
            self.set_status_message(self._t("STATUS_MESSAGE_LOCAL_MODE_NO_PROJECTS"), ttl=3)
            return
        self.load_projects()
        target_idx = 0
        if self.tasks:
            if self.last_project_id:
                for idx, proj in enumerate(self.tasks):
                    if proj.id == self.last_project_id:
                        target_idx = idx
                        break
                else:
                    self.last_project_id = None
            if not self.last_project_id and self.last_project_name:
                for idx, proj in enumerate(self.tasks):
                    if proj.name == self.last_project_name:
                        target_idx = idx
                        break
            else:
                target_idx = min(self.last_project_index, len(self.tasks) - 1)
        self.selected_index = target_idx
        self.list_view_offset = 0
        self._ensure_selection_visible()
        self.force_render()

    def return_to_projects_fast(self) -> None:
        """Return to project picker without blocking the UI thread.

        Uses cached snapshot immediately and refreshes counts asynchronously.
        """
        if not getattr(self, "global_storage_used", True):
            self.set_status_message(self._t("STATUS_MESSAGE_LOCAL_MODE_NO_PROJECTS"), ttl=3)
            return
        # Switch view state first (instant).
        self.project_mode = True
        self.detail_mode = False
        self.current_task_detail = None
        self.current_task = None
        self.navigation_stack = []
        self.current_filter = None

        cached = list(getattr(self, "_projects_cache", []) or [])
        self.tasks = cached

        filtered = self.filtered_tasks
        target_idx = 0
        if filtered:
            if self.last_project_id:
                for idx, proj in enumerate(filtered):
                    if proj.id == self.last_project_id:
                        target_idx = idx
                        break
                else:
                    self.last_project_id = None
            if not self.last_project_id and self.last_project_name:
                for idx, proj in enumerate(filtered):
                    if proj.name == self.last_project_name:
                        target_idx = idx
                        break
            else:
                target_idx = min(self.last_project_index, len(filtered) - 1)
        self.selected_index = target_idx
        self.list_view_offset = 0
        self._clamp_selection_to_filtered()
        self.force_render()

        self._schedule_projects_refresh()

    def navigate_back(self) -> None:
        """Context-aware back navigation (one level up)."""
        if getattr(self, "detail_mode", False):
            self.exit_detail_view()
            return
        if getattr(self, "project_mode", False):
            return
        # Inside a project: Tasks → Plans → Projects.
        if getattr(self, "project_section", "tasks") == "tasks":
            self.toggle_project_section()
            return
        self.return_to_projects_fast()

    def _schedule_projects_refresh(self, delay: float = 0.2) -> None:
        """Defer refresh to let the UI render immediately (reduces perceived lag)."""
        try:
            timer = threading.Timer(max(0.0, float(delay)), self._refresh_projects_cache_async)
            timer.daemon = True
            timer.start()
        except Exception:
            self._refresh_projects_cache_async()

    def _refresh_projects_cache_async(self) -> None:
        """Refresh cached project list in the background (keeps UI responsive)."""
        if getattr(self, "_projects_refresh_in_flight", False):
            return
        self._projects_refresh_in_flight = True
        gen = int(getattr(self, "_projects_refresh_generation", 0) or 0) + 1
        self._projects_refresh_generation = gen

        def worker() -> None:
            try:
                projects = self._build_projects_list()
                # Only publish the latest refresh.
                if getattr(self, "_projects_refresh_generation", 0) != gen:
                    return
                fingerprint = tuple(
                    (
                        getattr(p, "name", ""),
                        getattr(getattr(p, "status", None), "value", ("",))[0],
                        int(getattr(p, "progress", 0) or 0),
                        int(getattr(p, "children_count", 0) or 0),
                        int(getattr(p, "children_completed", 0) or 0),
                    )
                    for p in projects
                )
                changed = fingerprint != tuple(getattr(self, "_projects_cache_fingerprint", tuple()) or tuple())
                self._projects_cache = list(projects)
                self._projects_cache_fingerprint = fingerprint
                if not changed:
                    return

                # Update view only if we're still on the project picker.
                if not getattr(self, "project_mode", False) or getattr(self, "detail_mode", False):
                    return

                selected_id = ""
                filtered_now = self.filtered_tasks
                if filtered_now and 0 <= self.selected_index < len(filtered_now):
                    selected_id = getattr(filtered_now[self.selected_index], "id", "") or ""
                self.tasks = list(projects)
                filtered_after = self.filtered_tasks
                if selected_id and filtered_after:
                    for idx, proj in enumerate(filtered_after):
                        if proj.id == selected_id:
                            self.selected_index = idx
                            break
                    else:
                        self.selected_index = 0
                else:
                    self.selected_index = 0
                self.list_view_offset = 0
                self._clamp_selection_to_filtered()
                self.force_render()
            except Exception:
                return
            finally:
                self._projects_refresh_in_flight = False

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _normalize_status_value(status: Union[Status, str, bool, None]) -> Status:
        if isinstance(status, Status):
            return status
        if isinstance(status, bool):
            return Status.DONE if status else Status.TODO
        if isinstance(status, str):
            return Status.from_string(status)
        return Status.UNKNOWN

    @staticmethod
    def _subtask_status(subtask: Step) -> Status:
        return subtask.status_value()

    def _status_indicator(self, status: Union[Status, str, bool, None]) -> Tuple[str, str]:
        status_obj = self._normalize_status_value(status)
        if status_obj == Status.DONE:
            return '●', 'class:icon.check'
        if status_obj == Status.ACTIVE:
            return '●', 'class:icon.warn'
        if status_obj == Status.TODO:
            return '○', 'class:icon.fail'
        return '○', 'class:status.unknown'

    @staticmethod
    def _status_short_label(status: Status) -> str:
        if status == Status.DONE:
            return "[DONE]"
        if status == Status.ACTIVE:
            return "[ACTV]"
        if status == Status.TODO:
            return "[TODO]"
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
            path = self.detail_flat_subtasks[idx].key
            self._last_subtask_click_index = None
            self._last_subtask_click_time = 0.0
            self.show_subtask_details(path)
        else:
            self._last_subtask_click_index = idx
            self._last_subtask_click_time = now

    def _handle_body_mouse(self, mouse_event: MouseEvent):
        return handle_body_mouse(self, mouse_event)

    def _handle_footer_mouse(self, mouse_event: MouseEvent):
        """Track footer hover to enable description scrolling."""
        if mouse_event.event_type == MouseEventType.MOUSE_MOVE:
            try:
                self._footer_desc_hover_last_at = time.time()
                self._footer_desc_hover_y = int(getattr(mouse_event.position, "y", -1))
            except Exception:
                self._footer_desc_hover_last_at = time.time()
                self._footer_desc_hover_y = None
            # Keep rendering responsive while hovering.
            self.force_render()
            return None
        return NotImplemented

    def move_vertical_selection(self, delta: int) -> None:
        if self.checkpoint_mode:
            self.move_checkpoint_selection(delta)
            return
        if getattr(self, "confirm_mode", False):
            return
        if getattr(self, "list_editor_mode", False) and not getattr(self, "editing_mode", False):
            self.move_list_editor_selection(delta)
            return
        if self.detail_mode and self.current_task_detail and getattr(self, "detail_tab", "overview") != "overview":
            tab = getattr(self, "detail_tab", "overview")
            self.detail_tab_scroll_offsets[tab] = max(0, int(self.detail_tab_scroll_offsets.get(tab, 0)) + delta)
            self.force_render()
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
        text = (text or "").expandtabs(4)
        width = 0
        for ch in text:
            w = wcwidth(ch)
            if w is None:
                w = 0
            width += max(0, w)
        return width

    def _trim_display(self, text: str, width: int) -> str:
        """Обрезает текст так, чтобы видимая ширина не превышала width."""
        text = (text or "").expandtabs(4)
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
        # Respect explicit newlines (otherwise they break table borders in the renderer).
        text = (text or "").replace("\r\n", "\n").replace("\r", "\n").expandtabs(4)
        current = ""
        used = 0
        for ch in text:
            if ch == "\n":
                lines.append(self._pad_display(current, width))
                current = ""
                used = 0
                continue
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
        if current or not lines:
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
        key = (
            id(self.tasks),
            len(self.tasks),
            self.current_filter,
            str(getattr(self, "search_query", "") or ""),
            bool(getattr(self, "project_mode", False)),
        )
        if key == getattr(self, "_filtered_tasks_cache_key", None):
            return self._filtered_tasks_cache

        items = self.tasks
        if self.current_filter:
            items = [t for t in items if t.status == self.current_filter]
        query = (getattr(self, "search_query", "") or "").strip().lower()
        if not query:
            filtered = items
        else:
            tokens = [t for t in query.split() if t]
            if not tokens:
                filtered = items
            else:
                def _match_task(task: Task) -> bool:
                    if getattr(self, "project_mode", False):
                        hay = (task.name or "").lower()
                    else:
                        det = getattr(task, "detail", None)
                        tags = []
                        if det and getattr(det, "tags", None):
                            tags = [str(x) for x in (det.tags or [])]
                        hay = " ".join(
                            [
                                str(getattr(task, "id", "") or ""),
                                str(getattr(task, "name", "") or ""),
                                str(getattr(task, "domain", "") or ""),
                                " ".join(tags),
                            ]
                        ).lower()
                    return all(tok in hay for tok in tokens)

                filtered = [t for t in items if _match_task(t)]

        max_prog = 0
        max_children = 0
        for task in filtered:
            max_prog = max(max_prog, len(f"{int(getattr(task, 'progress', 0) or 0)}%"))
            done = int(getattr(task, "children_completed", 0) or 0)
            total = int(getattr(task, "children_count", 0) or 0)
            max_children = max(max_children, len(f"{done}/{total}"))
        self._filtered_tasks_metrics = {
            "max_progress_len": max(3, max_prog or 3),
            "max_children_len": max(3, max_children or 3),
        }
        self._filtered_tasks_cache_key = key
        self._filtered_tasks_cache = list(filtered) if filtered is not items else items
        return self._filtered_tasks_cache

    def _clamp_selection_to_filtered(self) -> None:
        total = len(self.filtered_tasks)
        if total <= 0:
            self.selected_index = 0
            self.list_view_offset = 0
            return
        if self.selected_index >= total:
            self.selected_index = total - 1
        if self.selected_index < 0:
            self.selected_index = 0
        self._ensure_selection_visible()

    def compute_signature(self) -> int:
        if getattr(self, "project_mode", False):
            return 0
        return self.manager.compute_signature()

    def maybe_reload(self):
        if getattr(self, "project_mode", False) and not self.detail_mode:
            # In project mode, check if projects changed and reload
            self._maybe_reload_projects()
            return
        _maybe_reload_helper(self)

    def _compute_projects_signature(self) -> int:
        """Compute signature for all projects in ~/.tasks/"""
        sig = 0
        if self.projects_root.exists():
            for path in self.projects_root.iterdir():
                if path.is_dir() and not path.name.startswith("."):
                    for f in path.rglob("TASK-*.task"):
                        if ".snapshots" in f.parts:
                            continue
                        try:
                            sig ^= int(f.stat().st_mtime_ns)
                        except OSError:
                            continue
        return sig if sig else int(time.time_ns())

    def _maybe_reload_projects(self) -> None:
        """Refresh projects list periodically (non-blocking)."""
        from time import time

        ts = time()
        if ts - self._last_check < 0.7:  # throttle project refresh; keep UI responsive
            return
        self._last_check = ts
        self._refresh_projects_cache_async()

    def load_tasks(
        self,
        preserve_selection: bool = False,
        selected_task_file: Optional[str] = None,
        skip_sync: bool = False,
        *,
        plan_parent: Optional[str] = None,
    ):
        with self._spinner(self._t("SPINNER_REFRESH_TASKS")):
            if plan_parent:
                details = self.manager.list_tasks("", skip_sync=skip_sync)
            else:
                domain_path = derive_domain_explicit(self.domain_filter, self.phase_filter, self.component_filter)
                details = self.manager.list_tasks(domain_path, skip_sync=skip_sync)

        snapshot = _projects_status_payload()
        wait = snapshot.get("rate_wait") or 0
        remaining = snapshot.get("rate_remaining")
        if wait > 0 and wait != self._last_rate_wait:
            message = self._t("STATUS_MESSAGE_RATE_LIMIT", remaining=remaining if remaining is not None else "?", seconds=int(wait))
            self.set_status_message(message, ttl=5)
            self._last_rate_wait = wait

        # Task view excludes plan tasks by default.
        if plan_parent:
            details = [d for d in details if str(getattr(d, "parent", "") or "") == str(plan_parent) and not self._is_plan_detail(d)]
        else:
            details = apply_context_filters(details, self.phase_filter, self.component_filter)
            # Unfiltered Tasks view is intentionally "below plans": hide plans and show only tasks
            # that belong to a plan (parent is set).
            details = [
                d
                for d in details
                if not self._is_plan_detail(d)
                and (str(getattr(d, "parent", "") or "").strip())
            ]

        def _task_factory(det, derived_status, calc_progress, children_completed, children_total):
            task_file = f".tasks/{det.domain + '/' if det.domain else ''}{det.id}.task"
            return Task(
                id=det.id,
                name=det.title,
                status=derived_status,
                description=(det.description or det.context or "")[:80],
                category=det.domain or det.priority,
                completed=derived_status == Status.DONE,
                task_file=task_file,
                progress=calc_progress,
                children_count=children_total,
                children_completed=children_completed,
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
        cache_key = f"tasks:{plan_parent}" if plan_parent else "tasks"
        self._section_cache[cache_key] = list(self.tasks)

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

        section = getattr(self, "project_section", "tasks") or "tasks"
        plan_parent = getattr(self, "plan_filter_id", None) if section == "tasks" else None
        plan_counts = None
        if section == "plans":
            details = self.manager.list_tasks("", skip_sync=skip_sync)
            plan_counts = self._plan_task_counts(details)
            seen: Set[str] = set()
            plans: List["TaskDetail"] = []
            for det in details:
                if not self._is_plan_detail(det):
                    continue
                det_id = str(getattr(det, "id", "") or "")
                if det_id and det_id in seen:
                    continue
                plans.append(det)
                if det_id:
                    seen.add(det_id)
            details = plans
        else:
            if plan_parent:
                details = self.manager.list_tasks("", skip_sync=skip_sync)
                details = [d for d in details if str(getattr(d, "parent", "") or "") == str(plan_parent) and not self._is_plan_detail(d)]
            else:
                domain_path = derive_domain_explicit(self.domain_filter, self.phase_filter, self.component_filter)
                details = self.manager.list_tasks(domain_path, skip_sync=skip_sync)
                details = apply_context_filters(details, self.phase_filter, self.component_filter)
                details = [
                    d
                    for d in details
                    if not self._is_plan_detail(d)
                    and (str(getattr(d, "parent", "") or "").strip())
                ]

        def _task_factory(det, derived_status, calc_progress, children_completed, children_total):
            task_file = f".tasks/{det.domain + '/' if det.domain else ''}{det.id}.task"
            if section == "plans" and plan_counts is not None:
                total, done = plan_counts.get(str(getattr(det, "id", "") or ""), (0, 0))
            else:
                total, done = children_total, children_completed
            return Task(
                id=det.id,
                name=det.title,
                status=derived_status,
                description=(det.description or det.context or "")[:80],
                category=det.domain or det.priority,
                completed=derived_status == Status.DONE,
                task_file=task_file,
                progress=calc_progress,
                children_count=total,
                children_completed=done,
                parent=det.parent,
                detail=det,
                domain=det.domain,
                phase=det.phase,
                component=det.component,
                blocked=det.blocked,
            )

        self.tasks = build_task_models(details, _task_factory)
        # Keep section cache in sync so back navigation remains instant.
        cache_key = self._section_key() if section == "plans" else (f"tasks:{plan_parent}" if plan_parent else "tasks")
        self._section_cache[cache_key] = list(self.tasks)

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
        if detail and detail.updated and detail.status == "DONE":
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
        end = self._parse_task_datetime(detail.updated) if detail.status == "DONE" else None
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
        if getattr(self.current_task_detail, "kind", "task") == "plan" and getattr(self, "detail_tab", "overview") == "overview":
            return len(self._plan_detail_tasks())
        return len(self.detail_flat_subtasks)

    @staticmethod
    def _plan_task_sort_key(detail: TaskDetail) -> tuple[int, int, str]:
        blocked = bool(getattr(detail, "blocked", False))
        try:
            prog = int(getattr(detail, "calculate_progress")() or 0)
        except Exception:
            prog = int(getattr(detail, "progress", 0) or 0)
        status = str(getattr(detail, "status", "") or "").strip().upper()
        if prog == 100 and not blocked:
            status = "DONE"
        order = {"ACTIVE": 0, "TODO": 1, "DONE": 2}.get(status, 99)
        return order, prog, str(getattr(detail, "id", "") or "")

    def _plan_detail_tasks(self) -> List[TaskDetail]:
        detail = self.current_task_detail
        if not detail or getattr(detail, "kind", "task") != "plan":
            self.detail_plan_tasks = []
            self._detail_plan_tasks_cache_key = None
            self._detail_plan_tasks_dirty = True
            return []
        embedded = getattr(detail, "_embedded_plan_tasks", None)
        if embedded is not None:
            embedded_key = (
                "embedded",
                str(getattr(detail, "id", "") or ""),
                str(getattr(detail, "_nested_path_prefix", "") or ""),
                id(embedded),
            )
            if not self._detail_plan_tasks_dirty and self._detail_plan_tasks_cache_key == embedded_key:
                plan_tasks = self.detail_plan_tasks or []
                if self.detail_selected_task_id:
                    for idx, task in enumerate(plan_tasks):
                        if str(getattr(task, "id", "") or "") == str(self.detail_selected_task_id):
                            self.detail_selected_index = idx
                            break
                if plan_tasks:
                    self.detail_selected_index = max(0, min(self.detail_selected_index, len(plan_tasks) - 1))
                    self.detail_selected_task_id = str(getattr(plan_tasks[self.detail_selected_index], "id", "") or "")
                else:
                    self.detail_selected_index = 0
                    self.detail_selected_task_id = None
                return plan_tasks

            plan_tasks: List[TaskDetail] = []
            for idx, node in enumerate(list(embedded or [])):
                task_path = f"t:{idx}"
                node_id = str(getattr(node, "id", "") or "").strip() or task_path
                view = SimpleNamespace(
                    id=node_id,
                    title=str(getattr(node, "title", "") or ""),
                    status=str(getattr(node, "status", "") or "TODO"),
                    priority=str(getattr(node, "priority", "") or "MEDIUM"),
                    description=str(getattr(node, "description", "") or ""),
                    context=str(getattr(node, "context", "") or ""),
                    success_criteria=list(getattr(node, "success_criteria", []) or []),
                    tests=list(getattr(node, "tests", []) or []),
                    criteria_confirmed=bool(getattr(node, "criteria_confirmed", False)),
                    tests_confirmed=bool(getattr(node, "tests_confirmed", False)),
                    criteria_auto_confirmed=bool(getattr(node, "criteria_auto_confirmed", False)),
                    tests_auto_confirmed=bool(getattr(node, "tests_auto_confirmed", False)),
                    criteria_notes=list(getattr(node, "criteria_notes", []) or []),
                    tests_notes=list(getattr(node, "tests_notes", []) or []),
                    steps=list(getattr(node, "steps", []) or []),
                    blocked=bool(getattr(node, "blocked", False)),
                    blockers=list(getattr(node, "blockers", []) or []),
                    calculate_progress=getattr(node, "calculate_progress", lambda: 0),
                    _embedded_task_node=node,
                    _embedded_task_path=task_path,
                )
                plan_tasks.append(view)
            plan_tasks.sort(key=self._plan_task_sort_key)
            self.detail_plan_tasks = plan_tasks
            self._detail_plan_tasks_cache_key = embedded_key
            self._detail_plan_tasks_dirty = False
            if self.detail_selected_task_id:
                for idx, task in enumerate(plan_tasks):
                    if str(getattr(task, "id", "") or "") == str(self.detail_selected_task_id):
                        self.detail_selected_index = idx
                        break
            if plan_tasks:
                self.detail_selected_index = max(0, min(self.detail_selected_index, len(plan_tasks) - 1))
                self.detail_selected_task_id = str(getattr(plan_tasks[self.detail_selected_index], "id", "") or "")
            else:
                self.detail_selected_index = 0
                self.detail_selected_task_id = None
            return plan_tasks

        plan_id = str(getattr(detail, "id", "") or "")
        plan_domain = str(getattr(detail, "domain", "") or "")
        cache_key = (plan_id, plan_domain)

        if not self._detail_plan_tasks_dirty and self._detail_plan_tasks_cache_key == cache_key:
            plan_tasks = self.detail_plan_tasks or []
            # Keep selection stable and within bounds.
            if self.detail_selected_task_id:
                for idx, task in enumerate(plan_tasks):
                    if str(getattr(task, "id", "") or "") == str(self.detail_selected_task_id):
                        self.detail_selected_index = idx
                        break
            if plan_tasks:
                self.detail_selected_index = max(0, min(self.detail_selected_index, len(plan_tasks) - 1))
                self.detail_selected_task_id = str(getattr(plan_tasks[self.detail_selected_index], "id", "") or "")
            else:
                self.detail_selected_index = 0
                self.detail_selected_task_id = None
            return plan_tasks

        try:
            # NOTE: list_tasks() is expensive (rglob + parse). Cache at the plan-detail level.
            all_details = self.manager.list_tasks("", skip_sync=True)
        except Exception:
            all_details = []
        plan_tasks = [
            d
            for d in (all_details or [])
            if str(getattr(d, "parent", "") or "") == plan_id
            and getattr(d, "kind", "task") != "plan"
        ]
        plan_tasks.sort(key=self._plan_task_sort_key)
        self.detail_plan_tasks = plan_tasks
        if self.detail_selected_task_id:
            for idx, task in enumerate(plan_tasks):
                if str(getattr(task, "id", "") or "") == str(self.detail_selected_task_id):
                    self.detail_selected_index = idx
                    break
        if plan_tasks:
            self.detail_selected_index = max(0, min(self.detail_selected_index, len(plan_tasks) - 1))
            self.detail_selected_task_id = str(getattr(plan_tasks[self.detail_selected_index], "id", "") or "")
        else:
            self.detail_selected_index = 0
            self.detail_selected_task_id = None
        self._detail_plan_tasks_cache_key = cache_key
        self._detail_plan_tasks_dirty = False
        return plan_tasks

    def _invalidate_plan_detail_tasks_cache(self) -> None:
        """Invalidate cached plan tasks list (next render will rebuild it)."""
        self._detail_plan_tasks_dirty = True
        self._detail_plan_tasks_cache_key = None
        self._detail_plan_rows_cache = []
        self._detail_plan_rows_cache_key = None
        self._detail_plan_summary_cache = None

    def _cached_step_tree_counts(self, task: TaskDetail) -> Tuple[int, int]:
        """Return (total_steps, done_steps) for a task, cached by on-disk fingerprint."""
        embedded_node = getattr(task, "_embedded_task_node", None)
        if embedded_node is not None:
            task_id = f"embedded:{id(embedded_node)}"
            task_domain = ""
        else:
            task_id = str(getattr(task, "id", "") or "")
            task_domain = str(getattr(task, "domain", "") or "")
        cache_key = (task_id, task_domain)
        fingerprint = (
            float(getattr(task, "_source_mtime", 0.0) or 0.0),
            str(getattr(task, "updated", "") or ""),
            int(getattr(task, "progress", 0) or 0),
            bool(getattr(task, "blocked", False)),
            str(getattr(task, "status", "") or ""),
            int(len(list(getattr(task, "steps", []) or []))),
        )
        cached = self._detail_task_step_counts_cache.get(cache_key)
        if cached and cached[0] == fingerprint:
            return int(cached[1]), int(cached[2])

        total = 0
        done = 0
        stack = [iter(list(getattr(task, "steps", []) or []))]
        while stack:
            try:
                node = next(stack[-1])
            except StopIteration:
                stack.pop()
                continue
            total += 1
            if bool(getattr(node, "completed", False)):
                done += 1
            plan = getattr(node, "plan", None)
            tasks = list(getattr(plan, "tasks", []) or []) if plan else []
            for tnode in reversed(tasks):
                child_steps = list(getattr(tnode, "steps", []) or [])
                if child_steps:
                    stack.append(iter(child_steps))

        self._detail_task_step_counts_cache[cache_key] = (fingerprint, total, done)
        return total, done

    def _plan_task_counts(self, details: List[TaskDetail]) -> dict[str, tuple[int, int]]:
        counts: dict[str, tuple[int, int]] = {}
        for det in details:
            parent = str(getattr(det, "parent", "") or "").strip()
            if not parent:
                continue
            if self._is_plan_detail(det):
                continue
            blocked = bool(getattr(det, "blocked", False))
            try:
                prog = int(getattr(det, "calculate_progress")() or 0)
            except Exception:
                prog = int(getattr(det, "progress", 0) or 0)
            status_raw = str(getattr(det, "status", "") or "").strip().upper()
            if prog == 100 and not blocked:
                status_raw = "DONE"
            total, done = counts.get(parent, (0, 0))
            total += 1
            if status_raw == "DONE":
                done += 1
            counts[parent] = (total, done)
        return counts

    def _selected_plan_task_detail(self) -> Optional[TaskDetail]:
        tasks = self._plan_detail_tasks()
        if not tasks:
            return None
        idx = max(0, min(self.detail_selected_index, len(tasks) - 1))
        self.detail_selected_index = idx
        self.detail_selected_task_id = str(getattr(tasks[idx], "id", "") or "")
        return tasks[idx]

    def _open_selected_plan_task_detail(self) -> None:
        selected = self._selected_plan_task_detail()
        if not selected:
            return
        embedded_node = getattr(selected, "_embedded_task_node", None)
        embedded_path = getattr(selected, "_embedded_task_path", None)
        if embedded_node is not None and embedded_path:
            # Save current context to navigation stack (plan view).
            self.navigation_stack.append({
                "task": self.current_task,
                "detail": self.current_task_detail,
                "selected_index": self.detail_selected_index,
                "selected_key": getattr(self, "detail_selected_path", "") or "",
                "entered_path": embedded_path,
                "selected_task_id": self.detail_selected_task_id,
                "detail_tab": getattr(self, "detail_tab", "overview"),
                "detail_tab_scroll_offsets": dict(getattr(self, "detail_tab_scroll_offsets", {}) or {}),
            })
            root_task_id, root_domain, _ = self._get_root_task_context()
            new_detail = self._task_node_to_task_detail(embedded_node, root_task_id, embedded_path)
            new_detail.domain = root_domain
            self.current_task_detail = new_detail
            self.detail_mode = True
            self.detail_selected_index = 0
            self.detail_selected_path = ""
            self.detail_selected_task_id = None
            self.detail_tab = "radar"
            self.detail_tab_scroll_offsets = {"radar": 0, "notes": 0, "plan": 0, "contract": 0, "meta": 0}
            self.detail_flat_dirty = True
            self._rebuild_detail_flat()
            self.detail_view_offset = 0
            self._set_footer_height(0)
            return
        task = Task(
            name=str(getattr(selected, "title", "") or ""),
            status=Status.from_string(str(getattr(selected, "status", "") or "")),
            description=str(getattr(selected, "description", "") or ""),
            category="task",
            id=str(getattr(selected, "id", "") or ""),
            parent=str(getattr(selected, "parent", "") or ""),
            detail=selected,
            domain=str(getattr(selected, "domain", "") or ""),
            phase=str(getattr(selected, "phase", "") or ""),
            component=str(getattr(selected, "component", "") or ""),
            blocked=bool(getattr(selected, "blocked", False)),
        )
        self.show_task_details(task)

    def show_task_details(self, task: Task):
        if self.project_mode:
            self._enter_project(task)
            return
        self.current_task = task
        self.current_task_detail = task.detail or TaskFileParser.parse(Path(task.task_file))
        self._detail_source_section = getattr(self, "project_section", "tasks") if not getattr(self, "project_mode", False) else "projects"
        self.detail_mode = True
        self.detail_selected_index = 0
        self.detail_plan_tasks = []
        self.detail_selected_task_id = None
        self._invalidate_plan_detail_tasks_cache()
        self.detail_tab = "radar"
        self.detail_tab_scroll_offsets = {"radar": 0, "notes": 0, "plan": 0, "contract": 0, "meta": 0}
        self.detail_collapsed = set(self.collapsed_by_task.get(self.current_task_detail.id, set()))
        self.detail_flat_dirty = True
        self._rebuild_detail_flat()
        self.detail_view_offset = 0
        self._set_footer_height(0)

    def show_subtask_details(self, path: str):
        """Enter into subtask as if it were a task (infinite nesting)."""
        if not self.current_task_detail:
            return
        key = str(path or "")
        kind = _detail_node_kind(key)
        entered_path = _detail_canonical_path(key, kind)
        if not entered_path:
            return

        # Save current context to navigation stack
        self.navigation_stack.append({
            "task": self.current_task,
            "detail": self.current_task_detail,
            "selected_index": self.detail_selected_index,
            "selected_key": self.detail_selected_path,
            "entered_path": entered_path,
            "detail_tab": getattr(self, "detail_tab", "overview"),
            "detail_tab_scroll_offsets": dict(getattr(self, "detail_tab_scroll_offsets", {}) or {}),
        })

        parent_id = self.current_task_detail.id
        if kind == "task":
            task_node, _, _ = _find_task_by_path(self.current_task_detail.steps, entered_path)
            if not task_node:
                # Restore stack on no-op to avoid corrupting nesting.
                self.navigation_stack.pop()
                return
            new_detail = self._task_node_to_task_detail(task_node, parent_id, entered_path)
        else:
            step = self._get_step_by_path(entered_path)
            if not step:
                self.navigation_stack.pop()
                return
            new_detail = self._step_to_task_detail(step, parent_id, entered_path)

        # Set as current task detail
        self.current_task_detail = new_detail
        self.detail_mode = True
        self.detail_selected_index = 0
        self.detail_selected_path = ""
        self.detail_selected_task_id = None
        self.detail_tab = "radar"
        self.detail_tab_scroll_offsets = {"radar": 0, "notes": 0, "plan": 0, "contract": 0, "meta": 0}
        if getattr(new_detail, "kind", "task") == "plan":
            self.detail_plan_tasks = []
            self._invalidate_plan_detail_tasks_cache()
        self.detail_flat_dirty = True
        self._rebuild_detail_flat()
        self.detail_view_offset = 0
        self._set_footer_height(0)

    def delete_current_item(self):
        """Удалить текущий выбранный элемент (задачу или подзадачу)"""
        delete_current_item(self)

    def confirm_delete_current_item(self) -> None:
        """Ask for confirmation before deleting the currently selected entity."""
        if getattr(self, "confirm_mode", False):
            return
        if not getattr(self, "filtered_tasks", None) and not getattr(self, "detail_mode", False):
            return

        title = self._t("CONFIRM_TITLE_DELETE")
        lines: List[str] = []
        if getattr(self, "project_mode", False) and not getattr(self, "detail_mode", False):
            if not self.filtered_tasks:
                return
            project = self.filtered_tasks[self.selected_index]
            lines = [
                self._t("CONFIRM_DELETE_PROJECT", name=getattr(project, "name", "")),
                self._t("CONFIRM_IRREVERSIBLE"),
            ]
        elif getattr(self, "detail_mode", False) and getattr(self, "current_task_detail", None):
            entry = self._selected_subtask_entry()
            if not entry:
                return
            path = entry.key
            st = entry.node
            lines = [
                self._t(
                    "CONFIRM_DELETE_SUBTASK",
                    path=self._display_subtask_path(_detail_canonical_path(path, entry.kind)),
                    title=getattr(st, "title", "") if st is not None else "",
                ),
                self._t("CONFIRM_IRREVERSIBLE"),
            ]
        else:
            if not self.filtered_tasks:
                return
            task = self.filtered_tasks[self.selected_index]
            lines = [
                self._t("CONFIRM_DELETE_TASK", task_id=getattr(task, "id", ""), title=getattr(task, "name", "")),
                self._t("CONFIRM_IRREVERSIBLE"),
            ]

        self._open_confirm_dialog(title=title, lines=lines, on_yes=self.delete_current_item)

    def _open_confirm_dialog(self, *, title: str, lines: List[str], on_yes) -> None:
        self.confirm_mode = True
        self.confirm_title = title
        self.confirm_lines = list(lines)
        self._confirm_on_yes = on_yes
        self._confirm_on_no = None
        self._set_footer_height(0)
        self.force_render()

    def _close_confirm_dialog(self) -> None:
        self.confirm_mode = False
        self.confirm_title = ""
        self.confirm_lines = []
        self._confirm_on_yes = None
        self._confirm_on_no = None
        # Restore footer height depending on current view
        self._set_footer_height(self._footer_height_default_for_mode())

    def _confirm_accept(self) -> None:
        handler = getattr(self, "_confirm_on_yes", None)
        self._close_confirm_dialog()
        if callable(handler):
            handler()

    def _confirm_cancel(self) -> None:
        handler = getattr(self, "_confirm_on_no", None)
        self._close_confirm_dialog()
        if callable(handler):
            handler()

    def toggle_subtask_completion(self):
        """Toggle selected subtask completion (no force-by-default)."""
        if not (self.detail_mode and self.current_task_detail):
            return
        entry = self._selected_subtask_entry()
        if not entry:
            return
        if entry.kind != "step" or not isinstance(entry.node, Step):
            return
        path = entry.key
        st = entry.node
        desired = not st.completed
        self._apply_subtask_completion(path=path, desired=desired, force=False, subtask_hint=st)

    def _apply_subtask_completion(self, *, path: str, desired: bool, force: bool, subtask_hint: Optional[Step] = None) -> None:
        """Set completion for a subtask path, respecting nested navigation stack."""
        if not self.current_task_detail:
            return

        # Get root task context for nested navigation
        root_task_id, root_domain, path_prefix = self._get_root_task_context()
        full_path = f"{path_prefix}.{path}" if path_prefix else path

        ok, msg = self.manager.set_step_completed(root_task_id, 0, desired, root_domain, path=full_path, force=force)
        if not ok:
            self.set_status_message(msg or self._t("STATUS_MESSAGE_CHECKPOINTS_REQUIRED"))
            if desired and not force:
                st = subtask_hint or self._get_step_by_path(path)
                if st:
                    self.enter_checkpoint_mode()
                    self.checkpoint_selected_index = self._first_unmet_checkpoint_index(st)
                    self.force_render()
            return

        # Reload root task and update current view
        updated_root = self.manager.load_task(root_task_id, root_domain, skip_sync=True)
        if updated_root:
            self.task_details_cache[root_task_id] = updated_root

            if not self.navigation_stack:
                self.current_task_detail = updated_root
            else:
                derived = self._derive_nested_detail(updated_root, root_task_id, path_prefix)
                if derived:
                    derived.domain = root_domain
                    self.current_task_detail = derived

            self._rebuild_detail_flat(path)

        self._update_tasks_list_silent(skip_sync=True)
        self.force_render()

    @staticmethod
    def _first_unmet_checkpoint_index(subtask: Step) -> int:
        if not getattr(subtask, "criteria_confirmed", False):
            return 0
        if not (getattr(subtask, "tests_confirmed", False) or getattr(subtask, "tests_auto_confirmed", False)):
            return 1
        return 0

    def confirm_force_complete_current(self) -> None:
        """Explicit force-complete with confirmation (task or subtask)."""
        if getattr(self, "confirm_mode", False):
            return
        if getattr(self, "project_mode", False) and not getattr(self, "detail_mode", False):
            return

        title = self._t("CONFIRM_TITLE_FORCE")

        if getattr(self, "detail_mode", False) and getattr(self, "current_task_detail", None):
            entry = self._selected_subtask_entry()
            if not entry:
                return
            if entry.kind != "step" or not isinstance(entry.node, Step):
                return
            path = entry.key
            st = entry.node

            def _do():
                self._apply_subtask_completion(path=path, desired=True, force=True, subtask_hint=st)

            lines = [
                self._t("CONFIRM_FORCE_SUBTASK", path=self._display_subtask_path(path), title=getattr(st, "title", "")),
                self._t("CONFIRM_IRREVERSIBLE"),
            ]
            self._open_confirm_dialog(title=title, lines=lines, on_yes=_do)
            return

        if not self.filtered_tasks:
            return
        task = self.filtered_tasks[self.selected_index]
        domain = getattr(task, "domain", "")

        def _do():
            ok, error = self.manager.update_task_status(task.id, "DONE", domain, force=True)
            if not ok:
                msg = error.get("message", self._t("ERR_UPDATE_FAILED")) if error else self._t("ERR_UPDATE_FAILED")
                self.set_status_message(msg)
                return
            self.load_current_list(preserve_selection=True, skip_sync=True)
            self.set_status_message(self._t("MSG_STATUS_UPDATED", task_id=task.id))
            self.force_render()

        lines = [
            self._t("CONFIRM_FORCE_TASK", task_id=getattr(task, "id", ""), title=getattr(task, "name", "")),
            self._t("CONFIRM_IRREVERSIBLE"),
        ]
        self._open_confirm_dialog(title=title, lines=lines, on_yes=_do)

    def toggle_task_completion(self):
        """Advance task status with safe defaults (no force-by-default)."""
        if not self.filtered_tasks or self.selected_index >= len(self.filtered_tasks):
            return
        if self.project_mode:
            self._enter_project(self.filtered_tasks[self.selected_index])
            return
        task = self.filtered_tasks[self.selected_index]
        domain = getattr(task, "domain", "")
        if task.status == Status.DONE:
            new_status = "ACTIVE"
        elif task.status == Status.ACTIVE:
            new_status = "DONE"
        else:
            new_status = "ACTIVE"

        ok, error = self.manager.update_task_status(task.id, new_status, domain, force=False)
        if not ok:
            msg = error.get("message", self._t("ERR_UPDATE_FAILED")) if error else self._t("ERR_UPDATE_FAILED")
            self.set_status_message(msg)
            if new_status == "DONE":
                # Guide user toward the missing work/checkpoints.
                try:
                    self.show_task_details(task)
                except Exception:
                    pass
            return
        # skip_sync=True чтобы pull_task_fields не перезаписал локальные изменения
        self.load_current_list(preserve_selection=True, skip_sync=True)
        self.set_status_message(self._t("MSG_STATUS_UPDATED", task_id=task.id))
        self.force_render()

    def start_editing(self, context: str, current_value: str, index: Optional[int] = None):
        """Начать редактирование текста"""
        self.editing_mode = True
        self.edit_context = context
        self.edit_index = index
        self._editing_multiline = context in {"task_description", "task_context", "task_contract", "task_plan_doc"}
        self.edit_buffer.text = current_value
        self.edit_buffer.cursor_position = len(current_value)
        if hasattr(self, "app") and self.app:
            self.app.layout.focus(self.edit_field)

    def save_edit(self):
        """Сохранить результат редактирования"""
        if not self.editing_mode:
            return

        context = self.edit_context
        raw_value = self.edit_buffer.text
        if getattr(self, "_editing_multiline", False):
            new_value = raw_value.rstrip()
        else:
            new_value = raw_value.replace("\r", "").replace("\n", " ").strip()

        if handle_token(self, new_value):
            return
        if handle_project_number(self, new_value):
            return
        if handle_project_workers(self, new_value):
            return
        if handle_bootstrap_remote(self, new_value):
            return
        if handle_create_plan(self, new_value):
            return
        if handle_create_task(self, new_value):
            return
        if context == "command_palette":
            # Cancel first, then dispatch (dispatch may open another editor).
            self.cancel_edit()
            self._run_command_palette(new_value)
            return

        allow_empty = context in {"task_description", "task_context", "task_contract", "task_plan_doc"}
        if not new_value and not allow_empty:
            self.cancel_edit()
            return

        if self._apply_list_editor_edit(context or "", new_value, self.edit_index):
            self.cancel_edit()
            return

        if handle_task_edit(self, context or "", new_value, self.edit_index):
            return

        self.cancel_edit()

    def _run_command_palette(self, raw: str) -> None:
        """Execute a command palette entry against the current task selection."""
        command = str(raw or "").strip()
        if not command:
            return

        try:
            parts = shlex.split(command)
        except ValueError as exc:
            self.set_status_message(self._t("CMD_PALETTE_PARSE_ERROR", error=str(exc)), ttl=4)
            return
        if not parts:
            return

        verb = str(parts[0] or "").strip().lower()
        args = [str(a) for a in parts[1:]]

        if verb in {"help", "?"}:
            if not getattr(self, "help_visible", False):
                self._footer_height_after_help = int(getattr(self, "footer_height", 0) or 0)
            self.help_visible = True
            self._set_footer_height(12)
            self.force_render()
            return

        if verb in {"handoff"}:
            self.export_handoff()
            return

        if verb in {"desc", "description"}:
            if args:
                self._apply_command_patch({"description": " ".join(args)})
            else:
                self._open_task_text_editor("task_description")
            return

        if verb in {"ctx", "context"}:
            if args:
                self._apply_command_patch({"context": " ".join(args)})
            else:
                self._open_task_text_editor("task_context")
            return

        if verb in {"plan"}:
            if args and str(args[0] or "").strip().lower() in {"sanitize", "split"}:
                self._sanitize_current_task_plan()
            else:
                self._open_task_text_editor("task_plan_doc")
            return

        if verb in {"contract"}:
            self._open_task_text_editor("task_contract")
            return

        if verb in {"tag", "tags"}:
            self._apply_command_tags(args)
            return

        if verb in {"prio", "priority"}:
            self._apply_command_priority(args)
            return

        if verb in {"dep", "deps", "depends"}:
            self._apply_command_deps(args)
            return

        if verb in {"domain", "move"}:
            if not args:
                self.set_status_message(self._t("CMD_PALETTE_USAGE_DOMAIN"), ttl=4)
                return
            self._apply_command_patch({"new_domain": args[0]})
            return

        if verb in {"phase"}:
            if not args:
                self.set_status_message(self._t("CMD_PALETTE_USAGE_PHASE"), ttl=4)
                return
            self._apply_command_patch({"phase": args[0]})
            return

        if verb in {"component"}:
            if not args:
                self.set_status_message(self._t("CMD_PALETTE_USAGE_COMPONENT"), ttl=4)
                return
            self._apply_command_patch({"component": args[0]})
            return

        self.set_status_message(self._t("CMD_PALETTE_UNKNOWN", verb=verb), ttl=4)

    def _command_palette_target(self) -> Optional[Tuple[str, str, TaskDetail]]:
        """Resolve current task target for command palette operations."""
        if getattr(self, "project_mode", False):
            return None
        # Prefer current detail view (root task context).
        if getattr(self, "detail_mode", False) and getattr(self, "current_task_detail", None):
            root_task_id, root_domain, _ = self._get_root_task_context()
            root_detail = self.task_details_cache.get(root_task_id)
            if not root_detail:
                root_detail = self.manager.load_task(root_task_id, root_domain, skip_sync=True)
            if not root_detail:
                root_detail = self.current_task_detail
            return root_task_id, (getattr(root_detail, "domain", "") or root_domain or ""), root_detail

        # Otherwise use current selection in lists (plans/tasks inside a project).
        if not getattr(self, "filtered_tasks", None):
            return None
        task = self.filtered_tasks[self.selected_index]
        domain = getattr(task, "domain", "") or ""
        detail = task.detail or self.manager.load_task(getattr(task, "id", ""), domain, skip_sync=True) or self._get_task_detail(task)
        if not detail:
            return None
        return detail.id, (detail.domain or domain), detail

    def export_handoff(self) -> None:
        """Export handoff snapshot for the current task/plan (copy + file)."""
        target = self._command_palette_target()
        if not target:
            self.set_status_message(self._t("CMD_PALETTE_NO_TASK"), ttl=4)
            return
        task_id, domain, detail = target
        payload = {"intent": "handoff", "task": task_id}
        if str(getattr(detail, "kind", "task") or "task") == "plan":
            payload = {"intent": "handoff", "plan": task_id}
        resp = handle_handoff(self.manager, payload)
        if not resp.success:
            msg = resp.error_message or self._t("STATUS_MESSAGE_HANDOFF_FAILED", error=resp.error_code or "failed")
            self.set_status_message(msg, ttl=6)
            return
        snapshot = resp.result or {}
        text = json.dumps(snapshot, ensure_ascii=False, indent=2)

        export_root = Path(getattr(self, "tasks_dir", Path("."))) / ".handoff"
        try:
            export_root.mkdir(parents=True, exist_ok=True)
        except Exception:
            export_root = Path(".")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        export_path = export_root / f"handoff-{task_id}-{stamp}.json"
        try:
            export_path.write_text(text, encoding="utf-8")
        except Exception as exc:
            msg = self._t("STATUS_MESSAGE_HANDOFF_FAILED", error=str(exc)[:80])
            self.set_status_message(msg, ttl=6)
            return

        copied = self._copy_to_clipboard(text)
        if copied:
            self.set_status_message(self._t("STATUS_MESSAGE_HANDOFF_EXPORTED", path=str(export_path)), ttl=5)
        else:
            self.set_status_message(self._t("STATUS_MESSAGE_HANDOFF_SAVED", path=str(export_path)), ttl=5)

    # ---------- Radar (AI cockpit) ----------

    def _invalidate_radar_cache(self) -> None:
        self._radar_cache_focus_id = ""
        self._radar_cache_payload = None
        self._radar_cache_error = ""
        self._radar_cache_at = 0.0

    def _radar_focus_id(self) -> str:
        if not getattr(self, "detail_mode", False) or not getattr(self, "current_task_detail", None):
            return ""
        try:
            root_task_id, _root_domain, _path_prefix = self._get_root_task_context()
            return str(root_task_id or "").strip()
        except Exception:
            return str(getattr(self.current_task_detail, "id", "") or "").strip()

    def _radar_snapshot(self, *, force: bool = False) -> Tuple[Optional[Dict[str, Any]], str]:
        """Return cached radar payload for current focus (fast, deterministic)."""
        focus_id = self._radar_focus_id()
        if not focus_id:
            return None, self._t("STATUS_TASK_NOT_SELECTED")
        now = time.time()
        if (
            not force
            and self._radar_cache_focus_id == focus_id
            and (now - float(self._radar_cache_at or 0.0)) < 0.6
            and isinstance(self._radar_cache_payload, dict)
        ):
            return self._radar_cache_payload, str(self._radar_cache_error or "")

        from core.desktop.devtools.interface.intent_api import process_intent

        resp = process_intent(self.manager, {"intent": "radar", "task": focus_id, "limit": 3, "max_chars": 12_000})
        payload = resp.result if isinstance(resp.result, dict) else {}
        err = "" if resp.success else str(resp.error_message or resp.error_code or "radar failed")
        self._radar_cache_focus_id = focus_id
        self._radar_cache_payload = dict(payload or {})
        self._radar_cache_error = err
        self._radar_cache_at = now
        return self._radar_cache_payload, err

    def _refresh_current_detail_from_disk(self) -> None:
        """Reload current detail view from the root task file (handles nested navigation)."""
        if not getattr(self, "current_task_detail", None):
            return
        try:
            root_task_id, root_domain, path_prefix = self._get_root_task_context()
        except Exception:
            return
        updated_root = self.manager.load_task(root_task_id, root_domain, skip_sync=True)
        if not updated_root:
            return
        self.task_details_cache[root_task_id] = updated_root
        if path_prefix:
            derived = self._derive_nested_detail(updated_root, root_task_id, path_prefix)
            if derived:
                derived.domain = root_domain
                self.current_task_detail = derived
            else:
                self.current_task_detail = updated_root
        else:
            self.current_task_detail = updated_root
        self.detail_flat_dirty = True
        try:
            self._rebuild_detail_flat()
        except Exception:
            pass
        if self.current_task_detail and getattr(self.current_task_detail, "kind", "task") == "plan":
            self._detail_plan_tasks_dirty = True

    def copy_radar_next(self) -> None:
        payload, err = self._radar_snapshot(force=True)
        if err:
            self.set_status_message(err[:120], ttl=5)
            return
        data = payload if isinstance(payload, dict) else {}
        next_items = data.get("next") if isinstance(data.get("next"), list) else []
        next_item = next_items[0] if next_items and isinstance(next_items[0], dict) else {}
        action = str(next_item.get("action", "") or "").strip()
        params = next_item.get("params") if isinstance(next_item.get("params"), dict) else {}
        if not action:
            self.set_status_message(self._t("RADAR_NO_NEXT"), ttl=4)
            return
        cmd = {"intent": action, **dict(params or {})}
        text = json.dumps(cmd, ensure_ascii=False)
        if self._copy_to_clipboard(text):
            self.set_status_message(self._t("RADAR_COPIED"), ttl=3)
        else:
            self.set_status_message(self._t("CLIPBOARD_EMPTY"), ttl=3)

    def execute_radar_next(self) -> None:
        payload, err = self._radar_snapshot(force=True)
        if err:
            self.set_status_message(err[:120], ttl=5)
            return
        data = payload if isinstance(payload, dict) else {}
        next_items = data.get("next") if isinstance(data.get("next"), list) else []
        next_item = next_items[0] if next_items and isinstance(next_items[0], dict) else {}
        action = str(next_item.get("action", "") or "").strip()
        params = next_item.get("params") if isinstance(next_item.get("params"), dict) else {}
        validated = bool(next_item.get("validated", False))
        if not action:
            self.set_status_message(self._t("RADAR_NO_NEXT"), ttl=4)
            return
        if not validated:
            self.set_status_message(self._t("RADAR_NEXT_NOT_VALIDATED"), ttl=4)
            return
        request = {"intent": action, **dict(params or {})}

        from core.desktop.devtools.interface.intent_api import process_intent

        with self._spinner(self._t("SPINNER_EXECUTE_NEXT", fallback="Выполняю следующий шаг")):
            resp = process_intent(self.manager, request)

        self._invalidate_radar_cache()
        if not resp.success:
            msg = str(resp.error_message or resp.error_code or "FAILED")
            self.set_status_message(msg[:140], ttl=6)
            self.force_render()
            return

        self._refresh_current_detail_from_disk()
        self.set_status_message(self._t("RADAR_EXECUTED"), ttl=3)
        self.force_render()

    def _open_task_text_editor(self, context: str) -> None:
        """Open a multiline editor for a task-level text field."""
        target = self._command_palette_target()
        if not target:
            self.set_status_message(self._t("CMD_PALETTE_NO_TASK"), ttl=4)
            return
        _, _, detail = target
        # If we're not in detail mode, enter it to keep the UX consistent.
        if not getattr(self, "detail_mode", False) and getattr(self, "filtered_tasks", None):
            try:
                self.show_task_details(self.filtered_tasks[self.selected_index])
            except Exception:
                pass
        current_value = ""
        if context == "task_description":
            current_value = str(getattr(detail, "description", "") or "")
        elif context == "task_context":
            current_value = str(getattr(detail, "context", "") or "")
        elif context == "task_plan_doc":
            current_value = str(getattr(detail, "plan_doc", "") or "")
        elif context == "task_contract":
            current_value = str(getattr(detail, "contract", "") or "")
        self.start_editing(context, current_value, None)

    def _sanitize_current_task_plan(self) -> None:
        """Sanitize Plan doc/steps by moving misplaced content into the right artifacts."""
        from core.desktop.devtools.application.plan_hygiene import plan_doc_overlap_reasons, plan_steps_overlap_reasons
        from core.desktop.devtools.application.plan_sanitizer import sanitize_plan

        target = self._command_palette_target()
        if not target:
            self.set_status_message(self._t("CMD_PALETTE_NO_TASK"), ttl=4)
            return
        task_id, domain, detail = target

        doc_reasons = plan_doc_overlap_reasons(str(getattr(detail, "plan_doc", "") or ""))
        steps_reasons = plan_steps_overlap_reasons(list(getattr(detail, "plan_steps", []) or []))
        if not doc_reasons and not steps_reasons:
            self.set_status_message(self._t("CMD_PALETTE_PLAN_ALREADY_CLEAN"), ttl=4)
            return

        snapshot_id = self._snapshot_task_file(detail, label="plan-sanitize")
        result = sanitize_plan(detail, self.manager, actor="human")
        if not result.changed:
            self.set_status_message(self._t("CMD_PALETTE_PLAN_ALREADY_CLEAN"), ttl=4)
            return

        self.manager.save_task(detail)
        self._refresh_after_task_update(task_id=task_id, domain=getattr(detail, "domain", "") or domain)

        parts: List[str] = []
        if result.moved_checklist_items:
            parts.append(f"{self._t('SUBTASKS')}+{result.moved_checklist_items}")
        if result.moved_step_ids_to_depends_on:
            parts.append(f"depends_on+{result.moved_step_ids_to_depends_on}")
        if result.moved_step_ids_to_dependencies:
            parts.append(f"{self._t('DETAIL_META_DEPENDENCIES')}+{result.moved_step_ids_to_dependencies}")
        if result.moved_done_criteria:
            parts.append(f"{self._t('DETAIL_DONE_CRITERIA')}+{result.moved_done_criteria}")
        if result.removed_plan_doc_lines:
            parts.append(f"plan_doc−{result.removed_plan_doc_lines}")
        if result.removed_plan_steps:
            parts.append(f"steps−{result.removed_plan_steps}")
        if snapshot_id:
            parts.append(f"snapshot {snapshot_id}")
        summary = ", ".join(parts) if parts else "ok"
        self.set_status_message(self._t("CMD_PALETTE_PLAN_SANITIZED", summary=summary), ttl=6)

    def _snapshot_task_file(self, task_detail: TaskDetail, *, label: str) -> Optional[str]:
        """Create a best-effort snapshot of the current task file for recovery."""
        tasks_dir = getattr(self.manager, "tasks_dir", None)
        if not tasks_dir:
            return None
        try:
            base = Path(tasks_dir).resolve()
        except Exception:
            return None
        src_raw = getattr(task_detail, "_source_path", None)
        if src_raw:
            src = Path(str(src_raw)).resolve()
        else:
            domain = str(getattr(task_detail, "domain", "") or "")
            src = (base / domain / f"{task_detail.id}.task").resolve() if domain else (base / f"{task_detail.id}.task").resolve()
        if not src.exists():
            return None
        try:
            import shutil

            snap_dir = base / ".snapshots"
            snap_dir.mkdir(parents=True, exist_ok=True)
            snapshot_id = f"{task_detail.id}-{label}-{time.time_ns()}"
            dest = snap_dir / f"{snapshot_id}.task"
            shutil.copy2(src, dest)
            return snapshot_id
        except Exception:
            return None

    def _apply_command_patch(self, patch: Dict[str, Any]) -> None:
        """Apply a Notes/Meta edit patch via shared helper (no contract/plan/subtasks)."""
        from core.desktop.devtools.application.task_editing import apply_step_edit, persist_step_edit

        target = self._command_palette_target()
        if not target:
            self.set_status_message(self._t("CMD_PALETTE_NO_TASK"), ttl=4)
            return
        task_id, domain, detail = target
        # Disallow command palette for Contract/Plan fields through patch.
        unsupported = {"contract", "plan_doc", "plan_steps", "plan_current"}
        if any(k in patch for k in unsupported):
            self.set_status_message(self._t("CMD_PALETTE_USE_TABS_FOR_ARTIFACTS"), ttl=4)
            return

        outcome, err = apply_step_edit(detail, self.manager, patch)
        if err:
            msg = err.message
            if err.code == "INVALID_DEPENDENCIES" and isinstance(err.payload, dict) and err.payload.get("errors"):
                msg = str(err.payload["errors"][0])
            if err.code == "CIRCULAR_DEPENDENCY" and isinstance(err.payload, dict) and err.payload.get("cycle"):
                msg = f"{self._t('ERR_CIRCULAR_DEP')}: {err.payload.get('cycle')}"
            self.set_status_message(msg, ttl=6)
            return
        ok, persist_err = persist_step_edit(self.manager, detail, target_domain=(outcome.target_domain if outcome else None))
        if not ok:
            self.set_status_message(persist_err.message if persist_err else self._t("ERR_UPDATE_FAILED"), ttl=6)
            return

        self._refresh_after_task_update(task_id=task_id, domain=getattr(detail, "domain", "") or domain)
        fields = ", ".join(outcome.updated_fields if outcome else [])
        self.set_status_message(self._t("STATUS_MESSAGE_TASK_EDITED", task_id=task_id, fields=fields), ttl=4)

    def _apply_command_tags(self, args: List[str]) -> None:
        target = self._command_palette_target()
        if not target:
            self.set_status_message(self._t("CMD_PALETTE_NO_TASK"), ttl=4)
            return
        task_id, domain, detail = target
        current = list(getattr(detail, "tags", []) or [])
        if not args:
            self.set_status_message(
                self._t("CMD_PALETTE_TAGS", task_id=task_id, value=(", ".join(current) if current else "-")),
                ttl=4,
            )
            return
        # Support: tag +a -b, tag =a,b, tag a,b (set)
        if len(args) == 1 and args[0].startswith("="):
            raw = args[0].lstrip("=")
            self._apply_command_patch({"tags": [t.strip() for t in raw.split(",") if t.strip()]})
            return
        if all(a.startswith(("+", "-")) for a in args):
            for token in args:
                op, val = token[0], token[1:]
                val = self._normalize_tag(val)
                if not val:
                    continue
                if op == "+" and val not in current:
                    current.append(val)
                if op == "-" and val in current:
                    current = [t for t in current if t != val]
            self._apply_command_patch({"tags": current})
            return
        # Default: set
        raw = " ".join(args)
        items = [t.strip() for t in raw.split(",") if t.strip()]
        self._apply_command_patch({"tags": items})

    def _apply_command_priority(self, args: List[str]) -> None:
        target = self._command_palette_target()
        if not target:
            self.set_status_message(self._t("CMD_PALETTE_NO_TASK"), ttl=4)
            return
        task_id, _, detail = target
        current = str(getattr(detail, "priority", "") or "MEDIUM").strip().upper()
        order = ["LOW", "MEDIUM", "HIGH"]
        if not args:
            try:
                next_value = order[(order.index(current) + 1) % len(order)]
            except ValueError:
                next_value = "MEDIUM"
            self._apply_command_patch({"priority": next_value})
            return
        self._apply_command_patch({"priority": args[0]})

    def _apply_command_deps(self, args: List[str]) -> None:
        target = self._command_palette_target()
        if not target:
            self.set_status_message(self._t("CMD_PALETTE_NO_TASK"), ttl=4)
            return
        task_id, _, detail = target
        current = list(getattr(detail, "depends_on", []) or [])
        if not args:
            self.set_status_message(
                self._t("CMD_PALETTE_DEPS", task_id=task_id, value=(", ".join(current) if current else "-")),
                ttl=4,
            )
            return
        if len(args) == 1 and args[0].startswith("="):
            raw = args[0].lstrip("=")
            self._apply_command_patch({"depends_on": [t.strip() for t in raw.split(",") if t.strip()]})
            return
        if all(a.startswith(("+", "-")) for a in args):
            for token in args:
                op, val = token[0], token[1:]
                if not val:
                    continue
                if op == "+":
                    self._apply_command_patch({"add_dep": val})
                elif op == "-":
                    self._apply_command_patch({"remove_dep": val})
            return
        raw = " ".join(args)
        items = [t.strip() for t in raw.split(",") if t.strip()]
        self._apply_command_patch({"depends_on": items})

    def _refresh_after_task_update(self, *, task_id: str, domain: str) -> None:
        """Refresh caches and current view after an in-place task edit."""
        preserve_path = str(getattr(self, "detail_selected_path", "") or "")
        updated_root = self.manager.load_task(task_id, domain, skip_sync=True)
        if updated_root:
            self.task_details_cache[task_id] = updated_root
            if getattr(self, "detail_mode", False) and getattr(self, "current_task_detail", None):
                if getattr(self, "navigation_stack", None):
                    self._refresh_navigation_stack_details(updated_root, task_id, domain)
                    _, _, path_prefix = self._get_root_task_context()
                    if path_prefix:
                        derived = self._derive_nested_detail(updated_root, task_id, path_prefix)
                        if derived:
                            derived.domain = domain
                            self.current_task_detail = derived
                        else:
                            self.current_task_detail = updated_root
                    else:
                        self.current_task_detail = updated_root
                else:
                    self.current_task_detail = updated_root
                self._rebuild_detail_flat(preserve_path if self.current_task_detail else None)
        self._update_tasks_list_silent(skip_sync=True)
        self.force_render()

    def cancel_edit(self):
        """Отменить редактирование"""
        self.editing_mode = False
        self.edit_context = None
        self.edit_index = None
        self.edit_buffer.text = ''
        self._pending_create_parent_id = None
        self._editing_multiline = False
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
            self.detail_selected_path = prev.get("selected_key", prev.get("selected_path", ""))
            self.detail_selected_task_id = prev.get("selected_task_id", None)
            self.detail_tab = prev.get("detail_tab", "radar")
            restored_offsets = prev.get("detail_tab_scroll_offsets", None)
            if isinstance(restored_offsets, dict):
                self.detail_tab_scroll_offsets = dict(restored_offsets)
            else:
                self.detail_tab_scroll_offsets = {"radar": 0, "notes": 0, "plan": 0, "contract": 0, "meta": 0}
            for tab in ("radar", "notes", "plan", "contract", "meta"):
                self.detail_tab_scroll_offsets.setdefault(tab, 0)
            self._rebuild_detail_flat()
            # If we returned to a Plan view, mark its tasks list cache dirty so progress reflects
            # any changes performed in nested task/step details.
            if self.current_task_detail and getattr(self.current_task_detail, "kind", "task") == "plan":
                self._detail_plan_tasks_dirty = True
        else:
            self.detail_mode = False
            self.current_task = None
            self.current_task_detail = None
            self.detail_selected_index = 0
            self.detail_selected_path = ""
            self.detail_view_offset = 0
            self.horizontal_offset = 0
            self.detail_tab = "radar"
            self.detail_tab_scroll_offsets = {"radar": 0, "notes": 0, "plan": 0, "contract": 0, "meta": 0}
            self.settings_mode = False
            self._set_footer_height(self._footer_height_default_for_mode())

    def cycle_detail_tab(self, delta: int = 1) -> None:
        """Cycle between detail tabs (radar/overview/plan/contract/notes/meta)."""
        if not getattr(self, "detail_mode", False) or not getattr(self, "current_task_detail", None):
            return
        current = getattr(self, "detail_tab", "radar") or "radar"
        available = self._detail_tabs()
        try:
            idx = available.index(current)
        except ValueError:
            idx = 0
        next_tab = available[(idx + delta) % len(available)]
        if next_tab == current:
            return
        self.detail_tab = next_tab
        if next_tab != "overview":
            self.detail_tab_scroll_offsets.setdefault(next_tab, 0)
        self.force_render()

    def _detail_tabs(self) -> Tuple[str, ...]:
        """Return available detail tabs for current view.

        Notes/Plan/Contract/Meta are task-level artifacts. When inside a nested subtask
        view (navigation_stack), restrict to overview to prevent editing/serializing
        synthetic task IDs like TASK-001/0.1.
        """
        detail = getattr(self, "current_task_detail", None)
        if getattr(self, "navigation_stack", None):
            # Radar is read-only and always targets the root task id (safe in nested views).
            return ("radar", "overview")
        if detail and "/" in str(getattr(detail, "id", "") or ""):
            return ("radar", "overview")
        return DETAIL_TABS

    # ---------- list editor (task/subtask lists) ----------

    def open_list_editor(self) -> None:
        if not getattr(self, "detail_mode", False) or not getattr(self, "current_task_detail", None):
            return
        self.list_editor_mode = True
        self.list_editor_stage = "menu"
        self.list_editor_selected_index = 0
        self.list_editor_view_offset = 0
        self.list_editor_target = None
        self.list_editor_pending_action = None
        self.force_render()

    def exit_list_editor(self) -> None:
        if not getattr(self, "list_editor_mode", False):
            return
        stage = getattr(self, "list_editor_stage", "menu") or "menu"
        if stage == "list":
            # Back to menu
            self.list_editor_stage = "menu"
            self.list_editor_selected_index = 0
            self.list_editor_view_offset = 0
            self.list_editor_target = None
            self.list_editor_pending_action = None
        else:
            # Close editor
            self.list_editor_mode = False
            self.list_editor_stage = "menu"
            self.list_editor_selected_index = 0
            self.list_editor_view_offset = 0
            self.list_editor_target = None
            self.list_editor_pending_action = None
        self.force_render()

    def move_list_editor_selection(self, delta: int) -> None:
        if not getattr(self, "list_editor_mode", False) or getattr(self, "editing_mode", False):
            return
        self.list_editor_selected_index = int(getattr(self, "list_editor_selected_index", 0) or 0) + int(delta)
        self.force_render()

    def activate_list_editor(self) -> None:
        if not getattr(self, "list_editor_mode", False) or getattr(self, "editing_mode", False):
            return
        stage = getattr(self, "list_editor_stage", "menu") or "menu"
        if stage == "menu":
            options = list(self._list_editor_menu_options() or [])
            if not options:
                return
            idx = max(0, min(int(getattr(self, "list_editor_selected_index", 0) or 0), len(options) - 1))
            self.list_editor_target = dict(options[idx])
            self.list_editor_stage = "list"
            self.list_editor_selected_index = 0
            self.list_editor_view_offset = 0
            self.force_render()
            return

        # In list stage: Enter edits item, or adds when empty.
        _, items = self._list_editor_current_title_and_items()
        if not items:
            self.add_list_editor_item()
        else:
            self.edit_list_editor_item()

    def add_list_editor_item(self) -> None:
        if not getattr(self, "list_editor_mode", False) or getattr(self, "editing_mode", False):
            return
        stage = getattr(self, "list_editor_stage", "menu") or "menu"
        if stage == "menu":
            # Convenience: open selected list first.
            self.activate_list_editor()
            if getattr(self, "list_editor_stage", "menu") != "list":
                return
        _, items = self._list_editor_current_title_and_items()
        selected = int(getattr(self, "list_editor_selected_index", 0) or 0)
        insert_at = max(0, min(selected + 1, len(items))) if items else 0
        self.list_editor_pending_action = "add"
        self.start_editing("list_editor_item_add", "", insert_at)

    def edit_list_editor_item(self) -> None:
        if not getattr(self, "list_editor_mode", False) or getattr(self, "editing_mode", False):
            return
        if getattr(self, "list_editor_stage", "menu") != "list" or not getattr(self, "list_editor_target", None):
            return
        _, items = self._list_editor_current_title_and_items()
        if not items:
            return
        idx = max(0, min(int(getattr(self, "list_editor_selected_index", 0) or 0), len(items) - 1))
        self.list_editor_pending_action = "edit"
        self.start_editing("list_editor_item_edit", str(items[idx] or ""), idx)

    def _list_editor_toggle_plan_steps_current(self) -> None:
        """Toggle plan_current boundary for plan steps list (Space in list editor)."""
        if not getattr(self, "list_editor_mode", False) or getattr(self, "editing_mode", False):
            return
        if getattr(self, "list_editor_stage", "menu") != "list":
            return
        target = getattr(self, "list_editor_target", None) or {}
        if str(target.get("scope", "") or "") != "task" or str(target.get("key", "") or "") != "plan_steps":
            return

        root_task_id, root_domain, root_detail, _ = self._list_editor_root_detail()
        if not root_detail:
            return
        steps = list(getattr(root_detail, "plan_steps", []) or [])
        if not steps:
            return
        total = len(steps)

        selected = int(getattr(self, "list_editor_selected_index", 0) or 0)
        selected = max(0, min(selected, total - 1))
        current = int(getattr(root_detail, "plan_current", 0) or 0)
        current = max(0, min(current, total))

        # plan_current is a boundary: [0..plan_current) are done, plan_current is current step.
        # Space toggles the boundary around the selected step.
        new_current = min(total, selected + 1) if selected >= current else selected
        if new_current == current:
            return
        root_detail.plan_current = new_current
        self._list_editor_persist_root(root_task_id, root_domain, root_detail)
        self.list_editor_selected_index = selected
        self.force_render()

    def confirm_delete_list_editor_item(self) -> None:
        if getattr(self, "confirm_mode", False) or not getattr(self, "list_editor_mode", False):
            return
        if getattr(self, "editing_mode", False):
            return
        if getattr(self, "list_editor_stage", "menu") != "list" or not getattr(self, "list_editor_target", None):
            return
        list_title, items = self._list_editor_current_title_and_items()
        if not items:
            return
        idx = max(0, min(int(getattr(self, "list_editor_selected_index", 0) or 0), len(items) - 1))
        item = str(items[idx] or "")

        def _do():
            self._delete_list_editor_item(idx)

        lines = [
            self._t("CONFIRM_DELETE_LIST_ITEM", list_title=list_title, number=idx + 1, item=item),
            self._t("CONFIRM_IRREVERSIBLE"),
        ]
        self._open_confirm_dialog(title=self._t("CONFIRM_TITLE_DELETE"), lines=lines, on_yes=_do)

    def _delete_list_editor_item(self, idx: int) -> None:
        if not getattr(self, "list_editor_mode", False):
            return
        if getattr(self, "list_editor_stage", "menu") != "list" or not getattr(self, "list_editor_target", None):
            return
        root_task_id, root_domain, root_detail, _ = self._list_editor_root_detail()
        if not root_detail:
            return
        items_ref = self._list_editor_items_ref(root_detail, self.list_editor_target or {})
        if items_ref is None:
            return
        if idx < 0 or idx >= len(items_ref):
            return
        target = self.list_editor_target or {}
        scope = str(target.get("scope", "") or "")
        key = str(target.get("key", "") or "")
        is_plan_steps = scope == "task" and key == "plan_steps"
        if scope == "task" and key in {"tags", "depends_on"}:
            from core.desktop.devtools.application.task_editing import apply_task_edit

            current_items = [str(x) for x in (items_ref or [])]
            candidate = current_items[:idx] + current_items[idx + 1 :]
            patch = {"tags": candidate} if key == "tags" else {"depends_on": candidate}
            outcome, err = apply_task_edit(root_detail, self.manager, patch)
            if err:
                msg = err.message
                if err.code == "INVALID_DEPENDENCIES" and isinstance(err.payload, dict) and err.payload.get("errors"):
                    msg = str(err.payload["errors"][0])
                self.set_status_message(msg, ttl=6)
                return
            self._list_editor_persist_root(root_task_id, root_domain, root_detail)
            self.list_editor_selected_index = max(0, min(idx, max(0, len(candidate) - 1)))
            self.force_render()
            return
        if is_plan_steps:
            current = int(getattr(root_detail, "plan_current", 0) or 0)
            if idx < current:
                root_detail.plan_current = max(0, current - 1)
        del items_ref[idx]
        if is_plan_steps:
            try:
                from core.desktop.devtools.application.plan_semantics import mark_plan_updated

                mark_plan_updated(root_detail)
            except Exception:
                pass
        self._list_editor_persist_root(root_task_id, root_domain, root_detail)
        # Keep selection stable.
        self.list_editor_selected_index = max(0, min(idx, max(0, len(items_ref) - 1)))
        self.force_render()

    def _list_editor_root_detail(self) -> Tuple[str, str, Optional[TaskDetail], str]:
        if not getattr(self, "current_task_detail", None):
            return "", "", None, ""
        root_task_id, root_domain, path_prefix = self._get_root_task_context()
        if not root_task_id:
            return "", "", None, ""
        root_detail: Optional[TaskDetail]
        if getattr(self, "navigation_stack", None):
            root_detail = self.task_details_cache.get(root_task_id)
            if not root_detail:
                root_detail = self.manager.load_task(root_task_id, root_domain, skip_sync=True)
        else:
            root_detail = self.current_task_detail
        return root_task_id, root_domain, root_detail, path_prefix

    def _list_editor_items_ref(self, root_detail: TaskDetail, target: Dict[str, Any]) -> Optional[List[str]]:
        scope = str(target.get("scope", "") or "")
        key = str(target.get("key", "") or "")
        if scope == "task":
            if not hasattr(root_detail, key):
                return None
            items = getattr(root_detail, key)
            if items is None:
                items = []
                setattr(root_detail, key, items)
            return items if isinstance(items, list) else None
        if scope == "subtask":
            path = str(target.get("path", "") or "")
            if not path:
                return None
            subtask, _, _ = _find_step_by_path(root_detail.steps, path)
            if not subtask or not hasattr(subtask, key):
                return None
            items = getattr(subtask, key)
            if items is None:
                items = []
                setattr(subtask, key, items)
            return items if isinstance(items, list) else None
        return None

    def _refresh_navigation_stack_details(self, updated_root: TaskDetail, root_task_id: str, root_domain: str) -> None:
        if not getattr(self, "navigation_stack", None):
            return

        prefix_parts: List[str] = []
        for idx, frame in enumerate(self.navigation_stack):
            if idx == 0:
                frame["detail"] = updated_root
            else:
                prefix = ".".join([p for p in prefix_parts if p])
                derived = self._derive_nested_detail(updated_root, root_task_id, prefix)
                if derived:
                    derived.domain = root_domain
                    frame["detail"] = derived
            prefix_parts.append(str(frame.get("entered_path", frame.get("selected_path", "")) or ""))

    def _list_editor_persist_root(self, root_task_id: str, root_domain: str, root_detail: TaskDetail) -> None:
        preserve_path = str(getattr(self, "detail_selected_path", "") or "")
        # Keep plan_current within plan_steps bounds after list mutations.
        try:
            steps = getattr(root_detail, "plan_steps", None)
            if isinstance(steps, list):
                current = int(getattr(root_detail, "plan_current", 0) or 0)
                root_detail.plan_current = max(0, min(current, len(steps)))
        except Exception:
            pass
        self.manager.save_task(root_detail)
        updated_root = self.manager.load_task(root_task_id, root_domain, skip_sync=True) or root_detail
        self.task_details_cache[root_task_id] = updated_root

        if getattr(self, "navigation_stack", None):
            self._refresh_navigation_stack_details(updated_root, root_task_id, root_domain)

            _, _, path_prefix = self._get_root_task_context()
            if path_prefix:
                derived = self._derive_nested_detail(updated_root, root_task_id, path_prefix)
                if derived:
                    derived.domain = root_domain
                    self.current_task_detail = derived
                else:
                    self.current_task_detail = updated_root
            else:
                self.current_task_detail = updated_root
        else:
            self.current_task_detail = updated_root

        self._rebuild_detail_flat(preserve_path if self.current_task_detail else None)
        self._update_tasks_list_silent(skip_sync=True)

    def _apply_list_editor_edit(self, context: str, new_value: str, edit_index: Optional[int]) -> bool:
        if context not in {"list_editor_item_add", "list_editor_item_edit"}:
            return False
        if not getattr(self, "list_editor_mode", False):
            return False
        if getattr(self, "list_editor_stage", "menu") != "list" or not getattr(self, "list_editor_target", None):
            return False

        root_task_id, root_domain, root_detail, _ = self._list_editor_root_detail()
        if not root_detail:
            return False
        items_ref = self._list_editor_items_ref(root_detail, self.list_editor_target or {})
        if items_ref is None:
            return False
        target = self.list_editor_target or {}
        scope = str(target.get("scope", "") or "")
        key = str(target.get("key", "") or "")
        is_plan_steps = str((self.list_editor_target or {}).get("scope", "") or "") == "task" and str((self.list_editor_target or {}).get("key", "") or "") == "plan_steps"

        # Special cases: tags/depends_on require normalization/validation (and should never
        # persist invalid state via raw list mutations).
        if scope == "task" and key in {"tags", "depends_on"}:
            from core.desktop.devtools.application.task_editing import apply_task_edit

            current_items = [str(x) for x in (items_ref or [])]
            if context == "list_editor_item_add":
                idx = int(edit_index) if edit_index is not None else len(current_items)
                idx = max(0, min(idx, len(current_items)))
                candidate = list(current_items)
                candidate.insert(idx, new_value)
                self.list_editor_selected_index = idx
            else:
                idx = int(edit_index) if edit_index is not None else int(getattr(self, "list_editor_selected_index", 0) or 0)
                if idx < 0 or idx >= len(current_items):
                    return False
                candidate = list(current_items)
                candidate[idx] = new_value
                self.list_editor_selected_index = idx

            patch = {"tags": candidate} if key == "tags" else {"depends_on": candidate}
            outcome, err = apply_task_edit(root_detail, self.manager, patch)
            if err:
                msg = err.message
                if err.code == "INVALID_DEPENDENCIES" and isinstance(err.payload, dict) and err.payload.get("errors"):
                    msg = str(err.payload["errors"][0])
                self.set_status_message(msg, ttl=6)
                return False
            # Persist via existing root-persist path to refresh nested views safely.
            self._list_editor_persist_root(root_task_id, root_domain, root_detail)
            return True

        if context == "list_editor_item_add":
            idx = int(edit_index) if edit_index is not None else len(items_ref)
            idx = max(0, min(idx, len(items_ref)))
            if is_plan_steps:
                current = int(getattr(root_detail, "plan_current", 0) or 0)
                if idx <= current:
                    root_detail.plan_current = current + 1
            items_ref.insert(idx, new_value)
            self.list_editor_selected_index = idx
        else:
            idx = int(edit_index) if edit_index is not None else int(getattr(self, "list_editor_selected_index", 0) or 0)
            if idx < 0 or idx >= len(items_ref):
                return False
            items_ref[idx] = new_value
            self.list_editor_selected_index = idx

        if is_plan_steps:
            try:
                from core.desktop.devtools.application.plan_semantics import mark_plan_updated

                mark_plan_updated(root_detail)
            except Exception:
                pass
        self._list_editor_persist_root(root_task_id, root_domain, root_detail)
        return True

    def _list_editor_menu_options(self) -> List[Dict[str, Any]]:
        options: List[Dict[str, Any]] = []
        task_prefix = self._t("LIST_EDITOR_SCOPE_TASK")
        subtask_prefix = self._t("LIST_EDITOR_SCOPE_SUBTASK")

        task_lists: List[Tuple[str, str]] = [
            ("plan_steps", "DETAIL_STEPS"),
            ("tags", "TAGS"),
            ("next_steps", "DETAIL_META_NEXT_STEPS"),
            ("dependencies", "DETAIL_META_DEPENDENCIES"),
            ("depends_on", "DETAIL_META_DEPENDS_ON"),
            ("success_criteria", "DETAIL_META_SUCCESS_CRITERIA"),
            ("problems", "DETAIL_META_PROBLEMS"),
            ("risks", "DETAIL_META_RISKS"),
            ("history", "DETAIL_META_HISTORY"),
        ]
        for key, label_key in task_lists:
            label = f"{task_prefix}: {self._t(label_key)}"
            if key == "plan_steps":
                _, _, root_detail, _ = self._list_editor_root_detail()
                if root_detail is not None:
                    steps = list(getattr(root_detail, "plan_steps", []) or [])
                    current = int(getattr(root_detail, "plan_current", 0) or 0)
                    current = max(0, min(current, len(steps)))
                    label = f"{task_prefix}: {self._t(label_key)} {current}/{len(steps)}"
            if key in {"tags", "depends_on"}:
                _, _, root_detail, _ = self._list_editor_root_detail()
                if root_detail is not None:
                    items = list(getattr(root_detail, key, []) or [])
                    label = f"{task_prefix}: {self._t(label_key)} ({len(items)})"
            options.append(
                {
                    "scope": "task",
                    "key": key,
                    "label": label,
                }
            )

        # Subtask lists are based on the currently selected subtask in this view.
        entry = self._selected_subtask_entry()
        if entry and entry.kind == "step":
            path = entry.key
            _, _, path_prefix = self._get_root_task_context()
            full_path = f"{path_prefix}.{path}" if path_prefix else path
            base = f"{subtask_prefix} {self._display_subtask_path(full_path)}:"
            options.extend(
                [
                    {
                        "scope": "subtask",
                        "key": "success_criteria",
                        "path": full_path,
                        "label": f"{base} {self._t('CRITERIA')}",
                    },
                    {
                        "scope": "subtask",
                        "key": "tests",
                        "path": full_path,
                        "label": f"{base} {self._t('TESTS')}",
                    },
                    {
                        "scope": "subtask",
                        "key": "blockers",
                        "path": full_path,
                        "label": f"{base} {self._t('BLOCKERS')}",
                    },
                ]
            )

        return options

    def _list_editor_current_title_and_items(self) -> Tuple[str, List[str]]:
        target = getattr(self, "list_editor_target", None) or {}
        if not target:
            return self._t("LIST_EDITOR_TITLE_LIST"), []
        _, _, root_detail, _ = self._list_editor_root_detail()
        if not root_detail:
            return self._t("LIST_EDITOR_TITLE_LIST"), []
        title = str(target.get("label", "") or self._t("LIST_EDITOR_TITLE_LIST"))
        items_ref = self._list_editor_items_ref(root_detail, target)
        items = [str(x) for x in (items_ref or [])]
        return title, items

    def edit_current_item(self):
        """Редактировать текущий элемент"""
        if self.detail_mode and self.current_task_detail:
            if getattr(self, "detail_tab", "overview") != "overview":
                return
            # В списке подзадач - редактируем название подзадачи
            entry = self._selected_subtask_entry()
            if entry and entry.kind == "step" and isinstance(entry.node, Step):
                st = entry.node
                self.start_editing('subtask_title', st.title, self.detail_selected_index)
        else:
            # В списке задач - редактируем название задачи
            if self.filtered_tasks:
                task = self.filtered_tasks[self.selected_index]
                task_detail = task.detail or TaskFileParser.parse(Path(task.task_file))
                self.current_task_detail = task_detail
                self.start_editing('task_title', task_detail.title)

    def get_footer_text(self) -> FormattedText:
        if getattr(self, "confirm_mode", False):
            return FormattedText([("class:text.dimmer", self._t("CONFIRM_HINT"))])
        if getattr(self, "help_visible", False):
            return FormattedText([
                ("class:icon.info", "ℹ "),
                ("class:header", self._t("HELP_KEYS_TITLE")),
                ("", "\n"),
                ("class:text", self._t("NAV_STATUS_HINT")),
                ("", "\n"),
                ("class:text.dim", self._t("NAV_DETAIL_OVERVIEW_HINT")),
                ("", "\n"),
                ("class:text.dim", self._t("NAV_DETAIL_TAB_SCROLL_HINT")),
                ("", "\n"),
                ("class:text.dim", self._t("NAV_CHECKPOINT_HINT")),
                ("", "\n"),
                ("class:icon.info", "ℹ "),
                ("class:header", self._t("HELP_PALETTE_TITLE")),
                ("", "\n"),
                ("class:text", ": tag +ux -mcp | : prio HIGH | : dep +TASK-002 | : domain desktop/devtools"),
                ("", "\n"),
                ("class:icon.info", "ℹ "),
                ("class:header", self._t("HELP_ARTIFACTS_TITLE")),
                ("", "\n"),
                ("class:text.dim", self._t("HELP_ARTIFACT_NOTES")),
                ("", "\n"),
                ("class:text.dim", self._t("HELP_ARTIFACT_PLAN")),
                ("", "\n"),
                ("class:text.dim", self._t("HELP_ARTIFACT_CONTRACT")),
                ("", "\n"),
                ("class:text.dim", self._t("HELP_ARTIFACT_META")),
                ("", "\n"),
                ("class:icon.info", "FAQ "),
                ("class:text", self._t("HELP_DOMAIN_FAQ")),
                ("", "\n"),
                ("class:text.dim", self._t("HELP_DOMAIN_DOC")),
            ])
        if getattr(self, "settings_mode", False):
            options = self._settings_options()
            idx = getattr(self, "settings_selected_index", 0)
            if options and 0 <= idx < len(options):
                hint = options[idx].get("hint", "")
                if hint:
                    return FormattedText([("class:text.dim", hint)])
            return FormattedText([("class:text.dimmer", self._t("NAV_SETTINGS_HINT", default="↑↓ navigate • Enter select • Esc close"))])
        if getattr(self, "list_editor_mode", False):
            stage = getattr(self, "list_editor_stage", "menu")
            hint_key = "LIST_EDITOR_HINT_MENU" if stage == "menu" else "LIST_EDITOR_HINT_LIST"
            if stage != "menu":
                target = getattr(self, "list_editor_target", None) or {}
                if str(target.get("scope", "") or "") == "task" and str(target.get("key", "") or "") == "plan_steps":
                    hint_key = "LIST_EDITOR_HINT_STEPS"
            return FormattedText([("class:text.dimmer", self._t(hint_key))])
        if self.detail_mode and self.current_task_detail:
            return build_footer_text(self)
        if self.editing_mode:
            hint_key = "NAV_EDIT_HINT_MULTILINE" if getattr(self, "_editing_multiline", False) else "NAV_EDIT_HINT"
            return FormattedText([("class:text.dimmer", self._t(hint_key))])
        return build_footer_text(self)

    def get_body_content(self) -> FormattedText:
        if getattr(self, "confirm_mode", False):
            return render_confirm_dialog(self)
        if getattr(self, "list_editor_mode", False):
            return render_list_editor_dialog(self)
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
        self._set_footer_height(self._footer_height_default_for_mode())
        self.force_render()

    def _resolve_body_container(self):
        if self.editing_mode:
            return self._build_edit_container()
        return self.main_window

    def _build_edit_container(self):
        labels = {
            'task_title': self._t("EDIT_TASK_TITLE"),
            'task_description': self._t("EDIT_TASK_DESCRIPTION"),
            'task_context': self._t("EDIT_TASK_CONTEXT"),
            'task_contract': self._t("EDIT_TASK_CONTRACT"),
            'task_plan_doc': self._t("EDIT_TASK_PLAN_DOC"),
            'command_palette': self._t("EDIT_COMMAND_PALETTE"),
            'subtask_title': self._t("EDIT_SUBTASK"),
            'criterion': self._t("EDIT_CRITERION"),
            'test': self._t("EDIT_TEST"),
            'blocker': self._t("EDIT_BLOCKER"),
            'create_plan_title': self._t("EDIT_CREATE_PLAN"),
            'create_task_title': self._t("EDIT_CREATE_TASK"),
            'list_editor_item_add': self._t("EDIT_LIST_ITEM_ADD"),
            'list_editor_item_edit': self._t("EDIT_LIST_ITEM"),
            'token': 'GitHub PAT',
            'project_number': self._t("EDIT_PROJECT_NUMBER"),
            'project_workers': self._t("EDIT_PROJECT_WORKERS"),
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

def cmd_tui(args) -> int:
    tui = TaskTrackerTUI(
        tasks_dir=None,  # force internal resolver to pick project storage (global by default)
        theme=getattr(args, "theme", DEFAULT_THEME),
        mono_select=getattr(args, "mono_select", False),
        use_global=bool(getattr(args, "use_global", True)),
    )
    tui.run()
    return 0
