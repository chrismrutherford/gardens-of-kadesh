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
import urllib.error

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

class MCPFailure(RuntimeError):
    pass


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

    def chat(self, prompt, expect_tools=True, max_tokens=1400):
        """One-shot non-streaming completion for peer loops (no tools)."""
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system",
                 "content": "You are a read-only advisor. Reply with advice "
                            "text only; you cannot execute orders."},
                {"role": "user", "content": prompt[:12000]},
            ],
            "temperature": self.temperature,
            "max_tokens": max_tokens,
        }
        if not expect_tools:
            payload["chat_template_kwargs"] = {"enable_thinking": True}
        else:
            payload["tools"] = TOOLS
            payload["stream"] = True
        req = urllib.request.Request(
            self.base + "/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=600) as r:
            data = json.loads(r.read())
        msg = data["choices"][0]["message"]
        # NOTE: no reasoning_content fallback — peers must publish only the
        # final answer; leaking chain-of-thought poisons the artifact files
        return msg.get("content") or ""

    def chat_stream(self, messages, log_raw_fn=None, use_tools=True,
                    max_tokens=2600, thinking=True):
        """Consume the SSE stream; log deltas live; return (content, calls).

        log_raw_fn: verbatim delta sink (per-role logger for peers);
        defaults to the commander's log_raw.
        """
        emit = log_raw_fn or log_raw
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "temperature": self.temperature,
            "max_tokens": max_tokens,
            "chat_template_kwargs": {"enable_thinking": thinking},
        }
        if use_tools:
            payload["tools"] = TOOLS
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
                    emit(d["reasoning_content"])
                if d.get("content") is not None:
                    emit(d["content"])
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
        emit("\n")
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

EXECUTOR_PROMPT = """You are the EXECUTOR: a fast fleet-control model for a
Homeworld-style RTS. Big-model advisors own an instruction QUEUE (see
Instruction queue in the round prompt): STANDING CONSTRAINTS always apply
(hold/maintain/keep — never mark them done, never block on them); work
the top TASK first until queue_done / queue_fail, then the next. Call
queue_done ONLY when observably complete (that ship exists in your fleet,
the tech IS researching); call queue_fail after 2-3 failed attempts (with
a note why). If there are no tasks, run the standing audit — never freeze
the fleet.

Standing audit (priority order):
0. INTEL: enemy positions/composition unknown -> build a Probe (30 RU) and
   order_move it toward the enemy contacts NOW. NEVER halt the economy or
   hold builds waiting for intel — go LOOK. Kill enemy probes in our space.
1. ECONOMY FIRST: every ResourceCollector parked/docked/parade gets
   order_harvest (nearest asteroid) BEFORE anything else — income compounds;
   without it you lose. RU bank > 1.5x next build -> build it now.
1b. LOGISTICS: park the Mothership near the resource patch and KEEP it
   there; build a ResourceController when a second patch exists.
2. PRODUCTION: plan hulls missing from buildQueue -> build. techTree.locked
   with EMPTY "missing" = disabled this match — never build/research those.
2b. NO IDLE WARSHIPS: order null/parade/order? -> task them (guard
   Mothership, or attack per doctrine PRIORITY). Docked/parade warships
   are not attackers mid-defense.
3. RESEARCH: labs idle -> order_research the next tech on the research
   path that is NOT in techTree.researched AND not in research[] now
   (tech name from the research list ONLY — NEVER a ship name).
   E_NO_LABS -> build a ResearchShip first. Engine rejects already-
   researched techs.

Doctrine: attack enemy collectors > research ships > warships > Mothership
LAST; interceptors vs strike craft, bombers/ion vs capitals; scuttle
piecemeal scouts into unguarded enemy collectors when behind.

TIME: the game runs in real time — NEVER call pause/step/resume; waiting is
done by issuing the next order next round. Holding is done with your fleet,
not the clock.

Issue MULTIPLE tool calls per round (batch them): fix EVERY gap — harvest
idlers, the build, the research — as parallel native tool calls in one
reply. Cap at 6. Keep replies short; skip long reasoning."""

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
techTree.locked ships: if "missing" lists techs → researchable, research
those. If "missing" is EMPTY → disabled for this match/race (e.g.
cross-race hulls) — do NOT try to research or build those.
Combat math: each ship reports health/healthMax, dps (gun damage/sec),
guns, range — weigh attack plans as (sum own effective dps)/(enemy
sum dps) and retreat when the ratio turns against you. Frigates/caps
outrange corvettes: fight inside your range, outside theirs.
If order_build is rejected with E_BAD_REQUEST, that hull's tech isn't
researched yet — research it (needs labs).

