"""مشاور مدل زبانی: Gemini رایگان یا Ollama محلی.

قواعد:

* مدل **هرگز** مستقیم معامله نمی‌کند؛ فقط یک پیشنهاد JSON برای سیاست می‌دهد.
* خروجی مدل با :func:`tradeagent.policy.validate_proposal` سنجیده می‌شود.
* اگر کلید/سرور نبود، پاسخ خراب بود یا سهمیه تمام شد، سیستم با سیاست قطعی ادامه می‌دهد.
* تعداد فراخوانی‌ها ساعتی محدود است (بودجه‌ی محلی، در فایل state ذخیره می‌شود).
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .config import Config
from .util import LOG, read_json, utc_iso, write_json

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"

_SUGGEST_RE = re.compile(r"models/([A-Za-z0-9._-]{3,})")

# مدل‌هایی که متنِ JSON برنمی‌گردانند (گفتار، تصویر، امبدینگ…) برای مشاور بی‌فایده‌اند
NON_TEXT_TOKENS = ("tts", "image", "imagen", "veo", "embed", "audio", "live", "native", "aqa")


def is_text_model(name: str) -> bool:
    low = (name or "").lower()
    return bool(low) and not any(tok in low for tok in NON_TEXT_TOKENS)


def suggested_model(error_body: str) -> str | None:
    """مدلی که خودِ گوگل در پیام خطا پیشنهاد می‌کند (مثلاً وقتی مدلی بازنشسته شده)."""
    text = error_body or ""
    if "no longer available" not in text.lower() and "use models/" not in text.lower():
        return None
    for name in _SUGGEST_RE.findall(text):
        if name.startswith("gemini-2.5") or not is_text_model(name):
            continue
        return name
    return None

SYSTEM_PROMPT = """تو یک مشاور ریسک برای یک سیستم معامله‌ی کاغذی (paper trading) هستی.
فقط JSON معتبر برگردان، بدون متن اضافه. ساختار مجاز:

{"variant": "momentum|meanrev|scalp",
 "params": {"breakout_k": 1.1, "sl_atr": 1.4, "rr": 1.5, "hold_s": 900,
            "min_edge_bps": 2.5, "max_spread_bps": 5.0, "cooldown_s": 90,
            "z_entry": 2.0, "max_vol_bps": 70, "lookback_s": 300},
 "reason": "یک یا دو جمله فارسی",
 "confidence": 0.0-1.0,
 "notes": ["حداکثر سه نکته"]}

