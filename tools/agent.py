#!/usr/bin/env python3
"""Homeworld AI agent (feature 002 / T040) — an LLM that PLAYES the game.

Talks to:
  - an LLM (any OpenAI-compatible chat endpoint, default llama.cpp at
    http://192.168.1.106:9931/) for decisions,
  - the game via the aiplay MCP sidecar (tools/aiplay_mcp.py -> UDS):
      http://127.0.0.1:8000/mcp

Agentic loop: every turn the LLM sees a compressed battlefield snapshot plus
recent tool results and answers with ONE JSON action:
    {"thought": "...", "tool": "order_move", "args": {...}}
The agent executes it through MCP, feeds the result back, and iterates until
the match ends, the LLM declares victory, or the tick budget is exhausted.

Monitoring: every turn is logged to stdout and --logfile (default
/tmp/aiplay-agent.log) with tick numbers, thoughts, actions and results.

Usage:
  python3 tools/agent.py [--mcp http://127.0.0.1:8000/mcp] [--player 0]
      [--llm http://192.168.1.106:9931] [--model NAME] [--max-turns N]
      [--logfile PATH]
"""
import argparse
import json
import re
import socket
import sys
import time
import urllib.request

TURN_INTERVAL = 4.0   # seconds between LLM decisions (real-time play)

# --------------------------------------------------------------- utilities

def log(msg, logfile):
    line = time.strftime("[%H:%M:%S] ") + str(msg)
    print(line, flush=True)
    if logfile:
        try:
            with open(logfile, "a") as f:
                f.write(line + "\n")
        except OSError:
            pass


def http_json(url, payload=None, timeout=60):
    """POST (or GET when payload is None) and return parsed JSON."""
    if payload is None:
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


# ------------------------------------------------------------ LLM client

class LLM:
    def __init__(self, base, model=None, temperature=0.4):
        self.base = base.rstrip("/")
        self.model = model
        self.temperature = temperature
        if self.model is None:
            models = http_json(self.base + "/v1/models", timeout=10).get("models", [])
            if not models:
                raise RuntimeError("LLM exposes no models: " + self.base)
            path = models[0]["model"]
            self.model = path.split("/")[-1]      # short name works on llama.cpp
        log(f"LLM: {self.base} model={self.model}", None)

    def chat(self, messages):
        out = http_json(self.base + "/v1/chat/completions", {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": 700,
        }, timeout=600)
        return out["choices"][0]["message"]["content"]


# ------------------------------------------------------------ MCP client

