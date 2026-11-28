"""آزمایشگاه مدل: تیک → کندل M1 → ویژگی → سنجش راه‌رونده."""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

from tradeagent.lab import (
    FEATURE_NAMES,
    build_samples,
    features_at,
    folds_of,
    m1_candles_from_ticks,
    run,
    train_softmax,
)


def make_tick_dir(tmp_path: Path, minutes: int = 240, per_second: int = 3) -> Path:
    root = tmp_path / "harvester"
    sym = root / "ticks" / "XAUUSD_i"
    sym.mkdir(parents=True, exist_ok=True)
    rnd = random.Random(11)
    price = 3600.0
    base = 1_760_000_000_000
    lines = ["time_msc;time;bid;ask;last;volume;flags"]
    for sec in range(minutes * 60):
        price += math.sin(sec / 300.0) * 0.02 + rnd.gauss(0, 0.05)
        for k in range(per_second):
            p = price + rnd.gauss(0, 0.01)
            lines.append(f"{base + sec * 1000 + k * 300};x;{p - 0.1:.2f};{p + 0.1:.2f};0;1;4")
    (sym / "20261127.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (sym / "20261128.csv").write_text("time_msc;time;bid;ask;last;volume;flags\n", encoding="utf-8")
    return root


def test_ticks_become_m1_candles() -> None:
    base_ms = (1_760_000_000 // 60) * 60 * 1000
    ticks = [(base_ms + i * 1000, 3600.0 + i * 0.01, 3600.2 + i * 0.01, 1.0) for i in range(180)]
    candles = m1_candles_from_ticks(ticks)
    assert len(candles) == 3
    assert all(c.ticks == 60 for c in candles)
    assert abs(candles[1].open - candles[0].close) < 0.05   # پیوستگی کندل‌ها


def test_features_only_look_backwards(tmp_path) -> None:
    root = make_tick_dir(tmp_path, minutes=130)
    from tradeagent.lab import load_candles

    candles = load_candles(root, "XAUUSD_i")
    assert len(candles) >= 110
    assert features_at(candles, 5) is None          # تاریخچه کم است
    feats = features_at(candles, 70)
    assert feats is not None and set(feats) == set(FEATURE_NAMES)

    # تغییر آینده نباید ویژگی گذشته را عوض کند
    before = features_at(candles, 70)
    candles[100].close *= 1.01
    assert features_at(candles, 70) == before


def test_labels_follow_the_forward_move(tmp_path) -> None:
    root = make_tick_dir(tmp_path, minutes=90)
    from tradeagent.lab import load_candles

    candles = load_candles(root, "XAUUSD_i")
    samples = build_samples(candles, horizon=3, flat_bp=0.0001)   # آستانهٔ ریز: خنثی تقریباً هیچ
    assert samples and all(s.y in (0, 2) for s in samples)
    big = build_samples(candles, horizon=3, flat_bp=1e9)          # آستانهٔ بزرگ: همه خنثی
    assert all(s.y == 1 for s in big)


def test_softmax_learns_a_separable_signal() -> None:
    rows, labels = [], []
    rnd = random.Random(2)
    for _ in range(300):
        v = rnd.gauss(0, 1)
        rows.append([v, rnd.gauss(0, 0.1), 0.0])
        labels.append(0 if v > 0 else 2)
    model = train_softmax(rows, labels, epochs=200)
    from tradeagent.lab import predict_proba

    correct = sum(
        1 for x, y in zip(rows, labels) if max(range(3), key=lambda c: predict_proba(model, x)[c]) == y
    )
    assert correct / len(rows) > 0.9


def test_walk_forward_folds_never_train_on_the_future() -> None:
    folds = folds_of(500, n_folds=5, embargo=3)
    assert folds and folds[0].train_n < folds[0].test_start
    for a, b in zip(folds, folds[1:]):
        assert a.test_end == b.test_start


def test_lab_end_to_end_writes_the_dashboard(tmp_path) -> None:
    root = make_tick_dir(tmp_path, minutes=420)
    data = run(root, "XAUUSD_i", horizon=3, out_dir=tmp_path / "lab", n_folds=3, epochs=60)
    assert data["ok"] is True, data.get("error")
    assert data["candles_n"] > 200 and data["samples"] > 200
    names = {r["name"] for r in data["summary"]}
    assert {"همیشه بالا", "قاعدهٔ تکانه", "رگرسیون لجستیک"} <= names
    html_text = (tmp_path / "lab" / "index.html").read_text(encoding="utf-8")
    assert "مقایسهٔ مدل‌ها" in html_text and "<svg" in html_text
    saved = json.loads((tmp_path / "lab" / "results.json").read_text(encoding="utf-8"))
    assert saved["symbol"] == "XAUUSD_i" and saved["horizon"] == 3
