"""ایجنت ابزاردار: مدل زبانی + ابزارهای MCP (trade-mcp).

مشکل نسخهٔ قبلی: مدل فقط یک خلاصهٔ JSON می‌دید و باید از حافظه‌اش حدس می‌زد.
حالا خودش می‌تواند از سرور MCP داده بگیرد: تحلیل مولتی‌تایم‌فریم طلا، اسپرد،
اندازهٔ پوزیشن، ریسک، ژورنال — و بعد پیشنهاد بدهد.

قرارداد JSON با مدل (ساده و قابل اجرا روی هر مدل محلی):

    {
      "analysis": "یک پاراگراف کوتاه",
      "tool_calls": [{"tool": "analyze_market", "arguments": {"symbol": "XAUUSD"}}],
      "proposal": {"variant": "...", "params": {...}, "confidence": 0.0-1.0,
                   "reason": "..."} | null
    }

اگر ``tool_calls`` خالی نبود، ابزارها اجرا و نتیجه دوباره به مدل داده می‌شود؛ حداکثر
``max_rounds`` دور. ابزارهای اجرای سفارش مسدود هستند (این پروژه فقط کاغذی است).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
import re
from typing import Any, Callable, Sequence

LOG = logging.getLogger("tradeagent.agent")

# ابزارهایی که هرگز به مدل اجازهٔ صدا زدنشان را نمی‌دهیم: پروژه کاغذی است.
BLOCKED_TOOLS = {
    "propose_order",
    "propose_manage_position",
    "confirm_proposal",
    "cancel_proposal",
}

# ابزارهایی که «دستور» می‌دهند یا وضعیت را عوض می‌کنند؛ هر چیز دیگری فقط داده می‌خواند.
BLOCKED_PATTERN = re.compile(
    r"^(open|close|modify|delete|set|place|send|withdraw|deposit|propose|confirm|cancel)_"
    r"|_(order|orders)$|^propose_|^manage_|^panic$",
    re.IGNORECASE,
)

DEFAULT_ALLOW = [
    "mt5_status",
    "account_info",
    "find_symbol",
    "symbol_spec",
    "get_candles",
    "market_snapshot",
    "analyze_market",
    "position_size",
    "list_positions",
    "risk_report",
    "trade_history",
    "journal_list",
    "journal_add",
]

CONTRACT = """You are the trading agent of tradeAgent. Reply with ONE JSON object only:

{
  "analysis": "<2-4 sentences in Persian: what the data says right now>",
  "tool_calls": [{"tool": "<name>", "arguments": {<args>}}],
  "momentum": {
    "direction": "up|down|flat",
    "phase": "accelerating|steady|fading|reversing|unclear",
    "strength": <0.0-1.0>,
    "horizon_s": <5-900>,
    "confidence": <0.0-1.0>,
    "reason": "<one short sentence in Persian, citing the numbers you saw>"
  },
  "proposal": {"variant": "<momentum|mean_reversion|...>", "params": {<only changed keys>},
               "confidence": <0.0-1.0>, "reason": "<short, Persian>"} | null
}

Rules:
- "momentum" is YOUR judgment of the last seconds of ticks: where the candle is going,
  whether it is speeding up (accelerating), holding (steady) or losing steam (fading),
  and on what horizon. Only you decide it - no script does. Fill it in every final reply,
  also when there is no proposal and when the data is thin (use phase "unclear" + low
  confidence then). Never invent numbers you did not see in a tool result.
- If you need market data, ask for tools in "tool_calls" and set "proposal": null.
- You may call at most %(max_calls)d tools in total; batch independent ones in one reply.
- Change at most 20%% of any parameter, never touch risk or leverage.
- Ordering/execution tools are disabled: this system is paper-only.
- Prefer local_* tools (they read our own tick files) when MT5 tools answer
  'not_connected'. You always have tick data, so you can always judge momentum.
