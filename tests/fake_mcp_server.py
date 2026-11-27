"""سرور MCP قلابی روی stdio برای تست کلاینت، بدون شبکه و بدون trade-mcp."""

from __future__ import annotations

import json
import sys

TOOLS = [
    {
        "name": "analyze_market",
        "description": "Multi-timeframe analysis of one symbol",
        "inputSchema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    },
    {
        "name": "propose_order",
        "description": "Propose an order (execution is guarded)",
        "inputSchema": {"type": "object", "properties": {"symbol": {"type": "string"}}},
    },
]

PROMPTS = [{"name": "gold_session_briefing", "description": "Gold briefing"}]


def send(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


for raw in sys.stdin:
    raw = raw.strip()
    if not raw:
        continue
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        continue
    method = msg.get("method")
    req_id = msg.get("id")
    if req_id is None:  # notification
        continue
    if method == "initialize":
        send(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": msg.get("params", {}).get("protocolVersion"),
                    "capabilities": {"tools": {}, "prompts": {}},
                    "serverInfo": {"name": "fake-mcp", "version": "0.0.1"},
                },
            }
        )
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}})
    elif method == "prompts/list":
        send({"jsonrpc": "2.0", "id": req_id, "result": {"prompts": PROMPTS}})
    elif method == "prompts/get":
        send(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "messages": [
                        {"role": "user", "content": {"type": "text", "text": "GOLD SKILL TEXT"}}
                    ]
                },
            }
        )
    elif method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        if name == "explode":
            send({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32000, "message": "boom"}})
            continue
        if name == "is_error":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"isError": True, "content": [{"type": "text", "text": "bad input"}]},
                }
            )
            continue
        send(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {"ok": True, "tool": name, "args": params.get("arguments") or {}}
                            ),
                        }
                    ]
                },
            }
        )
    else:
        send(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"unknown method {method}"},
            }
        )
