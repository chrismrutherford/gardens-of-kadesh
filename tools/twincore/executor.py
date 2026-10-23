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

import twincore.validator as V


BUILDERS = ("Mothership", "Carrier")


class Executor:
    """The ONLY issuer of v4 commands (its client is the binding owner)."""

    def __init__(self, client, ledger, recorder=None, validator=None,
                 own_player=0, max_per_tick=None, order_seq_start=1000):
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

    # -- program generation ---------------------------------------------
    def set_program(self, program, tick):
        """Swap in a WHOLE new program generation (FR-008; old in-flight
        commands keep running; suppression state resets)."""
        self.program = program
        self._item_state = {}
        self._suppressed = set()
        for it in program.production + program.tasking:
            self._item_state[it["id"]] = self._new_status(program, it, tick)

    def _new_status(self, program, item, tick):
        return {"program_version": program.version, "item_id": item["id"],
                "verb": item["verb"], "once": bool(item.get("once")),
                "issued_tick": None, "command_ids": [],
                "resolution": "pending", "reason": None, "aux": None,
                "attempts": 0}

    def item_status(self, item_id):
        st = self._item_state.get(item_id)
        return dict(st) if st else None

    def statuses(self):
        return [dict(v) for v in self._item_state.values()]

    # -- the tick ----------------------------------------------------------
    def on_tick(self, tick):
        """One deterministic executor tick. Returns minted commands, each
        {program_version, item_id, command_id, verb, params, resolution,
        aux}."""
        if self.program is None:
            return []
        proposals = self._proposals(tick)
        if not proposals:
            return []
        results = self.validator.validate_batch(
            [p for _, p in proposals], self.ledger)
        issued = []
        for (item_id, proposal), res in zip(proposals, results):
            st = self._item_state[item_id]
            if res["status"] == "clamped":
                # stays pending; cadence retry next tick
                if self.recorder:
                    self.recorder.record(
                        "validation", tick=tick, tool=proposal["verb"],
                        status="clamped", reason=res.get("reason"),
                        message=res.get("message"), hint=res.get("hint"),
                        proposed=proposal["params"],
                        program_version=st["program_version"],
                        item_id=item_id)
                continue
            if res["status"] == "rejected":
                st["resolution"] = "blocked"
                st["reason"] = res.get("reason")
                st["attempts"] += 1
                self._suppressed.add(item_id)   # max one attempt per cycle
                if self.recorder:
                    self.recorder.record(
                        "validation", tick=tick, tool=proposal["verb"],
                        status="rejected", reason=res.get("reason"),
                        message=res.get("message"), hint=res.get("hint"),
                        proposed=proposal["params"],
                        program_version=st["program_version"],
                        item_id=item_id)
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
            if self.recorder:
                self.recorder.record(
                    "validation", tick=tick, tool=proposal["verb"],
                    status=res.get("status"), reason=res.get("reason"),
                    proposed=proposal["params"],
                    program_version=st["program_version"], item_id=item_id)
                self.recorder.record(
                    "command", tick=tick, oid=cmd_id,
                    tool=proposal["verb"], params=params,
                    validation=res, status=resolution, aux=aux,
                    program_version=st["program_version"], item_id=item_id)
            minted = {"program_version": st["program_version"],
                      "item_id": item_id, "command_id": cmd_id,
                      "verb": proposal["verb"], "params": params,
                      "resolution": resolution, "aux": aux}
            issued.append(minted)
        return issued

    # -- mechanical translation (zero policy) ------------------------------
    def _proposals(self, tick):
        """Program items → validator proposals, fixed order: production
        (packet order), then tasking (packet order)."""
        out = []
        for item in self.program.production:
            if not self._eligible(item, tick):
                continue
            p = self._production_proposal(item)
            if p is None:
                continue   # blocked: no builder discovered
            out.append((item["id"], p))
        for item in self.program.tasking:
            if not self._eligible(item, tick):
                continue
            out.append((item["id"], self._tasking_proposal(item)))
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