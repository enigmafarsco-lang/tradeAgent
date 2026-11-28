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


def momentum_rule(candles: Sequence[Candle], i: int) -> dict[str, Any]:
    """قاعدهٔ سادهٔ ۵ دقیقه — هم پیش‌فرض بدون مدل، هم خط مبنا."""
    ref = candles[max(0, i - 5)].close
    move = ((candles[i].close / ref) - 1.0) * 1e4 if ref else 0.0
    direction = "up" if move > 0 else "down" if move < 0 else "flat"
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
        self.cache: dict[str, Any] = self._load_cache()
        self.candles: list[Candle] = []
        self.preds: list[Prediction] = []
        self.failures = 0
        self.last_error = ""
        self._saved = 0
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
            out = momentum_rule(self.candles, i)
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
            out = momentum_rule(self.candles, i)
            out["source"] = "rule"
            return out

    # -- امتیاز ----------------------------------------------------------
    def _score_ready(self, upto_i: int) -> None:
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
        bets = [p for p in scored if p.direction != "flat"]
        net = sum((p.actual_bp if p.direction == "up" else -p.actual_bp) - self.cost_bp for p in bets)
        return {
            "predictions": len(self.preds),
            "scored": len(scored),
            "hits": sum(p.hit or 0 for p in scored),
            "hit_pct": round(100.0 * sum(p.hit or 0 for p in scored) / len(scored), 1) if scored else 0.0,
            "bets": len(bets),
            "net_bp": round(net, 2) if bets else 0.0,
            "avg_bp": round(net / len(bets), 2) if bets else 0.0,
            "failures": self.failures,
            "last_error": self.last_error,
        }

    # -- حرکت ------------------------------------------------------------
    def walk(self, step_override: int | None = None, limit: int | None = None,
             start_i: int | None = None,
             progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
        if not self.candles:
            self.candles = load_candles(self.root, self.symbol, cache_dir=self.out / "cache")
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
        for i in range(start, stop, step):
            pred = self.predict(i)
            self.preds.append(Prediction(
                i=i, t=self.candles[i].t, entry=self.candles[i].close,
                direction=pred["direction"], strength=float(pred.get("strength", 0.5)),
                confidence=float(pred.get("confidence", 0.5)),
                reason=str(pred.get("reason", "")), source=pred.get("source", "?"),
            ))
            self._score_ready(i)
            made += 1
            if made % 25 == 0 or made == total:
                self.save()
                if progress:
                    elapsed = time.time() - self.started
                    rate = made / elapsed if elapsed else 0.0
                    progress({"made": made, "total": total,
                              "eta_s": int((total - made) / rate) if rate else 0,
                              **self.stats()})
            if limit and made >= limit:
                break
        self._score_ready(n - 1)
        self.save()
        return {"ok": True, **self.stats(), "candles": n, "step_min": step}

    # -- ذخیره -----------------------------------------------------------
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
body{margin:0;background:var(--bg);color:var(--fg);font-family:system-ui,Vazirmatn,Tahoma,sans-serif;padding:18px}
h1{font-size:18px;margin:0} .muted{color:var(--muted);font-size:12px}
.cards{display:flex;gap:10px;flex-wrap:wrap;margin:12px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:9px 14px;min-width:120px}
.card .k{font-size:11px;color:var(--muted)} .card .v{font-size:17px;font-weight:700}
canvas{width:100%;background:var(--card);border:1px solid var(--line);border-radius:12px}
table{width:100%;border-collapse:collapse;font-size:12px;background:var(--card);border-radius:12px;overflow:hidden;margin-top:12px}
th,td{padding:7px 9px;border-bottom:1px solid var(--line);text-align:right}
th{background:#16233c;color:var(--muted)}
.up{color:var(--up)} .down{color:var(--down)} .flat{color:var(--muted)}
.pill{padding:1px 7px;border-radius:99px;border:1px solid var(--line);font-size:11px}
</style></head><body>
<h1>استودیو ایجنت — <span id="sym">SYMBOL</span></h1>
<div class="muted" id="upd">در حال بارگذاری…</div>
<div class="cards" id="cards"></div>
<canvas id="chart" height="360"></canvas>
<h2 style="font-size:14px;margin:14px 0 6px">آخرین تصمیم‌ها</h2>
<table><thead><tr><th>زمان</th><th>جهت</th><th>اطمینان</th><th>منبع</th><th>واقعی</th><th>درست</th><th>دلیل</th></tr></thead>
<tbody id="rows"><tr><td colspan="7" class="muted">…</td></tr></tbody></table>
<script>
const sym = "SYMBOL";
let state = null;
function fmt(t){const d=new Date(t*1000);return d.toISOString().slice(5,16).replace('T',' ');}
function draw(){
  const c=document.getElementById('chart');
  if(!state||!state.candles.length) return;
  const dpr=window.devicePixelRatio||1;
  c.width=c.clientWidth*dpr; c.height=360*dpr;
  const ctx=c.getContext('2d'); ctx.scale(dpr,dpr);
  const W=c.clientWidth, H=360, pad=6;
  const cs=state.candles.slice(-240);
  const hi=Math.max(...cs.map(x=>x.h)), lo=Math.min(...cs.map(x=>x.l));
  const y=v=>pad+(hi-v)/(hi-lo||1)*(H-2*pad);
  const bw=Math.max(1.5,(W-2*pad)/cs.length*0.65);
  const step=(W-2*pad)/cs.length;
  ctx.strokeStyle='#1f2b45'; ctx.beginPath(); ctx.moveTo(0,H/2); ctx.lineTo(W,H/2); ctx.stroke();
  cs.forEach((x,i)=>{
    const cx=pad+i*step+step/2;
    ctx.strokeStyle=x.c>=x.o?'#22c55e':'#ef4444';
    ctx.beginPath(); ctx.moveTo(cx,y(x.h)); ctx.lineTo(cx,y(x.l)); ctx.stroke();
    ctx.fillStyle=ctx.strokeStyle;
    const top=y(Math.max(x.o,x.c)), bot=y(Math.min(x.o,x.c));
    ctx.fillRect(cx-bw/2, top, bw, Math.max(1,bot-top));
  });
  const idx={}; cs.forEach((x,i)=>idx[x.t]=i);
  state.preds.forEach(p=>{
    if(!(p.t in idx)) return;
    const cx=pad+idx[p.t]*step+step/2;
    const py=p.direction==='up'?y(state.candles.find(z=>z.t===p.t).h)-10:y(state.candles.find(z=>z.t===p.t).l)+10;
    ctx.fillStyle=p.direction==='up'?'#22c55e':p.direction==='down'?'#ef4444':'#94a3b8';
    ctx.beginPath(); ctx.arc(cx,py,3,0,6.28); ctx.fill();
  });
}
function render(){
  if(!state) return;
  document.getElementById('upd').textContent='آخرین به‌روزرسانی: '+state.updated+' · افق '+state.horizon+' کندل · گام '+state.step_min+' دقیقه';
  const s=state.stats;
  const b=state.baselines['همیشه بالا'];
  const cards=[['پیش‌بینی',s.predictions],['سنجیده',s.scored],['درست',s.hit_pct+'%'],
               ['معامله',s.bets],['خالص',(s.net_bp>=0?'+':'')+s.net_bp+'bp'],
               ['همیشه بالا',b? b.hit_pct+'%':'-'],['خطای مدل',s.failures]];
  document.getElementById('cards').innerHTML=cards.map(([k,v])=>`<div class="card"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('');
  const rows=state.preds.slice(-25).reverse().map(p=>{
    const cls=p.direction; const act=p.actual_bp===null?'—':(p.actual_bp>0?'+':'')+p.actual_bp+'bp';
    const ok=p.hit===null?'—':(p.hit?'✓':'✗');
    return `<tr><td>${fmt(p.t)}</td><td class="${cls}">${p.direction}</td><td>${p.confidence.toFixed(2)}</td>
    <td><span class="pill">${p.source}</span></td><td>${act}</td><td>${ok}</td><td>${p.reason||''}</td></tr>`;
  }).join('');
  document.getElementById('rows').innerHTML=rows||'<tr><td colspan="7" class="muted">هنوز تصمیمی نیست</td></tr>';
  draw();
}
async function tick(){
  try{ const r=await fetch('api/state',{cache:'no-store'}); state=await r.json(); render(); }catch(e){}
}
tick(); setInterval(tick,2000);
window.addEventListener('resize',draw);
</script></body></html>""".replace("SYMBOL", symbol)


def serve(state_dir: Path, symbol: str, port: int = 8620, host: str = "0.0.0.0") -> None:
    """سرور کوچک: صفحهٔ اپ + /api/state (از state.json خوانده می‌شود)."""
    import http.server
    import socketserver

    root = Path(state_dir)
    html_page = app_html(symbol, port).encode("utf-8")

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
            if path in ("/api/state", "/state.json"):
                try:
                    data = (root / "state.json").read_bytes()
                except OSError:
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
