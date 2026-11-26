"""بررسی سلامت (doctor): داده، پیکربندی، انتشار و مدل — بدون هیچ تغییر مخرب."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Config
from .llm import LLMAdvisor
from .publish import Publisher
from .ticks import HarvesterSource
from .util import utc_day, utc_iso

OK = "ok"
WARN = "warn"
FAIL = "fail"

SYMBOL_FA = {OK: "OK", WARN: "هشدار", FAIL: "خطا"}


@dataclass
class Check:
    name: str
    status: str
    detail: str

    def render(self) -> str:
        return f"[{SYMBOL_FA.get(self.status, self.status):5}] {self.name}: {self.detail}"


def _find_mt5_files(candidates: list[Path], limit: int = 6) -> list[Path]:
    found: list[Path] = []
    for candidate in candidates:
        if candidate.exists():
            found.append(candidate)
        if len(found) >= limit:
            break
    return found


def mt5_candidate_paths() -> list[Path]:
    home = Path.home()
    guesses: list[Path] = []
    wine_root = home / ".wine" / "drive_c" / "users"
    if wine_root.exists():
        for user_dir in wine_root.glob("*"):
            for term in (user_dir / "AppData" / "Roaming" / "MetaQuotes" / "Terminal").glob("*"):
                guesses.append(term / "MQL5" / "Files")
    for base in (home, Path("/media"), Path("/mnt")):
        if base.exists() and base == home:
            guesses.extend(
                [
                    home / "MetaTrader 5" / "MQL5" / "Files",
                    home / ".mt5" / "drive_c" / "Program Files" / "MetaTrader 5" / "MQL5" / "Files",
                ]
            )
    return guesses


def run_doctor(cfg: Config, network: bool = False) -> list[Check]:
    checks: list[Check] = []
    import sys

    py_ok = sys.version_info >= (3, 11)
    checks.append(Check("نسخه پایتون", OK if py_ok else FAIL, sys.version.split()[0]))

    checks.append(
        Check(
            "پیکربندی",
            OK if cfg.source else WARN,
            str(cfg.source) if cfg.source else "پیکربندی سفارشی پیدا نشد؛ پیش‌فرض پروژه استفاده می‌شود",
        )
    )

    state_dir = cfg.state_dir
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        probe = state_dir / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        checks.append(Check("پوشه state", OK, str(state_dir)))
    except OSError as exc:
        checks.append(Check("پوشه state", FAIL, f"{state_dir}: {exc}"))

    db_path = cfg.path("paper.sqlite3", "var/paper.sqlite3")
    try:
        conn = sqlite3.connect(str(db_path))
        trades = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='trades'").fetchone()
        count = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] if trades and trades[0] else 0
        conn.close()
        checks.append(Check("ژورنال SQLite", OK, f"{db_path} ({count} معامله ثبت‌شده)"))
    except sqlite3.Error as exc:
        checks.append(Check("ژورنال SQLite", FAIL, f"{db_path}: {exc}"))

    source_kind = str(cfg.get("data.source", "harvester")).lower()
    if source_kind == "synthetic":
        checks.append(Check("منبع داده", WARN, "synthetic — داده‌ی ساختگی، نه بازار واقعی"))
    else:
        root = cfg.path("data.harvester_dir", "~/mt5/MQL5/Files/harvester")
        if not root.exists():
            found = _find_mt5_files(mt5_candidate_paths())
            hint = ""
            if found:
                hint = " | مسیرهای احتمالی MT5: " + ", ".join(str(p) for p in found[:3])
            checks.append(
                Check(
                    "منبع داده harvester",
                    FAIL,
                    f"{root} وجود ندارد. اکسپرت TickHarvester را روی چارت بگذار.{hint}",
                )
            )
        else:
            src = HarvesterSource(root)
            symbols = src.available_symbols()
            status = src.status()
            age = None
            if status.get("updated_ts"):
                age = status.get("age_s")
            detail = f"{len(symbols)} نماد"
            if symbols:
                detail += f" ({', '.join(symbols[:8])}{'…' if len(symbols) > 8 else ''})"
            if age is not None:
                try:
                    age_val = float(age)
                    detail += f" · آخرین ضربان {age_val:.0f}s پیش"
                    checks.append(Check("منبع داده harvester", OK if age_val < 120 else WARN, detail))
                except (TypeError, ValueError):
                    checks.append(Check("منبع داده harvester", WARN, detail))
            else:
                checks.append(
                    Check(
                        "منبع داده harvester",
                        WARN if symbols else FAIL,
                        detail + " · status.json تازه پیدا نشد (اکسپرت اجرا نشده؟)",
                    )
                )
            today = utc_day()
            missing = [s for s in symbols if not (root / "ticks" / s / f"{today.replace('-', '')}.csv").exists()]
            if symbols and missing:
                checks.append(
                    Check(
                        "فایل تیک امروز",
                        WARN,
                        f"{len(missing)} نماد فایل امروز ندارند: {', '.join(missing[:6])}",
                    )
                )

    advisor = LLMAdvisor(cfg, state_dir / "llm_calls.json")
    ok, why = advisor.available()
    checks.append(
        Check(
            "مدل زبانی",
            OK if ok else WARN,
            f"{advisor.provider}/{advisor.model if advisor.provider == 'gemini' else advisor.ollama_model} · "
            + ("آماده" if ok else why),
        )
    )

    publisher = Publisher(cfg, cfg.root)
    ok, why = publisher.ensure()
    if not ok:
        checks.append(Check("انتشار گیت", WARN, why))
    else:
        detail = f"{publisher.dir} → branch `{publisher.branch}`"
        checks.append(Check("انتشار گیت", OK, detail))
        if network:
            from .util import CommandError, run_cmd

            try:
                run_cmd(["git", "-C", str(publisher.dir), "ls-remote", "--heads", "origin"], timeout=40)
                checks.append(Check("دسترسی remote", OK, "ls-remote موفق"))
            except CommandError as exc:
                checks.append(Check("دسترسی remote", WARN, f"ls-remote ناموفق: {exc}"))

    return checks


def render_checks(checks: list[Check]) -> str:
    return "\n".join(c.render() for c in checks)


def summary(checks: list[Check]) -> dict[str, Any]:
    counts = {OK: 0, WARN: 0, FAIL: 0}
    for check in checks:
        counts[check.status] = counts.get(check.status, 0) + 1
    return {"ts": utc_iso(), "counts": counts, "checks": [c.__dict__ for c in checks]}
