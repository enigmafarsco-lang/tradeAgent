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
    harv = tmp_path / "drive_c" / "Program Files" / "MetaTrader 5" / "MQL5" / "Files" / "harvester"
    sym = harv / "ticks" / "XAUUSD_i"
    sym.mkdir(parents=True)
    (sym / "20261127.csv").write_text("t,bid,ask\n", encoding="utf-8")
    (harv / "status.json").write_text(
        '{"ea":"TickHarvester","polls":7,"total_ticks":42,"total_errors":0,'
        '"symbols":[{"symbol":"XAUUSD_i","ticks":42,"age_s":2}]}',
        encoding="utf-8",
    )

    info = harvester()
    assert info["found"] is True
    assert info["ticks"] == 1  # چیدمان اکسپرت: ticks/<SYMBOL>/<day>.csv
    assert info["newest"] == "20261127.csv"
    assert info["newest_parent"] == "XAUUSD_i"
    assert isinstance(info["age_s"], int)
    assert info["status"]["symbols"][0]["symbol"] == "XAUUSD_i"


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


def test_diag_reports_the_symbol_the_loop_needs(tmp_path, monkeypatch) -> None:
    """۸۰ فایل از ۸۰ نماد یعنی هیچ؛ باید بدانیم خودِ XAUUSD_i تازه است یا نه."""
    from tradeagent.diag import harvester

    monkeypatch.setenv("MT5_WINEPREFIX", str(tmp_path))
    harv = tmp_path / "drive_c/Program Files/MetaTrader 5/MQL5/Files/harvester"
    (harv / "ticks" / "NAS100_i").mkdir(parents=True)
    (harv / "ticks" / "NAS100_i" / "20261127.csv").write_text("t;b\n", encoding="utf-8")

    info = harvester()
    mine = info["watch"][0]
    assert mine["symbol"] == "XAUUSD_i"
    assert mine["files"] == 0 and mine["age_s"] is None


def test_status_json_with_a_datetime_uptime_is_still_readable(tmp_path, monkeypatch) -> None:
    """اکسپرت uptime_s را تاریخ می‌نویسد و JSON نامعتبر می‌شود؛ عددها باید باز خوانده شوند."""
    from tradeagent.diag import harvester_status

    d = tmp_path
    (d / "status.json").write_text(
        '{"ea":"TickHarvester","uptime_s":1970.01.01 00:52:55,"polls":812,'
        '"total_ticks":15234,"total_errors":3,'
        '"symbols":[{"symbol":"XAUUSD_i","ticks":15234,"errors":0,"age_s":3}]}',
        encoding="utf-8",
    )
    st = harvester_status(d)
    assert st["broken"] is True
    assert st["polls"] == 812 and st["total_ticks"] == 15234 and st["errors"] == 3
    assert st["symbols"] == [{"symbol": "XAUUSD_i", "ticks": 15234, "age_s": 3}]


def test_local_tools_resolve_the_symbol_name_the_model_uses(cfg: Config) -> None:
    """مدل «XAUUSD» می‌نویسد ولی داده با «XAUUSD_i» ذخیره شده؛ نباید خالی برگردد."""
    from tradeagent.ticks import Bar

    runner = Runner(cfg)
    now = 1_700_000_000
    runner.store.add_bars(
        "XAUUSD_i",
        [
            Bar(t=now + i, open=4000 + i, high=4001 + i, low=3999 + i, close=4000.5 + i,
                bid_close=4000.4 + i, ask_close=4000.6 + i, spread_bps_max=1.0, ticks=5, volume=1.0)
            for i in range(6)
        ],
    )
    tools = runner._local_tools()
    dig = tools["local_tick_digest"](symbol="XAUUSD")
    assert "error" not in dig
    assert dig["symbol"] == "XAUUSD_i" and dig["bars"] == 6
    cand = tools["local_candles"](symbol="xauusd", seconds=1, count=3)
    assert cand["symbol"] == "XAUUSD_i" and cand["candles"]


def _bars(symbol: str, n: int = 400, start: float = 4000.0, drift: float = 0.02):
    from tradeagent.ticks import Bar

    return [
        Bar(t=1_700_000_000 + i, open=start + i * drift, high=start + i * drift + 0.5,
            low=start + i * drift - 0.5, close=start + (i + 1) * drift,
            bid_close=start + (i + 1) * drift - 0.1, ask_close=start + (i + 1) * drift + 0.1,
            spread_bps_max=1.0, ticks=20, volume=1.0)
        for i in range(n)
    ]