- The account trades exactly one instrument: "only_trade_this_symbol" in the context. Pass that
  string to every tool (never a shorter alias like XAUUSD if the context says XAUUSD_i), and if
  a tool returns prices that do not match "tick_digest.last_price", trust tick_digest and say so.
- Numbers ending in _bp are basis points: 1 bp = 0.01 percent. Say "12 bp", never "12 percent".
  Quote the tool numbers as they are; do not rescale them.
- "analysis" is ONE string (never an object), in Persian, not English.
- Write "analysis", "reason" and every human text in Persian (فارسی); keep symbol names,
  numbers and field names exactly as the tools returned them.
- If the data is insufficient (closed market, wide spread), say so in "analysis" and
  return "proposal": null (still give "momentum").
"""


@dataclass
class ToolCallRecord:
    tool: str
    arguments: dict[str, Any]
    ok: bool
    result: Any = None
    error: str = ""
    elapsed_ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "arguments": self.arguments,
            "ok": self.ok,
            "error": self.error,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass
class AgentTrace:
    proposal: dict[str, Any] | None = None
    momentum: dict[str, Any] | None = None
    analysis: str = ""
    calls: list[ToolCallRecord] = field(default_factory=list)
    rounds: int = 0
    error: str = ""
    repairs: int = 0
    last_reply: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "rounds": self.rounds,
            "analysis": self.analysis,
            "proposal": self.proposal,
            "momentum": self.momentum,
            "error": self.error,
            "last_reply": self.last_reply[-800:],
            "calls": [c.as_dict() for c in self.calls],
        }

    def summary(self) -> str:
        if self.error:
            return f"agent error: {self.error}"
        tools = ", ".join(c.tool for c in self.calls) or "none"
        return f"tools={tools} | rounds={self.rounds} | proposal={'yes' if self.proposal else 'no'}"


def parse_agent_reply(text: str) -> dict[str, Any]:
    """JSON مدل را بیرون بکش؛ مدل‌های کوچک گاهی متن اضافه می‌گذارند."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError):
        pass
    start = text.find("{")
    if start < 0:
        return {}
    depth = 0
    for idx in range(start, len(text)):
        if text[idx] == "{":
            depth += 1
        elif text[idx] == "}":
            depth -= 1
            if depth == 0:
                try:
                    data = json.loads(text[start : idx + 1])
                    return data if isinstance(data, dict) else {}
                except json.JSONDecodeError:
                    return {}
    return {}


def is_allowed_tool(name: str, allow: Sequence[str] | None, blocked: set[str]) -> bool:
    """ابزار خواندنی آزاد است، ابزار دستوری نه.

    قبلاً فقط فهرست سفید اجازه داشت و مدل برای MovingAverage/RelativeStrengthIndex خطای
    «مجاز نیست» می‌گرفت، در حالی که این‌ها فقط عدد برمی‌گردانند.
    """
    if not name:
        return False
    if name in blocked or BLOCKED_PATTERN.search(name):
        return False
    if allow is None:
        return True
    return name in allow


