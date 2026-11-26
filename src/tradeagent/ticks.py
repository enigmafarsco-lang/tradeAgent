"""داده‌ی تیک: خواندن افزایشی CSV اکسپرت، تجمیع به کندل ۱ ثانیه‌ای، منبع ساختگی.

قرارداد فایل اکسپرت (``TickHarvester.mq5``)::

    MQL5/Files/harvester/symbols.json
    MQL5/Files/harvester/status.json
    MQL5/Files/harvester/ticks/<SYMBOL>/<YYYYMMDD>.csv
    time_msc;time;bid;ask;last;volume;flags

اسکریپت هر بار فقط خطوط تازه را می‌خواند (offset ذخیره می‌شود) تا هربار کل
فایل روز دوباره پارس نشود.
"""

from __future__ import annotations

import math
import random
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from .util import LOG, read_json, write_json


# --------------------------------------------------------------------------
# مدل داده
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Tick:
    time_msc: int
    bid: float
    ask: float
    last: float = 0.0
    volume: float = 0.0
    flags: int = 0

    @property
    def mid(self) -> float:
        if self.bid > 0 and self.ask >= self.bid:
            return (self.bid + self.ask) / 2.0
        return self.bid or self.ask or self.last

    @property
    def spread_bps(self) -> float:
        mid = self.mid
        if mid <= 0 or self.ask <= 0 or self.bid <= 0:
            return 0.0
        return ((self.ask - self.bid) / mid) * 1e4


@dataclass
class Bar:
    """کندل ۱ ثانیه‌ای که از تیک‌ها ساخته می‌شود (قیمت‌ها mid)."""

    t: int
    open: float
    high: float
    low: float
    close: float
    bid_close: float = 0.0
    ask_close: float = 0.0
    spread_bps_max: float = 0.0
    ticks: int = 0
    volume: float = 0.0

    @property
    def spread_bps(self) -> float:
        if self.bid_close > 0 and self.ask_close > 0 and self.close > 0:
            return ((self.ask_close - self.bid_close) / self.close) * 1e4
        return self.spread_bps_max

    @property
    def range_bps(self) -> float:
        if self.close <= 0:
            return 0.0
        return ((self.high - self.low) / self.close) * 1e4

    def as_tuple(self) -> tuple:
        return (
            self.t,
            self.open,
            self.high,
            self.low,
            self.close,
            self.bid_close,
            self.ask_close,
            self.spread_bps_max,
            self.ticks,
            self.volume,
        )


# --------------------------------------------------------------------------
# پارس و تجمیع
# --------------------------------------------------------------------------
def _to_float(text: str, default: float = 0.0) -> float:
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def parse_tick_line(line: str) -> Tick | None:
    """خط CSV اکسپرت را به Tick تبدیل می‌کند. سرصفحه/خط خراب → None."""
    if not line or line.startswith("time_msc"):
        return None
    parts = line.rstrip("\r\n").split(";")
    if len(parts) < 3:
        return None
    try:
        time_msc = int(parts[0])
    except ValueError:
        return None
    bid = _to_float(parts[2])
    ask = _to_float(parts[3]) if len(parts) > 3 else bid
    last = _to_float(parts[4]) if len(parts) > 4 else 0.0
    volume = _to_float(parts[5]) if len(parts) > 5 else 0.0
    flags = int(_to_float(parts[6])) if len(parts) > 6 else 0
    if bid <= 0 and ask <= 0 and last <= 0:
        return None
    if ask <= 0:
        ask = bid
    return Tick(time_msc=time_msc, bid=bid, ask=ask, last=last, volume=volume, flags=flags)


