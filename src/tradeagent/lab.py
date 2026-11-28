"""آزمایشگاه آفلاین: از تیک‌های ثانیه‌ای کندل M1 بساز، ویژگی بساز، مدل‌ها را بسنج.

مسیر کار (همان چیزی که کاربر می‌خواهد):

    تیک‌های ثانیه‌ای  →  کندل M1  →  ویژگی (سرعت، شتاب، تکانه، دامنه، آهنگ تیک، اسپرد،
    روند قبلی چند بازه)  →  هدف: جهتِ ۲ تا ۳ کندل بعد  →  آموزش/سنجش راه‌رونده  →
    داشبورد گرافیکی

مدل‌ها همه پایتون خالص‌اند (بدون numpy/sklearn) تا روی هر ماشینی اجرا شود:

* `همیشه بالا` / `بیشینهٔ کلاس` — خط مبنا، بدون آموزش
* `قاعدهٔ تکانه` — علامت حرکت ۵ کندل قبل، خط مبنای حرفه‌ای‌تر
* `رگرسیون لجستیک` — softmax با L2، گرادیان نزولی (چندکلاسه: بالا/خنثی/پایین)

سنجش همیشه راه‌رونده است: آموزش فقط روی گذشته، آزمون روی آینده، و چند کندل فاصله
(embargo) تا هدفِ چسبیده به مرزِ آموزش نشت نکند.
"""

from __future__ import annotations

import html
import json
import math
import random
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from .util import LOG

MINUTE_MS = 60_000
LABELS = ("up", "flat", "down")
FA_LABEL = {"up": "صعودی", "flat": "خنثی", "down": "نزولی"}


# --------------------------------------------------------------------------
# داده
# --------------------------------------------------------------------------
@dataclass
class Candle:
    """کندل یک‌دقیقه‌ای ساخته‌شده از تیک."""

    t: int                     # ثانیهٔ شروع کندل (UTC)
    open: float
    high: float
    low: float
    close: float
    ticks: int = 0             # تعداد تیک در این دقیقه (آهنگ بازار)
    volume: float = 0.0
    spread_bps: float = 0.0    # میانگین اسپرد در دقیقه
    up_ticks: int = 0          # تیک‌های با جهت مثبت (فشار خرید)
    down_ticks: int = 0

    @property
    def range_bp(self) -> float:
        return ((self.high - self.low) / self.close) * 1e4 if self.close else 0.0

    @property
    def body_bp(self) -> float:
        return ((self.close - self.open) / self.open) * 1e4 if self.open else 0.0


def m1_candles_from_ticks(ticks: Iterable[tuple[int, float, float, float]]) -> list[Candle]:
    """(time_msc, bid, ask, volume) → کندل‌های M1 پُر (فقط دقیقه‌هایی که تیک داشته‌اند)."""
    out: list[Candle] = []
    cur: Candle | None = None
    cur_minute = -1
    last_mid = 0.0
    spread_sum = 0.0
    n_spread = 0
    for ms, bid, ask, volume in ticks:
        minute = ms // MINUTE_MS
        mid = (bid + ask) / 2.0 if bid > 0 and ask >= bid else (bid or ask)
        if mid <= 0:
            continue
        if minute != cur_minute:
            if cur is not None:
                cur.spread_bps = spread_sum / n_spread if n_spread else 0.0
                out.append(cur)
            cur = Candle(t=minute * 60, open=mid, high=mid, low=mid, close=mid)
            cur_minute = minute
            spread_sum, n_spread = 0.0, 0
        assert cur is not None
        cur.high = max(cur.high, mid)
        cur.low = min(cur.low, mid)
        cur.close = mid
        cur.ticks += 1
        cur.volume += volume or 1.0
        if last_mid:
            if mid > last_mid:
                cur.up_ticks += 1
            elif mid < last_mid:
                cur.down_ticks += 1
        last_mid = mid
        if bid > 0 and ask >= bid:
            spread_sum += ((ask - bid) / mid) * 1e4
            n_spread += 1
    if cur is not None:
        cur.spread_bps = spread_sum / n_spread if n_spread else 0.0
        out.append(cur)
    return [c for c in out if c.close > 0]


def find_tick_files(root: Path, symbol: str) -> list[Path]:
    """فایل‌های تیک را پیدا می‌کند: csv داخل harvester، یا jsonl لاگ ربات (s1_ticks_...)."""
    from .replay import tick_files

    root = Path(root).expanduser()
    files = tick_files(root, symbol)
    if not files and (root / symbol).is_dir():
        files = sorted((root / symbol).glob("*.csv"))
    if not files:
        files = sorted(root.glob("*.csv"))
    if files:
        return files
    return [p for p in sorted(root.glob("*.jsonl")) if symbol in p.name] or sorted(root.glob("*.jsonl"))


SEARCH_HINTS = (
    "~/trade-mc/logs",
    "~/trade-mc",
    "~/Documents/GitHub/trade-mc/logs",
    "~/Documents/GitHub/trade-mc",
    "~/Documents/GitHub",
    "~/logs",
)