class ToolAgent:
    """حلقهٔ مدل ↔ ابزار، با سقف دور و سقف تعداد فراخوانی."""

    def __init__(
        self,
        chat: Callable[[list[dict[str, str]]], str],
        mcp: Any,
        allow: list[str] | None = None,
        max_rounds: int = 3,
        max_calls: int = 6,
        blocked: set[str] | None = None,
        local_tools: dict[str, Callable[..., Any]] | None = None,
    ) -> None:
        self.chat = chat
        self.mcp = mcp
        # ابزارهای محلی: از تیک‌های خودمان، بدون نیاز به متاتریدر/MCP
        self.local_tools: dict[str, Callable[..., Any]] = dict(local_tools or {})
        self.allow = list(allow) if allow else None  # None = هر ابزار خواندنی
        self.blocked = set(blocked or BLOCKED_TOOLS)
        self.max_rounds = max(1, int(max_rounds))
        self.max_calls = max(0, int(max_calls))

    # -- ابزارها --------------------------------------------------------
    LOCAL_TOOL_SPECS = {
        "local_tick_digest": {
            "description": "Numbers from the last seconds of ticks: returns per window, velocity change, up-tick fraction, spread, last closes.",
            "args": {"symbol": {"type": "string", "required": False}},
        },
        "local_candles": {
            "description": "Aggregated OHLC from our own tick files (seconds=1,5,15,60) with count.",
            "args": {
                "symbol": {"type": "string", "required": False},
                "seconds": {"type": "integer", "required": False},
                "count": {"type": "integer", "required": False},
            },
        },
    }

    def _local_specs(self) -> list[dict[str, Any]]:
        return [
            {"name": name, "description": spec["description"], "args": spec["args"]}
            for name, spec in self.LOCAL_TOOL_SPECS.items()
            if name in self.local_tools
        ]

    def available_tools(self) -> list[dict[str, Any]]:
        local = self._local_specs()
        if self.mcp is None:
            return local
        try:
            allowed = (
                [t for t in self.allow if t not in self.blocked] if self.allow is not None else None
            )
            tools = self.mcp.tools_for_prompt(allow=allowed)
        except Exception as exc:  # noqa: BLE001 - هر خطای سرور نباید چرخه را بکشد
            LOG.warning("listing MCP tools failed: %s", exc)
            tools = []
        return local + tools

    def _execute(self, call: dict[str, Any], budget: int) -> ToolCallRecord | None:
        name = str(call.get("tool") or call.get("name") or "").strip()
        args = call.get("arguments") or call.get("args") or {}
        if not isinstance(args, dict):
            args = {}
        if not name:
            return None
        if name in self.local_tools:
            if budget <= 0:
                return ToolCallRecord(tool=name, arguments=args, ok=False, error="سقف فراخوانی ابزار پر شد")
            import time

            started = time.time()
            try:
                result = self.local_tools[name](**args)
                return ToolCallRecord(
                    tool=name, arguments=args, ok=True, result=result,
                    elapsed_ms=int((time.time() - started) * 1000),
                )
            except Exception as exc:  # noqa: BLE001
                return ToolCallRecord(
                    tool=name, arguments=args, ok=False,
                    error=f"{type(exc).__name__}: {exc}",
                    elapsed_ms=int((time.time() - started) * 1000),
                )
        if not is_allowed_tool(name, self.allow, self.blocked):
            return ToolCallRecord(
                tool=name,
                arguments=args,
                ok=False,
                error="ابزار برای ایجنت مجاز نیست (کاغذی/فقط‌خواندنی)",
            )
        if budget <= 0:
            return ToolCallRecord(tool=name, arguments=args, ok=False, error="سقف فراخوانی ابزار پر شد")
        import time

        started = time.time()
        try:
            result = self.mcp.call_tool(name, args)
            return ToolCallRecord(
                tool=name,
                arguments=args,
                ok=True,
                result=result,
                elapsed_ms=int((time.time() - started) * 1000),
            )
        except Exception as exc:  # noqa: BLE001
            return ToolCallRecord(
                tool=name,
                arguments=args,
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
                elapsed_ms=int((time.time() - started) * 1000),
            )

    # -- اجرا -----------------------------------------------------------
    def run(self, context: dict[str, Any], skill_text: str = "") -> AgentTrace:
        trace = AgentTrace()
        tools = self.available_tools()
        system = CONTRACT % {"max_calls": self.max_calls}
        if tools:
            system += "\nAvailable tools:\n" + json.dumps(tools, ensure_ascii=False)
        if skill_text:
            system += "\n\nSkill instructions from trade-mcp:\n" + skill_text[:2000]

        messages: list[dict[str, str]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False, default=str)[:6000]},
        ]
        budget = self.max_calls
        repairs = 0
        nudges = 0
        for round_no in range(1, self.max_rounds + 1):
            trace.rounds = round_no
            try:
                reply = self.chat(messages)
            except Exception as exc:  # noqa: BLE001 - مدل ممکن است در دسترس نباشد
                trace.error = f"{type(exc).__name__}: {exc}"
                return trace
            trace.last_reply = (reply or "")[-2000:]
            data = parse_agent_reply(reply)
            if not data and repairs < 2:
                # مدل کوچک گاهی متن آزاد می‌نویسد؛ یک بار «فقط JSON» را یادآوری کن
                repairs += 1
                trace.repairs = repairs
                messages.append({"role": "assistant", "content": (reply or "")[:2000]})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "پاسخ قبلی JSON معتبر نبود. فقط و فقط یک آبجکت JSON طبق قرارداد "
                            'بفرست: {"analysis": "...", "tool_calls": [], "momentum": {...}, '
                            '"proposal": null}. هیچ متن اضافه‌ای بیرون JSON نگذار.'
                        ),
                    }
                )
                LOG.info("پاسخ مدل JSON نبود؛ تلاش ترمیمی %s", repairs)
                continue
            if not data:
                trace.error = "پاسخ مدل JSON نبود"
                trace.analysis = (reply or "").strip()[:400]
                return trace
            raw_analysis = data.get("analysis")
            if isinstance(raw_analysis, dict):
                # بعضی مدل‌ها به‌جای رشته، آبجکت می‌دهند
                analysis = "؛ ".join(
                    f"{k}: {v}" for k, v in raw_analysis.items()
                ).strip()
            else:
                analysis = str(raw_analysis or "").strip()
            if analysis:
                trace.analysis = analysis[:600]
            momentum = data.get("momentum")
            if isinstance(momentum, dict) and momentum:
                trace.momentum = momentum

            calls = data.get("tool_calls") or []
            if not isinstance(calls, list):
                calls = []
            records: list[ToolCallRecord] = []
            if budget <= 0 and calls:
                first = calls[0] if isinstance(calls[0], dict) else {}
                records.append(
                    ToolCallRecord(
                        tool=str(first.get("tool") or "?"),
                        arguments=dict(first.get("arguments") or {}),
                        ok=False,
                        error="سقف فراخوانی ابزار پر شد",
                    )
                )
            for call in calls[: max(0, budget)]:
                if not isinstance(call, dict):
                    continue
                rec = self._execute(call, budget)
                if rec is None:
                    continue
                budget -= 1 if rec.error != "سقف فراخوانی ابزار پر شد" else 0
                records.append(rec)
            trace.calls.extend(records)

            proposal = data.get("proposal")
            if isinstance(proposal, dict) and proposal:
                trace.proposal = proposal
                return trace
            if not records:
                if nudges < 2 and budget > 0:
                    nudges += 1
                    missing = []
                    if not trace.calls:
                        missing.append(
                            "a tool call - start with local_tick_digest (numbers from our own "
                            "tick files) and local_candles for seconds=1,5,15"
                        )
                    if not trace.momentum:
                        missing.append('"momentum" filled in, judging direction/phase/strength yourself')
                    if missing:
                        messages.append({"role": "assistant", "content": (reply or "")[:2000]})
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "Your JSON is missing: " + "; ".join(missing) +
                                    ". Reply again with the same JSON contract, now with those "
                                    "parts present. Persian text, numbers only from tool results."
                                ),
                            }
                        )
                        LOG.info("ناقص بود (%s)؛ درخواست تکمیل", ", ".join(missing))
                        continue
                return trace

            messages.append({"role": "assistant", "content": reply})
            messages.append(
                {
                    "role": "user",
                    "content": "Tool results:\n"
                    + json.dumps(
                        [
                            {
                                "tool": r.tool,
                                "ok": r.ok,
                                "result": r.result if r.ok else r.error,
                            }
                            for r in records
                        ],
                        ensure_ascii=False,
                        default=str,
                    )[:5000]
                    + "\nNow reply with the same JSON contract (analysis, tool_calls, proposal).",
                }
            )
            if budget <= 0:
                messages.append(
                    {"role": "user", "content": "Tool budget is exhausted. Give your final proposal now."}
                )
        return trace
