"""Spec 005 v4: Program packets — schema validation, delegation envelope,
pulse-input builder (FR-004/008/009/013/017/018).

A Program IS the seat's sole strategic intent authority: S2 emits one per
pulse; the executor only translates it. Whole-packet validation
(all-or-nothing, FR-008): any violation rejects the ENTIRE packet, the
previous version stays authoritative, the reason loops to S2's next input.
Subject/target id liveness is NOT checked here — ids go stale on match
load; the executor revalidates against a fresh ledger per attempt.

Delegation envelope (`delegate[]`, FR-017): grants S2 licenses to S1 for
fast tactical action between pulses. Pure data; the executor enforces it.
No grants ⇒ S1 advisory-only for that program generation.

build_pulse_input() assembles the S2 pulse input (FR-009, static-first) and
build_micro_input() the S1 micro-input (FR-018). Budget breaches prune
oldest program history first, then oldest rollups/ledger lines, always
recording alerts (FR-013). P0 items and the charter are never pruned.
"""
import json
import math

import twincore.digest as digest

# verbs (registry order verbs, spec-003 layer)
TASKING_VERBS = (
    "order_attack", "order_move", "order_guard", "order_patrol",
    "order_formation", "order_halt", "order_tactics", "order_harvest",
    "order_dock",
)
PRODUCTION_VERBS = ("order_build", "order_research")
ENVELOPE_VERBS = tuple(TASKING_VERBS) + tuple(PRODUCTION_VERBS)
TACTICS = ("aggressive", "neutral", "evasive")
SCOPES = ("scope:defense", "scope:fleet")

MAX_ITEMS_PER_LIST = 32
MAX_MICRO_ITEMS = 8          # per contracts/s1-envelope.md §2
MAX_DELEGATE_GRANTS = 8
MAX_SHIPS_PER_ITEM = 64
MAX_POSTURE_CHARS = 400
POS_LIMIT = 100000.0

ID_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789"


class ProgramError(ValueError):
    """Whole-packet rejection; `.reason` is the human-readable cause."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _bad(reason):
    raise ProgramError(reason)


def _int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) \
        and math.isfinite(v)


def _id_ok(s, prefix=None):
    return isinstance(s, str) and 1 <= len(s) <= 8 \
        and all(c in ID_CHARS for c in s) \
        and (prefix is None or s.startswith(prefix))


# --------------------------------------------------------------------------
# whole-packet validation (FR-008 + FR-017)
# --------------------------------------------------------------------------

def validate_packet(raw, last_accepted_version=-1):
    """Validate a Program packet WHOLE. raw: JSON str or dict.

    Returns Program. Raises ProgramError with the reason; NOTHING applies
    on error and the previous version stays authoritative (FR-008).
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as e:
            _bad("not valid JSON: %s" % e)
    if not isinstance(raw, dict):
        _bad("packet must be a JSON object")

    version = raw.get("version")
    if not _int(version):
        _bad("version must be an int")
    if last_accepted_version is not None and version <= last_accepted_version:
        _bad("version %d must exceed last accepted %d"
             % (version, last_accepted_version))

    issued_tick = raw.get("issued_tick", 0)
    if not _int(issued_tick) or issued_tick < 0:
        _bad("issued_tick must be an int ≥ 0")

    posture = raw.get("posture")
    if posture is not None:
        if not isinstance(posture, str) or len(posture) > MAX_POSTURE_CHARS:
            _bad("posture must be a string ≤ %d chars" % MAX_POSTURE_CHARS)

    delegate = _validate_delegate(raw.get("delegate"))
    production = _validate_production(raw.get("production"))
    tasking = _validate_tasking(raw.get("tasking"))

    _check_id_collisions(production, tasking)
    return Program(raw, delegate=delegate, production=production,
                   tasking=tasking)


def _check_id_collisions(production, tasking):
    seen = set()
    for item in production + tasking:
        iid = item["id"]
        if iid in seen:
            _bad("item id %r used twice" % (iid,))
        seen.add(iid)


