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
        return replies[min(seen["n"] - 1, len(replies) - 1)]

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
        return replies[min(seen["n"] - 1, len(replies) - 1)]

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


def test_verify_mode_runs_one_cycle_with_mt5() -> None:
    """بعد از اتصال، باید بتوان بدون انتظار ۵ دقیقه یک چرخهٔ واقعی دید."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts" / "run_gold.sh").read_text(encoding="utf-8")
    assert '--verify) MODE="verify"' in script
    assert "loop --once --llm" in script
    cli = (root / "src" / "tradeagent" / "cli.py").read_text(encoding="utf-8")
    assert '"--once"' in cli and "force_llm" in cli


def test_agent_success_is_reported_as_llm_ok(cfg) -> None:
    """اگر ایجنت حکم داد، گزارش باید llm=true بدهد، نه false."""
    from tradeagent.loop import Runner

    runner = Runner(cfg)

    class FakeAgent:
        def run(self, context, skill_text=""):  # noqa: ANN001, ANN201
            from tradeagent.agent import AgentTrace

            trace = AgentTrace()
            trace.analysis = "شتاب صعودی"
            trace.momentum = {
                "direction": "up",
                "phase": "accelerating",
                "strength": 0.6,
                "horizon_s": 60,
                "confidence": 0.6,
                "reason": "ret_15s مثبت",
            }
            return trace

    runner.agent = FakeAgent()
    runner.advisor.should_consult = lambda cycle, force=False: True  # noqa: ARG005
    result = runner.run_cycle(now_ts=1_700_000_000, publish=False, force_llm=True)
    assert result["ok"] is True
    compact = result.get("compact") or {}
    assert compact.get("llm") is True
    # حکم تکانه در گزارش همان چرخه ثبت می‌شود
    latest = (run_path := __import__("pathlib").Path(result["report_md"]))
    assert "accelerating" in latest.read_text(encoding="utf-8")


def test_start_mt5_script_detaches_from_the_terminal() -> None:
    from pathlib import Path

    body = (Path(__file__).resolve().parents[1] / "scripts" / "start_mt5.sh").read_text(encoding="utf-8")
    assert "setsid" in body and "disown" in body
    assert "--check" in body
    assert "from tradeagent.diag import mt5_pids" in body


def test_mt5_crash_output_is_kept(tmp_path, monkeypatch) -> None:
    """خروجی wine نباید دور ریخته شود؛ دلیل بسته شدن متاتریدر همان‌جاست."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    body = (root / "scripts" / "start_mt5.sh").read_text(encoding="utf-8")
    assert "var/logs/mt5.log" in body
    assert '>>"$LOG" 2>&1' in body

    import os

    monkeypatch.chdir(tmp_path)
    (tmp_path / "var" / "logs").mkdir(parents=True)
    (tmp_path / "var" / "logs" / "mt5.log").write_text("err: crash\n", encoding="utf-8")
    from tradeagent.diag import mt5_wine_log

    assert mt5_wine_log() == ["err: crash"]
    assert os.getcwd() == str(tmp_path)


def test_shortcut_installer_writes_a_launchable_desktop_file(tmp_path) -> None:
    import os
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "install_mt5_shortcut.sh"
    body = script.read_text(encoding="utf-8")
    assert "--watch" in body
    assert "--watch" in (root / "scripts" / "start_mt5.sh").read_text(encoding="utf-8")

    env = {**os.environ, "HOME": str(tmp_path)}
    subprocess.run(["bash", str(script)], check=True, env=env, cwd=root, capture_output=True)
    desk = tmp_path / "Desktop" / "tradeagent-mt5.desktop"
    text = desk.read_text(encoding="utf-8")
    assert "Exec=" in text and str(root / "scripts" / "start_mt5.sh") in text
    assert "Terminal=false" in text  # کلیک روی آیکون نباید پنجرهٔ کنسول باز کند
    assert os.access(desk, os.X_OK)


