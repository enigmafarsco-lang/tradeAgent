from __future__ import annotations

from pathlib import Path

import pytest

from tradeagent.config import Config
from tradeagent.publish import Publisher, prune_jsonl
from tradeagent.util import run_cmd


def git(*args: str, cwd: Path | None = None) -> str:
    code, out, err = run_cmd(["git", *args], cwd=cwd, timeout=60, check=False)
    assert code == 0, f"git {args} failed: {err}"
    return out.strip()


@pytest.fixture()
def git_env(tmp_path: Path) -> tuple[Path, Path]:
    bare = tmp_path / "remote.git"
    code = tmp_path / "code"
    run_cmd(["git", "init", "--bare", "-q", str(bare)], timeout=30)
    code.mkdir()
    git("init", "-q", cwd=code)
    git("config", "user.email", "t@example.com", cwd=code)
    git("config", "user.name", "tester", cwd=code)
    (code / "README.md").write_text("code\n", encoding="utf-8")
    git("add", ".", cwd=code)
    git("commit", "-q", "-m", "init", cwd=code)
    git("remote", "add", "origin", str(bare), cwd=code)
    git("push", "-q", "-u", "origin", "HEAD:main", cwd=code)
    return code, bare


def test_publish_creates_branch_and_pushes(git_env, tmp_path: Path) -> None:
    code, bare = git_env
    cfg = Config({"publish": {
        "enabled": True, "remote": str(bare), "branch": "reports",
        "dir": str(tmp_path / "publish"), "paths": ["reports"], "mode": "every_cycle",
    }}, root=code)
    publisher = Publisher(cfg, code)
    assert publisher.ensure()[0] is True

    files = {"reports/paper/latest.md": "# گزارش\n", "reports/paper/cycles.jsonl": '{"cycle":1}\n'}
    result = publisher.publish(files, "cycle #1")
    assert result["ok"] is True
    assert result["action"] == "pushed"
    assert result["hash"]

    branches = git("--git-dir", str(bare), "branch", "--list")
    assert "reports" in branches
    content = git("--git-dir", str(bare), "show", "reports:reports/paper/latest.md")
    assert "گزارش" in content

    # بدون تغییر → بدون کامیت
    again = publisher.publish(files, "cycle #1 again")
    assert again["ok"] is True
    assert again["action"] == "no-changes"


def test_publish_dry_run_does_not_commit(git_env, tmp_path: Path) -> None:
    code, bare = git_env
    cfg = Config({"publish": {"enabled": True, "remote": str(bare), "branch": "reports",
                              "dir": str(tmp_path / "publish2"), "paths": ["reports"]}}, root=code)
    publisher = Publisher(cfg, code)
    result = publisher.publish({"reports/paper/latest.md": "# x\n"}, "dry", dry_run=True)
    assert result["ok"] and result["action"] == "dry-run"
    assert (tmp_path / "publish2" / "reports" / "paper" / "latest.md").read_text(encoding="utf-8") == "# x\n"


def test_publish_disabled(tmp_path: Path) -> None:
    cfg = Config({"publish": {"enabled": False}}, root=tmp_path)
    publisher = Publisher(cfg, tmp_path)
    ok, why = publisher.ensure()
    assert ok is False and "غیرفعال" in why
    result = publisher.publish({"reports/x.md": "x"}, "m")
    assert result["action"] == "skipped" and result["ok"] is False


def test_publish_survives_unreachable_remote(tmp_path: Path) -> None:
    cfg = Config({"publish": {
        "enabled": True, "remote": str(tmp_path / "does-not-exist.git"), "branch": "reports",
        "dir": str(tmp_path / "p3"), "paths": ["reports"], "push_timeout_s": 20,
    }}, root=tmp_path)
    publisher = Publisher(cfg, tmp_path)
    ok, _ = publisher.ensure()
    assert ok is True, "کلون ناموفق باید شاخه‌ی محلی بسازد"
    result = publisher.publish({"reports/paper/latest.md": "x"}, "m")
    assert result["action"] in ("committed-local", "committed-no-push")
    assert result["hash"]


def test_prune_jsonl() -> None:
    text = "".join(f'{{"i":{i}}}\n' for i in range(10))
    pruned = prune_jsonl(text, keep=3)
    assert len(pruned.strip().splitlines()) == 3
    assert '"i":9' in pruned
    assert prune_jsonl("a\nb\n", keep=10) == "a\nb\n"
