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


def test_candidate_urls_cover_all_local_addresses() -> None:
    urls = diag.candidate_urls("http://127.0.0.1:11434")
    assert urls[0] == "http://127.0.0.1:11434"
    assert "http://localhost:11434" in urls
    assert "http://[::1]:11434" in urls
    assert len(urls) == len(set(urls))


def test_best_url_picks_the_host_that_answers() -> None:
    probes = [
        diag.Probe("http://127.0.0.1:11434", False, "HTTP 503"),
        diag.Probe("http://[::1]:11434", True, "HTTP 200"),
    ]
    assert diag.best_url(probes) == "http://[::1]:11434"
    assert "::1" in diag.verdict(probes, "qwen2.5:7b-instruct")


def test_verdict_notes_cpu_only_models() -> None:
    probes = [
        diag.Probe("http://127.0.0.1:11434", True, "HTTP 200"),
        diag.Probe("مدل‌های بارگذاری‌شده (ollama ps)", True,
                   "NAME ID SIZE PROCESSOR CONTEXT RUNNER UNTIL qwen 845 5.3GB 100% CPU 8192 llamacpp"),
    ]
    assert "CPU" in diag.verdict(probes, "qwen2.5:7b-instruct")


def test_gpu_verdict_when_the_card_is_invisible() -> None:
    probes = [diag.Probe("کارت NVIDIA (nvidia-smi)", False, "«nvidia-smi» نصب نیست")]
    text = diag._gpu_verdict(probes)
    assert "درایور" in text and "100% GPU" in text


def test_gpu_verdict_when_vram_is_too_small() -> None:
    probes = [
        diag.Probe("کارت NVIDIA (nvidia-smi)", True, "RTX 3050, 4096 MiB, 3800 MiB, 550.1"),
        diag.Probe("VRAM آزاد", False, "0.3 GB آزاد از 4.0 GB"),
        diag.Probe("ollama از GPU استفاده کرد", False, "no compatible GPUs"),
    ]
    text = diag._gpu_verdict(probes)
    assert "VRAM" in text and "1.5b" in text


def test_gpu_verdict_when_everything_is_fine() -> None:
    probes = [
        diag.Probe("کارت NVIDIA (nvidia-smi)", True, "RTX 4070, 12288 MiB, 1000 MiB, 550.1"),
        diag.Probe("VRAM آزاد", True, "11 GB آزاد"),
        diag.Probe("ollama از GPU استفاده کرد", True, "library=cuda"),
    ]
    assert diag._gpu_verdict(probes) == ""
