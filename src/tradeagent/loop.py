"""ارکستراتور: یک چرخه‌ی کامل ۵ دقیقه‌ای و حلقه‌ی بی‌پایان.

مستقل از منبع داده: می‌تواند از پوشه‌ی اکسپرت متاتریدر بخواند یا از داده‌ی ساختگی.
"""

from __future__ import annotations

import json
import signal
import time
import os
from pathlib import Path
from typing import Any

from .config import Config
from .features import compute_features
from .agent import ToolAgent
from .llm import LLMAdvisor, LLMResult
from .mcp_client import mcp_from_config
from .nowcast import ForecastLedger, digests_for, tick_digest
from .paper import CycleResult, PaperEngine, Store
from .policy import (AdaptiveController, Decision, baseline_params, describe, new_policy,
                     sanitize_for_log, validate_proposal)
from .publish import Publisher, prune_jsonl
from .report import (jsonl_line, render_compact, render_cycle_json, render_daily, render_markdown,
                     render_readme, render_state)
from .ticks import HarvesterSource, SyntheticSource
from .util import LOG, LockFile, ensure_dir, utc_day, utc_iso, utc_stamp, write_json

DEFAULT_SYMBOLS = ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "EURUSD", "GBPUSD", "USDJPY", "AUDUSD"]



HAREV_REL = Path("drive_c/Program Files/MetaTrader 5/MQL5/Files/harvester")
_DISCOVERY_LOGGED: set[str] = set()


