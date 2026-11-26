"""سیاست معاملاتی: پارامترهای پایه، نردبان سازگاری و اعتبارسنجی پیشنهاد مدل.

اصل طراحی: تغییر سیاست باید **قابل توضیح، محدود و برگشت‌پذیر** باشد.

* هر پله‌ی نردبان یک تبدیل صریح روی پارامترهاست (نه «حس» مدل).
* کل پیکربندی از روی ``level`` بازسازی می‌شود؛ پس وضعیت هرگز خراب نمی‌شود.
* پیشنهاد مدل زبانی فقط می‌تواند variant را از فهرست مجاز عوض کند و پارامترها را
  حداکثر ±۲۰٪ جابه‌جا کند؛ سقف ریسک و لیست سفید دست‌نخورده می‌ماند.
"""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
from typing import Any

from .strategy import VARIANTS
from .util import LOG, clamp, round_sig, utc_iso

# بازه‌ی مجاز هر پارامتر (هیچ مسیری نمی‌تواند از این بیرون برود)
PARAM_BOUNDS: dict[str, tuple[float, float]] = {
    "breakout_k": (0.3, 3.0),
    "sl_atr": (0.5, 4.0),
    "rr": (0.5, 4.0),
    "hold_s": (60.0, 14400.0),
    "min_edge_bps": (0.0, 30.0),
    "max_spread_bps": (0.5, 40.0),
    "cooldown_s": (0.0, 7200.0),
    "z_entry": (0.5, 4.0),
    "max_vol_bps": (2.0, 300.0),
    "lookback_s": (60.0, 1800.0),
}
INT_PARAMS = {"hold_s", "cooldown_s", "lookback_s"}

BASELINES: dict[str, dict[str, float]] = {
    "momentum": {
        "lookback_s": 300,
        "breakout_k": 1.0,
        "sl_atr": 1.3,
        "rr": 1.6,
        "hold_s": 900,
        "min_edge_bps": 2.0,
        "max_spread_bps": 6.0,
        "cooldown_s": 300,
        "z_entry": 1.8,
        "max_vol_bps": 90.0,
    },
    "meanrev": {
        "lookback_s": 300,
        "breakout_k": 1.0,
        "sl_atr": 1.2,
        "rr": 1.2,
        "hold_s": 600,
        "min_edge_bps": 1.5,
        "max_spread_bps": 5.0,
        "cooldown_s": 240,
        "z_entry": 1.8,
        "max_vol_bps": 35.0,
    },
    "scalp": {
        "lookback_s": 120,
        "breakout_k": 0.8,
        "sl_atr": 0.9,
        "rr": 1.0,
        "hold_s": 300,
        "min_edge_bps": 1.0,
        "max_spread_bps": 3.5,
        "cooldown_s": 120,
        "z_entry": 1.2,
        "max_vol_bps": 80.0,
    },
}

MAX_STEP_DELTA_PCT = 20.0  # سقف جابه‌جایی هر پارامتر توسط مدل


