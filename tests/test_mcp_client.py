from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tradeagent.mcp_client import MCPClient, MCPError

SERVER = [sys.executable, str(Path(__file__).with_name("fake_mcp_server.py"))]


@pytest.fixture()
def client():
    c = MCPClient(SERVER, timeout_s=10)
    assert c.start(), "fake MCP server should start"
    yield c
    c.close()


def test_handshake_lists_tools_and_prompts(client: MCPClient) -> None:
    assert client.server_info.get("name") == "fake-mcp"
    names = [t["name"] for t in client.tools]
    assert "analyze_market" in names and "propose_order" in names
    assert [p["name"] for p in client.prompts] == ["gold_session_briefing"]


def test_call_tool_returns_structured_json(client: MCPClient) -> None:
    out = client.call_tool("analyze_market", {"symbol": "XAUUSD"})
    assert out["ok"] is True and out["tool"] == "analyze_market"
    assert out["args"] == {"symbol": "XAUUSD"}


def test_error_results_are_raised(client: MCPClient) -> None:
    with pytest.raises(MCPError):
        client.call_tool("is_error", {})
    with pytest.raises(MCPError):
        client.call_tool("explode", {})


def test_prompt_text_is_fetched(client: MCPClient) -> None:
    assert client.get_prompt("gold_session_briefing", {"symbol": "XAUUSD"}) == "GOLD SKILL TEXT"


def test_tools_for_prompt_filters_and_summarises(client: MCPClient) -> None:
    tools = client.tools_for_prompt(allow=["analyze_market"])
    assert [t["name"] for t in tools] == ["analyze_market"]
    assert tools[0]["args"]["symbol"]["required"] is True


def test_disabled_client_is_harmless() -> None:
    c = MCPClient(None, enabled=True)
    assert c.start() is False
    assert c.tools == []
    with pytest.raises(MCPError):
        c.call_tool("anything", {})


def test_missing_binary_does_not_raise() -> None:
    c = MCPClient("definitely-not-installed-xyz serve", timeout_s=2)
    assert c.start() is False
    assert c.enabled is False