def resolve_harvester_dir(configured: Path) -> Path:
    """پوشهٔ harvester را پیدا کن؛ اگر مسیر پیکربندی وجود نداشت، خودت بگرد.

    خطای رایج میدانی: سرویس با کانفیگی بالا می‌آید که ``data.harvester_dir`` در آن
    هنوز مسیر پیش‌فرض است، در حالی که MT5 در Wine جای دیگری می‌نویسد. نتیجه‌اش
    «۰ کندل در هر چرخه» و پوزیشن‌های بی‌خروج است. اینجا مسیر واقعی کشف می‌شود.
    """
    configured = Path(configured).expanduser()
    if (configured / "ticks").is_dir():
        return configured
    prefixes = []
    for env_name in ("MT5_WINEPREFIX", "WINEPREFIX"):
        val = os.environ.get(env_name)
        if val:
            prefixes.append(Path(val).expanduser())
    prefixes += [
        Path.home() / "Wine",
        Path.home() / ".wine",
        Path.home() / ".local/share/wineprefixes/mt5",
    ]
    candidates = [prefix / HAREV_REL for prefix in prefixes]
    candidates.append(Path.home() / "mt5/MQL5/Files/harvester")
    def _announce(found: Path) -> Path:
        key = f"{configured}->{found}"
        if key not in _DISCOVERY_LOGGED:
            _DISCOVERY_LOGGED.add(key)
            LOG.warning("data.harvester_dir=%s وجود ندارد؛ مسیر واقعی پیدا شد: %s", configured, found)
        return found

    for cand in candidates:
        if (cand / "ticks").is_dir():
            return _announce(cand)
    for base in (Path.home() / "Wine", Path.home() / ".wine"):
        if not base.is_dir():
            continue
        for match in base.glob("drive_c/**/MQL5/Files/harvester"):
            if (match / "ticks").is_dir():
                return _announce(match)
    LOG.error(
        "پوشهٔ harvester پیدا نشد (تنظیم‌شده: %s)؛ اکسپرت را اجرا کن یا data.harvester_dir را ست کن",
        configured,
    )
    return configured

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
        # ایجنت ابزاردار: اگر MCP پیکربندی شده باشد، مدل خودش داده می‌گیرد
        self.ledger = ForecastLedger(path=self.state_dir / "forecasts.json").load()
        self.mcp = mcp_from_config(cfg)
        self.agent: ToolAgent | None = None
        if self.mcp.enabled and self.mcp.start():
            allow = cfg.get("agent.allow_tools", None)
            self.agent = ToolAgent(
                chat=self.advisor.chat,
                mcp=self.mcp,
                local_tools=self._local_tools(),
                allow=list(allow) if isinstance(allow, list) else None,
                max_rounds=int(cfg.get("agent.max_rounds", 3)),
                max_calls=int(cfg.get("agent.max_calls", 6)),
            )
            LOG.info(
                "ایجنت ابزاردار فعال شد: %s (%d ابزار)",
                self.mcp.server_info.get("name", "mcp"),
                len(self.mcp.tools),
            )
        elif self.mcp.enabled:
            LOG.warning("MCP پیکربندی شده ولی بالا نیامد؛ چرخه قطعی ادامه می‌دهد")
            self.mcp.enabled = False
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
            root=resolve_harvester_dir(self.cfg.path("data.harvester_dir", "~/mt5/MQL5/Files/harvester")),
            symbols=symbols or None,
            state_path=self.state_dir / "ingest_state.json",
            stale_after_s=float(self.cfg.get("data.stale_after_s", 180)),
        )

    def _local_tools(self) -> dict[str, Any]:
        """ابزارهای محلی روی تیک‌های خودمان: بدون MT5 هم ایجنت داده دارد."""
        def _bars(symbol: str | None = None, seconds: int = 1, count: int = 60) -> list:
            sym = symbol or (self.cfg.symbols or ["XAUUSD"])[0]
            horizon = max(30, int(seconds) * max(1, int(count)))
            return self.store.recent_bars(sym, horizon)

        def local_tick_digest(symbol: str | None = None, **_kw: Any) -> dict:
            sym = symbol or (self.cfg.symbols or ["XAUUSD"])[0]
            return tick_digest(self.store.recent_bars(sym, 1200))

        def local_candles(symbol: str | None = None, seconds: int = 5, count: int = 12, **_kw: Any) -> dict:
            sym = symbol or (self.cfg.symbols or ["XAUUSD"])[0]
            bars = self.store.recent_bars(sym, max(120, int(seconds) * int(count) * 2))
            if not bars:
                return {"symbol": sym, "error": "بدون کندل (فید قطع است)"}
            step = max(1, int(seconds))
            out = []
            chunk: list = []
            for bar in bars:
                chunk.append(bar)
                if len(chunk) == step:
                    out.append(
                        {
                            "t": chunk[0].t,
                            "open": round(chunk[0].open, 6),
                            "high": round(max(b.high for b in chunk), 6),
                            "low": round(min(b.low for b in chunk), 6),
                            "close": round(chunk[-1].close, 6),
                            "ticks": sum(b.ticks for b in chunk),
                        }
                    )
                    chunk = []
            return {"symbol": sym, "seconds": step, "candles": out[-int(count):]}

        return {"local_tick_digest": local_tick_digest, "local_candles": local_candles}

    def publish_mcp_status(self) -> dict[str, Any]:
        """وضعیت پل متاتریدر را در یک فایل بنویس.

        سوکت ۸۷۸۷ فقط یک صاحب دارد؛ اگر سرویس آن را گرفته باشد هیچ اسکریپت دیگری
        نمی‌تواند «نسخهٔ دوم» بالا بیاورد. پس وضعیت را همین‌جا منتشر می‌کنیم.
        """
        status: dict[str, Any] = {"ts": utc_iso(), "mcp_ready": bool(self.mcp and self.mcp.enabled)}
        if self.mcp and self.mcp.enabled:
            try:
                raw = self.mcp.call_tool("mt5_status")
                if isinstance(raw, dict):
                    status.update(
                        {
                            "ok": bool(raw.get("ok")),
                            "bridge": raw.get("bridge") or {},
                            "terminal": raw.get("terminal") or {},
                            "account": raw.get("account") or {},
                            "error": raw.get("error"),
                            "hint": raw.get("hint"),
                        }
                    )
            except Exception as exc:  # noqa: BLE001
                status["ok"] = False
                status["error"] = f"{type(exc).__name__}: {exc}"
                if "bind" in str(exc).lower():
                    status["hint"] = (
                        "port 8787 already has an owner (another trade-mcp/loop); only one "
                        "process can host the bridge - stop the other one"
                    )
        try:
            write_json(self.state_dir / "mcp_status.json", status)
        except OSError:
            pass
        return status

    def skill_text(self) -> str:
        """متن مهارت آماده از trade-mcp (مثلاً gold_session_briefing)."""
        name = self.cfg.get("agent.skill_prompt", "")
        if not name or self.mcp is None or not self.mcp.enabled:
            return ""
        try:
            return self.mcp.get_prompt(str(name), {"symbol": self.cfg.get("agent.skill_symbol", "XAUUSD")})
        except Exception as exc:  # noqa: BLE001
            LOG.warning("خواندن پرامپت مهارت %s شکست خورد: %s", name, exc)
            return ""

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
            "tick_digest": result.extra.get("digest", {}),
            "your_forecast_score": result.extra.get("forecast_stats", {}),
            "your_recent_forecasts": result.extra.get("forecasts_recent", []),
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

        try:
            mcp_status = self.publish_mcp_status()
            if mcp_status.get("error") and "bind" in str(mcp_status.get("error")).lower():
                result_warn = "پورت ۸۷۸۷ دست یک پروسهٔ دیگر است؛ نسخهٔ طلایی/عمومی را خاموش کن"
                LOG.warning(result_warn)
            result_extra_status = {"mcp": mcp_status}
        except Exception as exc:  # noqa: BLE001
            LOG.warning("MCP status failed: %s", exc)
            result_extra_status = {}

        result = self.engine.process(ingest.bars, policy_in, cycle, now_ts)
        result.extra.update(result_extra_status)

        # خوراک ایجنت: خلاصهٔ عددی حرکت ثانیه‌ای (تصمیم با مدل است)
        window = int(self.cfg.get("nowcast.window_s", 1200))
        history = {
            sym: self.store.recent_bars(sym, window)
            for sym in (ingest.bars.keys() or self.cfg.symbols)
        }
        try:
            result.extra["digest"] = digests_for(history)
            prices = {
                sym: (bars[-1].close if bars else 0.0) for sym, bars in history.items()
            }
            ledger = self.ledger
            ledger.score(prices, now_ts)
            result.extra["forecast_stats"] = ledger.stats()
            result.extra["forecasts_recent"] = ledger.recent(5)
            ledger.save()
        except Exception as exc:  # noqa: BLE001
            result.warnings.append(f"digest/دفتر پیش‌بینی: {type(exc).__name__}: {exc}")
        result.source = ingest.source
        result.ingest = [info.as_dict() for info in ingest.per_symbol.values()]
        result.previous_policy = prev_policy
        result.extra["streak_losses"] = self.engine.streak_losses
        result.extra["streak_wins"] = self.engine.streak_wins
        result.extra["recent_trades"] = self.store.recent_trades(8)

        if not ingest.bars or all(not v for v in ingest.bars.values()):
            where = ""
            if isinstance(source, HarvesterSource):
                where = f" (مسیر خوانده‌شده: {source.root})"
                if not source.available_symbols():
                    result.warnings.append(
                        f"در {source.root} هیچ نمادی پیدا نشد؛ data.harvester_dir اشتباه است یا "
                        "symbols.json/پوشهٔ ticks وجود ندارد" + where
                    )
            if not result.warnings or "هیچ کندل تازه‌ای" not in result.warnings[-1]:
                result.warnings.append(
                    "هیچ کندل تازه‌ای از منبع داده نیامد؛ اکسپرت متاتریدر یا مسیر را چک کن." + where
                )
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
            if self.agent is not None:
                trace = self.agent.run(context, skill_text=self.skill_text())
                result.extra["agent"] = trace.as_dict()
                if trace.momentum:
                    result.extra["momentum"] = trace.momentum
                    symbol = (self.cfg.symbols or ["XAUUSD"])[0]
                    price = 0.0
                    bars = history.get(symbol) or []
                    if bars:
                        price = bars[-1].close
                    rec = self.ledger.record(symbol, trace.momentum, price, now_ts)
                    if rec is not None:
                        self.ledger.save()
                        LOG.info(
                            "پیش‌بینی ایجنت: %s %s (اطمینان %.2f، افق %ss)",
                            symbol, rec.direction, rec.confidence, rec.horizon_s,
                        )
                if trace.error:
                    result.warnings.append(f"ایجنت: {trace.error}")
                llm = LLMResult(trace.proposal, {"provider": "agent", **trace.as_dict()})
                LOG.info("ایجنت: %s", trace.summary())
            else:
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
        mom = (result.extra.get("momentum") or {}) if isinstance(result.extra, dict) else {}
        mom_txt = (
            f"{mom.get('direction', '?')}/{mom.get('phase', '?')} "
            f"قدرت={float(mom.get('strength', 0) or 0):.2f} "
            f"اطمینان={float(mom.get('confidence', 0) or 0):.2f} "
            f"افق={mom.get('horizon_s', '?')}s"
            if mom else "بدون حکم (مدل برنگرداند)"
        )
        LOG.info(
            "چرخه #%s تمام شد | equity=%.2f | بسته‌شده=%s | تکانه: %s | سیاست بعد: %s",
            cycle, result.equity, len(result.closed_trades), mom_txt, describe(next_policy),
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
            pid, _age = lock._owner()
            LOG.error(
                "یک نمونه‌ی دیگر در حال اجراست (pid=%s، %s). "
                "اگر مطمئنی مرده است: rm -f %s  — یا با 'systemctl --user stop tradeagent-loop.service' سرویس را بخوابان.",
                pid or "?", self.state_dir / "loop.lock", self.state_dir / "loop.lock",
            )
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
