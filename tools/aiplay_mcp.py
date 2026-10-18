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
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PROTO = "2025-06-18"
SERVER_INFO = {"name": "aiplay-mcp", "version": "0.1.0"}

SOCKET_PATH = "/tmp/homeworld-aiplay.sock"
_lock = threading.Lock()

TOOLS = [
    ("bind_session",  "Bind the AI to one player side of the running match", {"playerIndex": {"type": "integer", "description": "player to control (default 0)"}}),
    ("game_status",   "Session + match status (running/paused/tick/map)", {}),
    ("observe",       "Full battlefield snapshot: ships, resources, players, match status", {"sections": {"type": "array", "description": "optional: ships/resources"}, "owner": {"type": "integer", "description": "optional owner filter"}}),
    ("get_events",    "Lifecycle + order events (matchOver, shipDestroyed, orderApplied...)", {}),
    ("order_move",    "Move ships to a coordinate", {"ships": {"type": "array"}, "target": {"type": "array"}, "id": {"type": "integer"}}),
    ("order_attack",  "Attack target ships", {"ships": {"type": "array"}, "target": {"type": "integer"}, "id": {"type": "integer"}}),
    ("order_guard",   "Guard/protect a target ship", {"ships": {"type": "array"}, "target": {"type": "integer"}, "id": {"type": "integer"}}),
    ("order_dock",    "Dock (incl. resource deposit)", {"ships": {"type": "array"}, "dockWith": {"type": "integer"}, "id": {"type": "integer"}}),
    ("order_harvest", "Harvest a resource", {"ships": {"type": "array"}, "target": {"type": "integer"}, "id": {"type": "integer"}}),
    ("order_special", "Trigger special abilities", {"ships": {"type": "array"}, "target": {"type": "integer"}, "id": {"type": "integer"}}),
    ("order_halt",    "Halt ships", {"ships": {"type": "array"}, "id": {"type": "integer"}}),
    ("order_build",   "Build a ship type", {"ships": {"type": "array"}, "type": {"type": "string"}, "quantity": {"type": "integer"}, "id": {"type": "integer"}}),
    ("order_research","Start researching a technology", {"ships": {"type": "array"}, "tech": {"type": "string"}, "id": {"type": "integer"}}),
    ("order_formation", "Set formation", {"ships": {"type": "array"}, "formation": {"type": "string"}, "id": {"type": "integer"}}),
    ("pause",         "Pause the simulation", {}),
    ("resume",        "Resume the simulation", {}),
    ("step",          "Advance N ticks while paused", {"ticks": {"type": "integer"}}),
]

VERB_FOR_TOOL = {
    "order_move": "move", "order_attack": "attack", "order_guard": "guard",
    "order_dock": "dock", "order_harvest": "harvest", "order_special": "special",
    "order_halt": "halt", "order_build": "build", "order_research": "research",
    "order_formation": "formation",
}

METHOD_FOR_TOOL = {
    "bind_session": "aiSession.bind", "game_status": "aiSession.status",
    "observe": "state.snapshot", "get_events": "state.events",
    "pause": "time.pause", "resume": "time.resume", "step": "time.step",
}


def relay(request: dict) -> dict:
    method = request.get("method", "")
    tool = method[6:] if method.startswith("tools/") else method
    name = tool if tool in METHOD_FOR_TOOL or tool in VERB_FOR_TOOL else request.get("tool", "")
    params = dict(request.get("params", {}) or {})
    req_id = params.pop("id", 1)

    if name in METHOD_FOR_TOOL:
        wire = {"id": req_id, "method": METHOD_FOR_TOOL[name]}
        if params:
            wire["params"] = params
    elif name in VERB_FOR_TOOL:
        wire = {"id": req_id, "method": "order." + VERB_FOR_TOOL[name], "params": params}
    else:
        return {"ok": False, "error": f"unknown tool {name}"}

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
    payload = params = dict(arguments or {})
    payload.setdefault("id", name + "-1")
    if name in VERB_FOR_TOOL:
        payload = dict(arguments or {})
        payload.setdefault("id", 1)
        payload["ships"] = payload.get("ships", [])
        request = {"method": "tools/" + name, "params": payload}
        res = relay(request)
    else:
        res = relay({"method": name, "params": payload})
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
