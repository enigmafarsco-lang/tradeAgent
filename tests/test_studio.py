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


def test_a_broken_model_falls_back_to_the_rule(tmp_path) -> None:
    logs = make_logs(tmp_path)

    def chat(messages):  # noqa: ANN001
        raise RuntimeError("HTTP 503: Service Unavailable")

    studio = Studio(logs, "XAUUSD_i", tmp_path / "st", chat=chat, step_min=1, horizon=3)
    result = studio.walk(limit=6)
    assert result["ok"] and result["failures"] == 6
    assert "503" in result["last_error"]
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