def _validate_delegate(delegate):
    if delegate is None:
        return []
    if not isinstance(delegate, list) or len(delegate) > MAX_DELEGATE_GRANTS:
        _bad("delegate must be a list of ≤ %d grants" % MAX_DELEGATE_GRANTS)
    grants = []
    seen_ids = set()
    for g in delegate:
        if not isinstance(g, dict):
            _bad("delegate grant must be an object")
        gid = g.get("grant_id")
        if not _id_ok(gid, prefix="d") or len(gid) < 2:
            _bad("delegate grant_id %r must match dN" % (gid,))
        if gid in seen_ids:
            _bad("delegate grant_id %r used twice" % (gid,))
        seen_ids.add(gid)
        verbs = g.get("verbs")
        if not isinstance(verbs, list) or not verbs:
            _bad("delegate %s: verbs must be a non-empty list" % gid)
        for v in verbs:
            if v not in ENVELOPE_VERBS:
                _bad("delegate %s: verb %r is not delegable" % (gid, v))
        ships = g.get("ships")
        scope = None
        if isinstance(ships, str):
            if ships not in SCOPES:
                _bad("delegate %s: ships scope %r invalid" % (gid, ships))
            scope = ships
        else:
            _validate_ship_list(ships, "delegate %s: ships" % gid)
        zone = g.get("zone")
        if zone is not None:
            if (not isinstance(zone, list) or len(zone) != 4
                    or not all(_num(z) for z in zone)
                    or any(abs(z) > POS_LIMIT for z in zone)
                    or zone[3] <= 0):
                _bad("delegate %s: zone must be [cx,cy,cz,r], r > 0" % gid)
        override = g.get("override_tasking", False)
        if not isinstance(override, bool):
            _bad("delegate %s: override_tasking must be a bool" % gid)
        expires = g.get("expires_tick")
        if expires is not None and (not _int(expires) or expires < 0):
            _bad("delegate %s: expires_tick must be an int ≥ 0" % gid)
        grants.append({"grant_id": gid, "verbs": list(verbs),
                       "ships": scope if scope is not None
                       else list(ships),
                       "zone": zone, "override_tasking": override,
                       "expires_tick": expires})
    return grants


def _validate_ship_list(ships, ctx):
    if (not isinstance(ships, list) or not 1 <= len(ships) <= MAX_SHIPS_PER_ITEM
            or not all(_int(s) and s > 0 for s in ships)):
        _bad("%s must be 1-%d positive ints" % (ctx, MAX_SHIPS_PER_ITEM))


def _validate_production(production):
    if production is None:
        return []
    if (not isinstance(production, list)
            or len(production) > MAX_ITEMS_PER_LIST):
        _bad("production must be a list of ≤ %d items" % MAX_ITEMS_PER_LIST)
    out = []
    for it in production:
        it = dict(_validate_item_common(it))
        if it.get("verb") not in PRODUCTION_VERBS:
            _bad("%s: verb %r is not a production verb"
                 % (it["id"], it.get("verb")))
        _production_params(it)
        out.append(it)
    return out


def _production_params(it):
    """verb-shaped params for production items (order_build/order_research)."""
    params = it.get("params")
    if not isinstance(params, dict):
        _bad("%s: params must be an object" % it["id"])
    if it["verb"] == "order_build":
        ptype = params.get("type")
        if not isinstance(ptype, str) or not ptype:
            _bad("%s: order_build needs a type string" % it["id"])
        qty = params.get("quantity", 1)
        if not _int(qty) or not 1 <= qty <= 10:
            _bad("%s: quantity must be an int in [1, 10]" % it["id"])
    else:  # order_research
        tech = params.get("tech")
        if not isinstance(tech, str) or not tech:
            _bad("%s: order_research needs a tech string" % it["id"])


def _validate_tasking(tasking):
    if tasking is None:
        return []
    if not isinstance(tasking, list) or len(tasking) > MAX_ITEMS_PER_LIST:
        _bad("tasking must be a list of ≤ %d items" % MAX_ITEMS_PER_LIST)
    out = []
    for it in tasking:
        it = dict(_validate_item_common(it))
        verb = it["verb"]
        if verb not in TASKING_VERBS:
            _bad("%s: verb %r is not a tasking verb" % (it["id"], verb))
        _validate_ship_list(it.get("ships"), "%s: ships" % it["id"])
        it["ships"] = list(it["ships"])
        target = it.get("target")
        if target is not None and (not _int(target) or target <= 0):
            _bad("%s: target must be a positive int" % it["id"])
        pos = it.get("pos")
        if pos is not None:
            if (not isinstance(pos, list) or len(pos) != 3
                    or not all(_num(p) for p in pos)
                    or any(abs(p) > POS_LIMIT for p in pos)):
                _bad("%s: pos must be 3 finite numbers" % it["id"])
            it["pos"] = [float(p) for p in pos]
        tac = it.get("tactics")
        if tac is not None and tac not in TACTICS:
            _bad("%s: tactics must be one of %s" % (it["id"], ", ".join(TACTICS)))
        out.append(it)
    return out


def _validate_item_common(it):
    if not isinstance(it, dict):
        _bad("item must be an object")
    iid = it.get("id")
    if not _id_ok(iid):
        _bad("item id %r must be 1-8 chars [a-z0-9]" % (iid,))
    verb = it.get("verb")
    if not isinstance(verb, str):
        _bad("%s: verb required" % iid)
    priority = it.get("priority")
    if priority is not None and not _int(priority):
        _bad("%s: priority must be an int" % iid)
    once = it.get("once", False)
    if not isinstance(once, bool):
        _bad("%s: once must be a bool" % iid)
    return it


