#!/usr/bin/env python3
"""aiplay_protocol.py — single source of truth for the AIPlay MCP surface.

Spec: specs/003-full-coverage-aiplay-mcp-engine-i-o-full (FR-008/FR-012).
Owns:
  REGISTRY       every MCP tool: UDS method/order verb, funnel, param schema
  encode_call    MCP (tool, params) -> UDS wire {id, method, params}
  decode_call    UDS wire -> (tool|"reject", params|error-payload)
  encode_result / decode_result   UDS result/error -> MCP payloads
Id namespaces: the envelope `id` and the order `id` are distinct; order tools
keep their `id` echoed in params (engine events echo it), and it doubles as
the envelope id only when numeric.
"""
import json

# ---------------------------------------------------------------- error codes
# enum (src/AIPlay/aiplay.h) <-> code string (src/AIPlay/aiplay.c:31)
ERROR_ENUM_TO_CODE = {
    1: "E_NO_SESSION", 2: "E_BAD_ID", 3: "E_UNAFFORDABLE",
    4: "E_NO_BUILDER", 5: "E_NO_LABS", 6: "E_MATCH_OVER",
    7: "E_QUEUE_FULL", 8: "E_BAD_REQUEST", 9: "E_INTERNAL",
}
ERROR_CODE_TO_ENUM = {v: k for k, v in ERROR_ENUM_TO_CODE.items()}

# ------------------------------------------------------------------- schema
#
# Every tool carries a description and per-param descriptions (written for
# an LLM consuming the MCP surface: what it does, when to use it, what
# params mean in game terms). These are THE descriptions; the sidecar and
# any client generate their tool lists from them (FR-008/FR-012).


def _s(required=True, desc=None):
    d = {"type": "string", "required": required}
    if desc:
        d["desc"] = desc
    return d


def _i(required=True, min=None, desc=None):
    d = {"type": "integer", "required": required}
    if min is not None:
        d["min"] = min
    if desc:
        d["desc"] = desc
    return d


def _b(required=True, desc=None):
    d = {"type": "boolean", "required": required, "coerce": "on_off"}
    if desc:
        d["desc"] = desc
    return d


def _ships(required=True, max=64, desc=None):
    d = {"type": "array", "items": "integer", "required": required,
         "max": max}
    if desc:
        d["desc"] = desc
    return d


def _coord(required=True, desc=None):
    d = {"type": "array", "items": "number", "required": required,
         "length": 3}
    if desc:
        d["desc"] = desc
    return d


def _target(required=True, desc=None):
    d = {"type": "integer", "required": required}
    if desc:
        d["desc"] = desc
    return d


SHIPS_D = "[ship ids] from the last snapshot (ids are stable only within" \
          " this match; re-observe after any reset)"
ID_D = ("your request id (any positive integer); echoed in orderApplied/"
        "orderRejected events so you can track this exact order")

