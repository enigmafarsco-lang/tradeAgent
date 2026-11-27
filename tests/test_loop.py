from __future__ import annotations

import json
from pathlib import Path

from tradeagent.config import Config
from tradeagent.loop import Runner
from tradeagent.policy import build_policy_for_level


def test_cycles_write_reports_and_state(cfg: Config) -> None:
    runner = Runner(cfg)
    start = 1_700_000_000
    results = []
    for i in range(4):
        results.append(runner.run_cycle(now_ts=start + i * 300, publish=False))

    assert all(r["ok"] for r in results)
    assert [r["cycle"] for r in results] == [1, 2, 3, 4]

    state = cfg.state_dir
    assert (state / "reports" / "latest.md").exists()
    assert (state / "reports" / "cycles.jsonl").read_text(encoding="utf-8").strip()
    lines = [line for line in (state / "reports" / "cycles.jsonl").read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) == 4
    compact = json.loads(lines[-1])
    assert compact["cycle"] == 4
    assert compact["source"] == "synthetic"
    assert compact["symbols"] == ["BTCUSD", "ETHUSD"]

    policy = runner.store.get_meta("policy")
    assert policy["variant"] in ("momentum", "meanrev", "scalp")
    assert (state / "reports" / "archive").exists()
    assert runner.store.latest_cycle()["cycle"] == 4


def test_policy_level_persists_between_cycles(cfg: Config) -> None:
    runner = Runner(cfg)
    runner.store.set_meta("policy", build_policy_for_level("momentum", 3))
    start = 1_700_000_000
    runner.run_cycle(now_ts=start, publish=False)
    policy = runner.store.get_meta("policy")
    assert policy["level"] >= 3
    md = (cfg.state_dir / "reports" / "latest.md").read_text(encoding="utf-8")
    assert "پله" in md


def test_llm_disabled_by_config_is_fine(cfg: Config) -> None:
    cfg.set("llm.provider", "gemini")
    cfg.set("llm.enabled", True)  # اما کلید نیست
    runner = Runner(cfg)
    result = runner.run_cycle(now_ts=1_700_000_000, force_llm=True, publish=False)
    assert result["ok"]
    md = (cfg.state_dir / "reports" / "latest.md").read_text(encoding="utf-8")
    assert "مدل زبانی" in md


def test_publish_failure_does_not_break_cycle(cfg: Config, tmp_path: Path) -> None:
    cfg.set("publish.enabled", True)
    cfg.set("publish.remote", str(tmp_path / "missing.git"))
    cfg.set("publish.dir", str(tmp_path / "pub"))
    runner = Runner(cfg)
    result = runner.run_cycle(now_ts=1_700_000_000, publish=True)
    assert result["ok"]
    assert result["publish"]["action"] in ("committed-local", "committed-no-push", "pushed", "no-changes")


def test_manual_policy_respected(cfg: Config) -> None:
    runner = Runner(cfg)
    policy = build_policy_for_level("scalp", 0)
    runner.store.set_meta("policy", policy)
    runner.run_cycle(now_ts=1_700_000_000, publish=False)
    stored = runner.store.get_meta("policy")
    assert stored["variant"] in ("scalp", "momentum", "meanrev")


def test_resolve_harvester_dir_finds_the_wine_prefix(tmp_path, monkeypatch) -> None:
    """اگر کانفیگ مسیر اشتباه داشته باشد، مسیر واقعی زیر WINEPREFIX پیدا می‌شود."""
    from tradeagent.loop import resolve_harvester_dir

    prefix = tmp_path / "wine"
    real = prefix / "drive_c/Program Files/MetaTrader 5/MQL5/Files/harvester"
    (real / "ticks" / "EURUSD").mkdir(parents=True)
    monkeypatch.setenv("WINEPREFIX", str(prefix))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    found = resolve_harvester_dir(tmp_path / "nope")
    assert found == real


def test_resolve_harvester_dir_keeps_a_working_path(tmp_path, monkeypatch) -> None:
    from tradeagent.loop import resolve_harvester_dir

    good = tmp_path / "harv"
    (good / "ticks").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert resolve_harvester_dir(good) == good


