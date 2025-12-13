#!/usr/bin/env python3
"""MCP (Model Context Protocol) stdio server for task management.

Exposes task management functionality as MCP tools for AI assistants.

Usage:
    python -m core.desktop.devtools.interface.mcp_server

Or via the CLI:
    tasks mcp

Configuration for Claude Desktop (~/.config/claude/claude_desktop_config.json):
    {
      "mcpServers": {
        "tasks": {
          "command": "python",
          "args": ["-m", "core.desktop.devtools.interface.mcp_server"],
          "cwd": "/path/to/apply_task"
        }
      }
    }
"""

from __future__ import annotations

import asyncio
import json
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.desktop.devtools.application.context import derive_domain_explicit, save_last_task
from core.desktop.devtools.application.recommendations import next_recommendations
from core.desktop.devtools.application.task_manager import TaskManager
from core.desktop.devtools.interface.ai_state import (
    UserSignal,
    get_ai_state,
    read_user_signal,
    write_user_signal,
)
from core.desktop.devtools.interface.cli_ai import get_project_tasks_dir, process_intent
from core.desktop.devtools.interface.cli_automation import _automation_template_payload, _ensure_tmp_dir
from core.desktop.devtools.interface.cli_templates import (
    _template_docs_matrix,
    _template_subtask_entry,
    _template_test_matrix,
)
from core.desktop.devtools.interface.projects_integration import _projects_status_payload
from core.desktop.devtools.interface.serializers import task_to_dict
from core.desktop.devtools.interface.tasks_dir_resolver import resolve_project_root
from core.status import task_status_code, task_status_label


# ═══════════════════════════════════════════════════════════════════════════════
# MCP PROTOCOL TYPES
# ═══════════════════════════════════════════════════════════════════════════════

MCP_VERSION = "2024-11-05"
SERVER_NAME = "tasks-mcp"
SERVER_VERSION = "1.0.0"

# Global tasks root for namespace routing
GLOBAL_TASKS_ROOT = (Path.home() / ".tasks").resolve()

# Namespace validation (no path separators / traversal)
# Allow dots for legacy namespaces derived from https remotes.
_NAMESPACE_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def sanitize_namespace(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        raise ValueError("namespace is empty")
    if ".." in value or "/" in value or "\\" in value:
        raise ValueError("namespace contains forbidden path characters")
    if not _NAMESPACE_PATTERN.match(value):
        raise ValueError("namespace must match [A-Za-z0-9_-]{1,128}")
    return value


def namespace_dir(namespace: str) -> Path:
    ns = sanitize_namespace(namespace)
    candidate = (GLOBAL_TASKS_ROOT / ns).resolve()
    if not candidate.is_relative_to(GLOBAL_TASKS_ROOT):
        raise ValueError("namespace escapes global tasks root")
    return candidate


@dataclass
class JsonRpcRequest:
    """JSON-RPC 2.0 request."""
    jsonrpc: str
    method: str
    id: Optional[int | str] = None
    params: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict) -> "JsonRpcRequest":
        return cls(
            jsonrpc=data.get("jsonrpc", "2.0"),
            method=data["method"],
            id=data.get("id"),
            params=data.get("params", {}),
        )


def json_rpc_response(id: Optional[int | str], result: Any) -> Dict:
    """Create JSON-RPC success response."""
    return {"jsonrpc": "2.0", "id": id, "result": result}


def json_rpc_error(id: Optional[int | str], code: int, message: str, data: Any = None) -> Dict:
    """Create JSON-RPC error response."""
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": id, "error": error}


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════════════

