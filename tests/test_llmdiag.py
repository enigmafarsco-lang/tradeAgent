"""عیب‌یابی مدل: باید بگوید چرا ۵۰۳ می‌آید، نه اینکه فقط ۵۰۳ را تکرار کند."""

from __future__ import annotations

import tradeagent.llmdiag as diag


def test_report_lists_every_probe_with_a_mark() -> None:
    probes = [diag.Probe("نسخهٔ ollama", True, "0.12.3"),
              diag.Probe("تولید متن (api/generate)", False, "HTTP 503: ")]
    text = diag.format_report(probes, "مدل به حافظه نمی‌رسد")
    assert "✅" in text and "❌" in text and "503" in text
    assert "نتیجه: مدل به حافظه نمی‌رسد" in text


def test_verdict_points_at_memory_when_that_is_the_reason() -> None:
    probes = [diag.Probe("نسخهٔ ollama", True, "ok"),
              diag.Probe("تولید متن (api/generate)", False, "HTTP 503: model requires more system memory")]
    text = diag.verdict(probes, "qwen2.5:7b-instruct")
    assert "حافظه" in text and "1.5b" in text


def test_verdict_says_healthy_when_generation_works() -> None:
    probes = [diag.Probe("تولید متن (api/generate)", True, "سلام")]
    assert "سالم" in diag.verdict(probes, "qwen2.5:7b-instruct")


def test_studio_stops_calling_a_dead_model_after_three_errors(tmp_path) -> None:
    import json
    from pathlib import Path

    from tradeagent.studio import Studio

    d = tmp_path / "logs"
    d.mkdir()
    base = 1_760_000_000_000
    lines = []
    price = 3600.0
    for sec in range(200 * 60):
        price += ((sec % 37) - 18) * 0.004
        lines.append(json.dumps({"ts": (base + sec * 1000) // 1000, "bid": price - 0.1, "ask": price + 0.1}))
    (d / "s1_ticks_XAUUSD_i_20261127.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    calls = {"n": 0}

    def chat(messages):  # noqa: ANN001
        calls["n"] += 1
        raise RuntimeError("HTTP 503: Service Unavailable")

    studio = Studio(d, "XAUUSD_i", tmp_path / "st", chat=chat, flat_bp=0.0)
    studio.walk(limit=40)
    assert calls["n"] == 3                    # مدارشکن بعد از سه خطا
    assert studio.model_down is True
    assert all(p.source == "rule" for p in studio.preds)