def symbols_in_logs(dirs: Sequence[Path]) -> list[str]:
    """نمادهایی که در نام فایل‌های s1_ticks_<SYMBOL>_<date>.jsonl پیدا می‌شوند."""
    found: set[str] = set()
    for d in dirs:
        try:
            files = list(Path(d).glob("s1_ticks_*.jsonl"))
        except OSError:
            continue
        for f in files:
            tail = f.name[len("s1_ticks_"):]
            if "_" not in tail:
                continue
            found.add(tail.rsplit("_", 1)[0])
    return sorted(found)


def discover_tick_dirs(symbol: str, extra: Sequence[Path] = ()) -> list[Path]:
    """پوشه‌هایی که s1_ticks_<SYMBOL>_*.jsonl دارند (دادهٔ آفلاین ربات trade-mc)."""
    import os

    roots: list[Path] = []
    env = os.environ.get("TRADEAGENT_TICK_LOGS")
    if env:
        roots.append(Path(env).expanduser())
    roots.append(Path.cwd())
    roots.extend(Path(h).expanduser() for h in SEARCH_HINTS)
    roots.extend(Path(x).expanduser() for x in extra)

    found: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        dirs: list[Path] = [root]
        for pattern in ("*/logs", "*/", "logs"):
            try:
                dirs.extend(p for p in root.glob(pattern) if p.is_dir())
            except OSError:
                continue
        for cand in dirs[:80]:
            key = str(cand)
            if key in seen:
                continue
            seen.add(key)
            try:
                if any(cand.glob(f"s1_ticks_{symbol}*.jsonl")) or any(cand.glob("s1_ticks_*.jsonl")):
                    found.append(cand)
            except OSError:
                continue
    found.sort(key=lambda d: -len(list(d.glob(f"s1_ticks_{symbol}*.jsonl"))))
    return found


def probe_tick_file(path: Path) -> dict[str, Any]:
    """نوع و کلیدهای فایل تیک را برمی‌گرداند (برای پیام روشن وقتی داده نمی‌خواند)."""
    try:
        with Path(path).open("r", encoding="utf-8", errors="replace") as fh:
            first = fh.readline().strip()
            if not first:
                first = fh.readline().strip()
    except OSError as exc:
        return {"path": str(path), "kind": "unreadable", "error": str(exc)}
    if first.startswith("{"):
        try:
            obj = json.loads(first)
        except ValueError:
            return {"path": str(path), "kind": "jsonl?", "keys": []}
        return {"path": str(path), "kind": "jsonl", "keys": list(obj)[:14]}
    return {"path": str(path), "kind": "csv", "keys": first.split(";")[:7]}


def _tick_from_json(obj: dict[str, Any]) -> tuple[int, float, float, float] | None:
    """یک خط jsonl → (time_msc, bid, ask, volume)؛ کلیدهای رایج هر رباتی را می‌پذیرد."""
    lowered = {str(k).lower(): v for k, v in obj.items()}

    def num(*keys: str) -> float | None:
        for k in keys:
            v = lowered.get(k)
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                return float(v)
            if isinstance(v, str):
                try:
                    return float(v)
                except ValueError:
                    continue
        return None

    t = num("time_msc", "timemsc", "msc", "ts_ms", "epoch_ms", "ts", "timestamp", "time", "t")
    if t is None:
        return None
    if t > 1e14:            # میکروثانیه بود
        t /= 1000.0
    elif t < 1e11:          # ثانیه بود
        t *= 1000.0
    bid = num("bid", "bid_price", "b")
    ask = num("ask", "ask_price", "a")
    mid = num("mid", "price", "last", "close", "c")
    if bid is None and ask is None:
        if mid is None:
            return None
        bid = ask = mid
    bid = bid if bid is not None else (ask or 0.0)
    ask = ask if ask is not None else bid
    volume = num("volume", "last_volume", "vol", "size", "qty") or 1.0
    return int(t), float(bid), float(ask), float(volume)


def iter_tick_file(path: Path) -> Iterable[tuple[int, float, float, float]]:
    """فایل تیک را خط‌به‌خط می‌خواند؛ هم csv (؛) و هم jsonl."""
    try:
        with Path(path).open("r", encoding="utf-8", errors="replace") as fh:
            for lineno, line in enumerate(fh):
                line = line.strip()
                if not line:
                    continue
                if line.startswith("{"):
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    tick = _tick_from_json(obj)
                    if tick:
                        yield tick
                    continue
                if lineno == 0 and not line[:1].isdigit():
                    continue                      # سرستون
                parts = line.split(";")
                if len(parts) < 4:
                    parts = line.split(",")
                if len(parts) < 4:
                    continue
                try:
                    ms = int(float(parts[0]))
                    if ms > 10 ** 14:
                        ms //= 1000
                    elif ms < 10 ** 11:
                        ms *= 1000
                    bid = float(parts[2])
                    ask = float(parts[3])
                    volume = float(parts[5]) if len(parts) > 5 and parts[5] else 1.0
                except ValueError:
                    continue
                yield ms, bid, ask, volume
    except OSError:
        return