def test_agent_asks_again_when_momentum_is_missing() -> None:
    """مدل کوچک گاهی فقط analysis می‌دهد؛ باید یک بار دیگر خواسته شود، نه سکوت."""
    replies = [
        '{"analysis": "داده کم است", "tool_calls": [], "proposal": null}',
        '{"analysis": "تیک‌ها صعودی‌اند", "tool_calls": [{"tool": "local_tick_digest", '
        '"arguments": {}}], "momentum": {"direction": "up", "phase": "steady"}, "proposal": null}',
        '{"analysis": "صعودی", "tool_calls": [], "momentum": {"direction": "up", '
        '"phase": "steady", "strength": 0.6}, "proposal": null}',
    ]
    seen: list[list[dict[str, str]]] = []

    def chat(messages):
        seen.append(list(messages))
        return replies[min(len(seen) - 1, len(replies) - 1)]

    agent = ToolAgent(chat=chat, mcp=None, max_rounds=3, max_calls=4)
    trace = agent.run({"symbol": "XAUUSD_i"})
    assert len(seen) >= 2, "باید دوباره از مدل پرسیده شود"
    assert "missing" in seen[1][-1]["content"]
    assert trace.momentum and trace.momentum["direction"] == "up"


def test_contract_survives_percent_formatting() -> None:
    """قرارداد با % فرمت می‌شود؛ هر % اضافی در متن آن را می‌شکند."""
    from tradeagent.agent import CONTRACT

    text = CONTRACT % {"max_calls": 6}
    assert "at most 6 tools" in text
    assert "basis points" in text


def test_read_only_mcp_tools_are_allowed_but_orders_are_not() -> None:
    """MovingAverage/RelativeStrengthIndex فقط عدد می‌دهند؛ نباید رد شوند."""
    from tradeagent.agent import is_allowed_tool

    for name in ("MovingAverage", "RelativeStrengthIndex", "get_candles", "account_info"):
        assert is_allowed_tool(name, None, set()), name
    for name in ("propose_order", "close_position", "set_policy", "confirm_proposal"):
        assert not is_allowed_tool(name, None, set()), name


def test_analysis_as_object_is_flattened() -> None:
    """مدل گاهی analysis را آبجکت می‌دهد؛ گزارش نباید dict نشان دهد."""
    replies = [
        json.dumps(
            {
                "analysis": {"price_trend": "صعودی", "spread": "کم"},
                "tool_calls": [{"tool": "local_tick_digest", "arguments": {}}],
                "momentum": {"direction": "up", "phase": "steady"},
                "proposal": None,
            }
        )
    ]
    agent = ToolAgent(
        chat=lambda m: replies[0],
        mcp=None,
        local_tools={"local_tick_digest": lambda **_: {"last_price": 1.0}},
        max_rounds=1,
    )
    trace = agent.run({})
    assert trace.analysis == "price_trend: صعودی؛ spread: کم"


def test_unknown_tool_error_lists_the_real_tools() -> None:
    """مدل نام ابزار از خودش می‌سازد؛ خطا باید فهرست واقعی را بدهد."""
    replies = [
        json.dumps(
            {
                "analysis": "تحلیل فارسی",
                "tool_calls": [{"tool": "MovingAverage", "arguments": {"symbol": "XAUUSD_i"}}],
                "momentum": {"direction": "up", "phase": "steady"},
                "proposal": None,
            }
        )
    ]
    agent = ToolAgent(
        chat=lambda m: replies[0],
        mcp=None,
        local_tools={"local_tick_digest": lambda **_: {"last_price": 1.0}},
        max_rounds=1,
    )
    trace = agent.run({})
    call = trace.calls[0]
    assert call.ok is False
    assert "local_tick_digest" in call.error


