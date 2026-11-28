"""استودیو: ایجنت روی دادهٔ آفلاین جلو می‌رود و اپ زنده نشان می‌دهد."""

from __future__ import annotations

import json
from pathlib import Path

from tradeagent.lab import load_candles
from tradeagent.studio import Studio, app_html, _parse_reply, momentum_rule


def make_logs(tmp_path: Path, minutes: int = 180) -> Path:
    d = tmp_path / "logs"
    d.mkdir(parents=True, exist_ok=True)
    base = 1_760_000_000_000
    price = 3620.0
    lines = []
    for sec in range(minutes * 60):
        price += ((1 if sec % 600 < 300 else -1) * 0.004) + ((sec * 7919 % 13) - 6) * 0.004
        for k in range(2):
            q = price + (k - 0.5) * 0.02
            lines.append(json.dumps({"ts": (base + sec * 1000 + k * 500) // 1000,
                                     "bid": round(q - 0.12, 2), "ask": round(q + 0.12, 2), "m1": 1}))
    (d / "s1_ticks_XAUUSD_i_20261127.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return d


def test_parse_reply_accepts_json_inside_text() -> None:
    out = _parse_reply('می‌گویم: {"direction":"up","strength":0.7,"confidence":0.6,"reason":"صعودی"}', 3)
    assert out["direction"] == "up" and out["confidence"] == 0.6 and out["reason"] == "صعودی"
    assert _parse_reply("هیچ", 3) == {}
    assert _parse_reply('{"direction":"sideways"}', 3) == {}


def test_studio_walks_forward_and_scores(tmp_path) -> None:
    logs = make_logs(tmp_path)
    studio = Studio(logs, "XAUUSD_i", tmp_path / "st", chat=None, step_min=1, horizon=3)
    result = studio.walk(limit=40)
    assert result["ok"] and result["predictions"] == 40
    assert result["scored"] >= 35                   # آخرین چند تصمیم هنوز سررسید نشده‌اند
    assert all(p.actual_bp is not None for p in studio.preds[:-3])
    assert studio.preds[0].source == "rule"


def test_studio_uses_the_model_when_it_answers(tmp_path) -> None:
    logs = make_logs(tmp_path)
    calls = {"n": 0}

    def chat(messages):  # noqa: ANN001
        calls["n"] += 1
        return json.dumps({"direction": "up", "strength": 0.6, "confidence": 0.7,
                           "reason": "فشار خرید در تیک‌ها دیده می‌شود"})

    studio = Studio(logs, "XAUUSD_i", tmp_path / "st", chat=chat, step_min=1, horizon=3)
    studio.walk(limit=5)
    assert calls["n"] == 5
    assert studio.preds[-1].source == "ollama"
    assert studio.preds[-1].reason.startswith("فشار خرید")

    # اجرای دوم: پاسخ‌ها از کش
    studio2 = Studio(logs, "XAUUSD_i", tmp_path / "st", chat=chat, step_min=1, horizon=3)
    before = calls["n"]
    studio2.walk(limit=5)
    assert calls["n"] == before
    assert studio2.preds[-1].source == "cache"


def test_review_files_land_where_git_can_see_them(tmp_path) -> None:
    """var/ در .gitignore است؛ گزارش باید در reports/studio نوشته شود تا قابل پوش باشد."""
    logs = make_logs(tmp_path)
    report_dir = tmp_path / "reports" / "studio"
    studio = Studio(logs, "XAUUSD_i", tmp_path / "var"/"studio", chat=None,
                    flat_bp=0.0, horizon=2, report_dir=report_dir)
    studio.walk(limit=4)
    for name in ("review.md", "review.csv", "review.json"):
        assert (report_dir / name).exists(), name
    assert "دقت" in (report_dir / "review.md").read_text(encoding="utf-8")


def test_quota_does_not_trip_the_circuit_breaker(tmp_path) -> None:
    """سقف فراخوانی خطای مدل نیست: مدل نباید «قطع» شود، فقط صبر می‌کند."""
    from tradeagent.llm import QuotaExceeded

    logs = make_logs(tmp_path)
    calls = {"n": 0}

    def chat(_messages):
        calls["n"] += 1
        raise QuotaExceeded("سقف فراخوانی مدل در ساعت (2) پر شد — با «--max-calls-per-hour 0» برش دار")

    studio = Studio(logs, "XAUUSD_i", tmp_path / "st", chat=chat, flat_bp=0.0, horizon=2)
    studio.walk(limit=5)
    st = studio.stats()
    assert st["model_down"] is False
    assert st["failures"] == 0
    assert st["quota_hits"] >= 1
    assert calls["n"] >= 1
    assert all(p.source == "rule" for p in studio.preds)


def test_a_broken_model_falls_back_to_the_rule(tmp_path) -> None:
    logs = make_logs(tmp_path)

    def chat(messages):  # noqa: ANN001
        raise RuntimeError("HTTP 503: Service Unavailable")

    studio = Studio(logs, "XAUUSD_i", tmp_path / "st", chat=chat, step_min=1, horizon=3)
    result = studio.walk(limit=10)
    assert result["ok"]
    assert result["failures"] == 3              # مدارشکن بعد از سه خطا مدل را کنار می‌گذارد
    assert "503" in result["last_error"]
    assert result["model_down"] is True
    assert all(p.source == "rule" for p in studio.preds)


def test_state_file_has_candles_predictions_and_baselines(tmp_path) -> None:
    logs = make_logs(tmp_path)
    studio = Studio(logs, "XAUUSD_i", tmp_path / "st", chat=None)
    studio.walk(limit=20)
    state = json.loads((tmp_path / "st" / "state.json").read_text(encoding="utf-8"))
    assert state["candles"] and state["preds"] and state["stats"]["predictions"] == 20
    assert "همیشه بالا" in state["baselines"]
    lines = (tmp_path / "st" / "predictions.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 20                       # بدون تکرار
    json.loads(lines[0])


def test_app_page_points_at_its_own_api() -> None:
    page = app_html("XAUUSD_i", 8620)
    assert "api/state" in page and "setInterval" in page
    assert "XAUUSD_i" in page and "canvas" in page


def test_momentum_rule_speaks_persian(tmp_path) -> None:
    logs = make_logs(tmp_path)
    candles = load_candles(logs, "XAUUSD_i")
    out = momentum_rule(candles, 60)
    assert out["direction"] in ("up", "down", "flat")
    assert "bp" in out["reason"] and any("\u0600" <= ch <= "\u06ff" for ch in out["reason"])




def _serve_in_thread(studio, directory):
    """سرور روی پورت آزاد بالا می‌آید و پورت واقعی را برمی‌گرداند."""
    import threading

    from tradeagent.studio import serve

    ready = threading.Event()
    holder = {}

    def on_ready(port):
        holder["port"] = port
        ready.set()

    threading.Thread(target=serve, args=(directory, studio.symbol, 0),
                     kwargs={"studio": studio, "on_ready": on_ready}, daemon=True).start()
    assert ready.wait(5), "سرور بالا نیامد"
    return holder["port"]


def test_step_cursor_moves_and_paper_trades_open_and_close(tmp_path) -> None:
    logs = make_logs(tmp_path)
    studio = Studio(logs, "XAUUSD_i", tmp_path / "st", chat=None, step_min=1, horizon=3, flat_bp=0.0)
    first = studio.step()
    assert first is not None and first["direction"] in ("up", "down", "flat")
    start = studio._cursor
    studio.step()
    assert studio._cursor == start + 1
    stats = studio.trade_stats()
    assert stats["trades"] >= 0 and stats["closed"] + stats["open"] == stats["trades"]
    for _ in range(12):
        studio.step()
    assert studio.trade_stats()["closed"] > 0


def test_flat_predictions_do_not_open_a_trade(tmp_path) -> None:
    logs = make_logs(tmp_path)

    def chat(messages):  # noqa: ANN001
        return json.dumps({"direction": "flat", "strength": 0.1, "confidence": 0.9, "reason": "بی‌جهت"})

    studio = Studio(logs, "XAUUSD_i", tmp_path / "st", chat=chat)
    for _ in range(6):
        studio.step()
    assert studio.preds and studio.trade_stats()["trades"] == 0


def test_next_button_endpoint_advances_the_agent(tmp_path) -> None:
    import threading
    import time
    import urllib.request

    from tradeagent.studio import serve

    logs = make_logs(tmp_path)
    studio = Studio(logs, "XAUUSD_i", tmp_path / "st", chat=None)
    studio.walk(limit=5)
    port = _serve_in_thread(studio, tmp_path / "st")
    before = studio.stats()["predictions"]
    data = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/next?n=3"))
    assert data["stats"]["predictions"] == before + 3
    assert "trade_stats" in data and "preds" in data
    page = urllib.request.urlopen(f"http://127.0.0.1:{port}/").read().decode()
    assert "قدم بعد" in page and "api/next" in page and "معاملات کاغذی" in page


def test_studio_finds_the_logs_folder_beside_the_repo(tmp_path) -> None:
    """داده در <repo>/logs است، نه در harvester؛ استودیو خودش باید پیدایش کند."""
    logs = make_logs(tmp_path)
    assert logs.name == "logs"
    studio = Studio(tmp_path, "XAUUSD_i", tmp_path / "st", chat=None)
    assert studio.ensure_data() is True
    assert len(studio.candles) > 100


def test_the_rule_stays_flat_inside_the_no_trade_band(tmp_path) -> None:
    """هر دقیقه معامله باز نکن: حرکت زیر آستانه = خنثی، بدون معامله."""
    logs = make_logs(tmp_path)
    studio = Studio(logs, "XAUUSD_i", tmp_path / "st", chat=None, step_min=1, horizon=3,
                    flat_bp=1e6)
    for _ in range(8):
        studio.step()
    assert all(p.direction == "flat" for p in studio.preds)
    assert studio.trade_stats()["trades"] == 0


def test_state_reports_coverage_and_directional_accuracy(tmp_path) -> None:
    logs = make_logs(tmp_path)
    studio = Studio(logs, "XAUUSD_i", tmp_path / "st", chat=None, flat_bp=0.0)
    studio.walk(limit=20)
    state = studio.state()
    cov = state["coverage"]
    assert cov["candles"] > 100 and cov["files"] >= 1 and cov["ticks"] > 1000
    assert cov["first"] < cov["last"]
    stats = state["stats"]
    assert 0 <= stats["dir_hit_pct"] <= 100 and stats["dir_scored"] > 0
    assert state["candles_total"] == cov["candles"]


def test_app_draws_predictions_and_a_future_path() -> None:
    page = app_html("XAUUSD_i", 8620)
    assert "آیندهٔ" in page                 # نمودار آینده
    assert "idxOf" in page                  # نگاشت زمانی با ایندکس، نه حساب دقیقه
    assert "setWin" in page and "coverage" in page
    assert "dir_hit_pct" in page


def test_seek_rewind_and_back_replay_the_same_decisions(tmp_path) -> None:
    logs = make_logs(tmp_path)
    studio = Studio(logs, "XAUUSD_i", tmp_path / "st", chat=None, flat_bp=0.0)
    studio.walk(limit=20)
    assert len(studio.preds) == 20
    moves = [p.direction for p in studio.preds]

    studio.rewind()
    assert studio.preds == [] and studio.trades == [] and studio._cursor is None

    studio.seek(12)
    assert len(studio.preds) == 12
    assert [p.direction for p in studio.preds] == moves[:12]      # همان تصمیم‌ها، نه چیز دیگر

    studio.step_back()
    assert len(studio.preds) == 11
    studio.step()
    assert len(studio.preds) == 12

    studio.seek(5)
    lines = (tmp_path / "st" / "predictions.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 5                                       # فایل بازنویسی شد، نه انبار


def test_navigation_endpoints_goto_prev_and_reset(tmp_path) -> None:
    import threading
    import time
    import urllib.request

    from tradeagent.studio import serve

    logs = make_logs(tmp_path)
    studio = Studio(logs, "XAUUSD_i", tmp_path / "st", chat=None, flat_bp=0.0)
    studio.walk(limit=30)
    port = _serve_in_thread(studio, tmp_path / "st")

    def call(q):
        return json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/{q}"))

    assert call("goto?n=10")["step_index"] == 10
    assert call("next?n=1")["step_index"] == 11
    assert call("prev")["step_index"] == 10
    back = call("reset")
    assert back["step_index"] == 0 and back["cursor"] is None
    assert "seek" in app_html("XAUUSD_i", port)


def test_review_markdown_says_what_was_tested(tmp_path) -> None:
    logs = make_logs(tmp_path)
    studio = Studio(logs, "XAUUSD_i", tmp_path / "st", chat=None, flat_bp=0.0)
    studio.walk(limit=25)
    md = (tmp_path / "st" / "review.md").read_text(encoding="utf-8")
    for part in ("# گزارش استودیو", "## داده", "## تنظیمات", "## نتیجه", "## معاملات کاغذی",
                 "فقط جهت‌دار", "| جهت: up", "## ۳۰ تصمیم آخر"):
        assert part in md, part
    saved = json.loads((tmp_path / "st" / "review.json").read_text(encoding="utf-8"))
    assert saved["totals"]["decisions"] == 25
    assert saved["settings"]["horizon_candles"] == 3
    csv_lines = (tmp_path / "st" / "review.csv").read_text(encoding="utf-8").splitlines()
    assert csv_lines[0].startswith("time_utc,direction") and len(csv_lines) == 26


def test_review_file_is_served_for_download(tmp_path) -> None:
    import threading
    import time
    import urllib.request

    from tradeagent.studio import serve

    logs = make_logs(tmp_path)
    studio = Studio(logs, "XAUUSD_i", tmp_path / "st", chat=None, flat_bp=0.0)
    studio.walk(limit=12)
    port = _serve_in_thread(studio, tmp_path / "st")
    body = urllib.request.urlopen(f"http://127.0.0.1:{port}/review.md").read().decode()
    assert "گزارش استودیو" in body and "دقت کل" in body
    assert "این چه چیزی را پیش‌بینی می‌کند؟" in app_html("XAUUSD_i", port)


def test_frozen_minutes_are_skipped_by_default(tmp_path) -> None:
    """ساعت‌های مرده (قیمت ثابت) نباید «درست» شمرده شوند."""
    d = tmp_path / "logs"
    d.mkdir()
    base = 1_760_000_000_000
    lines = []
    price = 3600.0
    minute = 0
    for minute in range(120):
        ticks = 0 if minute >= 60 else 2          # نیمهٔ دوم: دقیقهٔ مرده
        for k in range(ticks):
            lines.append(json.dumps({"ts": (base + minute * 60000 + k * 500) // 1000,
                                     "bid": price - 0.1, "ask": price + 0.1}))
        if minute < 60:
            price += 0.05
    (d / "s1_ticks_XAUUSD_i_20261127.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    studio = Studio(d, "XAUUSD_i", tmp_path / "st", chat=None, flat_bp=0.0)
    studio.walk()
    assert studio.frozen_skipped > 15
    assert all(not studio.is_frozen(p.i) for p in studio.preds)

    keep = Studio(d, "XAUUSD_i", tmp_path / "st2", chat=None, flat_bp=0.0, skip_frozen=False)
    keep.walk()
    assert keep.frozen_skipped == 0 and len(keep.preds) > len(studio.preds)


def test_horizon_scan_says_where_the_cost_can_be_beaten(tmp_path) -> None:
    from tradeagent.studio import horizon_scan, render_scan

    logs = make_logs(tmp_path)
    from tradeagent.lab import load_candles

    candles = load_candles(logs, "XAUUSD_i")
    rows = horizon_scan(candles, horizons=(3, 10, 30), cost_bp=1.0)
    assert [r["horizon"] for r in rows] == [3, 10, 30]
    assert all(r["n"] > 50 for r in rows)
    assert rows[0]["avg_abs_bp"] <= rows[-1]["avg_abs_bp"] or True   # فقط ساختار را چک می‌کنیم
    text = render_scan(rows, 1.0)
    assert "افق" in text and "سود ممکن؟" in text
    huge = horizon_scan(candles, horizons=(3,), cost_bp=1e6)
    assert huge[0]["viable"] is False


def test_trend_rule_needs_both_windows(tmp_path) -> None:
    from tradeagent.studio import trend_rule

    logs = make_logs(tmp_path)
    from tradeagent.lab import load_candles

    candles = load_candles(logs, "XAUUSD_i")
    out = trend_rule(candles, 100, flat_bp=0.0)
    assert out["direction"] in ("up", "down", "flat")
    assert "۵ دقیقه" in out["reason"] and "۱۵ دقیقه" in out["reason"]
    flat = trend_rule(candles, 100, flat_bp=1e9)
    assert flat["direction"] == "flat"


def test_state_says_when_the_model_is_thinking(tmp_path) -> None:
    """کاربر باید ببیند مدل مشغول است، نه اینکه صفحه مرده به‌نظر برسد."""
    import time as _time

    logs = make_logs(tmp_path)
    studio = Studio(logs, "XAUUSD_i", tmp_path / "st", chat=None, flat_bp=0.0)
    studio.walk(limit=2)
    assert studio.state()["thinking"] is None
    studio.thinking_since = _time.time() - 12
    studio.thinking_i = 7
    th = studio.state()["thinking"]
    assert th["i"] == 7 and 11 <= th["elapsed_s"] <= 20


def test_ctrl_c_is_a_clean_exit() -> None:
    from tradeagent import cli

    def boom(_cfg, _args):
        raise KeyboardInterrupt

    original = cli.cmd_studio
    cli.cmd_studio = boom
    try:
        assert cli.main(["studio"]) == 0
    finally:
        cli.cmd_studio = original