def get_tool_definitions() -> List[Dict[str, Any]]:
    """Generate MCP tool definitions from intent handlers."""
    tools = []

    # Context - get current state
    tools.append({
        "name": "tasks_context",
        "description": "Get full context: all tasks, current task state, progress. Use this first to understand the situation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Task ID to focus on (optional)"
                },
                "include_all": {
                    "type": "boolean",
                    "description": "Include all tasks list",
                    "default": False
                },
                "domain": {"type": "string", "description": "Domain filter", "default": ""},
                "phase": {"type": "string", "description": "Phase filter", "default": ""},
                "component": {"type": "string", "description": "Component filter", "default": ""}
            }
        }
    })

    # Create - create new task
    tools.append({
        "name": "tasks_create",
        "description": "Create a new task with optional subtasks.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Task title (required)"
                },
                "description": {
                    "type": "string",
                    "description": "Task description"
                },
                "context": {
                    "type": "string",
                    "description": "Optional extra context for the task",
                    "default": ""
                },
                "priority": {
                    "type": "string",
                    "enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
                    "default": "MEDIUM"
                },
                "parent": {
                    "type": "string",
                    "description": "Parent task ID (optional)",
                    "default": "ROOT"
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Task tags (optional)"
                },
                "domain": {"type": "string", "description": "Domain for the task", "default": ""},
                "phase": {"type": "string", "description": "Phase tag", "default": ""},
                "component": {"type": "string", "description": "Component tag", "default": ""},
                "namespace": {"type": "string", "description": "Storage namespace (cross-namespace create)", "default": ""},
                "subtasks": {
                    "type": "array",
                    "description": "Initial subtasks to create",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "criteria": {"type": "array", "items": {"type": "string"}},
                            "tests": {"type": "array", "items": {"type": "string"}},
                            "blockers": {"type": "array", "items": {"type": "string"}}
                        },
                        "required": ["title"]
                    }
                },
                "idempotency_key": {
                    "type": "string",
                    "description": "Unique key to prevent duplicate creation"
                }
            },
            "required": ["title"]
        }
    })

    # Decompose - add subtasks
    tools.append({
        "name": "tasks_decompose",
        "description": "Break down a task into subtasks with criteria, tests, and blockers.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Task ID to decompose"
                },
                "subtasks": {
                    "type": "array",
                    "description": "Subtasks to add",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string", "description": "Subtask title"},
                            "criteria": {"type": "array", "items": {"type": "string"}, "description": "Success criteria"},
                            "tests": {"type": "array", "items": {"type": "string"}, "description": "Tests to run"},
                            "blockers": {"type": "array", "items": {"type": "string"}, "description": "Blockers"}
                        },
                        "required": ["title"]
                    }
                },
                "parent": {
                    "type": "string",
                    "description": "Parent subtask path for nesting (e.g., '0' or '0.1')"
                },
                "domain": {
                    "type": "string",
                    "description": "Domain for the task",
                    "default": ""
                }
            },
            "required": ["task", "subtasks"]
        }
    })

    # Define - set criteria/tests/blockers
    tools.append({
        "name": "tasks_define",
        "description": "Define or update criteria, tests, or blockers for a subtask.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task ID"},
                "path": {"type": "string", "description": "Subtask path (e.g., '0' or '0.1')"},
                "domain": {"type": "string", "description": "Domain for the task", "default": ""},
                "title": {"type": "string", "description": "Optional new subtask title"},
                "criteria": {"type": "array", "items": {"type": "string"}, "description": "Success criteria"},
                "tests": {"type": "array", "items": {"type": "string"}, "description": "Tests"},
                "blockers": {"type": "array", "items": {"type": "string"}, "description": "Blockers"}
            },
            "required": ["task", "path"]
        }
    })

    # Verify - confirm checkpoints
    tools.append({
        "name": "tasks_verify",
        "description": "Verify that criteria, tests, or blockers are satisfied.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task ID"},
                "path": {"type": "string", "description": "Subtask path"},
                "domain": {"type": "string", "description": "Domain for the task", "default": ""},
                "checkpoints": {
                    "type": "object",
                    "description": "Checkpoints to verify",
                    "properties": {
                        "criteria": {
                            "type": "object",
                            "properties": {
                                "confirmed": {"type": "boolean"},
                                "note": {"type": "string"}
                            }
                        },
                        "tests": {
                            "type": "object",
                            "properties": {
                                "confirmed": {"type": "boolean"},
                                "note": {"type": "string"}
                            }
                        },
                        "blockers": {
                            "type": "object",
                            "properties": {
                                "confirmed": {"type": "boolean"},
                                "note": {"type": "string"}
                            }
                        }
                    }
                }
            },
            "required": ["task", "path", "checkpoints"]
        }
    })

    # Progress - mark subtask complete/incomplete (legacy)
    tools.append({
        "name": "tasks_progress",
        "description": "Mark a subtask as completed or not completed. NOTE: Consider using tasks_done instead for unified completion.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task ID"},
                "path": {"type": "string", "description": "Subtask path"},
                "domain": {"type": "string", "description": "Domain for the task", "default": ""},
                "completed": {"type": "boolean", "description": "Completion status", "default": True}
            },
            "required": ["task", "path"]
        }
    })

    # Done - unified completion (auto-verify + mark completed)
    tools.append({
        "name": "tasks_done",
        "description": "Unified completion: auto-verify all checkpoints + mark as completed. Replaces 4 calls (verify×3 + progress) with 1.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task ID"},
                "path": {"type": "string", "description": "Subtask path (e.g., '0' or '0.1')"},
                "domain": {"type": "string", "description": "Domain for the task", "default": ""},
                "note": {"type": "string", "description": "Completion note (optional)"},
                "force": {"type": "boolean", "description": "Force completion even if checkpoints not confirmed", "default": False}
            },
            "required": ["task", "path"]
        }
    })

    # Delete - delete task or subtask
    tools.append({
        "name": "tasks_delete",
        "description": "Delete a task or subtask. If path is provided, deletes subtask; otherwise deletes entire task.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task ID"},
                "path": {"type": "string", "description": "Subtask path (optional - if not provided, deletes entire task)"},
                "domain": {"type": "string", "description": "Domain for the task", "default": ""}
            },
            "required": ["task"]
        }
    })

    # Complete - finish task
    tools.append({
        "name": "tasks_complete",
        "description": "Mark the entire task as complete.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task ID"},
                "domain": {"type": "string", "description": "Domain for the task", "default": ""},
                "status": {
                    "type": "string",
                    "enum": ["TODO", "ACTIVE", "DONE"],
                    "default": "DONE"
                }
            },
            "required": ["task"]
        }
    })

    # Batch - multiple operations
    tools.append({
        "name": "tasks_batch",
        "description": "Execute multiple operations atomically. If atomic=true, all changes are rolled back on any failure.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Default task ID for operations"},
                "domain": {"type": "string", "description": "Domain for the task", "default": ""},
                "atomic": {"type": "boolean", "description": "Rollback all on failure", "default": True},
                "operations": {
                    "type": "array",
                    "description": "Operations to execute",
                    "items": {
                        "type": "object",
                        "properties": {
                            "intent": {"type": "string", "enum": ["decompose", "define", "verify", "progress", "complete"]},
                        },
                        "required": ["intent"]
                    }
                }
            },
            "required": ["operations"]
        }
    })

    # Undo - revert last operation
    tools.append({
        "name": "tasks_undo",
        "description": "Undo the last modifying operation.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    })

    # Redo - redo undone operation
    tools.append({
        "name": "tasks_redo",
        "description": "Redo the last undone operation.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    })

    # History - show operation history
    tools.append({
        "name": "tasks_history",
        "description": "Show recent operation history with undo/redo state.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Number of operations to show", "default": 10}
            }
        }
    })

    # Storage - show storage info
    tools.append({
        "name": "tasks_storage",
        "description": "Show storage information: global/local paths, namespaces.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    })

    # AI Status - get AI session state (for TUI sync)
    tools.append({
        "name": "tasks_ai_status",
        "description": "Get AI session state: current operation, plan progress, statistics. Useful for debugging AI behavior.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    })

    # AI Signal - send signal from user to AI
    tools.append({
        "name": "tasks_user_signal",
        "description": "Read pending user signal (pause, stop, skip, message). AI should check this periodically.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    })

    # AI Signal setter - allow user/UI to send signals to AI
    tools.append({
        "name": "tasks_send_signal",
        "description": "Send a user signal to AI (pause/resume/stop/skip/message).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "signal": {
                    "type": "string",
                    "enum": ["pause", "resume", "stop", "skip", "message"],
                    "description": "Signal to send"
                },
                "message": {"type": "string", "description": "Optional message for signal=message", "default": ""},
            },
            "required": ["signal"]
        }
    })

    # AI Plan - set/advance/clear plan for transparency
    tools.append({
        "name": "tasks_plan",
        "description": "Set or update AI execution plan for a task (human-visible).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task ID for the plan"},
                "steps": {"type": "array", "items": {"type": "string"}, "description": "Plan steps (human readable)"},
                "advance": {"type": "boolean", "description": "Advance current plan step", "default": False},
                "clear": {"type": "boolean", "description": "Clear current plan", "default": False},
            }
        }
    })

    # Parity tools with CLI surface
    tools.append({
        "name": "tasks_list",
        "description": "List tasks (domain-aware) similar to CLI `list`.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "Domain filter", "default": ""},
                "phase": {"type": "string", "description": "Phase filter", "default": ""},
                "component": {"type": "string", "description": "Component filter", "default": ""},
                "status": {
                    "type": "string",
                    "enum": ["TODO", "ACTIVE", "DONE"],
                    "description": "Status filter"
                },
                "compact": {"type": "boolean", "description": "Return compact task objects", "default": True},
                "namespace": {"type": "string", "description": "Storage namespace to list from"},
                "all_namespaces": {"type": "boolean", "description": "Aggregate tasks from all namespaces", "default": False},
            }
        }
    })

    tools.append({
        "name": "tasks_show",
        "description": "Show a single task (domain-aware) similar to CLI `show`.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task ID"},
                "domain": {"type": "string", "description": "Domain filter", "default": ""},
                "namespace": {"type": "string", "description": "Storage namespace for cross-namespace lookup", "default": ""},
            },
            "required": ["task"]
        }
    })

    tools.append({
        "name": "tasks_next",
        "description": "Get next recommended task (parity with CLI `next`).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "Domain filter", "default": ""},
                "phase": {"type": "string", "description": "Phase filter", "default": ""},
                "component": {"type": "string", "description": "Component filter", "default": ""}
            }
        }
    })

    # Templates
    tools.append({
        "name": "tasks_template_subtasks",
        "description": "Generate flagship subtasks JSON template (CLI: template subtasks).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "description": "Number of template subtasks (min 3)", "default": 3}
            }
        }
    })

    # Automation devtools
    tools.append({
        "name": "tasks_automation_task_template",
        "description": "Generate automation task template payload (CLI: automation task-template).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "default": 3},
                "coverage": {"type": "integer", "default": 85},
                "risks": {"type": "string", "default": "perf;deps"},
                "sla": {"type": "string", "default": "p95<=200ms"}
            }
        }
    })

    tools.append({
        "name": "tasks_automation_health",
        "description": "Run automation health (pytest) similar to CLI automation health.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pytest_cmd": {"type": "string", "description": "Pytest command to run", "default": ""}
            }
        }
    })

    tools.append({
        "name": "tasks_automation_projects_health",
        "description": "Check GitHub Projects health (CLI: automation projects-health).",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    })

    tools.append({
        "name": "tasks_macro_ok",
        "description": "Macro: confirm criteria/tests/blockers and mark subtask done (CLI: ok).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task ID"},
                "path": {"type": "string", "description": "Subtask path e.g. '0' or '0.1'"},
                "criteria_note": {"type": "string"},
                "tests_note": {"type": "string"},
                "blockers_note": {"type": "string"},
                "force": {"type": "boolean", "default": False},
                "domain": {"type": "string", "default": ""}
            },
            "required": ["task", "path"]
        }
    })

    tools.append({
        "name": "tasks_macro_note",
        "description": "Add or clear checkpoint note (CLI: note).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string"},
                "path": {"type": "string"},
                "checkpoint": {"type": "string", "enum": ["criteria", "tests", "blockers"]},
                "note": {"type": "string"},
                "undo": {"type": "boolean", "default": False},
                "domain": {"type": "string", "default": ""}
            },
            "required": ["task", "path", "checkpoint", "note"]
        }
    })

    tools.append({
        "name": "tasks_macro_bulk",
        "description": "Apply batch checkpoints (CLI: bulk).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Default task for operations (optional)"},
                "operations": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "List of operations, same format as batch intent"
                },
                "atomic": {"type": "boolean", "default": False},
                "domain": {"type": "string", "default": ""}
            },
            "required": ["operations"]
        }
    })

    tools.append({
        "name": "tasks_macro_update",
        "description": "Update task status (CLI: update).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string"},
                "status": {"type": "string", "enum": ["TODO", "ACTIVE", "DONE"], "default": "ACTIVE"},
                "domain": {"type": "string", "default": ""},
                "force": {"type": "boolean", "default": False}
            },
            "required": ["task", "status"]
        }
    })

    tools.append({
        "name": "tasks_macro_suggest",
        "description": "Suggest next tasks (CLI: suggest).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "domain": {"type": "string", "default": ""},
                "phase": {"type": "string", "default": ""},
                "component": {"type": "string", "default": ""}
            }
        }
    })

    tools.append({
        "name": "tasks_macro_quick",
        "description": "Quick suggestion (CLI: quick).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "domain": {"type": "string", "default": ""},
                "phase": {"type": "string", "default": ""},
                "component": {"type": "string", "default": ""}
            }
        }
    })

    # Note - add progress note to subtask
    tools.append({
        "name": "tasks_note",
        "description": "Add a progress note to a subtask without marking it complete.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task ID"},
                "path": {"type": "string", "description": "Subtask path (e.g., '0' or '0.1')"},
                "domain": {"type": "string", "description": "Domain for the task", "default": ""},
                "note": {"type": "string", "description": "Progress note to add"}
            },
            "required": ["task", "path", "note"]
        }
    })

    # Block - block or unblock subtask
    tools.append({
        "name": "tasks_block",
        "description": "Block or unblock a subtask with optional reason.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task ID"},
                "path": {"type": "string", "description": "Subtask path (e.g., '0' or '0.1')"},
                "domain": {"type": "string", "description": "Domain for the task", "default": ""},
                "blocked": {"type": "boolean", "description": "Block status (true=block, false=unblock)", "default": True},
                "reason": {"type": "string", "description": "Reason for blocking (optional)"}
            },
            "required": ["task", "path"]
        }
    })

    return tools


