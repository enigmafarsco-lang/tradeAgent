from __future__ import annotations

from pathlib import Path

import pytest

from conftest import make_bars
from tradeagent.config import Config
from tradeagent.paper import PaperEngine, Position, Store, Trade
from tradeagent.policy import new_policy
from tradeagent.ticks import Bar, SyntheticSource
from tradeagent.util import utc_day


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    st = Store(tmp_path / "journal.sqlite3")
    yield st
    st.close()


def test_store_bars_roundtrip(store: Store) -> None:
    bars = make_bars([100.0, 100.5, 101.0])
    store.add_bars("BTCUSD", bars)
    store.add_bars("BTCUSD", bars)  # تکراری‌ها نادیده
    got = store.recent_bars("BTCUSD", 10)
    assert len(got) == 3
    assert got[0].close == 100.0
    assert got[-1].close == 101.0
    assert store.bar_count("BTCUSD") == 3


def test_engine_opens_and_closes_with_sl(cfg: Config, store: Store) -> None:
    engine = PaperEngine(cfg, store)
    policy = new_policy("momentum")
    prices = [100.0] * 360 + [101.0]
    bars = make_bars(prices, start=1_700_000_000)
    result = engine.process({"BTCUSD": bars}, policy, cycle=1, now_ts=bars[-1].t)
    assert result.open_positions, "باید یک پوزیشن باز شود"
    pos = result.open_positions[0]
    assert pos["side"] == 1
    assert pos["notional"] > 0

    # قیمت به زیر استاپ می‌رود
    sl_price = pos["sl_price"]
    drop = [sl_price * 0.995] * 5
    crash_bars = make_bars(drop, start=bars[-1].t + 1)
    result2 = engine.process({"BTCUSD": crash_bars}, policy, cycle=2, now_ts=crash_bars[-1].t)
    assert result2.closed_trades, "استاپ باید فعال شود"
    trade = result2.closed_trades[0]
    assert trade["exit_reason"] == "sl"
    assert trade["pnl"] < 0
    assert engine.streak_losses == 1
    assert engine.balance < 10000.0 + 1e-6
    assert not store.load_positions()


def test_both_sl_and_tp_in_same_bar_means_sl(cfg: Config, store: Store) -> None:
    engine = PaperEngine(cfg, store)
    pos = Position(
        symbol="BTCUSD", side=1, entry_ts=100, entry_price=100.0, notional=1000.0,
        risk_amount=10.0, sl_price=99.0, tp_price=101.0, sl_bps=100.0, tp_bps=100.0,
        hold_s=600, variant="momentum", policy_version=1, opened_cycle=1,
    )
    bar = Bar(t=110, open=100, high=102.0, low=98.0, close=100, bid_close=99.99, ask_close=100.01)
    trade = engine._check_exit(pos, bar, cycle=1)
    assert trade is not None
    assert trade.exit_reason == "sl"
    assert trade.pnl < 0


def test_time_exit_and_pnl_accounting(cfg: Config, store: Store) -> None:
    engine = PaperEngine(cfg, store)
    store.set_meta("balance", 10000.0)
    pos = Position(
        symbol="ETHUSD", side=-1, entry_ts=100, entry_price=200.0, notional=2000.0,
        risk_amount=20.0, sl_price=210.0, tp_price=190.0, sl_bps=500.0, tp_bps=500.0,
        hold_s=60, variant="meanrev", policy_version=1, opened_cycle=1,
    )
    store.save_position(pos)
    bar = Bar(t=170, open=200, high=200.5, low=199.0, close=199.0, bid_close=198.99, ask_close=199.01)
    trade = engine._check_exit(pos, bar, cycle=2)
    assert trade is not None
    assert trade.exit_reason == "time"
    assert trade.pnl > 0, "فروش در قیمت پایین‌تر سود است"
    assert trade.r_multiple > 0
    assert engine.balance > 10000.0
    assert engine.streak_wins == 1


def test_position_sizing_respects_risk(cfg: Config, store: Store) -> None:
    engine = PaperEngine(cfg, store)
    policy = new_policy("momentum")
    policy["params"]["sl_atr"] = 1.0
    intent = type("I", (), {"direction": 1, "sl_bps": 50.0, "tp_bps": 80.0, "hold_s": 300,
                            "edge_bps": 2.0, "variant": "momentum", "reason": "t", "as_dict": lambda self: {}})()
    bar = Bar(t=1000, open=100, high=101, low=99, close=100, bid_close=99.99, ask_close=100.01)
    pos = engine._open_position("BTCUSD", bar, intent, policy, 1, balance=10000.0)
    assert pos is not None
    assert pos.risk_amount == pytest.approx(10000 * 0.005, rel=1e-6)
    # ریسک تا استاپ تقریباً برابر risk_amount است
    loss_at_sl = pos.side * (pos.sl_price / pos.entry_price - 1) * pos.notional
    assert abs(loss_at_sl) <= pos.risk_amount * 1.5


