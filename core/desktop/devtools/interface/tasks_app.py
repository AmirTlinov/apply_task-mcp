#!/usr/bin/env python3
"""
tasks.py — flagship task manager (single-file CLI/TUI).

All tasks live under .tasks/ (one .task file per task).
"""

import argparse
import json
import os
import re
import sys
import time
import subprocess
import shlex
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import requests
import webbrowser
from dataclasses import dataclass
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union, Set
from contextlib import contextmanager

import yaml
from wcwidth import wcwidth
from core import Status, SubTask, TaskDetail
from core.desktop.devtools.application.task_manager import TaskManager, current_timestamp, _attach_subtask, _find_subtask_by_path, _flatten_subtasks
from core.desktop.devtools.interface.cli_parser import build_parser as build_cli_parser
from core.desktop.devtools.interface.tui_render import (
    render_detail_text,
    render_detail_text_impl,
    render_task_list_text,
    render_task_list_text_impl,
    render_subtask_details,
    render_subtask_details_impl,
    render_single_subtask_view,
)
from core.desktop.devtools.interface.edit_handlers import (
    handle_token,
    handle_project_number,
    handle_project_workers,
    handle_bootstrap_remote,
    handle_task_edit,
)
from core.desktop.devtools.interface.constants import AI_HELP, LANG_PACK, TIMESTAMP_FORMAT, GITHUB_GRAPHQL
from core.desktop.devtools.interface.i18n import translate, effective_lang as _effective_lang
from core.desktop.devtools.interface.cli_io import structured_response, structured_error, validation_response
from core.desktop.devtools.application.context import (
    save_last_task,
    get_last_task,
    resolve_task_reference,
    derive_domain_explicit,
    derive_folder_explicit,
    normalize_task_id,
    parse_smart_title,
)
from core.desktop.devtools.application.recommendations import next_recommendations, suggest_tasks, quick_overview
from application.ports import TaskRepository
from infrastructure.file_repository import FileTaskRepository
from infrastructure.task_file_parser import TaskFileParser
from infrastructure.projects_sync_service import ProjectsSyncService
from application.sync_service import SyncService
from util.sync_status import sync_status_fragments
from util.responsive import ColumnLayout, ResponsiveLayoutManager, detail_content_width
from core.desktop.devtools.interface.cli_commands import CliDeps, cmd_list as _cmd_list, cmd_show as _cmd_show, cmd_analyze as _cmd_analyze, cmd_next as _cmd_next, cmd_suggest as _cmd_suggest, cmd_quick as _cmd_quick
from core.desktop.devtools.interface.cli_subtask import cmd_subtask as _cmd_subtask
from core.desktop.devtools.interface.cli_create import cmd_create as _cmd_create, cmd_smart_create as _cmd_smart_create
from core.desktop.devtools.interface.cli_checkpoint import cmd_bulk as _cmd_bulk, cmd_checkpoint as _cmd_checkpoint
from core.desktop.devtools.interface.cli_interactive import (
    confirm,
    is_interactive,
    prompt,
    prompt_list,
    prompt_required,
    prompt_subtask_interactive,
    subtask_flags,
)
from core.desktop.devtools.interface.tui_mouse import handle_body_mouse
from core.desktop.devtools.interface.tui_settings import build_settings_options
from core.desktop.devtools.interface.tui_navigation import move_vertical_selection
from core.desktop.devtools.interface.tui_focus import focusable_line_indices
from core.desktop.devtools.interface.tui_settings_panel import render_settings_panel
from core.desktop.devtools.interface.tui_status import build_status_text
from core.desktop.devtools.interface.tui_footer import build_footer_text
from core.desktop.devtools.interface.tui_loader import (
    load_tasks_with_state,
    apply_context_filters,
    build_task_models,
    select_index_after_load,
)
from core.desktop.devtools.interface.tui_preview import build_side_preview_text
from core.desktop.devtools.interface.tui_actions import activate_settings_option, delete_current_item
from core.desktop.devtools.interface.tui_sync_indicator import build_sync_indicator
from core.desktop.devtools.interface.serializers import subtask_to_dict, task_to_dict
from core.desktop.devtools.interface.subtask_loader import (
    parse_subtasks_flexible,
    validate_flagship_subtasks,
    load_subtasks_source,
    _load_input_source,
)
from core.desktop.devtools.interface.subtask_validation import (
    CHECKLIST_SECTIONS,
    validate_subtasks_coverage,
    validate_subtasks_quality,
    validate_subtasks_structure,
)

import projects_sync
from projects_sync import (
    get_projects_sync,
    reload_projects_sync,
    update_projects_enabled,
    update_project_target,
    update_project_workers,
    detect_repo_slug,
)
from config import get_user_token, set_user_token, set_user_lang

# Cache for expensive Git Projects metadata lookups, throttled to avoid
# blocking the TUI render loop on every keypress.
from core.desktop.devtools.application import projects_status_cache


def _get_sync_service() -> ProjectsSyncService:
    """Factory used outside TaskManager to obtain sync adapter."""
    return ProjectsSyncService(get_projects_sync())


