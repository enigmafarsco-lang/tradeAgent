from __future__ import annotations

import math
import random

from tradeagent.nowcast import ForecastLedger, digests_for, tick_digest
from tradeagent.ticks import Bar


def series(n: int = 300, drift: float = 0.0, sigma: float = 0.00006, seed: int = 1) -> list[Bar]:
    rng = random.Random(seed)
    price, out = 2000.0, []
    for i in range(n):
        price *= math.exp(drift + rng.gauss(0, sigma))
        out.append(
            Bar(t=1_700_000_000 + i, open=price, high=price * 1.00001, low=price * 0.99999,
                close=price, bid_close=price, ask_close=price * 1.00002, ticks=2, volume=1.0)
        )
    return out


def test_digest_is_numbers_only() -> None:
    d = tick_digest(series())
    for key in ("last_price", "ret_5s_bp", "ret_30s_bp", "velocity_change_bp_per_s", "up_frac_30s"):
        assert key in d
    assert len(d["last_10_closes"]) == 10
    # هیچ تفسیری/برچسب روند نباید داخل digest باشد
    assert not any(k in d for k in ("direction", "prob_up", "hit_rate"))


def test_digest_tracks_a_trend_but_does_not_judge_it() -> None:
    up = tick_digest(series(drift=0.00005))
    down = tick_digest(series(drift=-0.00005, seed=4))
    assert up["ret_60s_bp"] > 0 > down["ret_60s_bp"]
    assert up["up_frac_60s"] > 0.5 > down["up_frac_60s"]


def test_digest_empty_and_short_input() -> None:
    assert tick_digest([]) == {}
    assert len(digests_for({"X": series(3)})) == 1


def test_ledger_records_and_scores_the_agents_call(tmp_path) -> None:
    ledger = ForecastLedger(path=tmp_path / "f.json")
    verdict = {"direction": "up", "confidence": 0.8, "horizon_s": 60, "strength": 0.5, "phase": "accelerating"}
    rec = ledger.record("XAUUSD_i", verdict, price=2000.0, now_ts=1000)
    assert rec is not None and rec.direction == "up"
    # هنوز افق نرسیده
    assert ledger.score({"XAUUSD_i": 2001.0}, now_ts=1030)["n"] == 0
    # افق رسید و قیمت بالا رفت → درست
    stats = ledger.score({"XAUUSD_i": 2002.0}, now_ts=1100)
    assert stats["n"] == 1 and stats["hit_rate"] == 1.0
    ledger.save()
    again = ForecastLedger(path=tmp_path / "f.json").load()
    assert len(again.rows) == 1 and again.rows[0].hit is True


def test_ledger_marks_a_wrong_call(tmp_path) -> None:
    ledger = ForecastLedger(path=tmp_path / "f.json")
    ledger.record("XAUUSD_i", {"direction": "down", "confidence": 0.9, "horizon_s": 30}, 2000.0, 0)
    stats = ledger.score({"XAUUSD_i": 2010.0}, now_ts=100)
    assert stats["n"] == 1 and stats["hit_rate"] == 0.0


def test_ledger_flat_call_needs_a_real_move(tmp_path) -> None:
    ledger = ForecastLedger(path=tmp_path / "f.json")
    ledger.record("XAUUSD_i", {"direction": "flat", "confidence": 0.6, "horizon_s": 30}, 2000.0, 0)
    assert ledger.score({"XAUUSD_i": 2000.02}, now_ts=100)["hit_rate"] == 1.0
