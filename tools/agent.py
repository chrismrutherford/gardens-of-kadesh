#!/usr/bin/env python3
"""Homeworld AI agent (feature 002 / T040) — an LLM that PLAYES the game.

Talks to:
  - an LLM (any OpenAI-compatible chat endpoint, default llama.cpp at
    http://192.168.1.106:9931/) for decisions,
  - the game via the aiplay MCP sidecar (tools/aiplay_mcp.py -> UDS):
      http://127.0.0.1:8302/mcp

Agentic loop: every turn the LLM sees a compressed battlefield snapshot plus
recent tool results and answers with ONE JSON action:
    {"thought": "...", "tool": "order_move", "args": {...}}
The agent executes it through MCP, feeds the result back, and iterates until
the match ends, the LLM declares victory, or the tick budget is exhausted.

Monitoring: /tmp/aiplay-agent.log is APPENDED across restarts (each run gets a
session banner). Every turn logs the world summary, the LLM's thought, the
action taken, the tool result and recent events — `tail -f` friendly.
Errors never kill the loop: each turn is individually crash-guarded with a
10 s back-off, and lost game/socket connections rebind automatically.

Usage:
  python3 tools/agent.py [--mcp http://127.0.0.1:8302/mcp] [--player 0]
      [--llm http://192.168.1.106:9931] [--model NAME] [--max-turns N]
      [--logfile PATH] [--interval SECONDS]
"""
import argparse
import datetime
import json
import re
import socket
import time
import urllib.request

# --------------------------------------------------------------- utilities

LOGFILE = None
SESSION = datetime.datetime.now().strftime("session-%H%M%S")


def log(msg):
    line = time.strftime("[%H:%M:%S]") + " " + str(msg).replace("\n", " ")
    print(line, flush=True)
    try:
        with open(LOGFILE, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def session_banner(meta):
    """Restart-friendly marker so `tail -f /tmp/aiplay-agent.log` reads well."""
    with open(LOGFILE, "a") as f:
        f.write("")
        f.write("=" * 72 + "\n")
        f.write(f"### NEW AGENT SESSION {SESSION} @ {time.strftime('%F %T')}\n")
        f.write(f"### {meta}\n")
        f.write("=" * 72 + "\n")
    log("session " + SESSION + " — " + meta)


def http_json(url, payload=None, timeout=60):
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
        log(f"LLM: {self.base} model={self.model}")

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
        init = http_json(self.url, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18",
                       "capabilities": {"tools": {}},
                       "clientInfo": {"name": "homeworld-agent", "version": "0.2"}},
        }, timeout=30)
        self.server = init.get("result", {}).get("serverInfo", {})

    def call(self, tool, args):
        return http_json(self.url, {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        }, timeout=120)


def result_text(mcp_out, limit=260):
    text = mcp_out["result"]["content"][0]["text"]
    text = text.replace("\n", " ")
    return text[:limit]


def wait_for_game(mcp, tries=60, player=0):
    sock_path = "/tmp/homeworld-aiplay.sock"
    for i in range(tries):
        try:
            socket.socket(socket.AF_UNIX).connect(sock_path)
        except OSError:
            if i % 10 == 0:
                log("waiting for game socket...")
            time.sleep(3)
            continue
        st = json.loads(mcp.call("game_status", {})["result"]["content"][0]["text"])
        if st.get("error"):
            log("game not ready: " + st["error"])
            time.sleep(5)
            continue
        log(f"match status: state={st['result']['state']} map={st['result']['mapName']}")
        return st["result"]
        # lobby is fine: bind + wait loop handles the rest
    raise RuntimeError("game never became ready")


def ensure_bound(mcp, player):
    r = mcp.call("bind_session", {"playerIndex": player})
    txt = result_text(r, 120)
    if '"error"' in txt:
        return False
    log("bound to player %d" % player)
    return True


# --------------------------------------------------------- prompt building

SYSTEM_PROMPT = """You are the fleet commander AI for a Homeworld (1999-style) RTS game,
playing one side of a real-time skirmish while a human spectates the live game window.

The battlefield has NO fog of war: you see every enemy ship. Coordinates are 3-D
floats [x,y,z]; distances are large (hundreds to tens of thousands of units).

Your resources (RU) pay for ships. Ships: ResourceCollector harvests asteroids for
RU, a Mothership (or Carrier) builds ships, interceptors/corvettes fight. Builds
take time: check the buildQueue before ordering another build.

Reply with exactly ONE json object, nothing else:
{"thought": "one or two sentences of reasoning",
 "tool": "<one of: order_move, order_attack, order_guard, order_dock,
          order_harvest, order_special, order_halt, order_build, order_research,
          order_formation, pause, resume, step, game_status, observe, get_events>",
 "args": {...tool args...}}

Principles:
1. Always keep 2+ ResourceCollectors harvesting (order_harvest the nearest unclaimed Asteroid).
2. Build ResourceCollectors early, then combat ships (order_build type="LightInterceptor").
3. Attack in groups: pick ONE enemy ship id, order 3+ fighters to attack it (order_attack).
4. Keep your Mothership away from danger (order_move if enemies approach it).
5. Don't re-order a ship that is already executing a similar order (see each ship's
   "order" field and the buildQueue).
6. If the game is paused, use resume. Use observe before deciding.

Each turn you receive: a world summary, the result of your last action, and recent
events (orderApplied/orderRejected/shipDestroyed...). When you see "matchOver",
the game has ended.
"""


