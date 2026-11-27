"""موتور معامله‌ی کاغذی + ژورنال SQLite.

قواعد صریح:

* فقط کاغذی. هیچ OrderSend، هیچ اتصال اجرایی، هیچ کلید API بروکر.
* سیگنال در **بسته‌شدن** کندل ۱ ثانیه‌ای گرفته می‌شود و پرکردن با همان قیمت بسته‌شدن
  به‌علاوه‌ی نیم‌اسپرد + اسلیپیج انجام می‌شود (محافظه‌کارانه، بدون نگاه به آینده).
* اگر در یک کندل هم استاپ و هم هدف لمس شوند، **استاپ** فرض می‌شود.
* سقف افت روزانه، سقف پوزیشن باز و کول‌داون هر نماد اعمال می‌شود.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

from . import features as feat
from .config import Config
from .strategy import Intent, evaluate
from .ticks import Bar
from .util import LOG, ensure_dir, utc_day, utc_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
    symbol TEXT NOT NULL,
    t INTEGER NOT NULL,
    open REAL, high REAL, low REAL, close REAL,
    bid_close REAL, ask_close REAL, spread_bps_max REAL,
    ticks INTEGER, volume REAL,
    PRIMARY KEY (symbol, t)
);
CREATE TABLE IF NOT EXISTS positions (
    symbol TEXT PRIMARY KEY,
    side INTEGER NOT NULL,
    entry_ts INTEGER NOT NULL,
    entry_price REAL NOT NULL,
    notional REAL NOT NULL,
    risk_amount REAL NOT NULL,
    sl_price REAL NOT NULL,
    tp_price REAL NOT NULL,
    sl_bps REAL NOT NULL,
    tp_bps REAL NOT NULL,
    hold_s INTEGER NOT NULL,
    variant TEXT NOT NULL,
    policy_version INTEGER NOT NULL,
    opened_cycle INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    side INTEGER NOT NULL,
    variant TEXT NOT NULL,
    entry_ts INTEGER NOT NULL,
    exit_ts INTEGER NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL NOT NULL,
    notional REAL NOT NULL,
    risk_amount REAL NOT NULL,
    pnl REAL NOT NULL,
    pnl_bps REAL NOT NULL,
    r_multiple REAL NOT NULL,
    exit_reason TEXT NOT NULL,
    sl_bps REAL NOT NULL,
    tp_bps REAL NOT NULL,
    hold_s INTEGER NOT NULL,
    policy_version INTEGER NOT NULL,
    cycle INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS policies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle INTEGER NOT NULL,
    ts TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle INTEGER NOT NULL,
    ts TEXT NOT NULL,
    source TEXT NOT NULL,
    action TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle INTEGER NOT NULL,
    ts TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trades_exit ON trades(exit_ts);
CREATE INDEX IF NOT EXISTS idx_bars_symbol_t ON bars(symbol, t);
"""


# --------------------------------------------------------------------------
# مدل‌ها
# --------------------------------------------------------------------------
@dataclass
class Position:
    symbol: str
    side: int
    entry_ts: int
    entry_price: float
    notional: float
    risk_amount: float
    sl_price: float
    tp_price: float
    sl_bps: float
    tp_bps: float
    hold_s: int
    variant: str
    policy_version: int
    opened_cycle: int

    def unrealized(self, last_price: float) -> float:
        if self.entry_price <= 0:
            return 0.0
        return self.side * (last_price / self.entry_price - 1.0) * self.notional

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Trade:
    symbol: str
    side: int
    variant: str
    entry_ts: int
    exit_ts: int
    entry_price: float
    exit_price: float
    notional: float
    risk_amount: float
    pnl: float
    pnl_bps: float
    r_multiple: float
    exit_reason: str
    sl_bps: float
    tp_bps: float
    hold_s: int
    policy_version: int
    cycle: int
    created_at: str = field(default_factory=utc_iso)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class CycleResult:
    cycle: int
    ts: str
    ts_epoch: int
    source: str
    policy: dict[str, Any]
    previous_policy: dict[str, Any] | None
    decisions: list[dict[str, Any]]
    closed_trades: list[dict[str, Any]]
    open_positions: list[dict[str, Any]]
    balance: float
    equity: float
    peak_equity: float
    drawdown_pct: float
    starting_balance: float
    overall_stats: dict[str, Any]
    today_stats: dict[str, Any]
    per_symbol: list[dict[str, Any]]
    ingest: list[dict[str, Any]]
    warnings: list[str]
    llm_note: str | None
    llm_meta: dict[str, Any]
    bars_processed: int
    entries_blocked: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# ذخیره‌سازی
