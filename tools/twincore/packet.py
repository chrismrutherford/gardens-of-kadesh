"""Spec 004 twin-core: control-packet validation + apply order (T016).

The strategist's ONLY influence path (FR-015): five elements —
directive.patch, standing orders (add/revoke), history op, digest config,
questions — all schema-validated BEFORE any element applies (US3-5: a
bad packet rejects whole, old directive stays, no same-pulse retry).

Apply order at a tactical tick boundary (protocol §2):
patch (monotonic bump, FR-016) → orders → history op → digest config.
Sticky goal gate (FR-017) runs INSIDE apply: an ungated primary-enemy
change is dropped from the patch (rest applies, audit flagged). Standing
order subjects are validated against the ledger at apply time (issue time).
"""
import copy
import json

import twincore.directive as directive


class _KEEP_DEFAULT:
    pass


KEEP = _KEEP_DEFAULT()


def _revoke_id(o):
    """{"revoke": "<standing order id>"} — or {"revoke": true, "id": x}."""
    if not isinstance(o, dict):
        return None
    r = o.get("revoke")
    if isinstance(r, str) and r:
        return r
    if r and o.get("id"):
        return o["id"]
    return None


def validate_packet(obj):
    """Whole-packet schema check; returns (ok, normalized|errors)."""
    if not isinstance(obj, dict):
        return False, ["packet must be an object"]
    errs = []
    patch = (obj.get("directive") or {}).get("patch")
    if patch is not None and not isinstance(patch, dict):
        errs.append("directive.patch must be an object")
    orders = obj.get("orders")
    if orders is not None and not isinstance(orders, list):
        errs.append("orders must be an array")
    else:
        for o in orders or []:
            oid_of_revoke = (_revoke_id(o) if isinstance(o, dict) else None)
            if oid_of_revoke is not None:
                continue  # {"revoke": <id>} — checked at apply
            if not directive.validate_standing_order(o):
                pass
            else:
                errs.append("orders: %s"
                            % "; ".join(directive
                                        .validate_standing_order(o)[:2]))
    op = obj.get("history_op")
    if op is not None and (not isinstance(op, dict) or
                           not set(op) or
                           not set(op) <= {"trim", "reset"}):
        errs.append("history_op must be null | {trim: {scope…}} | "
                    "{reset: {seed…}}")
    dc = obj.get("digest_config")
    if dc is not None:
        if not isinstance(dc, dict) or \
                set(dc) - {"emphasis", "suppression"}:
            errs.append("digest_config must have emphasis/suppression lists")
        elif any(not isinstance(dc[k], list) for k in dc):
            errs.append("digest_config sets must be arrays")
    qs = obj.get("questions")
    if qs is not None and (not isinstance(qs, list) or
                           any(not isinstance(q, str) or not q
                               for q in qs)):
        errs.append("questions must be an array of non-empty strings")
    if errs:
        return False, errs
    return True, _normalize(obj)


def _normalize(obj):
    return {
        "directive": {"patch": ((obj.get("directive") or {}).get("patch")
                                or {})},
        "orders": list(obj.get("orders") or []),
        "history_op": obj.get("history_op"),
        "digest_config": obj.get("digest_config") or {},
        "questions": list(obj.get("questions") or []),
    }


