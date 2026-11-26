from __future__ import annotations

from tradeagent.paper import CycleResult
from tradeagent.policy import new_policy
from tradeagent.report import (agent_checklist, render_compact, render_cycle_json, render_daily,
                               render_markdown, render_readme, render_state, jsonl_line)


def make_result(**overrides) -> CycleResult:
    base = dict(
        cycle=7,
        ts="2026-11-26T10:05:00Z",
        ts_epoch=1_764_151_500,
        source="harvester",
        policy=new_policy("momentum"),
        previous_policy=new_policy("momentum"),
        decisions=[
            {
                "ts": "2026-11-26T10:05:00Z",
                "cycle": 7,
                "source": "adaptive",
                "action": "tighten",
                "level_before": 0,
                "level_after": 1,
                "reason": "۲ ضرر پشت‌سرهم",
                "changes": {"variant": ["momentum", "momentum"], "params": {"breakout_k": [1.0, 1.25]}},
            }
        ],
        closed_trades=[
            {
                "symbol": "BTCUSD", "side": 1, "variant": "momentum", "entry_ts": 100, "exit_ts": 160,
                "entry_price": 100.0, "exit_price": 99.0, "notional": 5000.0, "risk_amount": 50.0,
                "pnl": -50.0, "pnl_bps": -100.0, "r_multiple": -1.0, "exit_reason": "sl",
                "sl_bps": 100.0, "tp_bps": 160.0, "hold_s": 900, "policy_version": 1, "cycle": 7,
            }
        ],
        open_positions=[
            {"symbol": "ETHUSD", "side": -1, "entry_ts": 100, "entry_price": 200.0, "notional": 1000.0,
             "risk_amount": 10.0, "sl_price": 202.0, "tp_price": 196.0, "sl_bps": 100.0, "tp_bps": 200.0,
             "hold_s": 600, "variant": "momentum", "policy_version": 1, "opened_cycle": 7,
             "last_price": 199.0, "unrealized": 5.0, "age_s": 60}
        ],
        balance=9950.0,
        equity=9955.0,
        peak_equity=10000.0,
        drawdown_pct=0.45,
        starting_balance=10000.0,
        overall_stats={"trades": 3, "wins": 1, "losses": 2, "win_rate": 33.33, "net_pnl": -45.0, "avg_r": -0.3},
        today_stats={"trades": 2, "wins": 1, "losses": 1, "win_rate": 50.0, "net_pnl": 5.0},
        per_symbol=[{"symbol": "BTCUSD", "trades": 2, "wins": 1, "win_rate": 50.0, "net_pnl": -10.0, "avg_bps": -5.0}],
        ingest=[
            {"symbol": "BTCUSD", "new_ticks": 1200, "new_bars": 300, "last_tick_ts": 1_764_151_490,
             "lag_s": 10.0, "files": 1, "stale": False, "errors": []},
            {"symbol": "ETHUSD", "new_ticks": 0, "new_bars": 0, "last_tick_ts": 1_764_000_000,
             "lag_s": 151500.0, "files": 1, "stale": True, "errors": []},
        ],
        warnings=["ETHUSD کهنه است"],
        llm_note=None,
        llm_meta={"provider": "gemini", "model": "gemini-2.5-flash", "ok": False, "error": "quota"},
        bars_processed=300,
        extra={"next_policy": new_policy("meanrev"), "streak_wins": 0, "streak_losses": 2,
               "publish": {"action": "pushed", "hash": "abc1234", "branch": "reports"}},
    )
    base.update(overrides)
    return CycleResult(**base)


def test_markdown_has_all_sections() -> None:
    md = render_markdown(make_result())
    for heading in ("## ۱) خلاصه", "## ۲) معاملات", "## ۳) پوزیشن‌های باز", "## ۴) سیاست",
                    "## ۵) کیفیت داده", "## ۶) مدل زبانی", "## ۷) هشدارها", "## ۸) برای ایجنت"):
        assert heading in md
    assert "BTCUSD" in md
    assert "نردبان قطعی" in md
    assert "abc1234" in md
    assert "کاغذی" in md


def test_agent_checklist_flags_stale_and_drawdown() -> None:
    result = make_result()
    items = agent_checklist(result)
    assert any("ETHUSD" in item for item in items)
    assert any("هشدار" in item for item in items)


def test_compact_and_cycle_json_are_serializable() -> None:
    result = make_result()
    compact = render_compact(result)
    assert compact["cycle"] == 7
    assert compact["trades_closed_cycle"] == 1
    assert compact["warnings"] == 1
    line = jsonl_line(compact)
    assert line.count("\n") == 0
    payload = render_cycle_json(result, {"next_policy": {"variant": "meanrev"}})
    assert payload["schema"] == 1
    assert payload["compact"]["cycle"] == 7
    assert payload["extra"]["next_policy"]["variant"] == "meanrev"


def test_daily_and_state_and_readme() -> None:
    rows = [render_compact(make_result(cycle=i, ts=f"2026-11-26T10:0{i}:00Z")) for i in range(3)]
    daily = render_daily(rows, "2026-11-26")
    assert daily.count("| ") >= 3
    state = render_state(make_result())
    assert state["cycle"] == 7
    assert state["policy"]["variant"] == "momentum"
    assert "latest.md" in render_readme("reports/paper")


def test_empty_trades_renders_gracefully() -> None:
    md = render_markdown(make_result(closed_trades=[], open_positions=[], decisions=[]))
    assert "معامله‌ای بسته نشد" in md
    assert "بدون تغییر سیاست برای چرخه‌ی بعد" in md
