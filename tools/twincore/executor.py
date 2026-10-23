"""Spec 005 v4: the deterministic executor (FR-005/FR-006, contracts/
executor-contract.md).

Doctrine-free by construction: this module embodies NO building priorities,
tech chains, engagement rules or fallbacks (FR-004). Its entire
behaviour is a mechanical translation of the ACTIVE Program's items into
validated wire commands (≤ max_per_tick per tick, all channels), plus the
enumerated reflex floor added in a later increment. Every minted command
cites {program_version, item_id}; every attempt outcome is recorded as a
ProgramStatus (program rows / trail) and looped into S2's next pulse input.

Per attempt semantics (executor-contract §2):
- standing items re-propose each tick while the program is active;
- one-shot (`once`) items retire after their first applied attempt;
- validator REJECTION (E_UNAFFORDABLE, E_BAD_ID, allied, schema) marks the
  item `blocked` and suppresses re-proposal for the remainder of this
  program generation — S2 sees the failure in its next pulse input and may
  fix it with a new version (retries happen at most per pulse cycle);
- ENGINE wire errors (e.g. E_NO_LABS aux=5) mark the attempt `rejected`
  with the aux code and DO NOT suppress: the item re-issues next tick
  (dedupe is the validator's job);
- cap deferrals (`clamped`) stay `pending` and re-issue next tick — that
  is cadence, not retry.
"""
import itertools
import json
import os

import twincore.validator as V


def _ex_io_append(path, text):
    """Real-time ex io stream (append + flush). Observability only."""
    try:
        with open(path, "a") as fh:
            fh.write(text)
            fh.flush()
    except Exception:
        pass


BUILDERS = ("Mothership", "Carrier")
DOCKABLES = ("Mothership", "Carrier", "ResearchShip")
COLLECTOR = "ResourceCollector"
# carried observation semantics: verbs under which a collector is idle
# economy (parade ships harvest NOTHING)
IDLE_HARVEST_VERBS = ("idle", "moving", "parade", "order?")


def _order_verb(ship):
    o = ship.get("order")
    if isinstance(o, dict):
        return o.get("verb")
    if isinstance(o, str):
        return o
    return None


def _full_for_deliver(ship):
    amount = None
    for k in ("cargo", "resourceUnits", "cargoAmount"):
        if ship.get(k) is not None:
            amount = float(ship[k])
            break
    if amount is None:
        return False
    cap = None
    for k in ("cargo_max", "cargoMax", "resourceHold"):
        if ship.get(k) is not None:
            cap = float(ship[k])
            break
    return cap is not None and amount >= cap


def _dist2(a, b):
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def _nearest(sid, source_pos, things, key_pos):
    """Deterministic nearest: euclidean distance, tie-break lower id."""
    best = None
    for t in sorted(things, key=lambda t: _sid_t(t, key_pos, "id")):
        d = _dist2(source_pos, t.get(key_pos) or [0.0, 0.0, 0.0])
        key = (d, _sid_t(t, key_pos, "id"))
        if best is None or key < best[0]:
            best = (key, t.get("id"))
    return None if best is None else best[1]


def _sid_t(thing, key_pos, pk):
    try:
        return int(thing.get(pk))
    except (TypeError, ValueError):
        return 0


