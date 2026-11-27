"""کلاینت MCP (Model Context Protocol) روی stdio — بدون وابستگی خارجی.

هدف: به ایجنت اجازه بدهد ابزارهای سرور ``trade-mcp`` را صدا بزند
(تحلیل بازار، ریسک، اندازهٔ پوزیشن، ژورنال، پیشنهاد سفارش) و نتیجه را در
تصمیم همین چرخه ببیند.

پروتکل: JSON-RPC 2.0، هر پیام در یک خط، روی stdin/stdout پروسهٔ فرزند.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import shlex
import shutil
import sys
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

LOG = logging.getLogger("tradeagent.mcp")

PROTOCOL_VERSION = "2025-06-18"


class MCPError(RuntimeError):
    """سرور MCP پیام خطا داد یا از دسترس خارج شد."""


class MCPClient:
    """یک نشست MCP با پروسهٔ فرزند.

    ``command`` می‌تواند رشته (توکِن می‌شود) یا لیست باشد؛ مثلاً
    ``"trade-mcp --mock serve"``. اگر ``enabled=False`` باشد، کاملاً بی‌اثر است و
    همهٔ متدها ``None``/لیست خالی برمی‌گردانند تا حلقه بدون MCP هم کار کند.
    """

    def __init__(
        self,
        command: str | list[str] | None = None,
        timeout_s: float = 20.0,
        enabled: bool = True,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> None:
        self.command = command
        self.timeout_s = float(timeout_s)
        self.enabled = bool(enabled and command)
        self._env = env
        self._cwd = cwd
        self._proc: subprocess.Popen[str] | None = None
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._next_id = 0
        self._lock = threading.Lock()
        self.server_info: dict[str, Any] = {}
        self.tools: list[dict[str, Any]] = []
        self.prompts: list[dict[str, Any]] = []
        self._started = False

    # -- چرخهٔ عمر ------------------------------------------------------
    def start(self) -> bool:
        """پروسه را بالا بیاور و دست‌دادن (initialize) را انجام بده."""
        if not self.enabled or self._started:
            return self._started
        argv = shlex.split(self.command) if isinstance(self.command, str) else list(self.command or [])
        if not argv:
            self.enabled = False
            return False
        # سرور ممکن است فقط در کنار همین مفسر نصب شده باشد (venv پروژه)
        if os.sep not in argv[0]:
            found = shutil.which(argv[0]) or shutil.which(argv[0], path=str(Path(sys.executable).parent))
            if found:
                argv[0] = found
            else:
                LOG.warning("MCP command %r is not installed or not on PATH", argv[0])
                self.enabled = False
                return False
        env = dict(os.environ)
        env.setdefault("PYTHONUNBUFFERED", "1")
        if self._env:
            env.update(self._env)
        try:
            self._proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=env,
                cwd=self._cwd,
            )
        except (OSError, ValueError) as exc:
            LOG.warning("MCP server could not start (%s): %s", argv, exc)
            self.enabled = False
            return False

        threading.Thread(target=self._pump, args=(self._proc.stdout,), daemon=True).start()
        threading.Thread(target=self._drain_stderr, args=(self._proc.stderr,), daemon=True).start()

        try:
            result = self.request(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"roots": {"listChanged": False}},
                    "clientInfo": {"name": "tradeAgent", "version": "0.1.0"},
                },
            )
        except MCPError as exc:
            LOG.warning("MCP initialize failed: %s", exc)
            self.close()
            return False

        self.server_info = dict(result.get("serverInfo") or {})
        self._notify("notifications/initialized", {})
        self.tools = self.list_tools()
        self.prompts = self.list_prompts()
        self._started = True
        LOG.info(
            "MCP ready: %s %s | %d tools, %d prompts",
            self.server_info.get("name", "?"),
            self.server_info.get("version", ""),
            len(self.tools),
            len(self.prompts),
        )
        return True

    def close(self) -> None:
        proc, self._proc = self._proc, None
        self._started = False
        if proc is None:
            return
        try:
            proc.stdin.close() if proc.stdin else None
        except OSError:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            try:
                proc.kill()
            except OSError:
                pass

    def __enter__(self) -> "MCPClient":
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- ترابری ---------------------------------------------------------
    def _pump(self, stream: Any) -> None:
        try:
            for line in stream:
                self._lines.put(line)
        except (ValueError, OSError):
            pass
        finally:
            self._lines.put(None)

    def _drain_stderr(self, stream: Any) -> None:
        try:
            for line in stream:
                LOG.debug("mcp stderr: %s", line.rstrip())
        except (ValueError, OSError):
            pass

    def _send(self, payload: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise MCPError("MCP server is not running")
        with self._lock:
            try:
                self._proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
                self._proc.stdin.flush()
            except (OSError, ValueError) as exc:
                raise MCPError(f"could not write to the MCP server: {exc}") from exc

    def _read_message(self, deadline: float) -> dict[str, Any]:
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise MCPError("MCP server did not answer in time")
            try:
                line = self._lines.get(timeout=remaining)
            except queue.Empty as exc:
                raise MCPError("MCP server did not answer in time") from exc
            if line is None:
                raise MCPError("MCP server closed the connection")
            line = line.strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                LOG.debug("non-JSON line from MCP server: %s", line[:200])
                continue

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        try:
            self._send({"jsonrpc": "2.0", "method": method, "params": params})
        except MCPError as exc:
            LOG.debug("notify %s failed: %s", method, exc)

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """یک درخواست JSON-RPC و انتظار پاسخ متناظر با همان id."""
        if self._proc is None:
            raise MCPError("MCP server is not running")
        self._next_id += 1
        req_id = self._next_id
        self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}})
        deadline = time.time() + self.timeout_s
        while True:
            msg = self._read_message(deadline)
            if msg.get("id") != req_id:
                if "method" in msg and msg.get("id") is not None:
                    # درخواست سرور به ما (مثل sampling) - پاسخ «پشتیبانی نمی‌شود»
                    self._send(
                        {
                            "jsonrpc": "2.0",
                            "id": msg["id"],
                            "error": {"code": -32601, "message": "not supported by tradeAgent"},
                        }
                    )
                continue
            if "error" in msg:
                err = msg["error"] or {}
                raise MCPError(f"{method}: {err.get('message') or err}")
            return dict(msg.get("result") or {})

    # -- API سطح بالا ---------------------------------------------------
    def list_tools(self) -> list[dict[str, Any]]:
        if self._proc is None:
            return []
        try:
            result = self.request("tools/list", {})
        except MCPError as exc:
            LOG.warning("tools/list failed: %s", exc)
            return []
        tools = result.get("tools") or []
        return [t for t in tools if isinstance(t, dict)]

    def list_prompts(self) -> list[dict[str, Any]]:
        if self._proc is None:
            return []
        try:
            result = self.request("prompts/list", {})
        except MCPError:
            return []
        return [p for p in (result.get("prompts") or []) if isinstance(p, dict)]

    def get_prompt(self, name: str, args: dict[str, Any] | None = None) -> str:
        result = self.request("prompts/get", {"name": name, "arguments": args or {}})
        parts = []
        for item in result.get("messages") or []:
            content = item.get("content") or {}
            if isinstance(content, dict) and content.get("type") == "text":
                parts.append(str(content.get("text", "")))
        return "\n".join(parts).strip()

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """ابزار را صدا بزن؛ خروجی ساختارشده (JSON ابزار) یا متن."""
        result = self.request("tools/call", {"name": name, "arguments": arguments or {}})
        if result.get("isError"):
            raise MCPError(f"{name} returned an error: {_as_text(result)}")
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            return structured
        text = _as_text(result)
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return text

    # -- فرمت برای مدل زبانی -------------------------------------------
    def tools_for_prompt(self, allow: list[str] | None = None, limit: int = 24) -> list[dict[str, Any]]:
        """فهرست ابزارها به شکل ساده برای گذاشتن در پرامپت مدل."""
        out: list[dict[str, Any]] = []
        for tool in self.tools[:limit]:
            name = tool.get("name")
            if not name or (allow is not None and name not in allow):
                continue
            schema = tool.get("inputSchema") or {}
            props = schema.get("properties") or {}
            out.append(
                {
                    "name": name,
                    "description": str(tool.get("description", "")).strip().splitlines()[0][:160],
                    "args": {
                        key: {
                            "type": str((val or {}).get("type", "any")),
                            "required": key in (schema.get("required") or []),
                        }
                        for key, val in props.items()
                    },
                }
            )
        return out


def _as_text(result: dict[str, Any]) -> str:
    parts = []
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text", "")))
    return "\n".join(parts).strip()


def mcp_from_config(cfg: Any, enabled: bool | None = None) -> MCPClient:
    """MCPClient از بخش ``[mcp]`` کانفیگ (یا متغیر محیطی)."""
    command = cfg.get("mcp.server_cmd", os.environ.get("TRADEAGENT_MCP_CMD", ""))
    if enabled is None:
        enabled = bool(cfg.get("mcp.enabled", bool(os.environ.get("TRADEAGENT_MCP_CMD"))))
    return MCPClient(
        command=command or None,
        timeout_s=float(cfg.get("mcp.timeout_s", 25.0)),
        enabled=enabled,
        env={"TRADEAGENT_MCP_CHILD": "1"},
    )