# Tool name -> intent mapping
TOOL_TO_INTENT = {
    "tasks_context": "context",
    "tasks_create": "create",
    "tasks_decompose": "decompose",
    "tasks_define": "define",
    "tasks_verify": "verify",
    "tasks_progress": "progress",
    "tasks_done": "done",  # NEW: unified completion
    "tasks_delete": "delete",  # NEW: delete task/subtask
    "tasks_complete": "complete",
    "tasks_note": "note",
    "tasks_block": "block",
    "tasks_batch": "batch",
    "tasks_undo": "undo",
    "tasks_redo": "redo",
    "tasks_history": "history",
    "tasks_storage": "storage",
    "tasks_plan": "plan",
    # Special tools (handled directly, not via intent)
    "tasks_ai_status": "_ai_status",
    "tasks_user_signal": "_user_signal",
    "tasks_send_signal": "_send_signal",
    "tasks_list": "_list",
    "tasks_show": "_show",
    "tasks_next": "_next",
    "tasks_template_subtasks": "_template_subtasks",
    "tasks_automation_task_template": "_automation_task_template",
    "tasks_automation_health": "_automation_health",
    "tasks_automation_projects_health": "_automation_projects_health",
    "tasks_macro_ok": "_macro_ok",
    "tasks_macro_note": "_macro_note",
    "tasks_macro_bulk": "_macro_bulk",
    "tasks_macro_update": "_macro_update",
    "tasks_macro_suggest": "_macro_suggest",
    "tasks_macro_quick": "_macro_quick",
}