# --------------------------------------------------------------------------
# ساخت و نرمال‌سازی
# --------------------------------------------------------------------------
def clamp_params(params: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, value in params.items():
        if key not in PARAM_BOUNDS:
            continue
        try:
            num = float(value)
        except (TypeError, ValueError):
            continue
        low, high = PARAM_BOUNDS[key]
        num = clamp(num, low, high)
        out[key] = int(round(num)) if key in INT_PARAMS else round_sig(num)
    return out


def baseline_params(variant: str) -> dict[str, float]:
    base = BASELINES.get(variant, BASELINES["momentum"])
    return clamp_params(dict(base))


def new_policy(
    variant: str = "momentum",
    params: dict[str, Any] | None = None,
    level: int = 0,
    generation: int = 0,
    source: str = "baseline",
    reason: str = "",
    cycle: int = 0,
) -> dict[str, Any]:
    variant = variant if variant in VARIANTS else "momentum"
    merged = baseline_params(variant)
    if params:
        merged.update(params)
    return {
        "schema": 1,
        "variant": variant,
        "params": clamp_params(merged),
        "level": int(level),
        "generation": int(generation),
        "version": int(generation) + 1,
        "source": source,
        "reason": reason,
        "risk_scale": 1.0,
        "last_change_cycle": int(cycle),
        "updated_ts": utc_iso(),
    }


def _next_variant(current: str) -> str:
    """چرخش کنترل‌شده بین خانواده‌ها هنگام شکست ساختاری یک استراتژی."""
    order = list(VARIANTS)
    idx = order.index(current) if current in order else 0
    return order[(idx + 1) % len(order)]


# --------------------------------------------------------------------------
# نردبان سازگاری (قطعی — حتی بدون هیچ مدلی کار می‌کند)
# --------------------------------------------------------------------------
def _step_1_tighten_entry(p: dict[str, float]) -> tuple[dict[str, float], str]:
    p["breakout_k"] = round_sig(p.get("breakout_k", 1.0) * 1.25)
    p["min_edge_bps"] = round_sig(p.get("min_edge_bps", 2.0) + 1.0)
    return p, "سخت‌ترکردن شرط ورود (breakout_k ↑، حداقل لبه ↑)"


def _step_2_tighten_costs(p: dict[str, float]) -> tuple[dict[str, float], str]:
    p["max_spread_bps"] = round_sig(p.get("max_spread_bps", 6.0) * 0.75)
    p["cooldown_s"] = round_sig(p.get("cooldown_s", 60) * 1.5)
    return p, "فیلتر هزینه سخت‌تر (سقف اسپرد ↓، فاصله بین ورودها ↑)"


def _step_3_wider_stop(p: dict[str, float]) -> tuple[dict[str, float], str]:
    p["sl_atr"] = round_sig(p.get("sl_atr", 1.3) * 1.15)
    p["rr"] = round_sig(p.get("rr", 1.6) * 0.9)
    p["hold_s"] = round_sig(p.get("hold_s", 900) * 0.9)
    return p, "فاصله‌ی استاپ ↑ و هدف نزدیک‌تر (کاهش نویز-استاپ)"


def _step_4_switch_variant(p: dict[str, float]) -> tuple[dict[str, float], str]:
    # خودِ variant جای دیگری عوض می‌شود؛ پارامترها به پایه برمی‌گردند
    return p, "تغییر خانواده‌ی استراتژی"


def _step_5_derisk(p: dict[str, float]) -> tuple[dict[str, float], str]:
    p["cooldown_s"] = round_sig(p.get("cooldown_s", 60) * 2.0)
    return p, "کاهش ریسک هر معامله و افزایش فاصله‌ی ورود"


LEVEL_STEPS = [
    _step_1_tighten_entry,
    _step_2_tighten_costs,
    _step_3_wider_stop,
    _step_4_switch_variant,
    _step_5_derisk,
]


def build_policy_for_level(
    baseline_variant: str,
    level: int,
    cycle: int = 0,
    generation: int = 0,
    source: str = "adaptive",
) -> dict[str, Any]:
    """سیاست را از صفر برای سطح مشخص می‌سازد؛ idempotent و بازتولیدپذیر."""
    level = int(clamp(level, 0, len(LEVEL_STEPS)))
    variant = baseline_variant if baseline_variant in VARIANTS else "momentum"
    reasons: list[str] = []
    # هر بار variant از پایه شروع می‌شود و پله‌ی «تغییر خانواده» یک‌بار اعمال می‌شود
    variant_switched = False
    params = baseline_params(variant)
    for idx in range(level):
        step = LEVEL_STEPS[idx]
        params, reason = step(params)
        if step is _step_4_switch_variant and not variant_switched:
            variant = _next_variant(variant)
            params = baseline_params(variant)
            variant_switched = True
        reasons.append(reason)
    policy = new_policy(
        variant=variant,
        params=params,
        level=level,
        generation=generation,
        source=source if level else "baseline",
        reason=" • ".join(reasons) if reasons else "سیاست پایه",
        cycle=cycle,
    )
    if level >= len(LEVEL_STEPS):
        policy["risk_scale"] = 0.5
    return policy


@dataclass
class Decision:
    """یک تغییر سیاست، با دلیل و قبل/بعد — برای گزارش و حسابرسی."""

    ts: str
    cycle: int
    source: str
    action: str
    level_before: int
    level_after: int
    reason: str
    changes: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


class AdaptiveController:
    """از روی استریک ضرر/برد و کول‌داون، سطح را جابه‌جا می‌کند."""

    def __init__(
        self,
        baseline_variant: str = "momentum",
        adapt_after_losses: int = 2,
        cooldown_cycles: int = 2,
        recover_after_wins: int = 2,
        max_level: int = 5,
    ) -> None:
        self.baseline_variant = baseline_variant
        self.adapt_after_losses = max(1, int(adapt_after_losses))
        self.cooldown_cycles = max(0, int(cooldown_cycles))
        self.recover_after_wins = max(1, int(recover_after_wins))
        self.max_level = int(clamp(max_level, 0, len(LEVEL_STEPS)))

    def maybe_adapt(
        self,
        policy: dict[str, Any],
        streak_losses: int,
        streak_wins: int,
        cycle: int,
    ) -> tuple[dict[str, Any], Decision | None]:
        level = int(policy.get("level", 0))
        last_change = int(policy.get("last_change_cycle", 0))
        if cycle - last_change < self.cooldown_cycles and level > 0:
            return policy, None
        if cycle - last_change < 1:
            return policy, None

        target = level
        action = ""
        reason = ""
        if streak_losses >= self.adapt_after_losses:
            target = min(self.max_level, level + 1)
            action = "tighten"
            reason = f"{streak_losses} ضرر پشت‌سرهم → پله {target}"
        elif streak_wins >= self.recover_after_wins and level > 0:
            target = max(0, level - 1)
            action = "recover"
            reason = f"{streak_wins} برد پشت‌سرهم → عقب‌نشینی به پله {target}"

        if target == level:
            return policy, None

        new = build_policy_for_level(
            self.baseline_variant,
            target,
            cycle=cycle,
            generation=int(policy.get("generation", 0)) + 1,
            source="adaptive",
        )
        changes = {
            "variant": [policy.get("variant"), new.get("variant")],
            "level": [level, target],
            "risk_scale": [policy.get("risk_scale", 1.0), new.get("risk_scale", 1.0)],
            "params": {
                key: [policy.get("params", {}).get(key), new["params"].get(key)]
                for key in sorted(set(policy.get("params", {})) | set(new["params"]))
                if policy.get("params", {}).get(key) != new["params"].get(key)
            },
        }
        decision = Decision(
            ts=utc_iso(),
            cycle=cycle,
            source="adaptive",
            action=action,
            level_before=level,
            level_after=target,
            reason=reason + " — " + str(new.get("reason", "")),
            changes=changes,
        )
        LOG.info("تغییر سیاست (adaptive): %s → %s | %s", level, target, reason)
        return new, decision


# --------------------------------------------------------------------------
# اعتبارسنجی پیشنهاد مدل زبانی
# --------------------------------------------------------------------------
ALLOWED_PROPOSAL_KEYS = {"variant", "params", "reason", "confidence", "notes"}


def validate_proposal(
    proposal: Any,
    current: dict[str, Any],
    cycle: int = 0,
) -> tuple[dict[str, Any] | None, list[str]]:
    """پیشنهاد JSON مدل را می‌سنجد؛ اگر امن نبود سیاست فعلی دست‌نخورده می‌ماند."""
    notes: list[str] = []
    if not isinstance(proposal, dict):
        return None, ["پیشنهاد dict نیست"]
    extra = set(proposal) - ALLOWED_PROPOSAL_KEYS
    if extra:
        notes.append(f"کلیدهای نادیده‌گرفته‌شده: {sorted(extra)}")

    try:
        confidence = float(proposal.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < 0.4:
        notes.append(f"اطمینان پایین ({confidence:.2f}) — رد شد")
        return None, notes

    variant = proposal.get("variant", current.get("variant"))
    if variant not in VARIANTS:
        notes.append(f"variant غیرمجاز: {variant!r} — رد شد")
        return None, notes

    cur_params = dict(current.get("params", {}))
    proposed_params = proposal.get("params", {}) or {}
    if not isinstance(proposed_params, dict):
        notes.append("params باید dict باشد — رد شد")
        return None, notes

    merged = dict(cur_params)
    changed: dict[str, list[float]] = {}
    for key, raw in proposed_params.items():
        if key not in PARAM_BOUNDS:
            notes.append(f"پارامتر ناشناخته نادیده گرفته شد: {key}")
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            notes.append(f"مقدار غیرعددی برای {key} نادیده گرفته شد")
            continue
        base = float(cur_params.get(key, BASELINES.get(variant, BASELINES["momentum"]).get(key, value)))
        if base > 0:
            delta_pct = abs(value - base) / base * 100.0
            if delta_pct > MAX_STEP_DELTA_PCT:
                low, high = PARAM_BOUNDS[key]
                value = clamp(base * (1 + MAX_STEP_DELTA_PCT / 100.0 * (1 if value > base else -1)), low, high)
                notes.append(f"{key}: جابه‌جایی بیش از {MAX_STEP_DELTA_PCT:.0f}% محدود شد")
        merged[key] = value
        if round(base, 6) != round(value, 6):
            changed[key] = [base, round(value, 6)]

    new = new_policy(
        variant=variant,
        params=merged,
        level=int(current.get("level", 0)),
        generation=int(current.get("generation", 0)) + 1,
        source="llm",
        reason=str(proposal.get("reason", ""))[:500],
        cycle=cycle,
    )
    new["last_change_cycle"] = cycle
    new["llm_confidence"] = round_sig(confidence, 3)
    new["llm_notes"] = [str(n)[:300] for n in (proposal.get("notes") or [])][:6]

    if not changed and variant == current.get("variant"):
        notes.append("پیشنهاد هیچ تغییری نداشت — رد شد")
        return None, notes

    LOG.info("پیشنهاد مدل پذیرفته شد: variant=%s تغییرات=%s", variant, sorted(changed))
    return new, notes


def describe(policy: dict[str, Any]) -> str:
    """توضیح یک‌خطی فارسی برای گزارش."""
    params = policy.get("params", {})
    variant_fa = {"momentum": "شکست تکانه", "meanrev": "بازگشت به میانگین", "scalp": "اسکالپ سریع"}
    return (
        f"{variant_fa.get(str(policy.get('variant')), policy.get('variant'))} | "
        f"پله {policy.get('level', 0)} | "
        f"SL={params.get('sl_atr')}×ATR، RR={params.get('rr')}، "
        f"نگهداری={params.get('hold_s')}s، اسپرد≤{params.get('max_spread_bps')}bp، "
        f"ریسک×{policy.get('risk_scale', 1.0)}"
    )


def diff_params(before: dict[str, Any], after: dict[str, Any]) -> dict[str, tuple[Any, Any]]:
    b = before.get("params", {}) if before else {}
    a = after.get("params", {}) if after else {}
    out: dict[str, tuple[Any, Any]] = {}
    for key in sorted(set(b) | set(a)):
        if b.get(key) != a.get(key):
            out[key] = (b.get(key), a.get(key))
    return out


def clone(policy: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(policy)


def sanitize_for_log(policy: dict[str, Any]) -> dict[str, Any]:
    """نسخه‌ی امن برای گزارش (بدون چیز حساس؛ سیاست‌ها حساس نیستند)."""
    keep = ("variant", "params", "level", "generation", "version", "source", "reason", "risk_scale")
    return {k: copy.deepcopy(policy.get(k)) for k in keep if k in policy}

