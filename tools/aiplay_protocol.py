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


def _s(required=True):
    return {"type": "string", "required": required}


def _i(required=True, min=None):
    d = {"type": "integer", "required": required}
    if min is not None:
        d["min"] = min
    return d


def _b(required=True):
    return {"type": "boolean", "required": required, "coerce": "on_off"}


def _ships(required=True, max=64):
    return {"type": "array", "items": "integer", "required": required,
            "max": max}


def _coord(required=True):
    return {"type": "array", "items": "number", "required": required,
            "length": 3}


def _target(required=True):
    return {"type": "integer", "required": required}


ORDER_VERBS = {
    "order_move":       {"funnel": "clWrapMove", "params": {
        "ships": _ships(), "target": _coord()}},
    "order_attack":     {"funnel": "clWrapAttack", "params": {
        "ships": _ships(), "target": _target()}},
    "order_guard":      {"funnel": "clWrapProtect", "params": {
        "ships": _ships(), "target": _target()}},
    "order_dock":       {"funnel": "clWrapDock", "params": {
        "ships": _ships(), "dockWith": _target()}},
    "order_harvest":    {"funnel": "clWrapCollectResource", "params": {
        "ships": _ships(), "target": _target()}},
    "order_special":    {"funnel": "clWrapSpecial", "params": {
        "ships": _ships(), "target": _target(required=False)}},
    "order_halt":       {"funnel": "clWrapHalt", "params": {
        "ships": _ships()}},
    "order_build":      {"funnel": "clWrapDeterministicBuild", "params": {
        "ships": _ships(required=False), "type": _s(),
        "quantity": _i(required=False, min=1)}},
    "order_research":   {"funnel": "clWrapResearch", "params": {
        "tech": _s()}},
    "order_formation":  {"funnel": "clWrapFormation", "params": {
        "ships": _ships(), "formation": _s()}},
    "order_tactics":    {"funnel": "clWrapSetTactics", "params": {
        "ships": _ships(), "tactics": _s()}},
    "order_launch":     {"funnel": "clWrapLaunchMultipleShips", "params": {
        "ships": _ships()}},
    "autolaunch":       {"funnel": "clWrapAutoLaunch", "params": {
        "onOff": _b()}},
    "order_scuttle":    {"funnel": "clWrapScuttle", "params": {
        "ships": _ships()}},
    "order_select":     {"funnel": None, "params": {"ships": _ships()}},
    # -- new funnels (spec 003 FR-001) --
    "order_kamikaze":   {"funnel": "clWrapSetKamikaze", "params": {
        "ships": _ships(), "onOff": _b(required=False)}},
    "order_parade":     {"funnel": "clWrapSetMilitaryParade", "params": {
        "ships": _ships(), "onOff": _b(required=False)}},
    "order_ru_transfer": {"funnel": "clWrapRUTransfer", "mp_only": True,
                          "params": {"to": _i(min=0), "amount": _i(min=0)}},
    "order_alliance":   {"funnel": "clWrapSetAlliance", "mp_only": True,
                         "params": {"player": _i(min=0), "onOff": _b()}},
    "order_hyperspace": {"funnel": "clWrapMpHyperspace", "mp_only": True,
                         "params": {"ships": _ships(), "target": _coord()}},
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
    "params": {
        "bind_session": {"playerIndex": _i(required=False, min=0)},
        "observe":      {"sections": {"type": "array", "items": "string",
                                      "required": False},
                         "owner": _i(required=False, min=0)},
        "step":         {"ticks": _i(required=False, min=1)},
        "save_game":    {"name": _s(required=False)},
        "game_status": {}, "get_events": {}, "pause": {}, "resume": {},
    },
    "order_tools": ORDER_VERBS,
    "funnel_overrides": FUNNEL_OVERRIDES,
}

SCHEMA_TYPES = {"string", "integer", "boolean", "array"}


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
        elif isinstance(snap[key], bool) != (typ is bool) and \
                not isinstance(snap[key], typ):
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