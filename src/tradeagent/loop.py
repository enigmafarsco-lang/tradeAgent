"""ارکستراتور: یک چرخه‌ی کامل ۵ دقیقه‌ای و حلقه‌ی بی‌پایان.

مستقل از منبع داده: می‌تواند از پوشه‌ی اکسپرت متاتریدر بخواند یا از داده‌ی ساختگی.
"""

from __future__ import annotations

import json
import signal
import time
from pathlib import Path
from typing import Any

from .config import Config
from .features import compute_features
from .llm import LLMAdvisor
from .paper import CycleResult, PaperEngine, Store
from .policy import (AdaptiveController, Decision, baseline_params, describe, new_policy,
                     sanitize_for_log, validate_proposal)
from .publish import Publisher, prune_jsonl
from .report import (jsonl_line, render_compact, render_cycle_json, render_daily, render_markdown,
                     render_readme, render_state)
from .ticks import HarvesterSource, SyntheticSource
from .util import LOG, LockFile, ensure_dir, utc_day, utc_iso, utc_stamp, write_json

DEFAULT_SYMBOLS = ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "EURUSD", "GBPUSD", "USDJPY", "AUDUSD"]


class Runner:
    def __init__(self, cfg: Config, cfg_path: Path | None = None) -> None:
        self.cfg = cfg
        self.cfg_path = cfg_path
        self.state_dir = ensure_dir(cfg.state_dir)
        self.reports_dir = ensure_dir(self.state_dir / "reports")
        self.store = Store(cfg.path("paper.sqlite", "var/paper.sqlite3"))
        self.engine = PaperEngine(cfg, self.store)
        self.adaptive = AdaptiveController(
            baseline_variant=str(cfg.get("policy.baseline_variant", "momentum")),
            adapt_after_losses=int(cfg.get("policy.adapt_after_losses", 2)),
            cooldown_cycles=int(cfg.get("policy.adapt_cooldown_cycles", 2)),
            recover_after_wins=int(cfg.get("policy.recover_after_wins", 2)),
            max_level=int(cfg.get("policy.max_level", 5)),
        )
        self.advisor = LLMAdvisor(cfg, self.state_dir / "llm_calls.json")
        self.publisher = Publisher(cfg, cfg.root)
        self.local_cycles = self.reports_dir / "cycles.jsonl"
        self._running = True

    # -- منبع ----------------------------------------------------------
    def build_source(self) -> HarvesterSource | SyntheticSource:
        kind = str(self.cfg.get("data.source", "harvester")).lower()
        symbols = self.cfg.symbols
        if kind == "synthetic":
            return SyntheticSource(symbols or DEFAULT_SYMBOLS, vol_bps_per_hour=float(self.cfg.get("data.synthetic_vol_bps", 45.0)))
        return HarvesterSource(
            root=self.cfg.path("data.harvester_dir", "~/mt5/MQL5/Files/harvester"),
            symbols=symbols or None,
            state_path=self.state_dir / "ingest_state.json",
            stale_after_s=float(self.cfg.get("data.stale_after_s", 180)),
        )

    # -- سیاست ---------------------------------------------------------
    def current_policy(self) -> dict[str, Any]:
        stored = self.store.get_meta("policy")
        if isinstance(stored, dict) and stored.get("variant") and stored.get("params"):
            return stored
        variant = str(self.cfg.get("policy.baseline_variant", "momentum"))
        policy = new_policy(variant=variant, params=baseline_params(variant), source="baseline")
        self.store.set_meta("policy", policy)
        return policy

    def save_policy(self, policy: dict[str, Any]) -> None:
        self.store.set_meta("policy", policy)

    # -- زمینه برای مدل ------------------------------------------------
    def build_llm_context(self, result: CycleResult, policy: dict[str, Any]) -> dict[str, Any]:
        market: list[dict[str, Any]] = []
        for item in result.ingest[:10]:
            bars = self.store.recent_bars(item["symbol"], 400)
            if len(bars) < 320:
                continue
            f = compute_features(bars, len(bars) - 1)
            market.append(
                {
                    "symbol": item["symbol"],
                    "ret_60_bps": round(f.ret_60_bps, 2),
                    "ret_300_bps": round(f.ret_300_bps, 2),
                    "vol_60_bps": round(f.vol_60_bps, 2),
                    "vol_300_bps": round(f.vol_300_bps, 2),
                    "atr_bps": round(f.atr_bps, 2),
                    "z": round(f.z, 2),
                    "spread_bps": round(f.spread_bps, 2),
                    "ticks_per_s": round(f.tick_rate, 1),
                    "stale": bool(item.get("stale")),
                }
            )
        return {
            "cycle": result.cycle,
            "equity": result.equity,
            "balance": result.balance,
            "pnl_pct": round((result.equity - result.starting_balance) / result.starting_balance * 100.0, 3)
            if result.starting_balance
            else 0.0,
            "drawdown_pct": result.drawdown_pct,
            "streak": {"losses": result.extra.get("streak_losses", 0), "wins": result.extra.get("streak_wins", 0)},
            "overall_stats": result.overall_stats,
            "today_stats": result.today_stats,
            "per_symbol": result.per_symbol[:10],
            "current_policy": sanitize_for_log(policy),
            "recent_trades": [
                {k: t[k] for k in ("symbol", "side", "exit_reason", "pnl_bps", "r_multiple", "hold_s")}
                for t in result.extra.get("recent_trades", [])[:8]
            ],
            "market": market,
            "open_positions": len(result.open_positions),
            "data_warnings": result.warnings[:6],
            "instruction": "Only JSON. variant + params within 20%. Never change risk/limits.",
        }

    # -- یک چرخه -------------------------------------------------------
    def run_cycle(
        self,
        now_ts: int | None = None,
        force_llm: bool = False,
        publish: bool = True,
        dry_run_publish: bool = False,
    ) -> dict[str, Any]:
        now_ts = int(now_ts or time.time())
        cycle = self.store.next_cycle_index()
        policy_in = self.current_policy()
        prev_policy = json.loads(json.dumps(policy_in))

        source = self.build_source()
        if isinstance(source, SyntheticSource):
            since = {
                symbol: int(self.store.get_meta(f"processed:{symbol}", 0) or 0)
                for symbol in source.symbols
            }
            ingest = source.fetch(since_by_symbol=since, end_s=now_ts)
        else:
            ingest = source.fetch(now_s=now_ts)

        result = self.engine.process(ingest.bars, policy_in, cycle, now_ts)
        result.source = ingest.source
        result.ingest = [info.as_dict() for info in ingest.per_symbol.values()]
        result.previous_policy = prev_policy
        result.extra["streak_losses"] = self.engine.streak_losses
        result.extra["streak_wins"] = self.engine.streak_wins
        result.extra["recent_trades"] = self.store.recent_trades(8)

        if not ingest.bars or all(not v for v in ingest.bars.values()):
            result.warnings.append("هیچ کندل تازه‌ای از منبع داده نیامد؛ اکسپرت متاتریدر یا مسیر را چک کن.")
        stale = [i["symbol"] for i in result.ingest if i.get("stale")]
        if stale and isinstance(source, HarvesterSource):
            result.warnings.append(f"داده‌ی {len(stale)} نماد کهنه است: {', '.join(stale[:6])}")

        # -- تصمیم برای چرخه‌ی بعد ----------------------------------
        decisions: list[dict[str, Any]] = []
        next_policy, decision = self.adaptive.maybe_adapt(
            policy_in, result.extra["streak_losses"], result.extra["streak_wins"], cycle
        )
        if decision is not None:
            decisions.append(decision.as_dict())
            self.store.add_decision(cycle, decision.as_dict())

        base = next_policy
        if self.advisor.should_consult(cycle, force=force_llm):
            context = self.build_llm_context(result, base)
            llm = self.advisor.consult(context, force=force_llm)
            meta = dict(llm.meta)
            result.llm_meta = meta
            if llm.proposal is not None:
                candidate, notes = validate_proposal(llm.proposal, base, cycle)
                meta["notes"] = notes
                meta["accepted"] = candidate is not None
                if candidate is not None:
                    result.llm_note = (
                        f"پذیرفته شد → {candidate.get('variant')} (نسخه {candidate.get('version')})؛ "
                        f"{candidate.get('reason') or 'بدون دلیل'}"
                    )
                    decision_llm = Decision(
                        ts=utc_iso(),
                        cycle=cycle,
                        source="llm",
                        action="llm-adjust",
                        level_before=int(base.get("level", 0)),
                        level_after=int(candidate.get("level", 0)),
                        reason=str(llm.proposal.get("reason", ""))[:400] or "پیشنهاد مدل",
                        changes={
                            "variant": [base.get("variant"), candidate.get("variant")],
                            "params": {
                                k: [base.get("params", {}).get(k), v]
                                for k, v in candidate.get("params", {}).items()
                                if base.get("params", {}).get(k) != v
                            },
                            "risk_scale": [base.get("risk_scale", 1.0), candidate.get("risk_scale", 1.0)],
                        },
                    )
                    decisions.append(decision_llm.as_dict())
                    self.store.add_decision(cycle, decision_llm.as_dict())
                    next_policy = candidate
                else:
                    result.llm_note = "رد شد: " + ("؛ ".join(notes) or "پیشنهاد امن نبود")
            else:
                result.llm_note = None
                if meta.get("error"):
                    result.warnings.append(f"مدل زبانی پاسخ نداد: {meta['error']}")

        result.decisions = decisions
        result.extra["next_policy"] = next_policy
        self.save_policy(next_policy)
        self.store.add_policy(cycle, next_policy)

        # -- گزارش ---------------------------------------------------
        stamp = utc_stamp(now_ts)
        markdown = render_markdown(result, {"publish": {}})
        payload = render_cycle_json(result, {"next_policy": sanitize_for_log(next_policy)})
        compact = render_compact(result)
        write_json(self.reports_dir / "archive" / utc_day(now_ts) / f"{stamp}.json", payload)
        (self.reports_dir / "archive" / utc_day(now_ts) / f"{stamp}.md").write_text(markdown, encoding="utf-8")
        (self.reports_dir / "latest.md").write_text(markdown, encoding="utf-8")

        with open(self.local_cycles, "a", encoding="utf-8") as fh:
            fh.write(jsonl_line(compact) + "\n")
        rows = [json.loads(line) for line in self.local_cycles.read_text(encoding="utf-8").splitlines() if line.strip()]
        day = utc_day(now_ts)
        today_rows = [r for r in rows if str(r.get("ts", "")).startswith(day)]
        daily_md = render_daily(today_rows, day)

        files = {
            str(self.cfg.get("report.dir", "reports/paper")).strip("/") + "/latest.md": markdown,
            str(self.cfg.get("report.dir", "reports/paper")).strip("/") + "/cycles.jsonl": prune_jsonl(
                self.local_cycles.read_text(encoding="utf-8")
            ),
            str(self.cfg.get("report.dir", "reports/paper")).strip("/") + "/daily/" + f"{day}.md": daily_md,
            str(self.cfg.get("report.dir", "reports/paper")).strip("/") + "/policy.json": json.dumps(
                {"active": sanitize_for_log(policy_in), "next": sanitize_for_log(next_policy), "ts": utc_iso()},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            str(self.cfg.get("report.dir", "reports/paper")).strip("/") + "/state.json": json.dumps(
                render_state(result), ensure_ascii=False, indent=2
            )
            + "\n",
            str(self.cfg.get("report.dir", "reports/paper")).strip("/") + "/README.md": render_readme(
                str(self.cfg.get("report.dir", "reports/paper"))
            ),
        }

        publish_info: dict[str, Any] = {"action": "disabled", "hash": None, "branch": self.publisher.branch}
        if publish:
            message = (
                f"cycle #{cycle} {result.ts} | equity={result.equity:.2f} "
                f"({render_compact(result)['pnl_pct']:+.3f}%) | trades={result.overall_stats.get('trades', 0)} | "
                f"{next_policy.get('variant')}@L{next_policy.get('level')}"
            )
            try:
                publish_info = self.publisher.publish(files, message, dry_run=dry_run_publish)
            except Exception as exc:  # noqa: BLE001 - انتشار هرگز نباید چرخه را بکشد
                LOG.exception("انتشار شکست خورد")
                publish_info = {"action": "error", "hash": None, "error": str(exc), "branch": self.publisher.branch}
            if publish_info.get("error") and publish_info.get("action") != "no-changes":
                result.warnings.append(f"انتشار: {publish_info['error']}")
            (self.reports_dir / "latest.md").write_text(
                render_markdown(result, {"publish": publish_info}), encoding="utf-8"
            )

        result.extra["publish"] = publish_info
        stored = render_compact(result)
        self.store.add_cycle(cycle, stored)
        LOG.info(
            "چرخه #%s تمام شد | equity=%.2f | بسته‌شده=%s | سیاست بعد: %s",
            cycle, result.equity, len(result.closed_trades), describe(next_policy),
        )
        return {
            "ok": True,
            "cycle": cycle,
            "equity": result.equity,
            "trades_closed": len(result.closed_trades),
            "report_md": str(self.reports_dir / "latest.md"),
            "publish": publish_info,
            "policy": describe(next_policy),
            "compact": stored,
        }

    # -- حلقه ----------------------------------------------------------
    def run_forever(self, interval: int | None = None) -> int:
        interval = int(interval or self.cfg.get("loop.interval_s", 300))
        lock = LockFile(self.state_dir / "loop.lock", stale_after_s=max(600, interval * 3))
        if not lock.acquire():
            LOG.error("یک نمونه‌ی دیگر در حال اجراست (%s)", self.state_dir / "loop.lock")
            return 2

        def _stop(signum: int, _frame: Any) -> None:
            LOG.info("سیگنال %s دریافت شد؛ پایان تمیز پس از چرخه‌ی جاری", signum)
            self._running = False

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

        backoff = 0
        LOG.info("حلقه شروع شد | هر %s ثانیه | منبع=%s", interval, self.cfg.get("data.source"))
        try:
            while self._running:
                started = time.time()
                try:
                    self.run_cycle()
                    backoff = 0
                except Exception as exc:  # noqa: BLE001 - حلقه نباید بمیرد
                    LOG.exception("چرخه شکست خورد: %s", exc)
                    backoff = min(900, backoff * 2 or 60)
                    write_json(
                        self.state_dir / "incidents" / f"{utc_stamp()}.json",
                        {"error": str(exc), "cycle_ts": utc_iso(), "backoff_s": backoff},
                    )
                wait = interval - (time.time() - started)
                if backoff:
                    wait = max(wait, backoff)
                deadline = time.time() + max(1.0, wait)
                while self._running and time.time() < deadline:
                    time.sleep(min(5.0, max(0.2, deadline - time.time())))
        finally:
            lock.release()
            self.store.close()
        return 0
