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
