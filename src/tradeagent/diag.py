"""خواندن وضعیت متاتریدر از روی دیسک — بدون اینکه کاربر چیزی کپی کند.

سه واقعیت که این ماژول را لازم کرد:

* متاتریدر لاگ‌ها را **UTF-16LE** می‌نویسد؛ خواندن utf-8 هر خط را نامرئی می‌کند؛
* دو لاگ وجود دارد: ``MQL5/Logs`` (تب Experts، جای Print اکسپرت) و ``logs`` (Journal)؛
  Journal همیشه تازه‌تر است ولی خطوط اکسپرت را ندارد؛
* دادهٔ ترمینال ممکن است در نصب پرتابل (کنار exe) یا در
  ``users/<u>/AppData/Roaming/MetaQuotes/Terminal/<hash>`` باشد.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Iterable

MAX_FILE_BYTES = 8_000_000
MAX_FILES = 400


def prefix() -> Path:
    return Path(os.environ.get("MT5_WINEPREFIX") or (Path.home() / "Wine")) / "drive_c"


def read_log(path: Path) -> list[str]:
    """UTF-16 یا UTF-8؟ از BOM/بایت صفر تشخیص بده."""
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = raw.decode("utf-16", errors="replace")
    elif b"\x00" in raw[:400]:
        text = raw.decode("utf-16-le", errors="replace")
    else:
        text = raw.decode("utf-8", errors="replace")
    return text.splitlines()


def data_dirs_from_process() -> list[Path]:
    """پوشهٔ داده را از فایل‌های بازی که خود ترمینال نگه داشته پیدا کن.

    متاتریدر لاگ روزِ جاری را هنگام کار باز نگه می‌دارد، پس ``/proc/<pid>/fd``
    مسیر واقعی پوشهٔ داده را لو می‌دهد — حتی وقتی جای غیرمنتظره‌ای نصب شده باشد.
    """
    dirs: set[Path] = set()
    for pid in mt5_pids():
        base = Path("/proc") / pid / "fd"
        try:
            entries = list(base.iterdir())
        except OSError:
            continue
        for e in entries:
            try:
                target = Path(os.readlink(e))
            except OSError:
                continue
            if target.suffix != ".log":
                continue
            if "MQL5" in str(target) or "MetaQuotes" in str(target) or "MetaTrader" in str(target):
                dirs.add(target.parent)
    return sorted(dirs)


def candidate_logs(extra_dirs: Iterable[str | Path] = ()) -> list[Path]:
    """فایل‌های لاگ محتمل، تازه‌ترین اول؛ جست‌وجوی بازگشتی هم دارد ولی محدود."""
    root = prefix()
    found: set[Path] = set()
    for pat in (
        "Program Files/MetaTrader 5/MQL5/Logs/*.log",
        "Program Files/MetaTrader 5/logs/*.log",
        "users/*/AppData/Roaming/MetaQuotes/Terminal/*/MQL5/Logs/*.log",
        "users/*/AppData/Roaming/MetaQuotes/Terminal/*/logs/*.log",
    ):
        found.update(root.glob(pat))
    for extra in extra_dirs:
        d = Path(extra)
        if d.is_dir():
            found.update(d.glob("*.log"))
    for d in data_dirs_from_process():  # از خود ترمینال در حال اجرا
        found.update(d.glob("*.log"))
    if not found:  # نصب در مسیر غیرمنتظره: جست‌وجوی محدود
        now = time.time()
        scanned = 0
        for f in sorted(root.rglob("*.log"), key=lambda f: -f.stat().st_mtime if f.exists() else 0):
            if scanned >= MAX_FILES:
                break
            scanned += 1
            try:
                st = f.stat()
            except OSError:
                continue
            if st.st_size > MAX_FILE_BYTES or now - st.st_mtime > 3 * 86400:
                continue
            if "MQL5" in str(f) or "MetaQuotes" in str(f):
                found.add(f)
    return sorted(found, key=lambda f: f.stat().st_mtime, reverse=True)


NOTICE_KEYS = (
    "automated trading is disabled",
    "automated trading is enabled",
    "expert is disabled",
    "not allowed",
    "expert ... removed",
    " removed",
    "deinitialized",
)


def journal_notices(limit: int = 3) -> list[str]:
    """آخرین اعلان‌های خودِ ترمینال (مثلاً «automated trading is disabled»)."""
    files = candidate_logs()
    if not files:
        return []
    # Journal همان فایلی است که خطوط اکسپرت را ندارد ولی پرنوشت است
    for f in files:
        lines = read_log(f)
        hits = [
            ln
            for ln in lines
            if any(k in ln.lower() for k in ("automated trading", "expert is disabled", "not allowed"))
            or ("expert" in ln.lower() and "removed" in ln.lower())
        ]
        if hits:
            return hits[-limit:]
    return []


def _attempt_flags(lines: list[str]) -> dict[str, bool]:
    """فقط آخرین تلاش مهم است، نه هشت خط آخر — وگرنه یک اتصال موفق را «۴۰۱۴» می‌خوانیم."""
    attempts = [ln for ln in lines if "connect" in ln.lower() and "[trade-mcp]" in ln.lower()]
    last = attempts[-1].lower() if attempts else ""
    return {

        "not_allowed": "err=4014" in last,
        "refused": "err=5272" in last or "err=5273" in last,
        "connected": "connected" in last and "failed" not in last,
        "last_attempt": attempts[-1][:160] if attempts else "",
    }


def ea_log(extra_dirs: Iterable[str | Path] = ()) -> dict[str, Any]:
    files = candidate_logs(extra_dirs)
    if not files:
        return {"found": False}
    best: tuple[tuple[int, float], Path, list[str]] | None = None
    for f in files:
        lines = read_log(f)
        ea = [ln for ln in lines if "trade-mcp" in ln.lower() or "TradeMCPConnector" in ln]
        if not ea:
            continue
        # خطوط خودِ اکسپرت («[trade-mcp] …») از «expert … loaded» ژورنال ارزشمندترند
        own = [ln for ln in ea if "[trade-mcp]" in ln.lower()]
        key = (1 if own else 0, f.stat().st_mtime)
        if best is None or key > best[0]:
            best = (key, f, ea)
    if best is None:
        newest = files[0]
        return {
            "found": True,
            "file": str(newest),
            "age_s": int(time.time() - newest.stat().st_mtime),
            "last": [],
            "refused": False,
            "not_allowed": False,
            "connected": False,
            "last_attempt": "",
            "no_line": True,
        }
    (_, m), f, ea = best
    return {
        "found": True,
        "file": str(f),
        "age_s": int(time.time() - m),
        "last": ea[-3:],
        **_attempt_flags(ea),
    }


def mt5_pids(proc_root: Path = Path("/proc")) -> list[str]:
    """پروسه‌های خودِ متاتریدر.

    wine نامِ برنامه را در /proc/<pid>/comm می‌گذارد؛ همان قابل‌اعتماد است. تطبیق با
    cmdline غلط مثبت می‌داد (هر شلی که رشتهٔ terminal64.exe را در متنش داشت).
    """
    pids: list[str] = []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            comm = (entry / "comm").read_text(encoding="utf-8", errors="replace").strip().lower()
        except OSError:
            continue
        if comm.startswith("terminal64"):
            pids.append(entry.name)
            continue
        if comm.startswith(("wine", "bash", "sh", "python")):
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        args = [a.decode("utf-8", "replace").strip('"') for a in raw.split(b"\x00") if a]
        if args and args[-1].rstrip("/").lower().endswith("terminal64.exe"):
            pids.append(entry.name)
    return sorted(pids, key=int)


def _loose_status(text: str) -> dict[str, Any]:
    """status.json وقتی JSON معتبر نیست (مثلاً فیلد تاریخ در جای عدد) باز هم خوانده شود."""
    def num(key: str) -> int | None:
        m = re.search(r'"' + key + r'"\s*:\s*(-?\d+)', text)
        return int(m.group(1)) if m else None

    symbols = []
    for m in re.finditer(r'\{[^{}]*"symbol"\s*:\s*"([^"]+)"[^{}]*\}', text):
        block = m.group(0)
        ticks = re.search(r'"ticks"\s*:\s*(-?\d+)', block)
        age = re.search(r'"age_s"\s*:\s*(-?\d+)', block)
        symbols.append(
            {
                "symbol": m.group(1),
                "ticks": int(ticks.group(1)) if ticks else None,
                "age_s": int(age.group(1)) if age else None,
            }
        )
    return {
        "broken": True,
        "head": text[:160],
        "polls": num("polls"),
        "total_ticks": num("total_ticks"),
        "errors": num("total_errors"),
        "symbols": symbols,
    }


def harvester_status(dir_path: Path) -> dict[str, Any]:
    """status.json خودِ اکسپرت: چند نماد، چند تیک، چند خطا."""
    path = dir_path / "status.json"
    if not path.exists():
        return {}
    try:
        blob = path.read_bytes()
    except OSError:
        return {}
    data = None
    for enc in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            data = json.loads(blob.decode(enc))
            break
        except (UnicodeDecodeError, ValueError):
            continue
    if not isinstance(data, dict):
        return _loose_status(blob.decode("utf-8", "replace"))
    symbols = data.get("symbols") or []
    return {
        "ea": data.get("ea"),
        "version": data.get("version"),
        "updated": data.get("updated_ts"),
        "polls": data.get("polls"),
        "total_ticks": data.get("total_ticks"),
        "errors": data.get("total_errors"),
        "symbols": [
            {"symbol": s.get("symbol"), "ticks": s.get("ticks"), "age_s": s.get("age_s")}
            for s in symbols
        ],
    }


def symbol_ticks(dir_path: Path, symbol: str) -> dict[str, Any]:
    """وضعیت یک نماد مشخص: چند فایل، تازه‌ترین کدام و چند ثانیه پیش."""
    files = [f for f in (dir_path / "ticks" / symbol).glob("*.csv") if f.is_file()]
    if not files:
        return {"symbol": symbol, "files": 0, "newest": None, "age_s": None, "size": 0}
    newest = max(files, key=lambda f: f.stat().st_mtime)
    return {
        "symbol": symbol,
        "files": len(files),
        "newest": newest.name,
        "age_s": int(time.time() - newest.stat().st_mtime),
        "size": newest.stat().st_size,
    }


def harvester(watch: Iterable[str] = ("XAUUSD_i",)) -> dict[str, Any]:
    """آیا اکسپرت هاروستر تیک تازه می‌نویسد؟ (bars=0 معمولاً یعنی نه)

    چیدمان خودِ اکسپرت: harvester/ticks/<SYMBOL>/<YYYYMMDD>.csv
    """
    root = prefix()
    dirs = [d for d in root.glob("**/MQL5/Files/harvester") if d.is_dir()]
    if not dirs:
        return {"found": False}
    d = max(dirs, key=lambda x: x.stat().st_mtime)
    files = [f for f in d.glob("ticks/**/*.csv") if f.is_file()]
    newest = max(files, key=lambda f: f.stat().st_mtime) if files else None
    age = int(time.time() - newest.stat().st_mtime) if newest else None
    return {
        "found": True,
        "dir": str(d),
        "ticks": len(files),
        "newest": newest.name if newest else None,
        "newest_parent": newest.parent.name if newest else None,
        "age_s": age,
        "size": newest.stat().st_size if newest else 0,
        "status": harvester_status(d),
        "watch": [symbol_ticks(d, sym) for sym in watch],
    }


HARVESTER_SYMBOL = re.compile(r"expert\s+(\w*Harvester)\s+\(([A-Za-z0-9_.\-]+),")


def harvester_symbol() -> tuple[str, str] | None:
    """آخرین بار اکسپرت هاروستر روی کدام نماد بالا آمده؟ (نماد اشتباه = تیک طلا نمی‌آید)"""
    root = prefix()
    files = [
        f
        for pat in ("Program Files/MetaTrader 5/logs/*.log", "**/MQL5/Logs/*.log")
        for f in root.glob(pat)
    ]
    best: tuple[float, str, str] | None = None
    for f in files:
        mtime = f.stat().st_mtime
        if best and mtime < best[0]:
            continue
        for line in reversed(read_log(f)):
            m = HARVESTER_SYMBOL.search(line)
            if m:
                best = (mtime, m.group(1), m.group(2))
                break
    if not best:
        return None
    return best[1], best[2]


def journal_tail(lines: int = 3) -> list[str]:
    """آخرین خطوط Journal ترمینال (نه Experts) — برای دیدن دلیل بسته شدن."""
    root = prefix()
    files = [
        f
        for pat in (
            "Program Files/MetaTrader 5/logs/*.log",
            "users/*/AppData/Roaming/MetaQuotes/Terminal/*/logs/*.log",
        )
        for f in root.glob(pat)
    ]
    if not files:
        return []
    newest = max(files, key=lambda f: f.stat().st_mtime)
    body = read_log(newest)
    return [ln for ln in body if ln.strip()][-lines:]


def last_tick_price(root: Path | None = None, symbol: str = "XAUUSD_i") -> dict[str, Any]:
    """آخرین bid/ask از فایل تیک خودمان (منبع حقیقتِ حلقه)."""
    h = harvester(watch=(symbol,))
    if not h.get("found"):
        return {"ok": False, "why": "پوشهٔ هاروستر پیدا نشد"}
    base = Path(str(h["dir"])) / "ticks" / symbol
    files = sorted(base.glob("*.csv"), key=lambda f: f.stat().st_mtime)
    if not files:
        return {"ok": False, "why": f"فایل تیکی برای {symbol} نیست"}
    path = files[-1]
    try:
        with path.open("rb") as fh:
            fh.seek(max(0, path.stat().st_size - 4096))
            tail = fh.read().decode("utf-8", "replace").splitlines()
    except OSError as exc:
        return {"ok": False, "why": str(exc)}
    for line in reversed(tail):
        parts = line.rstrip("\r\n").split(";")
        if len(parts) < 4 or not parts[0].strip().isdigit():
            continue
        try:
            return {
                "ok": True,
                "file": path.name,
                "ts_msc": int(parts[0]),
                "bid": float(parts[2]),
                "ask": float(parts[3]),
            }
        except (TypeError, ValueError):
            continue
    return {"ok": False, "why": "خط تیکِ خوانا در انتهای فایل نبود"}


def mt5_price(symbol: str = "XAUUSD_i", cfg: Any = None) -> dict[str, Any]:
    """قیمت همان نماد از خود متاتریدر (از راه پل MCP)."""
    try:
        from .config import Config
        from .mcp_client import mcp_from_config

        if cfg is None:
            cfg = Config.load("configs/gold.toml") if Path("configs/gold.toml").exists() else Config.load()
        mcp = mcp_from_config(cfg)
        if not mcp.enabled or not mcp.start():
            return {"ok": False, "why": "پل MCP بالا نیامد"}
        try:
            for tool, args in (
                ("market_snapshot", {"symbol": symbol}),
                ("mt5_status", None),
            ):
                try:
                    res = mcp.call_tool(tool, args) if args else mcp.call_tool(tool)
                except Exception:  # noqa: BLE001
                    continue
                price = _dig_price(res)
                if price:
                    return {"ok": True, "tool": tool, "price": price}
        finally:
            mcp.close()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "why": f"{type(exc).__name__}: {exc}"}
    return {"ok": False, "why": "هیچ ابزاری قیمت نداد"}


def _dig_price(node: Any, depth: int = 0) -> float:
    """اولین عدد معقولِ قیمتی در پاسخ ابزار (bid/ask/last/price)."""
    if depth > 4:
        return 0.0
    if isinstance(node, dict):
        for key in ("bid", "ask", "last", "price", "close", "mark"):
            value = node.get(key)
            if isinstance(value, (int, float)) and float(value) > 100:
                return float(value)
        for value in node.values():
            found = _dig_price(value, depth + 1)
            if found:
                return found
    elif isinstance(node, list):
        for value in reversed(node[-3:]):
            found = _dig_price(value, depth + 1)
            if found:
                return found
    return 0.0


def mt5_wine_log(lines: int = 4) -> list[str]:
    """خروجی wine که start_mt5.sh ذخیره کرده (کرش/خطا)."""
    path = Path("var/logs/mt5.log")
    if not path.exists():
        return []
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [ln for ln in raw if ln.strip()][-lines:]


def bridge_pids() -> list[tuple[str, str]]:
    """پروسه‌های trade-mcp (پل) که همین حالا زنده‌اند: (pid, cmdline)."""
    out: list[tuple[str, str]] = []
    me = str(os.getpid())
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or entry.name == me:
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        cmd = " ".join(a.decode("utf-8", "replace") for a in raw.split(b"\x00") if a).strip()
        if not cmd:
            continue
        if "trade-mcp" in cmd and "serve" in cmd:
            out.append((entry.name, cmd[:140]))
    return out


def port_free(port: int = 8787, host: str = "127.0.0.1") -> bool:
    """آیا می‌شود روی این پورت گوش داد؟ (یعنی پل قبلی رفته)"""
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def kill_stale_bridges(port: int = 8787, wait_s: float = 8.0) -> dict[str, Any]:
    """پلِ جاماندهٔ سرویسِ خوابیده را ببند.

    اگر نسخهٔ قبلی زنده بماند، اکسپرت به همان وصل می‌شود و پرسش‌های ما به سرورِ تازه
    می‌رسد — نتیجه: «MCP server did not answer in time» در حالی که لاگ می‌گوید connected.
    """
    victims = bridge_pids()
    for pid, _ in victims:
        try:
            os.kill(int(pid), 15)
        except (OSError, ValueError):
            continue
    started = time.time()
    while time.time() - started < wait_s:
        if port_free(port):
            return {"killed": [p for p, _ in victims], "port_free": True,
                    "waited_s": round(time.time() - started, 1)}
        time.sleep(0.4)
    return {"killed": [p for p, _ in victims], "port_free": port_free(port),
            "waited_s": round(time.time() - started, 1)}


def bridge_state(path: Path | None = None) -> dict[str, Any]:
    """وضعیت پل همان‌طور که سرویس روی دیسک نوشته است."""
    for candidate in (path, Path("var/mcp_status.json"), Path("var") / "state" / "mcp_status.json"):
        if candidate and Path(candidate).exists():
            try:
                data = json.loads(Path(candidate).read_text(encoding="utf-8", errors="replace"))
            except (OSError, ValueError):
                return {"error": "mcp_status.json خوانده نشد"}
            bridge = data.get("bridge") if isinstance(data, dict) else {}
            if not isinstance(bridge, dict):
                bridge = {}
            return {
                "listen": bridge.get("listen") or data.get("listen") if isinstance(data, dict) else None,
                "socket": bridge.get("socket"),
                "ea_connected": bridge.get("ea_connected"),
                "peer": bridge.get("peer"),
                "age_s": data.get("age_s") if isinstance(data, dict) else None,
            }
    return {"error": "mcp_status.json نیست (سرویس بالا نیست)"}


def report() -> None:
    """بلوک خوانا برای `run_gold.sh --status`."""
    b = bridge_state()
    if "error" in b:
        print(f"  پل: {b['error']}")
    else:
        print(
            f"  bridge={b.get('listen')} socket={b.get('socket')} "
            f"ea_connected={b.get('ea_connected')} peer={b.get('peer')}"
        )
    pids = mt5_pids()
    print(f"  terminal64.exe: {('running (pid ' + ', '.join(pids[:2]) + ')') if pids else 'NOT running - متاتریدر باز نیست'}")
    if not pids:
        print("    برای باز کردنِ مستقل از ترمینال: ./scripts/start_mt5.sh")
        for ln in journal_tail(2):
            print(f"    Journal آخر: {ln[:140]}")
        for ln in mt5_wine_log(3):
            print(f"    wine: {ln[:140]}")
    info = ea_log()
    if not info.get("found"):
        print(f"  Experts log: هیچ *.log زیر {prefix()} پیدا نشد")
        return
    f = Path(str(info["file"]))
    try:
        rel = f.relative_to(prefix())
    except ValueError:
        rel = f
    print(f"  Experts log: {rel} (آخرین نوشتن {info.get('age_s')}s پیش)")
    for line in (info.get("last") or [])[-3:]:
        print(f"    {line[:150]}")
    if info.get("no_line"):
        print("    (هیچ خط trade-mcp در هیچ فایل لاگی نبود: اکسپرت روی چارت اجرا نشده)")
    if info.get("connected"):
        print("  ✓ اکسپرت وصل است (آخرین خط: connected)")
    elif info.get("not_allowed"):
        print("  ! err=4014 یعنی «Function is not allowed for call» - نه بسته بودن سوکت.")
        print("    متاتریدر آدرس سوکت را در لیست مجاز ندارد. مسیر:")
        print("    Tools -> Options -> Expert Advisors -> تیک «Allow WebRequest for listed URL»")
        print("    و اضافه کردن  127.0.0.1  (اگر نپذیرفت: http://127.0.0.1 )")
    for note in journal_notices(2):
        print(f"  اعلان متاتریدر: {note[:140]}")
    h = harvester()
    if h.get("found"):
        print(
            f"  هاروستر: csv={h.get('ticks')} · تازه‌ترین "
            f"{h.get('newest_parent') or '-'}/{h.get('newest') or '-'} · {h.get('age_s')}s پیش"
        )
        for w in h.get("watch") or []:
            if w["files"] == 0:
                print(f"    {w['symbol']}: هیچ فایلی نیست — اکسپرت این نماد را انتخاب نکرده")
            else:
                print(
                    f"    {w['symbol']}: {w['files']} فایل · {w['newest']} · "
                    f"{w['age_s']}s پیش · {w['size']} بایت"
                )
        st = h.get("status") or {}
        if st.get("broken"):
            print("    status.json باگ اکسپرت است (uptime_s تاریخ نوشته می‌شود)؛ عددها از متن خوانده شد")
        if st:
            syms = ", ".join(
                f"{x['symbol']}({x['ticks']}t,{x['age_s']}s)" for x in st.get("symbols") or []
            )
            print(
                f"    status.json: polls={st.get('polls')} total_ticks={st.get('total_ticks')}"
                f" errors={st.get('errors')} · نمادها: {syms or '— هیچ نمادی انتخاب نشده —'}"
            )
        sym = harvester_symbol()
        if sym and not sym[1].startswith("XAUUSD"):
            print(f"  ! اکسپرت {sym[0]} روی چارت «{sym[1]}» است، نه XAUUSD_i → تیک طلا نمی‌آید")
        if h.get("ticks") == 0:
            print("  ! هیچ فایل تیکی نیست: اکسپرت TickHarvester روی چارت XAUUSD_i نیست")
        elif isinstance(h.get("age_s"), int) and h["age_s"] > 600:
            print("  ! تیک تازه نمی‌رسد (age > 10min): چرخه‌ها bars=0 می‌مانند")
    else:
        print("  هاروستر: پوشه‌ای پیدا نشد")