ORDER_VERBS = {
    "order_move": {
        "funnel": "clWrapMove", "desc":
        "Move ships to a 3D coordinate. Use to position/reposition scouts, "
        "fleets and harvest-adjacent staging. Ships keep their formation "
        "behavior when engaged.",
        "params": {
            "ships": _ships(desc=SHIPS_D),
            "target": _coord(desc="destination [x,y,z] world coordinates "
                                  "(units are meters; copy the coordinate "
                                  "system from ship `pos` fields)"),
            "id": _i(desc=ID_D, required=False)}},
    "order_attack": {
        "funnel": "clWrapAttack", "desc":
        "Attack enemy ships. Group fire on ONE target kills faster "
        "(2:1+ superiority: focus single targets); fighters may keep "
        "auto-engaging other intercept targets.",
        "params": {
            "ships": _ships(desc=SHIPS_D),
            "target": _target(desc="enemy ship id to attack (from "
                                   "snapshot ships with owner != bound)"),
            "id": _i(desc=ID_D, required=False)}},
    "order_guard": {
        "funnel": "clWrapProtect", "desc":
        "Guard/protect a target: escorts follow and defend it. Use to "
        "screen ResourceCollectors, the Mothership or research ships "
        "with fighters/corvettes.",
        "params": {
            "ships": _ships(desc=SHIPS_D),
            "target": _target(desc="ship id to protect"),
            "id": _i(desc=ID_D, required=False)}},
    "order_dock": {
        "funnel": "clWrapDock", "desc":
        "Dock ships for refuel/repair (ResourceCollectors also unload RU "
        "here). Retreating damaged fighters to a carrier/mothership heals "
        "them; collectors dock at the Mothership to bank their cargo.",
        "params": {
            "ships": _ships(desc=SHIPS_D),
            "dockWith": _target(desc="dock-capable ship id "
                                     "(Mothership/Carrier/ResearchShip...)"),
            "id": _i(desc=ID_D, required=False)}},
    "order_harvest": {
        "funnel": "clWrapCollectResource", "desc":
        "Send ResourceCollectors to harvest a specific asteroid. Income "
        "only accrues while collectors cycle harvest -> dock to unload; "
        "collectors sitting in parade are idle economy — keep 2+ "
        "harvesting, then one more queued (ECONOMY NAG rule).",
        "params": {
            "ships": _ships(desc=SHIPS_D),
            "target": _target(desc="resource (asteroid) id from the "
                                   "snapshot `resources` array — must be a "
                                   "resource, else E_BAD_ID"),
            "id": _i(desc=ID_D, required=False)}},
    "order_special": {
        "funnel": "clWrapSpecial", "desc":
        "Trigger a ship's special ability (cloaking, salvage by "
        "SalCapCorvette, grav-well by GravWellGenerator, repair by "
        "RepairCorvette...). Target ship id engages the ability on it.",
        "params": {
            "ships": _ships(desc=SHIPS_D),
            "target": _target(desc="optional target ship id for targeted "
                                   "abilities (e.g. salvage)", required=False),
            "id": _i(desc=ID_D, required=False)}},
    "order_halt": {
        "funnel": "clWrapHalt", "desc":
        "Abort queued commands and hold position. Use to break out of a "
        "bad/looping order (e.g. a move to [0,0,0]) before re-issuing.",
        "params": {
            "ships": _ships(desc=SHIPS_D),
            "id": _i(desc=ID_D, required=False)}},
    "order_build": {
        "funnel": "clWrapDeterministicBuild", "desc":
        "Queue ship construction. `type` must be a valid hull name for "
        "the race and free of `techTree.locked` missing-techs (unknown "
        "types are rejected synchronously with E_BAD_REQUEST). Fighters "
        "BUILD DOCKED: call order_launch or set autolaunch on to deploy. "
        "Opening: 2 ResourceCollectors, then ResearchShip. Rejects "
        "E_UNAFFORDABLE when RU < cost*quantity.",
        "params": {
            "ships": _ships(required=False,
                            desc="builder id (default: Mothership)"),
            "type": _s(desc="ship type name exactly as in the snapshot/"
                            "playbook (e.g. ResourceCollector, "
                            "LightInterceptor, ResearchShip, "
                            "IonCannonFrigate)"),
            "quantity": _i(min=1, required=False,
                           desc="how many to queue (default 1)"),
            "id": _i(desc=ID_D, required=False)}},
    "order_research": {
        "funnel": "clWrapResearch", "desc":
        "Start researching a tech (labs = one per owned ResearchShip). "
        "Fleet-wide: ignores `ships`. Chain order matters: MassDrive1Kt "
        "< Chassis1 < MassDrive10Kt < Chassis2 < MassDrive100Kt < "
        "Chassis3 < MassDrive1Mt. busy lab → E_NO_LABS (re-issue when "
        "free); already-known tech → E_BAD_REQUEST.",
        "params": {
            "tech": _s(desc="tech name exactly as in techTree (e.g. "
                            "MassDrive1Kt, Chassis1, TargetingSystems)"),
            "id": _i(desc=ID_D, required=False)}},
    "order_formation": {
        "funnel": "clWrapFormation", "desc":
        "Arrange a ship group in a formation. Use sparingly: ships in "
        "formation move at the slowest ship's speed until engaged.",
        "params": {
            "ships": _ships(desc=SHIPS_D),
            "formation": _s(desc="formation name (delta | broad | "
                                 "sphere | ... as in the Build UI)"),
            "id": _i(desc=ID_D, required=False)}},
    "order_tactics": {
        "funnel": "clWrapSetTactics", "desc":
        "Set combat stance: aggressive (auto-engage, max dps), neutral, "
        "evasive (retreat behavior; use for damaged strike craft or when "
        "holding a defensive wall). Warships in parade still auto-engage "
        "on aggressive.",
        "params": {
            "ships": _ships(desc=SHIPS_D),
            "tactics": _s(desc="aggressive | neutral | evasive"),
            "id": _i(desc=ID_D, required=False)}},
    "order_launch": {
        "funnel": "clWrapLaunchMultipleShips", "desc":
        "Launch all docked fighters from the carrier/mothership id. "
        "Required after fighter builds finish unless autolaunch is on.",
        "params": {
            "ships": _ships(desc="the dock-capable ship id "
                                 "(carrier/mothership)"),
            "id": _i(desc=ID_D, required=False)}},
    "autolaunch": {
        "funnel": "clWrapAutoLaunch", "desc":
        "Toggle auto-launch: new fighters launch as soon as built. Turn "
        "ON once at match start and stop worrying about docking.",
        "params": {
            "onOff": _b(desc="true=on (auto-launch built fighters)"),
            "id": _i(desc=ID_D, required=False)}},
    "order_scuttle": {
        "funnel": "clWrapScuttle", "desc":
        "Self-destruct ships. LAST RESORT only (denial): no refunds, no "
        "undo. Never scuttle economy/research ships by accident.",
        "params": {
            "ships": _ships(desc=SHIPS_D),
            "id": _i(desc=ID_D, required=False)}},
    "order_kamikaze": {
        "funnel": "clWrapSetKamikaze", "desc":
        "Set kamikaze on strike craft (one-way ramming attack — the ship "
        "is typically destroyed). Effective vs unarmed targets "
        "(ResourceCollectors, resourcers); piecemeal (small groups "
        "sequentially) beats one big wave; never useful against "
        "strike-craft counters. Costs your ship — denial economics.",
        "params": {
            "ships": _ships(desc="strike craft ids to launch the "
                                 "kamikaze run"),
            "onOff": _b(required=False,
                        desc="true=kamikaze mode on (default on)"),
            "id": _i(desc=ID_D, required=False)}},
    "order_parade": {
        "funnel": "clWrapSetMilitaryParade", "desc":
        "Set ships to military parade: inactive/stationed near the "
        "Mothership. Warships still auto-engage enemies on aggressive "
        "tactics; ResourceCollectors in parade harvest NOTHING.",
        "params": {
            "ships": _ships(desc=SHIPS_D),
            "onOff": _b(required=False,
                        desc="true=set parade on, false=release from "
                             "parade (default on)"),
            "id": _i(desc=ID_D, required=False)}},
    "order_ru_transfer": {
        "funnel": "clWrapRUTransfer", "desc":
        "Transfer resource units to another player (multiplayer team "
        "play). Amount is deducted from your bank (E_UNAFFORDABLE if "
        "insufficient).",
        "params": {
            "to": _i(min=0, desc="target player index (0=you; must "
                                 "differ)"),
            "amount": _i(min=0, desc="RU amount to give"),
            "id": _i(desc=ID_D, required=False)}},
    "order_alliance": {
        "funnel": "clWrapSetAlliance", "desc":
        "Offer/break alliance with another player (multiplayer). "
        "Allied players stop auto-attacking each other and may share "
        "RU transfers.",
        "params": {
            "player": _i(min=0, desc="other player index"),
            "onOff": _b(desc="true=offer/form alliance, false=break"),
            "id": _i(desc=ID_D, required=False)}},
    "order_hyperspace": {
        "funnel": "clWrapMpHyperspace", "desc":
        "Near-jump (short-range hyperspace) a capital-capable group to a "
        "coordinate. Use to surprise-deploy around a fight "
        "(e.g. behind the enemy fleet) or to redeploy capital ships fast.",
        "params": {
            "ships": _ships(desc="ships capable of hyperspace"),
            "target": _coord(desc="jump destination [x,y,z]"),
            "id": _i(desc=ID_D, required=False)}},
    "order_select": {
        "funnel": None, "desc":
        "Layer-side selection (no engine state change). Kept for "
        "completeness — the human UI selection is never altered by "
        "the AI layer.",
        "params": {
            "ships": _ships(desc=SHIPS_D),
            "id": _i(desc=ID_D, required=False)}},
}

