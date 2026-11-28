"""تشخیص جانِ مدل: چرا ollama جواب نمی‌دهد و چطور درستش کنیم.

یک دستور، چند پرس‌وجوی کوچک، و در آخر همان خطی که خودِ ollama در لاگش می‌نویسد.
بدون این، تنها چیزی که می‌بینیم `HTTP 503` است که هیچ نمی‌گوید.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TINY = {"stream": False, "options": {"num_predict": 8}}


@dataclass
class Probe:
    name: str
    ok: bool
    detail: str


def _request(url: str, payload: dict[str, Any] | None = None, timeout: float = 12.0) -> tuple[int, str]:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"},
        method="POST" if data is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - آدرس محلی
            return resp.status, resp.read().decode("utf-8", errors="replace")[:600]
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")[:600]
        except Exception:  # noqa: BLE001
            body = ""
        return exc.code, body
    except Exception as exc:  # noqa: BLE001
        return 0, f"{type(exc).__name__}: {exc}"


def _run(cmd: list[str], timeout: float = 8.0) -> str:
    exe = shutil.which(cmd[0])
    if not exe:
        return f"«{cmd[0]}» نصب نیست"
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: S603
        return (out.stdout or out.stderr).strip()[:1200] or "(خروجی خالی)"
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"


def diagnose(base_url: str, model: str) -> list[Probe]:
    """پنج گام: نسخه، فهرست مدل‌ها، تولید ساده، مسیر سازگار OpenAI، و لاگ سرویس."""
    base = base_url.rstrip("/")
    probes: list[Probe] = []

    code, body = _request(f"{base}/api/version")
    probes.append(Probe("نسخهٔ ollama", code == 200, body if code else body))

    code, body = _request(f"{base}/api/tags")
    has_model = model.split(":")[0] in body
    probes.append(Probe(f"مدل «{model}» در فهرست", code == 200 and has_model,
                        body[:300] if code == 200 else body))

    code, body = _request(f"{base}/api/generate", {"model": model, "prompt": "سلام", **TINY})
    probes.append(Probe("تولید متن (api/generate)", code == 200,
                        f"HTTP {code}: {body}" if code != 200 else body[:200]))

    code, body = _request(f"{base}/v1/chat/completions",
                          {"model": model, "max_tokens": 8, "stream": False,
                           "messages": [{"role": "user", "content": "سلام"}]})
    probes.append(Probe("مسیر سازگار OpenAI (v1/chat)", code == 200,
                        f"HTTP {code}: {body}" if code != 200 else body[:200]))

    ps = _run(["ollama", "ps"])
    probes.append(Probe("مدل‌های بارگذاری‌شده (ollama ps)", "NAME" in ps or "qwen" in ps, ps))

    logs = _run(["journalctl", "--user", "-u", "ollama", "-n", "15", "--no-pager"])
    if "نصب نیست" in logs or "No entries" in logs or not logs.strip():
        logs = _run(["journalctl", "-u", "ollama", "-n", "15", "--no-pager"])
    probes.append(Probe("لاگ سرویس ollama", bool(logs.strip()) and "No entries" not in logs, logs))
    return probes


def verdict(probes: list[Probe], model: str) -> str:
    """جملهٔ آخر: چه کاری واقعاً لازم است."""
    by_name = {p.name: p for p in probes}
    gen = next((p for p in probes if p.name.startswith("تولید متن")), None)
    if gen and gen.ok:
        return "مدل سالم است؛ استودیو باید با مدل کار کند."

    oai = next((p for p in probes if p.name.startswith("مسیر سازگار")), None)
    if oai and oai.ok:
        return ("api/generate جواب نمی‌دهد ولی مسیر سازگار OpenAI کار می‌کند؛ "
                "استودیو با --openai کار می‌کند.")

    detail = (gen.detail if gen else "").lower()
    if "memory" in detail or "out of memory" in detail or "cuda" in detail:
        return ("مدل به حافظه نمی‌رسد. یک مدل کوچک‌تر نصب کن: "
                "`ollama pull qwen2.5:1.5b-instruct` و استودیو را با "
                "`--model qwen2.5:1.5b-instruct` اجرا کن.")
    if "no such file" in detail or "not found" in detail or "pull" in detail:
        return f"مدل کامل نیست؛ `ollama rm {model} && ollama pull {model}`."
    if by_name.get("نسخهٔ ollama") and not by_name["نسخهٔ ollama"].ok:
        return "سرویس ollama روی این پورت جواب نمی‌دهد؛ `systemctl --user restart ollama`."

    log = by_name.get("لاگ سرویس ollama")
    if log and log.ok:
        tail = " | ".join(line for line in log.detail.splitlines()[-4:])
        return f"خودِ ollama در لاگ گفته: {tail[:300]}"
    return ("۵۰۳ با بدنهٔ خالی یعنی runner بالا نمی‌آید. "
            f"`ollama rm {model} && ollama pull {model}` و بعد "
            "`systemctl --user restart ollama`؛ اگر بودجهٔ حافظه کم است مدل ۱.۵b را امتحان کن.")


def format_report(probes: list[Probe], conclusion: str) -> str:
    lines = []
    for p in probes:
        mark = "✅" if p.ok else "❌"
        detail = " ".join(str(p.detail).split())[:400]
        lines.append(f"  {mark} {p.name}: {detail}")
    lines.append("")
    lines.append(f"  نتیجه: {conclusion}")
    return "\n".join(lines)
