"""رابط خط فرمان TradeAgent."""

from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path
from html import escape as html_escape
from typing import Any, Sequence

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
    p_replay.add_argument("--fake", choices=["up", "down", "flat", "random", "momentum"], default=None,
                          help="پیش‌بینی‌کنندهٔ ساختگی/قاعده‌ای (تست بدون مدل)")
    p_replay.add_argument("--model", default=None, help="نام مدل ollama (پیش‌فرض: llm.ollama_model)")

    p_lab = sub.add_parser("lab", help="آزمایشگاه مدل آفلاین: تیک → کندل M1 → ویژگی → سنجش مدل‌ها + داشبورد")
    p_lab.add_argument("--symbol", default=None)
    p_lab.add_argument("--horizon", type=int, default=3, help="چند کندل M1 بعد پیش‌بینی شود (۲ تا ۳)")
    p_lab.add_argument("--flat-bp", type=float, default=3.0, help="کمتر از این حرکت = خنثی")
    p_lab.add_argument("--folds", type=int, default=5)
    p_lab.add_argument("--cost-bp", type=float, default=1.0)
    p_lab.add_argument("--epochs", type=int, default=250)
    p_lab.add_argument("--data", default=None, help="پوشهٔ داده (harvester یا logs با s1_ticks_*.jsonl)")
    p_lab.add_argument("--list", action="store_true", help="فقط نشان بده چه داده‌ای هست و از چه نمادی")
    p_lab.add_argument("--all", action="store_true", help="همهٔ نمادهای موجود در لاگ‌ها را بسنج و یک جدول بده")

    p_studio = sub.add_parser("studio", help="استودیو: ایجنت روی دادهٔ آفلاین جلو می‌رود + اپ زندهٔ چارت")
    p_studio.add_argument("--symbol", default=None)
    p_studio.add_argument("--data", default=None, help="پوشهٔ تیک‌ها یا logs (پیش‌فرض: جست‌وجو)")
    p_studio.add_argument("--step-min", type=int, default=1, help="هر چند دقیقه یک تصمیم")
    p_studio.add_argument("--horizon", type=int, default=3, help="افق: چند کندل M1 بعد")
    p_studio.add_argument("--limit", type=int, default=None, help="حداکثر چند تصمیم (برای آزمون سریع)")
    p_studio.add_argument("--flat-bp", type=float, default=3.0, help="کمتر از این حرکت = خنثی")
    p_studio.add_argument("--cost-bp", type=float, default=1.0, help="هزینهٔ فرضی هر معامله (bp)")
    p_studio.add_argument("--min-conf", type=float, default=0.0,
                          help="کمترین اطمینان برای باز کردن معاملهٔ کاغذی")
    p_studio.add_argument("--no-skip-frozen", action="store_true",
                          help="دقیقه‌های مرده (بازار بسته/قیمت ثابت) را هم حساب کن")
    p_studio.add_argument("--doctor", action="store_true",
                          help="فقط مدل را عیب‌یابی کن: چرا ollama جواب نمی‌دهد")
    p_studio.add_argument("--model", default=None, help="نام مدل ollama (مثلاً qwen2.5:1.5b-instruct)")
    p_studio.add_argument("--num-ctx", type=int, default=None,
                          help="اندازهٔ context مدل (کوچک‌تر = VRAM کمتر، مثلاً 2048)")
    p_studio.add_argument("--ollama-url", default=None,
                          help="آدرس ollama (مثلاً http://[::1]:11434 اگر IPv4 جواب نمی‌دهد)")
    p_studio.add_argument("--scan", action="store_true",
                          help="فقط بگو با این هزینه کدام افق جای سود دارد (بدون تصمیم‌گیری)")
    p_studio.add_argument("--rule", choices=["momentum", "trend"], default="momentum",
                          help="قاعدهٔ جایگزین مدل: momentum (۵ دقیقه) یا trend (۵ و ۱۵ دقیقه هم‌جهت)")
    p_studio.add_argument("--no-llm", action="store_true", help="بدون مدل: فقط قاعدهٔ تکانه")
    p_studio.add_argument("--serve", action="store_true", help="اپ را روی پورت بالا بیاور")
    p_studio.add_argument("--auto", action="store_true",
                          help="با --serve: کل تاریخ را خودت جلو برو (بدون منتظر ماندن برای دکمه‌ها)")
    p_studio.add_argument("--no-open", action="store_true", help="مرورگر را خودکار باز نکن")
    p_studio.add_argument("--warm", type=int, default=30, help="چند قدم قبل از باز شدن اپ برود")
    p_studio.add_argument("--port", type=int, default=8620)
    p_studio.add_argument("--out", default="var/studio")
    p_lab.add_argument("--out", default="var/lab")
    p_lab.add_argument("--serve", action="store_true", help="بعد از ساخت، داشبورد را روی پورت بالا بیاور")
    p_lab.add_argument("--port", type=int, default=8610)

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