def collect_tick_files(root: Path, symbol: str, extra_dirs: Sequence[Path] = ()) -> list[Path]:
    """همهٔ فایل‌های تیک از harvester و از logs ربات، بدون تکرار."""
    files: list[Path] = []
    seen: set[str] = set()
    for directory in [Path(root), *[Path(d) for d in extra_dirs]]:
        for f in find_tick_files(directory, symbol):
            key = str(f.resolve())
            if key in seen or f.stat().st_size == 0:
                continue
            seen.add(key)
            files.append(f)
    return files


def load_candles(
    root: Path,
    symbol: str,
    cache_dir: Path | None = None,
    stats: dict[str, Any] | None = None,
    extra_dirs: Sequence[Path] = (),
) -> list[Candle]:
    """فایل‌های تیک نماد → کندل M1 (csv harvester یا jsonl لاگ ربات؛ تکراری‌ها یک‌بار).

    نتیجه در cache_dir کش می‌شود تا اجرای دوم روی ۷۵ مگابایت تیک، ثانیه‌ای باشد.
    """
    import gzip

    files = collect_tick_files(root, symbol, extra_dirs)
    if not files:
        return []
    stamp = "|".join(f"{f.name}:{f.stat().st_size}:{int(f.stat().st_mtime)}" for f in files if f.exists())
    import hashlib

    key = hashlib.sha1((symbol + "|" + stamp).encode("utf-8")).hexdigest()
    if cache_dir:
        cached = Path(cache_dir) / f"candles_{symbol}_{key}.json.gz"
        if cached.exists():
            try:
                with gzip.open(cached, "rt", encoding="utf-8") as fh:
                    payload = json.load(fh)
                if stats is not None and isinstance(payload.get("stats"), dict):
                    stats.update(payload["stats"])
                return [Candle(**item) for item in payload["candles"]]
            except (OSError, ValueError, TypeError, KeyError):
                pass

    seen: set[tuple[int, float]] = set()
    ticks: list[tuple[int, float, float, float]] = []
    per_file: dict[str, int] = {}
    for path in files:
        n = 0
        dup = 0
        for ms, bid, ask, volume in iter_tick_file(path):
            stamp_key = (ms, round(bid, 3))
            if stamp_key in seen:            # فایل‌های هم‌پوشان
                dup += 1
                if n < 20_000 and dup > 0.98 * (n + dup) and dup > 5_000:
                    LOG.info("رد کردن بقیهٔ %s (تکراری)", path.name)
                    break
                continue
            seen.add(stamp_key)
            ticks.append((ms, bid, ask, volume))
            n += 1
        per_file[path.name] = n
    ticks.sort(key=lambda x: x[0])
    candles = m1_candles_from_ticks(ticks)
    info = {
        "root": str(root),
        "files": per_file,
        "ticks": len(ticks),
        "kinds": sorted({probe_tick_file(f)["kind"] for f in files}),
        "first_ms": ticks[0][0] if ticks else 0,
        "last_ms": ticks[-1][0] if ticks else 0,
    }
    if stats is not None:
        stats.update(info)

    if cache_dir and candles:
        try:
            Path(cache_dir).mkdir(parents=True, exist_ok=True)
            with gzip.open(Path(cache_dir) / f"candles_{symbol}_{key}.json.gz", "wt", encoding="utf-8") as fh:
                json.dump({"candles": [asdict(c) for c in candles], "stats": info}, fh)
        except OSError:
            pass
    return candles


def read_tick_minutes(root: Path, symbol: str) -> dict[str, int]:
    """خلاصهٔ فایل‌های تیک (برای نمایش در گزارش)."""
    out: dict[str, int] = {}
    for path in find_tick_files(root, symbol):
        n = 0
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                next(fh, None)
                for _ in fh:
                    n += 1
        except OSError:
            continue
        out[path.name] = n
    return out


# --------------------------------------------------------------------------
# ویژگی‌ها
# --------------------------------------------------------------------------
FEATURE_NAMES: tuple[str, ...] = (
    "ret1_bp", "ret3_bp", "ret5_bp", "ret15_bp", "ret60_bp",
    "body_bp", "range_bp", "range_atr",
    "wick_up", "wick_down",
    "streak", "atr_bp", "vol_ratio",
    "tick_rate", "tick_ratio", "buy_pressure",
    "spread_bp", "spread_z",
    "ema20_dist", "ema60_dist",
    "velocity5", "accel", "trend_strength",
    "sin_hour", "cos_hour",
)


def _ema(values: Sequence[float], span: int) -> float:
    alpha = 2.0 / (span + 1.0)
    out = values[0]
    for v in values[1:]:
        out = alpha * v + (1 - alpha) * out
    return out


def _rsi(closes: Sequence[float], period: int = 14) -> float:
    if len(closes) < 2:
        return 50.0
    gains = losses = 0.0
    window = closes[-(period + 1):]
    for prev, cur in zip(window, window[1:]):
        delta = cur - prev
        gains += max(delta, 0.0)
        losses += max(-delta, 0.0)
    if gains + losses == 0:
        return 50.0
    return 100.0 * gains / (gains + losses)