# funnels covered by a named tool under a different path, with justification
# (SC-001 completeness without inventing new surface):
FUNNEL_OVERRIDES = {
    "clWrapBuildShip":   "covered by order_build (deterministic path; "
                         "raw BuildShip is a non-queueing variant of the "
                         "same funnel, single-player-semantic)",
    "clWrapCreateShip":  "covered by order_build (instant-materialize "
                         "variant, dev/debug semantics; same funnel path "
                         "with flags=CMD_START queueing)",
}

REGISTRY = {
    # session/state/time (METHOD_FOR_TOOL semantics)
    "state_tools": {
        "bind_session":  "aiSession.bind",
        "game_status":   "aiSession.status",
        "observe":       "state.snapshot",
        "get_events":    "state.events",
        "pause":         "time.pause",
        "resume":        "time.resume",
        "step":          "time.step",
        "save_game":     "game.save",
    },
    "descriptions": {
        "state_tools": {
            "bind_session":
                "One-time bind of the AI to a player side of the running "
                "match (default playerIndex 0). Exactly ONE binder per "
                "match (the commander); rebind same player is a no-op, "
                "bind another player is rejected (FR-010).",
            "game_status":
                "Session + match status: state (lobby/playing/matchOver), "
                "boundPlayerIndex (−1 = unbound/rebind needed), mapName, "
                "running/paused/tick. Cheap heartbeat call.",
            "observe":
                "Full battlefield snapshot: players (RU banks), ALL ships "
                "with health/weapons/current order (incl. enemy ships — "
                "their `order.targetId` shows whom they attack), "
                "asteroids with RU value, your build queue, active "
                "research and techTree (locked hulls + missing techs). "
                "This is the primary read: call it every decision round.",
            "get_events":
                "Newest-first event ring (≤256) since match start: "
                "orderApplied/orderRejected (audit your orders!), "
                "shipDestroyed (aux = killer owner+1 — your under-attack "
                "signal), matchStarted/Over/Continued, bindChanged.",
            "pause":
                "Pause the simulation (orders still queue). Use for deep "
                "re-planning; snapshots are deterministic while paused.",
            "resume":
                "Resume the simulation after pause.",
            "step":
                "Advance exactly N ticks while paused (default 1) and "
                "re-observe — deterministic what-if stepping. Requires "
                "paused state, else E_BAD_REQUEST.",
            "save_game":
                "Save the match (name auto-sanitized; default "
                "aiplay-autosave-<tick>). Call every ~10 minutes when "
                "nagged. Returns the saved name.",
        },
        "local_tools": {
            "guide":
                "Return the AI strategy guide: build costs, research "
                "trees + chains, opening playbook, event/dictionary "
                "semantics and multiplayer strategy tips (from the game "
                "docs: ai-playbook, snapshot dictionary, gameplay "
                "doctrine). Read this once before commanding.",
        },
    },
    "params": {
        "bind_session": {"playerIndex": _i(required=False, min=0,
                                           desc="player index to command "
                                                "(default 0)")},
        "observe":      {"sections": {"type": "array", "items": "string",
                                      "required": False,
                                      "desc": "subset to return: "
                                              "ships and/or resources "
                                              "(default: both)"},
                         "owner": _i(required=False, min=0,
                                     desc="only objects owned by this "
                                          "player index")},
        "step":         {"ticks": _i(required=False, min=1,
                                     desc="ticks to simulate (default 1)")},
        "save_game":    {"name": _s(required=False,
                                    desc="savegame name, max 64 safe "
                                         "chars")},
        "game_status": {}, "get_events": {}, "pause": {}, "resume": {},
        "guide": {},
    },
    "returns": {
        "state_tools": {
            "bind_session":
                "{playerIndex, sessionId, mapName} — or error: E_MATCH_OVER "
                "(match over), E_BAD_REQUEST (out-of-range/second binding). "
                "Emits bindChanged (first bind) or matchContinued (no-op "
                "rebind) events.",
            "game_status":
                "{state, boundPlayerIndex, sessionId, mapName, matchStatus:"
                "{running, paused, tick}} — boundPlayerIndex −1 means the "
                "engine restarted: rebind.",
            "observe":
                "Snapshot object: {tick, timeElapsed, paused, income "
                "(RU/min, −1 unknown/paused), players[], ships[], "
                "resources[], buildQueue[], research[], techTree{}, "
                "matchStatus{running, boundPlayerIndex, winner|null, "
                "perPlayer[]}}. `sections` narrows ships/resources; both "
                "keys always present.",
            "get_events":
                "{events: [{type, tick, id, aux, owner}, ...]} newest "
                "first, ≤256 (ring). audit orderApplied/orderRejected "
                "against your order ids.",
            "pause": "{paused: true}.",
            "resume": "{paused: false}.",
            "step":
                "{stepped: N, tick: <new tick>} — or E_BAD_REQUEST when "
                "not paused. Snapshots during pause are byte-identical.",
            "save_game":
                "{saved: finalName} — or E_INTERNAL. Name reflects "
                "sanitation (safe chars only).",
        },
        "local_tools": {
            "guide": "Markdown text: playbook + strategy digest + snapshot "
                     "dictionary concatenated.",
        },
        "orders":
            "{queued: true, orderId: <your id>} for accepted orders — "
            "then orderApplied (or orderRejected aux=<error enum>) appears "
            "in the next events poll; rejection at submit returns "
            "{error: {code, message}} synchronously (E_BAD_ID stale id, "
            "E_UNAFFORDABLE, E_NO_BUILDER, E_NO_LABS, E_MATCH_OVER, "
            "E_QUEUE_FULL, E_BAD_REQUEST).",
    },
    "order_tools": ORDER_VERBS,
    "funnel_overrides": FUNNEL_OVERRIDES,
    # local (sidecar-served, no UDS): doctrine/guide
    "local_tools": {"guide": "aiplay.guide"},
}