def cmd_lab(cfg: Any, args: Any) -> int:
    from .lab import discover_tick_dirs, find_tick_files, probe_tick_file, run as run_lab

    symbol = args.symbol or (cfg.symbols or ["XAUUSD_i"])[0]

    if getattr(args, "list", False):
        dirs = discover_tick_dirs(symbol, [Path(args.data).expanduser()] if args.data else [])
        if not dirs:
            print("  پوشه‌ای با فایل‌های s1_ticks_*.jsonl پیدا نشد.")
            print("  با --data مسیر را بده، مثلاً: ./scripts/lab.sh --list --data ~/trade-mc/logs")
            return 1
        for d in dirs[:6]:
            files = sorted(d.glob("s1_ticks_*.jsonl"))
            symbols = sorted({f.name.split("_ticks_")[1].rsplit("_", 1)[0] for f in files if "_ticks_" in f.name})
            print(f"  {d}")
            print(f"    {len(files)} فایل · نمادها: {', '.join(symbols[:12])}")
            for f in files[:2]:
                print(f"    نمونه: {f.name} ({f.stat().st_size / 1e6:.1f}MB) کلیدها: {probe_tick_file(f).get('keys')}")
        return 0

    if getattr(args, "all", False):
        return cmd_lab_all(cfg, args)

    if args.data:
        root = Path(args.data).expanduser()
    else:
        root = Path("").resolve()      # جست‌وجو در کنار مخزن + مسیرهای رایج (trade-mc/logs)
    found = find_tick_files(root, symbol)
    logs = [d for d in discover_tick_dirs(symbol, [root] if args.data else []) if str(d) != str(root)]
    print(f"═══ آزمایشگاه مدل: {symbol} · افق {args.horizon} کندل M1 ═══")
    if found:
        first = probe_tick_file(found[0])
        print(f"  داده: {len(found)} فایل {first.get('kind')} در {root}")
    if logs:
        sample = next(iter(sorted(logs[0].glob(f"s1_ticks_{symbol}*.jsonl"))), None) or \
            next(iter(sorted(logs[0].glob("s1_ticks_*.jsonl"))), None)
        keys = probe_tick_file(sample).get("keys") if sample else []
        print(f"  لاگ ربات: {logs[0]} ({len(list(logs[0].glob('s1_ticks_*.jsonl')))} فایل) کلیدها: {keys}")
    if not found and not logs:
        print("  داده‌ای پیدا نشد؛ با --data مسیر پوشهٔ تیک‌ها یا logs را بده.")

    data = run_lab(root, symbol, horizon=args.horizon, flat_bp=args.flat_bp, n_folds=args.folds,
                   cost_bp=args.cost_bp, epochs=args.epochs, out_dir=cfg.path(args.out, args.out),
                   serve=False, port=args.port)
    if not data.get("ok"):
        print("  " + str(data.get("error")))
        return 1

    print(f"  منبع: {data.get('source')}")
    print(f"  تیک: {data.get('ticks'):,} · بازه: {data.get('span')} · کندل M1: {data['candles_n']}")
    print(f"  نمونهٔ آموزش/آزمون: {data['samples']} در {data['folds_n']} فولد (افق {data['horizon']} کندل)")
    print("  ─" * 24)
    for row in data["summary"]:
        print(f"   {row['name']:<16} دقت {row['accuracy'] * 100:5.1f}%  "
              f"معامله {row['bets']:4d}  برد {row['win_pct']:5.1f}%  "
              f"خالص {row['net_bp']:+8.1f}bp  میانگین {row['avg_bp']:+.2f}bp")
    if data.get("verdict"):
        print("  ─" * 24)
        print(f"  نتیجه: {data['verdict']}")
    if data.get("importance"):
        top = ", ".join(i["feature"] for i in data["importance"][:6])
        print(f"  مهم‌ترین ویژگی‌ها: {top}")
    print(f"  گزارش: {data['html_path']}")
    print(f"  داشبورد: {'http://127.0.0.1:%d' % args.port if args.serve else 'با --serve بالا می‌آید'}")
    if args.serve:
        from .lab import serve_lab

        serve_lab(Path(data["html_path"]).parent, port=args.port)
    return 0


