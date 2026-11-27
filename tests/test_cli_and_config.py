from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import REPO_ROOT

from tradeagent.cli import main
from tradeagent.config import Config
from tradeagent.doctor import FAIL, OK, run_doctor, summary
from tradeagent.util import LockFile, atomic_write, clamp, read_json, write_json


def test_config_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = Config.load(None, root=tmp_path)
    monkeypatch.setenv("TRADEAGENT_SOURCE", "synthetic")
    monkeypatch.setenv("TRADEAGENT_SYMBOLS", "BTCUSD, ETHUSD")
    monkeypatch.setenv("TRADEAGENT_RISK_PCT", "0.25")
    monkeypatch.setenv("TRADEAGENT_PUBLISH", "false")
    cfg._apply_env()
    assert cfg.get("data.source") == "synthetic"
    assert cfg.symbols == ["BTCUSD", "ETHUSD"]
    assert cfg.get("paper.risk_per_trade_pct") == 0.25
    assert cfg.get("publish.enabled") is False


def test_dotenv_is_loaded_without_overriding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRADEAGENT_SOURCE", raising=False)
    monkeypatch.setenv("TRADEAGENT_RISK_PCT", "0.9")
    (tmp_path / ".env").write_text(
        "# comment\nexport TRADEAGENT_SOURCE=synthetic\nTRADEAGENT_RISK_PCT=0.1\nGEMINI_API_KEY=abc\n",
        encoding="utf-8",
    )
    cfg = Config.load(None, root=tmp_path)
    assert cfg.get("data.source") == "synthetic"
    # متغیر محیطی موجود بر .env مقدم است
    assert cfg.get("paper.risk_per_trade_pct") == 0.9
    import os

    assert os.environ["GEMINI_API_KEY"] == "abc"


def test_config_paths_expand(tmp_path: Path) -> None:
    cfg = Config({"data": {"state_dir": "var", "abs": "/tmp/x"},
                  "paper": {"sqlite": "var/paper.sqlite3"}}, root=tmp_path)
    assert cfg.path("data.state_dir") == tmp_path / "var"
    assert cfg.path("data.abs") == Path("/tmp/x")
    # کلید نقطه‌ای → مقدار پیکربندی
    assert cfg.path("paper.sqlite") == tmp_path / "var" / "paper.sqlite3"
    # کلید ناشناخته → پیش‌فرض (نه تبدیل به مسیر بی‌معنی در ریشه)
    assert cfg.path("paper.unknown_key", "var/fallback.db") == tmp_path / "var" / "fallback.db"
    # مسیر خام
    assert cfg.path("/tmp/raw.db") == Path("/tmp/raw.db")
    assert cfg.path("/tmp/raw2.db", "var/x") == Path("/tmp/raw2.db")


def test_atomic_write_and_json(tmp_path: Path) -> None:
    path = tmp_path / "a" / "b.json"
    write_json(path, {"x": 1})
    assert read_json(path) == {"x": 1}
    atomic_write(path, "plain")
    assert path.read_text(encoding="utf-8") == "plain"
    assert read_json(tmp_path / "missing.json", default={"d": 1}) == {"d": 1}
    assert clamp(5, 0, 3) == 3


def test_lockfile(tmp_path: Path) -> None:
    first = LockFile(tmp_path / "l.lock", stale_after_s=60)
    second = LockFile(tmp_path / "l.lock", stale_after_s=60)
    assert first.acquire() is True
    assert second.acquire() is False
    first.release()
    assert second.acquire() is True
    second.release()


def test_doctor_on_synthetic_has_no_failures(cfg: Config) -> None:
    checks = run_doctor(cfg, network=False)
    assert checks
    assert not [c for c in checks if c.status == FAIL], [c.render() for c in checks]
    payload = summary(checks)
    assert payload["counts"][OK] >= 1


def test_cli_demo_runs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--root", str(tmp_path), "demo", "--cycles", "2", "--step", "120"])
    assert code == 0
    out = capsys.readouterr().out
    assert "چرخه" in out
    assert "گزارش نمونه" in out
    latest = list((tmp_path / "var" / "demo").glob("*/reports/latest.md"))
    assert latest and latest[0].read_text(encoding="utf-8").startswith("# گزارش")


def test_cli_init_and_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--root", str(tmp_path), "init"]) == 0
    assert (tmp_path / "configs" / "local.toml").exists()
    assert main(["--root", str(tmp_path), "report"]) == 1
    capsys.readouterr()
    main(["--root", str(tmp_path), "demo", "--cycles", "1", "--step", "60"])
    code = main(["--root", str(tmp_path), "report"])
    assert code == 0