def features_at(candles: Sequence[Candle], i: int) -> dict[str, float] | None:
    """ویژگی‌های لحظهٔ بسته‌شدن کندل i؛ هیچ داده‌ای بعد از i خوانده نمی‌شود."""
    if i < 61:
        return None
    cur = candles[i]
    close = cur.close
    if close <= 0:
        return None

    def ret(k: int) -> float:
        base = candles[i - k].close
        return ((close / base) - 1.0) * 1e4 if base else 0.0

    window20 = candles[i - 19: i + 1]
    rng = [c.range_bp for c in window20]
    atr = sum(rng) / len(rng) if rng else 0.0
    ticks20 = sum(c.ticks for c in window20) / len(window20) if window20 else 0.0
    closes = [c.close for c in candles[max(0, i - 120): i + 1]]

    spread = cur.spread_bps
    spreads = [c.spread_bps for c in window20]
    s_mean = sum(spreads) / len(spreads) if spreads else 0.0
    s_std = math.sqrt(sum((s - s_mean) ** 2 for s in spreads) / len(spreads)) if spreads else 0.0

    streak = 0
    for j in range(i, 0, -1):
        step = candles[j].close - candles[j - 1].close
        if step > 0 and streak >= 0:
            streak += 1
        elif step < 0 and streak <= 0:
            streak -= 1
        else:
            break
    hours = (cur.t % 86_400) / 3600.0
    total_ticks = cur.up_ticks + cur.down_ticks
    ret5 = ret(5)
    ret10_avg = ret(10) / 2.0

    return {
        "ret1_bp": ret(1),
        "ret3_bp": ret(3),
        "ret5_bp": ret5,
        "ret15_bp": ret(15),
        "ret60_bp": ret(60),
        "body_bp": cur.body_bp,
        "range_bp": cur.range_bp,
        "range_atr": cur.range_bp / atr if atr else 1.0,
        "wick_up": ((cur.high - max(cur.open, cur.close)) / close) * 1e4,
        "wick_down": ((min(cur.open, cur.close) - cur.low) / close) * 1e4,
        "streak": float(max(-10, min(10, streak))),
        "atr_bp": atr,
        "vol_ratio": cur.range_bp / atr if atr else 1.0,
        "tick_rate": float(cur.ticks),
        "tick_ratio": cur.ticks / ticks20 if ticks20 else 1.0,
        "buy_pressure": (cur.up_ticks - cur.down_ticks) / total_ticks if total_ticks else 0.0,
        "spread_bp": spread,
        "spread_z": (spread - s_mean) / s_std if s_std else 0.0,
        "ema20_dist": ((close / _ema(closes[-20:], 20)) - 1.0) * 1e4 if len(closes) >= 20 else 0.0,
        "ema60_dist": ((close / _ema(closes[-60:], 60)) - 1.0) * 1e4 if len(closes) >= 60 else 0.0,
        "velocity5": ret5 / 5.0,
        "accel": ret5 - ret10_avg,
        "trend_strength": abs(ret(15)) / atr if atr else 0.0,
        "sin_hour": math.sin(2 * math.pi * hours / 24.0),
        "cos_hour": math.cos(2 * math.pi * hours / 24.0),
    }


@dataclass
class Sample:
    t: int
    x: list[float]
    y: int                     # 0=up, 1=flat, 2=down
    forward_bp: float          # حرکت واقعی کندل i+1..i+horizon
    close: float


def build_samples(candles: Sequence[Candle], horizon: int = 3, flat_bp: float = 3.0) -> list[Sample]:
    """هدف: خالص حرکت از قیمت کندل i تا بسته‌شدن کندل i+horizon."""
    samples: list[Sample] = []
    for i in range(len(candles) - horizon):
        feats = features_at(candles, i)
        if feats is None:
            continue
        entry = candles[i].close
        exit_px = candles[i + horizon].close
        forward = ((exit_px / entry) - 1.0) * 1e4 if entry else 0.0
        if forward > flat_bp:
            y = 0
        elif forward < -flat_bp:
            y = 2
        else:
            y = 1
        samples.append(Sample(t=candles[i].t, x=[feats[n] for n in FEATURE_NAMES],
                              y=y, forward_bp=forward, close=entry))
    return samples


# --------------------------------------------------------------------------
# مدل‌ها (پایتون خالص)
# --------------------------------------------------------------------------
def _standardize(rows: Sequence[Sequence[float]]) -> tuple[list[float], list[float]]:
    n = len(rows)
    k = len(rows[0]) if n else 0
    mean = [0.0] * k
    for row in rows:
        for j, v in enumerate(row):
            mean[j] += v
    mean = [m / n for m in mean]
    std = [0.0] * k
    for row in rows:
        for j, v in enumerate(row):
            std[j] += (v - mean[j]) ** 2
    std = [math.sqrt(s / n) or 1.0 for s in std]
    return mean, std


def _apply(row: Sequence[float], mean: Sequence[float], std: Sequence[float]) -> list[float]:
    return [min(6.0, max(-6.0, (v - m) / s)) for v, m, s in zip(row, mean, std)]


