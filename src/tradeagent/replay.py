"""بازپخش تیک‌های خودمان با مدل: هر ۵ دقیقه یک پیش‌بینی، بعد سنجش.

مسیر کار:
    ticks/<SYMBOL>/<YYYYMMDD>.csv  (خروجی TickHarvester)
        → کندل ثانیه‌ای (استریم، بدون بارگذاری کل فایل)
        → هر ``step`` ثانیه: عددهای خام به مدل، مدل جهت/قدرت/افق می‌دهد
        → بعد از ``horizon`` ثانیه: حرکت واقعی سنجیده و امتیاز داده می‌شود
        → var/backtest/predictions.jsonl  +  var/backtest/index.html (نمودار)

هیچ‌جا پایتون جهت را حدس نمی‌زند؛ فقط عدد می‌دهد، پیش‌بینی مدل را ثبت و بعد امتیاز می‌کند.
"""

from __future__ import annotations

import html
import json
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Sequence

from .agent import normalize_momentum, parse_agent_reply
from .nowcast import tick_digest
from .ticks import Bar, aggregate_ticks, parse_tick_line
from .util import LOG, ensure_dir, utc_iso

PREDICT_PROMPT = """تو تحلیلگر تیک‌های ثانیه‌ای طلا هستی. فقط با همین عددها تصمیم بگیر.
داده (JSON): {digest}

پیش‌بینی کن که قیمت در {horizon} ثانیهٔ بعد کجا می‌رود. فقط یک آبجکت JSON بده:
{{"analysis": "یک جملهٔ فارسی",
  "momentum": {{"direction": "up|down|flat", "phase": "accelerating|steady|fading|reversing|unclear",
                "strength": 0.0-1.0, "horizon_s": {horizon}, "confidence": 0.0-1.0,
                "expected_bp": عدد (چند بیسی‌پوینت حرکت انتظاری), "reason": "یک جملهٔ فارسی"}}}}
هیچ عددی از خودت نساز؛ فقط از داده."""


# ---------------------------------------------------------------------------
# خواندن تیک‌ها
# ---------------------------------------------------------------------------
def tick_files(root: Path, symbol: str) -> list[Path]:
    base = Path(root).expanduser() / "ticks" / symbol
    return sorted(base.glob("*.csv"))


def iter_second_bars(paths: Sequence[Path]) -> Iterator[Bar]:
    """فایل‌های روزانه را به کندل ثانیه‌ای تبدیل می‌کند، بدون خواندن کل فایل در حافظه."""
    bucket: list = []
    current = -1
    for path in paths:
        try:
            fh = path.open("r", encoding="utf-8", errors="replace")
        except OSError as exc:
            LOG.warning("فایل تیک خوانده نشد %s: %s", path, exc)
            continue
        with fh:
            for line in fh:
                tick = parse_tick_line(line)
                if tick is None:
                    continue
                sec = tick.time_msc // 1000
                if sec != current and bucket:
                    bars = aggregate_ticks(bucket)
                    if bars:
                        yield bars[0]
                    bucket = []
                current = sec
                bucket.append(tick)
    if bucket:
        bars = aggregate_ticks(bucket)
        if bars:
            yield bars[0]


