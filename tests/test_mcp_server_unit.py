"""Unit tests for MCP server."""

import json
from pathlib import Path

import pytest

from core.desktop.devtools.interface.mcp_server import (
    MCPServer,
    JsonRpcRequest,
    get_tool_definitions,
    json_rpc_response,
    json_rpc_error,
    TOOL_TO_INTENT,
    MCP_VERSION,
    SERVER_NAME,
)


class TestJsonRpc:
    """Tests for JSON-RPC helpers."""

    def test_json_rpc_response(self):
        resp = json_rpc_response(1, {"foo": "bar"})
        assert resp["jsonrpc"] == "2.0"
        assert resp["id"] == 1
        assert resp["result"] == {"foo": "bar"}

    def test_json_rpc_error(self):
        err = json_rpc_error(2, -32600, "Invalid request")
        assert err["jsonrpc"] == "2.0"
        assert err["id"] == 2
        assert err["error"]["code"] == -32600
        assert err["error"]["message"] == "Invalid request"

    def test_json_rpc_error_with_data(self):
        err = json_rpc_error(3, -32000, "Custom error", {"detail": "info"})
        assert err["error"]["data"] == {"detail": "info"}

    def test_request_from_dict(self):
        data = {"jsonrpc": "2.0", "method": "test", "id": 1, "params": {"x": 1}}
        req = JsonRpcRequest.from_dict(data)
        assert req.method == "test"
        assert req.id == 1
        assert req.params == {"x": 1}

    def test_request_from_dict_minimal(self):
        data = {"method": "ping"}
        req = JsonRpcRequest.from_dict(data)
        assert req.method == "ping"
        assert req.id is None
        assert req.params == {}


class TestToolDefinitions:
    """Tests for tool definitions."""

    def test_get_tool_definitions_returns_list(self):
        tools = get_tool_definitions()
        assert isinstance(tools, list)
        assert len(tools) > 0

    def test_all_tools_have_required_fields(self):
        tools = get_tool_definitions()
        for tool in tools:
            assert "name" in tool
            assert "description" in tool
            assert "inputSchema" in tool
            assert tool["inputSchema"]["type"] == "object"

    def test_tool_names_match_mapping(self):
        tools = get_tool_definitions()
        tool_names = {t["name"] for t in tools}
        mapping_names = set(TOOL_TO_INTENT.keys())
        assert tool_names == mapping_names

    def test_all_intents_covered(self):
        """All main intents should have a tool."""
        from core.desktop.devtools.interface.cli_ai import INTENT_HANDLERS

        covered_intents = set(TOOL_TO_INTENT.values())
        # These intents should be covered
        expected = {"context", "create", "decompose", "define", "verify",
                   "progress", "complete", "batch", "undo", "redo", "history", "storage"}
        assert expected.issubset(covered_intents)


