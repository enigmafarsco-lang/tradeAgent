"""خوراک ایجنت: خلاصهٔ حرکت ثانیه‌ای + دفتر پیش‌بینی‌ها.

**تصمیم با ایجنت است، نه با پایتون.**

این ماژول فقط *اعداد* را آماده می‌کند (سرعت، شتاب، دامنه، عدم‌تعادل تیک، اسپرد) و
به مدل می‌دهد؛ خودِ قضاوت «روند چیست، شتاب دارد یا تحلیل می‌رود، کِی برمی‌گردد» کار
مدل زبانی است. بعد از آن، همین ماژول پیش‌بینی مدل را ثبت می‌کند و وقتی افقش سر رسید،
با قیمت واقعی نمره می‌دهد - تا بدانیم ایجنت چقدر درست می‌گوید.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from .ticks import Bar

LOG = logging.getLogger("tradeagent.digest")

WINDOWS = (5, 15, 30, 60, 120)


def _ret_bp(now: float, then: float) -> float:
    if now <= 0 or then <= 0:
        return 0.0
    return (now / then - 1.0) * 1e4


def tick_digest(bars: Sequence[Bar], windows: Sequence[int] = WINDOWS) -> dict[str, Any]:
    """عددهای خام حرکت برای مدل: هیچ تفسیری اینجا انجام نمی‌شود."""
    if not bars:
        return {}
    bars = list(bars)
    close = bars[-1].close
    out: dict[str, Any] = {
        "last_price": round(close, 6),
        "last_ts": int(bars[-1].t),
        "bars": len(bars),
        "spread_bps": round(bars[-1].spread_bps, 3),
    }
    for w in windows:
        j = max(0, len(bars) - 1 - w)
        base = bars[j].close
        seg = bars[j:]
        highs = max(b.high for b in seg) or close
        lows = min(b.low for b in seg) or close
        ups = sum(1 for k in range(1, len(seg)) if seg[k].close >= seg[k - 1].close)
        out[f"ret_{w}s_bp"] = round(_ret_bp(close, base), 2)
        out[f"range_{w}s_bp"] = round(_ret_bp(highs, lows), 2)
        out[f"up_frac_{w}s"] = round(ups / max(1, len(seg) - 1), 3)

    # شتاب: تغییر سرعت بین دو نیمهٔ پنجرهٔ کوتاه
    half = max(2, len(bars) // 2)
    first, second = bars[:half], bars[half:]
    v1 = _ret_bp(first[-1].close, first[0].close) / max(1, half)
    v2 = _ret_bp(second[-1].close, second[0].close) / max(1, len(second))
    out["velocity_change_bp_per_s"] = round(v2 - v1, 5)

    # کندل‌های آخر برای «شکل» حرکت (مدل خودش تفسیر کند)
    tail = bars[-10:]
    out["last_10_closes"] = [round(b.close, 6) for b in tail]
    out["ticks_per_bar_avg"] = round(sum(b.ticks for b in bars[-30:]) / max(1, len(bars[-30:])), 1)
    return out


def digests_for(bars_by_symbol: dict[str, Sequence[Bar]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for symbol, bars in bars_by_symbol.items():
        digest = tick_digest(bars)
        if digest:
            out[symbol] = digest
    return out


# ---------------------------------------------------------------------------
# دفتر پیش‌بینی: ایجنت چه گفت، بعداً چه شد
# ---------------------------------------------------------------------------
@dataclass
class Forecast:
    symbol: str
    ts: int
    horizon_s: int
    direction: str            # up | down | flat
    confidence: float
    price: float
    strength: float = 0.0
    phase: str = ""
    scored: bool = False
    hit: bool | None = None
    move_bp: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "ts": self.ts,
            "horizon_s": self.horizon_s,
            "direction": self.direction,
            "confidence": self.confidence,
            "price": self.price,
            "strength": self.strength,
            "phase": self.phase,
            "scored": self.scored,
            "hit": self.hit,
            "move_bp": self.move_bp,
        }


@dataclass
class ForecastLedger:
    """ذخیره و نمره‌دهی پیش‌بینی‌های ایجنت (فایل JSON)."""

    path: Any = None
    rows: list[Forecast] = field(default_factory=list)

    def load(self) -> "ForecastLedger":
        try:
            data = json.loads(open(self.path, encoding="utf-8").read())
        except (OSError, ValueError, TypeError):
            return self
        for item in data.get("forecasts", []) if isinstance(data, dict) else []:
            try:
                self.rows.append(Forecast(**{k: item[k] for k in Forecast.__dataclass_fields__ if k in item}))
            except TypeError:
                continue
        return self

    def save(self) -> None:
        if not self.path:
            return
        payload = {"forecasts": [r.as_dict() for r in self.rows[-500:]]}
        try:
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
        except OSError as exc:
            LOG.warning("ذخیرهٔ دفتر پیش‌بینی نشد: %s", exc)

    # -- ثبت ------------------------------------------------------------
    def record(self, symbol: str, verdict: dict[str, Any], price: float, now_ts: int) -> Forecast | None:
        """قضاوت مدل را ثبت کن. ``verdict`` همان بخش momentum ایجنت است."""
        if not isinstance(verdict, dict) or not verdict:
            return None
        direction = str(verdict.get("direction") or "").lower()
        if direction not in ("up", "down", "flat"):
            return None
        try:
            confidence = float(verdict.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        try:
            horizon = int(verdict.get("horizon_s", 60))
        except (TypeError, ValueError):
            horizon = 60
        try:
            strength = float(verdict.get("strength", 0.0))
        except (TypeError, ValueError):
            strength = 0.0
        f = Forecast(
            symbol=symbol,
            ts=int(now_ts),
            horizon_s=max(5, min(3600, horizon)),
            direction=direction,
            confidence=max(0.0, min(1.0, confidence)),
            price=float(price),
            strength=max(0.0, min(1.0, strength)),
            phase=str(verdict.get("phase") or ""),
        )
        self.rows.append(f)
        return f

    # -- نمره‌دهی --------------------------------------------------------
    def score(self, prices: dict[str, float], now_ts: int, tolerance_bp: float = 1.0) -> dict[str, Any]:
        for row in self.rows:
            if row.scored or now_ts - row.ts < row.horizon_s:
                continue
            price = prices.get(row.symbol)
            if not price or row.price <= 0:
                continue
            move = _ret_bp(price, row.price)
            row.move_bp = round(move, 2)
            row.scored = True
            if row.direction == "flat":
                row.hit = abs(move) <= tolerance_bp
            elif row.direction == "up":
                row.hit = move > tolerance_bp
            else:
                row.hit = move < -tolerance_bp
        return self.stats()

    def stats(self) -> dict[str, Any]:
        scored = [r for r in self.rows if r.scored]
        if not scored:
            return {"n": 0, "hit_rate": 0.0, "avg_move_bp": 0.0, "pending": len(self.rows)}
        hits = sum(1 for r in scored if r.hit)
        # دقت وزنی: پیش‌بینی‌های با اطمینان بالا مهم‌ترند
        conf = sum(r.confidence for r in scored) or 1.0
        weighted = sum(r.confidence for r in scored if r.hit) / conf
        return {
            "n": len(scored),
            "hit_rate": round(hits / len(scored), 3),
            "weighted_hit_rate": round(weighted, 3),
            "avg_move_bp": round(sum(r.move_bp for r in scored) / len(scored), 2),
            "pending": len(self.rows) - len(scored),
        }

    def recent(self, limit: int = 5) -> list[dict[str, Any]]:
        return [r.as_dict() for r in self.rows[-limit:]]