class Executor:
    """The ONLY issuer of v4 commands (its client is the binding owner)."""

    def __init__(self, client, ledger, recorder=None, validator=None,
                 own_player=0, max_per_tick=None, order_seq_start=1000,
                 reflexes=None, zone=None, zone_radius=15000):
        self.client = client
        self.ledger = ledger
        self.recorder = recorder
        self.validator = validator if validator is not None else V.Validator()
        if max_per_tick is not None:
            self.validator.cap = max_per_tick
        self.own_player = own_player
        self._order_seq = itertools.count(order_seq_start)
        self.program = None
        self._item_state = {}
        self._suppressed = set()
        self._clamp_counts = {}   # item_id → clamped ticks this generation
        # FR-006: the reflex set — toggles only, nothing else autonomous
        self.reflexes = {"harvest": True, "deliver": True}
        if reflexes:
            self.reflexes.update({k: bool(v) for k, v in reflexes.items()})
        self._micro_queue = []   # envelope-validated S1 items awaiting a tick
        self.zone = zone
        self.zone_radius = zone_radius

    # -- S1 micro lane (FR-018; contracts/s1-envelope.md §2–3) ---------------
    def feed_micro(self, applied, version, seq, tick):
        """Queue envelope-validated S1 items; minted at the next tick as
        commands citing item_id `s1:mN` + program version.'''
        """
        for it in applied:
            self._micro_queue.append(dict(it, seq=seq, version=version,
                                          queued_tick=tick))

    def _micro_entries(self, tick):
        """S1 items → proposals; program wins on shared ships UNLESS the
        grant carries override_tasking; grant scope classes resolve to a
        mechanical ship-set here (license interpretation, not doctrine)."""
        covered = self._covered_ships()
        out = []
        while self._micro_queue:
            it = self._micro_queue.pop(0)
            grant = self.program.grant_for(it.get("grant")) \
                if self.program is not None else None
            override = bool(grant and grant.get("override_tasking"))
            ships = it.get("ships") or []
            scope = grant.get("ships") if grant else None
            if isinstance(scope, str) or (not ships and grant is not None):
                resolved = self._scope_ships(scope, tick) \
                    if isinstance(scope, str) else list(scope)
                ships = ships or resolved
            if not ships:
                continue                      # nothing licensed to command
            it = dict(it, ships=[int(s) for s in ships])
            if (set(ships) & covered) and not override:
                continue    # program wins; S1 yield recorded via no-issue
            out.append(("s1:%s" % it["id"],
                        self._tasking_proposal(it), "micro",
                        self.program.version if self.program is not None
                        else None))
        return out

    def _scope_ships(self, scope, tick):
        """Expand a grant scope to concrete ship ids from fresh state.
        zone (if granted) bounds defense; fleet = all own non-collectors;
        defense = own non-collectors near the base."""
        view = self.ledger.as_dict() if self.ledger is not None else {}
        own = [s for s in (view.get("ships_all") or {}).values()
               if s.get("owner") == self.own_player]
        war = [s for s in own if s.get("type") != COLLECTOR]
        if scope == "scope:fleet":
            return [s["id"] for s in war]
        if scope == "scope:defense":
            zone = (self.zone or self._default_zone(own))
            cx, cy, cz, r = zone
            in_zone = [s for s in war if _dist2(
                s.get("pos") or [0, 0, 0], [cx, cy, cz]) <= r * r]
            return [s["id"] for s in in_zone]
        return []

    def _default_zone(self, own):
        base = next((s for s in own if "Mothership" in (s.get("type") or "")
                     or "Carrier" in (s.get("type") or "")), None)
        p = (base or {}).get("pos") or [0, 0, 0]
        return [p[0], p[1], p[2], self.zone_radius]

    # -- program generation ---------------------------------------------
    def set_program(self, program, tick):
        """Swap in a WHOLE new program generation (FR-008; old in-flight
        commands keep running; suppression state resets)."""
        self._flush_clamp_summary(tick)
        self.program = program
        self._item_state = {}
        self._suppressed = set()
        self._clamp_counts = {}
        for it in program.production + program.tasking:
            self._item_state[it["id"]] = self._new_status(program, it, tick)

    def _flush_clamp_summary(self, tick):
        """Trail hygiene: one aggregate row per generation instead of a
        clamp row per tick."""
        if self.recorder and self._clamp_counts:
            self.recorder.record("validation", tick=tick, tool="*",
                                 status="clamped_summary",
                                 clamped=dict(self._clamp_counts))

    def _new_status(self, program, item, tick):
        return {"program_version": program.version, "item_id": item["id"],
                "verb": item["verb"], "once": bool(item.get("once")),
                "issued_tick": None, "command_ids": [],
                "resolution": "pending", "reason": None, "aux": None,
                "clamped_count": 0, "attempts": 0}

    def item_status(self, item_id):
        st = self._item_state.get(item_id)
        return dict(st) if st else None

    def statuses(self):
        return [dict(v) for v in self._item_state.values()]

    # -- the tick ----------------------------------------------------------
    def on_tick(self, tick):
        """One deterministic executor tick. Returns minted commands, each
        {program_version, item_id, command_id, verb, params, resolution,
        aux}. Program items first (fixed order), then the reflex floor —
        one combined ≤max_per_tick budget across all channels (FR-005)."""
        import os
        io = os.environ.get("V4_EX_IO_LOG", "/tmp/ex-io.log")
        _exlog = lambda text: _ex_io_append(io, text)
        entries = []
        if self.program is not None:
            entries += self._program_entries(tick)
        entries += self._micro_entries(tick)
        entries += self._reflex_entries(tick)
        if not entries:
            return []
        results = self.validator.validate_batch(
            [p for _it, p, _k, _v in entries if p is not None], self.ledger)
        issued = []
        idx = 0
        for item_id, proposal, kind, version in entries:
            if proposal is None:
                continue              # blocked pre-validation (no builder)
            res = results[idx]
            idx += 1
            st = self._item_state.get(item_id) if kind == "program" else None
            version = st["program_version"] if st else version
            if res["status"] == "clamped":
                # cadence retry next tick; record the FIRST clamp per item
                # per generation, count silently after (trail hygiene)
                n = self._clamp_counts.get(item_id, 0) + 1
                self._clamp_counts[item_id] = n
                if st is not None:
                    st["clamped_count"] = n
                if n == 1 and self.recorder:
                    self.recorder.record(
                        "validation", tick=tick, tool=proposal["verb"],
                        status="clamped", reason=res.get("reason"),
                        message=res.get("message"), hint=res.get("hint"),
                        proposed=proposal["params"],
                        program_version=version, item_id=item_id)
                continue
            if res["status"] == "rejected":
                if st is not None:
                    st["resolution"] = "blocked"
                    st["reason"] = res.get("reason")
                    st["attempts"] += 1
                    self._suppressed.add(item_id)  # 1 attempt per cycle
                if self.recorder:
                    self.recorder.record(
                        "validation", tick=tick, tool=proposal["verb"],
                        status="rejected", reason=res.get("reason"),
                        message=res.get("message"), hint=res.get("hint"),
                        proposed=proposal["params"],
                        program_version=version, item_id=item_id)
                continue
            # ok → mint the wire command
            params = dict(proposal["params"])
            cmd_id = next(self._order_seq)
            params["id"] = cmd_id
            wire = self.client.order(proposal["verb"], params)
            err = wire.get("error") if isinstance(wire, dict) else None
            aux = None
            resolution = "applied"
            if err:
                resolution = "rejected"
                aux = err.get("code")
            if st is not None:
                st["resolution"] = resolution
                st["aux"] = aux
                st["reason"] = (err or {}).get("message") if err else None
                st["issued_tick"] = tick
                st["attempts"] += 1
                st["command_ids"].append(cmd_id)
                if resolution == "applied":
                    self.ledger.register_command(
                        {"id": cmd_id, "tool": proposal["verb"],
                         "fingerprint": res.get("fingerprint"),
                         "issued_tick": tick})
            else:
                self.ledger.register_command(
                    {"id": cmd_id, "tool": proposal["verb"],
                     "fingerprint": res.get("fingerprint"),
                     "issued_tick": tick})
            if self.recorder:
                self.recorder.record(
                    "validation", tick=tick, tool=proposal["verb"],
                    status=res.get("status"), reason=res.get("reason"),
                    proposed=proposal["params"],
                    program_version=version, item_id=item_id)
                self.recorder.record(
                    "command", tick=tick, oid=cmd_id,
                    tool=proposal["verb"], params=params,
                    validation=res, status=resolution, aux=aux,
                    program_version=version, item_id=item_id)
            minted = {"program_version": version,
                      "item_id": item_id, "command_id": cmd_id,
                      "verb": proposal["verb"], "params": params,
                      "resolution": resolution, "aux": aux}
            issued.append(minted)
            _exlog("\nCMD tick=%s ch=%s item=%s v=%s cmd=%s %s %s -> %s%s"
                   % (tick, kind, item_id, version, cmd_id,
                      proposal["verb"], json.dumps(params, default=str),
                      resolution, (" aux=%s" % aux) if aux else ""))
        firsts = [it for it, v in self._clamp_counts.items() if v == 1]
        if not issued and firsts:
            _exlog("CLAMP tick=%s first:%s rest silent (counts=%s)"
                   % (tick, firsts, {k: v for k, v in
                                     self._clamp_counts.items()})
                   if tick % 25 == 0 else
                   "CLAMP tick=%s first-per-item=%s" % (tick, firsts))
        return issued

    # -- mechanical translation (zero policy) ------------------------------
    def _program_entries(self, tick):
        """Program items → validator proposals, fixed order: production
        (packet order), then tasking (packet order). [(item_id, proposal,
        "program")] (proposal None = blocked pre-validation)."""
        out = []
        for item in self.program.production:
            if not self._eligible(item, tick):
                continue
            p = self._production_proposal(item)
            if p is not None:        # None: blocked (no builder)
                out.append((item["id"], p, "program", self.program.version))
        for item in self.program.tasking:
            if not self._eligible(item, tick):
                continue
            out.append((item["id"], self._tasking_proposal(item), "program",
                        self.program.version))
        return out

    # -- reflex floor (FR-006; contracts §3) ---------------------------------
    def _reflex_entries(self, tick):
        """EXACTLY: (a) idle collector → harvest nearest asteroid;
        (b) full collector → dock nearest dock-capable own ship. Runs ONLY
        for ships not covered by a live program item (gap-fillers)."""
        if not (self.reflexes.get("harvest") or self.reflexes.get("deliver")):
            return []
        view = self.ledger.as_dict() if self.ledger is not None else {}
        ships_all = view.get("ships_all") or {}
        own = [s for s in ships_all.values()
               if s.get("owner") == self.own_player]
        collectors = [s for s in own if s.get("type") == COLLECTOR]
        if not collectors:
            return []
        covered = self._covered_ships()
        covered |= self.micro_covered_ships()
        dockables = [s for s in own if s.get("type") in DOCKABLES]
        resources = list((view.get("resources") or {}).values())
        out = []
        for s in sorted(collectors, key=lambda x: _sid_t(x, None, "id")):
            sid = s["id"]
            if sid in covered:
                continue                        # program wins, gap-fillers
            if self.reflexes.get("harvest") and self._idle_for_harvest(s):
                if not resources:
                    continue
                rid = _nearest(sid, s.get("pos") or [0, 0, 0],
                               resources, "pos")
                if rid is None:
                    continue
                out.append((sid, {"verb": "order_harvest", "params": {
                    "ships": [sid], "target": rid}}, "reflex", None))
            elif self.reflexes.get("deliver") and _full_for_deliver(s):
                if not dockables:
                    continue
                did = _nearest(sid, s.get("pos") or [0, 0, 0],
                               dockables, "pos")
                if did is None:
                    continue
                out.append((sid, {"verb": "order_dock", "params": {
                    "ships": [sid], "dockWith": did}}, "reflex", None))
        # map ship-id item keys to reflex names for citations
        named = []
        for item_id, proposal, kind, _v in out:
            tag = "reflex_harvest" \
                if proposal["verb"] == "order_harvest" else "reflex_deliver"
            named.append((tag, proposal, "reflex", None))
        return named

    def _idle_for_harvest(self, ship):
        verb = _order_verb(ship)
        return not verb or verb in IDLE_HARVEST_VERBS

    def _covered_ships(self):
        if self.program is None:
            return set()
        covered = set()
        for item in self.program.tasking:
            covered.update(item.get("ships") or [])
        return covered

    def micro_covered_ships(self):
        """Ships covered by QUEUED micro items (reflex gate input)."""
        out = set()
        for it in self._micro_queue:
            out.update(it.get("ships") or [])
        return out

    def _eligible(self, item, tick):
        st = self._item_state.get(item["id"])
        if st is None:                       # defensive; set_program seeds
            self._item_state[item["id"]] = self._new_status(
                self.program, item, tick)
            st = self._item_state[item["id"]]
        if item["id"] in self._suppressed:
            return False
        if item.get("once") and st["resolution"] == "applied":
            st["resolution"] = "retired"
            return False
        return True

    def _production_proposal(self, item):
        params = dict(item.get("params") or {})
        if item["verb"] == "order_build":
            bid = self._discover_builder()
            if bid is None:
                st = self._item_state[item["id"]]
                st["resolution"] = "blocked"
                st["reason"] = "E_NO_BUILDER"
                st["attempts"] += 1
                self._suppressed.add(item["id"])
                if self.recorder:
                    self.recorder.record(
                        "program", tick=None, event="item_blocked",
                        program_version=st["program_version"],
                        item_id=item["id"], reason="E_NO_BUILDER")
                return None
            params["ships"] = [bid]
        return {"verb": item["verb"], "params": params}

    def _tasking_proposal(self, item):
        params = {"ships": list(item["ships"])}
        verb = item["verb"]
        if verb == "order_dock":
            if item.get("target") is not None:
                params["dockWith"] = item["target"]
        elif verb == "order_tactics":
            if item.get("tactics") is not None:
                params["tactics"] = item["tactics"]
        elif verb == "order_move":
            # registry shape: the move destination IS the `target` coord
            if item.get("pos") is not None:
                params["target"] = list(item["pos"])
        else:
            if item.get("target") is not None:
                params["target"] = item["target"]
            if item.get("pos") is not None:
                params["pos"] = list(item["pos"])
        return {"verb": verb, "params": params}

    def _discover_builder(self):
        """Discovery rule (not doctrine): own Mothership preferred, then
        Carrier, lowest id; deterministic within a state snapshot."""
        view = self.ledger.as_dict()
        best = None
        for sid, s in sorted(view.get("ships_all", {}).items()):
            if s.get("owner") != self.own_player \
                    or s.get("type") not in BUILDERS:
                continue
            rank = BUILDERS.index(s["type"]), _sid(sid)
            if best is None or rank < best[0]:
                best = (rank, sid)
        return best[1] if best else None


def _sid(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0