def test_english_analysis_gets_one_persian_reminder() -> None:
    english = json.dumps(
        {
            "analysis": "The price is going up fast.",
            "tool_calls": [{"tool": "local_tick_digest", "arguments": {}}],
            "momentum": {"direction": "up", "phase": "accelerating"},
            "proposal": None,
        }
    )
    persian = json.dumps(
        {
            "analysis": "قیمت با شتاب بالا می‌رود.",
            "tool_calls": [],
            "momentum": {"direction": "up", "phase": "accelerating"},
            "proposal": None,
        }
    )
    seen: list[list[dict[str, str]]] = []

    def chat(messages):  # noqa: ANN001
        seen.append(list(messages))
        return english if len(seen) == 1 else persian

    agent = ToolAgent(
        chat=chat,
        mcp=None,
        local_tools={"local_tick_digest": lambda **_: {"last_price": 1.0}},
        max_rounds=3,
    )
    trace = agent.run({})
    assert len(seen) >= 2
    assert "Persian" in seen[1][-1]["content"]
    assert trace.analysis.startswith("قیمت")


def test_tool_result_from_another_instrument_is_flagged() -> None:
    """ابزاری که با «XAUUSD» قیمت ۱۸۳۹ می‌دهد نباید نظر مدل را عوض کند."""
    replies = [
        json.dumps(
            {
                "analysis": "تحلیل",
                "tool_calls": [{"tool": "market_snapshot", "arguments": {"symbol": "XAUUSD"}}],
                "momentum": {"direction": "up", "phase": "steady"},
                "proposal": None,
            }
        ),
        json.dumps(
            {
                "analysis": "قیمت ۳۶۳۶ است",
                "tool_calls": [],
                "momentum": {"direction": "up", "phase": "steady"},
                "proposal": None,
            }
        ),
    ]

    class MCP:
        def tools_for_prompt(self, allow=None, limit=24):  # noqa: ANN001
            return [{"name": "market_snapshot", "description": "snap", "args": {}}]

        def call_tool(self, name, arguments=None):  # noqa: ANN001
            return {"symbol": "XAUUSD", "bid": 1839.5, "ask": 1839.6}

    seen: list[list[dict[str, str]]] = []

    def chat(messages):  # noqa: ANN001
        seen.append(list(messages))
        return replies[min(len(seen) - 1, 1)]

    agent = ToolAgent(chat=chat, mcp=MCP(), price_ref=lambda: 3636.0, max_rounds=3)
    trace = agent.run({})
    assert trace.calls[0].note, "باید هشدار ناهم‌مقیاس بودن داشته باشد"
    assert "3636" in trace.calls[0].note
    assert "note" in seen[1][-1]["content"]


def test_final_round_only_asks_for_the_verdict() -> None:
    """اگر مدل در دورهای ابزاری حکم نداد، یک دور آخر «فقط حکم» گرفته شود."""
    tool_reply = json.dumps(
        {
            "analysis": "تحلیل",
            "tool_calls": [{"tool": "local_tick_digest", "arguments": {}}],
            "momentum": None,
            "proposal": None,
        }
    )
    verdict = json.dumps(
        {
            "analysis": "قیمت صعودی است",
            "tool_calls": [],
            "momentum": {"direction": "up", "phase": "accelerating", "strength": 0.6,
                         "horizon_s": 120, "confidence": 0.7, "reason": "شتاب"},
            "proposal": None,
        }
    )
    seen: list[list[dict[str, str]]] = []

    def chat(messages):  # noqa: ANN001
        seen.append(list(messages))
        return tool_reply if "ابزار تمام شد" not in messages[-1]["content"] else verdict

    agent = ToolAgent(
        chat=chat,
        mcp=None,
        local_tools={"local_tick_digest": lambda **_: {"last_price": 1.0}},
        max_rounds=2,
    )
    trace = agent.run({})
    assert trace.momentum and trace.momentum["direction"] == "up"
    assert any("ابزار تمام شد" in m["content"] for m in seen[-1])


def test_unknown_tool_lists_every_name() -> None:
    from tradeagent.agent import ToolAgent

    agent = ToolAgent(
        chat=lambda m: json.dumps(
            {
                "analysis": "x",
                "tool_calls": [{"tool": "BollingerBands", "arguments": {}}],
                "momentum": {"direction": "flat", "phase": "unclear"},
                "proposal": None,
            }
        ),
        mcp=None,
        local_tools={"local_tick_digest": lambda **_: {}},
        max_rounds=1,
    )
    trace = agent.run({})
    assert "local_tick_digest" in trace.calls[0].error