Call 1-6 tools per round as parallel native tool calls (batch independent
actions). Each tool result arrives with the next round. Keep commanding the fleet; when the match is over, call game_status.

Win condition: destroy every enemy ship (their Mothership is the anchor).

Strategy:
1. Keep 2+ ResourceCollectors harvesting (order_harvest nearest asteroid).
2. Build ResourceCollectors early, then combat ships (order_build).
3. Attack in groups of 3+: pick ONE enemy ship id (order_attack).
4. Park the Mothership near the resource patch for harvester trips and
   keep it there; protect it with a guard screen rather than moving it.
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


def guard_call(name, args, snapshot):
    """Post-parse safety for a native tool call (lean version)."""
    if not isinstance(args, dict):
        args = {}
    if "id" not in args or not isinstance(args["id"], (int, float)):
        args["id"] = int(time.time()) % 100000
    if name == "save_game":
        # epoch timestamp: ticks aren't comparable across match restarts,
        # and 13-digit epoch sorts naturally over days
        args["name"] = f"autosave-{int(time.time())}"
    return args


def merge_peer_artifacts():
    """agent-multi hook: fresh peer directives for this round (no LLM I/O)."""
    import os as _os
    import agent_multi as _am
    d = _os.environ.get("AIPLAY_MULTI_DIR")
    if not d:
        return ""
    try:
        fresh, stale = _am.merge_artifacts(_am.ArtifactStore(d))
    except Exception:
        return ""
    out = ""
    if fresh:
        out += "Peer directives (advisory, freshest first):\n" + \
            "\n".join(fresh) + "\n"
    if stale:
        out += "(stale peers ignored: " + "; ".join(stale[:3]) + ")\n"
    return out


def merge_orders_ring(name, args, result_text_snippet, tick):
    """agent-multi hook: record issued orders for the critic peer."""
    import os as _os
    d = _os.environ.get("AIPLAY_MULTI_DIR")
    if not d:
        return
    try:
        import agent_multi as _am
        store = _am.ArtifactStore(d)
        try:
            ring = store.read("orders").get("orders", [])
        except (OSError, ValueError):
            ring = []
        _am.append_order_ring(store, ring,
                              {"tool": name, "args": args,
                               "result": result_text_snippet[:80],
                               "tick": tick})
    except Exception:
        pass


def ships_already_executing(args, verb, snap):
    """True if every requested ship's CURRENT order matches the request."""
    ships = (args.get("ships") or []) if isinstance(args, dict) else []
    if not ships:
        return False
    cur = {s.get("id"): (s.get("order") or {})
           for s in snap.get("ships", [])}
    tgt = args.get("target")
    for sid in ships:
        o = cur.get(sid)
        if not isinstance(o, dict):
            return False          # idle/unknown: let the order through
        v = o.get("verb")
        t = o.get("targetId", o.get("target"))
        if v != verb:
            return False
        if tgt is not None and t is not None:
            if isinstance(t, list) and isinstance(tgt, list):
                if [round(x, 0) for x in t] != [round(x, 0) for x in tgt]:
                    return False
            elif t != tgt:
                return False
    return True


TOOL_VERB = {"order_move": "move", "order_attack": "attack",
             "order_guard": "guard", "order_dock": "dock",
             "order_harvest": "harvest", "order_special": "special",
             "order_kamikaze": "kamikaze", "order_parade": "parade"}


_QUEUE_TOOLS = {"queue_done", "queue_fail"}


QUEUE_ROLES = ("strategist", "analyst", "economist", "critic")


def _queues_for(d):
    import agent_multi as _am
    store = _am.ArtifactStore(d)
    queues = {}
    for role in QUEUE_ROLES:
        q = _am.InstructionQueue(store, role)
        q.load()
        queues[role] = q
    return queues


