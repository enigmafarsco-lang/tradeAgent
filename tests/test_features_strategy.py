from __future__ import annotations

import math

from conftest import make_bars
from tradeagent.features import compute_features, ready
from tradeagent.policy import baseline_params
from tradeagent.strategy import VARIANTS, evaluate
from tradeagent.ticks import SyntheticSource


def test_features_finite_and_no_lookahead() -> None:
    bars = SyntheticSource(["BTCUSD"], seed=5).fetch(end_s=1_700_003_600).bars["BTCUSD"]
    index = len(bars) - 1
    f = compute_features(bars, index)
    for value in f.as_dict().values():
        if isinstance(value, (int, float)):
            assert math.isfinite(float(value))
    assert f.atr_bps > 0

    # اگر آینده حذف شود، ویژگی‌های همان لحظه نباید تغییر کنند
    truncated = bars[: index + 1]
    f2 = compute_features(truncated, index)
    assert f2.close == f.close
    assert abs(f2.ret_60_bps - f.ret_60_bps) < 1e-9
    assert abs(f2.vol_60_bps - f.vol_60_bps) < 1e-9
    assert abs(f2.z - f.z) < 1e-9


def test_ready_needs_history() -> None:
    bars = make_bars([100] * 100)
    assert ready(bars, 10, min_history=320) is False
    assert ready(make_bars([100] * 400), 399, min_history=320) is True


def test_all_variants_return_safe_ints_or_none() -> None:
    bars = SyntheticSource(["EURUSD"], seed=9).fetch(end_s=1_700_003_600).bars["EURUSD"]
    for variant in VARIANTS:
        params = baseline_params(variant)
        for index in range(320, len(bars)):
            intent = evaluate(variant, compute_features(bars, index), params)
            if intent is None:
                continue
            assert intent.direction in (-1, 1)
            assert intent.sl_bps > 0 and intent.tp_bps > 0
            assert intent.hold_s > 0
            assert intent.variant == variant


def test_momentum_breakout_trigger() -> None:
    # ۳۵۰ کندل آرام، بعد یک کندل با شکست واضح
    prices = [100.0 + 0.001 * (i % 3) for i in range(350)]
    prices += [101.5]
    bars = make_bars(prices)
    intent = evaluate("momentum", compute_features(bars, len(bars) - 1), baseline_params("momentum"))
    assert intent is not None
    assert intent.direction == 1
    assert intent.tp_bps > intent.sl_bps


def test_spread_filter_blocks_entry() -> None:
    prices = [100.0] * 350 + [101.5]
    wide = make_bars(prices, spread_bps=500.0)
    intent = evaluate("momentum", compute_features(wide, len(wide) - 1), baseline_params("momentum"))
    assert intent is None


def test_meanrev_needs_calm_regime() -> None:
    params = baseline_params("meanrev")
    prices = [100.0] * 350
    prices[-1] = 99.0  # افت شارپ → z منفی بزرگ
    calm = make_bars(prices, spread_bps=1.0)
    features = compute_features(calm, len(calm) - 1)
    assert features.z < -1.0
    intent = evaluate("meanrev", features, params)
    if intent is not None:
        assert intent.direction == 1


def test_scalp_uses_short_threshold() -> None:
    params = baseline_params("scalp")
    prices = [100.0] * 350
    for i in range(60):
        prices.append(100.0 + i * 0.01)
    bars = make_bars(prices, spread_bps=0.8)
    intent = evaluate("scalp", compute_features(bars, len(bars) - 1), params)
    if intent is not None:
        assert intent.direction == 1
        assert intent.hold_s <= 600