قواعد سخت: هر پارامتر حداکثر ۲۰٪ نسبت به مقدار فعلی جابه‌جا شود؛ هیچ کلید دیگری
اضافه نکن؛ ریسک، اهرم، سقف ضرر روزانه و لیست نمادها قابل تغییر نیستند. اگر داده کافی
نیست، همان سیاست فعلی را با تغییر صفر و confidence پایین برگردان."""


@dataclass
class LLMResult:
    proposal: dict[str, Any] | None
    meta: dict[str, Any] = field(default_factory=dict)


def http_post_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", **headers}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - آدرس از پیکربندی می‌آید
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw) if raw else {}


class LLMAdvisor:
    """فراخوانی محدود و بی‌خطر مدل؛ همیشه با fallback قطعی."""

    def __init__(
        self,
        cfg: Config,
        state_path: Path,
        transport: Callable[[str, dict[str, Any], dict[str, str], float], dict[str, Any]] = http_post_json,
    ) -> None:
        self.cfg = cfg
        self.state_path = Path(state_path)
        self.transport = transport
        self.enabled = bool(cfg.get("llm.enabled", True))
        self.provider = str(cfg.get("llm.provider", "none") or "none").lower()
        self.model = str(cfg.get("llm.model", "gemini-3.8-flash"))
        self.ollama_model = str(cfg.get("llm.ollama_model", "qwen2.5:7b-instruct"))
        # فقط مدلِ همان provider اهمیت دارد؛ هشدار مدل gemini روی حالت ollama گمراه‌کننده بود
        if self.provider == "gemini" and not is_text_model(self.model):
            LOG.warning(
                "مدل %s متنی نیست (گفتار/تصویر)؛ ./scripts/setup_llm.sh مدل متنی انتخاب می‌کند",
                self.model,
            )
        if self.provider == "ollama" and not is_text_model(self.ollama_model):
            LOG.warning("مدل ollama «%s» متنی نیست؛ یک مدل متنی بگذار", self.ollama_model)
        self.ollama_url = str(cfg.get("llm.ollama_url", "http://127.0.0.1:11434")).rstrip("/")
        self.timeout = float(cfg.get("llm.timeout_s", 45))
        self.every_cycles = max(1, int(cfg.get("llm.every_cycles", 3)))
        self.max_calls_per_hour = max(0, int(cfg.get("llm.max_calls_per_hour", 12)))
        self.key_env = str(cfg.get("llm.gemini_api_key_env", "GEMINI_API_KEY"))

    # -- وضعیت ---------------------------------------------------------
    def api_key(self) -> str:
        return os.environ.get(self.key_env, "") or os.environ.get("GOOGLE_API_KEY", "")

    def available(self) -> tuple[bool, str]:
        if not self.enabled or self.provider in ("", "none", "off"):
            return False, "مدل زبانی غیرفعال است"
        if self.provider == "gemini" and not self.api_key():
            return False, f"متغیر {self.key_env} تنظیم نشده"
        if self.provider == "ollama" and not self.ollama_url.startswith("http"):
            return False, "آدرس Ollama نامعتبر است"
        return True, "ok"

    def _calls(self) -> list[float]:
        raw = read_json(self.state_path, {"calls": []}) or {}
        calls = raw.get("calls", []) if isinstance(raw, dict) else []
        return [float(c) for c in calls if isinstance(c, (int, float))]

    def _record_call(self) -> None:
        now = time.time()
        calls = [c for c in self._calls() if now - c < 3600] + [now]
        write_json(self.state_path, {"calls": calls, "last_call": utc_iso(now)})

    def calls_last_hour(self) -> int:
        now = time.time()
        return len([c for c in self._calls() if now - c < 3600])

    def should_consult(self, cycle: int, force: bool = False) -> bool:
        ok, _ = self.available()
        if not ok:
            return False
        if force:
            return True
        if cycle % self.every_cycles != 0:
            return False
        return self.calls_last_hour() < self.max_calls_per_hour

    # -- فراخوانی ------------------------------------------------------
    def _build_prompt(self, context: dict[str, Any]) -> str:
        return (
            "وضعیت فعلی سیستم معامله‌ی کاغذی:\n"
            + json.dumps(context, ensure_ascii=False, indent=1)
            + "\n\nفقط JSON بده."
        )

    def _gemini_post(self, key: str, payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """POST با دو سیاست: ۵۰۳/۴۲۹ را دوباره، ۴۰۴ را با مدلِ پیشنهادی گوگل."""
        meta: dict[str, Any] = {}
        url = GEMINI_URL.format(model=self.model, key=key)
        for attempt in range(3):
            try:
                return self.transport(url, payload, {}, self.timeout), meta
            except urllib.error.HTTPError as exc:
                raw = ""
                try:
                    raw = exc.read().decode("utf-8", errors="replace")
                except Exception:  # noqa: BLE001 - متن خطا اختیاری است
                    raw = ""
                if exc.code == 503 and attempt == 0:
                    LOG.warning("مدل %s موقتاً شلوغ است (HTTP 503) - ۳ ثانیه بعد دوباره", self.model)
                    time.sleep(3)
                    continue
                new_model = suggested_model(raw) if exc.code == 404 else None
                if new_model and new_model != self.model:
                    LOG.warning(
                        "مدل %s در دسترس نیست؛ گوگل %s را پیشنهاد داد - همان را امتحان می‌کنم",
                        self.model, new_model,
                    )
                    self.model = new_model
                    meta["model_switched_to"] = new_model
                    url = GEMINI_URL.format(model=self.model, key=key)
                    continue
                if exc.code == 429:
                    meta["quota"] = raw[:300]
                if raw:
                    exc.args = (*exc.args, raw[:400])
                raise
        raise RuntimeError("gemini: نتوانستم مدل مناسبی پیدا کنم")

    def _call_gemini(self, prompt: str) -> tuple[str, dict[str, Any]]:
        key = self.api_key()
        payload = {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.35,
                "maxOutputTokens": 900,
                "responseMimeType": "application/json",
            },
        }
        data, extra = self._gemini_post(key, payload)
        extra_model = extra.get("model_switched_to")
        text = ""
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError):
            text = json.dumps(data, ensure_ascii=False)[:800]
        usage = data.get("usageMetadata", {}) if isinstance(data, dict) else {}
        meta: dict[str, Any] = {"usage": usage}
        if extra_model:
            meta["model_switched_to"] = extra_model
        return text, meta

    def _call_ollama(self, prompt: str) -> tuple[str, dict[str, Any]]:
        url = f"{self.ollama_url}/api/chat"
        payload = {
            "model": self.ollama_model,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.35},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        }
        data = self.transport(url, payload, {}, self.timeout)
        text = ""
        try:
            text = data["message"]["content"]
        except (KeyError, TypeError):
            text = json.dumps(data, ensure_ascii=False)[:800]
        return text, {"model": self.ollama_model}


    # -- گفت‌وگوی آزاد برای ایجنت ابزاردار -----------------------------
    def chat(self, messages: list[dict[str, str]]) -> str:
        """پیام‌های آماده را بفرست و متن خام برگردان (برای حلقهٔ ابزار)."""
        ok, why = self.available()
        if not ok:
            raise RuntimeError(why or "مدل در دسترس نیست")
        if self.calls_last_hour() >= self.max_calls_per_hour:
            raise RuntimeError(
                f"سهمیهٔ ساعتی مدل ({self.max_calls_per_hour}) پر است — "
                "llm.max_calls_per_hour را در کانفیگ بالا ببر"
            )
        self._record_call()
        if self.provider == "ollama":
            url = f"{self.ollama_url}/api/chat"
            payload = {
                "model": self.ollama_model,
                "stream": False,
                # قرارداد ایجنت JSON است؛ حالت json مدل را از متن آزاد دور می‌کند
                "format": "json",
                "options": {"temperature": 0.3, "num_predict": 700},
                "messages": messages,
            }
            # ollama هنگام بارگذاری مدل 503 می‌دهد؛ چند بار با تأخیر تلاش کن
            last_exc: Exception | None = None
            data = None
            for attempt, pause in enumerate((0, 3, 8, 15)):
                if pause:
                    import time as _t

                    _t.sleep(pause)
                try:
                    data = self.transport(url, payload, {}, self.timeout)
                    break
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    if "503" not in str(exc) and "timeout" not in str(exc).lower():
                        raise
            if data is None:
                raise RuntimeError(
                    f"مدل ollama جواب نداد (آخرین خطا: {last_exc}). "
                    "بررسی کن: systemctl --user status ollama یا ollama list"
                )
            try:
                return str(data["message"]["content"])
            except (KeyError, TypeError):
                return json.dumps(data, ensure_ascii=False)[:800]
        if self.provider == "gemini":
            key = self.api_key()
            system = "\n".join(m.get("content", "") for m in messages if m.get("role") == "system")
            contents = [
                {
                    "role": "model" if m.get("role") == "assistant" else "user",
                    "parts": [{"text": str(m.get("content", ""))}],
                }
                for m in messages
                if m.get("role") != "system"
            ]
            payload = {
                "systemInstruction": {"parts": [{"text": system or SYSTEM_PROMPT}]},
                "contents": contents,
                "generationConfig": {"temperature": 0.3, "maxOutputTokens": 900},
            }
            data, _extra = self._gemini_post(key, payload)
            try:
                return str(data["candidates"][0]["content"]["parts"][0]["text"])
            except (KeyError, IndexError, TypeError):
                return json.dumps(data, ensure_ascii=False)[:800]
        raise RuntimeError(f"provider پشتیبانی‌نشده: {self.provider}")

    def consult(self, context: dict[str, Any], force: bool = False) -> LLMResult:
        ok, why = self.available()
        meta: dict[str, Any] = {
            "provider": self.provider,
            "model": self.model if self.provider == "gemini" else self.ollama_model,
            "ok": False,
            "ts": utc_iso(),
        }
        if not ok:
            meta["error"] = why
            return LLMResult(None, meta)
        if not force and self.calls_last_hour() >= self.max_calls_per_hour:
            meta["error"] = f"سهمیه‌ی ساعتی ({self.max_calls_per_hour}) پر است"
            return LLMResult(None, meta)

        prompt = self._build_prompt(context)
        started = time.time()
        try:
            if self.provider == "gemini":
                text, extra = self._call_gemini(prompt)
            elif self.provider == "ollama":
                text, extra = self._call_ollama(prompt)
            else:
                meta["error"] = f"provider پشتیبانی‌نشده: {self.provider}"
                return LLMResult(None, meta)
        except urllib.error.HTTPError as exc:
            meta["error"] = f"HTTP {exc.code}: {exc.reason}"
            meta["latency_ms"] = int((time.time() - started) * 1000)
            LOG.warning("فراخوانی مدل شکست خورد: %s", meta["error"])
            self._record_call()
            return LLMResult(None, meta)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            meta["error"] = f"{type(exc).__name__}: {exc}"
            meta["latency_ms"] = int((time.time() - started) * 1000)
            LOG.warning("فراخوانی مدل شکست خورد: %s", meta["error"])
            self._record_call()
            return LLMResult(None, meta)

        self._record_call()
        meta.update(extra)
        meta["latency_ms"] = int((time.time() - started) * 1000)
        meta["raw_excerpt"] = (text or "")[:600]
        try:
            proposal = json.loads(_strip_code_fence(text))
        except ValueError:
            meta["error"] = "پاسخ مدل JSON معتبر نبود"
            LOG.warning("%s | پاسخ: %s", meta["error"], (text or "")[:200])
            return LLMResult(None, meta)
        if not isinstance(proposal, dict):
            meta["error"] = "پاسخ مدل dict نبود"
            return LLMResult(None, meta)
        meta["ok"] = True
        return LLMResult(proposal, meta)


def _strip_code_fence(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return text