SCHEMA_TYPES = {"string", "integer", "boolean", "array"}

# condensed doctrine served as MCP server `instructions` (tools/list detail
# lives in descriptions above); digested from the community docs + the
# multiplayer strategy doctrine (gameplay.txt, HW1-applicable)
INSTRUCTIONS = """Gardens of Kadesh AI command guide (condensed — call the `guide`
tool for the full playbook):
1. ECONOMY FIRST: keep 2+ ResourceCollectors harvesting at all times and
   one more queued; `income` (RU/min) near 0 means nobody is harvesting.
   Kill enemy collectors with kamikaze/strike groups before his fleet forms.
2. OPENING: build 2x ResourceCollector, then a ResearchShip (labs come
   from ResearchShips), autolaunch on, then research the chain:
   MassDrive1Kt -> Chassis1 -> MassDrive10Kt -> Chassis2 -> MassDrive100Kt
   -> Chassis3 -> IonWeapons (2x IonCannonFrigate = mid-game win pair).
3. FLEET BEFORE ATTACK: attack only when you out-number the enemy ~2:1 or
   his fleet is committed elsewhere; groups of 3-4 fighters focus ONE
   target; retreat (order_guard your Mothership) below ~50% hp.
4. READ THE SNAPSHOT: enemy ship `order.targetId` names whom he attacks;
   shipDestroyed events (aux = killer owner+1) are the under-attack alarm.
   ResourceCollectors with order `parade` are idle economy — harvest them.
5. EVERY order tool takes `id` — echo it in events (orderApplied/
   orderRejected) so you can audit. orderRejected aux: 2=E_BAD_ID
   (stale id: re-observe), 3=E_UNAFFORDABLE, 5=E_NO_LABS, 8=E_BAD_REQUEST.
6. pause/step for deterministic what-ifs; ship ids are invalid after any
   match restart — rebind (bind_session) then re-observe.
"""


