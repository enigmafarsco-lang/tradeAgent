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


def harvester_status(dir_path: Path) -> dict[str, Any]:
    """status.json خودِ اکسپرت: چند نماد، چند تیک، چند خطا."""
    path = dir_path / "status.json"
    if not path.exists():
        return {}
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
        data = json.loads(raw)
    except (OSError, ValueError):
        return {"broken": True}
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


def harvester() -> dict[str, Any]:
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


def report() -> None:
    """بلوک خوانا برای `run_gold.sh --status`."""
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
        st = h.get("status") or {}
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