def train_softmax(
    rows: Sequence[Sequence[float]],
    labels: Sequence[int],
    epochs: int = 250,
    lr: float = 0.35,
    l2: float = 1e-3,
    seed: int = 7,
) -> dict[str, Any]:
    """رگرسیون لجستیک چندکلاسه با گرادیان نزولی و کلاس‌های نامتوازن‌تراز‌شده."""
    mean, std = _standardize(rows)
    X = [_apply(r, mean, std) for r in rows]
    k = len(X[0]) if X else 0
    counts = [max(1, sum(1 for y in labels if y == c)) for c in range(len(LABELS))]
    weights_per_class = [len(labels) / (len(LABELS) * c) for c in counts]
    W = [[random.Random(seed + c).gauss(0, 0.01) for _ in range(k)] for c in range(len(LABELS))]
    b = [0.0] * len(LABELS)
    n = len(X)
    for _ in range(epochs):
        gradW = [[0.0] * k for _ in LABELS]
        gradb = [0.0] * len(LABELS)
        for row, y in zip(X, labels):
            z = [sum(w * v for w, v in zip(W[c], row)) + b[c] for c in range(len(LABELS))]
            m = max(z)
            ex = [math.exp(v - m) for v in z]
            tot = sum(ex)
            probs = [e / tot for e in ex]
            for c in range(len(LABELS)):
                err = (probs[c] - (1.0 if c == y else 0.0)) * weights_per_class[y]
                if abs(err) < 1e-12:
                    continue
                wc = gradW[c]
                for j, v in enumerate(row):
                    wc[j] += err * v
                gradb[c] += err
        for c in range(len(LABELS)):
            for j in range(k):
                W[c][j] -= lr * (gradW[c][j] / n + l2 * W[c][j])
            b[c] -= lr * gradb[c] / n
    return {"mean": mean, "std": std, "W": W, "b": b, "classes": list(LABELS)}


def predict_proba(model: dict[str, Any], row: Sequence[float]) -> list[float]:
    x = _apply(row, model["mean"], model["std"])
    z = [sum(w * v for w, v in zip(model["W"][c], x)) + model["b"][c] for c in range(len(model["classes"]))]
    m = max(z)
    ex = [math.exp(v - m) for v in z]
    tot = sum(ex)
    return [e / tot for e in ex]


# --------------------------------------------------------------------------
# ارزیابی راه‌رونده
# --------------------------------------------------------------------------
@dataclass
class Fold:
    name: str
    train_n: int
    test_n: int
    test_start: int
    test_end: int


def folds_of(n: int, n_folds: int = 5, embargo: int = 3, min_train: float = 0.3) -> list[Fold]:
    start = max(int(n * min_train), 60)
    if n - start < n_folds:
        return []
    size = (n - start) // n_folds
    out: list[Fold] = []
    for k in range(n_folds):
        a = start + k * size
        b = n if k == n_folds - 1 else a + size
        out.append(Fold(name=f"f{k + 1}", train_n=a - embargo, test_n=b - a, test_start=a, test_end=b))
    return out


def _evaluate(
    name: str,
    predict: Callable[[Sequence[float]], int],
    samples: Sequence[Sample],
    fold: Fold,
    cost_bp: float,
) -> dict[str, Any]:
    hits = 0
    bets = 0
    net = 0.0
    wins = 0
    rows: list[dict[str, Any]] = []
    for s in samples[fold.test_start: fold.test_end]:
        yhat = predict(s.x)
        ok = int(yhat == s.y)
        hits += ok
        rows.append({"t": s.t, "y": LABELS[s.y], "yhat": LABELS[yhat], "bp": round(s.forward_bp, 2)})
        if yhat != 1:  # خنثی یعنی معامله نکن
            bets += 1
            gross = s.forward_bp if yhat == 0 else -s.forward_bp
            net += gross - cost_bp
            wins += int(gross - cost_bp > 0)
    acc = hits / max(1, len(rows))
    return {
        "name": name,
        "n": len(rows),
        "accuracy": round(acc, 4),
        "bets": bets,
        "win_pct": round(100.0 * wins / bets, 2) if bets else 0.0,
        "net_bp": round(net, 2),
        "avg_bp": round(net / bets, 3) if bets else 0.0,
        "rows": rows,
    }


