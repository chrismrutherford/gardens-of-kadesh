#!/usr/bin/env python3
"""Homeworld AI agent (feature 002 / T040) — an LLM fleet commander.

Streams from any OpenAI-compatible reasoning LLM (default llama.cpp at
http://192.168.1.106:9931/), uses NATIVE tool calling for game orders, and
plays through the aiplay MCP sidecar (http://127.0.0.1:8302/mcp).

Streaming (protophanes-style): reasoning_content deltas are logged LIVE as
they arrive — `tail -f /tmp/aiplay-agent.log` shows the commander thinking
token by token — content tokens stream too, and tool calls execute through
MCP as soon as the model finishes its round.

Usage:
  python3 tools/agent.py [--mcp http://127.0.0.1:8302/mcp] [--player 0]
      [--llm http://192.168.1.106:9931] [--model NAME] [--max-rounds N]
      [--logfile PATH] [--interval SECONDS]
"""
import argparse
import json
import socket
import sys
import time
import urllib.request

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from agent_tools import TOOLS, TOOL_NAMES  # noqa: E402

LOGFILE = None
SESSION = time.strftime("session-%H%M%S")


def log(msg):
    line = time.strftime("[%H:%M:%S]") + " " + str(msg).replace("\n", " ")
    print(line, flush=True)
    try:
        with open(LOGFILE, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def log_raw(text):
    """Streamed deltas: written verbatim, live, no newline added."""
    print(text, end="", flush=True)
    try:
        with open(LOGFILE, "a") as f:
            f.write(text)
    except OSError:
        pass


def session_banner(meta):
    with open(LOGFILE, "a") as f:
        f.write("=" * 72 + "\n")
        f.write(f"### NEW AGENT SESSION {SESSION} @ {time.strftime('%F %T')}\n")
        f.write(f"### {meta}\n")
        f.write("=" * 72 + "\n")
    log("session " + SESSION + " — " + meta)


# --------------------------------------------------------------- http utils

def http_json(url, payload=None, timeout=60):
    if payload is None:
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


# ---------------------------------------------------------------- LLM (SSE)

class LLM:
    """Streaming chat with native tool calling + reasoning (llama.cpp/Qwen3)."""

    def __init__(self, base, model=None, temperature=0.4):
        self.base = base.rstrip("/")
        self.model = model
        self.temperature = temperature
        if self.model is None:
            models = http_json(self.base + "/v1/models", timeout=10).get("models", [])
            if not models:
                raise RuntimeError("LLM exposes no models: " + self.base)
            self.model = models[0]["model"].split("/")[-1]
        log(f"LLM: {self.base} model={self.model} (streaming + native tools)")

    def chat_stream(self, messages):
        """Consume the SSE stream; log deltas live; return (content, calls)."""
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": TOOLS,
            "stream": True,
            "temperature": self.temperature,
            "max_tokens": 2600,
            "chat_template_kwargs": {"enable_thinking": True},
        }
        req = urllib.request.Request(
            self.base + "/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        content = ""
        calls = {}
        with urllib.request.urlopen(req, timeout=600) as r:
            for raw in r:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data.strip() == "[DONE]":
                    break
                try:
                    ch = json.loads(data)["choices"][0]
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
                d = ch.get("delta", {})
                if d.get("reasoning_content"):
                    log_raw(d["reasoning_content"])
                if d.get("content") is not None:
                    log_raw(d["content"])
                    content += d["content"]
                for tc in d.get("tool_calls") or []:
                    i = tc.get("index", 0)
                    slot = calls.setdefault(i, {"id": "", "name": "", "args": ""})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function", {})
                    if fn.get("name"):
                        slot["name"] += fn["name"]
                    if fn.get("arguments"):
                        slot["args"] += fn["arguments"]
        log_raw("\n")
        tool_calls = [calls[k] for k in sorted(calls)]
        return content, tool_calls


# ---------------------------------------------------------------- MCP client

class MCP:
    def __init__(self, url):
        self.url = url
        init = http_json(self.url, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18",
                       "capabilities": {"tools": {}},
                       "clientInfo": {"name": "homeworld-agent", "version": "0.3"}},
        }, timeout=30)
        self.server = init.get("result", {}).get("serverInfo", {})

    def call(self, tool, args):
        return http_json(self.url, {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        }, timeout=120)


def result_text(mcp_out, limit=280):
    return mcp_out["result"]["content"][0]["text"].replace("\n", " ")[:limit]


def ensure_bound(mcp, player):
    r = mcp.call("bind_session", {"playerIndex": player})
    if '"error"' in result_text(r, 120):
        return False
    log("bound to player %d" % player)
    return True


# ---------------------------------------------------------------- prompting