def commander_queue(rnd):
    """agent-multi hook: render every advisor's queue for this round."""
    import os as _os
    import agent_multi as _am
    d = _os.environ.get("AIPLAY_MULTI_DIR")
    if not d:
        return ""
    try:
        queues = _queues_for(d)
        parts = []
        pickup = []
        for role, q in queues.items():
            if not q.instructions:
                continue
            parts.append("=== %s's instructions ===" % role)
            parts.append(q.prompt_block().rstrip())
            # observability: what the commander is serving this round
            tasks = [t for t in q.instructions
                     if not (t.get("kind") == "standing" or
                             (not t.get("kind") and
                              _am.InstructionQueue._is_standing(t["text"])))
                     and t.get("state") in ("pending", "active")]
            stand = len(q.instructions) - len(tasks)
            if tasks:
                pickup.append("%s:#%d %s [%s]"
                              % (role, tasks[0]["id"],
                                 tasks[0]["text"][:40],
                                 tasks[0]["state"]))
            elif stand:
                pickup.append("%s: standing-only" % role)
        if pickup:
            log("[round %d] QUEUE pickup: %s"
                % (rnd, " | ".join(pickup)))
        if not parts:
            return ""
        return ("\nYou have one queue PER advisor. Do your best to serve "
                "them ALL: batch across queues (up to 6 calls/round); when "
                "you cannot, the CRITIC's latest advice names the most "
                "important.\n" + "\n".join(parts) + "\n")
    except Exception:
        return ""


_queue_state = {"active_id": None}


