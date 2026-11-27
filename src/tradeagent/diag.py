"""خواندن وضعیت متاتریدر از روی دیسک — بدون اینکه کاربر چیزی کپی کند.

سه واقعیت که این ماژول را لازم کرد:

* متاتریدر لاگ‌ها را **UTF-16LE** می‌نویسد؛ خواندن utf-8 هر خط را نامرئی می‌کند؛
* دو لاگ وجود دارد: ``MQL5/Logs`` (تب Experts، جای Print اکسپرت) و ``logs`` (Journal)؛
  Journal همیشه تازه‌تر است ولی خطوط اکسپرت را ندارد؛
* دادهٔ ترمینال ممکن است در نصب پرتابل (کنار exe) یا در
  ``users/<u>/AppData/Roaming/MetaQuotes/Terminal/<hash>`` باشد.
"""

from __future__ import annotations

import os
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
            "no_line": True,
        }
    (_, m), f, ea = best
    return {
        "found": True,
        "file": str(f),
        "age_s": int(time.time() - m),
        "last": ea[-3:],
        # 4014 = ERR_FUNCTION_NOT_ALLOWED («Function is not allowed for call»)، نه
        # «connection refused»: متاتریدر آدرس را در لیست مجازِ سوکت ندارد.
        "not_allowed": any("err=4014" in ln for ln in ea[-8:]),
        "refused": any("err=5272" in ln or "err=5273" in ln for ln in ea[-8:]),
        "connected": any("connected" in ln.lower() for ln in ea[-8:]),
    }


def mt5_pids() -> list[str]:
    pids: list[str] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmd = (entry / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        if "terminal64.exe" in cmd:
            pids.append(entry.name)
    return pids


def report() -> None:
    """بلوک خوانا برای `run_gold.sh --status`."""
    pids = mt5_pids()
    print(f"  terminal64.exe: {('running (pid ' + ', '.join(pids[:2]) + ')') if pids else 'NOT running - متاتریدر باز نیست'}")
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
    if info.get("not_allowed"):
        print("  ! err=4014 یعنی «Function is not allowed for call» - نه بسته بودن سوکت.")
        print("    متاتریدر آدرس سوکت را در لیست مجاز ندارد. مسیر:")
        print("    Tools -> Options -> Expert Advisors -> تیک «Allow WebRequest for listed URL»")
        print("    و اضافه کردن  127.0.0.1  (اگر نپذیرفت: http://127.0.0.1 )")
    for note in journal_notices(2):
        print(f"  اعلان متاتریدر: {note[:140]}")
