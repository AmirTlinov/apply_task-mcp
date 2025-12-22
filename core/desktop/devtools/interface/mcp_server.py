#!/usr/bin/env python3
"""MCP (Model Context Protocol) stdio server for apply_task.

This server is a thin, deterministic wrapper around the canonical AI intent API:
`core.desktop.devtools.interface.intent_api.process_intent`.

Canonical model:
- Plan: TaskDetail(kind="plan", id="PLAN-###") stores Contract + Plan checklist.
- Task: TaskDetail(kind="task", id="TASK-###", parent="PLAN-###") stores nested Steps.
- Step: recursive node inside a Task (`TaskDetail.steps`), with checkpoints:
  - criteria
  - tests
Blockers are stored as data (`blockers: [str]`) but are NOT a checkpoint.
"""

from __future__ import annotations

import json
import io
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from contextlib import redirect_stdout

from core.desktop.devtools.application.task_manager import TaskManager
from core.desktop.devtools.interface.intent_api import INTENT_HANDLERS, process_intent
from core.desktop.devtools.interface.tasks_dir_resolver import get_tasks_dir_for_project, resolve_project_root


MCP_VERSION = "2024-11-05"
SERVER_NAME = "apply-task-mcp"
SERVER_VERSION = "1.0.0"


@dataclass
class JsonRpcRequest:
    """JSON-RPC 2.0 request."""

    jsonrpc: str
    method: str
    id: Optional[int | str] = None
    params: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "JsonRpcRequest":
        return cls(
            jsonrpc=str(data.get("jsonrpc", "2.0") or "2.0"),
            method=str(data["method"]),
            id=data.get("id"),
            params=data.get("params", {}) if isinstance(data.get("params", {}), dict) else {},
        )


def json_rpc_response(id: Optional[int | str], result: Any) -> Dict[str, Any]:
    """Create JSON-RPC success response."""
    return {"jsonrpc": "2.0", "id": id, "result": result}


def json_rpc_error(id: Optional[int | str], code: int, message: str, data: Any = None) -> Dict[str, Any]:
    """Create JSON-RPC error response."""
    error: Dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": id, "error": error}


def _step_path_description() -> str:
    return "Step path inside task.steps (e.g. 's:0' or 's:0.t:1.s:2')."


def _task_path_description() -> str:
    return "Task path inside a step plan (e.g. 's:0.t:1')."


