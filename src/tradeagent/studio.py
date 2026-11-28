"""استودیو: ایجنت روی دادهٔ آفلاین می‌رود جلو، پیش‌بینی می‌کند و در یک اپ زنده دیده می‌شود.

تفاوت با `lab`: آنجا مدل‌های آماری آموزش می‌بینند؛ اینجا خودِ ایجنت (مدل زبانی) کندل‌به‌کندل
جلو می‌رود، از تیک و کندل‌های گذشته یک تصویر عددی می‌گیرد، جهت ۲–۳ کندل بعد را با دلیل فارسی
می‌گوید، بعد واقعیت را می‌بیند و امتیاز می‌گیرد.

سه قطعه:

* `walk`: حرکت گام‌به‌گام روی داده (هر `step_min` دقیقه یک تصمیم، افق `horizon` کندل)
* `llm_prediction`: فراخوانی ollama با کش روی دیسک؛ اگر مدل نبود، قاعدهٔ تکانه با برچسب `rule`
* `serve`: اپ مرورگری (بدون وابستگی) با چارت کندل، نشانگر پیش‌بینی‌ها، امتیاز زنده و دلیل‌ها
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from .lab import Candle, features_at, load_candles, m1_candles_from_ticks
from .util import LOG

MINUTE = 60

PREDICT_PROMPT = """تو یک تحلیل‌گر روند طلا/شاخص هستی و فقط با اعداد روبه‌رویی.
تصمیم: جهت حرکت از الان تا {horizon} کندل یک‌دقیقه‌ای بعد (یعنی حدود {horizon} دقیقه).

داده‌ی گذشته (همه از قبل از لحظهٔ تصمیم):
{digest}

فقط JSON بده، بدون توضیح بیرون آن:
{{"direction":"up|down|flat","strength":0..1,"confidence":0..1,
 "reason":"یک جملهٔ فارسی کوتاه با عدد"}}

