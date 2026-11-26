"""تولید گزارش چرخه: Markdown فارسی برای انسان/ایجنت + JSON ماشینی."""

from __future__ import annotations

import json
from typing import Any, Sequence

from . import SCHEMA_VERSION, __version__
from .paper import CycleResult
from .policy import describe
from .util import utc_day, utc_iso

VARIANT_FA = {"momentum": "شکست تکانه", "meanrev": "بازگشت به میانگین", "scalp": "اسکالپ سریع"}
REASON_FA = {"sl": "حد ضرر", "tp": "حد سود", "time": "پایان زمان", "manual": "دستی"}


def _money(value: float) -> str:
    return f"{value:,.2f}"


def _side(side: int) -> str:
    return "خرید" if side > 0 else "فروش"


def _pct(value: float) -> str:
    return f"{value:+.2f}%"


def _decision_line(decision: dict[str, Any]) -> str:
    src = {"adaptive": "نردبان قطعی", "llm": "مدل زبانی", "manual": "دستی"}.get(
        str(decision.get("source")), str(decision.get("source"))
    )
    return (
        f"- **{src}** · پله {decision.get('level_before')} → {decision.get('level_after')} · "
        f"{decision.get('reason', '')}"
    )


def _changes_block(decision: dict[str, Any]) -> list[str]:
    changes = decision.get("changes") or {}
    lines: list[str] = []
    if isinstance(changes.get("variant"), list) and len(changes["variant"]) == 2:
        lines.append(f"  - استراتژی: `{changes['variant'][0]}` → `{changes['variant'][1]}`")
    params = changes.get("params") or {}
    if isinstance(params, dict) and params:
        for key, pair in list(params.items())[:10]:
            if isinstance(pair, list) and len(pair) == 2:
                lines.append(f"  - `{key}`: {pair[0]} → {pair[1]}")
    if isinstance(changes.get("risk_scale"), list) and changes["risk_scale"][0] != changes["risk_scale"][1]:
        lines.append(f"  - ضریب ریسک: {changes['risk_scale'][0]} → {changes['risk_scale'][1]}")
    return lines


def agent_checklist(result: CycleResult) -> list[str]:
    """کارهایی که ایجنت باید در چرخه‌ی بعد نگاه کند — خودکار از وضعیت ساخته می‌شود."""
    items: list[str] = []
    pnl_pct = ((result.equity - result.starting_balance) / result.starting_balance * 100.0) if result.starting_balance else 0.0
    if result.warnings:
        items.append(f"هشدارهای داده/اجرا را بررسی کن ({len(result.warnings)} مورد).")
    if result.drawdown_pct > 2.0:
        items.append(f"افت از اوج {result.drawdown_pct:.2f}% است؛ آیا نردبان سازگاری درست عمل کرده؟")
    if abs(pnl_pct) > 0.01:
        items.append("آیا نتیجه‌ی این چرخه با انتظار آماری سیاست هم‌خوان است؟")
    if not result.closed_trades and not result.open_positions:
        items.append("در این چرخه هیچ معامله‌ای باز یا بسته نشد؛ آیا فیلترها بیش از حد سخت شده‌اند؟")
    if result.decisions:
        items.append("تغییر سیاست این چرخه را با داده‌ی بعدی بسنج؛ اگر نتیجه بدتر شد، پله را برگردان.")
    if result.llm_note:
        items.append("پیشنهاد مدل زبانی را با آمار واقعی مقایسه کن (پذیرش/رد در بخش ۶ آمده است).")
    stale = [i["symbol"] for i in result.ingest if i.get("stale")]
    if stale:
        items.append(f"داده‌ی این نمادها تازه نیست: {', '.join(stale[:6])} — اکسپرت/اتصال را چک کن.")
    items.append("اگر لازم بود، پارامترهای `configs/local.toml` یا سیاست را با کامیت بعدی اصلاح کن.")
    return items