_TOOL_SPECS: Dict[str, Dict[str, Any]] = {
    "context": {
        "description": "Get current context (plans, tasks, focus, suggestions).",
        "schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Focus task id (TASK-###)."},
                "plan": {"type": "string", "description": "Focus plan id (PLAN-###)."},
                "include_all": {"type": "boolean", "default": False, "description": "Include full plans/tasks lists."},
                "compact": {"type": "boolean", "default": True, "description": "Return compact task/plan summaries."},
                "tasks_limit": {"type": "integer", "description": "Max tasks to return in context list."},
                "tasks_cursor": {"type": "string", "description": "Tasks list cursor offset."},
                "plans_limit": {"type": "integer", "description": "Max plans to return in context list."},
                "plans_cursor": {"type": "string", "description": "Plans list cursor offset."},
                "tasks_status": {"type": "string", "description": "Filter tasks by status (TODO|ACTIVE|DONE) or array."},
                "plans_status": {"type": "string", "description": "Filter plans by status (TODO|ACTIVE|DONE) or array."},
                "tasks_parent": {"type": "string", "description": "Filter tasks by parent plan id (PLAN-###)."},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Filter by tags (applies to plans + tasks)."},
                "domain": {"type": "string", "description": "Filter by domain (applies to plans + tasks)."},
                "subtree": {
                    "type": "object",
                    "description": "Fetch a subtree node by path/id from a task.",
                    "properties": {
                        "task": {"type": "string", "description": "Task id (TASK-###). Defaults to focus task."},
                        "path": {"type": "string", "description": _step_path_description()},
                        "kind": {"type": "string", "description": "step|plan|task (default inferred)."},
                        "step_id": {"type": "string", "description": "Stable step id (STEP-...)."},
                        "task_node_id": {"type": "string", "description": "Stable task node id (NODE-...)."},
                        "compact": {"type": "boolean", "description": "Return compact node payload."},
                    },
                },
            },
            "required": [],
        },
    },
    "focus_get": {
        "description": "Get current focus (.last pointer).",
        "schema": {"type": "object", "properties": {}, "required": []},
    },
    "focus_set": {
        "description": "Set focus (.last pointer).",
        "schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Focus id (TASK-### or PLAN-###)."},
                "domain": {"type": "string", "default": "", "description": "Optional domain for the focus pointer."},
            },
            "required": ["task"],
        },
    },
    "focus_clear": {
        "description": "Clear focus (.last pointer).",
        "schema": {"type": "object", "properties": {}, "required": []},
    },
    "radar": {
        "description": "Radar View: compact snapshot (Now/Why/Verify/Next/Blockers/Open checkpoints).",
        "schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task id (TASK-###). Uses focus if omitted."},
                "plan": {"type": "string", "description": "Plan id (PLAN-###). Uses focus if omitted."},
                "limit": {"type": "integer", "default": 3, "description": "Max next suggestions to return (0..10)."},
            },
            "required": [],
        },
    },
    "resume": {
        "description": "Load a specific plan/task (or focus) with optional timeline.",
        "schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task id (TASK-###)."},
                "plan": {"type": "string", "description": "Plan id (PLAN-###)."},
                "events_limit": {"type": "integer", "description": "Timeline events limit (default 20)."},
            },
            "required": [],
        },
    },
    "create": {
        "description": "Create a Plan (PLAN-###) or Task (TASK-### under a Plan).",
        "schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Title for the new item."},
                "kind": {"type": "string", "description": "Optional: 'plan' or 'task'. Defaults to 'plan' unless parent is set."},
                "parent": {"type": "string", "description": "Required for kind=task: parent plan id (PLAN-###)."},
                "priority": {"type": "string", "description": "LOW|MEDIUM|HIGH (default MEDIUM)."},
                "description": {"type": "string"},
                "context": {"type": "string"},
                "contract": {"type": "string"},
                "contract_data": {"type": "object", "description": "Optional structured contract data (see AI_INTENTS.md)."},
                "steps": {"type": "array", "description": "Optional nested steps for kind=task.", "items": {"type": "object"}},
                "dry_run": {"type": "boolean", "default": False},
            },
            "required": ["title"],
        },
    },
    "decompose": {
        "description": "Add nested steps to a Task (TASK-###).",
        "schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task id (TASK-###)."},
                "parent": {"type": "string", "description": "Optional parent task path (e.g. s:0.t:1)."},
                "steps": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Steps to add. Each step requires title + success_criteria.",
                },
            },
            "required": ["task", "steps"],
        },
    },
    "task_add": {
        "description": "Add a task node inside a step plan (Step→Plan→Task).",
        "schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Root task id (TASK-###)."},
                "parent_step": {"type": "string", "description": _step_path_description()},
                "title": {"type": "string", "description": "Task node title."},
                "status": {"type": "string", "description": "TODO|ACTIVE|DONE (optional)."},
                "priority": {"type": "string", "description": "LOW|MEDIUM|HIGH (optional)."},
                "description": {"type": "string"},
                "context": {"type": "string"},
                "success_criteria": {"type": "array", "items": {"type": "string"}},
                "dependencies": {"type": "array", "items": {"type": "string"}},
                "next_steps": {"type": "array", "items": {"type": "string"}},
                "problems": {"type": "array", "items": {"type": "string"}},
                "risks": {"type": "array", "items": {"type": "string"}},
                "blocked": {"type": "boolean"},
                "blockers": {"type": "array", "items": {"type": "string"}},
                "status_manual": {"type": "boolean"},
            },
            "required": ["task", "parent_step", "title"],
        },
    },
    "task_define": {
        "description": "Update a task node inside a step plan by path.",
        "schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Root task id (TASK-###)."},
                "path": {"type": "string", "description": _task_path_description()},
                "title": {"type": "string"},
                "status": {"type": "string"},
                "priority": {"type": "string"},
                "description": {"type": "string"},
                "context": {"type": "string"},
                "success_criteria": {"type": "array", "items": {"type": "string"}},
                "dependencies": {"type": "array", "items": {"type": "string"}},
                "next_steps": {"type": "array", "items": {"type": "string"}},
                "problems": {"type": "array", "items": {"type": "string"}},
                "risks": {"type": "array", "items": {"type": "string"}},
                "blocked": {"type": "boolean"},
                "blockers": {"type": "array", "items": {"type": "string"}},
                "status_manual": {"type": "boolean"},
            },
            "required": ["task", "path"],
        },
    },
    "task_delete": {
        "description": "Delete a task node inside a step plan by path.",
        "schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Root task id (TASK-###)."},
                "path": {"type": "string", "description": _task_path_description()},
            },
            "required": ["task", "path"],
        },
    },
    "define": {
        "description": "Update step fields (title/success_criteria/tests/blockers) for a step path.",
        "schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task id (TASK-###)."},
                "path": {"type": "string", "description": _step_path_description()},
                "title": {"type": "string"},
                "success_criteria": {"type": "array", "items": {"type": "string"}},
                "tests": {"type": "array", "items": {"type": "string"}},
                "blockers": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["task", "path"],
        },
    },
    "verify": {
        "description": "Confirm checkpoints (criteria/tests) for a step/task/plan. For step/task/plan, provide path or node id; for task_detail (root plan/task), omit path.",
        "schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task id (TASK-### or PLAN-###)."},
                "kind": {
                    "type": "string",
                    "description": "Target kind: step|task|plan|task_detail. Defaults to step.",
                },
                "path": {"type": "string", "description": _step_path_description()},
                "step_id": {"type": "string", "description": "Stable step id (STEP-XXXX)."},
                "task_node_id": {"type": "string", "description": "Stable task node id (NODE-XXXX)."},
                "checkpoints": {
                    "type": "object",
                    "description": "Allowed: checkpoints.criteria / checkpoints.tests",
                    "properties": {
                        "criteria": {"type": "object"},
                        "tests": {"type": "object"},
                    },
                },
                "checks": {"type": "array", "description": "Optional verification checks for step targets."},
                "attachments": {"type": "array", "description": "Optional attachments for step targets."},
                "verification_outcome": {"type": "string", "description": "Optional outcome label for step targets."},
            },
            "required": ["task", "checkpoints"],
        },
    },
    "done": {
        "description": "Unified “verify + done” style completion (optional note is saved as a progress note first).",
        "schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task id (TASK-###)."},
                "path": {"type": "string", "description": _step_path_description()},
                "step_id": {"type": "string", "description": "Stable step id (STEP-...)."},
                "note": {"type": "string", "description": "Optional progress note saved before completion."},
                "force": {"type": "boolean", "default": False},
                "override_reason": {"type": "string", "description": "Required when force=true."},
            },
            "required": ["task", "path"],
        },
    },
    "progress": {
        "description": "Mark a step path completed/uncompleted (respects checkpoints unless force=true).",
        "schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task id (TASK-###)."},
                "path": {"type": "string", "description": _step_path_description()},
                "completed": {"type": "boolean"},
                "force": {"type": "boolean", "default": False},
            },
            "required": ["task", "path"],
        },
    },
    "edit": {
        "description": "Edit task/plan meta fields (no step mutations).",
        "schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task id (TASK-### or PLAN-###)."},
                "description": {"type": "string"},
                "context": {"type": "string"},
                "priority": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "depends_on": {"type": "array", "items": {"type": "string"}},
                "new_domain": {"type": "string"},
            },
            "required": ["task"],
        },
    },
    "note": {
        "description": "Add a progress note to a step path (does not complete it).",
        "schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task id (TASK-###)."},
                "path": {"type": "string", "description": _step_path_description()},
                "domain": {"type": "string", "default": "", "description": "Optional domain (usually not needed)."},
                "note": {"type": "string", "description": "Progress note text."},
            },
            "required": ["task", "path", "note"],
        },
    },
    "block": {
        "description": "Block/unblock a step path (blockers are data; this toggles blocked state).",
        "schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task id (TASK-###)."},
                "path": {"type": "string", "description": _step_path_description()},
                "domain": {"type": "string", "default": "", "description": "Optional domain (usually not needed)."},
                "blocked": {
                    "type": "boolean",
                    "default": True,
                    "description": "true = block, false = unblock",
                },
                "reason": {"type": "string", "description": "Optional reason."},
            },
            "required": ["task", "path"],
        },
    },
    "contract": {
        "description": "Set/clear a plan contract.",
        "schema": {
            "type": "object",
            "properties": {
                "plan": {"type": "string", "description": "Plan id (PLAN-###)."},
                "current": {"type": "string", "description": "New contract text."},
                "contract_data": {"type": "object", "description": "Optional structured contract data (see AI_INTENTS.md)."},
                "clear": {"type": "boolean", "default": False, "description": "Clear contract when true."},
            },
            "required": ["plan"],
        },
    },
    "plan": {
        "description": "Update plan checklist (`doc`, `steps`, `current`) and/or `advance=true`.",
        "schema": {
            "type": "object",
            "properties": {
                "plan": {"type": "string", "description": "Plan id (PLAN-###)."},
                "doc": {"type": "string", "description": "Plan documentation (free text)."},
                "steps": {"type": "array", "items": {"type": "string"}, "description": "Plan checklist steps."},
                "current": {"type": "integer", "description": "Current step index (0-based)."},
                "advance": {"type": "boolean", "default": False, "description": "Increment current by 1."},
            },
            "required": ["plan"],
        },
    },
    "mirror": {
        "description": "Export a compact plan slice for a plan/task (one in-progress item).",
        "schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task id (TASK-###)."},
                "plan": {"type": "string", "description": "Plan id (PLAN-###)."},
                "path": {"type": "string", "description": "Optional step/task path for subtree mirror."},
                "kind": {"type": "string", "description": "step|task (for path hints)."},
                "step_id": {"type": "string", "description": "Stable step id (STEP-...)."},
                "task_node_id": {"type": "string", "description": "Stable task node id (NODE-...)."},
                "limit": {"type": "integer", "description": "Limit number of items returned."},
            },
            "required": [],
        },
    },
    "complete": {
        "description": "Set status for a plan/task (TODO/ACTIVE/DONE).",
        "schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task id (TASK-### or PLAN-###)."},
                "status": {"type": "string", "description": "TODO|ACTIVE|DONE (default DONE)."},
                "force": {"type": "boolean", "default": False},
                "override_reason": {"type": "string", "description": "Required when force=true."},
                "domain": {"type": "string", "description": "Optional domain override."},
            },
            "required": ["task"],
        },
    },
    "delete": {
        "description": "Delete a task file (TASK/PLAN) or a nested step (when path/step_id is provided).",
        "schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task id (TASK-### or PLAN-###)."},
                "path": {"type": "string", "description": _step_path_description()},
                "step_id": {"type": "string", "description": "Stable step id (STEP-...)."},
                "domain": {"type": "string", "description": "Optional domain override."},
            },
            "required": ["task"],
        },
    },
    "batch": {
        "description": "Run multiple operations in one call (optional atomic rollback).",
        "schema": {
            "type": "object",
            "properties": {
                "operations": {"type": "array", "items": {"type": "object"}, "description": "List of intent payloads."},
                "atomic": {"type": "boolean", "default": False, "description": "Rollback all on first failure."},
                "task": {"type": "string", "description": "Default task id to apply when op omits it."},
            },
            "required": ["operations"],
        },
    },
    "undo": {
        "description": "Undo last operation (history).",
        "schema": {"type": "object", "properties": {}, "required": []},
    },
    "redo": {
        "description": "Redo last undone operation (history).",
        "schema": {"type": "object", "properties": {}, "required": []},
    },
    "history": {
        "description": "Get operation history (undo/redo metadata).",
        "schema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 20, "description": "Max operations returned."}},
            "required": [],
        },
    },
    "delta": {
        "description": "Get operations since a given operation id (delta updates).",
        "schema": {
            "type": "object",
            "properties": {
                "since": {"type": "string", "description": "Return ops strictly after this operation id."},
                "limit": {"type": "integer", "default": 50, "description": "Max operations returned (0..500)."},
                "include_undone": {"type": "boolean", "default": True, "description": "Include operations marked as undone."},
            },
            "required": [],
        },
    },
    "storage": {
        "description": "Get storage paths and namespaces.",
        "schema": {"type": "object", "properties": {}, "required": []},
    },
}