قواعد: اگر نشانه‌ها متضادند direction را flat بگذار و confidence زیر ۰.۴. strength یعنی
قدرت حرکت، confidence یعنی اطمینان به جهت. دلیل حداکثر ۱۲ کلمهٔ فارسی. هیچ کلید دیگری اضافه نکن."""


@dataclass
class Trade:
    """معاملهٔ کاغذی که از تحلیل ایجنت باز می‌شود (خنثی معامله نمی‌کند)."""

    i: int
    t: int
    side: int                  # +1 خرید، -1 فروش
    entry: float
    reason: str
    source: str
    confidence: float
    exit_t: int | None = None
    exit_px: float | None = None
    bp: float | None = None
    hit: int | None = None


@dataclass
class Prediction:
    i: int                     # ایندکس کندلی که تصمیم روی آن گرفته شد
    t: int                     # ثانیهٔ آن کندل
    entry: float
    direction: str
    strength: float
    confidence: float
    reason: str
    source: str                # ollama | rule | cache
    actual_bp: float | None = None
    hit: int | None = None



def horizon_scan(candles: Sequence[Candle], horizons: Sequence[int] = (3, 5, 10, 15, 30, 60),
                 cost_bp: float = 1.0) -> list[dict[str, Any]]:
    """برای هر افق: میانگین |حرکت| و سهم حرکت‌هایی که از هزینه بزرگ‌ترند.

    سؤال اصلی: با این هزینه، کدام افق اصلاً جای سود دارد؟ اگر میانگین |حرکت| در افق از هزینه
    کمتر باشد، هیچ مدلی نمی‌تواند سود بسازد.
    """
    rows: list[dict[str, Any]] = []
    n = len(candles)
    for h in horizons:
        moves: list[float] = []
        for i in range(60, n - h):
            a, b = candles[i].close, candles[i + h].close
            if a:
                moves.append(abs((b / a - 1.0) * 1e4))
        if not moves:
            continue
        moves.sort()
        avg = sum(moves) / len(moves)
        p75 = moves[int(len(moves) * 0.75)]
        over = sum(1 for m in moves if m > 2 * cost_bp) / len(moves)
        rows.append({
            "horizon": h,
            "n": len(moves),
            "avg_abs_bp": round(avg, 2),
            "p75_bp": round(p75, 2),
            "share_over_2x_cost_pct": round(100.0 * over, 1),
            "viable": avg > 2 * cost_bp,
        })
    return rows


def render_scan(rows: Sequence[dict[str, Any]], cost_bp: float) -> str:
    lines = [
        f"افق | نمونه | میانگین |حرکت| | صدک ۷۵ | سهم >{cost_bp * 2:g}bp | سود ممکن؟",
        "--- | --- | --- | --- | --- | ---",
    ]
    for r in rows:
        lines.append(f"{r['horizon']} کندل | {r['n']} | {r['avg_abs_bp']:.2f}bp | {r['p75_bp']:.2f}bp | "
                     f"{r['share_over_2x_cost_pct']}% | {'بله' if r['viable'] else 'نه'}")
    return "\n".join(lines)

def _utc(ts: Any) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.gmtime(float(ts)))
    except (TypeError, ValueError):
        return "-"


def _digest(candles: Sequence[Candle], i: int, window: int = 30) -> dict[str, Any]:
    """تصویر عددی فشردهٔ گذشته برای مدل.

    نسخهٔ اول ~۷۰۰ توکن بود؛ روی CPU همین پرامپت تنها ۳۰–۶۰ ثانیه طول می‌کشید و پاسخ به مهلت
    نمی‌رسید. اینجا اعداد گرد شده و کلیدها کوتاه‌اند: همان اطلاعات، یک‌سوم توکن.
    """
    feats = features_at(candles, i) or {}
    recent = candles[max(0, i - 7): i + 1]
    return {
        # c = [open, high, low, close] برای ۸ کندل اخیر، قدیمی → جدید
        "c": [[round(c.open, 2), round(c.high, 2), round(c.low, 2), round(c.close, 2)] for c in recent],
        "f": {k: round(float(v), 2) for k, v in feats.items() if isinstance(v, (int, float))},
        "w": window,
        "unit": "bp=basis point, f=features of the last candle, c=last 8 M1 candles old→new",
    }


def _parse_reply(text: str, horizon: int) -> dict[str, Any]:
    raw = (text or "").strip()
    if "{" in raw and "}" in raw:
        raw = raw[raw.index("{"): raw.rindex("}") + 1]
    try:
        obj = json.loads(raw)
    except ValueError:
        return {}
    direction = str(obj.get("direction", "")).lower()
    if direction not in ("up", "down", "flat"):
        return {}
    def clamp(v: Any, default: float) -> float:
        try:
            return max(0.0, min(1.0, float(v)))
        except (TypeError, ValueError):
            return default
    return {
        "direction": direction,
        "strength": clamp(obj.get("strength"), 0.5),
        "confidence": clamp(obj.get("confidence"), 0.5),
        "reason": str(obj.get("reason") or "").strip()[:200],
        "horizon": horizon,
    }


def trend_rule(candles: Sequence[Candle], i: int, flat_bp: float = 0.0) -> dict[str, Any]:
    """قاعدهٔ سخت‌گیرانه‌تر: ۵ و ۱۵ دقیقه هم‌جهت، و حرکت از آستانه بیشتر.

    خط مبنای منصفانه: اگر همین ساده هم بهتر از هر مدلی بود، مشکل مدل نیست، داده/افق است.
    """
    c5 = candles[max(0, i - 5)].close
    c15 = candles[max(0, i - 15)].close
    m5 = ((candles[i].close / c5) - 1.0) * 1e4 if c5 else 0.0
    m15 = ((candles[i].close / c15) - 1.0) * 1e4 if c15 else 0.0
    if abs(m5) < flat_bp or (m5 > 0) != (m15 > 0):
        return {"direction": "flat", "strength": 0.2, "confidence": 0.3,
                "reason": f"۵ دقیقه {m5:+.1f}bp و ۱۵ دقیقه {m15:+.1f}bp؛ هم‌جهت نیستند یا زیر آستانه‌اند."}
    direction = "up" if m5 > 0 else "down"
    agree = min(abs(m5), abs(m15)) / max(abs(m5), abs(m15), 1e-9)
    return {"direction": direction, "strength": min(1.0, abs(m5) / 10.0),
            "confidence": round(0.4 + 0.4 * agree, 2),
            "reason": f"۵ دقیقه {m5:+.1f}bp و ۱۵ دقیقه {m15:+.1f}bp هر دو {direction}."}


def momentum_rule(candles: Sequence[Candle], i: int, flat_bp: float = 0.0) -> dict[str, Any]:
    """قاعدهٔ سادهٔ ۵ دقیقه — پیش‌فرض بدون مدل، و خط مبنا.

    اگر حرکت از آستانهٔ خنثی کمتر بود `flat` می‌گوید: هر دقیقه معامله باز نکن.
    """
    ref = candles[max(0, i - 5)].close
    move = ((candles[i].close / ref) - 1.0) * 1e4 if ref else 0.0
    if abs(move) <= flat_bp:
        return {"direction": "flat", "strength": abs(move) / max(flat_bp, 1e-9), "confidence": 0.3,
                "reason": f"حرکت ۵ کندل اخیر فقط {move:+.1f}bp است؛ زیر آستانهٔ {flat_bp:g}bp، خنثی."}
    direction = "up" if move > 0 else "down"
    return {"direction": direction, "strength": min(1.0, abs(move) / 10.0), "confidence": 0.5,
            "reason": f"تغییر ۵ کندل اخیر {move:+.1f}bp است، پس {direction}."}


class Studio:
    def __init__(
        self,
        root: Path,
        symbol: str,
        out_dir: Path,
        chat: Callable[[list[dict[str, str]]], str] | None = None,
        step_min: int = 1,
        horizon: int = 3,
        window: int = 30,
        flat_bp: float = 3.0,
        cost_bp: float = 1.0,
        min_conf: float = 0.0,
        extra_dirs: Sequence[Path] = (),
        skip_frozen: bool = True,
        report_dir: "Path | str | None" = None,
    ) -> None:
        self.root = Path(root)
        self.symbol = symbol
        self.out = Path(out_dir)
        # گزارش‌ها جایی می‌روند که گیت می‌بیند؛ var/ در .gitignore است و پوش نمی‌شود
        self.report_dir = Path(report_dir) if report_dir else self.out
        self.chat = chat
        self.step_min = max(1, int(step_min))
        self.horizon = max(1, int(horizon))
        self.window = max(10, int(window))
        self.flat_bp = flat_bp
        self.cost_bp = cost_bp
        self.min_conf = min_conf
        self.extra_dirs = [Path(d) for d in extra_dirs]
        self.data_stats: dict[str, Any] = {}
        self.skip_frozen = skip_frozen
        self.frozen_skipped = 0
        self.cache: dict[str, Any] = self._load_cache()
        self.candles: list[Candle] = []
        self.preds: list[Prediction] = []
        self.failures = 0
        self.last_error = ""
        self.model_down = False        # مدارشکن: بعد از چند خطای پشت‌سرهم، مدل را صدا نزن
        self._fail_streak = 0
        self.quota_hits = 0           # سقف فراخوانی، نه خرابی مدل
        self.quota_error = ""
        self._saved = 0
        self._cursor: int | None = None
        self._full_write = False
        self.trades: list[Trade] = []
        self.started = time.time()
        self.lock = threading.RLock()          # یک قدم در هر لحظه، حتی از دو جای مختلف
        self.thinking_since: float | None = None
        self.thinking_i: int | None = None

    # -- کش -------------------------------------------------------------
    def _cache_path(self) -> Path:
        return self.out / "llm_cache.json"

    def _load_cache(self) -> dict[str, Any]:
        try:
            data = json.loads(self._cache_path().read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_cache(self) -> None:
        if self.cache:
            try:
                self.out.mkdir(parents=True, exist_ok=True)
                self._cache_path().write_text(json.dumps(self.cache, ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass

    # -- پیش‌بینی --------------------------------------------------------
    def is_frozen(self, i: int) -> bool:
        """دقیقهٔ مرده: بازار بسته است یا قیمت تکان نخورده (تعطیلی/پایان هفته)."""
        c = self.candles[i]
        if c.ticks < 2:
            return True
        if c.high == c.low:
            return True
        ref = self.candles[max(0, i - 5)]
        return ref.close == c.close and c.high == c.low

    def predict(self, i: int) -> dict[str, Any]:
        digest = _digest(self.candles, i, self.window)
        key = hashlib.sha1(
            (json.dumps(digest, sort_keys=True, ensure_ascii=False) + f"|{self.horizon}").encode()
        ).hexdigest()
        if key in self.cache:
            out = dict(self.cache[key])
            out["source"] = "cache"
            return out
        if self.chat is None or self.model_down:
            rule = getattr(self, "rule", None)
            out = rule(i) if rule else momentum_rule(self.candles, i, self.flat_bp)
            out["source"] = "rule"
            return out
        prompt = PREDICT_PROMPT.format(
            horizon=self.horizon, digest=json.dumps(digest, ensure_ascii=False)
        )
        self.thinking_since = time.time()
        self.thinking_i = i
        try:
            messages = [
                {"role": "system", "content": "فقط JSON بده. همهٔ متن‌ها فارسی باشند."},
                {"role": "user", "content": prompt},
            ]
            try:
                reply = self.chat(messages, num_predict=160)
            except TypeError as exc:            # چتِ ساده که بودجه نمی‌فهمد
                if "num_predict" not in str(exc):
                    raise
                reply = self.chat(messages)
            parsed = _parse_reply(reply, self.horizon)
            if not parsed:
                raise ValueError("پاسخ مدل JSON معتبر نبود")
            parsed["source"] = "ollama"
            self.cache[key] = {k: v for k, v in parsed.items() if k != "source"}
            self._fail_streak = 0
            return parsed
        except Exception as exc:  # noqa: BLE001 - مدل ممکن است قطع باشد
            if getattr(exc, "quota", False):
                # سهمیه خطای مدل نیست: نه مدارشکن را می‌زند، نه «قطعی» نشان می‌دهد
                self.quota_hits += 1
                self.quota_error = str(exc)[:200]
                if self.quota_hits == 1:
                    LOG.warning("سقف فراخوانی مدل پر شد (%s)؛ این اجرا با قاعده جلو می‌رود. "
                                "برای برداشتن سقف: ./scripts/studio.sh --serve --max-calls-per-hour 0",
                                self.quota_error)
                out = momentum_rule(self.candles, i, self.flat_bp)
                out["source"] = "rule"
                return out
            self.failures += 1
            self._fail_streak += 1
            self.last_error = f"{type(exc).__name__}: {exc}"[:200]
            if self._fail_streak >= 3 and not self.model_down:
                self.model_down = True
                if "timeout" in self.last_error.lower() or "timed out" in self.last_error.lower():
                    LOG.warning("مدل سه بار به مهلت نرسید (روی CPU کند است)؛ تا پایان این اجرا کنار "
                                "گذاشته شد. مدل کوچک‌تر: ./scripts/studio.sh --serve "
                                "--model qwen2.5:1.5b-instruct · مهلت بیشتر: --timeout 300")
                else:
                    LOG.warning("مدل سه بار پشت‌سرهم خطا داد؛ تا پایان این اجرا کنار گذاشته شد. "
                                "برای عیب‌یابی: ./scripts/studio.sh --doctor")
            LOG.warning("پیش‌بینی با مدل نشد (%s)؛ قاعدهٔ تکانه جایش را می‌گیرد", self.last_error)
            out = momentum_rule(self.candles, i, self.flat_bp)
            out["source"] = "rule"
            return out
        finally:
            self.thinking_since = None
            self.thinking_i = None

    # -- امتیاز ----------------------------------------------------------
    def _score_ready(self, upto_i: int) -> None:
        self._close_trades(upto_i)
        for p in self.preds:
            if p.hit is not None or p.i + self.horizon > upto_i:
                continue
            exit_px = self.candles[p.i + self.horizon].close
            bp = ((exit_px / p.entry) - 1.0) * 1e4 if p.entry else 0.0
            p.actual_bp = round(bp, 3)
            if p.direction == "flat":
                p.hit = int(abs(bp) <= self.flat_bp)
            elif p.direction == "up":
                p.hit = int(bp > 0)
            else:
                p.hit = int(bp < 0)

    def stats(self) -> dict[str, Any]:
        scored = [p for p in self.preds if p.hit is not None]
        directional = [p for p in scored if p.direction != "flat"]
        bets = [p for p in scored if p.direction != "flat"]
        net = sum((p.actual_bp if p.direction == "up" else -p.actual_bp) - self.cost_bp for p in bets)
        return {
            "predictions": len(self.preds),
            "scored": len(scored),
            "hits": sum(p.hit or 0 for p in scored),
            "hit_pct": round(100.0 * sum(p.hit or 0 for p in scored) / len(scored), 1) if scored else 0.0,
            "bets": len(bets),
            "dir_scored": len(directional),
            "dir_hit_pct": round(100.0 * sum(p.hit or 0 for p in directional) / len(directional), 1)
            if directional else 0.0,
            "net_bp": round(net, 2) if bets else 0.0,
            "avg_bp": round(net / len(bets), 2) if bets else 0.0,
            "failures": self.failures,
            "model_down": self.model_down,
            "quota_hits": self.quota_hits,
            "quota_error": self.quota_error,
            "last_error": self.last_error,
        }

    # -- یک قدم ---------------------------------------------------------
    def ensure_data(self) -> bool:
        if not self.candles:
            from .lab import discover_tick_dirs, find_tick_files

            extra = list(self.extra_dirs)
            if not find_tick_files(self.root, self.symbol):     # داده کنار مخزن نیست؛ در logs بگرد
                extra += [d for d in discover_tick_dirs(self.symbol, [self.root])
                          if str(d) != str(self.root)]
            self.candles = load_candles(self.root, self.symbol, cache_dir=self.out / "cache",
                                        extra_dirs=extra, stats=self.data_stats)
        return len(self.candles) >= self.window + self.horizon + 5

    def step(self) -> dict[str, Any] | None:
        """یک تصمیم جلو می‌رود (همان دکمهٔ «قدم بعد» در اپ)."""
        if not self.ensure_data():
            return None
        if self._cursor is None:
            self._cursor = self.window - 1
        stop = len(self.candles) - self.horizon
        i = self._cursor
        if i >= stop:
            return None
        if self.skip_frozen and self.is_frozen(i):
            self.frozen_skipped += 1
            self._cursor = i + max(1, int(getattr(self, "_step_override", 0) or self.step_min))
            return {"skipped": True, "i": i}
        pred = self.predict(i)
        point = Prediction(
            i=i, t=self.candles[i].t, entry=self.candles[i].close,
            direction=pred["direction"], strength=float(pred.get("strength", 0.5)),
            confidence=float(pred.get("confidence", 0.5)),
            reason=str(pred.get("reason", "")), source=pred.get("source", "?"),
        )
        self.preds.append(point)
        self._score_ready(i)
        self._maybe_trade(point)
        self._cursor = i + max(1, int(getattr(self, "_step_override", 0) or self.step_min))
        self.save()
        return asdict(point)

    def rewind(self) -> None:
        """برگرد به اول داده: دفتر تصمیم‌ها و معاملات پاک می‌شود."""
        self.preds = []
        self.trades = []
        self._cursor = None
        self._saved = 0
        self.failures = 0
        self.frozen_skipped = 0
        self.last_error = ""
        self._full_write = True
        self.save()

    def seek(self, steps: int) -> int:
        """تا قدم شمارهٔ `steps` جلو برو (از اول، با همان داده و کش)."""
        if not self.ensure_data():
            return 0
        self.rewind()
        made = 0
        target = max(0, int(steps))
        while made < target:
            res = self.step()
            if res is None:
                break
            if res.get("skipped"):
                continue
            made += 1
        self.save()
        return made

    def step_back(self) -> bool:
        """یک تصمیم عقب: دفتر از نو ساخته می‌شود تا قدم قبل."""
        cur = self._cursor if self._cursor is not None else 0
        start = self.window - 1
        step_units = max(1, int(getattr(self, "_step_override", 0) or self.step_min))
        target = max(0, (cur - start) // step_units - 1)
        if target <= 0:
            self.rewind()
            return False
        self.seek(target)
        return True

    def _maybe_trade(self, point: Prediction) -> None:
        if point.direction == "flat" or point.confidence < self.min_conf:
            return
        self.trades.append(Trade(
            i=point.i, t=point.t, side=1 if point.direction == "up" else -1,
            entry=point.entry, reason=point.reason, source=point.source,
            confidence=point.confidence,
        ))

    def _close_trades(self, upto_i: int) -> None:
        for tr in self.trades:
            if tr.hit is not None or tr.i + self.horizon > upto_i:
                continue
            exit_px = self.candles[tr.i + self.horizon].close
            bp = ((exit_px / tr.entry) - 1.0) * 1e4 if tr.entry else 0.0
            tr.exit_t = self.candles[tr.i + self.horizon].t
            tr.exit_px = round(exit_px, 3)
            tr.bp = round(tr.side * bp - self.cost_bp, 3)
            tr.hit = int(tr.bp > 0)

    def trade_stats(self) -> dict[str, Any]:
        closed = [t for t in self.trades if t.hit is not None]
        wins = sum(t.hit or 0 for t in closed)
        net = sum(t.bp or 0.0 for t in closed)
        return {
            "trades": len(self.trades),
            "closed": len(closed),
            "open": len(self.trades) - len(closed),
            "wins": wins,
            "win_pct": round(100.0 * wins / len(closed), 1) if closed else 0.0,
            "net_bp": round(net, 2),
            "avg_bp": round(net / len(closed), 2) if closed else 0.0,
            "cost_paid_bp": round(len(closed) * self.cost_bp, 1),
            "gross_bp": round(net + len(closed) * self.cost_bp, 2),
        }

    # -- حرکت ------------------------------------------------------------
    def walk(self, step_override: int | None = None, limit: int | None = None,
             start_i: int | None = None,
             progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
        if not self.ensure_data():
            return {"ok": False, "error": f"کندل کافی نیست: {len(self.candles)}"}
        n = len(self.candles)
        if n < self.window + self.horizon + 5:
            return {"ok": False, "error": f"کندل کافی نیست: {n}"}
        step = max(1, int(step_override or self.step_min))
        start = max(self.window - 1, start_i if start_i is not None else self.window - 1)
        stop = n - self.horizon
        made = 0
        total = len(range(start, stop, step))
        if limit:
            total = min(total, limit)
        self._cursor = start
        self._step_override = step
        try:
            while True:
                if limit and made >= limit:
                    break
                res = self.step()
                if res is None:
                    break
                if res.get("skipped"):
                    continue
                made += 1
                if made % 25 == 0 or made == total:
                    self.save()
                    if progress:
                        elapsed = time.time() - self.started
                        rate = made / elapsed if elapsed else 0.0
                        progress({"made": made, "total": total,
                                  "eta_s": int((total - made) / rate) if rate else 0,
                                  **self.stats()})
        finally:
            self._step_override = 0
        self._score_ready(n - 1)
        self._close_trades(n - 1)
        self.save()
        return {"ok": True, **self.stats(), "candles": n, "step_min": step}


    def scan(self) -> list[dict[str, Any]]:
        if not self.ensure_data():
            return []
        return horizon_scan(self.candles, cost_bp=self.cost_bp)

    # -- گزارش برای بازبینی ---------------------------------------------
    def review(self) -> dict[str, Any]:
        """خلاصهٔ قابل فرستادن: چه چیزی، روی چه داده‌ای، با چه دقتی."""
        cov = self.coverage()
        scored = [p for p in self.preds if p.hit is not None]
        directional = [p for p in scored if p.direction != "flat"]

        def bucket(key_fn):
            out: dict[str, list[int]] = {}
            for p in scored:
                out.setdefault(str(key_fn(p)), []).append(p.hit or 0)
            return {k: {"n": len(v), "hit_pct": round(100.0 * sum(v) / len(v), 1)}
                    for k, v in sorted(out.items())}

        return {
            "symbol": self.symbol,
            "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "data": cov,
            "settings": {
                "horizon_candles": self.horizon,
                "step_min": self.step_min,
                "flat_bp": self.flat_bp,
                "cost_bp": self.cost_bp,
                "min_conf": self.min_conf,
                "window": self.window,
            },
            "totals": {
                "decisions": len(self.preds),
                "scored": len(scored),
                "hit_pct": round(100.0 * sum(p.hit or 0 for p in scored) / len(scored), 1) if scored else 0.0,
                "directional": len(directional),
                "directional_hit_pct": round(100.0 * sum(p.hit or 0 for p in directional) / len(directional), 1)
                if directional else 0.0,
                "avg_actual_bp": round(sum(p.actual_bp or 0 for p in scored) / len(scored), 2) if scored else 0.0,
                "model_errors": self.failures,
            "model_down": self.model_down,
            "quota_hits": self.quota_hits,
            "quota_error": self.quota_error,
            "frozen_skipped": self.frozen_skipped,
            "avg_abs_move_bp": round(sum(abs(p.actual_bp or 0) for p in scored) / len(scored), 2)
            if scored else 0.0,
            "breakeven_ok": bool(scored) and (sum(abs(p.actual_bp or 0) for p in scored) / len(scored))
            > self.cost_bp,
            },
            "trades": self.trade_stats(),
            "by_direction": bucket(lambda p: p.direction),
            "by_source": bucket(lambda p: p.source),
            "by_hour": bucket(lambda p: time.strftime("%H", time.gmtime(p.t))),
            "recent": [
                {"t": time.strftime("%m-%d %H:%M", time.gmtime(p.t)),
                 "direction": p.direction, "confidence": p.confidence,
                 "entry": p.entry, "actual_bp": p.actual_bp, "hit": p.hit,
                 "source": p.source, "reason": p.reason}
                for p in self.preds[-30:]
            ],
        }

    def review_markdown(self) -> str:
        r = self.review()
        d, st, tot, tr = r["data"], r["settings"], r["totals"], r["trades"]
        lines = [
            f"# گزارش استودیو — {r['symbol']}",
            "",
            f"ساخته‌شده: {r['generated']}",
            "",
            "## داده",
            f"- منبع: `{d.get('source')}`",
            f"- فایل‌ها: {d.get('files', 0)} · تیک: {d.get('ticks', 0):,} · کندل M1: {d.get('candles', 0)}",
            f"- بازه: {_utc(d.get('first'))} → {_utc(d.get('last'))} · شکاف: {d.get('gaps', 0)}",
            "",
            "## تنظیمات",
            f"- افق: {st['horizon_candles']} کندل M1 · گام: {st['step_min']} دقیقه",
            f"- آستانهٔ خنثی: {st['flat_bp']}bp · هزینهٔ هر معامله: {st['cost_bp']}bp · کمترین اطمینان: {st['min_conf']}",
            "",
            "## نتیجه",
            f"- تصمیم: {tot['decisions']} · سنجیده: {tot['scored']} · دقت کل: **{tot['hit_pct']}%**",
            f"- فقط جهت‌دار (up/down): {tot['directional']} تصمیم · دقت: **{tot['directional_hit_pct']}%**",
            f"- میانگین حرکت واقعی بعد از افق: {tot['avg_actual_bp']:+.2f}bp · خطای مدل: {tot['model_errors']}",
            f"- دقیقه‌های مرده که رد شدند (بازار بسته): {tot.get('frozen_skipped', 0)}",
            f"- میانگین |حرکت| در افق: {tot.get('avg_abs_move_bp', 0):.2f}bp در برابر هزینهٔ {st['cost_bp']}bp "
            f"→ {'سربه‌سر ممکن است' if tot.get('breakeven_ok') else 'هزینه از حرکت بزرگ‌تر است؛ با این افق سود ممکن نیست'}",
            "",
            "## معاملات کاغذی",
            f"- بازشده: {tr['trades']} · بسته: {tr['closed']} · باز: {tr['open']}",
            f"- برد: {tr['wins']} ({tr['win_pct']}%) · خالص: {tr['net_bp']:+.2f}bp · "
            f"ناخالص: {tr.get('gross_bp', 0):+.2f}bp · هزینه: {tr.get('cost_paid_bp', 0):.0f}bp",
            "",
            "## تفکیک",
            "",
            "| گروه | تعداد | دقت |",
            "| --- | --- | --- |",
        ]
        for title, table in (("جهت", r["by_direction"]), ("منبع", r["by_source"])):
            for k, v in table.items():
                lines.append(f"| {title}: {k} | {v['n']} | {v['hit_pct']}% |")
        for k, v in r["by_hour"].items():
            lines.append(f"| ساعت {k} UTC | {v['n']} | {v['hit_pct']}% |")
        lines += [
            "",
            "## ۳۰ تصمیم آخر",
            "",
            "| زمان | جهت | ورود | واقعی | درست | منبع | دلیل |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for p in reversed(r["recent"]):
            mark = "—" if p["hit"] is None else ("✓" if p["hit"] else "✗")
            actual = "—" if p["actual_bp"] is None else f"{p['actual_bp']:+.2f}bp"
            reason = (p["reason"] or "").replace("|", "/")[:80]
            lines.append(f"| {p['t']} | {p['direction']} | {p['entry']} | {actual} | {mark} | {p['source']} | {reason} |")
        lines.append("")
        return "\n".join(lines)

    # -- ذخیره -----------------------------------------------------------
    def coverage(self) -> dict[str, Any]:
        """چه داده‌ای خوانده شد: فایل‌ها، تیک، کندل، شکاف‌ها."""
        cs = self.candles
        gaps = sum(1 for a, b in zip(cs, cs[1:]) if (b.t - a.t) > 60)
        return {
            "candles": len(cs),
            "first": cs[0].t if cs else 0,
            "last": cs[-1].t if cs else 0,
            "gaps": gaps,
            "files": len(self.data_stats.get("files") or {}),
            "ticks": self.data_stats.get("ticks", 0),
            "source": self.data_stats.get("root", str(self.root)),
        }

    def state(self) -> dict[str, Any]:
        candles = self.candles
        return {
            "symbol": self.symbol,
            "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "horizon": self.horizon,
            "step_min": self.step_min,
            "stats": self.stats(),
            "candles": [{"t": c.t, "o": round(c.open, 3), "h": round(c.high, 3),
                         "l": round(c.low, 3), "c": round(c.close, 3), "n": c.ticks}
                        for c in candles[-1500:]],
            "preds": [asdict(p) for p in self.preds[-400:]],
            "trades": [asdict(t) for t in self.trades[-200:]],
            "trade_stats": self.trade_stats(),
            "candles_total": len(candles),
            "coverage": self.coverage(),
            "cursor": self._cursor,
            "step_index": len(self.preds),
            "thinking": (None if self.thinking_since is None else
                         {"i": self.thinking_i,
                          "elapsed_s": round(time.time() - self.thinking_since, 1)}),
            "total_steps": max(0, len(self.candles) - self.horizon - (self.window - 1)),
            "baselines": self._baselines(),
        }

    def _baselines(self) -> dict[str, Any]:
        scored = [p for p in self.preds if p.hit is not None and p.actual_bp is not None]
        if not scored:
            return {}
        up_net = sum(p.actual_bp - self.cost_bp for p in scored)
        rule_hits = 0
        for p in scored:
            ref = self.candles[max(0, p.i - 5)].close
            direction = "up" if self.candles[p.i].close >= ref else "down"
            hit = (p.actual_bp > 0) if direction == "up" else (p.actual_bp < 0)
            rule_hits += int(hit)
        return {
            "همیشه بالا": {"hit_pct": round(100.0 * sum(1 for p in scored if p.actual_bp > 0) / len(scored), 1),
                           "net_bp": round(up_net, 2)},
            "قاعدهٔ ۵ دقیقه": {"hit_pct": round(100.0 * rule_hits / len(scored), 1), "net_bp": None},
        }

    def save(self) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        try:
            (self.out / "state.json").write_text(json.dumps(self.state(), ensure_ascii=False), encoding="utf-8")
            mode = "w" if self._full_write else "a"
            fresh = self.preds if self._full_write else self.preds[self._saved:]
            if fresh or self._full_write:
                with (self.out / "predictions.jsonl").open(mode, encoding="utf-8") as fh:
                    for p in fresh:
                        fh.write(json.dumps(asdict(p), ensure_ascii=False) + "\n")
            self._full_write = False
            self._saved = len(self.preds)
            review = self.review()
            (self.out / "review.json").write_text(json.dumps(review, ensure_ascii=False, indent=2),
                                                   encoding="utf-8")
            review_md = self.review_markdown()
            (self.out / "review.md").write_text(review_md, encoding="utf-8")
            with (self.out / "review.csv").open("w", encoding="utf-8") as fh:
                fh.write("time_utc,direction,confidence,entry,actual_bp,hit,source,reason\n")
                for p in self.preds:
                    fh.write(",".join([
                        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(p.t)), p.direction,
                        f"{p.confidence:.2f}", f"{p.entry:.3f}",
                        "" if p.actual_bp is None else f"{p.actual_bp:.3f}",
                        "" if p.hit is None else str(p.hit), p.source,
                        '"' + (p.reason or "").replace('"', "'") + '"',
                    ]) + "\n")
            if self.report_dir != self.out:
                self.report_dir.mkdir(parents=True, exist_ok=True)
                (self.report_dir / "review.md").write_text(review_md, encoding="utf-8")
                (self.report_dir / "review.json").write_text(
                    json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
                (self.report_dir / "review.csv").write_text(
                    (self.out / "review.csv").read_text(encoding="utf-8"), encoding="utf-8")
            self._save_cache()
        except OSError as exc:
            LOG.warning("ذخیرهٔ وضعیت استودیو نشد: %s", exc)


# --------------------------------------------------------------------------
# اپ مرورگری
# --------------------------------------------------------------------------
def app_html(symbol: str, port: int) -> str:
    return """<!doctype html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>استودیو ایجنت — SYMBOL</title>