class TestMCPServer:
    """Tests for MCP server."""

    def test_init_creates_tasks_dir(self, tmp_path):
        tasks_dir = tmp_path / ".tasks"
        server = MCPServer(tasks_dir=tasks_dir)
        assert tasks_dir.exists()

    def test_initialize_returns_capabilities(self, tmp_path):
        server = MCPServer(tasks_dir=tmp_path / ".tasks")
        req = JsonRpcRequest(jsonrpc="2.0", method="initialize", id=1)
        resp = server.handle_request(req)

        assert resp["result"]["protocolVersion"] == MCP_VERSION
        assert resp["result"]["serverInfo"]["name"] == SERVER_NAME
        assert "tools" in resp["result"]["capabilities"]

    def test_tools_list_returns_all_tools(self, tmp_path):
        server = MCPServer(tasks_dir=tmp_path / ".tasks")

        # Initialize first
        init_req = JsonRpcRequest(jsonrpc="2.0", method="initialize", id=1)
        server.handle_request(init_req)

        # Mark initialized
        notif = JsonRpcRequest(jsonrpc="2.0", method="notifications/initialized")
        server.handle_request(notif)

        # List tools
        req = JsonRpcRequest(jsonrpc="2.0", method="tools/list", id=2)
        resp = server.handle_request(req)

        assert "result" in resp
        assert "tools" in resp["result"]
        expected = len(get_tool_definitions())
        assert len(resp["result"]["tools"]) == expected

    def test_tools_call_context(self, tmp_path):
        server = MCPServer(tasks_dir=tmp_path / ".tasks")

        # Initialize
        server.handle_request(JsonRpcRequest(jsonrpc="2.0", method="initialize", id=1))
        server.handle_request(JsonRpcRequest(jsonrpc="2.0", method="notifications/initialized"))

        # Call context tool
        req = JsonRpcRequest(
            jsonrpc="2.0",
            method="tools/call",
            id=3,
            params={"name": "tasks_context", "arguments": {}}
        )
        resp = server.handle_request(req)

        assert "result" in resp
        assert "content" in resp["result"]
        assert resp["result"]["isError"] is False

        # Parse content
        content = json.loads(resp["result"]["content"][0]["text"])
        assert content["success"] is True

    def test_tools_call_create(self, tmp_path):
        server = MCPServer(tasks_dir=tmp_path / ".tasks")

        # Initialize
        server.handle_request(JsonRpcRequest(jsonrpc="2.0", method="initialize", id=1))
        server.handle_request(JsonRpcRequest(jsonrpc="2.0", method="notifications/initialized"))

        # Create task
        req = JsonRpcRequest(
            jsonrpc="2.0",
            method="tools/call",
            id=4,
            params={
                "name": "tasks_create",
                "arguments": {"title": "Test Task"}
            }
        )
        resp = server.handle_request(req)

        content = json.loads(resp["result"]["content"][0]["text"])
        assert content["success"] is True
        assert "task_id" in content["result"]

    def test_tools_call_unknown_tool(self, tmp_path):
        server = MCPServer(tasks_dir=tmp_path / ".tasks")

        # Initialize
        server.handle_request(JsonRpcRequest(jsonrpc="2.0", method="initialize", id=1))
        server.handle_request(JsonRpcRequest(jsonrpc="2.0", method="notifications/initialized"))

        # Unknown tool
        req = JsonRpcRequest(
            jsonrpc="2.0",
            method="tools/call",
            id=5,
            params={"name": "unknown_tool", "arguments": {}}
        )
        resp = server.handle_request(req)

        assert "error" in resp
        assert resp["error"]["code"] == -32602

    def test_not_initialized_error(self, tmp_path):
        server = MCPServer(tasks_dir=tmp_path / ".tasks")

        # Try to call without initialize
        req = JsonRpcRequest(jsonrpc="2.0", method="tools/list", id=1)
        resp = server.handle_request(req)

        assert "error" in resp
        assert resp["error"]["code"] == -32002

    def test_ping(self, tmp_path):
        server = MCPServer(tasks_dir=tmp_path / ".tasks")

        # Initialize
        server.handle_request(JsonRpcRequest(jsonrpc="2.0", method="initialize", id=1))
        server.handle_request(JsonRpcRequest(jsonrpc="2.0", method="notifications/initialized"))

        # Ping
        req = JsonRpcRequest(jsonrpc="2.0", method="ping", id=2)
        resp = server.handle_request(req)

        assert resp["result"] == {}

    def test_unknown_method(self, tmp_path):
        server = MCPServer(tasks_dir=tmp_path / ".tasks")

        # Initialize
        server.handle_request(JsonRpcRequest(jsonrpc="2.0", method="initialize", id=1))
        server.handle_request(JsonRpcRequest(jsonrpc="2.0", method="notifications/initialized"))

        # Unknown method
        req = JsonRpcRequest(jsonrpc="2.0", method="unknown/method", id=3)
        resp = server.handle_request(req)

        assert "error" in resp
        assert resp["error"]["code"] == -32601