def on_off(value):
    """bool/0/1/str -> engine 'on'/'off' (coercion present in the engine
    parser: aiplay_orders.c parseOrder onOff handling)."""
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, (int, float)) and value in (0, 1):
        return "on" if value else "off"
    if isinstance(value, str):
        low = value.lower()
        if low in ("on", "1"):
            return "on"
        if low in ("off", "0"):
            return "off"
    return value


def _params_for(tool):
    if tool in REGISTRY["order_tools"]:
        return REGISTRY["order_tools"][tool].get("params") or {}
    return REGISTRY["params"].get(tool)


def _is_order(tool):
    return tool in REGISTRY["order_tools"]


def _validate(tool, params):
    """Return None if params conform to the tool's schema, else an error
    payload {'code', 'message'} (sync rejection, FR-009)."""
    schema = _params_for(tool)
    if schema is None:
        return {"code": "E_BAD_REQUEST",
                "message": "unknown tool %s" % tool}
    if not isinstance(params, dict):
        return {"code": "E_BAD_REQUEST", "message": "params must be object"}
    for key, rule in schema.items():
        val = params.get(key)
        if val is None:
            if rule.get("required"):
                return {"code": "E_BAD_REQUEST",
                        "message": "missing %s for %s" % (key, tool)}
            continue
        t = rule["type"]
        if t == "integer":
            if not isinstance(val, (int, float)) or isinstance(val, bool) \
                    or int(val) != val:
                return {"code": "E_BAD_REQUEST",
                        "message": "%s must be integer" % key}
            if rule.get("min") is not None and val < rule["min"]:
                return {"code": "E_BAD_REQUEST",
                        "message": "%s must be >= %d" % (key, rule["min"])}
        elif t == "boolean":
            if isinstance(val, (bool, int)) or \
                    (isinstance(val, str) and val.lower() in ("on", "off", "0", "1")):
                continue                       # coerced later
            return {"code": "E_BAD_REQUEST",
                    "message": "%s must be boolean" % key}
        elif t == "string":
            if not isinstance(val, str) or val == "":
                return {"code": "E_BAD_REQUEST",
                        "message": "%s must be a non-empty string" % key}
        elif t == "array":
            if not isinstance(val, list):
                return {"code": "E_BAD_REQUEST",
                        "message": "%s must be an array" % key}
            if rule.get("items") == "integer":
                for v in val:
                    if not isinstance(v, (int, float)) or \
                            isinstance(v, bool) or int(v) != v:
                        return {"code": "E_BAD_REQUEST",
                                "message": "%s items must be integers" % key}
            if rule.get("length") and len(val) != rule["length"]:
                return {"code": "E_BAD_REQUEST",
                        "message": "%s must have %d entries"
                                   % (key, rule["length"])}
    ships = schema.get("ships")
    if ships:
        val = params.get("ships")
        if val is not None and len(val) > ships["max"]:
            return {"code": "E_BAD_REQUEST",
                    "message": "ships max %d per order" % ships["max"]}
    if tool == "order_move":
        if not isinstance(params.get("target"), list):
            return {"code": "E_BAD_REQUEST", "message": "move needs coord"}
    return None


