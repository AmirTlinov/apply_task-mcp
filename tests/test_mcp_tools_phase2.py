"""Unit tests for MCP tool schemas: v0 strict parameterization + focus/radar.

Phase 2 (control-tower) UX requires:
- no ambiguous no-arg tools for intents that require ids
- explicit focus helpers (focus_get/focus_set/focus_clear)
- a compact Radar View (intent=radar)
"""

from core.desktop.devtools.interface.mcp_server import get_tool_definitions


def _tool(name: str) -> dict:
    tools = get_tool_definitions()
    found = next((t for t in tools if t["name"] == name), None)
    assert found is not None, f"{name} tool not found"
    return found


class TestStrictSchemas:
    def test_tools_have_non_empty_schema(self):
        """Intents must be callable without guessing required parameters."""
        tools = get_tool_definitions()
        for tool in tools:
            schema = tool["inputSchema"]
            assert schema["type"] == "object"
            assert isinstance(schema.get("properties", {}), dict)

    def test_complete_requires_task(self):
        schema = _tool("tasks_complete")["inputSchema"]
        assert set(schema["required"]) == {"task"}

    def test_contract_requires_plan(self):
        schema = _tool("tasks_contract")["inputSchema"]
        assert set(schema["required"]) == {"plan"}

    def test_plan_requires_plan(self):
        schema = _tool("tasks_plan")["inputSchema"]
        assert set(schema["required"]) == {"plan"}

    def test_done_requires_task_and_path(self):
        schema = _tool("tasks_done")["inputSchema"]
        assert set(schema["required"]) == {"task", "path"}

    def test_edit_requires_task(self):
        schema = _tool("tasks_edit")["inputSchema"]
        assert set(schema["required"]) == {"task"}

    def test_delete_requires_task(self):
        schema = _tool("tasks_delete")["inputSchema"]
        assert set(schema["required"]) == {"task"}

    def test_batch_requires_operations(self):
        schema = _tool("tasks_batch")["inputSchema"]
        assert set(schema["required"]) == {"operations"}


class TestFocusAndRadarTools:
    def test_focus_get_has_empty_required(self):
        schema = _tool("tasks_focus_get")["inputSchema"]
        assert schema.get("required", []) == []

    def test_focus_set_requires_task(self):
        schema = _tool("tasks_focus_set")["inputSchema"]
        assert set(schema["required"]) == {"task"}
        props = schema["properties"]
        assert props["task"]["type"] == "string"
        assert props["domain"]["type"] == "string"

    def test_focus_clear_has_empty_required(self):
        schema = _tool("tasks_focus_clear")["inputSchema"]
        assert schema.get("required", []) == []

    def test_radar_schema_accepts_task_or_plan(self):
        schema = _tool("tasks_radar")["inputSchema"]
        assert schema["type"] == "object"
        props = schema["properties"]
        assert "task" in props
        assert "plan" in props
        assert "limit" in props

    def test_handoff_schema_accepts_task_or_plan(self):
        schema = _tool("tasks_handoff")["inputSchema"]
        assert schema["type"] == "object"
        props = schema["properties"]
        assert "task" in props
        assert "plan" in props
        assert "limit" in props

    def test_context_pack_schema_accepts_task_or_plan(self):
        schema = _tool("tasks_context_pack")["inputSchema"]
        assert schema["type"] == "object"
        props = schema["properties"]
        assert "task" in props
        assert "plan" in props
        assert "delta_limit" in props
        assert "max_chars" in props