def test_cli_doctor_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(["--root", str(tmp_path), "init"])
    capsys.readouterr()
    code = main(["--root", str(tmp_path), "doctor", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert "checks" in payload and "counts" in payload
    assert code in (0, 1)


def test_doctor_reads_per_symbol_status_age(tmp_path):
    """EA فیلد age_s را برای هر نماد می‌نویسد، نه سطح بالا؛ doctor باید بخواند."""
    import json

    from tradeagent.doctor import run_doctor

    harv = tmp_path / "harvester"
    (harv / "ticks" / "XAUUSD_i").mkdir(parents=True)
    (harv / "symbols.json").write_text(
        json.dumps({"symbols": [{"symbol": "XAUUSD_i"}]}), encoding="utf-8"
    )
    (harv / "status.json").write_text(
        json.dumps(
            {
                "ea": "TickHarvester",
                "updated_ts": "2026.11.26 13:00:00",
                "symbols": [
                    {"symbol": "XAUUSD_i", "age_s": 2, "ticks": 100},
                    {"symbol": "EURUSD_i", "age_s": 3, "ticks": 50},
                ],
            }
        ),
        encoding="utf-8",
    )
    cfg = Config.load(REPO_ROOT / "configs" / "default.toml", root=tmp_path)
    cfg.set("data.source", "harvester")
    cfg.set("data.harvester_dir", str(harv))
    cfg.set("publish.enabled", False)
    cfg.set("llm.enabled", False)
    checks = run_doctor(cfg, network=False)
    data_check = next(c for c in checks if "harvester" in c.name)
    assert data_check.status == "ok", data_check.detail
    assert "s پیش" in data_check.detail


def test_doctor_flags_stale_heartbeat(tmp_path):
    import json

    from tradeagent.doctor import run_doctor

    harv = tmp_path / "harvester"
    (harv / "ticks").mkdir(parents=True)
    (harv / "status.json").write_text(
        json.dumps({"symbols": [{"symbol": "XAUUSD_i", "age_s": 9999}]}), encoding="utf-8"
    )
    cfg = Config.load(REPO_ROOT / "configs" / "default.toml", root=tmp_path)
    cfg.set("data.source", "harvester")
    cfg.set("data.harvester_dir", str(harv))
    cfg.set("publish.enabled", False)
    cfg.set("llm.enabled", False)
    checks = run_doctor(cfg, network=False)
    data_check = next(c for c in checks if "harvester" in c.name)
    assert data_check.status == "warn"


def test_doctor_falls_back_to_mtime_when_status_is_half_written(tmp_path):
    """status.json ممکن است وسط بازنویسی خوانده شود؛ doctor نباید FAIL بدهد."""
    from tradeagent.doctor import run_doctor

    harv = tmp_path / "harvester"
    (harv / "ticks").mkdir(parents=True)
    (harv / "status.json").write_text('{"symbols":[{"symbol":"XAUUSD_i","age_s":', encoding="utf-8")
    cfg = Config.load(REPO_ROOT / "configs" / "default.toml", root=tmp_path)
    cfg.set("data.source", "harvester")
    cfg.set("data.harvester_dir", str(harv))
    cfg.set("publish.enabled", False)
    cfg.set("llm.enabled", False)
    checks = run_doctor(cfg, network=False)
    data_check = next(c for c in checks if "harvester" in c.name)
    # فایل تازه است → mtime می‌گوید سالم، نه هشدار «اکسپرت اجرا نشده»
    assert data_check.status == "ok", data_check.detail
    assert "s پیش" in data_check.detail


def test_lockfile_recovers_from_a_dead_owner(tmp_path: Path) -> None:
    import os
    import time

    path = tmp_path / "dead.lock"
    path.write_text("999999 2026-01-01T00:00:00Z\n", encoding="utf-8")
    os.utime(path, (time.time() - 10, time.time() - 10))
    lock = LockFile(path, stale_after_s=60)   # سن کم، ولی صاحبش مرده است
    assert lock.acquire() is True
    assert str(os.getpid()) in path.read_text(encoding="utf-8")
    lock.release()


def test_lockfile_keeps_a_fresh_unknown_lock(tmp_path: Path) -> None:
    import time

    path = tmp_path / "anon.lock"
    path.write_text("garbage", encoding="utf-8")
    os_utime = __import__("os").utime
    os_utime(path, (time.time(), time.time()))
    assert LockFile(path, stale_after_s=60).acquire() is False