def run_lab(
    samples: Sequence[Sample],
    n_folds: int = 5,
    embargo: int = 3,
    cost_bp: float = 1.0,
    epochs: int = 250,
) -> dict[str, Any]:
    """چند مدل را روی همان چینش راه‌رونده می‌سنجد."""
    folds = folds_of(len(samples), n_folds=n_folds, embargo=embargo)
    if not folds:
        return {"ok": False, "error": "داده برای آموزش کافی نیست (حداقل ~۲۰۰ کندل لازم است)"}

    results: dict[str, list[dict[str, Any]]] = {
        "همیشه بالا": [], "بیشینهٔ کلاس": [], "قاعدهٔ تکانه": [], "رگرسیون لجستیک": [],
    }
    weights: list[list[float]] = []
    for fold in folds:
        train = samples[: fold.train_n]
        if not train:
            continue
        majority = max(range(3), key=lambda c: sum(1 for s in train if s.y == c))
        model = train_softmax([s.x for s in train], [s.y for s in train], epochs=epochs)
        weights.append([sum(abs(model["W"][c][j]) for c in range(3)) / 3.0
                        for j in range(len(FEATURE_NAMES))])
        idx_ret5 = FEATURE_NAMES.index("ret5_bp")
        rules: dict[str, Callable[[Sequence[float]], int]] = {
            "همیشه بالا": lambda x: 0,
            "بیشینهٔ کلاس": lambda x, m=majority: m,
            "قاعدهٔ تکانه": lambda x: 0 if x[idx_ret5] > 0 else 2 if x[idx_ret5] < 0 else 1,
            "رگرسیون لجستیک": lambda x, mo=model: max(range(3), key=lambda c: predict_proba(mo, x)[c]),
        }
        for name, fn in rules.items():
            results[name].append(_evaluate(name, fn, samples, fold, cost_bp))

    summary: list[dict[str, Any]] = []
    for name, per_fold in results.items():
        n = sum(f["n"] for f in per_fold)
        if not n:
            continue
        summary.append({
            "name": name,
            "n": n,
            "accuracy": round(sum(f["accuracy"] * f["n"] for f in per_fold) / n, 4),
            "bets": sum(f["bets"] for f in per_fold),
            "win_pct": round(sum(f["win_pct"] * f["bets"] for f in per_fold) / max(1, sum(f["bets"] for f in per_fold)), 2),
            "net_bp": round(sum(f["net_bp"] for f in per_fold), 2),
            "avg_bp": round(sum(f["net_bp"] for f in per_fold) / max(1, sum(f["bets"] for f in per_fold)), 3),
        })
    summary.sort(key=lambda r: -r["accuracy"])

    importance: list[dict[str, Any]] = []
    if weights:
        avg = [sum(col) / len(weights) for col in zip(*weights)]
        top = sorted(range(len(FEATURE_NAMES)), key=lambda j: -avg[j])[:12]
        importance = [{"feature": FEATURE_NAMES[j], "weight": round(avg[j], 4)} for j in top]

    return {
        "ok": True,
        "summary": summary,
        "folds": [asdict(f) for f in folds],
        "per_fold": {k: [{kk: vv for kk, vv in f.items() if kk != "rows"} for f in v] for k, v in results.items()},
        "importance": importance,
        "samples": len(samples),
        "folds_n": len(folds),
    }


# --------------------------------------------------------------------------
# گزارش
# --------------------------------------------------------------------------
def _svg_series(values: Sequence[float], w: int = 980, h: int = 220, color: str = "#38bdf8") -> str:
    if len(values) < 2:
        return ""
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    step = w / (len(values) - 1)
    pts = " ".join(f"{i * step:.1f},{h - ((v - lo) / span) * (h - 20) - 10:.1f}" for i, v in enumerate(values))
    zero = h - ((0 - lo) / span) * (h - 20) - 10 if lo <= 0 <= hi else None
    base = f'<line x1="0" y1="{zero:.1f}" x2="{w}" y2="{zero:.1f}" stroke="#334155" stroke-dasharray="4 4"/>' if zero else ""
    return (
        f'<svg viewBox="0 0 {w} {h}" style="width:100%;height:{h}px" dir="ltr">{base}'
        f'<polyline fill="none" stroke="{color}" stroke-width="2" points="{pts}"/></svg>'
    )