def compress_snapshot(snap):
    """Trim the full snapshot to what fits comfortably in a context window."""
    out = {
        "tick": snap.get("tick"),
        "paused": snap.get("paused"),
        "matchStatus": snap.get("matchStatus"),
        "players": snap.get("players"),
        "buildQueue": snap.get("buildQueue", []),   # in-progress builds: don't spam
        "research": snap.get("research", []),
    }
    ships = [{"id": s["id"], "owner": s["owner"], "type": s["type"],
              "pos": [round(c) for c in s["pos"]],
              "health": round(s["health"]),
              "order": (s.get("order") or {}).get("verb", "idle")}
             for s in snap.get("ships", []) if s.get("health", 0) > 0]
    out["ships"] = ships[:80]
    out["resources"] = [{"id": r["id"], "value": r.get("value", 0),
                         "pos": [round(c) for c in r["pos"]]}
                        for r in snap.get("resources", [])[:40]]
    return out


def world_summary(view):
    mine = [s for s in view["ships"] if s["owner"] == 0]
    foes = [s for s in view["ships"] if s["owner"] != 0]
    ru = next((p["resourceUnits"] for p in view.get("players", []) if p["index"] == 0), "?")
    kinds = {}
    for s in mine:
        kinds[s["type"]] = kinds.get(s["type"], 0) + 1
    q = "; ".join(f"{b['type']} ({b.get('ticksRemaining','?')}t)"
                  for b in view.get("buildQueue", [])) or "none"
    return (f"tick {view.get('tick')} | RU {ru} | fleet: {kinds or 'none'} "
            f"| enemies: {len(foes)} | building: {q}")


def nearest_unclaimed(resources, ships):
    myships = [s for s in ships if s["owner"] == 0]
    if not resources:
        return None
    home = myships[0]["pos"] if myships else [0, 0, 0]
    cand = sorted(resources, key=lambda r: sum((a-b)**2 for a, b in zip(r["pos"], home)))
    for r in cand:
        if sum((a-b)**2 for a, b in zip(r["pos"], home)) > 2_000_000:
            return r["id"]
    return cand[0]["id"] if cand else None


def action_guard(action, snapshot):
    """Apply light-weight heuristics so a weak LLM can still be useful."""
    if not isinstance(action, dict) or "tool" not in action:
        return {"tool": "observe", "args": {}, "thought": "fallback: unparseable action"}
    tool, args = action["tool"], dict(action.get("args") or {})
    args.pop("shipId", None); args.pop("ship_ids", None); args.pop("target_id", None)
    if "id" not in args or not isinstance(args["id"], (int, float)):
        args["id"] = int(time.time()) % 100000

    collectors = [s["id"] for s in snapshot.get("ships", [])
                  if s["owner"] == 0 and s["type"] == "ResourceCollector"]
    mobile = [s["id"] for s in snapshot.get("ships", [])
              if s["owner"] == 0 and "Mothership" not in s["type"]]

    if tool in ("order_harvest",):
        if "target" not in args:
            tid = nearest_unclaimed(
                [{"id": r["id"], "pos": r["pos"]} for r in snapshot.get("resources", [])],
                snapshot.get("ships", []))
            if tid is None:
                return {"tool": "order_build", "args": {"type": "LightInterceptor"},
                        "thought": "no asteroids left; keep building"}
            args["target"] = tid
        args["ships"] = args.get("ships") or collectors or mobile[:1]
    if tool in ("order_build",) and not args.get("type"):
        args["type"] = "LightInterceptor"
    if tool in ("order_build",):
        args["ships"] = args.get("ships") or \
            ([s["id"] for s in snapshot.get("ships", [])
              if s["owner"] == 0 and s["type"] == "Mothership"] or mobile[:1])
    if tool in ("order_move", "order_formation", "order_special", "order_halt",
                "order_guard", "order_attack", "order_dock"):
        args["ships"] = args.get("ships") or mobile[:1]
    if tool in ("order_attack",) and not args.get("target"):
        enemies = [s["id"] for s in snapshot.get("ships", [])
                   if s["owner"] != 0 and s["health"] > 0]
        if enemies:
            args["target"] = enemies[0]
        else:
            return {"tool": "order_build", "args": {"type": "LightInterceptor"},
                    "thought": "no enemy ships visible; keep building"}
    action["args"] = args
    return action


