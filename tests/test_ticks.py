from __future__ import annotations

from pathlib import Path

import pytest

from conftest import make_bars
from tradeagent.ticks import (Bar, HarvesterSource, SyntheticSource, aggregate_ticks,
                             parse_tick_line, read_new_lines)


def test_parse_tick_line_valid_and_bad() -> None:
    tick = parse_tick_line("-1;0;0;0;0;0;0")
    assert tick is None
    tick = parse_tick_line("1700000000123;2026.11.26 10:00:00;100.1;100.3;100.2;0.5;6")
    assert tick is not None
    assert tick.time_msc == 1700000000123
    assert tick.mid == pytest.approx(100.2)
    assert 19 < tick.spread_bps < 21
    assert parse_tick_line("time_msc;time;bid;ask;last;volume;flags") is None
    assert parse_tick_line("") is None
    assert parse_tick_line("garbage") is None


def test_aggregate_ticks_groups_per_second() -> None:
    lines = [
        "1700000000100;t;100.0;100.2;0;0.1;1",
        "1700000000500;t;100.2;100.4;0;0.2;1",
        "1700000000900;t;100.1;100.3;0;0.3;1",
        "1700000001200;t;100.5;100.7;0;0.4;1",
    ]
    ticks = [parse_tick_line(line) for line in lines]
    bars = aggregate_ticks([t for t in ticks if t])
    assert len(bars) == 2
    assert bars[0].ticks == 3
    assert bars[0].open == pytest.approx(100.1)
    assert bars[0].close == pytest.approx(100.2)
    assert bars[0].high >= bars[0].low
    assert bars[1].t == 1700000001


def test_read_new_lines_keeps_partial_line(tmp_path: Path) -> None:
    path = tmp_path / "t.csv"
    path.write_text("a;1\nb;2\npartial", encoding="utf-8")
    lines, offset = read_new_lines(path, 0)
    assert lines == ["a;1", "b;2"]
    assert offset == len("a;1\nb;2\n")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(";3\n")
    lines2, offset2 = read_new_lines(path, offset)
    assert lines2 == ["partial;3"]
    assert offset2 == path.stat().st_size
    lines3, _ = read_new_lines(path, offset2)
    assert lines3 == []


def _write_ticks(root: Path, symbol: str, day: str, rows: list[str]) -> Path:
    folder = root / "ticks" / symbol
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{day}.csv"
    mode = "a" if path.exists() else "w"
    with open(path, mode, encoding="utf-8") as fh:
        if mode == "w":
            fh.write("time_msc;time;bid;ask;last;volume;flags\n")
        fh.write("\n".join(rows) + "\n")
    return path


def test_harvester_source_incremental(tmp_path: Path) -> None:
    root = tmp_path / "harvester"
    root.mkdir()
    state = tmp_path / "state.json"
    (root / "symbols.json").write_text('{"symbols":[{"symbol":"BTCUSD"}]}', encoding="utf-8")
    _write_ticks(root, "BTCUSD", "20261126", [
        "1700000000100;t;100.0;100.2;0;0.1;1",
        "1700000000600;t;100.1;100.3;0;0.1;1",
    ])
    src = HarvesterSource(root, state_path=state, stale_after_s=60)
    first = src.fetch(now_s=1700000007)
    assert first.bars["BTCUSD"][0].ticks == 2
    assert first.per_symbol["BTCUSD"].new_ticks == 2
    assert first.per_symbol["BTCUSD"].stale is False

    second = src.fetch(now_s=1700000008)
    assert second.per_symbol["BTCUSD"].new_ticks == 0
    assert second.bars["BTCUSD"] == []

    _write_ticks(root, "BTCUSD", "20261126", ["1700000009100;t;100.5;100.7;0;0.2;1"])
    third = src.fetch(now_s=1700000010)
    assert third.per_symbol["BTCUSD"].new_ticks == 1
    assert len(third.bars["BTCUSD"]) == 1
    assert third.bars["BTCUSD"][0].t == 1700000009


def test_harvester_source_missing_root_is_safe(tmp_path: Path) -> None:
    src = HarvesterSource(tmp_path / "nope", state_path=tmp_path / "s.json")
    result = src.fetch()
    assert result.bars == {}
    assert result.total_new_bars == 0


def test_synthetic_source_is_deterministic() -> None:
    a = SyntheticSource(["BTCUSD"], seed=3)
    b = SyntheticSource(["BTCUSD"], seed=3)
    end = 1_700_003_600
    bars_a = a.fetch(end_s=end).bars["BTCUSD"]
    bars_b = b.fetch(end_s=end).bars["BTCUSD"]
    assert bars_a and [x.close for x in bars_a] == [x.close for x in bars_b]
    # بدون تیک تکراری و با زمان صعودی
    times = [bar.t for bar in bars_a]
    assert times == sorted(times)
    assert len(set(times)) == len(times)


def test_bar_helpers() -> None:
    bar = Bar(t=1, open=100, high=101, low=99, close=100, bid_close=99.9, ask_close=100.1, spread_bps_max=20)
    assert bar.spread_bps > 0
    assert bar.range_bps > 0
    assert len(bar.as_tuple()) == 10


def test_make_bars_helper() -> None:
    bars = make_bars([100, 101, 102])
    assert bars[0].open == 100
    assert bars[-1].close == 102
    assert bars[1].open == 100