class MCP:
    def __init__(self, url):
        self.url = url
        self.next_id = 1
        init = http_json(self.url, {
            "jsonrpc": "2.0", "id": self.next_id, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18",
                       "capabilities": {"tools": {}},
                       "clientInfo": {"name": "homeworld-agent", "version": "0.1"}},
        }, timeout=30)
        self.next_id += 1
        self.server = init.get("result", {}).get("serverInfo", {})
        self.tools = http_json(self.url, {
            "jsonrpc": "2.0", "id": self.next_id, "method": "tools/list",
        }, timeout=30)["result"]["tools"]
        self.next_id += 1

    def call(self, tool, args):
        return http_json(self.url, {
            "jsonrpc": "2.0", "id": self.next_id, "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        }, timeout=120)
        # (next_id not strictly needed; server is stateless)


def wait_for_game(mcp_url, logfile, tries=60):
    mcp = MCP(mcp_url)
    sock = "/tmp/homeworld-aiplay.sock"
    for i in range(tries):
        try:
            socket.socket(socket.AF_UNIX).connect(sock)
        except OSError:
            log(f"waiting for game socket ({i+1})", logfile)
            time.sleep(3)
            continue
        st = mcp.call("game_status", {})
        st = json.loads(st["result"]["content"][0]["text"])
        if st.get("error"):
            log("game not ready: " + st["error"], logfile)
            time.sleep(5)
            continue
        res = st["result"]
        log(f"match status: state={res['state']} map={res['mapName']}", logfile)
        if res["state"] in ("playing", "lobby"):
            # only wait for 'playing' when an actual match runs; lobby ok (bind later)
            return mcp
        time.sleep(5)
    raise RuntimeError("game never became ready")


# --------------------------------------------------------- prompt building

SYSTEM_PROMPT = """You are the fleet commander AI for a Homeworld (1999-style) RTS game,
playing one side of a real-time skirmish while a human spectates the live game window.

The battlefield has NO fog of war: you see every enemy ship. Coordinates are 3-D
floats [x,y,z]; distances are large (hundreds to tens of thousands of units).

Your resources (RU) pay for ships. Ships: ResourceCollector harvests asteroids for
RU, a Mothership (or Carrier) builds ships, interceptors/corvettes fight.

Reply with exactly ONE json object, nothing else:
{"thought": "one or two sentences of reasoning",
 "tool": "<one of: order_move, order_attack, order_guard, order_dock,
          order_harvest, order_special, order_halt, order_build, order_research,
          order_formation, pause, resume, step, game_status, observe, get_events>",
 "args": {...tool args...}}

Principles:
1. Always keep some ResourceCollectors harvesting (order_harvest the nearest unclaimed Asteroid).
2. Build more ResourceCollectors early, then combat ships (order_build type="LightInterceptor").
3. Attack groups: pick one enemy ship id, order 3+ fighters to attack it (order_attack).
4. Keep your Mothership away from danger (order_move if enemies approach it).
5. Don't repeat an order for a ship that is already executing it (observe first).
6. If the game is paused, use resume. Use observe before deciding.

The tool result of each action is fed back to you. When you see "matchOver",
respond with {"thought":"...","tool":"game_status","args":{}}
"""


def compress_snapshot(snap):
    """Trim the full snapshot to what fits comfortably in a context window."""
    out = {
        "tick": snap.get("tick"),
        "paused": snap.get("paused"),
        "matchStatus": snap.get("matchStatus"),
        "players": snap.get("players"),
    }
    ships = []
    for s in snap.get("ships", []):
        ships.append({"id": s["id"], "owner": s["owner"], "type": s["type"],
                      "pos": [round(c, 1) for c in s["pos"]],
                      "health": round(s["health"], 1),
                      "order": (s.get("order") or {}).get("verb", "idle")})
        if len(ships) >= 80:
            break
    out["ships"] = ships
    res = []
    for r in snap.get("resources", [])[:40]:
        res.append({"id": r["id"], "value": r.get("value", 0),
                    "pos": [round(c, 0) for c in r["pos"]]})
    out["resources"] = res
    return out


def nearest_unclaimed(asteroid, ships, player):
    """Return a resource id reasonably far from the player's fleet."""
    myships = [s for s in ships if s["owner"] == player]
    if not myships:
        return None
    home = myships[0]["pos"]
    cand = sorted(asteroid, key=lambda r: sum((a-b)**2 for a, b in zip(r["pos"], home)))
    for r in cand:
        # far enough from home that it likely isn't taken
        if sum((a-b)**2 for a, b in zip(r["pos"], home)) > 2_000_000:
            return r["id"]
    return cand[0]["id"] if cand else None


def action_guard(action, snapshot, player):
    """Apply light-weight heuristics so a weak LLM can still be useful."""
    if not isinstance(action, dict) or "tool" not in action:
        return {"tool": "observe", "args": {}, "thought": "fallback: unparseable action"}
    tool, args = action["tool"], dict(action.get("args") or {})
    if tool in ("order_harvest",):
        res = [{"id": r["id"], "value": r.get("value", 0), "pos": r["pos"]}
               for r in snapshot.get("resources", [])]
        if "target" not in args:
            tid = nearest_unclaimed(res, snapshot.get("ships", []), player)
            if tid is None:
                return {"tool": "order_build", "args": {"type": "LightInterceptor"},
                        "thought": "no asteroids left; build fighters"}
            args["target"] = tid
        if "ships" not in args:
            collectors = [s["id"] for s in snapshot.get("ships", [])
                          if s["owner"] == player and s["type"] == "ResourceCollector"]
            args["ships"] = collectors if collectors else \
                [s["id"] for s in snapshot.get("ships", []) if s["owner"] == player][:1]
    if tool in ("order_build",) and not args.get("type"):
        args["type"] = "LightInterceptor"
    if tool in ("order_build", "order_attack", "order_move", "order_harvest",
                "order_formation", "order_special", "order_guard", "order_dock") \
            and "ships" not in args:
        mobile = [s["id"] for s in snapshot.get("ships", [])
                  if s["owner"] == player and "Mothership" not in s["type"]]
        args["ships"] = mobile[:1]
    if tool in ("order_attack",) and not args.get("target"):
        enemies = [s["id"] for s in snapshot.get("ships", [])
                   if s["owner"] != player and s["health"] > 0]
        if enemies:
            args["target"] = enemies[0]
        else:
            return {"tool": "order_build", "args": {"type": "LightInterceptor"},
                    "thought": "no enemy ships visible; keep building"}
    if "id" not in args:
        args["id"] = action.get("thought", "")[:20] + str(time.time())
    action["args"] = args
    return action


# ---------------------------------------------------------------- main loop

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--mcp", default="http://127.0.0.1:8000/mcp")
    ap.add_argument("--player", type=int, default=0)
    ap.add_argument("--llm", default="http://192.168.1.106:9931")
    ap.add_argument("--model", default=None)
    ap.add_argument("--max-turns", type=int, default=2000)
    ap.add_argument("--logfile", default="/tmp/aiplay-agent.log")
    a = ap.parse_args()

    log("Homeworld agent starting", a.logfile)
    llm = LLM(a.llm, a.model)
    mcp = wait_for_game(a.mcp, a.logfile)

    r = mcp.call("bind_session", {"playerIndex": a.player})
    log("bind: " + r["result"]["content"][0]["text"].replace("\n", " ")[:120], a.logfile)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    last_event_serial = None
    turn = 0

    try:
        while turn < a.max_turns:
            turn += 1
            # --- observe ---------------------------------------------------
            snap = json.loads(mcp.call("observe", {})["result"]["content"][0]["text"])
            if isinstance(snap, dict) and "result" in snap and "ships" not in snap:
                snap = snap["result"]      # unwrap the layer's envelope
            if "error" in snap:
                log("observe error: " + snap["error"], a.logfile)
                time.sleep(5)
                continue
            view = compress_snapshot(snap)

            # match over?
            if not snap.get("matchStatus", {}).get("running", False):
                log("match not running: " + json.dumps(snap.get("matchStatus", snap))[:200], a.logfile)
                # lobby between games: rebind and wait instead of exiting
                try:
                    r = mcp.call("bind_session", {"playerIndex": a.player})
                    log("rebind: " + r["result"]["content"][0]["text"].replace("\n", " ")[:100], a.logfile)
                    time.sleep(5)      # don't spin while waiting for the match
                except Exception as e:
                    log("rebind failed: " + repr(e), a.logfile)
                    time.sleep(5)
                continue

            # --- decide ----------------------------------------------------
            messages.append({
                "role": "user",
                "content": "Battlefield snapshot:\n" + json.dumps(view) +
                           "\nYour next action (one JSON object):"})
            reply = llm.chat(messages)
            messages = messages[-41:]   # keep context bounded
            m = re.search(r"\{.*\}", reply, re.S)
            action = None
            if m:
                try:
                    action = json.loads(m.group(0))
                except ValueError:
                    action = None
            if action is None:      # tolerate non-JSON replies
                action = {"thought": reply[:120], "tool": "game_status", "args": {}}
            thought = str(action.get("thought", ""))[:160]
            log(f"[turn {turn}] thought: {thought}", a.logfile)

            # --- act ---------------------------------------------------------
            action = action_guard(action, snap, a.player)
            log(f"[turn {turn}] action : {action['tool']} {json.dumps(action['args'])[:160]}", a.logfile)
            out = mcp.call(action["tool"], action["args"])
            result = out["result"]["content"][0]["text"]
            log(f"[turn {turn}] result : {result[:220]}".replace("\n", " "), a.logfile)
            messages.append({"role": "user", "content": "Tool result: " + result[:300]})

            # --- monitor events (ship losses etc.) --------------------------
            evs = json.loads(mcp.call("get_events", {})["result"]["content"][0]["text"])
            events = evs["result"]["events"][:5]
            if events:
                log(f"[turn {turn}] events : {json.dumps(events)[:260]}".replace("\n", " "), a.logfile)

            time.sleep(TURN_INTERVAL)
        else:
            log("tick/turn budget exhausted", a.logfile)
    except KeyboardInterrupt:
        log("agent stopped by user", a.logfile)
    log(f"agent finished after {turn} turns", a.logfile)


if __name__ == "__main__":
    main()