def validate_pat_token_http(token: str, timeout: float = 10.0) -> Tuple[bool, str]:
    if not token:
        return False, "PAT missing"
    query = "query { viewer { login } }"
    headers = {"Authorization": f"bearer {token}", "Accept": "application/vnd.github+json"}
    try:
        resp = requests.post(GITHUB_GRAPHQL, json={"query": query}, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        return False, f"Network unavailable: {exc}"
    if resp.status_code >= 400:
        return False, f"GitHub replied {resp.status_code}: {resp.text[:120]}"
    payload = resp.json()
    if payload.get("errors"):
        err = payload["errors"][0].get("message", "Unknown error")
        return False, err
    login = ((payload.get("data") or {}).get("viewer") or {}).get("login")
    if not login:
        return False, "Response missing viewer"
    return True, f"PAT valid (viewer={login})"


from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.input.ansi_escape_sequences import REVERSE_ANSI_SEQUENCES
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import HSplit, Layout, Window, VSplit
from prompt_toolkit.layout.containers import DynamicContainer
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.controls import FormattedTextControl, BufferControl
from prompt_toolkit.mouse_events import MouseEventType, MouseEvent, MouseButton, MouseModifier
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import TextArea
from prompt_toolkit.clipboard import InMemoryClipboard
try:  # pragma: no cover - optional dependency
    from prompt_toolkit.clipboard.pyperclip import PyperclipClipboard
except Exception:  # pragma: no cover
    PyperclipClipboard = None
from http.server import BaseHTTPRequestHandler, HTTPServer


# ============================================================================
# DATA MODELS
# ============================================================================
# Domain entities SubTask/TaskDetail импортируются из core.


@dataclass
class Task:
    name: str
    status: Status
    description: str
    category: str
    completed: bool = False
    task_file: Optional[str] = None
    progress: int = 0
    subtasks_count: int = 0
    subtasks_completed: int = 0
    id: Optional[str] = None
    parent: Optional[str] = None
    detail: Optional[TaskDetail] = None
    domain: str = ""
    phase: str = ""
    component: str = ""
    blocked: bool = False
CLI_DEPS = CliDeps(
    manager_factory=lambda: TaskManager(),
    translate=translate,
    derive_domain_explicit=derive_domain_explicit,
    resolve_task_reference=resolve_task_reference,
    save_last_task=save_last_task,
    normalize_task_id=normalize_task_id,
    task_to_dict=task_to_dict,
)

CHECKLIST_SECTIONS = [
    (
        "plan",
        ["plan", "break", "шаг"],
        "Plan: break work into atomic steps with measurable outcomes",
        ["step", "milestone", "outcome", "scope", "estimate"],
    ),
    (
        "validation",
        ["test", "lint", "вали", "qa"],
        "Validation plan: tests/linters per step and commit checkpoints",
        ["test", "pytest", "unit", "integration", "lint", "coverage", "commit", "checkpoint"],
    ),
    (
        "risks",
        ["risk", "dependency", "риск", "завис", "блок"],
        "Risk scan: failures, dependencies, bottlenecks",
        ["risk", "dependency", "blocker", "bottleneck", "assumption"],
    ),
    (
        "readiness",
        ["readiness", "ready", "done", "criteria", "dod", "готов", "metric"],
        "Readiness criteria: DoD, coverage/perf metrics, expected behavior",
        ["DoD", "definition", "coverage", "perf", "metric", "acceptance", "criteria"],
    ),
    (
        "execute",
        ["execute", "implement", "исполн", "build"],
        "Execute steps with per-step validation and record results",
        ["implement", "code", "wire", "build", "validate"],
    ),
    (
        "final",
        ["final", "full", "release", "финаль", "итог"],
        "Final verification: full tests/linters, metrics check, release/commit prep",
        ["regression", "full", "release", "report", "metrics", "handoff"],
    ),
]


def validate_flagship_subtasks(subtasks: List[SubTask]) -> Tuple[bool, List[str]]:
    from core.desktop.devtools.interface.subtask_loader import validate_flagship_subtasks as _vf
    return _vf(subtasks)


# ============================================================================
# FLEXIBLE SUBTASK PARSING (JSON ONLY)
# ============================================================================


# ============================================================================
# TUI
# ============================================================================

THEMES: Dict[str, Dict[str, str]] = {
    "dark-olive": {
        "": "#d7dfe6",  # без принудительной подложки
        "status.ok": "#9ad974 bold",
        "status.warn": "#e5c07b bold",
        "status.fail": "#e06c75 bold",
        "status.unknown": "#7a7f85",
        "text": "#d7dfe6",
        "text.dim": "#97a0a9",
        "text.dimmer": "#6d717a",
        "text.cont": "#8d95a0",  # заметно темнее для продолжений
        "selected": "bg:#3b3b3b #d7dfe6 bold",  # мягкий серый селект для моно-режима
        "selected.ok": "bg:#3b3b3b #9ad974 bold",
        "selected.warn": "bg:#3b3b3b #f0c674 bold",
        "selected.fail": "bg:#3b3b3b #ff6b6b bold",
        "selected.unknown": "bg:#3b3b3b #e8eaec bold",
        "header": "#ffb347 bold",
        "border": "#4b525a",
        "icon.check": "#9ad974 bold",
        "icon.warn": "#f9ac60 bold",
        "icon.fail": "#ff5156 bold",
    },
    "dark-contrast": {
        "": "#e8eaec",  # без черной подложки
        "status.ok": "#b8f171 bold",
        "status.warn": "#f0c674 bold",
        "status.fail": "#ff6b6b bold",
        "status.unknown": "#8a9097",
        "text": "#e8eaec",
        "text.dim": "#a7b0ba",
        "text.dimmer": "#6f757d",
        "text.cont": "#939aa4",
        "selected": "bg:#3d4047 #e8eaec bold",  # мягкий серый селект для моно-режима
        "selected.ok": "bg:#3d4047 #b8f171 bold",
        "selected.warn": "bg:#3d4047 #f0c674 bold",
        "selected.fail": "bg:#3d4047 #ff6b6b bold",
        "selected.unknown": "bg:#3d4047 #e8eaec bold",
        "header": "#ffb347 bold",
        "border": "#5a6169",
        "icon.check": "#b8f171 bold",
        "icon.warn": "#f9ac60 bold",
        "icon.fail": "#ff5156 bold",
    },
}

DEFAULT_THEME = "dark-olive"


class InteractiveFormattedTextControl(FormattedTextControl):
    def __init__(self, *args, mouse_handler=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._external_mouse_handler = mouse_handler

    def mouse_handler(self, mouse_event: MouseEvent):
        if self._external_mouse_handler:
            result = self._external_mouse_handler(mouse_event)
            if result is not NotImplemented:
                return result
        return super().mouse_handler(mouse_event)


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
        base = THEMES.get(theme)
        if not base:
            base = THEMES[DEFAULT_THEME]
        return dict(base)  # defensive copy

    @classmethod
    def build_style(cls, theme: str) -> Style:
        palette = cls.get_theme_palette(theme)
        return Style.from_dict(palette)

    def __init__(self, tasks_dir: Path = Path(".tasks"), domain: str = "", phase: str = "", component: str = "", theme: str = DEFAULT_THEME, mono_select: bool = False):
        self.tasks_dir = tasks_dir
        self.manager = TaskManager(tasks_dir)
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

        self.load_tasks(skip_sync=True)

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
            self.load_tasks(preserve_selection=True)

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
            if self.settings_mode and not self.editing_mode:
                self.activate_settings_option()
                return
            if self.editing_mode:
                # В режиме редактирования - сохранить
                self.save_edit()
            elif self.detail_mode and self.current_task_detail:
                # В режиме деталей Enter показывает карточку выбранной подзадачи
                entry = self._selected_subtask_entry()
                if entry:
                    path, _, _, _, _ = entry
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
            elif self.detail_mode:
                self.exit_detail_view()

        @kb.add("delete")
        @kb.add("c-d")
        def _(event):
            """Delete - удалить выбранную задачу или подзадачу"""
            self.delete_current_item()

        @kb.add("d")
        @kb.add("в")
        def _(event):
            """d - переключить выполнение подзадачи"""
            if self.detail_mode and self.current_task_detail:
                self.toggle_subtask_completion()

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
            """Left - collapse or go to parent in detail tree"""
            if getattr(self, "single_subtask_view", None):
                self.exit_detail_view()
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
            # в списке задач: поведение как backspace недоступно — оставляем без действия

        @kb.add("right")
        def _(event):
            """Right - expand or go to first child in detail tree"""
            if not self.detail_mode:
                if self.filtered_tasks:
                    self.show_task_details(self.filtered_tasks[self.selected_index])
                return
            if getattr(self, "single_subtask_view", None):
                return
            entry = self._selected_subtask_entry()
            if not entry:
                return
            path, _, _, collapsed, has_children = entry
            if has_children and collapsed:
                self._toggle_collapse_selected(expand=True)
                return
            self.show_subtask_details(path)

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
        self.set_status_message(self._t("STATUS_MESSAGE_LANG_SET", lang=next_lang))
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

    def _toggle_collapse_selected(self, expand: bool) -> None:
        entry = self._selected_subtask_entry()
        if not entry:
            return
        path, st, _, collapsed, has_children = entry
        if not has_children:
            # нет детей – попытка перейти к родителю при сворачивании
            if not expand and "." in path:
                parent_path = ".".join(path.split(".")[:-1])
                self._select_subtask_by_path(parent_path)
                self._ensure_detail_selection_visible(len(self.detail_flat_subtasks))
                self.force_render()
            return
        if expand:
            if collapsed:
                self.detail_collapsed.discard(path)
                self._rebuild_detail_flat(path)
            else:
                # перейти к первому ребёнку
                child_path = f"{path}.0" if st.children else path
                self._select_subtask_by_path(child_path)
                self._rebuild_detail_flat(child_path)
        else:
            if not collapsed:
                self.detail_collapsed.add(path)
                self._rebuild_detail_flat(path)
            elif "." in path:
                parent_path = ".".join(path.split(".")[:-1])
                self._select_subtask_by_path(parent_path)
                self._rebuild_detail_flat(parent_path)
        if self.current_task_detail:
            self.collapsed_by_task[self.current_task_detail.id] = set(self.detail_collapsed)
        self._ensure_detail_selection_visible(len(self.detail_flat_subtasks))
        self.force_render()

    def _ensure_settings_selection_visible(self, total: int) -> None:
        visible = self._visible_row_limit()
        if total <= visible:
            self.settings_view_offset = 0
            return
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
        """Scroll a single line of text, preserving leading border."""
        if not line or self.horizontal_offset == 0:
            return line

        # Check if line has table borders (starts with + or |)
        if line.startswith(('+', '|')):
            # Keep first character (left border)
            border_char = line[0]
            content = line[1:]

            # Apply offset to content
            if len(content) > self.horizontal_offset:
                scrolled_content = content[self.horizontal_offset:]
            else:
                scrolled_content = ""

            return border_char + scrolled_content
        else:
            # No border, apply offset normally
            return self.apply_horizontal_scroll(line)

    def apply_scroll_to_formatted(self, formatted_items: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
        """Apply horizontal scroll to formatted text line by line, preserving table structure."""
        if self.horizontal_offset == 0:
            return formatted_items

        result = []
        current_line = []

        for style, text in formatted_items:
            # Split text by newlines
            parts = text.split('\n')

            for i, part in enumerate(parts):
                if i > 0:
                    # We hit a newline - process accumulated line
                    if current_line:
                        # Convert line to plain text
                        line_text = ''.join(t for _, t in current_line)
                        # Scroll it
                        scrolled = self.scroll_line_preserve_borders(line_text)
                        # Add scrolled line
                        if scrolled:
                            result.append(('class:text', scrolled))
                        result.append(('', '\n'))
                        current_line = []

                # Add part to current line (if not empty)
                if part:
                    current_line.append((style, part))

        # Process last line if exists
        if current_line:
            line_text = ''.join(t for _, t in current_line)
            scrolled = self.scroll_line_preserve_borders(line_text)
            if scrolled:
                result.append(('class:text', scrolled))

        return result

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

    def _render_single_subtask_view(self, content_width: int) -> None:
        render_single_subtask_view(self, content_width)

    @property
    def filtered_tasks(self) -> List[Task]:
        if not self.current_filter:
            return self.tasks
        return [t for t in self.tasks if t.status == self.current_filter]

    def compute_signature(self) -> int:
        return self.manager.compute_signature()

    def maybe_reload(self):
        now = time.time()
        if now - self._last_check < 0.7:
            return
        self._last_check = now
        sig = self.compute_signature()
        if sig != self._last_signature:
            selected_task_file = self.tasks[self.selected_index].task_file if self.tasks else None
            prev_detail = self.current_task_detail.id if (self.detail_mode and self.current_task_detail) else None
            prev_detail_path = self.detail_selected_path
            prev_single = getattr(self, "single_subtask_view", None)

            self.load_tasks(preserve_selection=True, selected_task_file=selected_task_file, skip_sync=True)
            self._last_signature = sig
            self.set_status_message(self._t("STATUS_MESSAGE_CLI_UPDATED"), ttl=3)

            if prev_detail:
                for t in self.tasks:
                    if t.id == prev_detail:
                        # reopen detail preserving selection
                        self.show_task_details(t)
                        if prev_detail_path:
                            self._select_subtask_by_path(prev_detail_path)
                        items = self.get_detail_items_count()
                        self._ensure_detail_selection_visible(items)
                        if prev_single and prev_detail_path:
                            st = self._get_subtask_by_path(prev_detail_path)
                            if st:
                                self.show_subtask_details(prev_detail_path)
                        break

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
        self.current_task = task
        self.current_task_detail = task.detail or TaskFileParser.parse(Path(task.task_file))
        self.detail_mode = True
        self.detail_selected_index = 0
        self.detail_collapsed = set(self.collapsed_by_task.get(self.current_task_detail.id, set()))
        self._rebuild_detail_flat()
        self.detail_view_offset = 0
        self._set_footer_height(0)

    def show_subtask_details(self, path: str):
        return render_subtask_details(self, path)

    def _render_subtask_details_impl(self, path: str):
        return render_subtask_details_impl(self, path)

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
                domain = self.current_task_detail.domain
                ok, msg = self.manager.set_subtask(self.current_task_detail.id, 0, desired, domain, path=path)
                if not ok:
                    self.set_status_message(msg or self._t("STATUS_MESSAGE_CHECKPOINTS_REQUIRED"))
                    return
                updated = self.manager.load_task(self.current_task_detail.id, domain)
                if updated:
                    self.current_task_detail = updated
                    self.task_details_cache[self.current_task_detail.id] = updated
                    self._rebuild_detail_flat(path)
                self.load_tasks(preserve_selection=True)

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
        if hasattr(self, "single_subtask_view") and self.single_subtask_view:
            self.single_subtask_view = None
            self.horizontal_offset = 0
            self._set_footer_height(0 if self.detail_mode else 9)
            return
        if not self.detail_mode:
            return
        if self.navigation_stack:
            prev = self.navigation_stack.pop()
            self.current_task = prev["task"]
            self.current_task_detail = prev["detail"]
            self.detail_selected_index = 0
            self.detail_selected_path = ""
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
            # В режиме просмотра подзадачи
            if hasattr(self, "single_subtask_view") and self.single_subtask_view:
                # Редактируем название подзадачи
                entry = self._selected_subtask_entry()
                if entry:
                    _, st, _, _, _ = entry
                    self.start_editing('subtask_title', st.title, self.detail_selected_index)
            else:
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
        if getattr(self, "single_subtask_view", None):
            return FormattedText([])
        if self.detail_mode and self.current_task_detail:
            return FormattedText([("class:text.dim", self._t("NAV_ARROWS_HINT"))])
        if self.editing_mode:
            return FormattedText([
                ("class:text.dimmer", self._t("NAV_EDIT_HINT")),
            ])
        return build_footer_text(self)

    def get_body_content(self) -> FormattedText:
        """Returns content for main body - either task list or detail view."""
        if self.settings_mode:
            return self.get_settings_panel()
        if self.detail_mode and self.current_task_detail:
            # Если открыт просмотр конкретной подзадачи — показываем его
            if hasattr(self, "single_subtask_view") and self.single_subtask_view:
                return self.single_subtask_view
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


# ============================================================================
# COMMAND IMPLEMENTATIONS
# ============================================================================


def cmd_tui(args) -> int:
    tui = TaskTrackerTUI(
        Path(".tasks"),
        theme=getattr(args, "theme", DEFAULT_THEME),
        mono_select=getattr(args, "mono_select", False),
    )
    tui.run()
    return 0


def cmd_list(args) -> int:
    return _cmd_list(args, CLI_DEPS)


def cmd_show(args) -> int:
    return _cmd_show(args, CLI_DEPS)


def cmd_create(args) -> int:
    return _cmd_create(args)


def cmd_smart_create(args) -> int:
    return _cmd_smart_create(args)


def cmd_create_guided(args) -> int:
    """Полуинтерактивное создание задачи (шаг-ответ-шаг)"""
    if not is_interactive():
        print(translate("GUIDED_ONLY_INTERACTIVE"))
        print(translate("GUIDED_USE_PARAMS"))
        return 1

    print("=" * 60)
    print(translate("GUIDED_TITLE"))
    print("=" * 60)

    manager = TaskManager()

    # Шаг 1: Базовая информация
    print(f"\n{translate('GUIDED_STEP1')}")
    title = prompt_required(translate("GUIDED_TASK_TITLE"))
    parent = prompt_required(translate("GUIDED_PARENT_ID"))
    parent = normalize_task_id(parent)
    description = prompt_required(translate("GUIDED_DESCRIPTION"))
    while description.upper() == "TBD":
        print(translate("GUIDED_DESCRIPTION_TBD"))
        description = prompt_required(translate("GUIDED_DESCRIPTION"))

    # Шаг 2: Контекст и метаданные
    print(f"\n{translate('GUIDED_STEP2')}")
    context = prompt(translate("GUIDED_CONTEXT"), default="")
    tags_str = prompt(translate("GUIDED_TAGS"), default="")
    tags = [t.strip() for t in tags_str.split(",") if t.strip()]

    # Шаг 3: Риски
    print(f"\n{translate('GUIDED_STEP3')}")
    risks = prompt_list(translate("GUIDED_RISKS"), min_items=1)

    # Шаг 4: Критерии успеха / Тесты
    print(f"\n{translate('GUIDED_STEP4')}")
    tests = prompt_list(translate("GUIDED_TESTS"), min_items=1)

    # Шаг 5: Подзадачи
    print(f"\n{translate('GUIDED_STEP5')}")
    subtasks = []
    for i in range(3):
        subtasks.append(prompt_subtask_interactive(i + 1))

    while confirm(translate("GUIDED_ADD_MORE"), default=False):
        subtasks.append(prompt_subtask_interactive(len(subtasks) + 1))

    # Валидация
    print(translate("GUIDED_VALIDATION"))
    flagship_ok, flagship_issues = validate_flagship_subtasks(subtasks)
    if not flagship_ok:
        print(translate("GUIDED_WARN_ISSUES"))
        for idx, issue in enumerate(flagship_issues, 1):
            print(f"  {idx}. {issue}")

        if not confirm(translate("GUIDED_CONTINUE"), default=False):
            print(translate("GUIDED_CANCELLED"))
            return 1

    # Создание задачи
    print(translate("GUIDED_SAVING"))
    domain = derive_domain_explicit(
        getattr(args, 'domain', None),
        getattr(args, 'phase', None),
        getattr(args, 'component', None)
    )

    task = manager.create_task(
        title,
        status="FAIL",
        priority=getattr(args, 'priority', "MEDIUM"),
        parent=parent,
        domain=domain,
        phase=getattr(args, 'phase', "") or "",
        component=getattr(args, 'component', "") or "",
    )

    task.description = description
    task.context = context
    task.tags = tags
    task.risks = risks
    task.success_criteria = tests
    task.subtasks = subtasks
    task.update_status_from_progress()

    manager.save_task(task)
    save_last_task(task.id, task.domain)

    print("\n" + "=" * 60)
    print(translate("GUIDED_SUCCESS", task_id=task.id))
    print("=" * 60)
    print(f"[TASK] {task.title}")
    print(translate("GUIDED_PARENT", parent=task.parent))
    print(translate("GUIDED_SUBTASK_COUNT", count=len(task.subtasks)))
    print(translate("GUIDED_CRITERIA_COUNT", count=len(task.success_criteria)))
    print(translate("GUIDED_RISKS_COUNT", count=len(task.risks)))
    print("=" * 60)

    return 0


def cmd_update(args) -> int:
    # Бекст совместимость: допускаем оба порядка аргументов
    status = None
    task_id = None
    last_id, last_domain = get_last_task()
    for candidate in (args.arg1, args.arg2):
        if candidate and candidate.upper() in ("OK", "WARN", "FAIL"):
            status = candidate.upper()
        elif candidate:
            task_id = normalize_task_id(candidate)

    if status is None:
        return structured_error("update", translate("ERR_STATUS_REQUIRED"))

    if task_id is None:
        task_id = last_id
        if not task_id:
            return structured_error("update", translate("ERR_NO_TASK_AND_LAST"))

    manager = TaskManager()
    domain = derive_domain_explicit(getattr(args, "domain", ""), getattr(args, "phase", None), getattr(args, "component", None)) or last_domain or ""
    ok, error = manager.update_task_status(task_id, status, domain)
    if ok:
        save_last_task(task_id, domain)
        detail = manager.load_task(task_id, domain)
        payload = {"task": task_to_dict(detail, include_subtasks=True) if detail else {"id": task_id}}
        return structured_response(
            "update",
            status=status,
            message=translate("MSG_STATUS_UPDATED", task_id=task_id),
            payload=payload,
            summary=f"{task_id} → {status}",
        )

    payload = {"task_id": task_id, "domain": domain}
    if error and error.get("code") == "not_found":
        return structured_error("update", error.get("message", translate("ERR_TASK_NOT_FOUND", task_id=task_id)), payload=payload)
    return structured_response(
        "update",
        status="ERROR",
        message=(error or {}).get("message", translate("ERR_STATUS_NOT_UPDATED")),
        payload=payload,
        exit_code=1,
    )


def cmd_status_set(args) -> int:
    """Единообразная установка статуса (OK/WARN/FAIL) — терминология TUI=CLI."""
    manager = TaskManager()
    status = args.status.upper()
    ok, error = manager.update_task_status(normalize_task_id(args.task_id), status, args.domain or "")
    if ok:
        detail = manager.load_task(normalize_task_id(args.task_id), args.domain or "")
        payload = {"task": task_to_dict(detail, include_subtasks=True) if detail else {"id": normalize_task_id(args.task_id)}}
        return structured_response(
            "status-set",
            status="OK",
            message=f"{normalize_task_id(args.task_id)} → {status}",
            payload=payload,
            summary=f"{normalize_task_id(args.task_id)} → {status}",
        )
    payload = {"task_id": args.task_id, "domain": args.domain or "", "status": status}
    return structured_response(
        "status-set",
        status="ERROR",
        message=(error or {}).get("message", "Статус не обновлён"),
        payload=payload,
        exit_code=1,
    )


def cmd_analyze(args) -> int:
    return _cmd_analyze(args, CLI_DEPS)


def cmd_next(args) -> int:
    manager = TaskManager()
    domain = derive_domain_explicit(getattr(args, "domain", ""), getattr(args, "phase", None), getattr(args, "component", None))
    filters = {
        "domain": domain or "",
        "phase": getattr(args, "phase", None) or "",
        "component": getattr(args, "component", None) or "",
    }
    tasks = manager.list_tasks(domain, skip_sync=True)
    payload, selected = next_recommendations(tasks, filters, remember=save_last_task, serializer=task_to_dict)
    filter_hint = f" (domain='{filters['domain'] or '-'}', phase='{filters['phase'] or '-'}', component='{filters['component'] or '-'}')"
    if not payload["candidates"]:
        return structured_response(
            "next",
            status="OK",
            message="Все задачи завершены" + filter_hint,
            payload=payload,
            summary="Нет незавершённых задач",
        )
    primary = selected or tasks[0]
    return structured_response(
        "next",
        status="OK",
        message="Рекомендации обновлены" + filter_hint,
        payload=payload,
        summary=f"Выбрано {primary.id}",
    )


def _parse_semicolon_list(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(";") if item.strip()]


def cmd_add_subtask(args) -> int:
    manager = TaskManager()
    domain = derive_domain_explicit(getattr(args, "domain", ""), getattr(args, "phase", None), getattr(args, "component", None))
    task_id = normalize_task_id(args.task_id)
    criteria = _parse_semicolon_list(args.criteria)
    tests = _parse_semicolon_list(args.tests)
    blockers = _parse_semicolon_list(args.blockers)
    if not args.subtask or len(args.subtask.strip()) < 20:
        return structured_error("add-subtask", translate("ERR_SUBTASK_TITLE_MIN"))
    ok, err = manager.add_subtask(task_id, args.subtask.strip(), domain, criteria, tests, blockers)
    if ok:
        payload = {"task_id": task_id, "subtask": args.subtask.strip()}
        return structured_response(
            "add-subtask",
            status="OK",
            message=f"Подзадача добавлена в {task_id}",
            payload=payload,
            summary=f"{task_id} +subtask",
        )
    if err == "missing_fields":
        return structured_error(
            "add-subtask",
            "Добавь критерии/тесты/блокеры: --criteria \"...\" --tests \"...\" --blockers \"...\" (через ';')",
            payload={"task_id": task_id},
        )
    return structured_error("add-subtask", f"Задача {task_id} не найдена", payload={"task_id": task_id})


def cmd_add_dependency(args) -> int:
    manager = TaskManager()
    domain = derive_domain_explicit(getattr(args, "domain", ""), getattr(args, "phase", None), getattr(args, "component", None))
    task_id = normalize_task_id(args.task_id)
    if manager.add_dependency(task_id, args.dependency, domain):
        payload = {"task_id": task_id, "dependency": args.dependency}
        return structured_response(
            "add-dep",
            status="OK",
            message=f"Зависимость добавлена в {task_id}",
            payload=payload,
            summary=f"{task_id} +dep",
        )
    return structured_error("add-dep", f"Задача {task_id} не найдена", payload={"task_id": task_id})


def cmd_subtask(args) -> int:
    return _cmd_subtask(args)


def cmd_ok(args) -> int:
    manager = TaskManager()
    try:
        task_id, domain = resolve_task_reference(
            getattr(args, "task_id", None),
            getattr(args, "domain", None),
            getattr(args, "phase", None),
            getattr(args, "component", None),
        )
    except ValueError as exc:
        return structured_error("ok", str(exc))
    index = args.index
    checkpoints = [
        ("criteria", args.criteria_note, "criteria_done"),
        ("tests", args.tests_note, "tests_done"),
        ("blockers", args.blockers_note, "blockers_done"),
    ]
    for checkpoint, note, action in checkpoints:
        ok, msg = manager.update_subtask_checkpoint(task_id, index, checkpoint, True, note or "", domain)
        if not ok:
            payload = {"task_id": task_id, "checkpoint": checkpoint, "index": index}
            if msg == "not_found":
                return structured_error("ok", f"Задача {task_id} не найдена", payload=payload)
            if msg == "index":
                return structured_error("ok", translate("ERR_SUBTASK_INDEX"), payload=payload)
            return structured_error("ok", msg or "Не удалось подтвердить чекпоинт", payload=payload)
    ok, msg = manager.set_subtask(task_id, index, True, domain)
    if not ok:
        payload = {"task_id": task_id, "index": index}
        return structured_error("ok", msg or "Не удалось завершить подзадачу", payload=payload)
    detail = manager.load_task(task_id, domain)
    save_last_task(task_id, domain)
    payload = {
        "task": task_to_dict(detail, include_subtasks=True) if detail else {"id": task_id},
        "subtask_index": index,
    }
    if detail and 0 <= index < len(detail.subtasks):
        payload["subtask"] = subtask_to_dict(detail.subtasks[index])
    return structured_response(
        "ok",
        status="OK",
        message=f"Подзадача {index} полностью подтверждена и закрыта",
        payload=payload,
        summary=f"{task_id} subtask#{index} OK",
    )


def cmd_note(args) -> int:
    manager = TaskManager()
    try:
        task_id, domain = resolve_task_reference(
            getattr(args, "task_id", None),
            getattr(args, "domain", None),
            getattr(args, "phase", None),
            getattr(args, "component", None),
        )
    except ValueError as exc:
        return structured_error("note", str(exc))
    value = not args.undo
    ok, msg = manager.update_subtask_checkpoint(task_id, args.index, args.checkpoint, value, args.note or "", domain)
    if ok:
        detail = manager.load_task(task_id, domain)
        save_last_task(task_id, domain)
        payload = {
            "task": task_to_dict(detail, include_subtasks=True) if detail else {"id": task_id},
            "checkpoint": args.checkpoint,
            "index": args.index,
            "state": "DONE" if value else "TODO",
        }
        if detail and 0 <= args.index < len(detail.subtasks):
            payload["subtask"] = subtask_to_dict(detail.subtasks[args.index])
        return structured_response(
            "note",
            status="OK",
            message=f"{args.checkpoint.capitalize()} {'подтверждены' if value else 'сброшены'}",
            payload=payload,
            summary=f"{task_id} {args.checkpoint} idx {args.index}",
        )
    payload = {"task_id": task_id, "checkpoint": args.checkpoint, "index": args.index}
    if msg == "not_found":
        return structured_error("note", f"Задача {task_id} не найдена", payload=payload)
    if msg == "index":
        return structured_error("note", translate("ERR_SUBTASK_INDEX"), payload=payload)
    return structured_error("note", msg or "Операция не выполнена", payload=payload)


def cmd_bulk(args) -> int:
    return _cmd_bulk(args)


def cmd_checkpoint(args) -> int:
    return _cmd_checkpoint(args)


def cmd_move(args) -> int:
    """Переместить задачу в другую подпапку .tasks"""
    manager = TaskManager()
    if args.glob:
        count = manager.move_glob(args.glob, args.to)
        payload = {"glob": args.glob, "target": args.to, "moved": count}
        return structured_response(
            "move",
            status="OK",
            message=f"Перемещено задач: {count} в {args.to}",
            payload=payload,
            summary=f"{count} задач → {args.to}",
        )
    if not args.task_id:
        return structured_error("move", translate("ERR_TASK_ID_OR_GLOB"))
    task_id = normalize_task_id(args.task_id)
    if manager.move_task(task_id, args.to):
        save_last_task(task_id, args.to)
        payload = {"task_id": task_id, "target": args.to}
        return structured_response(
            "move",
            status="OK",
            message=f"{task_id} перемещена в {args.to}",
            payload=payload,
            summary=f"{task_id} → {args.to}",
        )
    return structured_error("move", f"Не удалось переместить {task_id}", payload={"task_id": task_id, "target": args.to})


def cmd_clean(args) -> int:
    if not any([args.tag, args.status, args.phase, args.glob]):
        return structured_error("clean", translate("ERR_FILTER_REQUIRED"))
    manager = TaskManager()
    if args.glob:
        is_dry = args.dry_run
        base = manager.tasks_dir.resolve()
        matched = []
        for detail in manager.repo.list("", skip_sync=True):
            try:
                rel = Path(detail.filepath).resolve().relative_to(base)
            except Exception:
                continue
            if rel.match(args.glob):
                matched.append(detail.id)
        if is_dry:
            payload = {"mode": "dry-run", "matched": matched, "glob": args.glob}
            return structured_response(
                "clean",
                status="OK",
                message=f"Будут удалены {len(matched)} задач(и) по glob",
                payload=payload,
                summary=f"dry-run {len(matched)} задач",
            )
        removed = manager.repo.delete_glob(args.glob)
        payload = {"removed": removed, "matched": matched, "glob": args.glob}
        return structured_response(
            "clean",
            status="OK",
            message=f"Удалено задач: {removed} по glob {args.glob}",
            payload=payload,
            summary=f"Удалено {removed}",
        )
    matched, removed = manager.clean_tasks(tag=args.tag, status=args.status, phase=args.phase, dry_run=args.dry_run)
    if args.dry_run:
        payload = {
            "mode": "dry-run",
            "matched": matched,
            "filters": {"tag": args.tag, "status": args.status, "phase": args.phase},
        }
        return structured_response(
            "clean",
            status="OK",
            message=f"Будут удалены {len(matched)} задач(и)",
            payload=payload,
            summary=f"dry-run {len(matched)} задач",
        )
    payload = {
        "removed": removed,
        "matched": matched,
        "filters": {"tag": args.tag, "status": args.status, "phase": args.phase},
    }
    return structured_response(
        "clean",
        status="OK",
        message=f"Удалено задач: {removed}",
        payload=payload,
        summary=f"Удалено {removed}",
    )


def cmd_projects_auth(args) -> int:
    if args.unset:
        set_user_token("")
        _invalidate_projects_status_cache()
        return structured_response(
            "projects-auth",
            status="OK",
            message="PAT cleared",
            payload={"token": None},
        )
    if not args.token:
        return structured_error("projects-auth", translate("ERR_TOKEN_OR_UNSET"))
    set_user_token(args.token)
    _invalidate_projects_status_cache()
    return structured_response(
        "projects-auth",
        status="OK",
        message="PAT saved",
        payload={"token": "***"},
    )


def cmd_projects_webhook(args) -> int:
    sync_service = _get_sync_service()
    if not sync_service.enabled:
        return structured_error("projects-webhook", "Projects sync disabled (missing token or config)")
    body = _load_input_source(args.payload, "--payload")
    try:
        result = sync_service.handle_webhook(body, args.signature, args.secret)
    except ValueError as exc:
        return structured_error("projects-webhook", str(exc))
    if result and result.get("conflict"):
        return structured_response(
            "projects-webhook",
            status="CONFLICT",
            message="Конфликт: локальные правки новее удалённых",
            payload=result,
        )
    updated = bool(result and result.get("updated"))
    message = "Task updated" if updated else "No matching task"
    return structured_response(
        "projects-webhook",
        status="OK",
        message=message,
        payload=result or {"updated": False},
    )


def cmd_projects_webhook_serve(args) -> int:
    sync_service = _get_sync_service()
    if not sync_service.enabled:
        return structured_error("projects-webhook-serve", "Projects sync disabled (missing token or config)")

    secret = args.secret

    class Handler(BaseHTTPRequestHandler):  # pragma: no cover - network entrypoint
        def do_POST(self_inner):
            length = int(self_inner.headers.get("Content-Length", "0"))
            raw = self_inner.rfile.read(length)
            signature = self_inner.headers.get("X-Hub-Signature-256")
            try:
                result = sync_service.handle_webhook(raw.decode(), signature, secret)
                if result and result.get("conflict"):
                    status = 409
                    payload = {"status": "conflict", **result}
                else:
                    status = 200
                    payload = result or {"updated": False}
            except ValueError as exc:
                status = 400
                payload = {"error": str(exc)}
            except Exception as exc:  # pragma: no cover
                status = 500
                payload = {"error": str(exc)}
            self_inner.send_response(status)
            self_inner.send_header("Content-Type", "application/json")
            self_inner.end_headers()
            self_inner.wfile.write(json.dumps(payload).encode())

        def log_message(self_inner, format, *args):
            return

    server = HTTPServer((args.host, args.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
    return 0


def cmd_projects_sync_cli(args) -> int:
    if not args.all:
        return structured_error("projects sync", "Укажи --all для явного подтверждения")
    sync_service = _get_sync_service()
    if not sync_service.enabled:
        status = _projects_status_payload()
        reason = status.get("status_reason") or "Projects sync отключён или не настроен"
        return structured_error("projects sync", reason)
    sync_service.consume_conflicts()
    manager = TaskManager()
    domain = derive_domain_explicit(getattr(args, "domain", ""), getattr(args, "phase", None), getattr(args, "component", None))
    tasks = manager.list_tasks(domain)
    pulled = pushed = 0
    for task in tasks:
        try:
            sync_service.pull_task_fields(task)
            pulled += 1
        except Exception:
            pass
        if sync_service.sync_task(task):
            pushed += 1
    conflicts = sync_service.consume_conflicts()
    payload = {
        "tasks": len(tasks),
        "pull_updates": pulled,
        "push_updates": pushed,
        "conflicts": conflicts,
    }
    conflict_suffix = f", конфликты={len(conflicts)}" if conflicts else ""
    projects_status_cache.invalidate_cache()
    return structured_response(
        "projects sync",
        status="OK",
        message=f"Синхронизация завершена ({pulled} pull / {pushed} push{conflict_suffix})",
        payload=payload,
        summary=f"{pulled} pull / {pushed} push{conflict_suffix}",
    )


def _projects_status_payload(force_refresh: bool = False) -> Dict[str, Any]:
    return projects_status_cache.projects_status_payload(_get_sync_service, force_refresh=force_refresh)


def cmd_projects_status(args) -> int:
    payload = _projects_status_payload(force_refresh=True)
    fragments = sync_status_fragments(payload, payload["runtime_enabled"], flash=False, filter_flash=False)
    message = " ".join(text for _, text in fragments)
    return structured_response(
        "projects status",
        status="OK",
        message=message,
        payload=payload,
        summary=payload["target_label"],
    )


def _invalidate_projects_status_cache() -> None:
    projects_status_cache.invalidate_cache()


def cmd_projects_autosync(args) -> int:
    desired = args.state.lower() == "on"
    update_projects_enabled(desired)
    reload_projects_sync()
    state_label = "включён" if desired else "выключен"
    payload = {"auto_sync": desired}
    _invalidate_projects_status_cache()
    return structured_response(
        "projects autosync",
        status="OK",
        message=f"Auto-sync {state_label}",
        payload=payload,
        summary=f"auto-sync {args.state}",
    )


def cmd_projects_workers(args) -> int:
    target = None if args.count == 0 else args.count
    update_project_workers(target)
    reload_projects_sync()
    label = "auto" if target is None else str(target)
    payload = {"workers": target}
    _invalidate_projects_status_cache()
    return structured_response(
        "projects workers",
        status="OK",
        message=f"Пул синхронизации установлен: {label}",
        payload=payload,
        summary=label,
    )


def cmd_edit(args) -> int:
    manager = TaskManager()
    domain = derive_domain_explicit(getattr(args, "domain", ""), getattr(args, "phase", None), getattr(args, "component", None))
    task = manager.load_task(normalize_task_id(args.task_id), domain)
    if not task:
        return structured_error("edit", f"Задача {args.task_id} не найдена")
    if args.description:
        task.description = args.description
    if args.context:
        task.context = args.context
    if args.tags:
        task.tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    if args.priority:
        task.priority = args.priority
    if args.phase:
        task.phase = args.phase
    if args.component:
        task.component = args.component
    if args.new_domain:
        task.domain = args.new_domain
    manager.save_task(task)
    payload = {"task": task_to_dict(task, include_subtasks=True)}
    return structured_response(
        "edit",
        status="OK",
        message=f"Задача {task.id} обновлена",
        payload=payload,
        summary=f"{task.id} updated",
    )


def cmd_lint(args) -> int:
    issues: List[str] = []
    tasks_dir = Path(".tasks")
    if not tasks_dir.exists():
        issues.append(".tasks каталог отсутствует")
    else:
        manager = TaskManager()
        for f in tasks_dir.rglob("TASK-*.task"):
            detail = TaskFileParser.parse(f)
            if not detail:
                issues.append(f"{f} не парсится")
                continue
            changed = False
            if not detail.description:
                issues.append(f"{f} без description")
            if not detail.success_criteria:
                issues.append(f"{f} без tests/success_criteria")
            if not detail.parent:
                detail.parent = detail.id
                changed = True
            if args.fix and changed:
                manager.save_task(detail)
    payload = {"issues": issues, "fix": bool(args.fix)}
    if issues:
        return structured_response(
            "lint",
            status="ERROR",
            message=f"Найдено {len(issues)} проблем(ы)",
            payload=payload,
            summary="Lint failed",
            exit_code=1,
        )
    return structured_response(
        "lint",
        status="OK",
        message="Lint OK",
        payload=payload,
        summary="Lint clean",
    )


def cmd_suggest(args) -> int:
    manager = TaskManager()
    folder = getattr(args, "folder", "") or ""
    domain = derive_domain_explicit(getattr(args, "domain", "") or folder, getattr(args, "phase", None), getattr(args, "component", None))
    filters = {
        "folder": folder or "",
        "domain": domain or "",
        "phase": getattr(args, "phase", None) or "",
        "component": getattr(args, "component", None) or "",
    }
    tasks = manager.list_tasks(domain, skip_sync=True)
    payload, _ranked = suggest_tasks(tasks, filters, remember=save_last_task, serializer=task_to_dict)
    filter_hint = f" (folder='{folder or domain or '-'}', phase='{filters['phase'] or '-'}', component='{filters['component'] or '-'}')"
    if not payload["suggestions"]:
        return structured_response(
            "suggest",
            status="OK",
            message="Все задачи завершены" + filter_hint,
            payload=payload,
            summary="Нет задач для рекомендации",
        )
    return structured_response(
        "suggest",
        status="OK",
        message="Рекомендации сформированы" + filter_hint,
        payload=payload,
        summary=f"{len(payload['suggestions'])} рекомендаций",
    )


def cmd_quick(args) -> int:
    """Быстрый обзор: топ-3 незавершённых задачи."""
    manager = TaskManager()
    folder = getattr(args, "folder", "") or ""
    domain = derive_domain_explicit(getattr(args, "domain", "") or folder, getattr(args, "phase", None), getattr(args, "component", None))
    filters = {
        "folder": folder or "",
        "domain": domain or "",
        "phase": getattr(args, "phase", None) or "",
        "component": getattr(args, "component", None) or "",
    }
    tasks = manager.list_tasks(domain, skip_sync=True)
    payload, top = quick_overview(tasks, filters, remember=save_last_task, serializer=task_to_dict)
    filter_hint = f" (folder='{folder or domain or '-'}', phase='{filters['phase'] or '-'}', component='{filters['component'] or '-'}')"
    if not payload["top"]:
        return structured_response(
            "quick",
            status="OK",
            message="Все задачи выполнены" + filter_hint,
            payload=payload,
            summary="Нет задач",
        )
    return structured_response(
        "quick",
        status="OK",
        message="Быстрый обзор top-3" + filter_hint,
        payload=payload,
        summary=f"Top-{len(top)} задач",
    )


def _template_subtask_entry(idx: int) -> Dict[str, Any]:
    return {
        "title": f"Результат {idx}: опиши измеримый итог (≥20 символов)",
        "criteria": [
            "Метрики успеха определены и зафиксированы",
            "Доказательства приёмки описаны",
            "Обновлены мониторинг/алерты",
        ],
        "tests": [
            "pytest -q tests/... -k <кейc>",
            "perf или интеграционный прогон",
        ],
        "blockers": [
            "Перечисли approvals/зависимости",
            "Опиши риски и план снятия блокеров",
        ],
    }


def _template_test_matrix() -> List[Dict[str, str]]:
    return [
        {
            "name": "Юнит + интеграция ≥85%",
            "command": "pytest -q --maxfail=1 --cov=src --cov-report=xml",
            "evidence": "coverage.xml ≥85%, отчёт приложен в задачу",
        },
        {
            "name": "Конфигурационный/перф",
            "command": "pytest -q tests/perf -k scenario && python scripts/latency_audit.py",
            "evidence": "p95 ≤ целевой SLO, лог проверки загружен в репозиторий",
        },
        {
            "name": "Регресс + ручная приёмка",
            "command": "pytest -q tests/e2e && ./scripts/manual-checklist.md",
            "evidence": "Чеклист приёмки с таймстемпом и ссылкой на демо",
        },
    ]


def _template_docs_matrix() -> List[Dict[str, str]]:
    return [
        {
            "artifact": "ADR",
            "path": "docs/adr/ADR-<номер>.md",
            "goal": "Зафиксировать выбранную архитектуру и компромиссы hexagonal monolith.",
        },
        {
            "artifact": "Runbook/операционный гайд",
            "path": "docs/runbooks/<feature>.md",
            "goal": "Описать фич-срез, команды запуска и алерты.",
        },
        {
            "artifact": "Changelog/RELNOTES",
            "path": "docs/releases/<date>-<feature>.md",
            "goal": "Протоколировать влияние на пользователей, метрики и тесты.",
        },
    ]


def cmd_template_subtasks(args) -> int:
    count = max(3, args.count)
    template = [_template_subtask_entry(i + 1) for i in range(count)]
    payload = {
        "type": "subtasks",
        "count": count,
        "template": template,
        "tests_template": _template_test_matrix(),
        "documentation_template": _template_docs_matrix(),
        "usage": "apply_task ... --subtasks 'JSON' | --subtasks @file | --subtasks - (всё на русском)",
    }
    return structured_response(
        "template.subtasks",
        status="OK",
        message="Сгенерирован JSON-шаблон подзадач",
        payload=payload,
        summary=f"{count} шаблонов",
    )


# ============================================================================
# Devtools automation helpers
# ============================================================================

AUTOMATION_TMP = Path(".tmp")


def _ensure_tmp_dir() -> Path:
    AUTOMATION_TMP.mkdir(parents=True, exist_ok=True)
    return AUTOMATION_TMP


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _automation_subtask_entry(index: int, coverage: int, risks: str, sla: str) -> Dict[str, Any]:
    return {
        "title": f"Subtask {index}: plan and validate",
        "criteria": [
            f"Coverage ≥{coverage}%",
            f"SLA {sla}",
            "Risks enumerated and mitigations defined",
        ],
        "tests": [
            f"pytest -q --maxfail=1 --cov=. --cov-report=xml (target ≥{coverage}%)",
            "perf/regression suite with evidence in logs",
        ],
        "blockers": [
            "Dependencies and approvals recorded",
            f"Risks: {risks}",
        ],
    }


def _automation_template_payload(count: int, coverage: int, risks: str, sla: str) -> Dict[str, Any]:
    count = max(3, count)
    subtasks = [_automation_subtask_entry(i + 1, coverage, risks, sla) for i in range(count)]
    return {
        "defaults": {"coverage": coverage, "risks": risks, "sla": sla},
        "usage": "apply_task automation task-create \"Title\" --parent TASK-XXX --description \"...\" --subtasks @.tmp/subtasks.template.json",
        "subtasks": subtasks,
    }


def cmd_automation_task_template(args) -> int:
    payload = _automation_template_payload(args.count, args.coverage, args.risks, args.sla)
    output_path = Path(args.output or (AUTOMATION_TMP / "subtasks.template.json"))
    _ensure_tmp_dir()
    _write_json(output_path, payload)
    return structured_response(
        "automation.task-template",
        status="OK",
        message=f"Шаблон сохранён: {output_path}",
        payload={"output": str(output_path.resolve()), "count": len(payload["subtasks"]), "defaults": payload["defaults"]},
        summary=str(output_path),
    )


def _resolve_parent(default_parent: Optional[str]) -> Optional[str]:
    if default_parent:
        return normalize_task_id(default_parent)
    last_id, _ = get_last_task()
    return normalize_task_id(last_id) if last_id else None


def _load_note(log_path: Path, fallback: str) -> str:
    if log_path.exists():
        text = log_path.read_text(encoding="utf-8").strip()
        if text:
            return text[:1000]
    return fallback


def cmd_automation_task_create(args) -> int:
    parent = _resolve_parent(args.parent)
    if not parent:
        return structured_error("automation.task-create", "Не найден parent: укажи --parent или установи .last")
    domain = derive_domain_explicit(getattr(args, "domain", ""), getattr(args, "phase", None), getattr(args, "component", None))
    subtasks_source = args.subtasks or str(AUTOMATION_TMP / "subtasks.template.json")
    subtasks_path = Path(subtasks_source[1:]) if subtasks_source.startswith("@") else Path(subtasks_source)
    if subtasks_source.startswith("@") or subtasks_path.exists():
        if not subtasks_path.exists():
            # автоформирование дефолтного шаблона
            payload = _automation_template_payload(args.count, args.coverage, args.risks, args.sla)
            _ensure_tmp_dir()
            _write_json(subtasks_path, payload)
        resolved_path = subtasks_path
        try:
            payload = json.loads(subtasks_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and isinstance(payload.get("subtasks"), list):
                _ensure_tmp_dir()
                resolved_path = subtasks_path.parent / "subtasks.resolved.json" if subtasks_path.is_file() else (AUTOMATION_TMP / "subtasks.resolved.json")
                _write_json(resolved_path, payload["subtasks"])
        except Exception:
            resolved_path = subtasks_path
        subtasks_arg = f"@{resolved_path}"
    else:
        subtasks_arg = subtasks_source
    desc = args.description or args.title
    create_args = argparse.Namespace(
        title=args.title,
        status=args.status,
        priority=args.priority,
        parent=parent,
        description=desc,
        context=args.context,
        tags=args.tags,
        subtasks=subtasks_arg,
        dependencies=None,
        next_steps=None,
        tests=args.tests,
        risks=args.risks,
        validate_only=not args.apply,
        domain=domain,
        phase=args.phase,
        component=args.component,
    )
    return cmd_create(create_args)


def cmd_automation_projects_health(args) -> int:
    payload = _projects_status_payload(force_refresh=True)
    summary = f"target={payload.get('target_label','—')} auto-sync={str(payload.get('auto_sync')).lower()} token={'yes' if payload.get('token_present') else 'no'} rate={payload.get('rate_remaining')}/{payload.get('rate_reset_human') or '-'}"
    return structured_response(
        "automation.projects-health",
        status="OK",
        message=payload.get("status_reason", "") or "Projects status",
        payload=payload,
        summary=summary,
    )


def cmd_automation_health(args) -> int:
    _ensure_tmp_dir()
    log_path = Path(args.log or (AUTOMATION_TMP / "health.log"))
    pytest_cmd = args.pytest_cmd.strip()
    result = {"pytest_cmd": pytest_cmd, "rc": 0, "stdout": "", "stderr": ""}
    if pytest_cmd:
        try:
            proc = subprocess.run(shlex.split(pytest_cmd), capture_output=True, text=True)
            result["rc"] = proc.returncode
            result["stdout"] = (proc.stdout or "").strip()
            result["stderr"] = (proc.stderr or "").strip()
        except FileNotFoundError as exc:
            result["rc"] = 1
            result["stderr"] = str(exc)
    _write_json(log_path, result)
    status = "OK" if result["rc"] == 0 else "ERROR"
    return structured_response(
        "automation.health",
        status=status,
        message="pytest выполнен" if pytest_cmd else "pytest пропущен",
        payload={"log": str(log_path.resolve()), **result},
        summary=f"log={log_path} rc={result['rc']}",
        exit_code=0 if status == "OK" else 1,
    )


def cmd_automation_checkpoint(args) -> int:
    try:
        task_id, domain = resolve_task_reference(args.task_id, getattr(args, "domain", None), getattr(args, "phase", None), getattr(args, "component", None))
    except ValueError as exc:
        return structured_error("automation.checkpoint", str(exc))
    manager = TaskManager()
    log_path = Path(args.log or (AUTOMATION_TMP / "checkpoint.log"))
    note = args.note or _load_note(log_path, f"log missing: {log_path}")
    payload: Dict[str, Any] = {"task_id": task_id, "index": args.index, "note": note}
    if args.mode == "note":
        ok, msg = manager.update_subtask_checkpoint(task_id, args.index, args.checkpoint, True, note, domain)
        if not ok:
            return structured_error("automation.checkpoint", msg or "Не удалось записать чекпоинт", payload=payload)
        detail = manager.load_task(task_id, domain)
        if detail:
            payload["task"] = task_to_dict(detail, include_subtasks=True)
        return structured_response(
            "automation.checkpoint.note",
            status="OK",
            message=f"Checkpoint {args.checkpoint} обновлён",
            payload=payload,
            summary=f"{task_id}#{args.index} {args.checkpoint}",
        )

    for checkpoint in ("criteria", "tests", "blockers"):
        ok, msg = manager.update_subtask_checkpoint(task_id, args.index, checkpoint, True, note, domain)
        if not ok:
            return structured_error("automation.checkpoint", msg or "Не удалось подтвердить чекпоинты", payload=payload)
    ok, msg = manager.set_subtask(task_id, args.index, True, domain)
    if not ok:
        return structured_error("automation.checkpoint", msg or "Не удалось закрыть подзадачу", payload=payload)
    detail = manager.load_task(task_id, domain)
    save_last_task(task_id, domain)
    if detail:
        payload["task"] = task_to_dict(detail, include_subtasks=True)
    return structured_response(
        "automation.checkpoint",
        status="OK",
        message="Подзадача закрыта через automation",
        payload=payload,
        summary=f"{task_id}#{args.index} ok",
    )


# ============================================================================
# CLI
# ============================================================================


def build_parser() -> argparse.ArgumentParser:
    return build_cli_parser(commands=sys.modules[__name__], themes=THEMES, default_theme=DEFAULT_THEME, automation_tmp=AUTOMATION_TMP)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not getattr(args, "command", None):
        parser.print_help()
        return 1
    if args.command == "help":
        parser.print_help()
        print("\nКонтекст: --domain или phase/component формируют путь; .last хранит TASK@domain.")
        print("\nПравила для ИИ-агентов:\n")
        print(AI_HELP.strip())
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