def render_markdown(result: CycleResult, extra: dict[str, Any] | None = None) -> str:
    extra = extra or {}
    pnl = result.equity - result.starting_balance
    pnl_pct = (pnl / result.starting_balance * 100.0) if result.starting_balance else 0.0
    lines: list[str] = []
    lines.append(f"# گزارش چرخه‌ی کاغذی — {result.ts}")
    lines.append("")
    lines.append(
        f"> چرخه **#{result.cycle}** · منبع داده: `{result.source or 'نامشخص'}` · "
        f"حالت: **کاغذی** (هیچ سفارش واقعی ارسال نمی‌شود) · نسخه {__version__}"
    )
    lines.append("")

    # ۱) خلاصه
    lines.append("## ۱) خلاصه")
    lines.append("")
    lines.append("| شاخص | مقدار |")
    lines.append("|---|---|")
    lines.append(f"| بالانس | {_money(result.balance)} |")
    lines.append(f"| اکوییتی (با سود/زیان باز) | {_money(result.equity)} |")
    lines.append(f"| سود/زیان از شروع | {pnl:+,.2f} ({_pct(pnl_pct)}) |")
    lines.append(f"| افت از اوج | {result.drawdown_pct:.2f}% |")
    lines.append(f"| معاملات کل | {result.overall_stats.get('trades', 0)} "
                 f"(برد {result.overall_stats.get('wins', 0)} / باخت {result.overall_stats.get('losses', 0)}) |")
    lines.append(f"| وین‌ریت کل | {result.overall_stats.get('win_rate', 0)}% |")
    lines.append(f"| میانگین R | {result.overall_stats.get('avg_r', 0)} |")
    lines.append(f"| استریک فعلی | {result.extra.get('streak_wins', 0)} برد / {result.extra.get('streak_losses', 0)} باخت |")
    lines.append(f"| کندل پردازش‌شده این چرخه | {result.bars_processed:,} |")
    lines.append(f"| معاملات بسته‌شده این چرخه | {len(result.closed_trades)} |")
    if result.entries_blocked:
        lines.append(f"| قفل ورود | {result.entries_blocked} |")
    lines.append("")

    # ۲) معاملات
    lines.append("## ۲) معاملات بسته‌شده در این چرخه")
    lines.append("")
    if result.closed_trades:
        lines.append("| نماد | جهت | ورود | خروج | دلیل خروج | سود/زیان | bp | R | مدت |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for t in result.closed_trades:
            dur = int(t["exit_ts"]) - int(t["entry_ts"])
            lines.append(
                f"| {t['symbol']} | {_side(int(t['side']))} | {t['entry_price']:.6g} | {t['exit_price']:.6g} | "
                f"{REASON_FA.get(str(t['exit_reason']), t['exit_reason'])} | {t['pnl']:+,.2f} | "
                f"{t['pnl_bps']:+.1f} | {t['r_multiple']:+.2f} | {dur}s |"
            )
    else:
        lines.append("_این چرخه معامله‌ای بسته نشد._")
    lines.append("")

    # ۳) پوزیشن باز
    lines.append("## ۳) پوزیشن‌های باز")
    lines.append("")
    if result.open_positions:
        lines.append("| نماد | جهت | ورود | قیمت فعلی | سود باز | SL | TP | سن |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for p in result.open_positions:
            lines.append(
                f"| {p['symbol']} | {_side(int(p['side']))} | {p['entry_price']:.6g} | "
                f"{p.get('last_price', 0):.6g} | {p.get('unrealized', 0):+,.2f} | "
                f"{p['sl_price']:.6g} | {p['tp_price']:.6g} | {int(p.get('age_s', 0))}s |"
            )
    else:
        lines.append("_پوزیشن بازی نیست._")
    lines.append("")

    # ۴) سیاست
    lines.append("## ۴) سیاست معاملاتی")
    lines.append("")
    lines.append(f"**سیاست فعال این چرخه:** {describe(result.policy)}")
    lines.append("")
    lines.append(f"- نسخه: `{result.policy.get('version')}` · منبع: `{result.policy.get('source')}` · "
                 f"پله: `{result.policy.get('level')}`")
    lines.append(f"- دلیل آخرین تغییر: {result.policy.get('reason') or '—'}")
    if result.decisions:
        lines.append("")
        lines.append("**تغییرات ثبت‌شده در این چرخه:**")
        for decision in result.decisions:
            lines.append(_decision_line(decision))
            lines.extend(_changes_block(decision))
    else:
        lines.append("")
        lines.append("**تغییر سیاستی در این چرخه اعمال نشد.**")
    next_policy = result.extra.get("next_policy")
    if next_policy:
        lines.append("")
        lines.append(f"**سیاست چرخه‌ی بعد:** {describe(next_policy)}")
    lines.append("")

    # ۵) کیفیت داده
    lines.append("## ۵) کیفیت داده")
    lines.append("")
    if result.ingest:
        lines.append("| نماد | کندل تازه | آخرین تیک (UTC) | تأخیر | وضعیت |")
        lines.append("|---|---|---|---|---|")
        for item in result.ingest:
            lag = item.get("lag_s")
            lag_txt = "∞" if lag in (None, float("inf")) else f"{float(lag):.0f}s"
            state = "کهنه" if item.get("stale") else "سالم"
            last_ts = int(item.get("last_tick_ts") or 0)
            lines.append(
                f"| {item['symbol']} | {item.get('new_bars', 0)} | "
                f"{utc_iso(last_ts) if last_ts else '—'} | {lag_txt} | {state} |"
            )
    else:
        lines.append("_داده‌ی ورودی در این چرخه ثبت نشد._")
    lines.append("")

    # ۶) مدل زبانی
    lines.append("## ۶) مدل زبانی")
    lines.append("")
    meta = result.llm_meta or {}
    if not meta:
        lines.append("_مدل زبانی در این چرخه صدا زده نشد (طبق `llm.every_cycles`)._")
    elif not meta.get("ok"):
        lines.append(f"- وضعیت: **بدون پاسخ** · `{meta.get('provider')}` · علت: {meta.get('error', '—')}")
        lines.append("- سیستم با سیاست قطعی ادامه داد؛ هیچ تغییری اعمال نشد.")
    else:
        lines.append(f"- provider: `{meta.get('provider')}` · model: `{meta.get('model')}` · "
                     f"تأخیر: {meta.get('latency_ms', '—')}ms")
        lines.append(f"- نتیجه: **{result.llm_note or '—'}**")
        if meta.get("notes"):
            for note in meta["notes"][:6]:
                lines.append(f"  - {note}")
        if meta.get("accepted") is False and meta.get("raw_excerpt"):
            lines.append(f"- خروجی ردشده (خلاصه): `{str(meta['raw_excerpt'])[:300]}`")
    lines.append("")

    # ۷) هشدار
    lines.append("## ۷) هشدارها")
    lines.append("")
    if result.warnings:
        for warning in result.warnings:
            lines.append(f"- {warning}")
    else:
        lines.append("_هشداری نیست._")
    lines.append("")

    # ۸) برای ایجنت
    lines.append("## ۸) برای ایجنت — بررسی چرخه‌ی بعد")
    lines.append("")
    for item in agent_checklist(result):
        lines.append(f"- [ ] {item}")
    lines.append("")
    pub = extra.get("publish") or result.extra.get("publish")
    if pub:
        lines.append("## ۹) انتشار")
        lines.append("")
        lines.append(
            f"- وضعیت: `{pub.get('action', '—')}` · کامیت: `{pub.get('hash') or '—'}` · "
            f"branch: `{pub.get('branch', '—')}`"
        )
        if pub.get("error"):
            lines.append(f"- خطا: {pub['error']}")
        lines.append("")
    lines.append("---")
    lines.append(
        f"_این گزارش خودکار توسط TradeAgent ساخته شد. کاغذی و پژوهشی است، توصیه‌ی مالی نیست. "
        f"زمان تولید: {utc_iso()}._"
    )
    lines.append("")
    return "\n".join(lines)


def render_compact(result: CycleResult) -> dict[str, Any]:
    """یک خط JSON فشرده برای `cycles.jsonl`."""
    pnl_pct = 0.0
    if result.starting_balance:
        pnl_pct = (result.equity - result.starting_balance) / result.starting_balance * 100.0
    return {
        "schema": SCHEMA_VERSION,
        "cycle": result.cycle,
        "ts": result.ts,
        "source": result.source,
        "balance": result.balance,
        "equity": result.equity,
        "pnl_pct": round(pnl_pct, 4),
        "drawdown_pct": result.drawdown_pct,
        "trades_total": result.overall_stats.get("trades", 0),
        "trades_closed_cycle": len(result.closed_trades),
        "wins": result.overall_stats.get("wins", 0),
        "losses": result.overall_stats.get("losses", 0),
        "open_positions": len(result.open_positions),
        "bars": result.bars_processed,
        "variant": result.policy.get("variant"),
        "level": result.policy.get("level"),
        "next_variant": (result.extra.get("next_policy") or {}).get("variant"),
        "next_level": (result.extra.get("next_policy") or {}).get("level"),
        "decisions": [
            {"source": d.get("source"), "action": d.get("action"), "to": d.get("level_after")}
            for d in result.decisions
        ],
        "warnings": len(result.warnings),
        "llm": (result.llm_meta or {}).get("ok", False),
        "symbols": sorted({i["symbol"] for i in result.ingest}),
    }


def render_cycle_json(result: CycleResult, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = result.as_dict()
    payload["schema"] = SCHEMA_VERSION
    payload["generator"] = f"tradeagent/{__version__}"
    payload["compact"] = render_compact(result)
    if extra:
        payload["extra"] = extra
    return payload


def render_daily(results: Sequence[dict[str, Any]], day: str) -> str:
    """جدول روزانه از ردیف‌های فشرده."""
    lines = [f"# خلاصه‌ی روزانه — {day}", "", "| چرخه | زمان | اکوییتی | سود% | افت% | معامله | استراتژی | پله |",
             "|---|---|---|---|---|---|---|---|"]
    for row in results:
        lines.append(
            f"| {row.get('cycle')} | {row.get('ts')} | {_money(float(row.get('equity', 0)))} | "
            f"{float(row.get('pnl_pct', 0)):+.3f} | {float(row.get('drawdown_pct', 0)):.2f} | "
            f"{row.get('trades_closed_cycle', 0)} | {row.get('variant')} | {row.get('level')} |"
        )
    lines.append("")
    return "\n".join(lines)


def render_readme(report_dir: str) -> str:
    return f"""# گزارش‌های کاغذی TradeAgent

این پوشه توسط چرخه‌ی خودکار نوشته می‌شود؛ دستی ویرایش نکن.

| فایل | محتوا |
|---|---|
| `latest.md` | کامل‌ترین گزارش آخرین چرخه (برای خواندن سریع انسان/ایجنت) |
| `cycles.jsonl` | یک خط JSON فشرده به ازای هر چرخه (برای تحلیل سریع و دیف سبک) |
| `daily/YYYY-MM-DD.md` | جدول تجمعی همان روز |
| `policy.json` | سیاست فعال و سیاست چرخه‌ی بعد |
| `state.json` | وضعیت کاغذی: بالانس، اکوییتی، استریک، تعداد معاملات |

هیچ سفارشی به بروکر ارسال نمی‌شود. مسیر: `{report_dir}`
"""


def render_state(result: CycleResult) -> dict[str, Any]:
    return {
        "schema": SCHEMA_VERSION,
        "updated_ts": utc_iso(),
        "cycle": result.cycle,
        "balance": result.balance,
        "equity": result.equity,
        "peak_equity": result.peak_equity,
        "drawdown_pct": result.drawdown_pct,
        "starting_balance": result.starting_balance,
        "streak_wins": result.extra.get("streak_wins", 0),
        "streak_losses": result.extra.get("streak_losses", 0),
        "open_positions": len(result.open_positions),
        "overall_stats": result.overall_stats,
        "today_stats": result.today_stats,
        "policy": {
            "variant": result.policy.get("variant"),
            "level": result.policy.get("level"),
            "version": result.policy.get("version"),
            "source": result.policy.get("source"),
        },
        "day": utc_day(result.ts_epoch or None),
    }


def jsonl_line(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