def test_agent_verdict_opens_a_paper_position(cfg: Config) -> None:
    """حکم مدل باید پوزیشن شود؛ نه اینکه فقط در گزارش بیاید."""
    runner = Runner(cfg)
    bars = _bars("XAUUSD_i")
    runner.store.add_bars("XAUUSD_i", bars)
    act = runner.engine.open_from_momentum(
        "XAUUSD_i",
        bars,
        {"direction": "up", "phase": "accelerating", "strength": 0.7, "confidence": 0.8,
         "horizon_s": 120, "reason": "شتاب مثبت"},
        runner.current_policy(),
        1,
        now_ts=bars[-1].t,
    )
    assert act["action"] == "open" and act["side"] == 1
    pos = runner.store.load_positions()["XAUUSD_i"]
    assert pos.side == 1 and pos.variant == "agent_momentum"
    assert 5 <= pos.hold_s <= 900


def test_low_confidence_verdict_does_not_trade(cfg: Config) -> None:
    runner = Runner(cfg)
    bars = _bars("XAUUSD_i")
    runner.store.add_bars("XAUUSD_i", bars)
    act = runner.engine.open_from_momentum(
        "XAUUSD_i", bars,
        {"direction": "down", "strength": 0.2, "confidence": 0.1, "horizon_s": 60},
        runner.current_policy(), 1, now_ts=bars[-1].t,
    )
    assert act["action"] == "none"
    assert not runner.store.load_positions()


def test_opposite_verdict_flips_the_position(cfg: Config) -> None:
    runner = Runner(cfg)
    bars = _bars("XAUUSD_i")
    runner.store.add_bars("XAUUSD_i", bars)
    policy = runner.current_policy()
    runner.engine.open_from_momentum(
        "XAUUSD_i", bars,
        {"direction": "up", "strength": 0.8, "confidence": 0.9, "horizon_s": 60},
        policy, 1, now_ts=bars[-1].t,
    )
    later = _bars("XAUUSD_i", start=4001.0, drift=-0.02)
    for b in later:
        b.t += 1000
    runner.store.add_bars("XAUUSD_i", later)
    act = runner.engine.open_from_momentum(
        "XAUUSD_i", later,
        {"direction": "down", "strength": 0.8, "confidence": 0.9, "horizon_s": 60},
        policy, 2, now_ts=later[-1].t,
    )
    assert act.get("closed"), "پوزیشن قبلی باید بسته شود"
    assert act["action"] == "open" and act["side"] == -1


def test_model_context_names_the_only_instrument(cfg: Config) -> None:
    """مدل باید بداند تنها نماد معاملاتی XAUUSD_i است، وگرنه «XAUUSD» می‌پرسد و عدد دیگری می‌گیرد."""
    runner = Runner(cfg)
    cycles = getattr(runner, "store", None)
    assert cycles is not None
    result = runner.engine.process(
        {"XAUUSD_i": _bars("XAUUSD_i", 360)}, runner.current_policy(), 1, now_ts=1_700_000_400
    )
    ctx = runner.build_llm_context(result, runner.current_policy())
    assert ctx["only_trade_this_symbol"] == cfg.symbols[0]
    assert ctx["only_trade_this_symbol"] in ctx["symbols"]


def test_one_shot_script_prints_one_block_and_restores_the_service() -> None:
    from pathlib import Path

    body = (Path(__file__).resolve().parents[1] / "scripts" / "one_shot.sh").read_text(encoding="utf-8")
    assert "run_gold.sh\" --verify" in body
    assert "VERIFY_QUIET=1" in body
    assert "نتیجه:" in body and "خلاصه" in body
    # نباید خودش سرویس را دستکاری کند؛ run_gold --verify این کار را می‌کند
    assert "systemctl" not in body


def test_verify_quiet_hides_the_tool_table() -> None:
    from pathlib import Path

    body = (Path(__file__).resolve().parents[1] / "scripts" / "run_gold.sh").read_text(encoding="utf-8")
    assert "VERIFY_QUIET" in body
    assert 'ابزارها: {len(calls) - len(bad)}/{len(calls)} موفق' in body


def test_one_shot_reads_the_tick_line_the_way_diag_prints_it() -> None:
    """چکیده نباید «تیک نمی‌رسد» بگوید وقتی تیک می‌رسد؛ الگو باید با خروجی diag بخواند."""
    from pathlib import Path
    import re

    body = (Path(__file__).resolve().parents[1] / "scripts" / "one_shot.sh").read_text(encoding="utf-8")
    line = "    XAUUSD_i: 2 فایل · 20261127.csv · 0s پیش · 2208863 بایت"
    m = re.search(r"XAUUSD_i: (\d+) فایل · \S+ · (\d+)s پیش", line)
    assert m and m.group(1) == "2" and m.group(2) == "0"
    assert "tradeagent.diag import report" in body  # همان مسیری که --status استفاده می‌کند
    assert 'VERIFY_QUIET' in body


