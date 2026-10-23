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
from twincore.validator import _fingerprint


def _ex_io_append(path, text):
    """Real-time ex io stream (append + flush). Observability only."""
    try:
        with open(path, "a") as fh:
            fh.write(text)
            fh.flush()
    except Exception:
        pass


PRODUCTION_VERBS = ("order_build", "order_research")
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

    # engine orderRejected aux codes (spec-003) → FR-020 verdict legible
    AUX_WORDS = {
        2: "E_BAD_ID: a ship/resource/target id in the order is not in "
           "the LEDGER/ENEMY/ASTEROID sections (ids are match-scoped; "
           "re-observe)",
        3: "E_UNAFFORDABLE: not enough RU (bank is in the LEDGER)",
        5: "E_NO_LABS: research lab busy — one tech at a time "
           "(lab_busy in the RESEARCH MENU is the live state)",
        8: "E_BAD_REQUEST: the order itself is invalid — tech/hull names "
           "must come from the RESEARCH/BUILD MENU; ships/targets from "
           "the LEDGER/ASTEROID sections",
    }

    def __init__(self, client, ledger, recorder=None, validator=None,
                 own_player=0, max_per_tick=None, order_seq_start=1000,
                 reflexes=None, zone=None, zone_radius=15000,
                 verdict_budget=200, verdict_feed_k=6):
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
        # FR-020 verdict ring — every deny/coerce/clamp/engine-denied the
        # executor explains, de-duplicated per subject with a # repeat
        # counter (reset per program generation in set_program).
        self._verdicts = []
        self.verdict_budget = int(verdict_budget)
        self.verdict_feed_k = int(verdict_feed_k)
        # oid → item citation map (engine-event consumer, FR-023)
        self._oid_items = {}
        # semantic-name legality (from the live techTree, FR-009 menus)
        self._legal_caches = None   # (legal_techs, legal_hulls) by view id

    # -- teaching firewall (FR-020) ---------------------------------------
    def add_verdict(self, kind, subject, text, tick=None):
        """Record ONE actionable verdict (what was refused + what IS
        allowed). Bounded text; repeated identical denials gain a
        `# repeat <n>` marker so the models see the loop. Sinks: trail
        `validation` row + both channels' renderers pull verdicts()."""
        text = str(text)[:self.verdict_budget]
        for v in self._verdicts:
            if v["subject"] == subject and v["kind"] == kind \
                    and v["text"].split(" # repeat")[0] == text:
                v["n"] += 1
                v["text"] = "%s # repeat %d" % (text, v["n"])
                v["tick"] = tick
                if self.recorder:
                    self.recorder.record(
                        "validation", tick=tick, tool="verdict",
                        status="verdict", verdict_kind=kind, subject=subject,
                        verdict_text=v["text"], repeat=v["n"])
                return v
        v = {"kind": kind, "subject": subject, "text": text, "tick": tick,
             "n": 1}
        self._verdicts.append(v)
        self._verdicts = self._verdicts[-24:]
        if self.recorder:
            self.recorder.record("validation", tick=tick, tool="verdict",
                                 status="verdict", verdict_kind=kind,
                                 subject=subject, verdict_text=text,
                                 repeat=1)
        return v

    def verdicts(self, limit=None):
        """Latest verdicts for input rendering (last K, oldest first)."""
        k = self.verdict_feed_k if limit is None else int(limit)
        return [dict(v) for v in self._verdicts[-k:]]

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

    # -- semantic-name legality (FR-009 menus → FR-020 denies) -----------
    def _legal_names(self):
        """Legal tech/hull names from the LIVE techTree. Enforcement is
        armed only when the tree is real (a `locked` list exists or a
        researched set exists) — a tree-less view enforces nothing."""
        view = self.ledger.as_dict() if self.ledger is not None else {}
        tt = view.get("techTree") or {}
        researched = set(tt.get("researched") or [])
        locked = tt.get("locked")
        if not locked and not researched:
            return None                     # no tree data: skip enforcement
        needers = {}
        legal_hulls = set()
        for ent in locked or []:
            hull = ent.get("type")
            needs = set(ent.get("missing") or [])
            for tech in needs:
                needers.setdefault(tech, set()).add(hull)
            if not needs and not ent.get("disabled"):
                legal_hulls.add(hull)
        legal_techs = set(needers)
        for s in (view.get("ships_all") or {}).values():
            if s.get("type"):
                legal_hulls.add(s["type"])
        return legal_techs, legal_hulls, needers

    def _semantic_deny(self, item, tick):
        """Pre-wire semantic validation of production names (FR-020):
        unknown/locked/already-researched tech or hull → the ITEM is
        blocked with a verdict naming the legal alternatives; never the
        whole packet, never the engine, never doctrine."""
        legal = self._legal_names()
        if legal is None:
            return None
        legal_techs, legal_hulls, _needers = legal
        params = item.get("params") or {}
        view = self.ledger.as_dict() if self.ledger is not None else {}
        tt = view.get("techTree") or {}
        researched = set(tt.get("researched") or [])
        if item["verb"] == "order_research":
            tech = params.get("tech") or ""
            if tech and tech in researched:
                alts = sorted(legal_techs - {tech})[:6]
                return ("order_research %r already researched; "
                        "researchable now: %s" % (tech, alts))
            if tech and legal_techs and tech not in legal_techs:
                alts = sorted(legal_techs)[:6]
                return ("order_research tech %r unknown; researchable "
                        "now: %s%s" % (tech, alts,
                                       " ..." if len(legal_techs) > 6
                                       else ""))
        elif item["verb"] == "order_build":
            hull = params.get("type") or ""
            if hull and legal_hulls and hull not in legal_hulls:
                needs_str = "; ".join(
                    "%s needs %s" % (ent.get("type"), ent.get("missing"))
                    for ent in tt.get("locked") or []
                    if ent.get("type") == hull and ent.get("missing"))
                if needs_str:
                    return ("order_build %r LOCKED — %s; buildable now: "
                            "%s" % (hull, needs_str,
                                    sorted(legal_hulls)[:6]))
                return ("order_build type %r unknown; buildable now: %s"
                        % (hull, sorted(legal_hulls)[:6]))
        return None

    # -- program generation ---------------------------------------------
    def set_program(self, program, tick):
        """Swap in a WHOLE new program generation (FR-008; old in-flight
        commands keep running; suppression state resets)."""
        self._flush_clamp_summary(tick)
        # SUPERSEDE: the prior generation's production oids (kept in-flight
        # to stop standing re-mints, see on_engine_event) are retired — a
        # new program owns production now.
        for oid, item_id in list(self._oid_items.items()):
            st = self._item_state.get(item_id)
            if st is not None and st["program_version"] != program.version:
                self.ledger.mark_command_done(oid)
                self._oid_items.pop(oid, None)
        self.program = program
        self._item_state = {}
        self._suppressed = set()
        self._clamp_counts = {}
        for it in program.production + program.tasking:
            self._item_state[it["id"]] = self._new_status(program, it, tick)
        self._verdicts = []          # new generation: denial history resets

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
                "verdict": None,
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
                if n == 1:
                    vtxt = ("per-tick cap %d reached — commands across "
                            "program+S1+reflex share one budget; item "
                            "waits and re-issues next tick"
                            % (self.validator.cap,))
                    if st is not None:
                        st["verdict"] = vtxt
                    self.add_verdict("clamp", item_id, vtxt, tick)
                if n == 1 and self.recorder:
                    self.recorder.record(
                        "validation", tick=tick, tool=proposal["verb"],
                        status="clamped", reason=res.get("reason"),
                        message=res.get("message"), hint=res.get("hint"),
                        proposed=proposal["params"],
                        program_version=version, item_id=item_id)
                continue
            if res["status"] == "rejected":
                vtxt = "%s; allowed: ships/targets from the LEDGER / " \
                       "ENEMY CONTACTS / ASTEROID LEDGER, names from the " \
                       "BUILD/RESEARCH MENU" % res.get("reason")
                if st is not None:
                    st["resolution"] = "blocked"
                    st["reason"] = res.get("reason")
                    st["verdict"] = vtxt
                    st["attempts"] += 1
                    self._suppressed.add(item_id)  # 1 attempt per cycle
                self.add_verdict("deny", item_id, vtxt, tick)
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
                resolution = "engine_denied"   # issued ≠ accepted (FR-023)
                aux = err.get("code")
                vtxt = "engine denied %s (aux=%s): %s" % (
                    proposal["verb"], aux,
                    self.AUX_WORDS.get(aux if isinstance(aux, int)
                                       else None, err.get("message")
                                       or "see BUILD/RESEARCH MENUS"))
                self.add_verdict("engine_denied", item_id, vtxt, tick)
            if st is not None:
                st["resolution"] = resolution
                st["aux"] = aux
                st["reason"] = (err or {}).get("message") if err else None
                if err:
                    st["verdict"] = vtxt
                st["issued_tick"] = tick
                st["attempts"] += 1
                st["command_ids"].append(cmd_id)
                if resolution == "applied":
                    self.ledger.register_command(
                        {"id": cmd_id, "tool": proposal["verb"],
                         "fingerprint": res.get("fingerprint"),
                         "issued_tick": tick})
            elif resolution == "applied":
                self.ledger.register_command(
                    {"id": cmd_id, "tool": proposal["verb"],
                     "fingerprint": res.get("fingerprint"),
                     "issued_tick": tick})
            self._oid_items[cmd_id] = item_id   # event consumer (FR-023)
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

    # -- engine-event consumer (FR-023: async feedback) ----------------------
    def on_engine_event(self, ev, tick=None):
        """Fold one engine event into per-item status + verdicts.
        orderRejected: item → engine_denied (aux) + legible verdict;
        orderApplied: the engine ACKED the wire command — in-flight oid
        cleared (issued ⇒ accepted). Returns True when consumed."""
        etype = ev.get("type")
        oid = ev.get("id")
        if oid is None:
            return False
        item_id = self._oid_items.get(oid)
        if etype == "orderRejected":
            aux = ev.get("aux")
            if item_id is None:
                return False
            st = self._item_state.get(item_id)
            word = self.AUX_WORDS.get(aux if isinstance(aux, int) else None)
            vtxt = "engine denied (aux=%s): %s" % (
                aux, word or "check names in the BUILD/RESEARCH MENU "
                "and ids in the LEDGER/ASTEROID sections")
            if st is not None:
                st["resolution"] = "engine_denied"
                st["aux"] = aux
                st["verdict"] = vtxt
            self.add_verdict("engine_denied", item_id, vtxt, tick)
            self.ledger.mark_command_done(oid)   # stop dedupe-blocking
            return True
        if etype == "orderApplied":
            item_id = self._oid_items.get(oid)
            st = self._item_state.get(item_id) if item_id is not None else None
            if st is not None and st["verb"] in PRODUCTION_VERBS:
                # ACCEPTED ≠ retired: a standing PRODUCTION item keeps its
                # oid in-flight for the program generation so the dedupe
                # gate stops re-minting it every tick (live lesson: order_
                # build re-issued ~every 150 ticks, duplicating the engine
                # build queue). Superseding the program retires it.
                st["engine_acked"] = True
                return True
            self.ledger.mark_command_done(oid)   # accepted, no longer pending
            return True
        return False

    def on_ship_destroyed(self, ev):
        """shipDestroyed: aux = killer owner + 1 (spec-003). Enemy losses
        feed the rollup counter (FR-009 rollups)."""
        try:
            killer = int(ev.get("aux", 0)) - 1
            victim_owner = ev.get("owner")
        except (TypeError, ValueError):
            return False
        if killer == self.own_player and victim_owner is not None \
                and int(victim_owner) != self.own_player:
            self.kills_counter = getattr(self, "kills_counter", 0) + 1
            return True
        return False

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
        (b) full collector → dock nearest dock-capable own ship. Re-checked
        EVERY tick (continuous enforcement): a ship that drifted back to
        idle/parade must not stay blocked by a stale in-flight harvest oid —
        state truth wins and the stale oid is dropped. Runs ONLY for ships
        not covered by a live program item (gap-fillers)."""
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
                proposal = {"verb": "order_harvest", "params": {
                    "ships": [sid], "target": rid}}
                self._drop_stale_inflight(proposal, tick)
                out.append((sid, proposal, "reflex", None))
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

    def _drop_stale_inflight(self, proposal, tick):
        """Continuous-reflex helper (FR-006): when an idle/again-idle
        collector is blocked only by a STALE identical in-flight harvest
        oid (the ship drifted back to idle — the command's effect lapsed),
        drop that oid so the floor can re-dispatch every tick."""
        if self.ledger is None:
            return
        fp = _fingerprint(proposal["verb"], proposal["params"])
        inflight = self.ledger.in_flight()
        dropped = [oid for oid, rec in inflight.items()
                   if rec.get("fingerprint") == fp]
        for oid in dropped:
            self.ledger.mark_command_done(oid)
        if dropped and self.recorder:
            self.recorder.record("validation", tick=tick, tool="*",
                                 status="stale_inflight_dropped",
                                 oids=dropped,
                                 note="reflex re-issue: ship idle again")

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
        deny = self._semantic_deny(item, None)
        if deny is not None:
            st = self._item_state[item["id"]]
            st["resolution"] = "blocked"
            st["reason"] = deny
            st["verdict"] = deny
            st["attempts"] += 1
            self._suppressed.add(item["id"])
            self.add_verdict("deny", item["id"], deny)
            if self.recorder:
                self.recorder.record(
                    "program", tick=None, event="item_blocked",
                    program_version=st["program_version"],
                    item_id=item["id"], reason=deny)
            return None
        params = dict(item.get("params") or {})
        if item["verb"] == "order_build":
            bid = self._discover_builder()
            if bid is None:
                st = self._item_state[item["id"]]
                st["resolution"] = "blocked"
                st["reason"] = "E_NO_BUILDER"
                st["verdict"] = ("no build-capable ship (BUILDERS "
                                 "Mothership/Carrier — census in LEDGER)")
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