"""انتشار گزارش‌ها در گیت، بدون دست‌زدن به ریپوی کد.

یک کلون جدا در `var/publish` نگه داشته می‌شود؛ هر چرخه فقط فایل‌های گزارش داخل
شاخه‌ی مخصوص (پیش‌فرض `reports`) کامیت و پوش می‌شوند. اگر شبکه/احراز هویت نبود،
کامیت محلی می‌ماند و هش گزارش می‌شود (هیچ چیز گم نمی‌شود).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import Config
from .util import (LOG, CommandError, atomic_write, ensure_dir, run_cmd, utc_iso)


class Publisher:
    def __init__(self, cfg: Config, code_root: Path | None = None) -> None:
        self.cfg = cfg
        self.code_root = Path(code_root or cfg.root)
        self.enabled = bool(cfg.get("publish.enabled", True))
        self.branch = str(cfg.get("publish.branch", "reports"))
        self.dir = cfg.path("publish.dir", "var/publish")
        self.mode = str(cfg.get("publish.mode", "every_cycle"))
        self.paths = list(cfg.get("publish.paths", ["reports"]) or ["reports"])
        self.max_commits_per_hour = int(cfg.get("publish.max_commits_per_hour", 20))
        self.push_timeout = float(cfg.get("publish.push_timeout_s", 120))
        self.author_name = str(cfg.get("publish.author_name", "tradeagent-bot"))
        self.author_email = str(cfg.get("publish.author_email", "tradeagent@localhost"))
        self._last_error: str | None = None

    # -- کمکی ----------------------------------------------------------
    def _remote_url(self) -> str:
        explicit = str(self.cfg.get("publish.remote", "") or "").strip()
        if explicit:
            return explicit
        try:
            _, out, _ = run_cmd(["git", "-C", str(self.code_root), "remote", "get-url", "origin"], timeout=20)
            return out.strip()
        except CommandError as exc:
            self._last_error = f"origin پیدا نشد: {exc}"
            return ""

    def _git(self, *args: str, timeout: float = 60.0, check: bool = True) -> tuple[int, str, str]:
        return run_cmd(["git", "-C", str(self.dir), *args], timeout=timeout, check=check)

    def _configure(self) -> None:
        for key, value in (("user.name", self.author_name), ("user.email", self.author_email)):
            run_cmd(["git", "-C", str(self.dir), "config", key, value], timeout=20, check=False)
        run_cmd(["git", "-C", str(self.dir), "config", "commit.gpgsign", "false"], timeout=20, check=False)

    # -- آماده‌سازی ----------------------------------------------------
    def ensure(self) -> tuple[bool, str]:
        """کلون محلی گزارش را می‌سازد یا سالم‌بودنش را تأیید می‌کند."""
        if not self.enabled:
            return False, "انتشار غیرفعال است (publish.enabled=false)"
        remote = self._remote_url()
        if not remote:
            return False, self._last_error or "آدرس remote تنظیم نشده"
        ensure_dir(self.dir)
        if (self.dir / ".git").exists():
            self._configure()
            run_cmd(["git", "-C", str(self.dir), "remote", "set-url", "origin", remote], timeout=20, check=False)
            code, out, _ = self._git("rev-parse", "--abbrev-ref", "HEAD", check=False)
            current = out.strip()
            if code != 0:
                return self._bootstrap_branch(remote)
            if current != self.branch:
                # هرگز شاخه‌ی کاربر را جابه‌جا نکن؛ فقط اگر خودمان ساخته‌ایم
                code, _, _ = self._git("rev-parse", "--verify", f"refs/heads/{self.branch}", check=False)
                if code == 0:
                    self._git("checkout", self.branch, check=False)
                else:
                    return self._bootstrap_branch(remote, force_orphan=True)
            return True, f"آماده است ({self.dir}, branch={self.branch})"

        # کلون تازه
        try:
            run_cmd(
                ["git", "clone", "--depth", "1", "--branch", self.branch, remote, str(self.dir)],
                timeout=self.push_timeout,
            )
            self._configure()
            return True, f"کلون شد ({self.branch})"
        except CommandError:
            LOG.info("شاخه‌ی %s روی remote نیست؛ شاخه‌ی تازه ساخته می‌شود", self.branch)
            return self._bootstrap_branch(remote)

    def _bootstrap_branch(self, remote: str, force_orphan: bool = False) -> tuple[bool, str]:
        ensure_dir(self.dir)
        if not (self.dir / ".git").exists():
            run_cmd(["git", "-C", str(self.dir), "init", "-q"], timeout=30)
        run_cmd(["git", "-C", str(self.dir), "remote", "remove", "origin"], timeout=20, check=False)
        run_cmd(["git", "-C", str(self.dir), "remote", "add", "origin", remote], timeout=20)
        self._configure()
        code, _, _ = self._git("rev-parse", "--verify", f"refs/heads/{self.branch}", check=False)
        if code == 0 and not force_orphan:
            self._git("checkout", self.branch, check=False)
            return True, f"شاخه‌ی محلی {self.branch} استفاده شد"
        self._git("checkout", "-q", "--orphan", self.branch, check=False)
        return True, f"شاخه‌ی یتیم {self.branch} ساخته شد"

    # -- انتشار --------------------------------------------------------
    def _commits_last_hour(self) -> int:
        try:
            _, out, _ = self._git("log", "--since=1 hour ago", "--oneline", check=False)
            return len([line for line in out.splitlines() if line.strip()])
        except CommandError:
            return 0

    def publish(self, files: dict[str, str], message: str, dry_run: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "ok": False,
            "action": "skipped",
            "branch": self.branch,
            "dir": str(self.dir),
            "files": sorted(files),
            "hash": None,
            "error": None,
            "ts": utc_iso(),
        }
        ok, why = self.ensure()
        if not ok:
            result["error"] = why
            return result

        written: list[str] = []
        for rel, text in files.items():
            target = self.dir / rel
            atomic_write(target, text)
            written.append(rel)

        if dry_run:
            result.update(ok=True, action="dry-run", files=written)
            return result

        for path in self.paths:
            if (self.dir / path).exists():
                self._git("add", "--", path, timeout=60)

        code, out, _ = self._git("status", "--porcelain", timeout=30, check=False)
        if not out.strip():
            result.update(ok=True, action="no-changes")
            return result

        if self.max_commits_per_hour and self._commits_last_hour() >= self.max_commits_per_hour:
            result.update(ok=True, action="committed-no-push", error="سقف کامیت ساعتی")
            try:
                self._commit(message)
                _, h, _ = self._git("rev-parse", "--short", "HEAD")
                result["hash"] = h.strip()
            except CommandError as exc:
                result["error"] = str(exc)
            return result

        try:
            self._commit(message)
            _, h, _ = self._git("rev-parse", "--short", "HEAD")
            result["hash"] = h.strip()
        except CommandError as exc:
            result.update(action="commit-failed", error=str(exc))
            return result

        try:
            self._git("push", "origin", f"HEAD:refs/heads/{self.branch}", timeout=self.push_timeout)
            result.update(ok=True, action="pushed")
            LOG.info("گزارش پوش شد: %s (%s)", result["hash"], self.branch)
            return result
        except CommandError as exc:
            LOG.warning("پوش ناموفق: %s", exc)
            # یک تلاش دیگر با rebase، بدون از دست دادن کامیت
            try:
                self._git("pull", "--rebase", "--autostash", "origin", self.branch, timeout=self.push_timeout, check=False)
                self._git("push", "origin", f"HEAD:refs/heads/{self.branch}", timeout=self.push_timeout)
                result.update(ok=True, action="pushed-after-rebase")
                return result
            except CommandError as exc2:
                result.update(ok=False, action="committed-local", error=f"{exc2}")
                return result

    def _commit(self, message: str) -> None:
        env = {
            "GIT_AUTHOR_NAME": self.author_name,
            "GIT_AUTHOR_EMAIL": self.author_email,
            "GIT_COMMITTER_NAME": self.author_name,
            "GIT_COMMITTER_EMAIL": self.author_email,
        }
        run_cmd(["git", "-C", str(self.dir), "commit", "-q", "-m", message], timeout=60, env=env)


def prune_jsonl(text: str, keep: int = 5000) -> str:
    """نگه‌داشتن آخرین خطوط برای اینکه فایل گزارش بی‌نهایت بزرگ نشود."""
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) <= keep:
        return "\n".join(lines) + "\n"
    return "\n".join(lines[-keep:]) + "\n"
