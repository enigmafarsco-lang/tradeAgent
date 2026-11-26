"""ویژگی‌های عددی از کندل‌های ۱ ثانیه‌ای — تمام محاسبه‌ها قطعی و بدون نگاه به آینده."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Sequence

from .ticks import Bar


@dataclass
class Features:
    t: int
    close: float
    ret_60_bps: float
    ret_300_bps: float
    ret_900_bps: float
    vol_60_bps: float
    vol_300_bps: float
    atr_bps: float
    ema_fast: float
    ema_slow: float
    z: float
    hi_300: float
    lo_300: float
    mid_300: float
    spread_bps: float
    tick_rate: float
    slope_300_bps: float

    def as_dict(self) -> dict:
        return asdict(self)


def _close_ago(bars: Sequence[Bar], index: int, seconds: int) -> float:
    """قیمت بسته‌شدن ``seconds`` ثانیه قبل (حداکثر تا ابتدای پنجره)."""
    j = max(0, index - max(1, seconds))
    return bars[j].close


def _ret_bps(bars: Sequence[Bar], index: int, seconds: int) -> float:
    prev = _close_ago(bars, index, seconds)
    if prev <= 0:
        return 0.0
    return (bars[index].close / prev - 1.0) * 1e4


def _stdev(values: Sequence[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    return math.sqrt(max(0.0, var))


def _ema(values: Sequence[float], span: int) -> float:
    if not values:
        return 0.0
    alpha = 2.0 / (span + 1.0)
    out = values[0]
    for value in values[1:]:
        out = out + alpha * (value - out)
    return out


def _log_returns(bars: Sequence[Bar], index: int, seconds: int) -> list[float]:
    start = max(1, index - seconds + 1)
    out: list[float] = []
    for i in range(start, index + 1):
        prev = bars[i - 1].close
        cur = bars[i].close
        if prev > 0 and cur > 0:
            out.append(math.log(cur / prev))
    return out


def _block_atr(bars: Sequence[Bar], index: int, block: int = 60, blocks: int = 5) -> float:
    """میانگین دامنه‌ی چند بلوک متوالی؛ تقریب ATR چنددقیقه‌ای از داده‌ی ثانیه‌ای."""
    ranges: list[float] = []
    end = index
    for _ in range(blocks):
        start = max(0, end - block + 1)
        seg = bars[start : end + 1]
        if len(seg) >= 2:
            hi = max(b.high for b in seg)
            lo = min(b.low for b in seg)
            last = seg[-1].close
            if last > 0:
                ranges.append((hi - lo) / last * 1e4)
        end = start - 1
        if end <= 0:
            break
    return sum(ranges) / len(ranges) if ranges else 0.0


def compute_features(bars: Sequence[Bar], index: int) -> Features:
    """ویژگی‌ها در بسته‌شدن کندل ``index``؛ فقط از داده‌ی همان کندل و قبلش."""
    if index < 0 or index >= len(bars):
        raise IndexError("index خارج از محدوده‌ی کندل‌ها")
    bar = bars[index]
    close = bar.close
    window_start = max(1, index - 300 + 1)

    ret_60 = _ret_bps(bars, index, 60)
    ret_300 = _ret_bps(bars, index, 300)
    ret_900 = _ret_bps(bars, index, 900)

    rets_60 = _log_returns(bars, index, 60)
    rets_300 = _log_returns(bars, index, 300)
    vol_60 = _stdev(rets_60) * math.sqrt(60.0) * 1e4
    vol_300 = _stdev(rets_300) * math.sqrt(300.0) * 1e4

    # ATR روی بلوک‌های ۶۰ ثانیه‌ای (۵ دقیقه) — نه نویز یک‌ثانیه‌ای؛
    # وگرنه استاپ‌ها از حد نویز/اسپرد کوچک‌تر می‌شوند و هر معامله فوراً استاپ می‌خورد.
    atr_bps = _block_atr(bars, index, block=60, blocks=5)

    # کانال ۳۰۰ ثانیه‌ای *بدون* خود کندل جاری تا شکست واقعی معنی داشته باشد
    hist = [bars[i].close for i in range(window_start, index + 1)]
    hi_300 = max((bars[i].high for i in range(window_start, index)), default=close)
    lo_300 = min((bars[i].low for i in range(window_start, index)), default=close)
    mid_300 = (hi_300 + lo_300) / 2.0
    z = 0.0
    if len(hist) >= 20:
        mean = sum(hist) / len(hist)
        sd = _stdev(hist)
        z = (close - mean) / sd if sd > 0 else 0.0

    # شیب خط رگرسیون ساده روی ۳۰۰ ثانیه (بپ در ثانیه)
    slope = 0.0
    if len(hist) >= 30:
        n = len(hist)
        mean_x = (n - 1) / 2.0
        mean_y = sum(hist) / n
        num = sum((i - mean_x) * (hist[i] - mean_y) for i in range(n))
        den = sum((i - mean_x) ** 2 for i in range(n))
        if den > 0 and mean_y > 0:
            slope = (num / den) / mean_y * 1e4

    spread = sum(bars[i].spread_bps for i in range(max(0, index - 59), index + 1))
    spread_bps = spread / min(60, index + 1)
    tick_rate = sum(bars[i].ticks for i in range(max(0, index - 59), index + 1)) / min(60, index + 1)

    return Features(
        t=bar.t,
        close=close,
        ret_60_bps=ret_60,
        ret_300_bps=ret_300,
        ret_900_bps=ret_900,
        vol_60_bps=vol_60,
        vol_300_bps=vol_300,
        atr_bps=max(atr_bps, 0.05),
        ema_fast=_ema([bars[i].close for i in range(max(0, index - 30), index + 1)], 8),
        ema_slow=_ema([bars[i].close for i in range(max(0, index - 120), index + 1)], 30),
        z=z,
        hi_300=hi_300,
        lo_300=lo_300,
        mid_300=mid_300,
        spread_bps=spread_bps,
        tick_rate=tick_rate,
        slope_300_bps=slope,
    )


def ready(bars: Sequence[Bar], index: int, min_history: int = 320) -> bool:
    """آیا تاریخچه برای ویژگی‌های معنادار کافی است؟"""
    return index >= min_history and index < len(bars)
