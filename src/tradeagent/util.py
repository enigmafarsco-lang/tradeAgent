"""ابزارهای کوچک مشترک: لاگ، زمان، نوشتن اتمی، قفل، اجرای دستور."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

LOG = logging.getLogger("tradeagent")


def setup_logging(level: str = "INFO", logfile: Path | None = None) -> None:
    """لاگ به stderr (و اختیاری به فایل). هرگز به stdout ننویس."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    for h in list(root.handlers):
        root.removeHandler(h)
    fmt = logging.Formatter("%(asctime)sZ %(levelname)-7s %(name)s | %(message)s", "%Y-%m-%dT%H:%M:%S")
    fmt.converter = time.gmtime  # type: ignore[assignment]
    stderr = logging.StreamHandler(sys.stderr)
    stderr.setFormatter(fmt)
    root.addHandler(stderr)
    if logfile is not None:
        logfile.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(logfile, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)


def now_ms() -> int:
    return int(time.time() * 1000)


def utc_stamp(ts: float | int | None = None, fmt: str = "%Y%m%dT%H%M%SZ") -> str:
    return time.strftime(fmt, time.gmtime(time.time() if ts is None else ts))


def utc_iso(ts: float | int | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if ts is None else ts))


def utc_day(ts: float | int | None = None) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() if ts is None else ts))


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def atomic_write(path: Path, text: str) -> None:
    """نوشتن اتمی تا گزارش نیمه‌کاره هرگز روی دیسک نماند."""
    ensure_dir(path.parent)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_json(path: Path, obj: Any) -> None:
    atomic_write(path, json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False) + "\n")


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def round_sig(value: float, digits: int = 4) -> float:
    if value == 0 or value != value:
        return 0.0
    return float(f"{value:.{digits}g}")


class LockFile:
    """قفل ساده بر اساس فایل، تا دو چرخه هم‌زمان روی هم نیفتند."""

    def __init__(self, path: Path, stale_after_s: float = 1800.0) -> None:
        self.path = path
        self.stale_after_s = stale_after_s

    def acquire(self) -> bool:
        ensure_dir(self.path.parent)
        if self.path.exists():
            age = time.time() - self.path.stat().st_mtime
            if age < self.stale_after_s:
                return False
            LOG.warning("قفل کهنه (%.0f ثانیه) پاک شد: %s", age, self.path)
        try:
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(f"{os.getpid()} {utc_iso()}\n")
        return True

    def release(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, *exc: object) -> None:
        self.release()


class CommandError(RuntimeError):
    def __init__(self, cmd: Sequence[str], code: int, out: str, err: str) -> None:
        super().__init__(f"command failed ({code}): {' '.join(cmd)}: {err.strip() or out.strip()}")
        self.cmd = list(cmd)
        self.code = code
        self.out = out
        self.err = err


def run_cmd(
    cmd: Iterable[str],
    cwd: Path | None = None,
    timeout: float = 120.0,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> tuple[int, str, str]:
    """اجرای دستور بدون تعامل؛ خروجی (code, stdout, stderr)."""
    argv = [str(c) for c in cmd]
    full_env = os.environ.copy()
    full_env.setdefault("GIT_TERMINAL_PROMPT", "0")
    full_env.setdefault("GIT_ASKPASS", "true")
    if env:
        full_env.update(env)
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=full_env,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        raise CommandError(argv, 127, "", str(exc)) from exc
    except subprocess.TimeoutExpired as exc:
        raise CommandError(argv, 124, exc.stdout or "", f"timeout after {timeout}s") from exc
    if check and proc.returncode != 0:
        raise CommandError(argv, proc.returncode, proc.stdout, proc.stderr)
    return proc.returncode, proc.stdout, proc.stderr