def encode_call(tool, params):
    """MCP (tool, params) -> UDS wire message. Validation happens here too:
    an invalid call returns the wire envelope that decodes to 'reject'."""
    envelope = 1
    wire = dict(params or {})
    if _is_order(tool):
        rid = wire.get("id")
        if isinstance(rid, (int, float)):
            envelope = rid
    else:
        # order-id namespace (params.id) is meaningless for state tools
        wire.pop("id", None)
    err = _validate(tool, wire)
    if err:
        return {"id": envelope, "error": err}
    if tool in REGISTRY["state_tools"]:
        wire_params = {k: v for k, v in wire.items()} or None
        msg = {"id": envelope, "method": REGISTRY["state_tools"][tool]}
        if wire_params:
            msg["params"] = wire_params
        return msg
    verb = tool[len("order_"):] if tool.startswith("order_") else tool
    return {"id": envelope, "method": "order." + verb, "params": wire}


def decode_call(wire):
    """UDS wire -> (tool, params) or ('reject', error payload)."""
    if "error" in wire:
        return "reject", wire["error"]
    method = wire.get("method", "")
    params = dict(wire.get("params") or {})
    if method in ("aiSession.bind", "aiSession.status", "state.snapshot",
                  "state.events", "time.pause", "time.resume", "time.step",
                  "game.save"):
        for t, m in REGISTRY["state_tools"].items():
            if m == method:
                return t, params
    if method.startswith("order."):
        verb = method[6:]
        for t in REGISTRY["order_tools"]:
            want = t[len("order_"):] if t.startswith("order_") else t
            if want == verb:
                err = _validate(t, params)
                if err:
                    return "reject", err
                # coercion
                for key, rule in _params_for(t).items():
                    if rule.get("coerce") == "on_off" and key in params:
                        params[key] = on_off(params[key])
                return t, params
    return "reject", {"code": "E_BAD_REQUEST",
                      "message": "unknown method %s" % method}