def test_daily_loss_stop_blocks_new_entries(cfg: Config, store: Store) -> None:
    engine = PaperEngine(cfg, store)
    bars = make_bars([100.0] * 360 + [101.0])
    store.set_meta("day_key", utc_day(bars[-1].t))
    store.set_meta("day_start_balance", 10000.0)
    store.set_meta("balance", 9600.0)  # -4% در روز
    policy = new_policy("momentum")
    result = engine.process({"BTCUSD": bars}, policy, cycle=1, now_ts=bars[-1].t)
    assert result.entries_blocked is not None
    assert not result.open_positions


def test_max_open_positions(cfg: Config, store: Store) -> None:
    engine = PaperEngine(cfg, store)
    policy = new_policy("momentum")
    fs = SyntheticSource(["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"], seed=21)
    end = 1_700_010_000
    bars = fs.fetch(end_s=end).bars
    result = engine.process(bars, policy, cycle=1, now_ts=end)
    assert len(result.open_positions) <= int(cfg.get("paper.max_open_positions"))


def test_store_stats_and_positions(store: Store) -> None:
    trade = Trade(
        symbol="BTCUSD", side=1, variant="momentum", entry_ts=1, exit_ts=2, entry_price=100.0,
        exit_price=101.0, notional=1000.0, risk_amount=10.0, pnl=10.0, pnl_bps=100.0,
        r_multiple=1.0, exit_reason="tp", sl_bps=100.0, tp_bps=100.0, hold_s=60,
        policy_version=1, cycle=1,
    )
    store.add_trade(trade)
    stats = store.stats()
    assert stats["trades"] == 1
    assert stats["win_rate"] == 100.0
    assert store.per_symbol_stats()[0]["symbol"] == "BTCUSD"


def test_stale_position_is_closed_when_its_feed_dies(cfg: Config, store: Store) -> None:
    """سناریوی میدانی: نماد از فید می‌افتد و پوزیشن ۱۸ ساعت باز می‌ماند."""
    engine = PaperEngine(cfg, store)
    policy = new_policy("momentum")
    prices = [100.0] * 360 + [101.0]
    bars = make_bars(prices, start=1_700_000_000)
    result = engine.process({"BTCUSD": bars}, policy, cycle=1, now_ts=bars[-1].t)
    assert result.open_positions, "باید یک پوزیشن باز شود"
    opened = bars[-1].t
    assert store.load_positions(), "پوزیشن در انبار هست"

    # دیگر هیچ کندلی برای این نماد نمی‌آید؛ فقط زمان می‌گذرد
    later = opened + 18 * 3600
    result2 = engine.process({}, policy, cycle=2, now_ts=later)
    assert result2.closed_trades, "پوزیشن رهاشده باید بسته شود"
    trade = result2.closed_trades[0]
    assert trade["exit_reason"] == "time"
    assert trade["exit_ts"] == later
    assert not store.load_positions(), "دیگر پوزیشن بازی نمانده"
    assert any("رهاشده" in w for w in result2.warnings)
    assert store.stats()["trades"] == 1


def test_stale_position_without_any_history_closes_flat(cfg: Config, store: Store) -> None:
    """اگر هیچ کندلی در انبار نباشد، با قیمت ورود (بدون سود/زیان) بسته می‌شود."""
    engine = PaperEngine(cfg, store)
    pos = Position(
        symbol="XAUUSD",
        side=1,
        entry_price=2000.0,
        notional=1000.0,
        risk_amount=10.0,
        sl_price=1990.0,
        tp_price=2020.0,
        sl_bps=50.0,
        tp_bps=100.0,
        entry_ts=1_700_000_000,
        hold_s=900,
        variant="momentum",
        policy_version=1,
        opened_cycle=4,
    )
    store.save_position(pos)
    warnings: list[str] = []
    closed = engine._sweep_stale_positions({"XAUUSD": pos}, 5, 1_700_000_000 + 3600, warnings)
    assert len(closed) == 1
    assert closed[0].exit_reason == "stale"
    assert closed[0].exit_price == 2000.0
    assert store.load_positions() == {}
    assert warnings and "رهاشده" in warnings[0]