def aggregate_ticks(ticks: Iterable[Tick]) -> list[Bar]:
    """تیک‌ها را روی مرز ثانیه گروه می‌کند. ورودی لازم نیست مرتب باشد."""
    buckets: dict[int, list[Tick]] = {}
    for tick in ticks:
        if tick.time_msc <= 0:
            continue
        buckets.setdefault(tick.time_msc // 1000, []).append(tick)
    bars: list[Bar] = []
    for sec in sorted(buckets):
        group = sorted(buckets[sec], key=lambda t: t.time_msc)
        mids = [t.mid for t in group if t.mid > 0]
        if not mids:
            continue
        highs = [t.mid for t in group]
        bars.append(
            Bar(
                t=sec,
                open=mids[0],
                high=max(highs),
                low=min(highs),
                close=mids[-1],
                bid_close=group[-1].bid,
                ask_close=group[-1].ask,
                spread_bps_max=max((t.spread_bps for t in group), default=0.0),
                ticks=len(group),
                volume=sum(t.volume for t in group),
            )
        )
    return bars


def read_new_lines(path: Path, offset: int) -> tuple[list[str], int]:
    """خطوط کامل تازه از ``offset`` به بعد. خط نیمه‌نوشته برای بار بعد می‌ماند."""
    try:
        size = path.stat().st_size
    except OSError:
        return [], offset
    if size < offset:
        # فایل کوتاه شد (چرخش روز/پاک‌سازی): از صفر بخوان
        offset = 0
    if size == offset:
        return [], offset
    with open(path, "rb") as fh:
        fh.seek(offset)
        chunk = fh.read(size - offset)
    if not chunk:
        return [], offset
    cut = chunk.rfind(b"\n")
    if cut < 0:
        return [], offset
    good = chunk[: cut + 1]
    lines = good.decode("utf-8", errors="replace").splitlines()
    return lines, offset + len(good)


# --------------------------------------------------------------------------
# آمار خواندن
# --------------------------------------------------------------------------
@dataclass
class SymbolIngest:
    symbol: str
    new_ticks: int = 0
    new_bars: int = 0
    last_tick_ts: int = 0
    lag_s: float = 0.0
    files: int = 0
    stale: bool = False
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "new_ticks": self.new_ticks,
            "new_bars": self.new_bars,
            "last_tick_ts": self.last_tick_ts,
            "lag_s": round(self.lag_s, 1),
            "files": self.files,
            "stale": self.stale,
            "errors": self.errors,
        }


@dataclass
class IngestResult:
    bars: dict[str, list[Bar]]
    per_symbol: dict[str, SymbolIngest]
    source: str = "harvester"

    @property
    def total_new_bars(self) -> int:
        return sum(len(v) for v in self.bars.values())


# --------------------------------------------------------------------------
# منبع: پوشه‌ی اکسپرت
# --------------------------------------------------------------------------
class HarvesterSource:
    """خواندن افزایشی از ``<root>/ticks/<SYMBOL>/<day>.csv``."""

    def __init__(
        self,
        root: Path,
        symbols: Sequence[str] | None = None,
        state_path: Path | None = None,
        stale_after_s: float = 180.0,
    ) -> None:
        self.root = Path(root).expanduser()
        self.ticks_dir = self.root / "ticks"
        self.state_path = state_path
        self.forced_symbols = list(symbols or [])
        self.stale_after_s = stale_after_s
        self.state: dict[str, int] = read_json(self.state_path, {}) if state_path else {}

    # -- کشف نمادها -----------------------------------------------------
    def available_symbols(self) -> list[str]:
        if self.forced_symbols:
            return self.forced_symbols
        meta = read_json(self.root / "symbols.json", {})
        listed: list[str] = []
        if isinstance(meta, dict):
            raw = meta.get("symbols") or meta.get("selected") or []
            if isinstance(raw, list):
                listed = [str(item.get("symbol", item)) if isinstance(item, dict) else str(item) for item in raw]
        else:
            listed = [str(x) for x in (meta or [])]
        if listed:
            return listed
        if self.ticks_dir.exists():
            return sorted(p.name for p in self.ticks_dir.iterdir() if p.is_dir())
        return []

    def status(self) -> dict:
        data = read_json(self.root / "status.json", {})
        return data if isinstance(data, dict) else {}

    # -- خواندن --------------------------------------------------------
    def _symbol_files(self, symbol: str) -> list[Path]:
        folder = self.ticks_dir / symbol
        if not folder.exists():
            return []
        return sorted(folder.glob("*.csv"))

    def fetch(self, now_s: float | None = None) -> IngestResult:
        now_s = time.time() if now_s is None else now_s
        result = IngestResult(bars={}, per_symbol={})
        symbols = self.available_symbols()
        if not symbols:
            LOG.warning("هیچ نمادی پیدا نشد در %s", self.root)
        for symbol in symbols:
            info = SymbolIngest(symbol=symbol)
            ticks: list[Tick] = []
            for path in self._symbol_files(symbol):
                key = f"{symbol}/{path.name}"
                offset = int(self.state.get(key, 0))
                try:
                    lines, new_offset = read_new_lines(path, offset)
                except OSError as exc:
                    info.errors.append(f"{path.name}: {exc}")
                    continue
                info.files += 1
                if lines:
                    for line in lines:
                        tick = parse_tick_line(line)
                        if tick is not None:
                            ticks.append(tick)
                self.state[key] = new_offset
            if not ticks:
                # فایل‌ها ممکن است قدیمی باشند؛ باز هم تازگی را گزارش کن
                info.stale = True
                result.bars.setdefault(symbol, [])
            else:
                ticks.sort(key=lambda t: t.time_msc)
                info.new_ticks = len(ticks)
                info.last_tick_ts = ticks[-1].time_msc // 1000
                bars = aggregate_ticks(ticks)
                info.new_bars = len(bars)
                result.bars[symbol] = bars
            result.per_symbol[symbol] = info
        if self.state_path:
            write_json(self.state_path, self.state)
        for info in result.per_symbol.values():
            if info.last_tick_ts:
                info.lag_s = max(0.0, now_s - info.last_tick_ts)
                info.stale = info.lag_s > self.stale_after_s
            else:
                info.lag_s = float("inf")
                info.stale = True
        return result