def apply_packet(packet_obj, directive_obj, ledger, sticky, tick,
                 pulse_id=None, evidence_min=2):
    """Validate + apply at a tick boundary. Returns a result dict — never
    raises for packet content problems (callers log `packet_rejected`)."""
    result = {"ok": False, "applied": [], "dropped": [], "revoked": [],
              "audits": [], "questions": [], "history_op": None,
              "digest_config": {}, "directive": copy.deepcopy(directive_obj),
              "rejected_reason": None}
    ok, normalized_or_errs = validate_packet(packet_obj)
    if not ok:
        result["rejected_reason"] = "; ".join(normalized_or_errs)
        return result
    norm = normalized_or_errs

    start_version = directive_obj.get("version", 0)

    # -- 1. directive patch (sticky-gated) ------------------------------------
    patch = dict(norm["directive"]["patch"])
    if "primary_enemy" in patch:
        current = directive_obj.get("primary_enemy")
        new_enemy = patch["primary_enemy"]
        if new_enemy != current:
            allowed, why = sticky.can_change(new_enemy, tick)
            if not allowed:
                del patch["primary_enemy"]
                result["dropped"].append("primary_enemy")
                result["audits"].append(
                    "sticky: primary_enemy change to %r dropped (%s) — "
                    "pulse %r" % (new_enemy, why, pulse_id))

    if patch:
        try:
            new_d = directive.apply_patch(directive_obj, patch)
        except directive.PatchRejected as e:
            result["rejected_reason"] = "directive.patch: %s" % e
            return result
        result["directive"] = new_d
        result["applied"].append("patch")
        result["audits"].append(
            "directive v%d → v%d (%d keys)"
            % (start_version, new_d["version"], len(patch)))
        sticky.changed(new_d.get("primary_enemy"), tick) \
            if "primary_enemy" in patch else None

    # -- 2. standing orders ------------------------------------------------------
    living = list(result["directive"]["standing_orders"])
    added, revoked = [], []
    for o in norm["orders"]:
        if isinstance(o, dict) and _revoke_id(o) is not None:
            oid = _revoke_id(o)
            before = len(living)
            living = [x for x in living if x.get("id") != oid]
            if len(living) < before:
                revoked.append(oid)
            else:
                result["audits"].append("revoke %s: not found" % oid)
            continue
        # subject vs ledger (issue-time validation)
        bad = _subject_errors(o, ledger)
        if bad:
            result["dropped"].append("%s (%s)" % (o.get("id"), bad))
            result["audits"].append("order %s subject unverifiable: %s"
                                    % (o.get("id"), bad))
            continue
        entry = dict(o)
        entry["issued"] = {"tick": tick, "pulse": pulse_id}
        living.append(entry)
        added.append(entry["id"])
    n = len(living)
    if len(living) > MAX_STANDING_ORDERS:
        keep = sorted(living, key=lambda x: (x.get("priority", 0),
                                             x.get("id", ""))) \
            [:MAX_STANDING_ORDERS]
        result["dropped"].append("%d orders over the max %d"
                                 % (len(living) - len(keep),
                                    MAX_STANDING_ORDERS))
        living = keep
    result["directive"]["standing_orders"] = living
    result["revoked"] = revoked
    if added:
        result["applied"].append("orders")

    # -- 3. history op -----------------------------------------------------------
    op = norm["history_op"]
    if op:
        result["history_op"] = copy.deepcopy(op)
        result["applied"].append("history_op")

    # -- 4. digest config ------------------------------------------------------------
    if norm["digest_config"]:
        result["digest_config"] = norm["digest_config"]
        result["applied"].append("digest_config")

    # -- 5. questions -----------------------------------------------------------------
    if norm["questions"]:
        result["questions"] = list(norm["questions"])
        result["applied"].append("questions")

    result["ok"] = True
    return result


MAX_STANDING_ORDERS = 6

_SUBJECT_ID_FIELDS = ("ships", "target")


def _subject_errors(order, ledger):
    """Standing-order subject ids must resolve in the ledger (issue-time)."""
    subj = order.get("subject") or {}
    if not isinstance(subj, dict):
        return "subject must be an object"
    view = ledger.as_dict()
    known = {int(k) for k in view.get("ships_all", {})
             if str(k).lstrip("-").isdigit()}
    ships = subj.get("ships")
    if ships is not None:
        if not isinstance(ships, list):
            return "subject.ships must be an array"
        for s in ships:
            if not isinstance(s, int) or s not in known:
                return "subject.ships: unresolved id %r" % (s,)
    tgt = subj.get("target")
    if isinstance(tgt, int) and tgt not in known:
        return "subject.target: unresolved id %r" % (tgt,)
    return None


def packet_json(packet_obj):
    return json.dumps(packet_obj, sort_keys=True, default=str)