# --------------------------------------------------------------------------
# micro-packet validation (FR-018, contracts/s1-envelope.md §2)
# --------------------------------------------------------------------------

def validate_micro_packet(raw, program, last_seq=0, tick=0):
    """Validate an S1 micro-packet WHOLE against the CURRENT program's
    envelope. Returns (applied_items, blocked: list[(item_id, reason)]).
    A schema violation rejects the ENTIRE packet (ProgramError); envelope
    violations block the offending items only (recorded, never executed).

    `program` may be None (no active program): every tasking item is then
    out-of-envelope (advisory-only tier).
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as e:
            _bad("micro-packet not valid JSON: %s" % e)
    if not isinstance(raw, dict):
        _bad("micro-packet must be a JSON object")
    seq = raw.get("seq")
    if not _int(seq) or seq <= last_seq:
        _bad("micro seq must be an int > last applied seq")
    pver = raw.get("program_version")
    if program is None:
        if not _int(pver):
            _bad("micro program_version must be an int")
    elif _int(pver) and pver != program.version:
        _bad("micro program_version %r does not match active program %d"
             % (pver, program.version))
    tasking = raw.get("tasking")
    if not isinstance(tasking, list) or len(tasking) > MAX_MICRO_ITEMS:
        _bad("micro tasking must be a list of ≤ %d items" % MAX_MICRO_ITEMS)

    applied, blocked = [], []
    used_ids = set()
    for it in tasking:
        try:
            it = dict(_validate_item_common(it))
            verb = it["verb"]
            if verb not in TASKING_VERBS and verb not in PRODUCTION_VERBS:
                _bad("%s: verb %r is not delegable" % (it["id"], verb))
            if verb in PRODUCTION_VERBS:
                _production_params(it)   # schema only; grant check below
            if verb in TASKING_VERBS:
                ships = it.get("ships")
                if ships is not None:
                    # scope-class grants may resolve ships at the executor
                    # (license interpretation, FR-017); absent ships with a
                    # grant scope are legal here, illegal without one.
                    _validate_ship_list(ships, "%s: ships" % it["id"])
                    it["ships"] = list(ships)
                elif program is None:
                    _bad("%s: tasking without ships requires an active "
                         "program with a scope grant" % it["id"])
                target = it.get("target")
                if target is not None and (not _int(target) or target <= 0):
                    _bad("%s: target must be a positive int" % it["id"])
                pos = it.get("pos")
                if pos is not None:
                    if (not isinstance(pos, list) or len(pos) != 3
                            or not all(_num(p) for p in pos)
                            or any(abs(p) > POS_LIMIT for p in pos)):
                        _bad("%s: pos must be 3 finite numbers" % it["id"])
            if it["id"] in used_ids:
                _bad("micro item id %r used twice" % (it["id"],))
            used_ids.add(it["id"])
            reason = _envelope_violation(program, it, tick)
            if reason:
                blocked.append((it["id"], reason))
            else:
                applied.append(it)
        except ProgramError as e:
            raise ProgramError("micro-packet rejected: %s" % e.reason)
    return applied, blocked


def _envelope_violation(program, item, tick):
    """Legality of ONE validated micro item vs the CURRENT envelope
    (FR-017). Returns a reason string, or None when licensed."""
    if program is None:
        return "no active program: S1 is advisory-only"
    gid = item.get("grant")
    grant = program.grant_for(gid)
    if grant is None:
        return "item grant %r not in the current envelope" % (gid,)
    if grant["expires_tick"] is not None and tick > grant["expires_tick"]:
        return "grant %s expired at tick %d" % (gid, grant["expires_tick"])
    if item["verb"] not in grant["verbs"]:
        return "verb %r not licensed by grant %s" % (item["verb"], gid)
    scope = grant["ships"]
    ships = item.get("ships", [])
    if not isinstance(scope, str):
        outside = [s for s in ships if s not in scope]
        if outside:
            return "ships %s outside grant %s" % (outside, gid)
    return None


# --------------------------------------------------------------------------
# Program model
# --------------------------------------------------------------------------

class Program:
    """Validated Program packet (immutably wrapped)."""

    def __init__(self, packet, delegate, production, tasking):
        self._raw = packet
        self.version = packet["version"]
        self.issued_tick = packet.get("issued_tick", 0)
        self.posture = packet.get("posture")
        self._delegate = delegate
        self._production = production
        self._tasking = tasking

    def grant_for(self, grant_id):
        for g in self._delegate:
            if g["grant_id"] == grant_id:
                return dict(g)
        return None

    @property
    def delegate(self):
        return [dict(g) for g in self._delegate]

    @property
    def production(self):
        return [dict(i) for i in self._production]

    @property
    def tasking(self):
        return [dict(i) for i in self._tasking]

    @property
    def raw(self):
        return dict(self._raw)

    def live_grants(self, tick):
        """Grants still active at `tick` (envelope expiry, FR-017)."""
        out = []
        for g in self._delegate:
            exp = g["expires_tick"]
            if exp is None or tick <= exp:
                out.append(dict(g))
        return out

    def has_envelope(self):
        return bool(self._delegate)


# --------------------------------------------------------------------------
# budgeted input builders (FR-009/FR-013, FR-018)
# --------------------------------------------------------------------------

def _section(header, lines):
    if not lines:
        return []
    return [header] + list(lines)


def build_pulse_input(*, charter_lines, p0_items, ledger_lines,
                      rollup_lines, prev_program_lines, prev_status_lines,
                      micro_lines, reflex_lines, budget_units=None):
    """Assemble the S2 pulse input; static-first (FR-009).

    Budget enforcement (FR-013): prune order is (1) previous program
    history (prev program echo + statuses — the OLDEST program history),
    (2) oldest rollups, (3) oldest ledger lines. Charter and pinned P0
    are never pruned. Every prune is recorded as an alert.
    Returns (text, meta) with meta = {units, pruned, alerts}.
    """
    charter_lines = list(charter_lines or [])
    p0_items = list(p0_items or [])
    ledger_lines = list(ledger_lines or [])
    rollup_lines = list(rollup_lines or [])
    prev_program_lines = list(prev_program_lines or [])
    prev_status_lines = list(prev_status_lines or [])
    micro_lines = list(micro_lines or [])
    reflex_lines = list(reflex_lines or [])
    alerts = []

    def render(prune_stage=0):
        parts = []
        parts += _section("# CHARTER", charter_lines)
        if p0_items:
            parts += _section("# P0 (verbatim, pinned)",
                              [p["text"] if isinstance(p, dict) else str(p)
                               for p in p0_items])
        parts += _section("# LEDGER", ledger_lines[prune_stage["ledger"]:])
        parts += _section("# ROLLUPS", rollup_lines[prune_stage["rollups"]:])
        parts += _section("# LAST PROGRAM", prev_program_lines)
        parts += _section("# PROGRAM STATUS", prev_status_lines)
        parts += _section("# S1 MICRO", micro_lines)
        parts += _section("# REFLEXES", reflex_lines)
        return "\n".join(parts), parts

    stage = {"ledger": 0, "rollups": 0}
    text, parts = render(stage)
    pruned = []
    if budget_units is not None:
        while digest.units(text) > budget_units:
            if prev_program_lines or prev_status_lines:
                prev_program_lines, prev_status_lines = [], []
                pruned.append("history")
            elif stage["rollups"] < len(rollup_lines):
                stage["rollups"] = len(rollup_lines)   # oldest first: all
                pruned.append("rollups")
            elif stage["ledger"] < len(ledger_lines):
                stage["ledger"] += max(1, len(ledger_lines) // 4)
                stage["ledger"] = min(stage["ledger"], len(ledger_lines))
                pruned.append("ledger")
            else:
                break                                    # pinned floor hit
            for p in pruned[-1:]:
                alerts.append("budget prune: %s (%d units > %d)"
                              % (p, digest.units(text), budget_units))
            text, parts = render(stage)
        if digest.units(text) > budget_units:
            alerts.append("over budget even after prune floor: %d > %d"
                          % (digest.units(text), budget_units))
    meta = {"units": digest.units(text), "pruned": pruned, "alerts": alerts}
    return text, meta


def build_micro_input(*, charter_lines, p0_items, envelope_lines,
                      live_lines, prev_micro_lines, prev_status_lines,
                      budget_units=None):
    """Assemble the S1 micro-input (FR-018): micro-charter + pinned P0 +
    envelope echo + envelope-relevant live view + own previous packet."""
    charter_lines = list(charter_lines or [])
    envelope_lines = list(envelope_lines or [])
    lines = []
    lines += _section("# MICRO-CHARTER", charter_lines)
    if p0_items:
        lines += _section("# P0 (verbatim, pinned)",
                          [p["text"] if isinstance(p, dict) else str(p)
                           for p in p0_items])
    lines += _section("# ENVELOPE (verbatim grants)", envelope_lines)
    lines += _section("# LIVE VIEW", list(live_lines or []))
    lines += _section("# LAST MICRO", list(prev_micro_lines or []))
    lines += _section("# MICRO STATUS", list(prev_status_lines or []))
    text = "\n".join(lines)
    alerts = []
    meta = {"units": digest.units(text), "alerts": alerts}
    if budget_units is not None and meta["units"] > budget_units:
        # micro input carries no prunable program history; the envelope +
        # P0 are pinned, so an overflow is a loud alert (FR-013)
        alerts.append("micro input over budget: %d > %d"
                      % (meta["units"], budget_units))
    return text, meta