# ═══════════════════════════════════════════════════════════════════════════════
# MCP SERVER
# ═══════════════════════════════════════════════════════════════════════════════

class MCPServer:
    """MCP stdio server for task management."""

    def __init__(self, tasks_dir: Optional[Path] = None, use_global: bool = True):
        if tasks_dir:
            self.tasks_dir = tasks_dir
        else:
            self.tasks_dir = get_project_tasks_dir(resolve_project_root(), use_global=use_global)

        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.manager = TaskManager(tasks_dir=self.tasks_dir)
        self._initialized = False

    @staticmethod
    def _json_content(payload: Any) -> Dict[str, Any]:
        """Return MCP content entry as text (JSON serialized).

        Note: MCP spec only supports 'text', 'image', 'audio', 'resource_link', 'resource'.
        The 'json' type is a non-standard extension not supported by Claude Code.
        """
        return {
            "type": "text",
            "text": json.dumps(payload, ensure_ascii=False, indent=2),
        }

    def handle_request(self, request: JsonRpcRequest) -> Dict:
        """Handle a single JSON-RPC request."""
        method = request.method
        params = request.params

        # Initialize
        if method == "initialize":
            return self._handle_initialize(request.id, params)

        # Check initialized
        if not self._initialized and method != "notifications/initialized":
            return json_rpc_error(request.id, -32002, "Server not initialized")

        # Route methods
        if method == "notifications/initialized":
            self._initialized = True
            return None  # No response for notifications

        elif method == "tools/list":
            return self._handle_tools_list(request.id)

        elif method == "tools/call":
            return self._handle_tools_call(request.id, params)

        elif method == "ping":
            return json_rpc_response(request.id, {})

        else:
            return json_rpc_error(request.id, -32601, f"Method not found: {method}")

    def _handle_initialize(self, id: Optional[int | str], params: Dict) -> Dict:
        """Handle initialize request."""
        return json_rpc_response(id, {
            "protocolVersion": MCP_VERSION,
            "serverInfo": {
                "name": SERVER_NAME,
                "version": SERVER_VERSION,
            },
            "capabilities": {
                "tools": {},
            }
        })

    def _handle_tools_list(self, id: Optional[int | str]) -> Dict:
        """Handle tools/list request."""
        return json_rpc_response(id, {
            "tools": get_tool_definitions()
        })

    def _handle_tools_call(self, id: Optional[int | str], params: Dict) -> Dict:
        """Handle tools/call request."""
        tool_name = params.get("name")
        arguments = params.get("arguments", {})

        if tool_name not in TOOL_TO_INTENT:
            return json_rpc_error(id, -32602, f"Unknown tool: {tool_name}")

        intent = TOOL_TO_INTENT[tool_name]

        # Handle special tools directly
        if intent == "_ai_status":
            return self._handle_ai_status(id)
        elif intent == "_user_signal":
            return self._handle_user_signal(id)
        elif intent == "_send_signal":
            return self._handle_send_signal(id, arguments)
        elif intent == "_list":
            return self._handle_list(id, arguments)
        elif intent == "_show":
            return self._handle_show(id, arguments)
        elif intent == "_next":
            return self._handle_next(id, arguments)
        elif intent == "_template_subtasks":
            return self._handle_template_subtasks(id, arguments)
        elif intent == "_automation_task_template":
            return self._handle_automation_task_template(id, arguments)
        elif intent == "_macro_ok":
            return self._handle_macro_ok(id, arguments)
        elif intent == "_macro_note":
            return self._handle_macro_note(id, arguments)
        elif intent == "_macro_bulk":
            return self._handle_macro_bulk(id, arguments)
        elif intent == "_macro_update":
            return self._handle_macro_update(id, arguments)
        elif intent == "_macro_suggest":
            return self._handle_macro_suggest(id, arguments)
        elif intent == "_macro_quick":
            return self._handle_macro_quick(id, arguments)
        elif intent == "_automation_health":
            return self._handle_automation_health(id, arguments)
        elif intent == "_automation_projects_health":
            return self._handle_automation_projects_health(id, arguments)

        # Build intent request
        intent_data = {"intent": intent, **arguments}

        # Process through cli_ai
        try:
            # Explicit namespace routing (never overload domain)
            manager = self.manager
            ns = (arguments.get("namespace") or "").strip()
            if ns:
                ns_dir = namespace_dir(ns)
                if ns_dir.exists() and ns_dir.is_dir():
                    manager = TaskManager(tasks_dir=ns_dir, auto_sync=False)
                else:
                    return json_rpc_error(id, -32602, f"Namespace not found: {ns}")
                intent_data.pop("namespace", None)

            response = process_intent(manager, intent_data)

            # Format as MCP tool result
            result_content = {
                "success": response.success,
                "result": response.result,
                "context": response.context,
                "suggestions": [s.to_dict() for s in response.suggestions],
            }

            if response.meta:
                result_content["meta"] = response.meta.to_dict()

            if response.error:
                result_content["error"] = response.error.to_dict()

            return json_rpc_response(id, {
                "content": [
                    self._json_content(result_content)
                ],
                "isError": not response.success
            })

        except Exception as e:
            return json_rpc_response(id, {
                "content": [
                    self._json_content({"error": str(e)})
                ],
                "isError": True
            })

    def _handle_ai_status(self, id: Optional[int | str]) -> Dict:
        """Handle tasks_ai_status tool call."""
        ai_state = get_ai_state()
        result = ai_state.to_dict()

        return json_rpc_response(id, {
            "content": [
                self._json_content(result)
            ],
            "isError": False
        })

    def _handle_user_signal(self, id: Optional[int | str]) -> Dict:
        """Handle tasks_user_signal tool call."""
        signal, message = read_user_signal(self.tasks_dir)

        result = {
            "signal": signal.value,
            "message": message,
            "has_signal": signal != UserSignal.NONE,
        }

        # Add hint for what to do with the signal
        if signal == UserSignal.PAUSE:
            result["action"] = "Pause execution, wait for resume"
        elif signal == UserSignal.STOP:
            result["action"] = "Stop current task execution"
        elif signal == UserSignal.SKIP:
            result["action"] = "Skip current subtask and move to next"
        elif signal == UserSignal.MESSAGE:
            result["action"] = f"User message: {message}"

        return json_rpc_response(id, {
            "content": [
                self._json_content(result)
            ],
            "isError": False
        })

    def _handle_send_signal(self, id: Optional[int | str], params: Dict) -> Dict:
        """Handle tasks_send_signal tool call."""
        raw_signal = (params.get("signal") or "").strip().lower()
        message = (params.get("message") or "").strip()
        try:
            signal = UserSignal(raw_signal)
        except Exception:
            return json_rpc_error(id, -32602, f"Unknown signal: {raw_signal}")

        try:
            write_user_signal(signal, message, tasks_dir=self.tasks_dir)
            # Mirror into in-memory AI state for immediate UI visibility
            get_ai_state().send_signal(signal, message)
        except Exception as exc:
            return json_rpc_error(id, -32603, f"Failed to write signal: {exc}")

        result = {"success": True, "signal": signal.value, "message": message}
        return json_rpc_response(id, {"content": [self._json_content(result)], "isError": False})

    def _handle_list(self, id: Optional[int | str], params: Dict) -> Dict:
        domain = params.get("domain", "") or ""
        phase = params.get("phase", "") or ""
        component = params.get("component", "") or ""
        status_filter = (params.get("status") or "").strip()
        ns = (params.get("namespace") or "").strip()
        all_namespaces = bool(params.get("all_namespaces") or params.get("include_all"))

        domain_path = derive_domain_explicit(domain, phase, component)

        def _flatten_subtasks(nodes) -> List[Any]:
            flat: List[Any] = []
            for st in nodes or []:
                flat.append(st)
                flat.extend(_flatten_subtasks(getattr(st, "children", [])))
            return flat

        tasks: List[Any] = []
        if all_namespaces:
            if GLOBAL_TASKS_ROOT.exists():
                for ns_dir in GLOBAL_TASKS_ROOT.iterdir():
                    if not ns_dir.is_dir() or ns_dir.name.startswith("."):
                        continue
                    try:
                        ns_manager = TaskManager(tasks_dir=ns_dir, auto_sync=False)
                        ns_tasks = ns_manager.list_tasks(domain_path, skip_sync=True)
                        for t in ns_tasks:
                            t._namespace = ns_dir.name
                        tasks.extend(ns_tasks)
                    except Exception:
                        continue
        else:
            manager = self.manager
            if ns:
                ns_dir = namespace_dir(ns)
                if not ns_dir.exists() or not ns_dir.is_dir():
                    return json_rpc_error(id, -32602, f"Namespace not found: {ns}")
                manager = TaskManager(tasks_dir=ns_dir, auto_sync=False)
            tasks = manager.list_tasks(domain_path, skip_sync=True)
            current_ns = ns or self.tasks_dir.name
            for t in tasks:
                t._namespace = current_ns

        if status_filter:
            try:
                status_code = task_status_code(status_filter)
            except ValueError:
                return json_rpc_error(id, -32602, "Invalid status filter")
            tasks = [t for t in tasks if (t.status or "").upper() == status_code]

        task_dicts = []
        for t in tasks:
            flat = _flatten_subtasks(getattr(t, "subtasks", []))
            total_subtasks = len(flat)
            completed_subtasks = sum(1 for st in flat if getattr(st, "completed", False))
            d = task_to_dict(t, include_subtasks=False, compact=True)
            d["subtask_count"] = total_subtasks
            d["completed_count"] = completed_subtasks
            d["tags"] = list(getattr(t, "tags", []) or [])
            d["updated_at"] = getattr(t, "updated", "") or None
            d["namespace"] = getattr(t, "_namespace", self.tasks_dir.name)
            task_dicts.append(d)

        result = {
            "success": True,
            "tasks": task_dicts,
            "filters": {
                "domain": domain_path,
                "phase": phase,
                "component": component,
                "status": status_filter or None,
                "namespace": ns or None,
                "all_namespaces": all_namespaces,
            },
        }
        return json_rpc_response(id, {
            "content": [
                self._json_content(result)
            ],
            "isError": False
        })

    def _handle_show(self, id: Optional[int | str], params: Dict[str, Any]) -> Dict:
        """Handle tasks_show tool call."""
        task_id = params.get("task")
        domain = params.get("domain", "") or ""
        namespace = params.get("namespace", "") or ""

        if not task_id:
            return json_rpc_error(id, -32602, "Missing 'task' argument")

        task = None

        # If namespace provided, look directly in that namespace
        if namespace:
            try:
                ns_dir = namespace_dir(namespace)
            except Exception as exc:
                return json_rpc_error(id, -32602, f"Invalid namespace: {exc}")
            if ns_dir.exists() and ns_dir.is_dir():
                ns_manager = TaskManager(tasks_dir=ns_dir, auto_sync=False)
                task = ns_manager.load_task(task_id, domain) or ns_manager.load_task(task_id, "")

        # Try current namespace with domain filter if not found in specified namespace
        if not task:
            task = self.manager.load_task(task_id, domain)

        # Fallback: search all namespaces if not found
        if not task:
            if GLOBAL_TASKS_ROOT.exists():
                for ns_dir in GLOBAL_TASKS_ROOT.iterdir():
                    if ns_dir.is_dir() and not ns_dir.name.startswith("."):
                        try:
                            ns_manager = TaskManager(tasks_dir=ns_dir, auto_sync=False)
                            task = ns_manager.load_task(task_id, domain) or ns_manager.load_task(task_id, "")
                            if task:
                                task._namespace = ns_dir.name
                                break
                        except Exception:
                            continue

        if not task:
            return json_rpc_error(id, -32602, f"Task {task_id} not found")

        result = {
            "success": True,
            "task": task_to_dict(task, include_subtasks=True),
            "domain": task.domain or domain,
            "namespace": getattr(task, "_namespace", namespace or self.tasks_dir.name),
        }
        return json_rpc_response(id, {
            "content": [
                self._json_content(result)
            ],
            "isError": False
        })

    def _handle_next(self, id: Optional[int | str], params: Dict) -> Dict:
        domain = params.get("domain", "") or ""
        phase = params.get("phase", "") or ""
        component = params.get("component", "") or ""
        domain_path = derive_domain_explicit(domain, phase, component)
        tasks = self.manager.list_tasks(domain_path, skip_sync=True)
        payload, selected = next_recommendations(
            tasks,
            {"domain": domain_path, "phase": phase, "component": component},
            remember=save_last_task,
            serializer=task_to_dict,
        )
        result = {
            "success": True,
            "payload": payload,
            "selected": selected.id if selected else None,
        }
        return json_rpc_response(id, {
            "content": [
                self._json_content(result)
            ],
            "isError": False
        })

    def _handle_template_subtasks(self, id: Optional[int | str], params: Dict) -> Dict:
        count = max(3, int(params.get("count", 3)))
        template = [_template_subtask_entry(i + 1) for i in range(count)]
        payload = {
            "type": "subtasks",
            "count": count,
            "template": template,
            "tests_template": _template_test_matrix(),
            "documentation_template": _template_docs_matrix(),
            "usage": "apply_task ... --subtasks 'JSON' | --subtasks @file | --subtasks -",
        }
        return json_rpc_response(id, {
            "content": [self._json_content({"success": True, "payload": payload})],
            "isError": False
        })

    def _handle_automation_task_template(self, id: Optional[int | str], params: Dict) -> Dict:
        payload = _automation_template_payload(
            int(params.get("count", 3)),
            int(params.get("coverage", 85)),
            str(params.get("risks", "perf;deps")),
            str(params.get("sla", "p95<=200ms")),
        )
        return json_rpc_response(id, {
            "content": [self._json_content({"success": True, "payload": payload})],
            "isError": False
        })

    def _handle_macro_ok(self, id: Optional[int | str], params: Dict) -> Dict:
        task = params.get("task")
        path = params.get("path")
        if not task or path is None:
            return json_rpc_error(id, -32602, "task and path are required")
        domain = params.get("domain", "") or ""
        ns = (params.get("namespace") or "").strip()
        manager = self.manager
        if ns:
            try:
                ns_dir = namespace_dir(ns)
            except Exception as exc:
                return json_rpc_error(id, -32602, f"Invalid namespace: {exc}")
            if not ns_dir.exists() or not ns_dir.is_dir():
                return json_rpc_error(id, -32602, f"Namespace not found: {ns}")
            manager = TaskManager(tasks_dir=ns_dir, auto_sync=False)
        note_ops = []
        for field, checkpoint in (("criteria_note", "criteria"), ("tests_note", "tests"), ("blockers_note", "blockers")):
            val = params.get(field)
            if val:
                note_ops.append({
                    "intent": "verify",
                    "task": task,
                    "path": path,
                    "domain": domain,
                    "checkpoints": {checkpoint: {"confirmed": True, "note": val}},
                })
        done_op = {"intent": "done", "task": task, "path": path, "domain": domain, "force": params.get("force", False)}
        batch_ops = note_ops + [done_op]
        resp = process_intent(manager, {"intent": "batch", "task": task, "operations": batch_ops, "atomic": True, "domain": domain})
        content = resp.result if resp.success else {"error": resp.error.to_dict() if resp.error else "unknown"}
        return json_rpc_response(id, {"content": [self._json_content(content)], "isError": not resp.success})

    def _handle_macro_note(self, id: Optional[int | str], params: Dict) -> Dict:
        task = params.get("task")
        path = params.get("path")
        checkpoint = params.get("checkpoint")
        note = params.get("note")
        if not all([task, path, checkpoint, note]):
            return json_rpc_error(id, -32602, "task, path, checkpoint, note are required")
        domain = params.get("domain", "") or ""
        ns = (params.get("namespace") or "").strip()
        manager = self.manager
        if ns:
            try:
                ns_dir = namespace_dir(ns)
            except Exception as exc:
                return json_rpc_error(id, -32602, f"Invalid namespace: {exc}")
            if not ns_dir.exists() or not ns_dir.is_dir():
                return json_rpc_error(id, -32602, f"Namespace not found: {ns}")
            manager = TaskManager(tasks_dir=ns_dir, auto_sync=False)
        op = {
            "intent": "verify",
            "task": task,
            "path": path,
            "domain": domain,
            "checkpoints": {checkpoint: {"confirmed": not params.get("undo", False), "note": note}},
        }
        resp = process_intent(manager, op)
        content = resp.result if resp.success else {"error": resp.error.to_dict() if resp.error else "unknown"}
        return json_rpc_response(id, {"content": [self._json_content(content)], "isError": not resp.success})

    def _handle_macro_bulk(self, id: Optional[int | str], params: Dict) -> Dict:
        operations = params.get("operations", [])
        task = params.get("task")
        domain = params.get("domain", "") or ""
        ns = (params.get("namespace") or "").strip()
        manager = self.manager
        if ns:
            try:
                ns_dir = namespace_dir(ns)
            except Exception as exc:
                return json_rpc_error(id, -32602, f"Invalid namespace: {exc}")
            if not ns_dir.exists() or not ns_dir.is_dir():
                return json_rpc_error(id, -32602, f"Namespace not found: {ns}")
            manager = TaskManager(tasks_dir=ns_dir, auto_sync=False)
        atomic = params.get("atomic", False)
        resp = process_intent(manager, {"intent": "batch", "task": task, "domain": domain, "operations": operations, "atomic": atomic})
        content = resp.result if resp.success else {"error": resp.error.to_dict() if resp.error else "unknown"}
        return json_rpc_response(id, {"content": [self._json_content(content)], "isError": not resp.success})

    def _handle_macro_update(self, id: Optional[int | str], params: Dict) -> Dict:
        task = params.get("task")
        status = params.get("status", "ACTIVE")
        domain = params.get("domain", "") or ""
        if not task:
            return json_rpc_error(id, -32602, "task is required")
        ns = (params.get("namespace") or "").strip()
        manager = self.manager
        if ns:
            try:
                ns_dir = namespace_dir(ns)
            except Exception as exc:
                return json_rpc_error(id, -32602, f"Invalid namespace: {exc}")
            if not ns_dir.exists() or not ns_dir.is_dir():
                return json_rpc_error(id, -32602, f"Namespace not found: {ns}")
            manager = TaskManager(tasks_dir=ns_dir, auto_sync=False)

        try:
            status_code = task_status_code(status)
        except ValueError:
            return json_rpc_error(id, -32602, "Invalid status")

        ok, err = manager.update_task_status(task, status, domain, force=params.get("force", False))
        payload = {
            "task": task,
            "status": task_status_label(status_code),
            "status_code": status_code,
            "domain": domain,
            "namespace": ns or None,
            "updated": ok,
        }
        if not ok:
            payload["error"] = err
        return json_rpc_response(id, {"content": [self._json_content(payload)], "isError": not ok})

    def _handle_macro_suggest(self, id: Optional[int | str], params: Dict) -> Dict:
        domain = params.get("domain", "") or ""
        phase = params.get("phase", "") or ""
        component = params.get("component", "") or ""
        domain_path = derive_domain_explicit(domain, phase, component)
        tasks = self.manager.list_tasks(domain_path, skip_sync=True)
        payload, selected = next_recommendations(
            tasks,
            {"domain": domain_path, "phase": phase, "component": component},
            remember=save_last_task,
            serializer=task_to_dict,
        )
        result = {"success": True, "payload": payload, "selected": selected.id if selected else None}
        return json_rpc_response(id, {"content": [self._json_content(result)], "isError": False})

    def _handle_macro_quick(self, id: Optional[int | str], params: Dict) -> Dict:
        # Alias to suggest
        return self._handle_macro_suggest(id, params)

    def _handle_automation_health(self, id: Optional[int | str], params: Dict) -> Dict:
        pytest_cmd = params.get("pytest_cmd", "").strip()
        perf_start = time.perf_counter()
        wall_start = time.time()
        tmp_dir = _ensure_tmp_dir()
        log_path = tmp_dir / "health.mcp.log"
        result = {"pytest_cmd": pytest_cmd or "(skipped)", "log": str(log_path)}
        rc = 0
        stdout = ""
        stderr = ""
        if pytest_cmd:
            try:
                proc = subprocess.run(shlex.split(pytest_cmd), capture_output=True, text=True)
                rc = proc.returncode
                stdout = (proc.stdout or "").strip()
                stderr = (proc.stderr or "").strip()
            except FileNotFoundError as exc:
                rc = 1
                stderr = str(exc)
        result["stdout"] = stdout
        result["stderr"] = stderr
        result["rc"] = rc
        result["duration_sec"] = round(time.perf_counter() - perf_start, 4)
        result["duration_ms"] = int(result["duration_sec"] * 1000)
        result["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(wall_start))
        result["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(wall_start + result["duration_sec"]))
        try:
            log_path.write_text(
                json.dumps(
                    {
                        "rc": rc,
                        "stdout": stdout,
                        "stderr": stderr,
                        "cmd": pytest_cmd,
                        "duration_sec": result["duration_sec"],
                        "duration_ms": result["duration_ms"],
                        "started_at": result["started_at"],
                        "finished_at": result["finished_at"],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception:
            pass
        result["success"] = rc == 0
        return json_rpc_response(id, {"content": [self._json_content(result)], "isError": rc != 0})

    def _handle_automation_projects_health(self, id: Optional[int | str], params: Dict) -> Dict:
        perf_start = time.perf_counter()
        wall_start = time.time()
        payload = _projects_status_payload(force_refresh=True)
        result = {
            "success": True,
            "target": payload.get("target_label"),
            "auto_sync": payload.get("auto_sync"),
            "token_present": payload.get("token_present"),
            "rate_remaining": payload.get("rate_remaining"),
            "rate_reset": payload.get("rate_reset_human"),
            "status_reason": payload.get("status_reason"),
            "project_url": payload.get("project_url"),
            "pool": payload.get("pool"),
        }
        duration_sec = time.perf_counter() - perf_start
        result["duration_sec"] = round(duration_sec, 4)
        result["duration_ms"] = int(duration_sec * 1000)
        result["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(wall_start))
        result["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(wall_start + duration_sec))
        return json_rpc_response(id, {"content": [self._json_content(result)], "isError": False})


def run_stdio_server(tasks_dir: Optional[Path] = None, use_global: bool = True):
    """Run MCP server over stdio."""
    server = MCPServer(tasks_dir=tasks_dir, use_global=use_global)

    # Read from stdin, write to stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            data = json.loads(line)
            request = JsonRpcRequest.from_dict(data)
            response = server.handle_request(request)

            if response is not None:  # Notifications don't get responses
                print(json.dumps(response), flush=True)

        except json.JSONDecodeError as e:
            error = json_rpc_error(None, -32700, f"Parse error: {e}")
            print(json.dumps(error), flush=True)
        except Exception as e:
            error = json_rpc_error(None, -32603, f"Internal error: {e}")
            print(json.dumps(error), flush=True)


async def run_stdio_server_async(tasks_dir: Optional[Path] = None, use_global: bool = True):
    """Run MCP server over stdio (async version)."""
    server = MCPServer(tasks_dir=tasks_dir, use_global=use_global)

    loop = asyncio.get_event_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)

    writer_transport, writer_protocol = await loop.connect_write_pipe(
        asyncio.streams.FlowControlMixin, sys.stdout
    )
    writer = asyncio.StreamWriter(writer_transport, writer_protocol, reader, loop)

    while True:
        try:
            line = await reader.readline()
            if not line:
                break

            line = line.decode().strip()
            if not line:
                continue

            data = json.loads(line)
            request = JsonRpcRequest.from_dict(data)
            response = server.handle_request(request)

            if response is not None:
                output = json.dumps(response) + "\n"
                writer.write(output.encode())
                await writer.drain()

        except json.JSONDecodeError as e:
            error = json_rpc_error(None, -32700, f"Parse error: {e}")
            writer.write((json.dumps(error) + "\n").encode())
            await writer.drain()
        except Exception as e:
            error = json_rpc_error(None, -32603, f"Internal error: {e}")
            writer.write((json.dumps(error) + "\n").encode())
            await writer.drain()


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="MCP stdio server for task management")
    parser.add_argument("--tasks-dir", type=Path, help="Tasks directory")
    parser.add_argument("--local", action="store_true", help="Use local .tasks instead of global")
    parser.add_argument("--async", dest="use_async", action="store_true", help="Use async mode")

    args = parser.parse_args()

    if args.use_async:
        asyncio.run(run_stdio_server_async(
            tasks_dir=args.tasks_dir,
            use_global=not args.local
        ))
    else:
        run_stdio_server(
            tasks_dir=args.tasks_dir,
            use_global=not args.local
        )


if __name__ == "__main__":
    main()