def cmd_lab_all(cfg: Any, args: Any) -> int:
    """همهٔ نمادهای موجود در لاگ ربات: یک ردیف برای هر نماد."""
    from .lab import discover_tick_dirs, run as run_lab, symbols_in_logs

    base = Path(args.data).expanduser() if args.data else Path("").resolve()
    dirs = discover_tick_dirs(args.symbol or "XAUUSD_i", [base] if args.data else [])
    if not dirs:
        dirs = [base] if base.is_dir() else []
    symbols = symbols_in_logs(dirs) or []
    if not symbols:
        print("  نمادی در لاگ‌ها پیدا نشد. با --data مسیر پوشهٔ لاگ را بده.")
        return 1
    print(f"═══ آزمایشگاه روی {len(symbols)} نماد · افق {args.horizon} کندل M1 ═══")
    print("   نماد            تیک     کندل   مدل%   قاعده%   خالص مدل   خالص قاعده   نتیجه")
    rows = []
    out_root = cfg.path(args.out, args.out)
    for sym in symbols:
        data = run_lab(base, sym, horizon=args.horizon, flat_bp=args.flat_bp, n_folds=args.folds,
                       cost_bp=args.cost_bp, epochs=args.epochs, out_dir=out_root / sym,
                       serve=False, port=args.port)
        if not data.get("ok"):
            print(f"   {sym:<14} —  {data.get('error')}")
            continue
        summary = {r["name"]: r for r in data["summary"]}
        model = summary.get("رگرسیون لجستیک", {})
        rule = summary.get("قاعدهٔ تکانه", {})
        best = data["summary"][0] if data["summary"] else {}
        print(f"   {sym:<14} {data['ticks'] or 0:>7,} {data['candles_n']:>6}  "
              f"{model.get('accuracy', 0) * 100:5.1f}%  {rule.get('accuracy', 0) * 100:5.1f}%  "
              f"{model.get('net_bp', 0):+9.1f}  {rule.get('net_bp', 0):+10.1f}   {best.get('name', '')}")
        rows.append((sym, data))
    if not rows:
        return 1
    index = out_root / "index.html"
    index.parent.mkdir(parents=True, exist_ok=True)
    links = "".join(
        f'<li><a href="{html_escape(sym)}/index.html">{html_escape(sym)}</a> — '
        f'{d["candles_n"]} کندل · {d.get("verdict", "")}</li>'
        for sym, d in rows
    )
    index.write_text(
        '<!doctype html><html lang="fa" dir="rtl"><meta charset="utf-8">'
        "<title>آزمایشگاه مدل — همهٔ نمادها</title>"
        "<body style=\"background:#0b1120;color:#e2e8f5;font-family:system-ui,Tahoma;padding:22px\">"
        f"<h1>آزمایشگاه مدل — {len(rows)} نماد</h1>"
        "<p style='color:#8ba0c7'>افق: " + str(args.horizon) + " کندل M1 · هزینه: "
        + str(args.cost_bp) + " bp</p><ul>" + links + "</ul></body></html>",
        encoding="utf-8",
    )
    print(f"  فهرست: {index}")
    return 0