def _tool_name(intent: str) -> str:
    return f"tasks_{intent}"


TOOL_TO_INTENT: Dict[str, str] = {_tool_name(intent): intent for intent in sorted(INTENT_HANDLERS.keys())}


def get_tool_definitions() -> List[Dict[str, Any]]:
    """Return MCP tool definitions (1:1 with canonical intent API intents)."""
    tools: List[Dict[str, Any]] = []
    for tool_name, intent in sorted(TOOL_TO_INTENT.items(), key=lambda kv: kv[0]):
        spec = _TOOL_SPECS.get(intent) or {}
        description = str(spec.get("description") or f"Run apply_task AI intent '{intent}'.")
        schema = spec.get("schema") or {"type": "object", "properties": {}, "required": []}
        tools.append({"name": tool_name, "description": description, "inputSchema": schema})
    return tools


class MCPServer:
    """MCP stdio server exposing apply_task AI intents."""

    def __init__(self, tasks_dir: Optional[Path] = None, use_global: bool = True):
        if tasks_dir is None:
            tasks_dir = get_tasks_dir_for_project(
                use_global=use_global,
                project_root=resolve_project_root(),
                create=True,
            )
        self.tasks_dir = Path(tasks_dir)
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        # MCP must be stdout-clean and side-effect free by default: avoid auto-sync on init.
        self.manager = TaskManager(self.tasks_dir, auto_sync=False, use_global=use_global)
        self._initialized = False

    @staticmethod
    def _json_content(payload: Any) -> Dict[str, Any]:
        return {"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}

    def handle_request(self, request: JsonRpcRequest) -> Optional[Dict[str, Any]]:
        method = request.method
        params = request.params

        if method == "initialize":
            return json_rpc_response(
                request.id,
                {
                    "protocolVersion": MCP_VERSION,
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                    "capabilities": {"tools": {}},
                },
            )

        if not self._initialized and method != "notifications/initialized":
            return json_rpc_error(request.id, -32002, "Server not initialized")

        if method == "notifications/initialized":
            self._initialized = True
            return None

        if method == "tools/list":
            return json_rpc_response(request.id, {"tools": get_tool_definitions()})

        if method == "tools/call":
            return self._handle_tools_call(request.id, params)

        if method == "ping":
            return json_rpc_response(request.id, {})

        return json_rpc_error(request.id, -32601, f"Method not found: {method}")

    def _handle_tools_call(self, id: Optional[int | str], params: Dict[str, Any]) -> Dict[str, Any]:
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        if tool_name not in TOOL_TO_INTENT:
            return json_rpc_error(id, -32602, f"Unknown tool: {tool_name}")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return json_rpc_error(id, -32602, "arguments must be an object")

        intent = TOOL_TO_INTENT[tool_name]
        payload = dict(arguments)
        payload["intent"] = intent
        leaked = io.StringIO()
        with redirect_stdout(leaked):
            resp = process_intent(self.manager, payload)
        leaked_text = leaked.getvalue()
        if leaked_text.strip():
            # Never leak prints into the JSON-RPC channel; route to stderr + warnings.
            print(leaked_text, file=sys.stderr, end="")
            resp.warnings.append(leaked_text.strip().splitlines()[0])
        body = resp.to_dict()
        return json_rpc_response(
            id,
            {
                "content": [self._json_content(body)],
                "isError": not bool(body.get("success", False)),
            },
        )


def run_stdio(*, tasks_dir: Optional[Path] = None, use_global: bool = True) -> int:
    """Run MCP server over stdio (newline-delimited JSON-RPC)."""
    server = MCPServer(tasks_dir=tasks_dir, use_global=use_global)
    for line in sys.stdin:
        raw = line.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            resp = json_rpc_error(None, -32700, f"Parse error: {exc}")
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
            continue
        if not isinstance(data, dict) or "method" not in data:
            resp = json_rpc_error(data.get("id") if isinstance(data, dict) else None, -32600, "Invalid Request")
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
            continue
        req = JsonRpcRequest.from_dict(data)
        out = server.handle_request(req)
        if out is None:
            continue
        sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    """Module entrypoint for `python -m core.desktop.devtools.interface.mcp_server`."""
    import argparse

    parser = argparse.ArgumentParser(prog="apply_task-mcp", add_help=True)
    parser.add_argument("--tasks-dir", type=str, help="Explicit tasks directory (overrides resolver).")
    parser.add_argument(
        "--local",
        dest="use_global",
        action="store_false",
        help="Use local storage <project>/.tasks",
    )
    parser.add_argument(
        "--global",
        "-g",
        dest="use_global",
        action="store_true",
        help="Use global storage ~/.tasks (default)",
    )
    parser.set_defaults(use_global=True)
    args = parser.parse_args(argv)

    tasks_dir = Path(args.tasks_dir).expanduser().resolve() if args.tasks_dir else None
    return run_stdio(tasks_dir=tasks_dir, use_global=bool(args.use_global))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