def render_html(data: dict[str, Any], title: str) -> str:
    summary = data["summary"]
    candles = data.get("candles", [])
    best = summary[0] if summary else {"name": "-", "accuracy": 0, "net_bp": 0}

    def pct(v: float) -> str:
        return f"{v * 100:.1f}%"

    cards = "".join(
        f'<div class="card"><div class="k">{html.escape(str(k))}</div><div class="v">{html.escape(str(v))}</div></div>'
        for k, v in [
            ("کندل M1", data.get("candles_n", 0)),
            ("نمونهٔ آموزش/آزمون", data.get("samples", 0)),
            ("افق", f"{data.get('horizon', 0)} کندل"),
            ("بهترین مدل", f"{best['name']} · {pct(best['accuracy'])}"),
            ("خالص بهترین", f"{best['net_bp']:+.1f} bp"),
        ]
    )

    rows = "".join(
        f'<tr class="{"best" if i == 0 else ""}"><td>{html.escape(r["name"])}</td><td>{r["n"]}</td>'
        f'<td><b>{pct(r["accuracy"])}</b></td><td>{r["bets"]}</td><td>{r["win_pct"]}%</td>'
        f'<td class="{"pos" if r["net_bp"] > 0 else "neg"}">{r["net_bp"]:+.1f}</td>'
        f'<td class="{"pos" if r["avg_bp"] > 0 else "neg"}">{r["avg_bp"]:+.2f}</td></tr>'
        for i, r in enumerate(summary)
    )

    fold_rows = ""
    for name, per in data.get("per_fold", {}).items():
        cells = "".join(f"<td>{pct(f['accuracy'])}</td>" for f in per)
        fold_rows += f"<tr><td>{html.escape(name)}</td>{cells}</tr>"
    fold_head = "".join(f"<th>{f['name']}</th>" for f in data.get("folds", []))

    imp = "".join(
        f'<tr><td>{html.escape(i["feature"])}</td><td>{i["weight"]:.4f}</td>'
        f'<td><div class="bar" style="width:{min(100.0, i["weight"] * 600):.0f}%"></div></td></tr>'
        for i in data.get("importance", [])
    )

    closes = [c["close"] for c in candles][-2000:]
    chart = _svg_series(closes)
    label_series = {lab: [c["label"] for c in candles][-2000:] for lab in LABELS}
    marker_html = ""
    if candles and data.get("importance") is not None:
        marker_html = "".join(
            f'<span class="pill {c["label"]}">{FA_LABEL[c["label"]]}</span>' for c in candles[-24:]
        )

    last = data.get("last", {})
    last_html = ""
    if last:
        prob = last.get("proba") or []
        bars = "".join(
            f'<div class="pbar"><span>{FA_LABEL[LABELS[i]]}</span>'
            f'<div class="bar {LABELS[i]}" style="width:{p * 100:.0f}%"></div><b>{p * 100:.0f}%</b></div>'
            for i, p in enumerate(prob)
        )
        last_html = (
            f'<h2>آخرین کندل: چه می‌گوید مدل؟</h2><div class="box">'
            f'<div class="muted">کندل {html.escape(str(last.get("time", "")))} · مدل: {html.escape(str(last.get("model", "")))}</div>'
            f'{bars}</div>'
        )

    return f"""<!doctype html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>
:root{{--bg:#0b1120;--card:#111c33;--line:#1f2b45;--fg:#e2e8f5;--muted:#8ba0c7;--up:#22c55e;--down:#ef4444;--flat:#94a3b8}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);font-family:system-ui,Vazirmatn,Tahoma,sans-serif;padding:22px}}
h1{{font-size:19px;margin:0 0 4px}} h2{{font-size:15px;margin:26px 0 8px;color:#c7d2fe}}
.muted{{color:var(--muted);font-size:12px}}
.cards{{display:flex;flex-wrap:wrap;gap:10px;margin:14px 0}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 14px;min-width:130px}}
.card .k{{font-size:11px;color:var(--muted)}} .card .v{{font-size:17px;font-weight:700;margin-top:3px}}
table{{width:100%;border-collapse:collapse;background:var(--card);border-radius:12px;overflow:hidden;font-size:13px}}
th,td{{padding:8px 10px;border-bottom:1px solid var(--line);text-align:right}}
th{{background:#16233c;color:var(--muted);font-weight:600}}
tr.best td{{background:#132a1f}}
.pos{{color:var(--up)}} .neg{{color:var(--down)}}
.box{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px}}
.bar{{display:inline-block;height:8px;border-radius:4px;background:#334155;vertical-align:middle}}
.bar.up{{background:var(--up)}} .bar.down{{background:var(--down)}} .bar.flat{{background:var(--flat)}}
.pbar{{display:flex;align-items:center;gap:8px;margin:6px 0;font-size:12px}}
.pbar span{{width:52px;color:var(--muted)}}
.pbar .bar{{height:12px}} .pbar b{{width:44px}}
.pill{{display:inline-block;padding:2px 8px;border-radius:99px;font-size:11px;margin:2px;border:1px solid var(--line)}}
.pill.up{{color:var(--up)}} .pill.down{{color:var(--down)}} .pill.flat{{color:var(--flat)}}
</style></head><body>
<h1>{html.escape(title)}</h1>
<div class="muted">{html.escape(str(data.get("subtitle", "")))}</div>
<div class="cards">{cards}</div>
<h2>مقایسهٔ مدل‌ها (آزمون راه‌رونده، خارج از نمونه)</h2>
<table><thead><tr><th>مدل</th><th>نمونه</th><th>دقت جهت</th><th>معامله</th><th>برد</th>
<th>خالص bp</th><th>میانگین bp</th></tr></thead><tbody>{rows}</tbody></table>
<div class="muted" style="margin-top:6px">هزینهٔ فرضی هر معامله {data.get("cost_bp", 1.0)} bp · «خنثی» معامله نمی‌شود.</div>
{last_html}
<h2>مسیر قیمت (کندل M1)</h2>
<div class="box">{chart}</div>
<h2>دقت هر فولد</h2>
<table><thead><tr><th>مدل</th>{fold_head}</tr></thead><tbody>{fold_rows}</tbody></table>
<h2>مهم‌ترین ویژگی‌ها</h2>
<table><thead><tr><th>ویژگی</th><th>وزن</th><th></th></tr></thead><tbody>{imp}</tbody></table>
<h2>آخرین پیش‌بینی‌ها</h2>
<div class="box">{marker_html}</div>
<div class="muted" style="margin-top:18px">داده: {html.escape(str(data.get("source", "")))} · ساخته‌شده {time.strftime("%Y-%m-%d %H:%M")}</div>
</body></html>"""


# --------------------------------------------------------------------------
# اجرا
# --------------------------------------------------------------------------
def _span(stats: dict[str, Any]) -> str:
    first = stats.get("first_ms") or 0
    last = stats.get("last_ms") or 0
    if not first:
        return ""
    fmt = "%Y-%m-%d %H:%M"
    return (time.strftime(fmt, time.gmtime(first / 1000)) + " → "
            + time.strftime(fmt, time.gmtime(last / 1000)) + " UTC")


