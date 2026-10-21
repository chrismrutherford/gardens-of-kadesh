#!/usr/bin/env python3
"""AI-play MCP sidecar (feature 002).

HTTP MCP server bound to 127.0.0.1 only (Streamable HTTP binding: JSON
request/response over POST /mcp). Relays MCP tool calls to the game's local
AI-play layer over a Unix domain socket per
specs/002-ai-plays-the-game-via-a-spectating-ui/contracts/ai-play.md.

Usage: python3 tools/aiplay-mcp.py [--socket PATH] [--port N]
"""
import argparse
import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import aiplay_protocol as P  # noqa: E402

PROTO = "2025-06-18"
SERVER_INFO = {"name": "aiplay-mcp", "version": "1.0.0"}

SOCKET_PATH = "/tmp/homeworld-aiplay.sock"
_lock = threading.Lock()

# TOOLS is generated from the single-source registry (spec 003 FR-008/F-012);
# never hand-edit the tool list — extend aiplay_protocol.REGISTRY instead.
DESCRIPTIONS = {
    "bind_session": "Bind the AI to one player side of the running match",
    "game_status": "Session + match status (running/paused/tick/map)",
    "observe": "Full battlefield snapshot: ships, resources, players, match status",
    "get_events": "Lifecycle + order events (matchOver, shipDestroyed, orderApplied...)",
    "order_move": "Move ships to a coordinate",
    "order_attack": "Attack target ships",
    "order_guard": "Guard/protect a target ship",
    "order_dock": "Dock (incl. resource deposit)",
    "order_harvest": "Harvest a resource",
    "order_special": "Trigger special abilities",
    "order_halt": "Halt ships",
    "order_build": "Build a ship type",
    "order_research": "Start researching a technology",
    "order_formation": "Set formation",
    "pause": "Pause the simulation",
    "resume": "Resume the simulation",
    "step": "Advance N ticks while paused",
    "save_game": "Save the match (autosave every ~10 minutes)",
    "order_tactics": "Set combat tactics for ships (aggressive/neutral/evasive)",
    "order_launch": "Launch all docked fighters from a dock-capable ship",
    "autolaunch": "Toggle auto-launch of new fighters (on/off)",
    "order_scuttle": "Scuttle (self-destruct) ships — last resort",
    "order_select": "Layer-side selection (no engine state change)",
    "order_kamikaze": "Set kamikaze mode on ships — one-way attack",
    "order_parade": "Set military parade (form up) on ships",
    "order_ru_transfer": "Transfer RUs to another player (multiplayer)",
    "order_alliance": "Offer/break alliance with another player (multiplayer)",
    "order_hyperspace": "Near-jump/hyperspace ships to a coordinate (MP funnel)",
}

_JSONTYPE = {"string": "string", "integer": "integer", "boolean": "boolean",
             "array": "array"}


def _tools_from_registry():
    def schema_for(tool):
        out = {}
        params = (P.REGISTRY["params"].get(tool)
                  or P.REGISTRY["order_tools"].get(tool, {}).get("params"))
        for key, rule in (params or {}).items():
            entry = {"type": _JSONTYPE[rule["type"]]}
            if rule.get("required"):
                entry["description"] = "required"
            if rule.get("min") is not None:
                entry["description"] = entry.get("description", "") + \
                    " (>= %d)" % rule["min"]
            out[key] = entry
        if P._is_order(tool):
            out.setdefault("id", {"type": "integer",
                                  "description": "order id echoed in events"})
        return out
    tools = []
    order = list(P.REGISTRY["state_tools"]) + list(P.REGISTRY["order_tools"])
    for name in order:
        desc = DESCRIPTIONS.get(name, name)
        mp = P.REGISTRY["order_tools"].get(name, {}).get("mp_only")
        if mp:
            desc += " [multiplayer]"
        tools.append((name, desc, schema_for(name)))
    return tools


TOOLS = _tools_from_registry()


def relay(request: dict) -> dict:
    """MCP request -> engine via the single-source encoder/decoder."""
    method = request.get("method", "")
    tool = method[6:] if method.startswith("tools/") else method
    params = dict(request.get("params", {}) or {})

    if tool not in P.REGISTRY["state_tools"] \
            and tool not in P.REGISTRY["order_tools"]:
        tool = request.get("tool", "")
    wire = P.encode_call(tool, params)
    if "error" in wire:
        return {"ok": False, **wire}

    line = (json.dumps(wire) + "\n").encode()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(SOCKET_PATH)
        sock.settimeout(10.0)
        sock.sendall(line)
        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
        first = buf.split(b"\n", 1)[0]
        return json.loads(first.decode())
    finally:
        sock.close()


def call_tool(name: str, arguments: dict) -> dict:
    if name in P.REGISTRY["order_tools"]:
        payload = dict(arguments or {})
        payload.setdefault("id", 1)
        payload["ships"] = payload.get("ships", [])
        request = {"method": "tools/" + name, "params": payload}
        res = relay(request)
    else:
        params = dict(arguments or {})
        params.setdefault("id", name + "-1")
        res = relay({"method": name, "params": params})
    text = json.dumps(res, indent=2)
    return {"content": [{"type": "text", "text": text}]}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path.rstrip("/") != "/mcp":
            self._send({"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32602, "message": "use POST /mcp"}}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(length) or b"{}")
        except Exception as e:
            self._send({"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32700, "message": f"parse error: {e}"}}, 400)
            return

        method = req.get("method")
        rid = req.get("id")
        with _lock:
            if method == "initialize":
                result = {
                    "protocolVersion": PROTO,
                    "capabilities": {"tools": {}},
                    "serverInfo": SERVER_INFO,
                }
            elif method == "tools/list":
                result = {"tools": [
                    {"name": n, "description": d, "inputSchema": {"type": "object",
                     "properties": s or {}, "additionalProperties": True}}
                    for n, d, s in TOOLS]}
            elif method == "tools/call":
                call = req.get("params", {})
                try:
                    result = call_tool(call.get("name", ""), call.get("arguments", {}))
                except Exception as e:  # engine not running / socket dead
                    result = {"content": [{"type": "text",
                                           "text": json.dumps({"ok": False,
                                                    "error": f"ai-play layer unreachable: {e}"})}],
                              "isError": True}
            elif method == "ping":
                result = {}
            else:
                self._send({"jsonrpc": "2.0", "id": rid,
                            "error": {"code": -32601, "message": f"unknown method {method}"}}, 200)
                return
        self._send({"jsonrpc": "2.0", "id": rid, "result": result})

    def do_GET(self):
        self._send({"name": SERVER_INFO["name"], "status": "ok",
                    "socket": SOCKET_PATH, "mcp": "POST /mcp"})


def main():
    global SOCKET_PATH
    ap = argparse.ArgumentParser()
    ap.add_argument("--socket", default=SOCKET_PATH)
    ap.add_argument("--port", type=int, default=8302)
    args = ap.parse_args()
    SOCKET_PATH = args.socket

    print(f"aiplay-mcp: http://127.0.0.1:{args.port}/mcp (socket {SOCKET_PATH})")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
