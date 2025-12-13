import json

from core.desktop.devtools.interface.mcp_server import MCPServer, JsonRpcRequest


def _init_server(server: MCPServer):
    server.handle_request(JsonRpcRequest(jsonrpc="2.0", method="initialize", id=1, params={}))
    server.handle_request(JsonRpcRequest(jsonrpc="2.0", method="notifications/initialized", id=None, params={}))


def _parse_content(content: dict) -> dict:
    """Parse MCP content entry. Supports both text (standard) and json (legacy) types."""
    if content["type"] == "text":
        return json.loads(content["text"])
    elif content["type"] == "json":
        return content["json"]
    raise ValueError(f"Unsupported content type: {content['type']}")


def test_mcp_returns_text_content(monkeypatch, tmp_path):
    server = MCPServer(tasks_dir=tmp_path, use_global=False)
    _init_server(server)

    # create a sample task
    manager = server.manager
    task = manager.create_task("Sample", domain="")
    manager.save_task(task)

    # list
    list_resp = server.handle_request(JsonRpcRequest(
        jsonrpc="2.0",
        method="tools/call",
        id=2,
        params={"name": "tasks_list", "arguments": {}},
    ))
    content = list_resp["result"]["content"][0]
    assert content["type"] == "text"
    list_data = _parse_content(content)
    assert any(t["id"] == task.id for t in list_data["tasks"])

    # show
    show_resp = server.handle_request(JsonRpcRequest(
        jsonrpc="2.0",
        method="tools/call",
        id=3,
        params={"name": "tasks_show", "arguments": {"task": task.id}},
    ))
    show_content = show_resp["result"]["content"][0]
    assert show_content["type"] == "text"
    show_data = _parse_content(show_content)
    assert show_data["task"]["id"] == task.id

    # template subtasks
    tpl_resp = server.handle_request(JsonRpcRequest(
        jsonrpc="2.0",
        method="tools/call",
        id=4,
        params={"name": "tasks_template_subtasks", "arguments": {"count": 3}},
    ))
    tpl_content = tpl_resp["result"]["content"][0]
    assert tpl_content["type"] == "text"
    tpl_data = _parse_content(tpl_content)
    assert tpl_data["success"]

    # macro update (status)
    upd_resp = server.handle_request(JsonRpcRequest(
        jsonrpc="2.0",
        method="tools/call",
        id=5,
        params={"name": "tasks_macro_update", "arguments": {"task": task.id, "status": "DONE", "force": True}},
    ))
    upd_data = _parse_content(upd_resp["result"]["content"][0])
    assert upd_data["updated"]

    # automation health returns log path and rc
    health_resp = server.handle_request(JsonRpcRequest(
        jsonrpc="2.0",
        method="tools/call",
        id=6,
        params={"name": "tasks_automation_health", "arguments": {"pytest_cmd": ""}},
    ))
    health_data = _parse_content(health_resp["result"]["content"][0])
    assert "log" in health_data
    assert "rc" in health_data

    # projects health contains rate metadata
    proj_resp = server.handle_request(JsonRpcRequest(
        jsonrpc="2.0",
        method="tools/call",
        id=7,
        params={"name": "tasks_automation_projects_health", "arguments": {}},
    ))
    proj_data = _parse_content(proj_resp["result"]["content"][0])
    assert "rate_remaining" in proj_data
    assert proj_data["success"] is True
