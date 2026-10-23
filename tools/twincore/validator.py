"""Spec 004 twin-core: tri-state command validator (T010, FR-006/FR-007).

Checks in order (protocol §3):
1. interface — verb exists + params match the REGISTRY schema (single
   source, spec 003) — else rejected(E_BAD_REQUEST, hint)
2. ids — target/ship ids resolvable in the ledger's last observation —
   else rejected(E_BAD_ID, hint: re-observe); id-surge rebuild is the
   driver's job
3. alliance — attack-family vs ally/ally asset → E_ALLIED_PROTECTION
4. affordability — build vs bank via cost table (tunable data; unknown
   types are engine-side decisions, never guessed prices)
5. dedupe/clamp — duplicate in-flight collapse to clamped; per-tick cap
   ≤6 commands

Output tri-state (data-model.md): {ok} | {rejected, reason, hint} |
{clamped, original, corrected}. Nothing reaches the game unvalidated;
rejections/clamps loop back to S1 as first-class errors (FR-007).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

import aiplay_protocol as P  # noqa: E402

PER_TICK_CAP = 6

# tunable cost data (retunable per playbook tuning note; unknown types are
# NOT priced here — the engine remains authoritative, FR-006 wording)
DEFAULT_COSTS = {
    "LightInterceptor": 80,
    "HeavyInterceptor": 90,
    "AttackBomber": 180,
    "LightCorvette": 160,
    "HeavyCorvette": 400,
    "MultiGunCorvette": 250,
    "SalvageCorvette": 160,
    "RepairCorvette": 160,
    "ResourceCollector": 500,
    "ResourceController": 700,
    "SupportFrigate": 950,
    "AssaultFrigate": 1800,
    "IonCannonFrigate": 1800,
    "MissileDestroyer": 2600,
    "StandardDestroyer": 2600,
}

ATTACK_VERBS = {"order_attack", "order_kamikaze"}
AFFORD_VERBS = {"order_build", "order_research"}
RESOURCE_ONLY_TARGET = {"order_harvest", "order_dock"}


def _oid(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


class Validator:
    # registry-derived id-checked set (drift-proof: any tool carrying ships
    # or target params is treated as id-checked; SC-001 guarantees 1:1).
    # Computed once eagerly below the class body; tests read the attribute.
    _id_tools = None

    def __init__(self, costs=None, cap=PER_TICK_CAP):
        self.costs = dict(DEFAULT_COSTS if costs is None else costs)
        self.cap = cap
        self.tick_issued = 0

    @classmethod
    def _id_checked(cls):
        if cls._id_tools is None:
            cls._id_tools = {
                tool for tool, spec in P.REGISTRY["order_tools"].items()
                if "ships" in (spec.get("params") or {})
                or "target" in (spec.get("params") or {})}
        return cls._id_tools

    # -- single call ---------------------------------------------------------
    def validate(self, proposal, ledger, in_flight=None):
        """→ tri-state dict per data-model.md."""
        tool = proposal.get("verb")
        params = dict(proposal.get("params") or {})
        state_view = ledger.as_dict() if ledger is not None else {}

        # 1. interface: verb + schema (registry single-source)
        if not isinstance(tool, str) or tool not in P.REGISTRY["order_tools"]:
            return self._rejected("E_BAD_REQUEST",
                                  "unknown verb %r" % tool, propose_observe())
        err = P._validate(tool, params)
        if err:
            return self._rejected(err["code"], err["message"],
                                  propose_observe())

        # 2. ids resolvable in ledger's last observation
        ids = _id_params(tool, params)
        unknown = [i for i in ids if not self._resolvable(i, state_view)]
        if unknown:
            return self._rejected(
                "E_BAD_ID", "unresolved ids %s" % unknown,
                "re-observe (ids are match-scoped; rebuild ledger)")
        if tool in RESOURCE_ONLY_TARGET:
            tgt = params.get("target")
            if isinstance(tgt, int) and \
                    not (state_view.get("resources") or {}).get(tgt):
                return self._rejected(
                    "E_BAD_REQUEST", "target must be a resource",
                    "harvest/dock target a Resource id from the ledger")

        # 3. alliance protection
        if tool in ATTACK_VERBS:
            tgt = params.get("target")
            if isinstance(tgt, int):
                owner = _owner_of(state_view, tgt)
                if owner is not None and ledger is not None and \
                        ledger.is_allied(owner):
                    return self._rejected(
                        "E_ALLIED_PROTECTION",
                        "target %s belongs to an ally" % tgt,
                        "re-point at an enemy blob (directive.primary_enemy)")

        # 4. affordability (build only in v1; engine stays authoritative)
        if tool in AFFORD_VERBS:
            cost = self.costs.get(params.get("type"))
            if cost is not None and not tool.endswith("research"):
                qty = params.get("quantity", 1)
                if isinstance(qty, int) and isinstance(
                        state_view.get("bank"), (int, float)):
                    need = cost * qty
                    if state_view["bank"] < need:
                        return self._rejected(
                            "E_UNAFFORDABLE",
                            "need %d RUs, bank %d" % (need, state_view["bank"]),
                            "reduce quantity or wait for income")

        # 5. dedupe (vs in-flight): same order-id re-issue OR same
        # fingerprint (same verb + same ships + same target) collapses
        in_flight = in_flight if in_flight is not None else \
            (ledger.in_flight() if ledger is not None else {})
        fp = _fingerprint(tool, params)
        for oid, rec in in_flight.items():
            if rec.get("tool") == tool and (
                    rec.get("fingerprint") == fp or
                    (params.get("id") is not None and
                     _oid(params.get("id")) == _oid(oid))):
                return {"status": "clamped",
                        "original": dict(proposal),
                        "corrected": None,
                        "reason": "duplicate in-flight order id %s" % oid,
                        "hint": "await outcome (orderApplied/orderRejected)"}
        return {"status": "ok", "fingerprint": fp}

    def validate_batch(self, proposals, ledger):
        """Validate in order with the per-tick cap; extras are clamped.
        In-flight tracking is progressive: an accepted proposal becomes
        in-flight for the calls after it (intra-batch duplicate collapse)."""
        out = []
        budget = self.cap
        in_flight = dict(ledger.in_flight()) if ledger is not None else {}
        next_dup_id = -1
        for proposal in proposals:
            res = self.validate(proposal, ledger, in_flight)
            if res["status"] == "ok":
                if budget <= 0:
                    out.append({"status": "clamped",
                                "original": dict(proposal),
                                "corrected": None,
                                "reason": "per-tick cap %d exceeded"
                                          % self.cap,
                                "hint": "defer to next tick"})
                    continue
                budget -= 1
                in_flight[next_dup_id] = {
                    "tool": proposal["verb"],
                    "fingerprint": res.get("fingerprint")}
                next_dup_id -= 1
            out.append(res)
        return out

    @staticmethod
    def _rejected(reason, message, hint):
        return {"status": "rejected", "reason": reason,
                "message": message, "hint": hint}

    def _resolvable(self, sid, state_view):
        return _oid(sid) in {
            _id_int(k) for k in (state_view.get("ships_all") or {})
            if _id_int(k) is not None} or \
            _oid(sid) in {
                _id_int(k) for k in (state_view.get("resources") or {})
                if _id_int(k) is not None}


def _id_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def propose_observe():
    return "re-observe to refresh ship ids before re-issuing"


def _owner_of(state_view, sid):
    entry = (state_view.get("ships_all") or {}).get(_oid(sid))
    if isinstance(entry, dict):
        return entry.get("owner")
    return None


def _fingerprint(tool, params):
    return json_hash({"t": tool, "p": {k: params[k] for k in params
                                       if k != "id"}})


def json_hash(obj):
    import json
    import hashlib
    return hashlib.sha1(json.dumps(obj, sort_keys=True, default=str)
                        .encode()).hexdigest()[:12]


def _id_params(tool, params):
    """Ids this verb binds for resolution: ships[] + target when integer."""
    ids = []
    for s in params.get("ships") or []:
        i = _oid(s)
        if i is not None:
            ids.append(i)
    tgt = params.get("target")
    if tgt is not None and not isinstance(tgt, (list, tuple, dict)):
        i = _oid(tgt)
        if i is not None:
            ids.append(i)
    return ids


# eager registry-derived check-table (plain attribute; SC-001 drift-proof)
Validator._ID_CHECKED_TOOLS = Validator._id_checked()