SYSTEM_PROMPT = """You are the fleet commander AI for a Homeworld (1999-style) RTS game,
playing one side of a real-time skirmish while a human spectates the live game window.

The battlefield has NO fog of war: you see every enemy ship. Coordinates are 3-D
floats [x,y,z]; distances are large (hundreds to tens of thousands of units).

Your resources (RU) pay for ships. ResourceCollectors harvest asteroids for RU,
the Mothership builds ships, interceptors/corvettes fight. Builds take time —
check buildQueue before ordering more builds.

Fleet roster you can build (order_build type="..."), with research
prerequisites (research first via order_research tech="..."):

  Always available (no tech): ResourceCollector, ResourceController,
      LightInterceptor, Probe, ProximitySensor, SensorArray
  requires HeavyGuns:      HeavyDefender, HeavyCorvette
  requires Chassis2:       AttackBomber, MultiGunCorvette, MinelayerCorvette
  requires Chassis2 + MassDrive10Kt:   StandardFrigate
  requires Chassis3:       DDDFrigate, GravWellGenerator
  requires CloakGeneratorTech:  CloakGenerator
  requires IonWeapons:     IonCannonFrigate
  requires Chassis3 + MassDrive100Kt:  MissileDestroyer, StandardDestroyer
  requires HeavyGuns + Chassis3:       HeavyCruiser
  RepairTech / SalvageTech enable RepairCorvette / SalCapCorvette support use

Research techs (exact names for order_research tech="..."):
  NewAlloys, MassDrive1Kt, CoolingSystems, CloakDefenseFighter,
  TargetingSystems, PlasmaWeapons, Chassis1, MassDrive10Kt, MediumGuns,
  MineLayerTech, Chassis2, AdvancedCoolingSystems, MassDrive100Kt,
  FireControl, SupportRefuelTech, AdvanceTacticalSupport, IonWeapons,
  DDDFDFGFTech, Chassis3, MassDrive1Mt, AdvancedFireControl, MissileWeapons,
  ConstructionTech, HeavyGuns, ProximityDetector, SensorsArrayTech,
  GravityWellGeneratorTech, CloakGeneratorTech, RepairTech, SalvageTech

Research needs labs = ResearchShips in your fleet (techTree.labs in the
snapshot). If techTree.labs == 0, NO research is possible — build a
ResearchShip first. order_research without labs returns E_NO_LABS.
If order_build is rejected with E_BAD_REQUEST, that hull's tech isn't
researched yet — research it (needs labs).

Call ONE tool per round. The tool result plus a fresh snapshot arrive next
round. Keep commanding the fleet; when the match is over, call game_status.

Win condition: destroy every enemy ship (their Mothership is the anchor).

Strategy:
1. Keep 2+ ResourceCollectors harvesting (order_harvest nearest asteroid).
2. Build ResourceCollectors early, then combat ships (order_build).
3. Attack in groups of 3+: pick ONE enemy ship id (order_attack).
4. Keep the Mothership away from danger (order_move).
5. Don't re-order ships already executing similar orders (see each ship's
   "order" field and the buildQueue in the snapshot).
"""


def order_detail(order):
    """Full order detail incl. destination so the LLM can spot stuck ships."""
    if not order:
        return "idle"
    verb = order.get("verb")
    tgt = order.get("target")
    if tgt is not None and isinstance(tgt, (list, tuple)):
        if all(abs(v) < 1.0 for v in tgt):
            return "STUCK-move-to-origin (re-order this ship!)"
        return "%s -> [%d, %d, %d]" % (verb, tgt[0], tgt[1], tgt[2])
    return str(verb)


def compress_snapshot(snap):
    out = {
        "tick": snap.get("tick"),
        "paused": snap.get("paused"),
        "matchStatus": snap.get("matchStatus"),
        "players": snap.get("players"),
        "buildQueue": snap.get("buildQueue", []),
        "research": snap.get("research", []),
        "techTree": snap.get("techTree", {}),   # labs + locked hulls + missing techs
    }
    out["ships"] = [{"id": s["id"], "owner": s["owner"], "type": s["type"],
                     "pos": [round(c) for c in s["pos"]],
                     "health": round(s["health"]),
                     "order": order_detail(s.get("order"))}
                    for s in snap.get("ships", []) if s.get("health", 0) > 0][:80]
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
    inc = view.get("income", -1)
    inc_s = f"{inc:.1f} RU/min" if isinstance(inc, (int, float)) and inc >= 0 else "unknown"
    return (f"tick {view.get('tick')} | RU {ru} | income {inc_s} | fleet: {kinds or 'none'} "
            f"| enemies: {len(foes)} | building: {q}")


# ---------------------------------------------------------------- main loop

