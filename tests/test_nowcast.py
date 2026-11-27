from __future__ import annotations

import math
import random

from tradeagent.nowcast import MomentumNowcaster, nowcast_symbols
from tradeagent.ticks import Bar


def series(n: int = 1200, drift: float = 0.00002, sigma: float = 0.00006, seed: int = 1) -> list[Bar]:
    rng = random.Random(seed)
    price, out = 2000.0, []
    for i in range(n):
        price *= math.exp(drift + rng.gauss(0, sigma))
        out.append(
            Bar(
                t=1_700_000_000 + i,
                open=price,
                high=price * 1.00001,
                low=price * 0.99999,
                close=price,
                bid_close=price,
                ask_close=price * 1.00002,
                ticks=2,
                volume=1.0,
            )
        )
    return out


def test_trend_series_is_detected() -> None:
    up = MomentumNowcaster().run("XAUUSD", series(drift=0.00002))
    down = MomentumNowcaster().run("XAUUSD", series(drift=-0.00002, seed=3))
    assert up.prob_up > 0.65, up.summary()
    assert down.prob_up < 0.35, down.summary()
    assert up.hit_rate > 0.7
    assert up.expected_bps > 0


def test_random_walk_is_not_overclaimed() -> None:
    """روی گام تصادفی، دقت نباید جادویی بالا باشد (ضد توهم)."""
    res = MomentumNowcaster().run("XAUUSD", series(drift=0.0, seed=7))
    assert 0.3 <= res.hit_rate <= 0.7
    assert res.edge <= 0.5


def test_short_history_returns_neutral() -> None:
    res = MomentumNowcaster().run("XAUUSD", series(n=50))
    assert res.prob_up == 0.5
    assert res.samples_test == 0


def test_nowcast_symbols_shape() -> None:
    out = nowcast_symbols({"XAUUSD_i": series(), "BITCOIN_i": series(seed=5)})
    assert set(out) == {"XAUUSD_i", "BITCOIN_i"}
    item = out["XAUUSD_i"]
    assert {"prob_up", "hit_rate", "samples", "horizon_s"} <= set(item)
    assert isinstance(item["samples"]["test"], int)