def _verdict(data: dict[str, Any]) -> str:
    """یک خط ساده: مدل از قاعدهٔ ساده بهتر است یا نه."""
    rows = {r["name"]: r for r in data.get("summary", [])}
    model = rows.get("رگرسیون لجستیک")
    rule = rows.get("قاعدهٔ تکانه")
    best = data["summary"][0] if data.get("summary") else None
    if not model or not rule:
        return ""
    diff = model["net_bp"] - rule["net_bp"]
    if model["avg_bp"] <= 0:
        head = "مدل روی این داده سود ندارد (میانگین bp منفی یا صفر)"
    elif diff > 0:
        head = f"مدل از قاعدهٔ تکانه بهتر است ({diff:+.1f} bp)"
    else:
        head = f"قاعدهٔ تکانه از مدل بهتر است ({diff:+.1f} bp)"
    return (f"{head} · دقت مدل {model['accuracy'] * 100:.1f}% در برابر {rule['accuracy'] * 100:.1f}% · "
            f"بهترین: {best['name'] if best else '-'}")


def _own_label(candle: Candle, flat_bp: float) -> str:
    """رنگ کندل برای نمودار: بدنهٔ خودش، با همان آستانهٔ خنثی."""
    move = candle.body_bp
    return "up" if move > flat_bp else "down" if move < -flat_bp else "flat"


def run(
    root: Path,
    symbol: str,
    horizon: int = 3,
    flat_bp: float = 3.0,
    n_folds: int = 5,
    cost_bp: float = 1.0,
    epochs: int = 250,
    out_dir: Path | None = None,
    serve: bool = False,
    port: int = 8610,
    data_dirs: Sequence[Path] = (),
) -> dict[str, Any]:
    started = time.time()
    out = Path(out_dir) if out_dir else Path("var/lab")
    stats: dict[str, Any] = {}
    root = Path(root).expanduser()
    logs_dirs = [d for d in discover_tick_dirs(symbol, data_dirs) if str(d) != str(root)][:4]
    files = collect_tick_files(root, symbol, logs_dirs)
    if not files:
        tried = [str(root)] + [str(d) for d in logs_dirs[:5]]
        return {"ok": False, "error": "فایل تیک پیدا نشد. مسیرهای جست‌وجوشده: " + " · ".join(tried)}
    candles = load_candles(root, symbol, cache_dir=out / "cache", stats=stats, extra_dirs=logs_dirs)
    if len(candles) < 120:
        return {"ok": False, "error": f"کندل کافی نیست: {len(candles)} (حداقل ۱۲۰ لازم است)"}
    samples = build_samples(candles, horizon=horizon, flat_bp=flat_bp)
    if len(samples) < 120:
        return {"ok": False, "error": f"نمونهٔ کافی نیست: {len(samples)}"}
    folds = folds_of(len(samples), n_folds=n_folds, embargo=horizon)
    if folds:
        last_train = samples[: folds[-1].train_n]
        model = train_softmax([s.x for s in last_train], [s.y for s in last_train], epochs=epochs)
        last = {
            "time": time.strftime("%Y-%m-%d %H:%M", time.gmtime(samples[-1].t)) + " UTC",
            "model": "رگرسیون لجستیک (آموزش روی همهٔ گذشته)",
            "proba": [round(p, 4) for p in predict_proba(model, samples[-1].x)],
        }
    else:
        last = {}

    data = run_lab(samples, n_folds=n_folds, embargo=horizon, cost_bp=cost_bp, epochs=epochs)
    if not data.get("ok"):
        return data
    data.update({
        "symbol": symbol,
        "horizon": horizon,
        "flat_bp": flat_bp,
        "cost_bp": cost_bp,
        "candles_n": len(candles),
        "candles": [{"t": c.t, "close": round(c.close, 3), "label": _own_label(c, flat_bp)}
                    for c in candles],
        "source": stats.get("root", str(root)),
        "files": stats.get("files", {}),
        "sources": [stats.get("root", str(root))] + [str(d) for d in logs_dirs],
        "ticks": stats.get("ticks", 0),
        "kinds": stats.get("kinds", []),
        "span": _span(stats),
        "verdict": _verdict(data),
        "seconds": round(time.time() - started, 1),
        "last": last,
        "subtitle": f"{symbol} · {len(candles)} کندل M1 · هدف: جهت {horizon} کندل بعد",
    })
    html_text = render_html(data, f"آزمایشگاه مدل — {symbol}")

    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(html_text, encoding="utf-8")
    (out / "results.json").write_text(json.dumps({k: v for k, v in data.items() if k != "candles"},
                                                 ensure_ascii=False, indent=2), encoding="utf-8")
    data["html_path"] = str(out / "index.html")

    if serve:
        serve_lab(out, port=port)
    return data


def serve_lab(out_dir: Path, port: int = 8610, host: str = "0.0.0.0") -> None:
    """سرور کوچک برای دیدن گزارش در مرورگر (بدون وابستگی)."""
    import http.server
    import socketserver

    root = Path(out_dir)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a: Any, **k: Any) -> None:
            super().__init__(*a, directory=str(root), **k)

        def log_message(self, fmt: str, *a: Any) -> None:  # noqa: A003
            LOG.debug("lab %s", fmt % a)

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True

    LOG.info("آزمایشگاه روی http://%s:%d (Ctrl+C برای توقف)", host, port)
    with Server((host, port), Handler) as httpd:
        httpd.serve_forever()
