from __future__ import annotations

from tradeagent.policy import (AdaptiveController, BASELINES, PARAM_BOUNDS, build_policy_for_level,
                              clamp_params, describe, new_policy, validate_proposal)
from tradeagent.strategy import VARIANTS


def test_clamp_params_respects_bounds() -> None:
    raw = {"breakout_k": 99, "sl_atr": -5, "rr": 1.0, "hold_s": 10, "unknown": 3}
    out = clamp_params(raw)
    assert out["breakout_k"] == PARAM_BOUNDS["breakout_k"][1]
    assert out["sl_atr"] == PARAM_BOUNDS["sl_atr"][0]
    assert out["hold_s"] == PARAM_BOUNDS["hold_s"][0]
    assert "unknown" not in out
    assert isinstance(out["hold_s"], int)


def test_new_policy_starts_from_baseline() -> None:
    policy = new_policy("meanrev")
    assert policy["variant"] == "meanrev"
    assert policy["params"]["z_entry"] == BASELINES["meanrev"]["z_entry"]
    assert policy["level"] == 0
    assert "بازگشت به میانگین" in describe(policy)


def test_build_policy_for_levels_is_deterministic_and_switches_variant() -> None:
    p0 = build_policy_for_level("momentum", 0)
    p1 = build_policy_for_level("momentum", 1)
    p1b = build_policy_for_level("momentum", 1)
    assert p0["variant"] == "momentum"
    assert p1["params"] == p1b["params"]
    assert p1["params"]["breakout_k"] > p0["params"]["breakout_k"]

    p4 = build_policy_for_level("momentum", 4)
    assert p4["variant"] != "momentum", "پله ۴ باید خانواده استراتژی را عوض کند"
    assert p4["variant"] in VARIANTS

    p5 = build_policy_for_level("momentum", 5)
    assert p5["risk_scale"] == 0.5


def test_adaptive_controller_ladder_and_cooldown() -> None:
    ctrl = AdaptiveController(adapt_after_losses=2, cooldown_cycles=2, recover_after_wins=2, max_level=5)
    policy = new_policy("momentum")

    policy, decision = ctrl.maybe_adapt(policy, streak_losses=1, streak_wins=0, cycle=1)
    assert decision is None and policy["level"] == 0

    policy, decision = ctrl.maybe_adapt(policy, streak_losses=2, streak_wins=0, cycle=2)
    assert decision is not None
    assert decision.action == "tighten"
    assert policy["level"] == 1

    # کول‌داون: دو چرخه بعد نمی‌تواند دوباره بالا برود
    policy, decision = ctrl.maybe_adapt(policy, streak_losses=5, streak_wins=0, cycle=3)
    assert decision is None and policy["level"] == 1

    policy, decision = ctrl.maybe_adapt(policy, streak_losses=5, streak_wins=0, cycle=4)
    assert decision is not None and policy["level"] == 2

    # بردهای پشت‌سرهم → عقب‌نشینی
    policy, decision = ctrl.maybe_adapt(policy, streak_losses=0, streak_wins=2, cycle=6)
    assert decision is not None and decision.action == "recover"
    assert policy["level"] == 1


def test_validate_proposal_rejects_unsafe() -> None:
    current = new_policy("momentum")
    bad_variant, notes = validate_proposal({"variant": "martingale", "confidence": 0.9}, current)
    assert bad_variant is None
    assert notes

    low_conf, _ = validate_proposal({"variant": "meanrev", "confidence": 0.1}, current)
    assert low_conf is None

    nothing, _ = validate_proposal({"params": {}, "confidence": 0.9}, current)
    assert nothing is None


def test_validate_proposal_clamps_big_moves() -> None:
    current = new_policy("momentum")
    proposed = {
        "variant": "momentum",
        "params": {"breakout_k": 99.0, "min_edge_bps": 100.0},
        "reason": "تست",
        "confidence": 0.8,
    }
    new, notes = validate_proposal(proposed, current, cycle=5)
    assert new is not None
    base_k = current["params"]["breakout_k"]
    assert new["params"]["breakout_k"] <= base_k * 1.2 + 1e-9
    assert new["params"]["breakout_k"] <= PARAM_BOUNDS["breakout_k"][1]
    assert any("محدود" in n for n in notes)
    assert new["source"] == "llm"
    assert new["llm_confidence"] == 0.8
    assert new["version"] == current["version"] + 1


def test_validate_proposal_accepts_small_change_and_ignores_unknown_keys() -> None:
    current = new_policy("meanrev")
    proposed = {
        "variant": "meanrev",
        "params": {"z_entry": current["params"]["z_entry"] * 1.1},
        "reason": "نوسان کم شده",
        "confidence": 0.7,
        "risk_per_trade_pct": 90,
    }
    new, notes = validate_proposal(proposed, current, cycle=3)
    assert new is not None
    assert new["params"]["z_entry"] > current["params"]["z_entry"]
    assert "risk_per_trade_pct" not in new["params"]
    assert any("کلیدهای نادیده" in n for n in notes)


def test_validate_proposal_never_accepts_risk_keys() -> None:
    current = new_policy("momentum")
    new, _ = validate_proposal({"params": {"risk_scale": 12}, "confidence": 0.95}, current)
    # risk_scale پارامتر استراتژی نیست؛ فقط در سطح سیاست است و نباید از params بیاید
    assert new is None or new.get("risk_scale") == 1.0