def cmd_studio(cfg: Any, args: Any) -> int:
    """ایجنت کندل‌به‌کندل روی داده جلو می‌رود و اپ زنده نشان می‌دهد."""
    import threading

    from .lab import discover_tick_dirs, find_tick_files, probe_tick_file
    from .llm import LLMAdvisor
    from .studio import Studio, serve

    import sys

    try:                                     # خروجی خط‌به‌خط، تا در فایل هم زنده دیده شود
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass

    symbol = args.symbol or (cfg.symbols or ["XAUUSD_i"])[0]
    root = Path(args.data).expanduser() if args.data else Path("").resolve()
    out_dir = cfg.path(args.out, args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if getattr(args, "doctor", False):
        from .llm import LLMAdvisor
        from .llmdiag import best_url, diagnose, format_report, verdict

        advisor = LLMAdvisor(cfg, cfg.state_dir / "llm_calls.json")
        model = args.model or advisor.ollama_model
        url = args.ollama_url or advisor.ollama_url
        print(f"═══ عیب‌یابی مدل: {model} روی {url} ═══")
        probes = diagnose(url, model)
        print(format_report(probes, verdict(probes, model)))
        fixed = best_url(probes)
        if fixed and fixed != url:
            print(f"  برای همین اجرا: ./scripts/studio.sh --serve --ollama-url {fixed}")
        return 0

    print(f"═══ استودیو: {symbol} · گام {args.step_min} دقیقه · افق {args.horizon} کندل ═══")
    found = find_tick_files(root, symbol)
    logs = [d for d in discover_tick_dirs(symbol, [root] if args.data else []) if str(d) != str(root)]
    if not found and logs:
        # داده در پوشهٔ logs است؛ همان‌جا ریشهٔ داده شود
        root, found = logs[0], find_tick_files(logs[0], symbol)
    print(f"  داده: {root} · {len(found)} فایل ({probe_tick_file(found[0]).get('kind') if found else '?'})")
    if not found:
        print("  فایل تیکی پیدا نشد؛ با --data مسیر logs را بده.")
        return 1

    if getattr(args, "scan", False):
        from .studio import render_scan

        studio = Studio(root, symbol, out_dir, chat=None, step_min=args.step_min,
                        horizon=args.horizon, flat_bp=args.flat_bp, extra_dirs=logs,
                        cost_bp=args.cost_bp)
        rows = studio.scan()
        if not rows:
            print("  کندل کافی نیست.")
            return 1
        cov = studio.coverage()
        print(f"  داده: {cov['files']} فایل · {cov['ticks']:,} تیک · {cov['candles']} کندل M1 · شکاف {cov['gaps']}")
        print("  " + render_scan(rows, args.cost_bp).replace("\n", "\n  "))
        best = [r for r in rows if r["viable"]]
        if best:
            print(f"  کمترین افق که جای سود دارد: {best[0]['horizon']} کندل "
                  f"(میانگین {best[0]['avg_abs_bp']}bp در برابر هزینهٔ {args.cost_bp}bp)")
        else:
            print(f"  هیچ افقی تا {rows[-1]['horizon']} کندل میانگین حرکتش از ۲ برابر هزینه "
                  f"({args.cost_bp * 2:g}bp) بیشتر نیست؛ با این هزینه معامله معنا ندارد.")
        return 0

    chat = None
    if not args.no_llm:
        if args.model:
            cfg.set("llm.ollama_model", args.model)
        if args.ollama_url:
            cfg.set("llm.ollama_url", args.ollama_url)
        if args.num_ctx:
            cfg.set("llm.ollama_num_ctx", args.num_ctx)
        if getattr(args, "max_calls_per_hour", None) is not None:
            cfg.set("llm.max_calls_per_hour", args.max_calls_per_hour)
        advisor = LLMAdvisor(cfg, cfg.state_dir / "llm_calls.json")
        ok, why = advisor.preflight()
        if not ok and not args.ollama_url:
            from .llmdiag import candidate_urls

            for alt in candidate_urls(advisor.ollama_url)[1:]:
                cfg.set("llm.ollama_url", alt)
                try_advisor = LLMAdvisor(cfg, cfg.state_dir / "llm_calls.json")
                ok_alt, why_alt = try_advisor.preflight()
                if ok_alt:
                    advisor, ok, why = try_advisor, True, why_alt
                    print(f"  مدل روی {alt} جواب داد؛ همان استفاده می‌شود.")
                    break
        if ok:
            print(f"  مدل: آماده است · {advisor.ollama_model} · سقف فراخوانی: {advisor.quota_label()}")

            def chat(messages):  # noqa: ANN001, ANN202
                return advisor.chat(messages)
        else:
            print(f"  مدل جواب نداد: {why[:120]}")
            print("  ─ با قاعدهٔ تکانه جلو می‌رود و در جدول، منبع هر تصمیم «rule» نوشته می‌شود.")
            print("  ─ برای فهمیدن علت: ./scripts/studio.sh --doctor")
            print("  ─ یا مدل کوچک‌تر: ./scripts/studio.sh --serve --model qwen2.5:1.5b-instruct")

    studio = Studio(root, symbol, out_dir, chat=chat, step_min=args.step_min,
                    horizon=args.horizon, flat_bp=args.flat_bp, min_conf=args.min_conf,
                    extra_dirs=logs, skip_frozen=not args.no_skip_frozen)
    if args.rule == "trend" and chat is None:
        from .studio import trend_rule

        studio.rule = lambda i: trend_rule(studio.candles, i, studio.flat_bp)

    def progress(info: dict[str, Any]) -> None:
        eta = info.get("eta_s") or 0
        print(f"  پیشرفت: {info['made']}/{info['total']} · دقت تا اینجا {info['hit_pct']}% · "
              f"خالص {info['net_bp']:+.0f}bp · ~{eta // 60} دقیقه مانده", flush=True)

    if args.serve:
        # چند قدم اول را برو تا اپ از همان اول چارت و تصمیم داشته باشد
        warm = max(1, int(args.warm))
        studio.walk(limit=warm)
        threading.Thread(target=serve, args=(out_dir, symbol, args.port),
                         kwargs={"studio": studio}, daemon=True).start()
        url = f"http://127.0.0.1:{args.port}"
        print("  " + "─" * 40)
        print(f"  اپ آماده است: {url}")
        print("  دکمه‌های «قدم بعد / ۵ قدم / تا آخر» ایجنت را جلو می‌برند.")
        print(f"  گزارش بازبینی: {url}/review.md  (دکمهٔ 📄 بالای چارت)")
        print("  " + "─" * 40)
        if not args.no_open:
            try:
                import webbrowser

                webbrowser.open(url)
            except Exception:  # noqa: BLE001
                pass
        if not args.auto:
            print("  تا وقتی این پنجره باز است اپ کار می‌کند (Ctrl+C برای بستن).")
            try:
                while True:
                    time.sleep(3600)
            except KeyboardInterrupt:
                print("  بسته شد.")
                return 0

    result = studio.walk(limit=args.limit, progress=progress)
    if not result.get("ok"):
        print("  " + str(result.get("error")))
        return 1
    print(f"  کندل: {result['candles']} · تصمیم: {result['predictions']} · سنجیده: {result['scored']}")
    print(f"  دقت جهت: {result['hit_pct']}% · معامله: {result['bets']} · خالص: {result['net_bp']:+.1f}bp "
          f"· میانگین: {result['avg_bp']:+.2f}bp")
    trades = studio.trade_stats()
    print(f"  معاملات کاغذی: {trades['closed']} بسته · {trades['open']} باز · برد {trades['win_pct']}% · "
          f"خالص {trades['net_bp']:+.1f}bp · هزینهٔ پرداختی {trades['cost_paid_bp']:.0f}bp")
    if result.get("failures"):
        print(f"  خطای مدل: {result['failures']} (آخرین: {result['last_error']})")
    print(f"  گزارش بازبینی (همین را بفرست): {out_dir / 'review.md'}")
    print(f"  نسخهٔ CSV/JSON: {out_dir / 'review.csv'} · {out_dir / 'review.json'}")
    print(f"  وضعیت: {out_dir / 'state.json'}")
    print(f"  پیش‌بینی‌ها: {out_dir / 'predictions.jsonl'}")
    return 0


def cmd_replay(cfg: Config, args: argparse.Namespace) -> int:
    import random

    from .loop import resolve_harvester_dir
    from .replay import Replay, load_cache, model_predict, save_cache

    root = resolve_harvester_dir(cfg.path("data.harvester_dir", "~/mt5/MQL5/Files/harvester"))
    symbol = args.symbol or (cfg.symbols or ["XAUUSD_i"])[0]
    out_dir = cfg.path(args.out, args.out)
    if args.model:
        cfg.set("llm.ollama_model", args.model)

    if args.fake:
        def predictor(bars, horizon):  # noqa: ANN001, ANN202
            if args.fake == "momentum":
                ref = bars[-min(len(bars), 300)]
                direction = "up" if bars[-1].close > ref.close else "down" if bars[-1].close < ref.close else "flat"
                analysis = f"قاعده: مقایسهٔ قیمت با ۵ دقیقهٔ قبل ({direction})"
            else:
                direction = args.fake if args.fake != "random" else random.choice(["up", "down", "flat"])
                analysis = f"پیش‌بینی ساختگی: {direction}"
            return {"direction": direction, "phase": "steady", "strength": 0.5,
                    "confidence": 0.5, "horizon_s": horizon, "expected_bp": 0.0,
                    "analysis": analysis}
    else:
        advisor = LLMAdvisor(cfg, cfg.state_dir / "llm_calls.json")
        ok, why = False, ""
        for attempt in range(1, 6):  # مدل ممکن است همین حالا در حال بارگذاری باشد
            ok, why = advisor.preflight()
            if ok:
                break
            print(f"  تلاش {attempt}/5: {why}")
            time.sleep(6)
        if not ok:
            print("  مدل جواب نمی‌دهد. این دو خط را بزن و خروجی‌شان را بفرست:")
            print("    curl -sS -o /tmp/oll.json -w '%{http_code}\n' -X POST "
                  f"{advisor.ollama_url}/api/chat -d '{{\"model\":\"{advisor.ollama_model}\","
                  "\"messages\":[{\"role\":\"user\",\"content\":\"سلام\"}],\"stream\":false}'; cat /tmp/oll.json")
            print("    journalctl --user -u ollama -n 25 --no-pager")
            return 1
        print(f"  مدل آماده است: {why}")

        cache = load_cache(out_dir / "llm_cache.json")

        def predictor(bars, horizon):  # noqa: ANN001, ANN202
            return model_predict(advisor.chat, bars, horizon, cache)

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
    if stats.get("failures"):
        print(f"  پیش‌بینی‌های ناموفق: {stats['failures']} · آخرین خطا: {stats.get('last_error')}")
    if not stats.get("steps") and stats.get("failures"):
        print("  هیچ پیش‌بینی‌ای ساخته نشد؛ اول مدل را بالا بیاور (ollama serve) بعد دوباره.")
        return 1
    print(f"  پیش‌بینی: {stats['steps']} · سنجیده: {stats['scored']} · درست: {stats['hit_pct']}% "
          f"· میانگین خالص: {stats['avg_net_bp']}bp · {stats['elapsed_s']}s")
    for direction, bucket in sorted((stats.get("by_direction") or {}).items()):
        print(f"    {direction}: n={bucket['n']} · درست {bucket['hit_pct']}% · میانگین {bucket['avg_bp']}bp")
    if not args.fake:
        save_cache(out_dir / "llm_cache.json", cache)
    print(f"  گزارش: {out_dir / 'index.html'}")
    if args.publish and not args.fake:
        pub = Publisher(cfg, cfg.root).publish(
            {
                "backtest/index.html": (out_dir / "index.html").read_text(encoding="utf-8"),
                "backtest/predictions.jsonl": (out_dir / "predictions.jsonl").read_text(encoding="utf-8"),
                "backtest/stats.json": (out_dir / "stats.json").read_text(encoding="utf-8"),
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
        "lab": cmd_lab,
        "studio": cmd_studio,
    }
    handler = handlers.get(args.cmd)
    if handler is None:  # pragma: no cover
        parser.error(f"دستور ناشناخته: {args.cmd}")
        return 2
    return handler(cfg, args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