# --------------------------------------------------------------------------
# منبع: داده‌ی ساختگی قطعی (برای تست و توسعه‌ی بدون متاتریدر)
# --------------------------------------------------------------------------
class SyntheticSource:
    """قدم‌زدن تصادفی با رژیم‌های نوسان؛ برای هر نماد و ثانیه قطعی است."""

    def __init__(
        self,
        symbols: Sequence[str],
        vol_bps_per_hour: float = 45.0,
        seed: int = 7,
        start_price: float = 100.0,
        spread_bps: float = 2.0,
    ) -> None:
        self.symbols = list(symbols)
        self.vol_bps_per_hour = vol_bps_per_hour
        self.seed = seed
        self.start_price = start_price
        self.spread_bps = spread_bps
        self._cache: dict[str, list[Bar]] = {}
        self._cache_until: int = 0

    def _rng(self, symbol: str) -> random.Random:
        return random.Random((zlib.crc32(symbol.encode()) ^ self.seed * 7919) & 0xFFFFFFFF)

    def _generate(self, symbol: str, start_s: int, end_s: int) -> list[Bar]:
        rng = self._rng(symbol)
        scale = 0.7 + (zlib.crc32(symbol.encode()) % 100) / 100.0
        price = self.start_price * (1 + (zlib.crc32(symbol.encode()) % 50) / 100.0)
        sigma = self.vol_bps_per_hour * scale / 1e4 / math.sqrt(3600.0)
        drift = 0.0
        bars: list[Bar] = []
        for sec in range(start_s, end_s):
            if sec % 1800 == 0:
                # تغییر رژیم: جهت و نوسان هر نیم ساعت
                drift = rng.choice([-1.0, -0.4, 0.0, 0.4, 1.0]) * sigma * 0.55
                sigma = (self.vol_bps_per_hour * scale / 1e4 / math.sqrt(3600.0)) * rng.choice(
                    [0.55, 0.8, 1.0, 1.5, 2.6]
                )
            step = rng.gauss(drift, sigma)
            open_px = price
            price = max(1e-6, price * (1 + step))
            wick = abs(rng.gauss(0, sigma)) * price
            hi = max(open_px, price) + wick * rng.random()
            lo = min(open_px, price) - wick * rng.random()
            spr = self.spread_bps * (0.6 + rng.random() * 1.2)
            half = spr / 2 / 1e4 * price
            bars.append(
                Bar(
                    t=sec,
                    open=open_px,
                    high=hi,
                    low=max(1e-9, lo),
                    close=price,
                    bid_close=price - half,
                    ask_close=price + half,
                    spread_bps_max=spr,
                    ticks=max(1, int(rng.expovariate(1 / 4.0)) + 1),
                    volume=round(rng.lognormvariate(0, 1), 4),
                )
            )
        return bars

    def fetch(self, since_by_symbol: dict[str, int] | None = None, end_s: int | None = None) -> IngestResult:
        """کندل‌های ثانیه‌ای از ``since_by_symbol`` (شامل) تا ``end_s``."""
        end = int(time.time()) if end_s is None else int(end_s)
        since = since_by_symbol or {}
        result = IngestResult(bars={}, per_symbol={}, source="synthetic")
        for symbol in self.symbols:
            last = int(since.get(symbol, 0))
            start = last + 1 if last else end - 3600
            bars = self._generate(symbol, start, end)
            result.bars[symbol] = bars
            result.per_symbol[symbol] = SymbolIngest(
                symbol=symbol,
                new_ticks=0,
                new_bars=len(bars),
                last_tick_ts=bars[-1].t if bars else 0,
                files=1,
            )
        return result
