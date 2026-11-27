"""حس پیش‌بینی روند از تیک‌های ثانیه‌ای (nowcast).

سؤال: از حرکت ثانیه‌به‌ثانیهٔ قیمت، آیا می‌شود جهت/شتاب کندل بعدی را حدس زد؟

مدل: پرسپترون کوچک روی چهار ویژگی که از خود کندل‌های یک‌ثانیه‌ای ساخته می‌شوند:

    r_fast   بازده ۵ ثانیهٔ اخیر (سرعت)
    accel    تغییر سرعت (شتاب): r_fast - r_fast قبلی
    r_slow   بازده ۳۰ ثانیهٔ اخیر (روند)
    imb      عدم‌تعادل تیک‌های صعودی/نزولی در پنجره

برچسب: علامت بازده ``horizon_s`` ثانیه بعد.

مهم: دقت **اندازه‌گیری می‌شود** (walk-forward: آموزش روی گذشته، آزمون روی ادامهٔ
همان سری) و با Brier score گزارش می‌شود. اگر مدل چیزی یاد نگرفته باشد، همان را
می‌گوییم، نه اینکه ادعا کنیم روند را می‌شناسیم.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

from .ticks import Bar


def _log(x: float) -> float:
    return math.log(x) if x > 0 else 0.0


@dataclass
class NowcastResult:
    symbol: str = ""
    prob_up: float = 0.5          # احتمال اینکه بازده افق مثبت باشد
    expected_bps: float = 0.0     # اندازهٔ حرکت پیش‌بینی‌شده
    hit_rate: float = 0.0         # دقت روی نمونهٔ آزمون
    brier: float = 0.0
    samples_train: int = 0
    samples_test: int = 0
    horizon_s: int = 60
    features: dict[str, float] = field(default_factory=dict)

    @property
    def edge(self) -> float:
        """فاصله از بی‌طرفی (۰ = هیچ نظری ندارد)."""
        return abs(self.prob_up - 0.5) * 2.0

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "prob_up": round(self.prob_up, 4),
            "expected_bps": round(self.expected_bps, 2),
            "hit_rate": round(self.hit_rate, 4),
            "brier": round(self.brier, 4),
            "samples": {"train": self.samples_train, "test": self.samples_test},
            "horizon_s": self.horizon_s,
            "edge": round(self.edge, 3),
            "features": {k: round(v, 6) for k, v in self.features.items()},
        }

    def summary(self) -> str:
        return (
            f"{self.symbol}: p(up)={self.prob_up:.2f} · انتظار {self.expected_bps:+.1f}bp · "
            f"دقت {self.hit_rate:.0%} روی {self.samples_test} نمونه"
        )


class MomentumNowcaster:
    """پرسپترون با نرخ یادگیری ثابت؛ کاملاً پایتون خالص و قطعی."""

    def __init__(self, horizon_s: int = 60, window_s: int = 1200, lr: float = 0.08) -> None:
        self.horizon_s = max(1, int(horizon_s))
        self.window_s = max(self.horizon_s * 4, int(window_s))
        self.lr = float(lr)
        self.weights = [0.0, 0.0, 0.0, 0.0]
        self.bias = 0.0

    # -- ویژگی‌ها -------------------------------------------------------
    def _features(self, bars: Sequence[Bar], i: int, r_fast_prev: float) -> list[float]:
        close = bars[i].close
        def back(n: int) -> float:
            j = max(0, i - n)
            return bars[j].close or close
        r_fast = _log(close / back(5)) if back(5) > 0 else 0.0
        r_slow = _log(close / back(30)) if back(30) > 0 else 0.0
        window = bars[max(0, i - 30) : i + 1]
        ups = sum(1 for k in range(1, len(window)) if window[k].close >= window[k - 1].close)
        downs = (len(window) - 1) - ups
        imb = (ups - downs) / max(1, len(window) - 1)
        accel = r_fast - r_fast_prev
        return [r_fast * 1e4, accel * 1e4, r_slow * 1e4, imb]

    def _sigmoid(self, z: float) -> float:
        if z >= 0:
            return 1.0 / (1.0 + math.exp(-min(z, 30.0)))
        ez = math.exp(max(z, -30.0))
        return ez / (1.0 + ez)

    def predict(self, features: list[float]) -> float:
        z = self.bias + sum(w * f for w, f in zip(self.weights, features))
        return self._sigmoid(z / 10.0)  # مقیاس‌دهی تا ویژگی‌های bp مدل را اشباع نکنند

    # -- آموزش + ارزیابی ------------------------------------------------
    def run(self, symbol: str, bars: Sequence[Bar]) -> NowcastResult:
        result = NowcastResult(symbol=symbol, horizon_s=self.horizon_s)
        if not bars or len(bars) < 200:
            return result

        bars = list(bars)[-self.window_s :]
        samples: list[tuple[list[float], int, float]] = []
        r_fast_prev = 0.0
        for i in range(31, len(bars) - self.horizon_s):
            feats = self._features(bars, i, r_fast_prev)
            r_fast_prev = feats[0] / 1e4
            future = bars[min(len(bars) - 1, i + self.horizon_s)].close
            now = bars[i].close
            if now <= 0 or future <= 0:
                continue
            ret = _log(future / now)
            samples.append((feats, 1 if ret > 0 else 0, ret))
        if len(samples) < 60:
            return result

        split = int(len(samples) * 0.7)
        train, test = samples[:split], samples[split:]

        # مقیاس‌دهی ویژگی‌ها (میانگین/انحراف روی نمونهٔ آموزش) تا bpهای بزرگ غالب نشوند
        dim = 4
        mean = [sum(s[0][k] for s in train) / len(train) for k in range(dim)]
        std = []
        for k in range(dim):
            var = sum((s[0][k] - mean[k]) ** 2 for s in train) / len(train)
            std.append(math.sqrt(var) or 1.0)

        def scaled(feats: list[float]) -> list[float]:
            return [(feats[k] - mean[k]) / std[k] for k in range(dim)]

        self.weights = [0.0] * dim
        self.bias = 0.0
        for feats, label, _ret in train:
            xs = scaled(feats)
            p = self.predict(xs)
            err = label - p
            self.weights = [w + self.lr * err * x for w, x in zip(self.weights, xs)]
            self.bias += self.lr * err

        correct = 0
        brier_sum = 0.0
        for feats, label, _ret in test:
            p = self.predict(scaled(feats))
            correct += 1 if (p >= 0.5) == (label == 1) else 0
            brier_sum += (p - label) ** 2

        result.samples_train = len(train)
        result.samples_test = len(test)
        result.hit_rate = correct / max(1, len(test))
        result.brier = brier_sum / max(1, len(test))

        last_feats = self._features(bars, len(bars) - 1, r_fast_prev)
        xs = scaled(last_feats)
        result.prob_up = self.predict(xs)
        result.features = dict(zip(("r_fast_bp", "accel_bp", "r_slow_bp", "imbalance"), last_feats))

        # اندازهٔ حرکت: میانگین |بازده| افق × جهت پیش‌بینی‌شده
        mean_abs = sum(abs(s[2]) for s in samples) / len(samples)
        result.expected_bps = mean_abs * 1e4 * (1 if result.prob_up >= 0.5 else -1) * result.edge
        return result


def nowcast_symbols(
    bars_by_symbol: dict[str, Sequence[Bar]],
    horizon_s: int = 60,
    window_s: int = 1200,
) -> dict[str, dict]:
    """برای چند نماد هم‌زمان؛ خروجی آمادهٔ گذاشتن در context و گزارش."""
    out: dict[str, dict] = {}
    for symbol, bars in bars_by_symbol.items():
        if not bars:
            continue
        res = MomentumNowcaster(horizon_s=horizon_s, window_s=window_s).run(symbol, bars)
        out[symbol] = res.as_dict()
    return out