def test_bridge_status_is_published_for_the_watcher(cfg: Config) -> None:
    """سوکت ۸۷۸۷ یک صاحب دارد؛ پس سرویس باید وضعیتش را روی دیسک بگذارد."""
    runner = Runner(cfg)

    class FakeMCP:
        enabled = True
        calls: list[str] = []

        def call_tool(self, name, args=None):  # noqa: ANN001, ANN201
            FakeMCP.calls.append(name)
            assert name == "mt5_status"
            return {
                "ok": True,
                "bridge": {"listen": "127.0.0.1:8787", "ea_connected": True, "ea_peer": "127.0.0.1:5"},
                "terminal": {"company": "Alpari", "build": 6333},
                "account": {"login": 53290594, "server": "Alpari-MT5-Demo", "balance": 9618.64},
            }

    runner.mcp = FakeMCP()
    status = runner.publish_mcp_status()
    assert status["ok"] is True
    path = Path(cfg.state_dir) / "mcp_status.json"
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["bridge"]["ea_connected"] is True
    assert on_disk["account"]["login"] == 53290594

    class BrokenMCP(FakeMCP):
        def call_tool(self, name, args=None):  # noqa: ANN001, ANN201
            raise RuntimeError("cannot listen on 127.0.0.1:8787: [Errno 98] bind_failed")

    runner.mcp = BrokenMCP()
    status = runner.publish_mcp_status()
    assert status["ok"] is False
    assert "bind" in status["error"].lower()
    assert "owner" in status["hint"].lower()


def test_bridge_probe_proves_the_socket_exists() -> None:
    """«listen» در config اثبات باز بودن سوکت نیست؛ باید واقعاً connect کنیم."""
    import socket
    import threading

    from tradeagent.loop import Runner

    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    threading.Thread(target=lambda: srv.accept()[0].close(), daemon=True).start()

    assert Runner._probe_socket(f"127.0.0.1:{port}") is True
    srv.close()
    assert Runner._probe_socket(f"127.0.0.1:{port}") is False


def test_ea_log_is_read_from_the_prefix(tmp_path, monkeypatch) -> None:
    """لاگ اکسپرت باید از خودِ متاتریدر خوانده شود، تا کاربر کپی نکند."""
    from tradeagent.loop import Runner

    logs = tmp_path / "drive_c" / "Program Files" / "MetaTrader 5" / "MQL5" / "Logs"
    logs.mkdir(parents=True)
    (logs / "20261127.log").write_text(
        "2026.11.27 14:52:32\tTradeMCPConnector (XAUUSD_i,H1)\t[trade-mcp] connect to 127.0.0.1:8787 failed, err=4014\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MT5_WINEPREFIX", str(tmp_path))

    info = Runner._ea_log()
    assert info["found"] is True
    assert info["not_allowed"] is True
    assert "err=4014" in info["last"][-1]


def test_ea_log_prefers_the_file_that_has_expert_lines(tmp_path, monkeypatch) -> None:
    """Journal تازه‌تر است ولی خط اکسپرت ندارد؛ نباید آن را به‌جای Experts بدهد."""
    from tradeagent.loop import Runner

    root = tmp_path / "drive_c" / "Program Files" / "MetaTrader 5"
    (root / "MQL5" / "Logs").mkdir(parents=True)
    (root / "logs").mkdir(parents=True)
    (root / "MQL5" / "Logs" / "20261127.log").write_text(
        "2026.11.27 14:52:32\tTradeMCPConnector\t[trade-mcp] connect to 127.0.0.1:8787 failed, err=4014\n",
        encoding="utf-8",
    )
    journal = root / "logs" / "20261127.log"
    journal.write_text("2026.11.27 15:00:01\tNetwork\tterminal synchronized\n", encoding="utf-8")
    import os

    os.utime(journal, None)  # Journal تازه‌تر است
    monkeypatch.setenv("MT5_WINEPREFIX", str(tmp_path))

    info = Runner._ea_log()
    assert "MQL5" in info["file"]
    assert info["not_allowed"] is True


def test_ea_log_is_utf16_as_metatrader_writes_it(tmp_path, monkeypatch) -> None:
    """متاتریدر لاگ را UTF-16LE می‌نویسد؛ utf-8 خواندن یعنی دیدن هیچ خطی."""
    from tradeagent.loop import Runner

    logs = tmp_path / "drive_c" / "Program Files" / "MetaTrader 5" / "MQL5" / "Logs"
    logs.mkdir(parents=True)
    line = "2026.11.27 14:52:32\tTradeMCPConnector (XAUUSD_i,H1)\t[trade-mcp] connect failed, err=4014\r\n"
    (logs / "20261127.log").write_bytes(line.encode("utf-16-le"))
    monkeypatch.setenv("MT5_WINEPREFIX", str(tmp_path))

    info = Runner._ea_log()
    assert info["not_allowed"] is True
    assert "err=4014" in info["last_attempt"]


def test_diag_finds_logs_deep_inside_the_prefix(tmp_path, monkeypatch) -> None:
    """اگر دادهٔ ترمینال در AppData باشد، جست‌وجوی بازگشتی باید پیدایش کند."""
    from tradeagent.diag import ea_log

    monkeypatch.setenv("MT5_WINEPREFIX", str(tmp_path))
    deep = tmp_path / "drive_c" / "users" / "joshua" / "AppData" / "Roaming" / "MetaQuotes" / "Terminal" / "X1" / "MQL5" / "Logs"
    deep.mkdir(parents=True)
    (deep / "20261127.log").write_bytes(
        "2026.11.27 14:54:15\tTradeMCPConnector\t[trade-mcp] connected\r\n".encode("utf-16-le")
    )
    info = ea_log()
    assert info["found"] is True and info["connected"] is True