# --------------------------------------------------------------------------
class Store:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        ensure_dir(self.path.parent)
        self.conn = sqlite3.connect(str(self.path), timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # -- meta ---------------------------------------------------------
    def get_meta(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except ValueError:
            return default

    def set_meta(self, key: str, value: Any) -> None:
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )
        self.conn.commit()

    # -- کندل‌ها -------------------------------------------------------
    def add_bars(self, symbol: str, bars: Sequence[Bar]) -> None:
        rows = [(symbol, *b.as_tuple()) for b in bars]
        self.conn.executemany(
            "INSERT OR IGNORE INTO bars(symbol,t,open,high,low,close,bid_close,ask_close,spread_bps_max,ticks,volume)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        self.conn.commit()

    def recent_bars(self, symbol: str, limit: int) -> list[Bar]:
        rows = self.conn.execute(
            "SELECT t,open,high,low,close,bid_close,ask_close,spread_bps_max,ticks,volume"
            " FROM bars WHERE symbol=? ORDER BY t DESC LIMIT ?",
            (symbol, int(limit)),
        ).fetchall()
        out = [
            Bar(
                t=int(r["t"]),
                open=float(r["open"]),
                high=float(r["high"]),
                low=float(r["low"]),
                close=float(r["close"]),
                bid_close=float(r["bid_close"] or 0.0),
                ask_close=float(r["ask_close"] or 0.0),
                spread_bps_max=float(r["spread_bps_max"] or 0.0),
                ticks=int(r["ticks"] or 0),
                volume=float(r["volume"] or 0.0),
            )
            for r in rows
        ]
        out.reverse()
        return out

    def symbols(self) -> list[str]:
        rows = self.conn.execute("SELECT DISTINCT symbol FROM bars ORDER BY symbol").fetchall()
        return [str(r["symbol"]) for r in rows]

    def resolve_symbol(self, symbol: str | None) -> str:
        """نامی که مدل می‌نویسد همیشه دقیقاً برابر نام فایل/دیتابیس نیست.

        ``XAUUSD`` در برابر ``XAUUSD_i`` و بالعکس، حروف کوچک/بزرگ، و پیشوند/پسوند بروکر.
        """
        name = (symbol or "").strip()
        if not name:
            return name
        known = {s.lower(): s for s in self.symbols()}
        if not known:
            return name
        low = name.lower()
        tries = [low]
        if low.endswith("_i"):
            tries.append(low[:-2])
        else:
            tries.append(low + "_i")
        tries += [low.replace(".", ""), low.replace("_", ""), low.split(".")[0]]
        for cand in tries:
            if cand in known:
                return known[cand]
        # تنها یک نماد در دیتابیس؟ همان را بده، ولی نامِ خواسته‌شده را لو بده
        if len(known) == 1:
            return next(iter(known.values()))
        for key, real in known.items():
            if key.startswith(low) or low.startswith(key):
                return real
        return name

    def bar_count(self, symbol: str) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS n FROM bars WHERE symbol=?", (symbol,)).fetchone()
        return int(row["n"]) if row else 0

    # -- پوزیشن -------------------------------------------------------
    def save_position(self, pos: Position) -> None:
        self.conn.execute(
            "INSERT INTO positions(symbol,side,entry_ts,entry_price,notional,risk_amount,sl_price,tp_price,"
            "sl_bps,tp_bps,hold_s,variant,policy_version,opened_cycle)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(symbol) DO UPDATE SET side=excluded.side, entry_ts=excluded.entry_ts,"
            " entry_price=excluded.entry_price, notional=excluded.notional, risk_amount=excluded.risk_amount,"
            " sl_price=excluded.sl_price, tp_price=excluded.tp_price, sl_bps=excluded.sl_bps,"
            " tp_bps=excluded.tp_bps, hold_s=excluded.hold_s, variant=excluded.variant,"
            " policy_version=excluded.policy_version, opened_cycle=excluded.opened_cycle",
            (
                pos.symbol, pos.side, pos.entry_ts, pos.entry_price, pos.notional, pos.risk_amount,
                pos.sl_price, pos.tp_price, pos.sl_bps, pos.tp_bps, pos.hold_s, pos.variant,
                pos.policy_version, pos.opened_cycle,
            ),
        )
        self.conn.commit()

    def delete_position(self, symbol: str) -> None:
        self.conn.execute("DELETE FROM positions WHERE symbol=?", (symbol,))
        self.conn.commit()

    def load_positions(self) -> dict[str, Position]:
        rows = self.conn.execute("SELECT * FROM positions").fetchall()
        out: dict[str, Position] = {}
        for r in rows:
            out[r["symbol"]] = Position(
                symbol=r["symbol"], side=int(r["side"]), entry_ts=int(r["entry_ts"]),
                entry_price=float(r["entry_price"]), notional=float(r["notional"]),
                risk_amount=float(r["risk_amount"]), sl_price=float(r["sl_price"]),
                tp_price=float(r["tp_price"]), sl_bps=float(r["sl_bps"]), tp_bps=float(r["tp_bps"]),
                hold_s=int(r["hold_s"]), variant=str(r["variant"]),
                policy_version=int(r["policy_version"]), opened_cycle=int(r["opened_cycle"]),
            )
        return out

    # -- معاملات ------------------------------------------------------
    def add_trade(self, trade: Trade) -> None:
        self.conn.execute(
            "INSERT INTO trades(symbol,side,variant,entry_ts,exit_ts,entry_price,exit_price,notional,"
            "risk_amount,pnl,pnl_bps,r_multiple,exit_reason,sl_bps,tp_bps,hold_s,policy_version,cycle,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                trade.symbol, trade.side, trade.variant, trade.entry_ts, trade.exit_ts, trade.entry_price,
                trade.exit_price, trade.notional, trade.risk_amount, trade.pnl, trade.pnl_bps,
                trade.r_multiple, trade.exit_reason, trade.sl_bps, trade.tp_bps, trade.hold_s,
                trade.policy_version, trade.cycle, trade.created_at,
            ),
        )
        self.conn.commit()

    def trades_since(self, since_ts: int, limit: int = 500) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM trades WHERE exit_ts >= ? ORDER BY exit_ts DESC LIMIT ?",
            (int(since_ts), int(limit)),
        ).fetchall()
        return [dict(r) for r in rows]

    def recent_trades(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM trades ORDER BY exit_ts DESC LIMIT ?", (int(limit),)).fetchall()
        return [dict(r) for r in rows]

    def stats(self, since_ts: int | None = None) -> dict[str, Any]:
        where = "WHERE exit_ts >= ?" if since_ts else ""
        params: tuple = (int(since_ts),) if since_ts else ()
        row = self.conn.execute(
            "SELECT COUNT(*) n, SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) wins,"
            " SUM(pnl) pnl, AVG(pnl_bps) avg_bps, AVG(r_multiple) avg_r FROM trades " + where,
            params,
        ).fetchone()
        n = int(row["n"] or 0)
        wins = int(row["wins"] or 0)
        losses = n - wins
        return {
            "trades": n,
            "wins": wins,
            "losses": losses,
            "win_rate": round((wins / n * 100.0) if n else 0.0, 2),
            "net_pnl": round(float(row["pnl"] or 0.0), 4),
            "avg_bps": round(float(row["avg_bps"] or 0.0), 3),
            "avg_r": round(float(row["avg_r"] or 0.0), 3),
        }

    def per_symbol_stats(self, min_trades: int = 1) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT symbol, COUNT(*) n, SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) wins, SUM(pnl) pnl,"
            " AVG(pnl_bps) avg_bps, AVG(r_multiple) avg_r FROM trades GROUP BY symbol ORDER BY symbol"
        ).fetchall()
        out = []
        for r in rows:
            n = int(r["n"] or 0)
            if n < min_trades:
                continue
            out.append(
                {
                    "symbol": r["symbol"],
                    "trades": n,
                    "wins": int(r["wins"] or 0),
                    "win_rate": round((int(r["wins"] or 0) / n * 100.0) if n else 0.0, 1),
                    "net_pnl": round(float(r["pnl"] or 0.0), 4),
                    "avg_bps": round(float(r["avg_bps"] or 0.0), 2),
                    "avg_r": round(float(r["avg_r"] or 0.0), 3),
                }
            )
        return out

    # -- سیاست/تصمیم/چرخه ---------------------------------------------
    def add_policy(self, cycle: int, policy: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO policies(cycle, ts, payload) VALUES(?,?,?)",
            (int(cycle), utc_iso(), json.dumps(policy, ensure_ascii=False)),
        )
        self.conn.commit()

    def add_decision(self, cycle: int, decision: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO decisions(cycle, ts, source, action, payload) VALUES(?,?,?,?,?)",
            (
                int(cycle),
                decision.get("ts", utc_iso()),
                str(decision.get("source", "")),
                str(decision.get("action", "")),
                json.dumps(decision, ensure_ascii=False),
            ),
        )
        self.conn.commit()

    def add_cycle(self, cycle: int, payload: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO cycles(cycle, ts, payload) VALUES(?,?,?)",
            (int(cycle), payload.get("ts", utc_iso()), json.dumps(payload, ensure_ascii=False)),
        )
        self.conn.commit()

    def latest_cycle(self) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT cycle FROM cycles ORDER BY id DESC LIMIT 1").fetchone()
        return {"cycle": int(row["cycle"])} if row else None

    def next_cycle_index(self) -> int:
        last = self.latest_cycle()
        return (last["cycle"] + 1) if last else 1