def commander_queue_action(name, args, result, round_no):
    """Route pseudo-tools + order bookkeeping to the per-role queues."""
    import os as _os
    import agent_multi as _am
    d = _os.environ.get("AIPLAY_MULTI_DIR")
    if not d:
        return False
    try:
        queues = _queues_for(d)
        role = args.get("q") if isinstance(args, dict) else None
        if name in ("queue_done", "queue_fail"):
            # target queue: explicit "q" param, else the queue owning that id
            candidates = [queues[role]] if role in queues else \
                [q for q in queues.values()]
            target, iid = None, None
            try:
                iid = int(args.get("id"))
            except (TypeError, ValueError, AttributeError):
                iid = None
            for q in candidates:
                top = q.state.get("active_ref") or q.topmost()
                if iid is not None and q._indexOf(iid) is not None:
                    target, top = q, q.instructions[q._indexOf(iid)]
                    break
                if iid is None and top is not None and target is None:
                    target, _top = q, top
            if target is not None:
                target.state["active_ref"] = top
                target.mark("done" if name == "queue_done" else "failed",
                            note=str(result)[:120])
                return True
            return False
        # order results: NOTE onto every queue's active task (progress is
        # visible to each editor). Engine errors are NOT fatal to tasks —
        # an unrelated batch error (E_BAD_ID on a stale id) must not kill
        # a queued objective; persistent lack of progress trips the stall
        # flag instead, and the executor's own queue_fail judges imposs.
        for q in queues.values():
            top = q.state.get("active_ref") or q.topmost()
            if top is None:
                continue
            q.note("round %d: %s %s -> %s"
                   % (round_no, name, json.dumps(args)[:60],
                      result[:60]))
        return False
    except Exception:
        return False


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
    import os as _os
    prompt_key = _os.environ.get("AIPLAY_MULTI_PROMPT")  # "executor" override
    base_prompt = EXECUTOR_PROMPT if prompt_key == "executor" else SYSTEM_PROMPT
    # executor must not touch the clock: strip time-control tools
    if prompt_key == "executor":
        global TOOLS
        _TIME_TOOLS = {"pause", "resume", "step"}
        TOOLS = [t for t in TOOLS
                 if t["function"]["name"] not in _TIME_TOOLS]

    session_banner(f"player={a.player} mcp={a.mcp} llm={a.llm} rounds={a.max_rounds}")
    # load the playbook (build costs + research chains extracted from game data)
    pb = ""
    try:
        pbd = __file__.rsplit("/", 1)[0] + "/../documentation/community/"
        pb = open(pbd + "ai-playbook.md").read()
        pb += "\n\n=== SNAPSHOT DATA DICTIONARY (field + verb semantics) ===\n" \
              + open(pbd + "ai-snapshot-dictionary.md").read()
    except OSError:
        pass  # stay lean if missing
    messages_sys = base_prompt + ("\n\n=== PLAYBOOK (authoritative game data) ===\n" + pb) if pb else base_prompt
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

    messages = [{"role": "system", "content": messages_sys}]
    rnd = 0
    next_event_idx = 0
    last_tick_seen = -1

    # nag scheduler: periodic reminders injected into the Events line,
    # including the save autosave nag (~10 min)
    # nags stay ACTIVE (injected every round) until their trigger tool fires
    nags = [
        {"every": 600, "last": 0, "active": False, "trigger": "save_game", "msg":
         "SAVE NAG: it has been ~10 minutes since the last save. Call save_game "
         "— the agent will name it (epoch timestamp) — just call it THIS round."},
        {"every": 300, "last": 0, "active": False, "trigger": None, "msg":
         "ECONOMY NAG: review RU income — make sure 2+ ResourceCollectors are "
         "harvesting and one more is queued if income is low."},
        {"every": 480, "last": 0, "active": False, "trigger": None, "msg":
         "FLEET NAG: you must build a LARGE fleet before attacking — use the "
         "research tree (research, then build) and pick a strategy deliberately: "
         "early rush ONLY if massive surplus resources are available, otherwise "
         "prefer expanding harvesting and building your fleet first, then attack. "
         "If you out-number the enemy 2:1, focus attacks on ONE enemy ship; "
         "if losing, retreat fighters to guard the Mothership."},
    ]
    last_save_action = None
    # dedupe memory: key -> tick of last identical order issue
    last_order_tick = {}

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
            # binding check every round: a fresh/restarted game engine has
            # boundPlayerIndex = -1 (tick-based reset detection can miss
            # restarts when the save's starting tick is >= the old tail);
            # read it from matchStatus (added to the snapshot by the layer)
            if isinstance(snap, dict) and \
                    snap.get("matchStatus", {}).get("boundPlayerIndex", 0) \
                    == -1:
                log("[round %d] engine reports UNBOUND — rebinding" % rnd)
                try:
                    ensure_bound(mcp, a.player)
                except Exception as exc:
                    log("rebind failed (retrying next round): %r" % exc)

            view = compress_snapshot(snap)
            if view["tick"] < last_tick_seen:
                log("[round %d] MATCH RESET detected (tick %d -> %d); ids regenerated"
                    % (rnd, last_tick_seen, view["tick"]))
                next_event_idx = 0          # fresh event stream
                last_order_tick.clear()     # ids regenerated: drop dedupe keys
                # the engine process may be NEW (game restart): binding was
                # lost (boundPlayer=-1) — rebind or every order gets rejected
                try:
                    ensure_bound(mcp, a.player)
                except Exception as exc:
                    log("rebind after reset failed: %r" % exc)
                try:
                    import agent_multi as _am
                    import os as _os
                    _d = _os.environ.get("AIPLAY_MULTI_DIR")
                    if _d:
                        for _r in QUEUE_ROLES:
                            _am.InstructionQueue(
                                _am.ArtifactStore(_d), _r).clear()
                except Exception:
                    pass
            last_tick_seen = max(last_tick_seen, view["tick"])
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
            peerline = merge_peer_artifacts()
            qblock = commander_queue(rnd)
            # TRUNCATION POLICY: full snapshots only for the last 2 rounds;
            # older rounds' snapshot sections collapse to the ~141-char
            # world summary (the 41-message window otherwise carries
            # ~3.7k tokens/round = ~150k tokens vs a 65k ctx = HTTP 400)
            view_json = json.dumps(view)
            summary_line = world_summary(view)
            for i, m in enumerate(messages):
                if m.get("role") == "user" and m["content"] and \
                        isinstance(m["content"], str) and \
                        "\nSnapshot:" in m["content"] and \
                        i < len(messages) - 2:
                    head, _, tail = m["content"].partition("\nSnapshot:")
                    m["content"] = (head + "\n" + summary_line +
                                    "\n(older snapshot elided)" +
                                    tail.rpartition("\nCommand the fleet")[2]
                                    if False else
                                    head + "\n" + summary_line
                                    + "\nCommand the fleet (1-6 parallel tool calls).")
            messages.append({
                "role": "user",
                "content": ("Events: " + json.dumps(fresh[:4]) + "\n"
                            + nagline + "\n"
                            + qblock +
                            peerline +
                            "Snapshot:\n" + view_json +
                            "\nCommand the fleet (1-6 parallel tool calls).")})

            log("[round %d] reasoning+answer stream:" % rnd)
            try:
                content, calls = llm.chat_stream(messages)
            except urllib.error.HTTPError as he:
                if he.code == 400:
                    # context overflow: llama.cpp rejects > ctx — drop the
                    # oldest half of the conversation and retry this round
                    log("[round %d] HTTP 400 (context full) — trimming history"
                        % rnd)
                    messages = messages[:1] + messages[-8:]
                    content, calls = llm.chat_stream(messages)
                else:
                    raise
            messages = messages[-41:]

            if not calls:
                # thinking monologue burned the whole token budget before any
                # tool call — retry THIS round with thinking off so the model
                # emits commands directly (nudge alone just repeats the loop)
                log("[round %d] no tool call after thought; retrying "
                    "thinking-off" % rnd)
                messages.append({"role": "user",
                                 "content": "Stop planning. Emit the tool "
                                 "calls now."})
                content, calls = llm.chat_stream(messages, thinking=False,
                                                 max_tokens=2000)
            if not calls:
                log("[round %d] no tool call; nudging back to command" % rnd)
                messages.append({"role": "assistant", "content": content or "..."})
                messages.append({"role": "user",
                                 "content": "Call a tool to command the fleet."})
                time.sleep(a.interval)
                continue

            executed_this_round = 0
            MAX_CALLS_PER_ROUND = 16
            for call in calls:
                if executed_this_round >= MAX_CALLS_PER_ROUND:
                    log("[round %d] further calls dropped (batch cap)"
                        % rnd)
                    break
                name = call["name"]
                if name not in TOOL_NAMES:
                    log("[round %d] unknown tool %s" % (rnd, name))
                    continue
                try:
                    args = json.loads(call["args"] or "{}")
                except ValueError:
                    args = {}
                args = guard_call(name, args, snap)
                key = (name, tuple(sorted(args.get("ships", []) or [])) if name != "order_harvest" else (args.get("ships") or [None])[0],
                       args.get("target"), args.get("dockWith"),
                       args.get("tech"), args.get("type"),
                       args.get("formation"), args.get("tactics"))
                if key != (None, (), None, None, None, None, None, None) and \
                        not fresh and \
                        last_order_tick.get(key, -10**9) > \
                        view.get("tick", 0) - 80:
                    # identical order issued very recently with nothing
                    # happening since: no double-queue (formation churn!)
                    log("[round %d] duplicate order suppressed (%s)"
                        % (rnd, name))
                    break
                last_order_tick[key] = view.get("tick", 0)
                verb = TOOL_VERB.get(name)
                if not fresh and verb and ships_already_executing(args, verb, snap):
                    # authoritative dedupe: the snapshot shows the requested
                    # ships already executing this exact order
                    log("[round %d] order already executing (%s on %r)"
                        % (rnd, verb, args.get("ships")))
                    break
                log("[round %d] tool  : %s %s" % (
                    rnd, name, json.dumps(args)[:150]))
                if name == "save_game":
                    nags[0]["active"] = False      # save nag satisfied
                    nags[0]["last"] = time.time()
                    log("[round %d] save nag satisfied" % rnd)
                result = result_text(mcp.call(name, args)) \
                    if name not in _QUEUE_TOOLS \
                    else json.dumps({"handled": True})
                if commander_queue_action(name, args, result, rnd) and \
                        name in _QUEUE_TOOLS:
                    log("[round %d] queue %s: instruction marked" % (rnd, name))
                merge_orders_ring(name, args, result, view.get("tick", 0))
                executed_this_round += 1
                log("[round %d] result: %s" % (rnd, result))
                cid = call.get("id") or f"r{rnd}-{executed_this_round}"
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