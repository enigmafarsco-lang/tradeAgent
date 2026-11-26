"""سه خانواده‌ی استراتژی قطعی. انتخابِ کدام‌یک و با چه پارامتری، کار سیاست است.

هیچ‌کدام سفارش واقعی نمی‌فرستند؛ فقط «قصد» (Intent) برای موتور کاغذی می‌سازند.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .features import Features

VARIANTS = ("momentum", "meanrev", "scalp")


@dataclass
class Intent:
    direction: int  # +1 خرید، -1 فروش
    sl_bps: float
    tp_bps: float
    hold_s: int
    edge_bps: float
    variant: str
    reason: str

    def as_dict(self) -> dict:
        return asdict(self)


def _spread_ok(f: Features, params: dict[str, Any]) -> bool:
    max_spread = float(params.get("max_spread_bps", 6.0))
    return f.spread_bps <= max_spread


def _size_from_atr(f: Features, params: dict[str, Any]) -> tuple[float, float]:
    """فاصله‌ی استاپ هرگز نباید از هزینه‌ی رفت‌وبرگشت کوچک‌تر باشد."""
    cost_floor = 3.0 * max(f.spread_bps, 0.5)  # اسپرد + اسلیپیج + کمی حاشیه
    sl = max(float(params.get("sl_atr", 1.2)) * f.atr_bps, cost_floor, 1.0)
    tp = max(1.0, float(params.get("rr", 1.2)) * sl)
    return sl, tp


def momentum(f: Features, params: dict[str, Any]) -> Intent | None:
    """شکست کانال ۵ دقیقه‌ای با تأیید تکانه‌ی ۶۰ ثانیه‌ای."""
    if not _spread_ok(f, params):
        return None
    edge = abs(f.ret_60_bps) / max(f.vol_60_bps, 0.5)
    k = float(params.get("breakout_k", 1.0))
    threshold = max(float(params.get("min_edge_bps", 2.0)), k * max(f.vol_60_bps, 0.4))
    if f.ret_60_bps >= threshold and f.close > f.hi_300:
        direction = 1
    elif f.ret_60_bps <= -threshold and f.close < f.lo_300:
        direction = -1
    else:
        return None
    sl, tp = _size_from_atr(f, params)
    return Intent(
        direction=direction,
        sl_bps=sl,
        tp_bps=tp,
        hold_s=int(params.get("hold_s", 900)),
        edge_bps=round(edge, 2),
        variant="momentum",
        reason=f"شکست کانال ۳۰۰s با تکانه {f.ret_60_bps:+.1f}bp و vol {f.vol_60_bps:.1f}bp",
    )


def meanrev(f: Features, params: dict[str, Any]) -> Intent | None:
    """بازگشت به میانگین در رژیم کم‌نوسان."""
    if not _spread_ok(f, params):
        return None
    if f.vol_300_bps > float(params.get("max_vol_bps", 35.0)):
        return None
    z_entry = float(params.get("z_entry", 1.8))
    if abs(f.z) < z_entry:
        return None
    direction = -1 if f.z > 0 else 1
    sl, tp = _size_from_atr(f, params)
    return Intent(
        direction=direction,
        sl_bps=sl,
        tp_bps=tp,
        hold_s=int(params.get("hold_s", 600)),
        edge_bps=round(abs(f.z), 2),
        variant="meanrev",
        reason=f"z={f.z:+.2f} در رژیم کم‌نوسان (vol300={f.vol_300_bps:.1f}bp)",
    )


def scalp(f: Features, params: dict[str, Any]) -> Intent | None:
    """ادامه‌ی تکانه‌ی کوتاه با هدف کوچک، فقط وقتی اسپرد نازک است."""
    if not _spread_ok(f, params):
        return None
    if f.atr_bps > float(params.get("max_vol_bps", 80.0)):
        return None
    k = float(params.get("breakout_k", 0.8))
    threshold = max(float(params.get("min_edge_bps", 1.0)), k * max(f.vol_60_bps, 0.4))
    if f.ret_60_bps >= threshold:
        direction = 1
    elif f.ret_60_bps <= -threshold:
        direction = -1
    else:
        return None
    sl, tp = _size_from_atr(f, params)
    return Intent(
        direction=direction,
        sl_bps=sl,
        tp_bps=tp,
        hold_s=int(params.get("hold_s", 180)),
        edge_bps=round(abs(f.ret_60_bps) / max(f.vol_60_bps, 0.5), 2),
        variant="scalp",
        reason=f"تکانه‌ی سریع {f.ret_60_bps:+.1f}bp با اسپرد {f.spread_bps:.1f}bp",
    )


_REGISTRY = {"momentum": momentum, "meanrev": meanrev, "scalp": scalp}


def evaluate(variant: str, f: Features, params: dict[str, Any]) -> Intent | None:
    fn = _REGISTRY.get(variant)
    if fn is None:
        return None
    return fn(f, params)