def test_diag_surfaces_terminal_notices(tmp_path, monkeypatch) -> None:
    """«automated trading is disabled» باید دیده شود، نه اینکه حدس بزنیم."""
    from tradeagent.diag import journal_notices

    monkeypatch.setenv("MT5_WINEPREFIX", str(tmp_path))
    journal = tmp_path / "drive_c" / "Program Files" / "MetaTrader 5" / "logs"
    journal.mkdir(parents=True)
    (journal / "20261127.log").write_text(
        "GH 0 14:55:01.000 Experts automated trading is disabled\n", encoding="utf-8"
    )
    assert any("automated trading is disabled" in n for n in journal_notices())


def test_diag_knows_4014_is_a_permission_error(tmp_path, monkeypatch) -> None:
    """۴۰۱۴ = «Function is not allowed», نه connection refused."""
    from tradeagent.diag import ea_log

    monkeypatch.setenv("MT5_WINEPREFIX", str(tmp_path))
    logs = tmp_path / "drive_c" / "Program Files" / "MetaTrader 5" / "MQL5" / "logs"
    logs.mkdir(parents=True)
    (logs / "20261127.log").write_text(
        "HK 0 15:16:05.028 TradeMCPConnector (XAUUSD_i,M1) [trade-mcp] connect to 127.0.0.1:8787 failed, err=4014\n",
        encoding="utf-8",
    )
    info = ea_log()
    assert info["not_allowed"] is True
    assert info["refused"] is False


def test_last_attempt_decides_not_the_older_errors(tmp_path, monkeypatch) -> None:
    """بعد از یک اتصال موفق، خط‌های ۴۰۱۴ قدیمی نباید «blocked» گزارش شوند."""
    from tradeagent.diag import ea_log

    monkeypatch.setenv("MT5_WINEPREFIX", str(tmp_path))
    logs = tmp_path / "drive_c" / "Program Files" / "MetaTrader 5" / "MQL5" / "logs"
    logs.mkdir(parents=True)
    (logs / "20261127.log").write_text(
        "NE 0 15:27:52 TradeMCPConnector [trade-mcp] connect to 127.0.0.1:8787 failed, err=4014\n"
        "GH 0 15:27:58 TradeMCPConnector [trade-mcp] connected to 127.0.0.1:8787\n",
        encoding="utf-8",
    )
    info = ea_log()
    assert info["connected"] is True
    assert info["not_allowed"] is False


def test_diag_reports_harvester_freshness(tmp_path, monkeypatch) -> None:
    """bars=0 باید علتش دیده شود: تیک تازه می‌رسد یا نه."""
    from tradeagent.diag import harvester

    monkeypatch.setenv("MT5_WINEPREFIX", str(tmp_path))
    ticks = tmp_path / "drive_c" / "Program Files" / "MetaTrader 5" / "MQL5" / "Files" / "harvester" / "ticks"
    ticks.mkdir(parents=True)
    (ticks / "XAUUSD_i-20261127.csv").write_text("t,bid,ask\n", encoding="utf-8")

    info = harvester()
    assert info["found"] is True
    assert info["ticks"] == 1
    assert info["newest"].startswith("XAUUSD_i")
    assert isinstance(info["age_s"], int)


def test_mt5_pids_ignores_shells_that_mention_the_name() -> None:
    """شلی که فقط رشتهٔ terminal64.exe را در متن دارد، پروسهٔ متاتریدر نیست."""
    from tradeagent.diag import mt5_pids

    pids = mt5_pids()
    for pid in pids:  # هرچه برگشت باید واقعاً آرگومانِ برنامه باشد
        args = (Path("/proc") / pid / "cmdline").read_bytes().split(b"\x00")
        assert any(a.rstrip(b"/").endswith(b"terminal64.exe") for a in args)


def test_mt5_pids_finds_wine_process_by_comm(tmp_path) -> None:
    """wine نامِ برنامه را در comm می‌گذارد؛ همان باید کافی باشد."""
    from tradeagent.diag import mt5_pids

    def mk(pid: str, comm: str, argv: bytes = b"\x00") -> None:
        d = tmp_path / pid
        d.mkdir()
        (d / "comm").write_text(comm + "\n", encoding="utf-8")
        (d / "cmdline").write_bytes(argv)

    mk("4242", "terminal64.exe", b"Z:\\t\\terminal64.exe\x00/portable\x00")
    mk("4243", "bash", b"bash\x00-c\x00./scripts/start_mt5.sh terminal64.exe\x00")
    mk("4244", "wine64-preloader", b"wine\x00terminal64.exe\x00")
    assert mt5_pids(tmp_path) == ["4242"]