# --------------------------------------------------------------------------
# موتور
# --------------------------------------------------------------------------
class PaperEngine:
    def __init__(self, cfg: Config, store: Store) -> None:
        self.cfg = cfg
        self.store = store
        self.starting_balance = float(cfg.get("paper.starting_balance", 10000.0))
        self.risk_pct = float(cfg.get("paper.risk_per_trade_pct", 0.5))
        self.max_open = int(cfg.get("paper.max_open_positions", 4))
        self.daily_loss_stop_pct = float(cfg.get("paper.daily_loss_stop_pct", 3.0))
        self.slip_bps = float(cfg.get("paper.slippage_bps", 0.8))
        self.commission_bps = float(cfg.get("paper.commission_bps", 0.2))
        self.max_notional_multiple = float(cfg.get("paper.max_notional_multiple", 5.0))
        self.warmup = int(cfg.get("data.warmup_bars", 2400))
        self.max_bars = int(cfg.get("data.max_bars_per_cycle", 7200))

        if store.get_meta("balance") is None:
            store.set_meta("balance", self.starting_balance)
            store.set_meta("peak_equity", self.starting_balance)
            store.set_meta("streak_losses", 0)
            store.set_meta("streak_wins", 0)
            store.set_meta("day_key", utc_day())
            store.set_meta("day_start_balance", self.starting_balance)

    # -- وضعیت --------------------------------------------------------
    @property
    def balance(self) -> float:
        return float(self.store.get_meta("balance", self.starting_balance))

    @property
    def peak_equity(self) -> float:
        return float(self.store.get_meta("peak_equity", self.starting_balance))

    @property
    def streak_losses(self) -> int:
        return int(self.store.get_meta("streak_losses", 0))

    @property
    def streak_wins(self) -> int:
        return int(self.store.get_meta("streak_wins", 0))

    def _roll_day(self, now_ts: int) -> None:
        key = utc_day(now_ts)
        if self.store.get_meta("day_key") != key:
            self.store.set_meta("day_key", key)
            self.store.set_meta("day_start_balance", self.balance)
            self.store.set_meta("day_blocked", False)

    # -- ورود ---------------------------------------------------------
    def _open_position(
        self, symbol: str, bar: Bar, intent: Intent, policy: dict[str, Any], cycle: int, balance: float
    ) -> Position | None:
        if bar.close <= 0:
            return None
        risk_scale = float(policy.get("risk_scale", 1.0) or 1.0)
        risk_amount = balance * (self.risk_pct / 100.0) * risk_scale
        sl_frac = max(intent.sl_bps, 0.5) / 1e4
        notional = risk_amount / sl_frac
        notional = min(notional, balance * self.max_notional_multiple)
        if notional < balance * 1e-4:
            return None

        half_spread = bar.spread_bps / 2.0 / 1e4
        slip = self.slip_bps / 1e4
        entry = bar.close * (1 + intent.direction * (half_spread + slip))
        sl_price = entry * (1 - intent.direction * intent.sl_bps / 1e4)
        tp_price = entry * (1 + intent.direction * intent.tp_bps / 1e4)

        pos = Position(
            symbol=symbol,
            side=intent.direction,
            entry_ts=bar.t,
            entry_price=entry,
            notional=notional,
            risk_amount=risk_amount,
            sl_price=sl_price,
            tp_price=tp_price,
            sl_bps=intent.sl_bps,
            tp_bps=intent.tp_bps,
            hold_s=intent.hold_s,
            variant=intent.variant,
            policy_version=int(policy.get("version", 0)),
            opened_cycle=cycle,
        )
        commission = notional * self.commission_bps / 1e4
        self._set_balance(balance - commission)
        self.store.save_position(pos)
        self.store.set_meta(f"last_entry:{symbol}", bar.t)
        self.store.set_meta(f"last_intent:{symbol}", {"ts": utc_iso(bar.t), **intent.as_dict()})
        LOG.info(
            "ورود کاغذی %s %s @ %.6f (notional=%.2f، SL=%.1fbp، TP=%.1fbp، %s)",
            symbol, "خرید" if intent.direction > 0 else "فروش", entry, notional,
            intent.sl_bps, intent.tp_bps, intent.variant,
        )
        return pos

    def _set_balance(self, value: float) -> None:
        self.store.set_meta("balance", round(value, 6))

    # -- خروج ---------------------------------------------------------
    def _check_exit(self, pos: Position, bar: Bar, cycle: int) -> Trade | None:
        if pos.side > 0:
            hit_sl = bar.low <= pos.sl_price
            hit_tp = bar.high >= pos.tp_price
        else:
            hit_sl = bar.high >= pos.sl_price
            hit_tp = bar.low <= pos.tp_price

        exit_price: float | None = None
        reason = ""
        slip = self.slip_bps / 1e4
        if hit_sl:
            # محافظه‌کارانه: اگر هر دو در یک کندل لمس شدند، استاپ اول
            exit_price = pos.sl_price * (1 - pos.side * slip)
            reason = "sl"
        elif hit_tp:
            exit_price = pos.tp_price * (1 - pos.side * slip)
            reason = "tp"
        elif bar.t >= pos.entry_ts + pos.hold_s:
            half_spread = bar.spread_bps / 2.0 / 1e4
            exit_price = bar.close * (1 - pos.side * (half_spread + slip))
            reason = "time"
        if exit_price is None or exit_price <= 0:
            return None

        return self._finalize(pos, exit_price, bar.t, reason, cycle)

    def _finalize(self, pos: Position, exit_price: float, exit_ts: int, reason: str, cycle: int) -> Trade:
        """ثبت یک خروج (هر دلیلی) و به‌روزرسانی بالانس/استریک."""
        pnl = pos.side * (exit_price / pos.entry_price - 1.0) * pos.notional
        pnl -= pos.notional * self.commission_bps / 1e4
        pnl_bps = pos.side * (exit_price / pos.entry_price - 1.0) * 1e4
        r_multiple = pnl / pos.risk_amount if pos.risk_amount else 0.0

        trade = Trade(
            symbol=pos.symbol,
            side=pos.side,
            variant=pos.variant,
            entry_ts=pos.entry_ts,
            exit_ts=exit_ts,
            entry_price=round(pos.entry_price, 10),
            exit_price=round(exit_price, 10),
            notional=round(pos.notional, 6),
            risk_amount=round(pos.risk_amount, 6),
            pnl=round(pnl, 6),
            pnl_bps=round(pnl_bps, 4),
            r_multiple=round(r_multiple, 4),
            exit_reason=reason,
            sl_bps=pos.sl_bps,
            tp_bps=pos.tp_bps,
            hold_s=pos.hold_s,
            policy_version=pos.policy_version,
            cycle=cycle,
        )
        self.store.add_trade(trade)
        self.store.delete_position(pos.symbol)
        self._set_balance(self.balance + pnl)

        if pnl > 0:
            self.store.set_meta("streak_wins", self.streak_wins + 1)
            self.store.set_meta("streak_losses", 0)
        else:
            self.store.set_meta("streak_losses", self.streak_losses + 1)
            self.store.set_meta("streak_wins", 0)
        LOG.info(
            "خروج کاغذی %s %s (%s) pnl=%.2f (%.1fbp، %.2fR)",
            pos.symbol, "برد" if pnl > 0 else "باخت", reason, pnl, pnl_bps, r_multiple,
        )
        return trade

    # -- بستن موقعیت‌های رهاشده -----------------------------------------
    def _sweep_stale_positions(
        self,
        positions: dict[str, Position],
        cycle: int,
        now_ts: int,
        warnings: list[str],
    ) -> list[Trade]:
        """موقعیت‌هایی که داده‌شان قطع شده را رها نکن.

        تا پیش از این، خروج فقط داخل حلقهٔ کندل‌های *تازه* هر نماد بررسی می‌شد؛ اگر
        نمادی از فید می‌افتاد (قطعی اکسپرت، تعطیلی نماد، حذف از واچ‌لیست) موقعیتش
        برای همیشه باز می‌ماند - همان چیزی که دو پوزیشن ۱۸ ساعته ساخت و
        ``trades`` را صفر نگه داشت. حالا هر چرخه، موقعیت‌های گذشته از ``hold_s``
        با آخرین قیمت شناخته‌شده بسته می‌شوند.
        """
        if not now_ts:
            return []
        closed: list[Trade] = []
        timeout_s = 0.0
        late = 0.0
        for symbol, pos in list(positions.items()):
            age = now_ts - int(pos.entry_ts)
            if age < int(pos.hold_s):
                continue
            hist = self.store.recent_bars(symbol, 1)
            if hist:
                bar = hist[-1]
                half_spread = bar.spread_bps / 2.0 / 1e4
                exit_price = bar.close * (1 - pos.side * (half_spread + self.slip_bps / 1e4))
                if exit_price <= 0:
                    exit_price = pos.entry_price
                reason = "time"
                late = max(late, float(age - int(pos.hold_s)))
            else:
                exit_price = pos.entry_price
                reason = "stale"
                timeout_s = max(timeout_s, float(age))
            trade = self._finalize(pos, exit_price, now_ts, reason, cycle)
            positions.pop(symbol, None)
            closed.append(trade)
        if closed:
            reasons = {t.exit_reason for t in closed}
            warnings.append(
                f"{len(closed)} موقعیت رهاشده بسته شد "
                f"({'/'.join(sorted(reasons))}; تأخیر تا {max(late, timeout_s):.0f}s)"
            )
        return closed

    # -- چرخه ---------------------------------------------------------
    def process(
        self,
        bars_by_symbol: dict[str, list[Bar]],
        policy: dict[str, Any],
        cycle: int,
        now_ts: int | None = None,
    ) -> CycleResult:
        now_ts = int(now_ts or 0)
        self._roll_day(now_ts or max((b[-1].t for b in bars_by_symbol.values() if b), default=0))
        positions = self.store.load_positions()
        closed: list[Trade] = []
        warnings: list[str] = []
        # پیش از هر چیز: موقعیت‌هایی که داده‌شان مرده است را رها نکن
        closed.extend(self._sweep_stale_positions(positions, cycle, now_ts, warnings))
        bars_processed = 0
        entries_blocked: str | None = None

        day_start = float(self.store.get_meta("day_start_balance", self.starting_balance))
        if self.store.get_meta("day_blocked", False):
            entries_blocked = "سقف افت روزانه فعال است"
        elif day_start > 0 and (self.balance - day_start) / day_start * 100.0 <= -self.daily_loss_stop_pct:
            self.store.set_meta("day_blocked", True)
            entries_blocked = f"افت روزانه از {self.daily_loss_stop_pct}% گذشت"
            warnings.append(entries_blocked)

        for symbol, bars in bars_by_symbol.items():
            if not bars:
                continue
            self.store.add_bars(symbol, bars)
            limit = max(self.warmup, len(bars)) + 2
            hist = self.store.recent_bars(symbol, limit)
            if not hist:
                continue
            processed_key = f"processed:{symbol}"
            last_processed = int(self.store.get_meta(processed_key, 0) or 0)
            start_idx = len(hist)
            for i, bar in enumerate(hist):
                if bar.t > last_processed:
                    start_idx = i
                    break
            if len(hist) - start_idx > self.max_bars:
                warnings.append(f"{symbol}: فقط {self.max_bars} کندل آخر این چرخه پردازش شد")
                start_idx = len(hist) - self.max_bars

            for i in range(start_idx, len(hist)):
                bar = hist[i]
                bars_processed += 1
                pos = positions.get(symbol)
                if pos is not None:
                    trade = self._check_exit(pos, bar, cycle)
                    if trade is not None:
                        closed.append(trade)
                        positions.pop(symbol, None)
                    continue

                if entries_blocked or len(positions) >= self.max_open:
                    continue
                if not feat.ready(hist, i, min_history=max(320, int(policy.get("params", {}).get("lookback_s", 300)))):
                    continue

                cooldown = int(policy.get("params", {}).get("cooldown_s", 60))
                last_entry = int(self.store.get_meta(f"last_entry:{symbol}", 0) or 0)
                if bar.t - last_entry < cooldown:
                    continue

                f = feat.compute_features(hist, i)
                intent = evaluate(str(policy.get("variant")), f, policy.get("params", {}))
                if intent is None:
                    continue
                pos = self._open_position(symbol, bar, intent, policy, cycle, self.balance)
                if pos is not None:
                    positions[symbol] = pos

            self.store.set_meta(processed_key, max(last_processed, hist[-1].t))

        # -- ارزش‌گذاری ------------------------------------------------
        open_positions: list[dict[str, Any]] = []
        unrealized = 0.0
        for symbol, pos in positions.items():
            bars = self.store.recent_bars(symbol, 1)
            last_price = bars[-1].close if bars else pos.entry_price
            upnl = pos.unrealized(last_price)
            unrealized += upnl
            open_positions.append(
                {
                    **pos.as_dict(),
                    "last_price": round(last_price, 10),
                    "unrealized": round(upnl, 4),
                    "age_s": max(0, now_ts - pos.entry_ts) if now_ts else 0,
                }
            )

        equity = self.balance + unrealized
        peak = max(self.peak_equity, equity)
        self.store.set_meta("peak_equity", peak)
        drawdown = ((peak - equity) / peak * 100.0) if peak > 0 else 0.0

        today_ts = int(now_ts or 0)
        since_day = today_ts - (today_ts % 86400) if today_ts else 0
        today_stats = self.store.stats(since_ts=since_day) if since_day else self.store.stats(since_ts=0)

        return CycleResult(
            cycle=cycle,
            ts=utc_iso(now_ts or None),
            ts_epoch=now_ts,
            source="",
            policy=policy,
            previous_policy=None,
            decisions=[],
            closed_trades=[t.as_dict() for t in closed],
            open_positions=open_positions,
            balance=round(self.balance, 4),
            equity=round(equity, 4),
            peak_equity=round(peak, 4),
            drawdown_pct=round(drawdown, 3),
            starting_balance=self.starting_balance,
            overall_stats=self.store.stats(),
            today_stats=today_stats,
            per_symbol=self.store.per_symbol_stats(),
            ingest=[],
            warnings=warnings,
            llm_note=None,
            llm_meta={},
            bars_processed=bars_processed,
            entries_blocked=entries_blocked,
        )