<style>
:root{--bg:#0b1120;--card:#111c33;--line:#1f2b45;--fg:#e2e8f5;--muted:#8ba0c7;--up:#22c55e;--down:#ef4444}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font-family:system-ui,Vazirmatn,Tahoma,sans-serif;padding:16px}
h1{font-size:18px;margin:0} h2{font-size:14px;margin:16px 0 6px;color:#c7d2fe}
.muted{color:var(--muted);font-size:12px}
.cards{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:8px 12px;min-width:105px}
.card .k{font-size:11px;color:var(--muted)} .card .v{font-size:16px;font-weight:700}
button{background:#1d4ed8;color:#fff;border:0;border-radius:10px;padding:9px 16px;font-size:14px;cursor:pointer;margin-inline-end:6px}
button.ghost{background:#1e293b;border:1px solid var(--line)}
button:disabled{opacity:.5;cursor:default}
canvas{width:100%;background:var(--card);border:1px solid var(--line);border-radius:12px;margin-top:8px}
table{width:100%;border-collapse:collapse;font-size:12px;background:var(--card);border-radius:12px;overflow:hidden;margin-top:8px}
th,td{padding:6px 9px;border-bottom:1px solid var(--line);text-align:right}
th{background:#16233c;color:var(--muted)}
.up{color:var(--up)} .down{color:var(--down)} .flat{color:var(--muted)}
.pill{padding:1px 7px;border-radius:99px;border:1px solid var(--line);font-size:11px}
.box{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:9px 12px;font-size:13px;line-height:1.9}
.pos{color:var(--up)} .neg{color:var(--down)}
</style></head><body>
<h1>استودیو ایجنت — <span id="sym">SYMBOL</span> <span class="muted" id="prog"></span></h1>
<div class="muted" id="upd">…</div>
<div id="alert" style="display:none;margin:8px 0;padding:9px 12px;border-radius:10px;
     background:#3b1d1d;border:1px solid #7f1d1d;color:#fecaca;font-size:13px"></div>
<div style="margin:10px 0">
  <button class="ghost" onclick="api('reset')">⏮ از اول</button>
  <button class="ghost" onclick="api('prev')">◀ عقب</button>
  <button id="b1" onclick="go(1)">قدم بعد ▶</button>
  <button class="ghost" onclick="go(5)">۵ قدم</button>
  <button class="ghost" onclick="go(25)">۲۵ قدم</button>
  <button class="ghost" onclick="go(0)">تا آخر</button>
  <a class="ghost" href="review.md" target="_blank" style="color:#c7d2fe;text-decoration:none;padding:8px 12px;border:1px solid var(--line);border-radius:10px">📄 گزارش بازبینی</a>
</div>
<div style="display:flex;align-items:center;gap:10px;margin:6px 0 10px">
  <input id="seek" type="range" min="0" max="0" value="0" style="flex:1"
         oninput="document.getElementById('seekv').textContent=this.value"
         onchange="api('goto', this.value)">
  <span class="muted">قدم <b id="seekv">0</b></span>
</div>
<div class="cards" id="cards"></div>
<div style="margin:8px 0">
  <button class="ghost" onclick="setWin(90)">۹۰ کندل</button>
  <button class="ghost" onclick="setWin(180)">۱۸۰ کندل</button>
  <button class="ghost" onclick="setWin(360)">۳۶۰ کندل</button>
  <button class="ghost" onclick="toggleFollow()" id="fb">همراه تصمیم: روشن</button>
  <span class="muted" id="cov"></span>
</div>
<div class="box" id="what" style="margin-top:8px"></div>
<canvas id="chart" height="400"></canvas>
<div class="muted" id="legend">▲/▼ = تصمیم ایجنت روی کندل · خط‌چین = از قیمت تصمیم تا قیمت واقعی بعد از افق · سبز یعنی درست</div>
<h2>معاملات کاغذی</h2>
<table><thead><tr><th>زمان</th><th>جهت</th><th>ورود</th><th>خروج</th><th>bp</th><th>نتیجه</th><th>اطمینان</th><th>دلیل</th></tr></thead>
<tbody id="trades"><tr><td colspan="8" class="muted">…</td></tr></tbody></table>
<h2>آخرین تصمیم‌ها</h2>
<table><thead><tr><th>زمان</th><th>جهت</th><th>اطمینان</th><th>منبع</th><th>واقعی</th><th>درست</th><th>دلیل</th></tr></thead>
<tbody id="rows"><tr><td colspan="7" class="muted">…</td></tr></tbody></table>
<script>
let state=null, busy=false;
const fmt=t=>new Date(t*1000).toISOString().slice(5,16).replace('T',' ');
let WIN = 180;           // چند کندل روی چارت
let follow = true;       // پنجره همراه تصمیم فعلی جلو برود
function draw(){
  const c=document.getElementById('chart'); if(!state||!state.candles||!state.candles.length) return;
  const dpr=window.devicePixelRatio||1; c.width=c.clientWidth*dpr; c.height=400*dpr;
  const ctx=c.getContext('2d'); ctx.setTransform(dpr,0,0,dpr,0,0);
  const W=c.clientWidth,H=400,padL=8,padR=64,padT=10,padB=22;
  const all=state.candles;
  const idxOf={}; all.forEach((x,i)=>{idxOf[x.t]=i;});
  // پنجره: تا تصمیم فعلی (یا آخر داده)
  const lastIdx=all.length-1;
  // تصمیم فعلی حدود ۶۵٪ عرض بنشیند تا حرکت روی چارت دیده شود، نه پرش به انتها
  const cur = (state.cursor!==null && state.cursor!==undefined)
    ? state.cursor
    : ((state.step_index||0) > 0 ? lastIdx : 30);   // بعد از «از اول» چارت به آغاز داده برمی‌گردد
  let startI = Math.round(cur - WIN*0.65);
  startI = Math.max(0, Math.min(startI, Math.max(0, all.length-WIN-1)));
  const endI = Math.min(lastIdx, startI + WIN);
  const cs=all.slice(startI, endI+1);
  if(!cs.length) return;
  const predsIn=(state.preds||[]).filter(p=>idxOf[p.t]!==undefined && idxOf[p.t]>=startI && idxOf[p.t]<=endI);
  const extra=cs.length+state.horizon+2;             // جای نمودار آینده
  const futures=[];                                   // پیش‌بینی آینده: از آخرین تصمیم
  const lastPred=(state.preds||[])[state.preds.length-1];
  const hi0=Math.max(...cs.map(x=>x.h)), lo0=Math.min(...cs.map(x=>x.l));
  const hi=hi0, lo=lo0;
  const plotW=W-padL-padR;
  const step=plotW/extra;
  const y=v=>padT+(hi-v)/((hi-lo)||1)*(H-padT-padB);
  const xAt=k=>padL+k*step+step/2;                     // k = ایندکس داخل پنجره
  ctx.fillStyle='#0b1120'; ctx.fillRect(0,0,W,H);
  // خطوط قیمت
  ctx.font='11px system-ui'; ctx.textAlign='right';
  for(let g=0; g<=4; g++){
    const v=lo+(hi-lo)*g/4, yy=y(v);
    ctx.strokeStyle='rgba(31,43,69,.8)'; ctx.beginPath(); ctx.moveTo(padL,yy); ctx.lineTo(W-padR,yy); ctx.stroke();
    ctx.fillStyle='#8ba0c7'; ctx.fillText(v.toFixed(2), W-padR+56, yy+4);
  }
  // کندل‌ها
  const bw=Math.max(1.2, step*0.62);
  cs.forEach((x,k)=>{
    const cx=xAt(k);
    ctx.strokeStyle=x.c>=x.o?'#22c55e':'#ef4444'; ctx.lineWidth=1;
    ctx.beginPath(); ctx.moveTo(cx,y(x.h)); ctx.lineTo(cx,y(x.l)); ctx.stroke();
    ctx.fillStyle=ctx.strokeStyle;
    const top=y(Math.max(x.o,x.c)), bot=y(Math.min(x.o,x.c));
    ctx.fillRect(cx-bw/2, top, bw, Math.max(1,bot-top));
  });
  // پیش‌بینی‌ها: نشانگر + خط تا قیمت واقعی بعد از افق
  predsIn.forEach(p=>{
    const k=idxOf[p.t]-startI;
    const cx=xAt(k), ey=y(p.entry);
    ctx.fillStyle=p.direction==='up'?'#22c55e':p.direction==='down'?'#ef4444':'#94a3b8';
    ctx.beginPath(); ctx.arc(cx, ey+(p.direction==='down'?8:-8), 3.4, 0, 6.28); ctx.fill();
    const k2=k+state.horizon;
    if(k2<=cs.length-1 && p.actual_bp!==null){
      const px2=cs[k2].c;
      ctx.strokeStyle=p.hit?'rgba(34,197,94,.95)':'rgba(239,68,68,.95)';
      ctx.setLineDash([3,3]); ctx.lineWidth=1.4;
      ctx.beginPath(); ctx.moveTo(cx,ey); ctx.lineTo(xAt(k2),y(px2)); ctx.stroke(); ctx.setLineDash([]);
      ctx.setLineDash([]); 
      ctx.fillStyle=ctx.strokeStyle;
      ctx.beginPath(); ctx.arc(xAt(k2),y(px2),2.4,0,6.28); ctx.fill();
    }
  });
  // نمودار آینده: مسیر پیش‌بینی‌شدهٔ افق بعد از کندلِ تصمیم فعلی
  if(lastPred){
    const k0raw = (idxOf[lastPred.t]!==undefined ? idxOf[lastPred.t] : (state.cursor||cs.length-1)) - startI;
    const k0 = Math.max(0, Math.min(k0raw, cs.length-1));
    const p0=y(cs[k0].c);
    const dir=lastPred.direction==='up'?1:lastPred.direction==='down'?-1:0;
    const scale=Math.max(2,(hi-lo)*0.18)*Math.max(0.35,lastPred.confidence||0.5);
    ctx.strokeStyle=dir>0?'#22c55e':dir<0?'#ef4444':'#94a3b8';
    ctx.setLineDash([6,4]); ctx.lineWidth=2;
    ctx.beginPath(); ctx.moveTo(xAt(k0), p0);
    const kEnd=k0+state.horizon;
    ctx.lineTo(xAt(kEnd), y(cs[k0].c+dir*scale)); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle=ctx.strokeStyle;
    ctx.beginPath(); ctx.arc(xAt(kEnd), y(cs[k0].c+dir*scale), 3.5, 0, 6.28); ctx.fill();
    ctx.font='11px system-ui'; ctx.textAlign='left';
    ctx.fillText('آیندهٔ '+(lastPred.direction==='up'?'صعودی':lastPred.direction==='down'?'نزولی':'خنثی'),
                 Math.max(40, Math.min(W-70, xAt(kEnd)+4)), Math.max(14, y(cs[k0].c+dir*scale)-8));
  }
  // خط «اکنون»: کجای تاریخ ایستاده‌ایم
  if(state.cursor!==null && state.cursor!==undefined && state.cursor<=lastIdx){
    const k=state.cursor-startI;
    if(k>=0 && k<cs.length){
      ctx.strokeStyle='rgba(250,204,21,.55)'; ctx.setLineDash([2,4]); ctx.lineWidth=1;
      ctx.beginPath(); ctx.moveTo(xAt(k),padT); ctx.lineTo(xAt(k),H-padB); ctx.stroke(); ctx.setLineDash([]);
      ctx.fillStyle='rgba(250,204,21,.9)'; ctx.textAlign='center'; ctx.font='11px system-ui';
      ctx.fillText('اکنون', xAt(k), padT+11);
    }
  }
  // محور زمان
  ctx.fillStyle='#8ba0c7'; ctx.textAlign='center'; ctx.font='11px system-ui';
  [0, Math.floor(cs.length/2), cs.length-1].forEach(k=>{
    const d=new Date(cs[k].t*1000); ctx.fillText(d.toISOString().slice(5,16).replace('T',' '), xAt(k), H-6);
  });
  draw._win = {startI, endI};
}
let lastSig='';
function render(force){
  if(!state) return;
  const sig=(state.updated||'')+'|'+(state.step_index||0)+'|'+((state.trades||[]).length);
  if(!force && sig===lastSig){ draw(); return; }   // چیزی عوض نشده: صفحه را نلرزان
  lastSig=sig;
  const s=state.stats||{}, ts=state.trade_stats||{}, cov=state.coverage||{};
  document.getElementById('upd').textContent='آخرین به‌روزرسانی: '+state.updated+
    ' · افق '+state.horizon+' کندل · گام '+state.step_min+' دقیقه'+
    (s.last_error?(' · خطای مدل: '+s.last_error.slice(0,60)):'');
  document.getElementById('cov').textContent = cov.candles ?
    ('داده: '+(cov.files||0)+' فایل · '+(cov.ticks||0).toLocaleString('en-US')+' تیک · '+
     cov.candles+' کندل M1 · '+fmt(cov.first)+' → '+fmt(cov.last)+' · شکاف: '+(cov.gaps||0)) : '';
  document.getElementById('prog').textContent='قدم '+(state.step_index||s.predictions||0)+' از '+(state.total_steps||0);
  const seek=document.getElementById('seek');
  if(seek){ seek.max=state.total_steps||0; seek.value=state.step_index||0;
            document.getElementById('seekv').textContent=state.step_index||0; }
  const cards=[['پیش‌بینی',s.predictions||0],['سنجیده',s.scored||0],['درست',(s.hit_pct||0)+'%'],
    ['خالص تحلیل',((s.net_bp||0)>=0?'+':'')+(s.net_bp||0)+'bp'],
    ['دقت جهت‌دار',(s.dir_hit_pct||0)+'%'],['معامله',(ts.closed||0)+'/'+(ts.trades||0)],['برد',(ts.win_pct||0)+'%'],
    ['خالص معاملات',((ts.net_bp||0)>=0?'+':'')+(ts.net_bp||0)+'bp'],
    ['هزینه',(ts.cost_paid_bp||0)+'bp'],['باز',ts.open||0],
    ['مدل', s.model_down?'قطع':((s.quota_hits||0)>0?'سقف‌خورده':'آماده')]];
  const lp=(state.preds||[])[(state.preds||[]).length-1];
  const what=document.getElementById('what');
  if(what){
    if(lp){
      const from=fmt(lp.t), toT=lp.t+state.horizon*60;
      const to=fmt(toT);
      what.innerHTML='<b>این چه چیزی را پیش‌بینی می‌کند؟</b> جهت حرکت قیمت از بسته‌شدن کندل '+
        '<b>'+from+'</b> با قیمت <b>'+lp.entry+'</b> تا <b>'+state.horizon+'</b> کندل یک‌دقیقه‌ای بعد '+
        '(یعنی حدود ساعت <b>'+to+'</b>) — تصمیم: <b class="'+lp.direction+'">'+
        (lp.direction==='up'?'صعودی':lp.direction==='down'?'نزولی':'خنثی')+
        '</b> با اطمینان '+lp.confidence.toFixed(2)+' · منبع: '+lp.source+
        '<br><span class="muted">خط زرد = همان کندل تصمیم (اکنون) · خط‌چین رنگی از آن کندل به راست = مسیر پیش‌بینی‌شده · '+
        'خط‌چین‌های سبز/سرخ در گذشته = هر تصمیم و نتیجهٔ واقعی‌اش</span>';
    } else {
      what.textContent='هنوز تصمیمی گرفته نشده؛ «قدم بعد» را بزن.';
    }
  }
  const th=state.thinking, banner=document.getElementById('alert');
  if(th && banner){
    banner.style.display='block';
    banner.style.background='#1e3a8a';
    banner.innerHTML='🧠 مدل روی کندل '+(th.i)+' فکر می‌کند… '+th.elapsed_s+' ثانیه. '+
      'روی CPU هر تصمیم می‌تواند ۲۰–۹۰ ثانیه بگیرد؛ صبر کن، صفحه را نبند.';
  }
  const alert=document.getElementById('alert');
  if(alert){
    if(s.model_down || (s.failures||0)>0){
      alert.style.display='block';
      alert.style.background='#7f1d1d';
      alert.innerHTML='⚠️ مدل جواب نمی‌دهد ('+(s.failures||0)+' خطا'+(s.last_error?': '+s.last_error.slice(0,80):'')+
        ') — الان با قاعدهٔ تکانه جلو می‌رود. عیب‌یابی: <code>./scripts/studio.sh --doctor</code>';
    } else if((s.quota_hits||0)>0){
      alert.style.display='block';
      alert.style.background='#92400e';
      alert.innerHTML='⏳ سقف فراخوانی مدل در ساعت پر شده ('+(s.quota_hits)+' بار) — تصمیم‌ها با قاعده گرفته می‌شوند. '+
        'برداشتن سقف: <code>./scripts/studio.sh --serve --max-calls-per-hour 0</code>';
    } else if(!th) { alert.style.display='none'; }
  }
  document.getElementById('cards').innerHTML=cards.map(([k,v])=>`<div class="card"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('');
  const rows=(state.preds||[]).slice(-20).reverse().map(p=>{
    const act=p.actual_bp===null?'—':((p.actual_bp>0?'+':'')+p.actual_bp+'bp');
    const ok=p.hit===null?'—':(p.hit?'✓':'✗');
    return `<tr><td>${fmt(p.t)}</td><td class="${p.direction}">${p.direction}</td>
      <td>${p.confidence.toFixed(2)}</td><td><span class="pill">${p.source}</span></td>
      <td>${act}</td><td>${ok}</td><td>${p.reason||''}</td></tr>`;}).join('');
  document.getElementById('rows').innerHTML=rows||'<tr><td colspan="7" class="muted">هنوز تصمیمی نیست</td></tr>';
  const trs=(state.trades||[]).slice(-20).reverse().map(t=>{
    const cls=t.bp===null?'':(t.bp>0?'pos':'neg');
    const res=t.hit===null?'باز':(t.hit?'برد':'باخت');
    return `<tr><td>${fmt(t.t)}</td><td class="${t.side>0?'up':'down'}">${t.side>0?'خرید':'فروش'}</td>
      <td>${t.entry}</td><td>${t.exit_px===null?'—':t.exit_px}</td>
      <td class="${cls}">${t.bp===null?'—':((t.bp>0?'+':'')+t.bp)}</td><td>${res}</td>
      <td>${t.confidence.toFixed(2)}</td><td>${t.reason||''}</td></tr>`;}).join('');
  document.getElementById('trades').innerHTML=trs||'<tr><td colspan="8" class="muted">هنوز معامله‌ای نیست</td></tr>';
  draw();
}
function setWin(n){ WIN=n; draw(); }
function toggleFollow(){ follow=!follow; document.getElementById('fb').textContent='همراه تصمیم: '+(follow?'روشن':'خاموش'); draw(); }
async function api(verb, n){
  if(busy) return; busy=true;
  const btn=document.getElementById('b1'); if(btn) btn.disabled=true;
  try{
    const q=(n===undefined||verb==='reset'||verb==='prev')?'':('?n='+n);
    const r=await fetch('api/'+verb+q,{cache:'no-store'});
    state=await r.json(); render(true);
  }catch(e){} finally{ busy=false; if(btn) btn.disabled=false; }
}
async function pull(){ try{const r=await fetch('api/state',{cache:'no-store'}); state=await r.json(); render();}catch(e){} }
async function go(n){
  if(busy) return; busy=true;
  const btn=document.getElementById('b1'); btn.disabled=true; btn.textContent='…';
  try{
    const r=await fetch('api/next?n='+n,{cache:'no-store'});
    state=await r.json(); render(true);
  }catch(e){} finally{ busy=false; btn.disabled=false; btn.textContent='قدم بعد ▶'; }
}
pull(); setInterval(pull,5000);
window.addEventListener('resize',draw);
</script></body></html>""".replace("SYMBOL", symbol)


def serve(
    state_dir: Path,
    symbol: str,
    port: int = 8620,
    host: str = "0.0.0.0",
    studio: "Studio | None" = None,
    on_ready: "Callable[[int], None] | None" = None,
) -> None:
    """سرور اپ: صفحه + /api/state + /api/next?n= (دکمهٔ «قدم بعد»).

    اگر `studio` بدهی، دکمه‌ها واقعاً ایجنت را جلو می‌برند؛ وگرنه فقط state.json خوانده می‌شود.
    """
    import http.server
    import socketserver
    import threading

    root = Path(state_dir)
    html_page = app_html(symbol, port).encode("utf-8")
    lock = threading.Lock()

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, body: bytes, ctype: str, code: int = 200) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                self._send(html_page, "text/html; charset=utf-8")
                return
            if studio is not None and path.startswith("/api/") and path.split("?")[0] in (
                    "/api/next", "/api/prev", "/api/reset", "/api/goto"):
                import urllib.parse

                qs = urllib.parse.parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
                verb = path.split("?")[0]
                try:
                    n = int(qs.get("n", ["1"])[0])
                except ValueError:
                    n = 1
                with lock, studio.lock:      # قدم‌ها با گرم‌کردن پس‌زمینه قاطی نشوند
                    if not studio.ensure_data():
                        self._send(json.dumps({"error": "داده کافی نیست"}).encode(), "application/json")
                        return
                    if verb == "/api/reset":
                        studio.rewind()
                    elif verb == "/api/prev":
                        studio.step_back()
                    elif verb == "/api/goto":
                        studio.seek(n)
                    elif n <= 0:                     # تا آخر
                        studio.walk()
                    else:
                        made = 0
                        while made < min(n, 500):
                            res = studio.step()
                            if res is None:
                                break
                            if res.get("skipped"):
                                continue
                            made += 1
                    studio.save()
                    body = json.dumps(studio.state(), ensure_ascii=False).encode()
                self._send(body, "application/json; charset=utf-8")
                return
            if path.startswith("/api/next") and studio is not None:
                n = 1
                if "?" in self.path:
                    import urllib.parse

                    qs = urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    try:
                        n = int(qs.get("n", ["1"])[0])
                    except ValueError:
                        n = 1
                with lock, studio.lock:      # قدم‌ها با گرم‌کردن پس‌زمینه قاطی نشوند
                    if not studio.ensure_data():
                        self._send(json.dumps({"error": "داده کافی نیست"}).encode(), "application/json")
                        return
                    if n <= 0:                       # تا آخر
                        studio.walk()
                    else:
                        for _ in range(min(n, 500)):
                            if studio.step() is None:
                                break
                    studio.save()
                    body = json.dumps(studio.state(), ensure_ascii=False).encode()
                self._send(body, "application/json; charset=utf-8")
                return
            if path in ("/review.md", "/review.csv", "/review.json"):
                try:
                    body = (root / path.lstrip("/")).read_bytes()
                except OSError:
                    body = b"not ready"
                ctype = ("text/markdown; charset=utf-8" if path.endswith(".md")
                         else "text/csv; charset=utf-8" if path.endswith(".csv")
                         else "application/json; charset=utf-8")
                self._send(body, ctype)
                return
            if path in ("/api/state", "/state.json"):
                try:
                    data = (json.dumps(studio.state(), ensure_ascii=False).encode()
                            if studio is not None else (root / "state.json").read_bytes())
                except (OSError, TypeError):
                    data = json.dumps({"candles": [], "preds": [], "stats": {},
                                       "baselines": {}, "repeated": 0}).encode()
                self._send(data, "application/json; charset=utf-8")
                return
            self._send(b"not found", "text/plain; charset=utf-8", 404)

        def log_message(self, fmt: str, *a: Any) -> None:  # noqa: A003
            LOG.debug("studio %s", fmt % a)

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    with Server((host, port), Handler) as httpd:
        real_port = httpd.server_address[1]
        LOG.info("استودیو: http://%s:%d", host, real_port)
        if on_ready is not None:
            on_ready(real_port)
        httpd.serve_forever()