def encode_result(payload):
    """MCP result payload -> UDS {id, result} (wrapped by the transport)."""
    return {"id": 1, "result": payload}


def decode_result(wire):
    """UDS {id, result}|{id, error} -> ('ok'|'error', payload)."""
    if "error" in wire:
        return "error", wire["error"]
    return "ok", wire.get("result")


def dumps(wire):
    return json.dumps(wire)


# ------------------------------------------------- snapshot/event schema
# (data-model.md: authoritative field tables; FR-004/FR-006)

ORDER_STATE_VERBS = {"move", "attack", "guard", "dock", "harvest", "launch",
                     "special", "halt", "parade", "passive-attack", "moving",
                     "order?"}

EVENT_TYPES = {"matchStarted", "matchOver", "orderApplied", "orderRejected",
               "shipDestroyed", "bindChanged", "matchContinued"}


def validate_snapshot(snap):
    """Validate engine snapshot against the authoritative schema; return a
    list of drift errors (empty = faithful). Used by the sidecar to fail
    loud on schema drift (US1 T010)."""
    errs = []
    add = errs.append
    if not isinstance(snap, dict):
        return ["snapshot must be an object"]
    for key, typ in (("tick", int), ("paused", bool), ("income", (int, float))):
        if key not in snap:
            add("missing %s" % key)
            continue
        v = snap[key]
        ok = isinstance(v, bool) if typ is bool else \
            isinstance(v, typ) and not isinstance(v, bool)
        if not ok:
            add("%s wrong type" % key)
    for arr in ("players", "ships", "resources", "buildQueue", "research"):
        if arr not in snap:
            add("missing %s" % arr)
        elif not isinstance(snap[arr], list):
            add("%s must be list" % arr)
    for p in snap.get("players", []):
        for f in ("index", "isAI", "resourceUnits", "alive", "lost"):
            if f not in p:
                add("player missing %s" % f)
    for s in snap.get("ships", []):
        for f in ("id", "owner", "type", "pos", "vel", "health", "healthMax",
                  "dps", "guns", "range", "order"):
            if f not in s:
                add("ship %s missing %s" % (s.get("id"), f))
        if isinstance(s.get("health"), (int, float)) and \
                isinstance(s.get("healthMax"), (int, float)) and \
                s["healthMax"] > 0 and \
                (s["health"] < 0 or s["health"] > s["healthMax"] * 2):
            add("ship %s health unclamped" % s.get("id"))
        o = s.get("order")
        if o is not None:
            if not isinstance(o, dict) or o.get("verb") not in ORDER_STATE_VERBS:
                add("ship %s bad order verb %r" % (s.get("id"), o))
    for r in snap.get("resources", []):
        for f in ("id", "value", "pos"):
            if f not in r:
                add("resource missing %s" % f)
    tt = snap.get("techTree")
    if tt is not None:
        for f in ("labs", "canResearch", "locked", "researched"):
            if f not in tt:
                add("techTree missing %s" % f)
    ms = snap.get("matchStatus")
    if ms is not None:
        for f in ("running", "boundPlayerIndex", "winner", "perPlayer"):
            if f not in ms:
                add("matchStatus missing %s" % f)
    return errs


def validate_events(res):
    """state.events payload -> drift errors (empty = faithful)."""
    errs = []
    events = (res or {}).get("events")
    if not isinstance(events, list):
        return ["events must be list"]
    for e in events:
        for f in ("type", "tick", "id", "aux", "owner"):
            if f not in e:
                errs.append("event missing %s" % f)
        if e.get("type") not in EVENT_TYPES:
            errs.append("unknown event type %r" % e.get("type"))
        if isinstance(e.get("type"), str) and \
                e["type"] == "orderRejected" and \
                e.get("aux") not in ERROR_ENUM_TO_CODE:
            errs.append("orderRejected aux %r not an aiplayError" % e.get("aux"))
    return errs


if __name__ == "__main__":
    import sys
    name, *rest = sys.argv[1:]
    print(json.dumps(encode_call(name, json.loads(" ".join(rest)) if rest else {})))