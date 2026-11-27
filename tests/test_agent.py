from __future__ import annotations

import json

from tradeagent.agent import ToolAgent, parse_agent_reply


class FakeMCP:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def tools_for_prompt(self, allow=None, limit=24):  # noqa: ANN001
        tools = [
            {"name": "analyze_market", "description": "analysis", "args": {"symbol": {"type": "string", "required": True}}},
            {"name": "propose_order", "description": "order", "args": {}},
        ]
        return [t for t in tools if allow is None or t["name"] in allow]

    def call_tool(self, name, arguments=None):  # noqa: ANN001
        self.calls.append((name, arguments or {}))
        return {"ok": True, "tool": name}


def test_parse_tolerates_fences_and_chatter() -> None:
    assert parse_agent_reply('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_agent_reply('bla bla {"a": {"b": 2}} tail') == {"a": {"b": 2}}
    assert parse_agent_reply("no json here") == {}
    assert parse_agent_reply('{"broken": ') == {}


def test_agent_calls_tool_then_proposes() -> None:
    replies = [
        json.dumps({"analysis": "need data", "tool_calls": [{"tool": "analyze_market", "arguments": {"symbol": "XAUUSD"}}]}),
        json.dumps({"analysis": "trend up", "tool_calls": [], "proposal": {"variant": "momentum", "params": {"breakout_k": 1.1}, "confidence": 0.7}}),
    ]
    seen: list[list[dict]] = []

    def chat(messages):  # noqa: ANN001
        seen.append(messages)
        return replies[len(seen) - 1]

    mcp = FakeMCP()
    trace = ToolAgent(chat=chat, mcp=mcp, max_rounds=3, max_calls=4).run({"cycle": 1})
    assert trace.rounds == 2
    assert mcp.calls == [("analyze_market", {"symbol": "XAUUSD"})]
    assert trace.proposal["confidence"] == 0.7
    assert any(m["role"] == "user" and "Tool results" in m["content"] for m in seen[-1])


def test_blocked_tool_is_refused_not_executed() -> None:
    def chat(messages):  # noqa: ANN001
        return json.dumps(
            {
                "analysis": "x",
                "tool_calls": [{"tool": "propose_order", "arguments": {"symbol": "XAUUSD"}}],
                "proposal": None,
            }
        )

    mcp = FakeMCP()
    trace = ToolAgent(chat=chat, mcp=mcp, max_rounds=1, max_calls=3).run({})
    assert mcp.calls == []
    assert trace.calls and trace.calls[0].ok is False
    assert "مجاز نیست" in trace.calls[0].error


def test_budget_caps_tool_calls() -> None:
    def chat(messages):  # noqa: ANN001
        return json.dumps(
            {
                "analysis": "x",
                "tool_calls": [{"tool": "analyze_market", "arguments": {"symbol": f"S{i}"}} for i in range(5)],
                "proposal": None,
            }
        )

    mcp = FakeMCP()
    trace = ToolAgent(chat=chat, mcp=mcp, max_rounds=2, max_calls=2).run({})
    assert len(mcp.calls) == 2
    assert any("سقف" in c.error for c in trace.calls)


def test_model_failure_is_reported_not_raised() -> None:
    def chat(messages):  # noqa: ANN001
        raise RuntimeError("model down")

    trace = ToolAgent(chat=chat, mcp=FakeMCP()).run({})
    assert trace.proposal is None
    assert "model down" in trace.error


def test_non_json_reply_is_reported() -> None:
    trace = ToolAgent(chat=lambda m: "I think gold goes up", mcp=FakeMCP()).run({})
    assert trace.proposal is None
    assert "JSON" in trace.error


def test_local_tools_are_offered_and_usable() -> None:
    """بدون MT5 هم ایجنت باید ابزار محلی داشته باشد."""
    from tradeagent.agent import ToolAgent

    calls = []

    def local_tick_digest(symbol=None, **_kw):
        calls.append(symbol)
        return {"ret_15s_bp": 3.5, "up_frac_30s": 0.6}

    replies = [
        json.dumps({"analysis": "خواندن تیک", "tool_calls": [{"tool": "local_tick_digest", "arguments": {"symbol": "XAUUSD_i"}}], "proposal": None}),
        json.dumps({"analysis": "شتاب مثبت", "momentum": {"direction": "up", "phase": "accelerating", "strength": 0.5, "horizon_s": 60, "confidence": 0.6},
                    "tool_calls": [], "proposal": {"variant": "momentum", "params": {"breakout_k": 1.1}, "confidence": 0.6}}),
    ]
    seen = {"n": 0}

    def chat(messages):  # noqa: ANN001
        seen["n"] += 1
        return replies[seen["n"] - 1]

    agent = ToolAgent(chat=chat, mcp=None, local_tools={"local_tick_digest": local_tick_digest})
    assert [t["name"] for t in agent.available_tools()] == ["local_tick_digest"]
    trace = agent.run({"cycle": 1})
    assert calls == ["XAUUSD_i"]
    assert trace.calls[0].ok is True
    assert trace.momentum["direction"] == "up"
    assert trace.proposal["params"]["breakout_k"] == 1.1
