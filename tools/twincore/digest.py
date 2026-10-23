"""Spec 004 twin-core: importance classifier, rollups, digest builder (T009).

Events are classified P0 (break-interrupts) / P1 (report next window/digest)
/ P2 (rollup only) per FR-010, with playbook-derived configurable defaults
(research D9). Older raw deltas are replaced by interval rollups (FR-012).
The digest is the strategist-facing compacted record (protocol §5):
verbatim P0 items (outcome-tracked, never evicted by trimming, FR-011),
noteworthy changes, order outcomes with reasons, S1's mandatory one-line
explanations for any disobedience (veto reports), and ledger-cited
projections only (FR-013).
"""

import copy

DELTA_KEYS = ("created", "damaged", "healed", "vanished", "destroyed",
              "orders_applied", "orders_rejected", "lifecycle")

CLASSIFIER_THRESHOLDS = {
    "zone_radius": 25000.0,
    "zone_breach_min_strength": 30.0,
    "annihilation_min_own_losses": 4,
    "ru_bank_high": 1000.0,
}


def units(text):
    """Rough context-unit estimate (chars/4 ≈ tokens) for budget checks."""
    return max(0, len(text or "") // 4)


def _as_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return -1


def default_rules():
    """D9 default rule set (all retunable data; config may override)."""
    return {
        "thresholds": dict(CLASSIFIER_THRESHOLDS),
        # explicit p0 rule list lives in config digest.p0_rules; dispatch is
        # rule-name → function below (P0_RULES)
    }


# ---- rule implementations ---------------------------------------------------
# every rule is (rule-name) → (delta, ledger, thresholds) → detail | None

def _own_losses(delta, ledger):
    """Own ships destroyed: {id: killer} filtered to the seat's own stock."""
    lost = []
    for raw in (delta.get("destroyed") or {}):
        try:
            sid = int(raw)
        except (TypeError, ValueError):
            continue
        own = ledger.get("own", {}).get("ships", {})
        entry = own.get(sid)
        if entry or _was_own(sid, ledger):
            lost.append(sid)
    return lost


def _was_own(sid, ledger):
    # own ships live in ledger['own']['ships'] post-losses; deaths just
    # happened — consult destroyed bookkeeping via own_destroyed_recent
    return sid in (ledger.get("own_destroyed_recent") or [])


def _command_centre(ledger):
    return ledger.get("command_centre")


def rule_command_centre_lost(delta, ledger, th):
    mom = _command_centre(ledger)
    if mom is None:
        return None
    if _as_int(mom) in map(_as_int, (delta.get("destroyed") or {})):
        return {"id": mom}
    return None


def rule_command_centre_attacked(delta, ledger, th):
    mom = _command_centre(ledger)
    if mom is None:
        return None
    if _as_int(mom) in [ _as_int(x) for x in (delta.get("damaged") or []) ]:
        return {"id": mom}
    return None


def rule_battle_group_annihilated(delta, ledger, th):
    losses = _own_losses(delta, ledger)
    if len(losses) >= th["annihilation_min_own_losses"]:
        return {"own_losses": len(losses), "ids": losses}
    return None


def _in_zone(pos, ledger, th):
    centre = center_of_zone(ledger)
    if centre is None or not pos:
        return False
    return sum((a - b) ** 2 for a, b in zip(pos, centre)) ** 0.5 \
        <= th["zone_radius"]


def center_of_zone(ledger):
    """Defended-zone anchor: own command centre position, else strongest
    own blob; None when no position source (never fabricate)."""
    own = (ledger.get("own") or {}).get("ships") or {}
    mom = ledger.get("command_centre")
    pos = None
    if mom is not None:
        entry = own.get(_as_int(mom)) or own.get(mom)
        if entry and entry.get("pos"):
            pos = entry["pos"]
    elif own:
        first = next(iter(own.values()))
        pos = first.get("pos")
    if pos is None:
        own_blobs = [b for b in (ledger.get("blobs") or [])
                     if b.get("kind") == "own"]
        if own_blobs:
            return max(own_blobs, key=lambda b: b.get("strength", 0))["center"]
    return pos


def rule_defended_zone_breach(delta, ledger, th):
    for b in (ledger.get("blobs") or []):
        if b.get("kind") != "enemy":
            continue
        if b.get("strength", 0) < th["zone_breach_min_strength"]:
            continue
        if _in_zone(b.get("center") or [], ledger, th):
            return {"blob": b}
    return None


def rule_ru_flow_zeroed_while_bank_high(delta, ledger, th):
    income = ledger.get("income")
    bank = ledger.get("bank") or 0
    if income is not None and income <= 0 and bank >= th["ru_bank_high"]:
        return {"income": income, "bank": bank}
    return None


P0_RULES = {
    "command_centre_lost": rule_command_centre_lost,
    "command_centre_attacked": rule_command_centre_attacked,
    "battle_group_annihilated": rule_battle_group_annihilated,
    "defended_zone_breach": rule_defended_zone_breach,
    "ru_flow_zeroed_while_bank_high": rule_ru_flow_zeroed_while_bank_high,
}


def classify_deltas(deltas, ledger, rules=None):
    """Classify delta records into (class, rule, kind, detail) items.

    P0 = break-interrupts; P1 = own meaningful losses (below annihilation),
    order errors, alliance events, first-time enemy sightings; P2 = chatter
    (built/created own units, healed, ordinary damage). FR-010/FR-011.
    """
    rules = rules or default_rules()
    th = rules["thresholds"]
    out = []
    for delta in deltas:
        tick = delta.get("tick", 0)
        def emit(cls, rule, kind, detail):
            out.append({"tick": tick, "class": cls, "rule": rule,
                        "kind": kind, "detail": detail})

        # P0 first (order matters: interrupts win)
        p0_fired = False
        for name, fn in P0_RULES.items():
            detail = fn(delta, ledger, th)
            if detail is not None:
                emit("P0", name, name, detail)
                p0_fired = True

        # new enemy hull classes ever seen = report-worthy sighting
        sighted = []
        for raw in (delta.get("created") or []):
            try:
                sid = int(raw)
            except (TypeError, ValueError):
                continue
            k = ledger.get("enemy", {}).get("by_class", {})
            cls_name = None
            for cname, know in k.items():
                if sid in (know.get("ids_seen") or []):
                    cls_name = cname
            if cls_name:
                sighted.append(sid)
                emit("P1", "sighting", "sighting",
                     {"id": sid, "class": cls_name})

        # order outcomes below P0
        for rej in (delta.get("orders_rejected") or []):
            emit("P1", "order_error", "order_rejected", dict(rej))
        if delta.get("lifecycle") == "matchOver":
            emit("P0", "match_over", "lifecycle", {"event": "matchOver"})

        # P1: ordinary own losses (below annihilation threshold)
        losses = _own_losses(delta, ledger)
        if 0 < len(losses) < th["annihilation_min_own_losses"]:
            emit("P1", "own_loss", "destroyed", {"ids": losses})

        # P2: chatter — suppressed when a P0 fired on this delta (interrupt
        # wins; ordinary chatter still lands in rollups)
        if not p0_fired:
            for key in ("created", "damaged", "healed"):
                got = delta.get(key)
                if not got:
                    continue
                if key == "created" and sighted:
                    got = [s for s in got if s not in sighted
                           and int(_as_int(s)) not in sighted]
                if not got:
                    continue
                emit("P2", key, key, {key: len(got)})
    return out


class P0Tracker:
    """P0 items survive until superseded by outcome (FR-011(c))."""

    def __init__(self):
        self._items = []

    def push(self, item):
        self._items.append(dict(item, outcome="open"))

    def resolve_outcome(self, kind, outcome, tick=None):
        for item in self._items:
            if item["kind"] == kind and item.get("outcome") == "open":
                item["outcome"] = outcome
                if tick is not None:
                    item["outcome_tick"] = tick

    def take(self):
        """All P0 items verbatim (open first, resolved after)."""
        return [dict(i) for i in self._items]

    def compact(self):
        """Keep open items + the last resolved per kind (record history)."""
        open_ = [i for i in self._items if i.get("outcome") == "open"]
        last_resolved = {}
        for i in self._items:
            if i.get("outcome") != "open":
                last_resolved.setdefault(i["kind"], i)
        self._items = open_ + [last_resolved[k] for k
                               in sorted(last_resolved)]


def _classify_ledgerless(delta):
    """Ledger-less fallback for rollup histograms: chatter counts P2;
    only deltas with unknown ownership are estimated (no fabrication)."""
    tick = delta.get("tick", 0)
    out = []

    def emit(cls, rule, kind, detail):
        out.append({"tick": tick, "class": cls, "rule": rule,
                    "kind": kind, "detail": detail})

    for rej in (delta.get("orders_rejected") or []):
        emit("P1", "order_error", "order_rejected", dict(rej))
    if delta.get("lifecycle") == "matchOver":
        emit("P0", "match_over", "lifecycle", {"event": "matchOver"})
    for key in ("created", "damaged", "healed", "vanished"):
        got = delta.get(key)
        if got:
            emit("P2", key, key, {key: len(got)})
    destroyed = delta.get("destroyed") or {}
    if destroyed and not _own_losses(delta, {}):
        emit("P1", "enemy_loss", "destroyed", {"ids": list(destroyed)})
    return out


def rollup(deltas, interval_ticks=160):
    """Compress deltas into a ≤2-line interval rollup (FR-012), class
    histogram counting classification records (ledger-less estimate)."""
    if not deltas:
        return {"interval": None, "lines": [],
                "class_histogram": {"P0": 0, "P1": 0, "P2": 0}}
    lo = min(d["tick"] for d in deltas)
    hi = max(d["tick"] for d in deltas)
    kinds = {}
    for d in deltas:
        for k in d.get("kinds") or []:
            n = d.get(k)
            if isinstance(n, (list, dict, tuple)):
                n = len(n)
            elif isinstance(n, str):      # lifecycle markers etc.
                n = 1
            kinds[k] = kinds.get(k, 0) + (n or 0)
    hist = {"P0": 0, "P1": 0, "P2": 0}
    for d in deltas:
        for c in _classify_ledgerless(d):
            hist[c["class"]] = hist.get(c["class"], 0) + 1
    lines = []
    if kinds.get("damaged") or kinds.get("healed"):
        parts = []
        if kinds.get("damaged"):
            parts.append("%d damaged" % kinds["damaged"])
        if kinds.get("healed"):
            parts.append("%d healed" % kinds["healed"])
        lines.append(", ".join(parts))
    extra = []
    for k in ("created", "destroyed", "vanished", "orders_applied",
              "orders_rejected"):
        if kinds.get(k):
            extra.append("%d %s" % (kinds[k], k))
    if extra:
        lines.append(", ".join(extra))
    lines = [l for l in lines if l][:2]
    return {"interval": (lo, min(hi, lo + interval_ticks - 1)),
            "lines": lines, "class_histogram": hist,
            "span_ticks": hi - lo + 1}


def build_digest(pulse_id, tick_range, p0_items, rollups, changes,
                 order_status, s1_notes, ledger, budget=(300, 800)):
    """Compact the strategist-facing digest (protocol §5). Projections MUST
    cite the ledger (FR-013). Oversized → one reduced-scope regeneration;
    still oversized → gap logged (returns with 'budget_gap': True)."""
    dg = {
        "interval_pulse": pulse_id,
        "tick_range": list(tick_range),
        "p0": copy.deepcopy(p0_items),
        "changes": list(changes),
        "order_status": list(order_status),
        "s1_notes": list(s1_notes),
        "projections": [
            {"metric": "bank", "value": ledger.get("bank"),
             "source": "ledger"},
            {"metric": "income", "value": ledger.get("income"),
             "source": "ledger"},
            {"metric": "own_units", "value": _len(
                (ledger.get("own") or {}).get("ships") or {}),
             "source": "ledger"},
            {"metric": "threat_estimate",
             "value": _threat_estimate(ledger), "source": "ledger"},
        ],
    }
    size = units(_json_text(dg))
    if size < budget[0]:
        dg["changes"].insert(0, "(digest below min budget — interval quiet)")
        size = units(_json_text(dg))
    if size > budget[1]:
        slim = dict(dg)
        # greedy keep: longest changes prefix that fits (never pad garbage)
        changes = dg["changes"]
        lo_len = 0
        while lo_len < len(changes):
            candidate = slim
            candidate["changes"] = changes[:lo_len] + \
                ["(reduced: %d changes elided)" % (len(changes) - lo_len)]
            if units(_json_text(candidate)) <= budget[1]:
                break
            lo_len += max(1, (len(changes) - lo_len) // 2)
            slim = dict(dg)
        if units(_json_text(slim)) <= budget[1]:
            slim["budget_reduced"] = True
            return slim
        dg["budget_gap"] = True
        return dg
    return dg


def _threat_estimate(ledger):
    """Enemy blob strength in zone: ledger-cited number only."""
    th = CLASSIFIER_THRESHOLDS
    zone = center_of_zone(ledger)
    total = 0.0
    centre = zone or [0, 0, 0]
    for b in (ledger.get("blobs") or []):
        if b.get("kind") != "enemy":
            continue
        pos = b.get("center") or [0, 0, 0]
        dist = sum((a - c) ** 2 for a, c in zip(pos, centre)) ** 0.5
        if dist <= th["zone_radius"]:
            total += b.get("strength", 0.0)
    return round(total, 1)


def _len(v):
    try:
        return len(v)
    except TypeError:
        return 0


def _json_text(d):
    import json
    return json.dumps(d, sort_keys=True, default=str)