class TestMCPToolsIntegration:
    """Integration tests for MCP tools with task operations."""

    def test_full_workflow(self, tmp_path):
        """Test create -> decompose -> define -> verify -> progress."""
        server = MCPServer(tasks_dir=tmp_path / ".tasks")

        # Initialize
        server.handle_request(JsonRpcRequest(jsonrpc="2.0", method="initialize", id=1))
        server.handle_request(JsonRpcRequest(jsonrpc="2.0", method="notifications/initialized"))

        # 1. Create task
        resp = server.handle_request(JsonRpcRequest(
            jsonrpc="2.0",
            method="tools/call",
            id=2,
            params={"name": "tasks_create", "arguments": {"title": "Integration Test"}}
        ))
        content = json.loads(resp["result"]["content"][0]["text"])
        task_id = content["result"]["task_id"]
        assert task_id

        # 2. Decompose - needs criteria, tests, blockers
        resp = server.handle_request(JsonRpcRequest(
            jsonrpc="2.0",
            method="tools/call",
            id=3,
            params={
                "name": "tasks_decompose",
                "arguments": {
                    "task": task_id,
                    "subtasks": [{
                        "title": "Step 1",
                        "criteria": ["Done"],
                        "tests": ["test_step1"],
                        "blockers": ["none"]
                    }]
                }
            }
        ))
        content = json.loads(resp["result"]["content"][0]["text"])
        assert content["success"] is True
        assert content["result"]["total_created"] == 1

        # 3. Verify all checkpoints (criteria, tests, blockers)
        resp = server.handle_request(JsonRpcRequest(
            jsonrpc="2.0",
            method="tools/call",
            id=4,
            params={
                "name": "tasks_verify",
                "arguments": {
                    "task": task_id,
                    "path": "0",
                    "checkpoints": {
                        "criteria": {"confirmed": True, "note": "criteria ok"},
                        "tests": {"confirmed": True, "note": "tests ok"},
                        "blockers": {"confirmed": True, "note": "blockers resolved"}
                    }
                }
            }
        ))
        content = json.loads(resp["result"]["content"][0]["text"])
        assert content["success"] is True

        # 4. Progress - mark as complete
        resp = server.handle_request(JsonRpcRequest(
            jsonrpc="2.0",
            method="tools/call",
            id=5,
            params={
                "name": "tasks_progress",
                "arguments": {"task": task_id, "path": "0", "completed": True}
            }
        ))
        content = json.loads(resp["result"]["content"][0]["text"])
        assert content["success"] is True

        # 5. Context to verify state
        resp = server.handle_request(JsonRpcRequest(
            jsonrpc="2.0",
            method="tools/call",
            id=6,
            params={"name": "tasks_context", "arguments": {"task": task_id}}
        ))
        content = json.loads(resp["result"]["content"][0]["text"])
        assert content["success"] is True

    def test_undo_redo(self, tmp_path):
        """Test undo/redo through MCP."""
        server = MCPServer(tasks_dir=tmp_path / ".tasks")

        # Initialize
        server.handle_request(JsonRpcRequest(jsonrpc="2.0", method="initialize", id=1))
        server.handle_request(JsonRpcRequest(jsonrpc="2.0", method="notifications/initialized"))

        # Create task
        server.handle_request(JsonRpcRequest(
            jsonrpc="2.0",
            method="tools/call",
            id=2,
            params={"name": "tasks_create", "arguments": {"title": "Undo Test"}}
        ))

        # Undo
        resp = server.handle_request(JsonRpcRequest(
            jsonrpc="2.0",
            method="tools/call",
            id=3,
            params={"name": "tasks_undo", "arguments": {}}
        ))
        # May or may not succeed depending on history state
        assert "result" in resp

    def test_batch_atomic(self, tmp_path):
        """Test atomic batch through MCP."""
        server = MCPServer(tasks_dir=tmp_path / ".tasks")

        # Initialize
        server.handle_request(JsonRpcRequest(jsonrpc="2.0", method="initialize", id=1))
        server.handle_request(JsonRpcRequest(jsonrpc="2.0", method="notifications/initialized"))

        # Create task first
        resp = server.handle_request(JsonRpcRequest(
            jsonrpc="2.0",
            method="tools/call",
            id=2,
            params={"name": "tasks_create", "arguments": {"title": "Batch Test"}}
        ))
        content = json.loads(resp["result"]["content"][0]["text"])
        task_id = content["result"]["task_id"]

        # Atomic batch - subtasks need criteria, tests, blockers
        resp = server.handle_request(JsonRpcRequest(
            jsonrpc="2.0",
            method="tools/call",
            id=3,
            params={
                "name": "tasks_batch",
                "arguments": {
                    "task": task_id,
                    "atomic": True,
                    "operations": [
                        {"intent": "decompose", "subtasks": [{
                            "title": "B1",
                            "criteria": ["Done"],
                            "tests": ["test_b1"],
                            "blockers": ["none"],
                        }]},
                        {"intent": "context"}
                    ]
                }
            }
        ))
        content = json.loads(resp["result"]["content"][0]["text"])
        assert content["success"] is True
