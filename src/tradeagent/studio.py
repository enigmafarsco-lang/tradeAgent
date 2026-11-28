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

import json
import hashlib
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
قدرت حرکت، confidence یعنی اطمینان به جهت. هیچ کلید دیگری اضافه نکن."""


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


def _digest(candles: Sequence[Candle], i: int, window: int = 30) -> dict[str, Any]:
    """تصویر عددی گذشته برای مدل: کندل‌های اخیر + ویژگی‌های مشتق‌شده."""
    feats = features_at(candles, i) or {}
    recent = candles[max(0, i - window + 1): i + 1]
    return {
        "candles": [
            {"t": c.t, "o": round(c.open, 3), "h": round(c.high, 3), "l": round(c.low, 3),
             "c": round(c.close, 3), "ticks": c.ticks, "up": c.up_ticks, "down": c.down_ticks,
             "spread_bp": round(c.spread_bps, 2)}
            for c in recent[-12:]
        ],
        "features": {k: round(v, 3) for k, v in feats.items()},
        "window_candles": len(recent),
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
    ) -> None:
        self.root = Path(root)
        self.symbol = symbol
        self.out = Path(out_dir)
        self.chat = chat
        self.step_min = max(1, int(step_min))
        self.horizon = max(1, int(horizon))
        self.window = max(10, int(window))
        self.flat_bp = flat_bp
        self.cost_bp = cost_bp
        self.min_conf = min_conf
        self.extra_dirs = [Path(d) for d in extra_dirs]
        self.data_stats: dict[str, Any] = {}
        self.cache: dict[str, Any] = self._load_cache()
        self.candles: list[Candle] = []
        self.preds: list[Prediction] = []
        self.failures = 0
        self.last_error = ""
        self._saved = 0
        self._cursor: int | None = None
        self.trades: list[Trade] = []
        self.started = time.time()

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
    def predict(self, i: int) -> dict[str, Any]:
        digest = _digest(self.candles, i, self.window)
        key = hashlib.sha1(
            (json.dumps(digest, sort_keys=True, ensure_ascii=False) + f"|{self.horizon}").encode()
        ).hexdigest()
        if key in self.cache:
            out = dict(self.cache[key])
            out["source"] = "cache"
            return out
        if self.chat is None:
            out = momentum_rule(self.candles, i, self.flat_bp)
            out["source"] = "rule"
            return out
        prompt = PREDICT_PROMPT.format(
            horizon=self.horizon, digest=json.dumps(digest, ensure_ascii=False)
        )
        try:
            reply = self.chat([
                {"role": "system", "content": "فقط JSON بده. همهٔ متن‌ها فارسی باشند."},
                {"role": "user", "content": prompt},
            ])
            parsed = _parse_reply(reply, self.horizon)
            if not parsed:
                raise ValueError("پاسخ مدل JSON معتبر نبود")
            parsed["source"] = "ollama"
            self.cache[key] = {k: v for k, v in parsed.items() if k != "source"}
            return parsed
        except Exception as exc:  # noqa: BLE001 - مدل ممکن است قطع باشد
            self.failures += 1
            self.last_error = f"{type(exc).__name__}: {exc}"[:200]
            LOG.warning("پیش‌بینی با مدل نشد (%s)؛ قاعدهٔ تکانه جایش را می‌گیرد", self.last_error)
            out = momentum_rule(self.candles, i, self.flat_bp)
            out["source"] = "rule"
            return out

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
                if self.step() is None:
                    break
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
            fresh = self.preds[self._saved:]
            if fresh:
                with (self.out / "predictions.jsonl").open("a", encoding="utf-8") as fh:
                    for p in fresh:
                        fh.write(json.dumps(asdict(p), ensure_ascii=False) + "\n")
            self._saved = len(self.preds)
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
.pos{color:var(--up)} .neg{color:var(--down)}
</style></head><body>
<h1>استودیو ایجنت — <span id="sym">SYMBOL</span> <span class="muted" id="prog"></span></h1>
<div class="muted" id="upd">…</div>
<div style="margin:10px 0">
  <button id="b1" onclick="go(1)">قدم بعد ▶</button>
  <button class="ghost" onclick="go(5)">۵ قدم</button>
  <button class="ghost" onclick="go(25)">۲۵ قدم</button>
  <button class="ghost" onclick="go(0)">تا آخر</button>
</div>
<div class="cards" id="cards"></div>
<div style="margin:8px 0">
  <button class="ghost" onclick="setWin(90)">۹۰ کندل</button>
  <button class="ghost" onclick="setWin(180)">۱۸۰ کندل</button>
  <button class="ghost" onclick="setWin(360)">۳۶۰ کندل</button>
  <button class="ghost" onclick="toggleFollow()" id="fb">همراه تصمیم: روشن</button>
  <span class="muted" id="cov"></span>
</div>
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
  const anchor = (follow && state.cursor) ? Math.min(state.cursor+state.horizon+2, lastIdx) : lastIdx;
  const endI=Math.max(WIN, anchor), startI=Math.max(0, endI-WIN);
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
  // نمودار آینده: مسیر پیش‌بینی‌شدهٔ افق بعد از آخرین تصمیم
  if(lastPred){
    const k0=cs.length-1;
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
                 Math.min(W-70, xAt(kEnd)+4), Math.max(14, y(cs[k0].c+dir*scale)-8));
  }
  // محور زمان
  ctx.fillStyle='#8ba0c7'; ctx.textAlign='center'; ctx.font='11px system-ui';
  [0, Math.floor(cs.length/2), cs.length-1].forEach(k=>{
    const d=new Date(cs[k].t*1000); ctx.fillText(d.toISOString().slice(5,16).replace('T',' '), xAt(k), H-6);
  });
  draw._win = {startI, endI};
}
function render(){
  if(!state) return;
  const s=state.stats||{}, ts=state.trade_stats||{}, cov=state.coverage||{};
  document.getElementById('upd').textContent='آخرین به‌روزرسانی: '+state.updated+
    ' · افق '+state.horizon+' کندل · گام '+state.step_min+' دقیقه'+
    (s.last_error?(' · خطای مدل: '+s.last_error.slice(0,60)):'');
  document.getElementById('cov').textContent = cov.candles ?
    ('داده: '+(cov.files||0)+' فایل · '+(cov.ticks||0).toLocaleString('en-US')+' تیک · '+
     cov.candles+' کندل M1 · '+fmt(cov.first)+' → '+fmt(cov.last)+' · شکاف: '+(cov.gaps||0)) : '';
  document.getElementById('prog').textContent='قدم '+(s.predictions||0)+' از '+(state.total_steps||0);
  const cards=[['پیش‌بینی',s.predictions||0],['سنجیده',s.scored||0],['درست',(s.hit_pct||0)+'%'],
    ['خالص تحلیل',((s.net_bp||0)>=0?'+':'')+(s.net_bp||0)+'bp'],
    ['دقت جهت‌دار',(s.dir_hit_pct||0)+'%'],['معامله',(ts.closed||0)+'/'+(ts.trades||0)],['برد',(ts.win_pct||0)+'%'],
    ['خالص معاملات',((ts.net_bp||0)>=0?'+':'')+(ts.net_bp||0)+'bp'],
    ['هزینه',(ts.cost_paid_bp||0)+'bp'],['باز',ts.open||0]];
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
async function pull(){ try{const r=await fetch('api/state',{cache:'no-store'}); state=await r.json(); render();}catch(e){} }
async function go(n){
  if(busy) return; busy=true;
  const btn=document.getElementById('b1'); btn.disabled=true; btn.textContent='…';
  try{
    const r=await fetch('api/next?n='+n,{cache:'no-store'});
    state=await r.json(); render();
  }catch(e){} finally{ busy=false; btn.disabled=false; btn.textContent='قدم بعد ▶'; }
}
pull(); setInterval(pull,3000);
window.addEventListener('resize',draw);
</script></body></html>""".replace("SYMBOL", symbol)


def serve(
    state_dir: Path,
    symbol: str,
    port: int = 8620,
    host: str = "0.0.0.0",
    studio: "Studio | None" = None,
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
            if path.startswith("/api/next") and studio is not None:
                n = 1
                if "?" in self.path:
                    import urllib.parse

                    qs = urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    try:
                        n = int(qs.get("n", ["1"])[0])
                    except ValueError:
                        n = 1
                with lock:
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

    LOG.info("استودیو: http://%s:%d", host, port)
    with Server((host, port), Handler) as httpd:
        httpd.serve_forever()
