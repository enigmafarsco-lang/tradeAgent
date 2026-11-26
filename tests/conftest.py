"""ابزار تست مشترک: پیکربندی موقت با داده‌ی ساختگی و انتشار غیرفعال."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from tradeagent.config import Config  # noqa: E402
from tradeagent.ticks import Bar, SyntheticSource  # noqa: E402


@pytest.fixture()
def cfg(tmp_path: Path) -> Config:
    cfg = Config.load(REPO_ROOT / "configs" / "default.toml", root=tmp_path)
    cfg.set("data.source", "synthetic")
    cfg.set("data.state_dir", str(tmp_path / "var"))
    cfg.set("data.symbols", ["BTCUSD", "ETHUSD"])
    cfg.set("data.warmup_bars", 400)
    cfg.set("data.max_bars_per_cycle", 3600)
    cfg.set("paper.sqlite3", str(tmp_path / "var" / "paper.sqlite3"))
    cfg.set("paper.starting_balance", 10000.0)
    cfg.set("paper.max_open_positions", 3)
    cfg.set("publish.enabled", False)
    cfg.set("llm.enabled", False)
    return cfg


@pytest.fixture()
def source() -> SyntheticSource:
    return SyntheticSource(["BTCUSD", "ETHUSD"], vol_bps_per_hour=45.0, seed=11)


def make_bars(prices: list[float], start: int = 1_700_000_000, spread_bps: float = 1.0) -> list[Bar]:
    bars: list[Bar] = []
    for i, price in enumerate(prices):
        half = price * spread_bps / 2 / 1e4
        prev = prices[i - 1] if i else price
        bars.append(
            Bar(
                t=start + i,
                open=prev,
                high=max(prev, price) * (1 + 0.00002),
                low=min(prev, price) * (1 - 0.00002),
                close=price,
                bid_close=price - half,
                ask_close=price + half,
                spread_bps_max=spread_bps,
                ticks=3,
                volume=0.5,
            )
        )
    return bars
