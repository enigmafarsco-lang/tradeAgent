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


def test_patch_script_fixes_the_known_connector_error(tmp_path) -> None:
    """اکسپرت trade-mcp تابع LongToString را صدا می‌زند ولی تعریف نکرده است."""
    import subprocess
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "scripts" / "patch_trade_mcp_connector.sh"
    assert src.exists(), "patch script must exist"
    body = src.read_text(encoding="utf-8")
    # تابع کمکی و نقطهٔ درج بعد از include باید در اسکریپت باشد
    assert "string LongToString(const long value)" in body
    assert 'stripped.startswith("#include")' in body


def test_free_text_reply_gets_one_json_repair_attempt() -> None:
    """مدل ۷B گاهی متن می‌نویسد؛ ایجنت باید یک بار «فقط JSON» را یادآوری کند."""
    from tradeagent.agent import ToolAgent

    replies = [
        "قیمت دارد بالا می‌رود و به نظرم خرید خوب است.",
        json.dumps(
            {
                "analysis": "شتاب مثبت در ۹۰ ثانیه",
                "tool_calls": [],
                "momentum": {
                    "direction": "up",
                    "phase": "accelerating",
                    "strength": 0.6,
                    "horizon_s": 90,
                    "confidence": 0.55,
                },
                "proposal": None,
            }
        ),
    ]
    seen = {"n": 0}
    sent: list[list[dict[str, str]]] = []

    def chat(messages):  # noqa: ANN001
        sent.append(list(messages))
        seen["n"] += 1
        return replies[seen["n"] - 1]

    agent = ToolAgent(chat=chat, mcp=None)
    trace = agent.run({"cycle": 1})

    assert trace.error == ""
    assert trace.repairs == 1
    assert trace.momentum["phase"] == "accelerating"
    # پیام ترمیمی واقعاً به مدل رسیده است
    assert any("JSON" in m["content"] for m in sent[1] if m["role"] == "user")


def test_run_gold_never_fights_its_own_service(tmp_path) -> None:
    """سرویس باید صبر کند تا نمونهٔ قبلی واقعاً بمیرد، بعد بالا بیاید."""
    from pathlib import Path

    body = (Path(__file__).resolve().parents[1] / "scripts" / "run_gold.sh").read_text(encoding="utf-8")
    # الگوی پروسه لنگر دارد، وگرنه شل‌های دیگر هم گرفته می‌شدند
    assert 'pgrep -f "^$ROOT/.venv/bin/tradeagent"' in body
    # تا خروج واقعی نمونهٔ قبلی صبر می‌کند
    assert "kill -0" in body and "همه بسته شدند" in body
    # قفل زنده را پاک نمی‌کند
    assert 'if [[ -n "${PID:-}" ]] && kill -0 "$PID" 2>/dev/null; then' in body
    # آمادگی با فایل وضعیت پل سنجیده می‌شود، نه فقط is-active
    assert "var/state/mcp_status.json" in body


def test_run_gold_finds_status_file_where_the_loop_writes_it() -> None:
    """حلقه در state_dir می‌نویسد (پیش‌فرض var)؛ اسکریپت نباید فقط var/state را ببیند."""
    from pathlib import Path

    body = (Path(__file__).resolve().parents[1] / "scripts" / "run_gold.sh").read_text(encoding="utf-8")
    assert "status_file()" in body
    assert "var/mcp_status.json var/state/mcp_status.json" in body
    assert "ea_connected" in body


def test_contract_asks_the_model_for_a_momentum_verdict() -> None:
    """حکم تکانه باید صریح در قرارداد باشد، نه شانسی از متن skill."""
    from tradeagent.agent import CONTRACT

    for key in ("momentum", "accelerating", "fading", "horizon_s", "strength"):
        assert key in CONTRACT, key
    assert "Persian" in CONTRACT and "فارسی" in CONTRACT


def test_run_gold_has_a_restart_mode() -> None:
    from pathlib import Path

    body = (Path(__file__).resolve().parents[1] / "scripts" / "run_gold.sh").read_text(encoding="utf-8")
    assert '--restart) MODE="restart"' in body
    assert "systemctl --user restart tradeagent-gold.service" in body


def test_run_gold_status_reports_mt5_itself() -> None:
    """اگر اکسپرت وصل نیست، باید بگوید متاتریدر اصلاً باز است یا نه."""
    from pathlib import Path

    body = (Path(__file__).resolve().parents[1] / "scripts" / "run_gold.sh").read_text(encoding="utf-8")
    assert "mt5_report()" in body
    assert "from tradeagent.diag import report" in body
    diag = (Path(__file__).resolve().parents[1] / "src" / "tradeagent" / "diag.py").read_text(encoding="utf-8")
    assert "terminal64.exe" in diag and "NOT running" in diag
    assert "Experts log" in diag


def test_mt5_logs_are_looked_for_in_both_layouts() -> None:
    """نصب پرتابل در Program Files است، ولی نصب معمولی لاگ را در AppData می‌نویسد."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src" / "tradeagent"
    body = (root / "diag.py").read_text(encoding="utf-8")
    assert "AppData/Roaming/MetaQuotes/Terminal" in body
    assert "Program Files/MetaTrader 5/MQL5/Logs/*.log" in body
    watch = (Path(__file__).resolve().parents[1] / "scripts" / "connect_trade_mcp.sh").read_text(encoding="utf-8")
    assert "from tradeagent.diag import ea_log" in watch