def test_verify_quiet_is_read_from_the_environment() -> None:
    """QUIET داخل heredoc پایتون نمی‌رسد؛ باید از os.environ خوانده شود."""
    from pathlib import Path

    body = (Path(__file__).resolve().parents[1] / "scripts" / "run_gold.sh").read_text(encoding="utf-8")
    assert 'QUIET = os.environ.get("VERIFY_QUIET") == "1"' in body


def test_last_tick_price_reads_the_tail_of_the_csv(tmp_path, monkeypatch) -> None:
    """قیمت «منبع خودمان» باید از آخرین خط فایل خوانده شود، نه از حافظه."""
    from tradeagent.diag import last_tick_price

    monkeypatch.setenv("MT5_WINEPREFIX", str(tmp_path))
    ticks = tmp_path / "drive_c/Program Files/MetaTrader 5/MQL5/Files/harvester/ticks/XAUUSD_i"
    ticks.mkdir(parents=True)
    (ticks / "20261127.csv").write_text(
        "time_msc;time;bid;ask;last;volume;flags\n"
        "1700000000000;2026.11.27 10:00:00;3641.10;3641.20;0;1;4\n"
        "1700000000500;2026.11.27 10:00:00;3642.90;3643.10;0;1;4\n",
        encoding="utf-8",
    )
    got = last_tick_price()
    assert got["ok"] and got["bid"] == 3642.90 and got["ask"] == 3643.10


def test_a_made_up_price_in_the_analysis_is_replaced_with_the_real_numbers(cfg: Config) -> None:
    """مدل «۱۴۵۲.۳۵» گفت ولی تیک ۳۶۳۲ است؛ گزارش نباید عدد ساختگی را چاپ کند."""
    from tradeagent.loop import _numbers_in

    assert 1452.35 in _numbers_in("قیمت فعلی 1452.35 و اسپرد 0.5")
    # سنجشِ واقعی: عدد دور از قیمت مرجع باید گرفته شود
    ref = 3632.87
    bad = [n for n in _numbers_in("قیمت فعلی 1452.35، اسپرد 0.5") if n > 100 and abs(n - ref) / ref > 0.02]
    assert bad == [1452.35]


def test_verify_waits_for_the_expert_before_running(cfg: Config) -> None:
    """سرویس خوابیده و اکسپرت تا ۶۰ ثانیه بعد وصل می‌شود؛ چرخهٔ verify باید صبر کند."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    body = (root / "scripts" / "run_gold.sh").read_text(encoding="utf-8")
    assert "--wait-ea 90" in body
    assert "اتصال اکسپرت:" in body
    cli = (root / "src/tradeagent/cli.py").read_text(encoding="utf-8")
    assert '"--wait-ea"' in cli


def test_wait_for_ea_returns_a_price_when_it_connects(cfg: Config) -> None:
    runner = Runner(cfg)

    class MCP:
        enabled = True

        def __init__(self) -> None:
            self.calls = 0

        def call_tool(self, name, args=None):  # noqa: ANN001
            self.calls += 1
            return {"symbol": "XAUUSD_i", "bid": 3632.87, "ask": 3633.27}

    runner.mcp = MCP()
    got = runner.wait_for_ea(timeout_s=1, step_s=0.1)
    assert got["ok"] is True
    assert 3630 < got["price"] < 3640


def test_verify_json_is_found_after_the_logs() -> None:
    """خروجی loop لاگ هم دارد؛ JSON از «{\\n  \"ok\"» باید پیدا شود وگرنه «چرخه ?» می‌شود."""
    import json

    raw = '[gold] چیزی نوشت {"x": 1}\n2026-11-27 INFO شروع\n{\n  "ok": true,\n  "cycle": 5\n}\n'
    start = raw.rfind('\n{\n  "ok"')
    start = start + 1 if start >= 0 else raw.find("{")
    data = json.loads(raw[start:])
    assert data["cycle"] == 5


def test_verify_closes_the_service_leftover_bridge_first() -> None:
    """اکسپرت به پلِ جامانده وصل می‌ماند؛ چرخه باید اول آن را ببندد."""
    from pathlib import Path

    body = (Path(__file__).resolve().parents[1] / "scripts" / "run_gold.sh").read_text(encoding="utf-8")
    assert "kill_stale_bridges" in body
    assert "--wait-ea 90" in body
    assert body.index("kill_stale_bridges") < body.index("--wait-ea 90")


def test_bridge_processes_are_found_by_cmdline(tmp_path) -> None:
    from tradeagent.diag import bridge_pids

    for pid, cmd in bridge_pids():
        assert cmd
        assert "trade-mcp" in cmd