# ---------------------------------------------------------------- main loop

def main():
    global LOGFILE
    ap = argparse.ArgumentParser(description="Homeworld LLM fleet commander")
    ap.add_argument("--mcp", default="http://127.0.0.1:8302/mcp")
    ap.add_argument("--player", type=int, default=0)
    ap.add_argument("--llm", default="http://192.168.1.106:9931")
    ap.add_argument("--model", default=None)
    ap.add_argument("--max-turns", type=int, default=2000)
    ap.add_argument("--logfile", default="/tmp/aiplay-agent.log")
    ap.add_argument("--interval", type=float, default=4.0)
    a = ap.parse_args()
    LOGFILE = a.logfile

    meta = f"player={a.player} mcp={a.mcp} llm={a.llm} max-turns={a.max_turns}"
    session_banner(meta)
    llm = LLM(a.llm, a.model)

    mcp = None
    while True:                              # wait for game + MCP to be up
        try:
            mcp = MCP(a.mcp)
            log("MCP sidecar: " + json.dumps(mcp.server))
            break
        except Exception as e:
            log(f"MCP not ready ({e!r}); retrying in 5s")
            time.sleep(5)

    for i in range(60):
        try:
            socket.socket(socket.AF_UNIX).connect("/tmp/homeworld-aiplay.sock")
            break
        except OSError:
            if i % 10 == 0:
                log("waiting for game socket...")
            time.sleep(3)
    else:
        raise RuntimeError("game socket never appeared")

    bind_ok = False
    while not bind_ok:
        bind_ok = ensure_bound(mcp, a.player)
        if not bind_ok:
            log("bind pending (match probably still loading); retrying in 5s")
            time.sleep(5)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    turn = 0
    next_event_idx = 0

    while turn < a.max_turns:
        turn += 1
        try:
            # ---- observe --------------------------------------------------
            snap = json.loads(
                mcp.call("observe", {})["result"]["content"][0]["text"])
            if isinstance(snap, dict) and "result" in snap and "ships" not in snap:
                snap = snap["result"]
            if "error" in snap:
                log("observe error: " + snap["error"])
                time.sleep(5)
                continue

            view = compress_snapshot(snap)
            log("[turn %d] world: %s" % (turn, world_summary(view)))

            if not snap.get("matchStatus", {}).get("running", False):
                log("match not running — waiting/rebinding")
                time.sleep(5)
                ensure_bound(mcp, a.player)
                continue

            # ---- recent events as user-visible feedback --------------------
            evs = json.loads(mcp.call("get_events", {})["result"]["content"][0]["text"])
            events = evs["result"]["events"]
            fresh = events[:max(0, len(events) - next_event_idx)] if events else []
            next_event_idx = len(events)
            if fresh:
                log("[turn %d] events: %s" % (
                    turn, json.dumps(fresh[:4])[:240]))

            # ---- decide ----------------------------------------------------
            messages.append({
                "role": "user",
                "content": (f"Events: {json.dumps(fresh[:4])}\n"
                            "Battlefield snapshot:\n"
                            + json.dumps(view)
                            + "\nYour next action (one JSON object):")})
            reply = llm.chat(messages)
            messages = messages[-41:]

            m = re.search(r"\{.*\}", reply, re.S)
            action = None
            if m:
                try:
                    action = json.loads(m.group(0))
                except ValueError:
                    action = None
            if action is None:
                action = {"thought": reply[:120], "tool": "game_status", "args": {}}
            log("[turn %d] thought: %s" % (turn, str(action.get("thought", ""))[:160]))

            # ---- act --------------------------------------------------------
            action = action_guard(action, snap)
            log("[turn %d] action : %s %s" % (
                turn, action["tool"], json.dumps(action["args"])[:150]))
            result = result_text(mcp.call(action["tool"], action["args"]))
            log("[turn %d] result : %s" % (turn, result))

            messages.append({"role": "user",
                             "content": "Tool result: " + result[:300]})
            time.sleep(a.interval)
        except KeyboardInterrupt:
            log("agent stopped by user")
            return
        except Exception as e:
            import traceback
            log("[turn %d] ERROR (recovering, 10s backoff): %r" % (turn, e))
            log("trace: " + traceback.format_exc(limit=3).replace("\n", " | ")[:400])
            if not ensure_bound(mcp, a.player):
                time.sleep(10)
            else:
                time.sleep(10)

    log("tick/turn budget exhausted after %d turns" % turn)


if __name__ == "__main__":
    main()