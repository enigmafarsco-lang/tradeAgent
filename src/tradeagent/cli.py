"""رابط خط فرمان TradeAgent."""

from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path
from typing import Sequence

from . import __version__
from .config import DEFAULT_CONFIG, REPO_ROOT, Config
from .doctor import render_checks, run_doctor, summary
from .llm import LLMAdvisor
from .paper import Store
from .policy import describe
from .publish import Publisher
from .publish import Publisher
from .util import ensure_dir, setup_logging, utc_iso, utc_stamp


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tradeagent",
        description="سیستم ۲۴/۷ تیک‌برداری + چرخه‌ی کاغذی هوش مصنوعی با گزارش گیت",
    )
    parser.add_argument("--config", default=None, help="مسیر فایل TOML (پیش‌فرض: configs/local.toml یا default)")
    parser.add_argument("--root", default=None, help="ریشه‌ی پروژه (پیش‌فرض: محل نصب)")
    parser.add_argument("--version", action="version", version=f"tradeagent {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="ساخت پیکربندی محلی و پوشه‌ها")
    p_init.add_argument("--force", action="store_true", help="بازنویسی configs/local.toml")

    p_doctor = sub.add_parser("doctor", help="بررسی سلامت داده/انتشار/مدل")
    p_doctor.add_argument("--network", action="store_true", help="تست دسترسی remote گیت")
    p_doctor.add_argument("--json", action="store_true", help="خروجی JSON")

    p_cycle = sub.add_parser("cycle", help="اجرای یک چرخه")
    p_cycle.add_argument("--llm", action="store_true", help="این چرخه مدل صدا زده شود")
    p_cycle.add_argument("--no-publish", action="store_true", help="بدون پوش به گیت")
    p_cycle.add_argument("--dry-run-publish", action="store_true", help="فقط نوشتن، بدون کامیت")
    p_cycle.add_argument("--now", type=int, default=None, help="زمان یونیکس ساختگی (تست)")

    p_loop = sub.add_parser("loop", help="حلقه‌ی ۵ دقیقه‌ای (production)")
    p_loop.add_argument("--interval", type=int, default=None, help="ثانیه بین چرخه‌ها (پیش‌فرض 300)")
    p_loop.add_argument("--once", action="store_true", help="فقط یک چرخه، بعد خروج (برای تست اتصال)")
    p_loop.add_argument("--llm", action="store_true", help="مدل را حتی بدون دادهٔ تازه صدا بزن")
    p_loop.add_argument("--wait-ea", type=float, default=0, help="چند ثانیه برای وصل شدن اکسپرت صبر کن")

    p_demo = sub.add_parser("demo", help="اجرای نمونه با داده‌ی ساختگی، بدون متاتریدر")
    p_demo.add_argument("--cycles", type=int, default=6, help="تعداد چرخه")
    p_demo.add_argument("--step", type=int, default=300, help="فاصله‌ی ثانیه‌ای چرخه‌ها")
    p_demo.add_argument("--keep", action="store_true", help="پاک‌نکردن state موقت")

    sub.add_parser("status", help="وضعیت فعلی از ژورنال")
    p_report = sub.add_parser("report", help="مسیر آخرین گزارش")
    p_report.add_argument("--cat", action="store_true", help="چاپ متن گزارش")

    p_ticks = sub.add_parser("ticks", help="خلاصهٔ تیک‌های ذخیره‌شده (چند روز، چند تیک، بازهٔ قیمت)")
    p_ticks.add_argument("--symbol", default=None, help="نماد (پیش‌فرض: data.symbols)")
    p_ticks.add_argument("--json", action="store_true", help="خروجی JSON")

    p_replay = sub.add_parser("replay", help="بازپخش تیک‌ها: هر N ثانیه پیش‌بینی مدل + امتیاز + گزارش گرافیکی")
    p_replay.add_argument("--symbol", default=None, help="نماد (پیش‌فرض: data.symbols)")
    p_replay.add_argument("--step", type=int, default=300, help="هر چند ثانیه یک پیش‌بینی (پیش‌فرض ۳۰۰)")
    p_replay.add_argument("--horizon", type=int, default=300, help="افق پیش‌بینی به ثانیه (پیش‌فرض ۳۰۰)")
    p_replay.add_argument("--warmup", type=int, default=900, help="چند ثانیه داده قبل از اولین پیش‌بینی")
    p_replay.add_argument("--cost-bp", type=float, default=1.0, help="هزینهٔ رفت‌وبرگشت در bp")
    p_replay.add_argument("--limit", type=int, default=0, help="حداکثر تعداد پیش‌بینی (۰=همه)")
    p_replay.add_argument("--out", default="var/backtest", help="پوشهٔ خروجی گزارش")
    p_replay.add_argument("--publish", action="store_true", help="گزارش را روی شاخهٔ reports پوش کن")
    p_replay.add_argument("--fake", choices=["up", "down", "flat", "random"], default=None, help="پیش‌بینی‌کنندهٔ ساختگی (تست بدون مدل)")

    p_pub = sub.add_parser("publish-test", help="تست کامیت/پوش گزارش‌ها")
    p_pub.add_argument("--write", action="store_true", help="کامیت واقعی (پیش‌فرض: فقط بررسی کلون)")

    return parser


def load_config(args: argparse.Namespace) -> Config:
    root = Path(args.root).resolve() if args.root else REPO_ROOT
    if args.config:
        cfg = Config.load(args.config, root)
    else:
        local = root / "configs" / "local.toml"
        cfg = Config.load(local if local.exists() else None, root)
    return cfg


def cmd_init(cfg: Config, args: argparse.Namespace) -> int:
    target = cfg.root / "configs" / "local.toml"
    ensure_dir(target.parent)
    if target.exists() and not args.force:
        print(f"پیکربندی محلی از قبل هست: {target}")
    else:
        source = cfg.source or DEFAULT_CONFIG
        shutil.copyfile(source, target)
        print(f"ساخته شد: {target}  (از {source})")
    for name in ("var", "var/reports", "var/logs"):
        ensure_dir(cfg.root / name)
    print(
        "\nقدم‌های بعدی:\n"
        "  1) اکسپرت mt5/Experts/TickHarvester.mq5 را در متاتریدر کامپایل و اجرا کن\n"
        "  2) مسیر data.harvester_dir را در configs/local.toml بگذار\n"
        "  3) tradeagent doctor\n"
        "  4) tradeagent demo    # بدون متاتریدر هم کار می‌کند\n"
        "  5) tradeagent loop    # چرخه‌ی واقعی هر ۵ دقیقه"
    )
    return 0


def cmd_doctor(cfg: Config, args: argparse.Namespace) -> int:
    checks = run_doctor(cfg, network=args.network)
    if args.json:
        print(json.dumps(summary(checks), ensure_ascii=False, indent=2))
    else:
        print(render_checks(checks))
        counts = summary(checks)["counts"]
        print(f"\nخلاصه: {counts['ok']} سالم، {counts['warn']} هشدار، {counts['fail']} خطا")
    return 1 if any(c.status == "fail" for c in checks) else 0


def _runner(cfg: Config):
    from .loop import Runner

    return Runner(cfg)


def cmd_cycle(cfg: Config, args: argparse.Namespace) -> int:
    runner = _runner(cfg)
    result = runner.run_cycle(
        now_ts=args.now,
        force_llm=args.llm,
        publish=not args.no_publish,
        dry_run_publish=args.dry_run_publish,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_loop(cfg: Config, args: argparse.Namespace) -> int:
    runner = _runner(cfg)
    if getattr(args, "once", False):
        try:
            result = runner.run_cycle(
                publish=True,
                force_llm=bool(getattr(args, "llm", False)),
                wait_ea_s=float(getattr(args, "wait_ea", 0) or 0),
            )
        except Exception as exc:  # noqa: BLE001 - یک چرخه نباید کل ابزار را با traceback بکشد
            import traceback

            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": f"{type(exc).__name__}: {exc}",
                        "trace": traceback.format_exc()[-1200:],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    return runner.run_forever(args.interval)


def cmd_ticks(cfg: Config, args: argparse.Namespace) -> int:
    from .loop import resolve_harvester_dir
    from .replay import summarize

    root = resolve_harvester_dir(cfg.path("data.harvester_dir", "~/mt5/MQL5/Files/harvester"))
    symbol = args.symbol or (cfg.symbols or ["XAUUSD_i"])[0]
    info = summarize(root, symbol)
    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0
    print(f"  نماد: {info['symbol']} · {info['files']} فایل · {info['ticks']:,} تیک")
    print(f"  بازه: {info['from']} → {info['to']}")
    print(f"  قیمت: {info['low']} … {info['high']}")
    for day in info["days"]:
        print(f"    {day['file']}: {day['ticks']:,} تیک · {day['low']} … {day['high']}")
    return 0


def cmd_replay(cfg: Config, args: argparse.Namespace) -> int:
    import random

    from .loop import resolve_harvester_dir
    from .replay import Replay, model_predict

    root = resolve_harvester_dir(cfg.path("data.harvester_dir", "~/mt5/MQL5/Files/harvester"))
    symbol = args.symbol or (cfg.symbols or ["XAUUSD_i"])[0]

    if args.fake:
        def predictor(bars, horizon):  # noqa: ANN001, ANN202
            direction = args.fake if args.fake != "random" else random.choice(["up", "down", "flat"])
            return {"direction": direction, "phase": "steady", "strength": 0.5,
                    "confidence": 0.5, "horizon_s": horizon, "expected_bp": 0.0,
                    "analysis": f"پیش‌بینی ساختگی: {direction}"}
    else:
        advisor = LLMAdvisor(cfg, cfg.state_dir / "llm_calls.json")

        def predictor(bars, horizon):  # noqa: ANN001, ANN202
            return model_predict(advisor.chat, bars, horizon)

    run = Replay(
        root=root,
        symbol=symbol,
        predictor=predictor,
        step_s=args.step,
        horizon_s=args.horizon,
        warmup_s=args.warmup,
        cost_bp=args.cost_bp,
        out_dir=cfg.path(args.out, args.out),
    )
    stats = run.run(limit_steps=args.limit)
    if not stats.get("ok"):
        print("  " + str(stats.get("error")))
        return 1
    print(f"  پیش‌بینی: {stats['steps']} · سنجیده: {stats['scored']} · درست: {stats['hit_pct']}% "
          f"· میانگین خالص: {stats['avg_net_bp']}bp · {stats['elapsed_s']}s")
    for direction, bucket in sorted((stats.get("by_direction") or {}).items()):
        print(f"    {direction}: n={bucket['n']} · درست {bucket['hit_pct']}% · میانگین {bucket['avg_bp']}bp")
    out = cfg.path(args.out, args.out)
    print(f"  گزارش: {out / 'index.html'}")
    if args.publish and not args.fake:
        pub = Publisher(cfg, cfg.root).publish(
            {
                "backtest/index.html": (out / "index.html").read_text(encoding="utf-8"),
                "backtest/predictions.jsonl": (out / "predictions.jsonl").read_text(encoding="utf-8"),
                "backtest/stats.json": (out / "stats.json").read_text(encoding="utf-8"),
            },
            "replay: پیش‌بینی ۵ دقیقه‌ای مدل روی تیک‌های واقعی",
        )
        print(f"  پوش: {pub.get('status') or pub.get('error') or pub}")
    return 0


def cmd_demo(cfg: Config, args: argparse.Namespace) -> int:
    demo_root = cfg.root / "var" / "demo" / utc_stamp()
    ensure_dir(demo_root)
    cfg.set("data.source", "synthetic")
    cfg.set("data.state_dir", str(demo_root))
    cfg.set("paper.sqlite", str(demo_root / "paper.sqlite3"))
    cfg.set("publish.enabled", False)
    cfg.set("llm.enabled", False)
    cfg.set("data.symbols", ["BTCUSD", "ETHUSD", "SOLUSD", "EURUSD"])
    cfg.set("paper.starting_balance", 10000.0)

    from .loop import Runner

    runner = Runner(cfg)
    now = int(time.time()) - args.step * args.cycles
    for i in range(args.cycles):
        result = runner.run_cycle(now_ts=now + i * args.step, publish=False)
        compact = result["compact"]
        print(
            f"چرخه {compact['cycle']:>2} | {compact['ts']} | equity={compact['equity']:,.2f} "
            f"({compact['pnl_pct']:+.3f}%) | افت={compact['drawdown_pct']:.2f}% | "
            f"بسته={compact['trades_closed_cycle']} | {compact['variant']}@L{compact['level']}"
        )
    report = demo_root / "reports" / "latest.md"
    print(
        "\n⚠️  این دمو با دادهٔ ساختگی اجرا شد؛ سود/زیان‌اش واقعی نیست و فقط "
        "سلامت مسیر داده→استراتژی→گزارش را نشان می‌دهد."
    )
    print(f"گزارش نمونه: {report}")
    print(f"ژورنال: {demo_root / 'paper.sqlite3'}")
    if not args.keep:
        print("(برای نگه‌داشتن این نمونه همان‌جا بماند؛ پوشه در var/demo است)")
    return 0


def cmd_status(cfg: Config, args: argparse.Namespace) -> int:
    store = Store(cfg.path("paper.sqlite", "var/paper.sqlite3"))
    policy = store.get_meta("policy") or {}
    payload = {
        "ts": utc_iso(),
        "balance": store.get_meta("balance"),
        "peak_equity": store.get_meta("peak_equity"),
        "streak_losses": store.get_meta("streak_losses", 0),
        "streak_wins": store.get_meta("streak_wins", 0),
        "overall": store.stats(),
        "per_symbol": store.per_symbol_stats(),
        "open_positions": [p.as_dict() for p in store.load_positions().values()],
        "policy": describe(policy) if policy else "—",
        "recent_trades": store.recent_trades(10),
    }
    store.close()
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_report(cfg: Config, args: argparse.Namespace) -> int:
    path = cfg.state_dir / "reports" / "latest.md"
    if not path.exists():
        candidates = sorted(
            (p for p in cfg.root.glob("var/**/reports/latest.md") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            path = candidates[0]
    if not path.exists():
        print("هنوز گزارشی ساخته نشده. اول `tradeagent demo` یا `tradeagent cycle` را اجرا کن.")
        return 1
    if args.cat:
        print(path.read_text(encoding="utf-8"))
    else:
        print(str(path))
    return 0


def cmd_publish_test(cfg: Config, args: argparse.Namespace) -> int:
    publisher = Publisher(cfg, cfg.root)
    ok, why = publisher.ensure()
    print(f"کلون گزارش: {'OK' if ok else 'ناموفق'} — {why}")
    if not ok:
        return 1
    print(f"مسیر: {publisher.dir}")
    print(f"branch: {publisher.branch}")
    if args.write:
        readme = publisher.dir / str(cfg.get("report.dir", "reports/paper")) / "README.md"
        readme.parent.mkdir(parents=True, exist_ok=True)
        if not readme.exists():
            readme.write_text("# گزارش‌های کاغذی TradeAgent\n\nاین پوشه خودکار نوشته می‌شود.\n", encoding="utf-8")
        from .report import render_readme

        target = publisher.dir / str(cfg.get("report.dir", "reports/paper")) / "README.md"
        target.write_text(render_readme(str(cfg.get("report.dir"))), encoding="utf-8")
        result = publisher.publish(
            {
                str(cfg.get("report.dir", "reports/paper")).strip("/") + "/README.md": target.read_text(
                    encoding="utf-8"
                )
            },
            f"tradeagent: publish smoke test {utc_iso()}",
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = load_config(args)
    setup_logging(str(cfg.get("general.log_level", "INFO")), cfg.state_dir / "logs" / "tradeagent.log")
    handlers = {
        "init": cmd_init,
        "doctor": cmd_doctor,
        "cycle": cmd_cycle,
        "loop": cmd_loop,
        "demo": cmd_demo,
        "status": cmd_status,
        "report": cmd_report,
        "publish-test": cmd_publish_test,
        "ticks": cmd_ticks,
        "replay": cmd_replay,
    }
    handler = handlers.get(args.cmd)
    if handler is None:  # pragma: no cover
        parser.error(f"دستور ناشناخته: {args.cmd}")
        return 2
    return handler(cfg, args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