def summarize(root: Path, symbol: str) -> dict[str, Any]:
    """خلاصه‌ای که با یک نگاه بگوید چه داده‌ای داریم."""
    files = tick_files(root, symbol)
    total = 0
    first_ts = last_ts = 0
    low = high = 0.0
    days: list[dict[str, Any]] = []
    for path in files:
        n = 0
        lo = hi = 0.0
        f_last = 0
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    tick = parse_tick_line(line)
                    if tick is None:
                        continue
                    n += 1
                    price = tick.bid or tick.ask
                    lo = price if not lo else min(lo, price)
                    hi = price if not hi else max(hi, price)
                    f_last = tick.time_msc
                    if not first_ts:
                        first_ts = tick.time_msc
        except OSError:
            continue
        total += n
        low = lo if not low else min(low, lo)
        high = hi if not high else max(high, hi)
        last_ts = max(last_ts, f_last)
        days.append({"file": path.name, "ticks": n, "low": round(lo, 2), "high": round(hi, 2)})
    return {
        "symbol": symbol,
        "files": len(files),
        "ticks": total,
        "low": round(low, 2),
        "high": round(high, 2),
        "from": _iso(first_ts // 1000) if first_ts else "",
        "to": _iso(last_ts // 1000) if last_ts else "",
        "days": days,
    }


def _iso(ts: int) -> str:
    import datetime as _dt

    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


# ---------------------------------------------------------------------------
# پیش‌بینی با مدل
# ---------------------------------------------------------------------------
def model_predict(chat: Callable[[list[dict[str, str]]], str], bars: Sequence[Bar], horizon_s: int) -> dict[str, Any]:
    digest = tick_digest(bars)
    if not digest:
        return {}
    prompt = PREDICT_PROMPT.format(digest=json.dumps(digest, ensure_ascii=False), horizon=horizon_s)
    reply = chat([{"role": "system", "content": "فقط JSON. متن‌ها فارسی."}, {"role": "user", "content": prompt}])
    data = parse_agent_reply(reply or "")
    momentum = normalize_momentum(data.get("momentum"))
    if not momentum:
        return {}
    momentum["analysis"] = str(data.get("analysis") or "")[:300]
    try:
        momentum["expected_bp"] = float(momentum.get("expected_bp") or 0.0)
    except (TypeError, ValueError):
        momentum["expected_bp"] = 0.0
    return momentum


# ---------------------------------------------------------------------------
# بازپخش
# ---------------------------------------------------------------------------
@dataclass
class Step:
    ts: int
    price: float
    pred: dict[str, Any]
    due_ts: int
    actual_bp: float | None = None
    hit: int | None = None


class Replay:
    def __init__(
        self,
        root: Path,
        symbol: str,
        predictor: Callable[[Sequence[Bar], int], dict[str, Any]],
        step_s: int = 300,
        horizon_s: int = 300,
        warmup_s: int = 900,
        cost_bp: float = 1.0,
        out_dir: Path | None = None,
    ) -> None:
        self.root = Path(root)
        self.symbol = symbol
        self.predictor = predictor
        self.step_s = max(30, int(step_s))
        self.horizon_s = max(30, int(horizon_s))
        self.warmup_s = max(60, int(warmup_s))
        self.cost_bp = float(cost_bp)
        self.out = ensure_dir(out_dir or Path("var/backtest"))

    # -- اجرا -----------------------------------------------------------
    def run(self, limit_steps: int = 0) -> dict[str, Any]:
        files = tick_files(self.root, self.symbol)
        if not files:
            return {"ok": False, "error": f"هیچ فایل تیکی برای {self.symbol} در {self.root} نیست"}
        window = max(self.warmup_s, 1800)
        buf: deque[Bar] = deque(maxlen=window)
        steps: list[Step] = []
        next_step = 0
        started = time.time()
        for bar in iter_second_bars(files):
            buf.append(bar)
            if len(buf) < 60:
                continue
            if next_step == 0:
                next_step = bar.t + self.warmup_s
            if bar.t >= next_step:
                pred = self.predictor(list(buf), self.horizon_s)
                if pred:
                    steps.append(Step(ts=bar.t, price=bar.close, pred=pred, due_ts=bar.t + self.horizon_s))
                next_step = bar.t + self.step_s
                if limit_steps and len(steps) >= limit_steps:
                    break
            # سنجشِ پیش‌بینی‌های سررسیده
            for step in steps:
                if step.actual_bp is None and bar.t >= step.due_ts:
                    step.actual_bp = (bar.close / step.price - 1.0) * 1e4 if step.price else 0.0
                    step.hit = _score(step.pred.get("direction"), step.actual_bp)
        stats = summarize_steps(steps, self.cost_bp, self.horizon_s)
        stats.update(
            {
                "ok": True,
                "symbol": self.symbol,
                "step_s": self.step_s,
                "horizon_s": self.horizon_s,
                "elapsed_s": round(time.time() - started, 1),
                "files": [p.name for p in files],
            }
        )
        self.write(steps, stats)
        return stats

    # -- خروجی ----------------------------------------------------------
    def write(self, steps: Sequence[Step], stats: dict[str, Any]) -> None:
        rows = [
            {
                "ts": s.ts,
                "time": _iso(s.ts),
                "price": round(s.price, 3),
                "direction": s.pred.get("direction"),
                "phase": s.pred.get("phase"),
                "strength": s.pred.get("strength"),
                "confidence": s.pred.get("confidence"),
                "expected_bp": s.pred.get("expected_bp"),
                "analysis": s.pred.get("analysis") or s.pred.get("reason") or "",
                "due": _iso(s.due_ts),
                "actual_bp": None if s.actual_bp is None else round(s.actual_bp, 2),
                "hit": s.hit,
            }
            for s in steps
        ]
        (self.out / "predictions.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + ("\n" if rows else ""),
            encoding="utf-8",
        )
        (self.out / "index.html").write_text(render_html(rows, stats), encoding="utf-8")
        (self.out / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")


def _score(direction: Any, actual_bp: float, flat_bp: float = 3.0) -> int:
    """۱ اگر جهت درست بود؛ flat وقتی حرکت کمتر از آستانه بود هم درست حساب می‌شود."""
    if direction == "flat":
        return 1 if abs(actual_bp) <= flat_bp else 0
    if direction == "up":
        return 1 if actual_bp > 0 else 0
    if direction == "down":
        return 1 if actual_bp < 0 else 0
    return 0


def summarize_steps(steps: Sequence[Step], cost_bp: float, horizon_s: int) -> dict[str, Any]:
    scored = [s for s in steps if s.hit is not None]
    hits = sum(1 for s in scored if s.hit)
    signed = []
    for s in scored:
        d = s.pred.get("direction")
        actual = s.actual_bp or 0.0
        if d == "up":
            signed.append(actual - cost_bp)
        elif d == "down":
            signed.append(-actual - cost_bp)
        else:
            signed.append(0.0)
    by_dir: dict[str, dict[str, Any]] = {}
    for s in scored:
        d = str(s.pred.get("direction"))
        bucket = by_dir.setdefault(d, {"n": 0, "hit": 0, "bp": 0.0})
        bucket["n"] += 1
        bucket["hit"] += int(s.hit or 0)
        bucket["bp"] += float(s.actual_bp or 0.0) * (1 if d == "up" else -1 if d == "down" else 0)
    for bucket in by_dir.values():
        bucket["hit_pct"] = round(100.0 * bucket["hit"] / max(1, bucket["n"]), 1)
        bucket["avg_bp"] = round(bucket["bp"] / max(1, bucket["n"]), 2)
    return {
        "steps": len(steps),
        "scored": len(scored),
        "hit_pct": round(100.0 * hits / max(1, len(scored)), 1) if scored else 0.0,
        "avg_net_bp": round(sum(signed) / max(1, len(signed)), 2) if signed else 0.0,
        "sum_net_bp": round(sum(signed), 1) if signed else 0.0,
        "cost_bp": cost_bp,
        "horizon_s": horizon_s,
        "by_direction": by_dir,
    }


# ---------------------------------------------------------------------------
# گزارش گرافیکی (HTML مستقل، نمودار SVG داخلش)
# ---------------------------------------------------------------------------
def _svg(rows: Sequence[dict[str, Any]], width: int = 1000, height: int = 320) -> str:
    if not rows:
        return "<p>داده‌ای برای نمودار نیست</p>"
    prices = [r["price"] for r in rows]
    lo, hi = min(prices), max(prices)
    span = (hi - lo) or 1.0
    pad = 30
    n = len(rows)
    def x(i: int) -> float:
        return pad + (width - 2 * pad) * (i / max(1, n - 1))
    def y(p: float) -> float:
        return height - pad - (height - 2 * pad) * ((p - lo) / span)

    path = " ".join(
        ("M" if i == 0 else "L") + f"{x(i):.1f},{y(r['price']):.1f}" for i, r in enumerate(rows)
    )
    marks = []
    for i, r in enumerate(rows):
        if r.get("hit") is None:
            continue
        color = "#1a9850" if r["hit"] else "#d73027"
        d = r.get("direction")
        cy = y(r["price"])
        cx = x(i)
        if d == "up":
            marks.append(f'<polygon points="{cx:.1f},{cy - 8:.1f} {cx - 5:.1f},{cy + 4:.1f} {cx + 5:.1f},{cy + 4:.1f}" fill="{color}" opacity="0.9"/>')
        elif d == "down":
            marks.append(f'<polygon points="{cx:.1f},{cy + 8:.1f} {cx - 5:.1f},{cy - 4:.1f} {cx + 5:.1f},{cy - 4:.1f}" fill="{color}" opacity="0.9"/>')
        else:
            marks.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="3.5" fill="{color}" opacity="0.8"/>')
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
        f'style="background:#0f1420;border-radius:10px">'
        f'<path d="{path}" fill="none" stroke="#4da3ff" stroke-width="1.6"/>'
        + "".join(marks)
        + f'<text x="{pad}" y="18" fill="#8b98a8" font-size="12">قیمت (خط آبی) · پیش‌بینی‌ها: ▲ خرید، ▼ فروش، ● خنثی — سبز=درست، قرمز=غلط</text>'
        + f'<text x="{pad}" y="{height - 8}" fill="#8b98a8" font-size="11">{html.escape(rows[0]["time"])} → {html.escape(rows[-1]["time"])}</text>'
        + "</svg>"
    )


def render_html(rows: Sequence[dict[str, Any]], stats: dict[str, Any]) -> str:
    by_dir = stats.get("by_direction") or {}
    dir_rows = "".join(
        f"<tr><td>{html.escape(str(k))}</td><td>{v['n']}</td>"
        f"<td>{v['hit_pct']}%</td><td>{v['avg_bp']:+.2f}</td></tr>"
        for k, v in sorted(by_dir.items())
    )
    def actual_cell(row: dict[str, Any]) -> str:
        value = row.get("actual_bp")
        return "—" if value is None else f"{value:+.2f}"

    tail = "".join(
        f"<tr><td>{html.escape(str(r['time']))}</td><td>{r['direction']}</td>"
        f"<td>{r.get('confidence')}</td><td>{r.get('expected_bp')}</td>"
        f"<td>{actual_cell(r)}</td>"
        f"<td>{'—' if r['hit'] is None else ('✅' if r['hit'] else '❌')}</td>"
        f"<td class='an'>{html.escape(str(r.get('analysis') or '')[:140])}</td></tr>"
        for r in rows[-40:][::-1]
    )
    return f"""<!doctype html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8">
<title>بازپخش تیک طلا — پیش‌بینی مدل</title>
<style>
 body{{font-family:system-ui,Tahoma,sans-serif;background:#0b0f17;color:#e6edf3;margin:0;padding:24px}}
 h1{{font-size:20px;margin:0 0 6px}} .muted{{color:#8b98a8;font-size:13px}}
 .cards{{display:flex;gap:12px;flex-wrap:wrap;margin:18px 0}}
 .card{{background:#131a26;border:1px solid #22303f;border-radius:12px;padding:12px 16px;min-width:150px}}
 .card b{{display:block;font-size:22px;margin-top:4px}}
 table{{width:100%;border-collapse:collapse;margin-top:14px;font-size:13px}}
 th,td{{padding:6px 8px;border-bottom:1px solid #1d2937;text-align:right}}
 th{{color:#8b98a8;font-weight:600}} .an{{color:#c9d4e0;max-width:420px}}
</style></head><body>
<h1>بازپخش تیک طلا — {html.escape(str(stats.get('symbol')))}</h1>
<div class="muted">هر {stats.get('step_s')} ثانیه یک پیش‌بینی، افق {stats.get('horizon_s')} ثانیه · هزینهٔ فرضی هر معامله {stats.get('cost_bp')}bp · فایل‌ها: {html.escape(', '.join(stats.get('files') or []))}</div>
<div class="cards">
 <div class="card">گام‌های پیش‌بینی‌شده<b>{stats.get('steps')}</b></div>
 <div class="card">سنجیده‌شده<b>{stats.get('scored')}</b></div>
 <div class="card">درست<b>{stats.get('hit_pct')}%</b></div>
 <div class="card">میانگین خالص<b>{stats.get('avg_net_bp')}bp</b></div>
 <div class="card">جمع خالص<b>{stats.get('sum_net_bp')}bp</b></div>
</div>
{_svg(rows)}
<table><thead><tr><th>جهت</th><th>تعداد</th><th>درست</th><th>میانگین bp</th></tr></thead>
<tbody>{dir_rows}</tbody></table>
<h2 style="font-size:16px;margin-top:22px">۴۰ پیش‌بینی آخر</h2>
<table><thead><tr><th>زمان</th><th>جهت</th><th>اطمینان</th><th>انتظار bp</th><th>واقعی bp</th><th>درست؟</th><th>تحلیل مدل</th></tr></thead>
<tbody>{tail}</tbody></table>
</body></html>
"""