def main():
    global LOGFILE
    ap = argparse.ArgumentParser(description="Homeworld LLM fleet commander")
    ap.add_argument("--mcp", default="http://127.0.0.1:8302/mcp")
    ap.add_argument("--player", type=int, default=0)
    ap.add_argument("--llm", default="http://192.168.1.106:9931")
    ap.add_argument("--model", default=None)
    ap.add_argument("--max-rounds", type=int, default=2000)
    ap.add_argument("--logfile", default="/tmp/aiplay-agent.log")
    ap.add_argument("--interval", type=float, default=4.0)
    a = ap.parse_args()
    LOGFILE = a.logfile

    session_banner(f"player={a.player} mcp={a.mcp} llm={a.llm} rounds={a.max_rounds}")
    llm = LLM(a.llm, a.model)

    while True:
        try:
            mcp = MCP(a.mcp)
            log("MCP sidecar: " + json.dumps(mcp.server))
            break
        except Exception as e:
            log(f"MCP not ready ({e!r}); retry in 5s")
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

    while not ensure_bound(mcp, a.player):
        log("bind pending (match loading); retry in 5s")
        time.sleep(5)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    rnd = 0
    next_event_idx = 0

    # nag scheduler: periodic reminders injected into the Events line,
    # including the save autosave nag (~10 min)
    # nags stay ACTIVE (injected every round) until their trigger tool fires
    nags = [
        {"every": 600, "last": 0, "active": False, "trigger": "save_game", "msg":
         "SAVE NAG: it has been ~10 minutes since the last save. Call save_game "
         "with a fresh name like 'autosave-tick-<tick>' THIS round."},
        {"every": 300, "last": 0, "active": False, "trigger": None, "msg":
         "ECONOMY NAG: review RU income — make sure 2+ ResourceCollectors are "
         "harvesting and one more is queued if income is low."},
        {"every": 480, "last": 0, "active": False, "trigger": None, "msg":
         "FLEET NAG: review the fleet — if you out-number the enemy 2:1, focus "
         "attacks on ONE enemy ship; if losing, retreat fighters to guard the Mothership."},
    ]
    last_save_action = None

    while rnd < a.max_rounds:
        rnd += 1
        try:
            snap = json.loads(mcp.call("observe", {})["result"]["content"][0]["text"])
            if isinstance(snap, dict) and "result" in snap and "ships" not in snap:
                snap = snap["result"]
            if "error" in snap:
                log("observe error: " + snap["error"])
                time.sleep(5)
                continue
            view = compress_snapshot(snap)
            log("[round %d] world: %s" % (rnd, world_summary(view)))

            if not snap.get("matchStatus", {}).get("running", False):
                log("match not running — waiting/rebinding")
                time.sleep(5)
                ensure_bound(mcp, a.player)
                continue

            # nags: activate when due, stay injected until trigger tool fires
            now = time.time()
            due = []
            for n in nags:
                if not n["active"] and now - n["last"] >= n["every"]:
                    n["active"] = True
                    n["last"] = now
                    log("[round %d] NAG activated: %s" % (rnd, n["msg"].split(':')[0]))
                if n["active"]:
                    due.append(n["msg"])
            # remember when the agent last ordered a save
            save_action = None
            # (save detection happens post-action; see below)

            evs = json.loads(mcp.call("get_events", {})["result"]["content"][0]["text"])
            events = evs["result"]["events"]
            fresh = events[:max(0, len(events) - next_event_idx)] if events else []
            next_event_idx = len(events)
            if fresh:
                log("[round %d] events: %s" % (rnd, json.dumps(fresh[:4])[:240]))

            nagline = "\n".join(due) if due else ""
            messages.append({
                "role": "user",
                "content": ("Events: " + json.dumps(fresh[:4]) + "\n"
                            + nagline + "\n"
                            "Snapshot:\n" + json.dumps(view) +
                            "\nCommand the fleet (one tool call).")})

            log("[round %d] reasoning+answer stream:" % rnd)
            content, calls = llm.chat_stream(messages)
            messages = messages[-41:]

            if not calls:
                log("[round %d] no tool call; nudging back to command" % rnd)
                messages.append({"role": "assistant", "content": content or "..."})
                messages.append({"role": "user",
                                 "content": "Call a tool to command the fleet."})
                time.sleep(a.interval)
                continue

            for call in calls:
                name = call["name"]
                if name not in TOOL_NAMES:
                    log("[round %d] unknown tool %s" % (rnd, name))
                    continue
                try:
                    args = json.loads(call["args"] or "{}")
                except ValueError:
                    args = {}
                if "id" in args and not isinstance(args["id"], (int, float)):
                    args["id"] = int(time.time()) % 100000
                log("[round %d] tool  : %s %s" % (
                    rnd, name, json.dumps(args)[:150]))
                if name == "save_game":
                    nags[0]["active"] = False      # save nag satisfied
                    nags[0]["last"] = time.time()
                    log("[round %d] save nag satisfied" % rnd)
                result = result_text(mcp.call(name, args))
                log("[round %d] result: %s" % (rnd, result))
                cid = call.get("id") or f"r{rnd}"
                messages.append({
                    "role": "assistant", "content": None,
                    "tool_calls": [{"id": cid, "type": "function",
                                    "function": {"name": name,
                                                 "arguments": json.dumps(args)}}]})
                messages.append({"role": "tool", "tool_call_id": cid,
                                  "content": result[:400]})
            time.sleep(a.interval)
        except KeyboardInterrupt:
            log("agent stopped by user")
            return
        except Exception as e:
            import traceback
            log("[round %d] ERROR (recovering, 10s backoff): %r" % (rnd, e))
            log("trace: " + traceback.format_exc(limit=3).replace("\n", " | ")[:400])
            try:
                ensure_bound(mcp, a.player)
            except Exception:
                pass
            time.sleep(10)

    log("round budget exhausted after %d rounds" % rnd)


if __name__ == "__main__":
    main()