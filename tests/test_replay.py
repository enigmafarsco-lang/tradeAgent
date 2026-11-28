from __future__ import annotations

import json
import math
from pathlib import Path

from tradeagent.replay import Replay, _score, model_predict, summarize, summarize_steps


def make_ticks(tmp_path: Path, seconds: int = 4000) -> Path:
    ticks = tmp_path / "ticks" / "XAUUSD_i"
    ticks.mkdir(parents=True)
    base = 1_700_000_000_000
    lines = ["time_msc;time;bid;ask;last;volume;flags"]
    for i in range(seconds):
        price = 3600 + math.sin(i / 60.0) * 2
        lines.append(f"{base + i * 1000};x;{price:.2f};{price + 0.4:.2f};0;1;4")
    (ticks / "20261127.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tmp_path


def test_ticks_summary_counts_the_days(tmp_path) -> None:
    root = make_ticks(tmp_path)
    info = summarize(root, "XAUUSD_i")
    assert info["files"] == 1 and info["ticks"] == 4000
    assert info["low"] < info["high"]
    assert info["days"][0]["file"] == "20261127.csv"


def test_replay_predicts_scores_and_draws(tmp_path) -> None:
    root = make_ticks(tmp_path)

    def predictor(bars, horizon):  # noqa: ANN001
        return {"direction": "up", "phase": "steady", "strength": 0.5, "confidence": 0.6,
                "horizon_s": horizon, "expected_bp": 1.0, "analysis": "ساختگی"}

    run = Replay(root, "XAUUSD_i", predictor, step_s=300, horizon_s=300, warmup_s=600,
                 out_dir=root / "out")
    stats = run.run()
    assert stats["ok"] and stats["steps"] >= 5 and stats["scored"] >= 4
    html = (root / "out" / "index.html").read_text(encoding="utf-8")
    assert "<svg" in html and "درست" in html
    rows = [json.loads(x) for x in (root / "out" / "predictions.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows and "actual_bp" in rows[0]


def test_score_counts_flat_inside_the_band() -> None:
    assert _score("up", 5.0) == 1
    assert _score("up", -5.0) == 0
    assert _score("down", -5.0) == 1
    assert _score("flat", 1.0) == 1
    assert _score("flat", 9.0) == 0


def test_summary_subtracts_the_cost() -> None:
    class S:
        def __init__(self, d, bp):
            self.pred = {"direction": d}
            self.actual_bp = bp
            self.hit = _score(d, bp)

    stats = summarize_steps([S("up", 5.0), S("down", -4.0), S("flat", 0.2)], cost_bp=1.0, horizon_s=300)
    assert stats["scored"] == 3
    assert stats["avg_net_bp"] == round(((5.0 - 1.0) + (4.0 - 1.0) + 0.0) / 3, 2)


def test_model_predict_parses_a_reply() -> None:
    def chat(messages):  # noqa: ANN001
        return json.dumps({"analysis": "قیمت بالا می‌رود", "momentum": {
            "direction": "up", "phase": "steady", "strength": 0.6, "confidence": 0.7,
            "expected_bp": 2.5, "reason": "شتاب"}})

    from tradeagent.ticks import Bar

    bars = [Bar(t=1_700_000_000 + i, open=3600, high=3601, low=3599, close=3600 + i * 0.01,
                bid_close=3599.9, ask_close=3600.1, spread_bps_max=1.0, ticks=5, volume=1.0)
            for i in range(200)]
    pred = model_predict(chat, bars, 300)
    assert pred["direction"] == "up" and pred["expected_bp"] == 2.5
    assert pred["analysis"].startswith("قیمت")


def test_a_dead_model_does_not_kill_the_replay(tmp_path) -> None:
    """ollama 503 داد؛ بازپخش باید ادامه بدهد و تعداد شکست را گزارش کند، نه traceback."""
    root = make_ticks(tmp_path)
    calls = {"n": 0}

    def predictor(bars, horizon):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RuntimeError("HTTP Error 503: Service Unavailable")
        return {"direction": "up", "phase": "steady", "strength": 0.5, "confidence": 0.5,
                "horizon_s": horizon, "expected_bp": 0.0, "analysis": "بعد از خطا"}

    run = Replay(root, "XAUUSD_i", predictor, step_s=300, horizon_s=300, warmup_s=600,
                 out_dir=tmp_path / "out")
    stats = run.run()
    assert stats["ok"] is True
    assert stats["failures"] == 2
    assert "503" in stats["last_error"]
    assert stats["steps"] >= 1


def test_http_error_keeps_the_server_message() -> None:
    """503 بی‌توضیح بی‌فایده است؛ ollama دلیلش را در بدنه می‌فرستد."""
    import urllib.error

    from tradeagent.llm import http_post_json

    class FakeError(urllib.error.HTTPError):
        def __init__(self) -> None:  # noqa: D401
            super().__init__("u", 503, "Service Unavailable", {}, None)

        def read(self):  # noqa: ANN201
            return b'{"error":"model requires more system memory"}'

    import tradeagent.llm as llm_mod

    original = llm_mod.urllib.request.urlopen

    def boom(*a, **k):  # noqa: ANN002, ANN003
        raise FakeError()

    llm_mod.urllib.request.urlopen = boom
    try:
        try:
            http_post_json("http://x/api/chat", {}, {}, 1.0)
        except RuntimeError as exc:
            assert "503" in str(exc) and "system memory" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("باید خطا بدهد")
    finally:
        llm_mod.urllib.request.urlopen = original
