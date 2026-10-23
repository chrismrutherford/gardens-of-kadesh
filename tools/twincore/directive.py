"""Spec 004 twin-core: directive schema, boot default, sticky gate (T011).

The strategist-owned, versioned intent document for S1 (data-model):
primary enemy (≤1, sticky-gated), doctrine (defence tiers, harass cadence,
aggression/1.4×), build split (30/30/40 standing policy), standing orders,
attentions. Patches are semantic edits only (never wholesale rewrites,
FR-016), applied at tactical tick boundaries; full revision history goes to
the recorder. The sticky gate (FR-017) requires accumulated digest evidence
across ≥2 pulses plus a minimum hold time before a primary-enemy change
may apply.
"""
import os
import sys
import copy

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

import aiplay_protocol as P  # noqa: E402

BUILD_SPLIT_DEFAULT = {"attack": 30, "defence": 30, "economy": 40}


def boot_directive():
    """FR-023: playbook conservative opening (dock-and-defend) until an
    accepted strategist patch supersedes it."""
    return {
        "version": 1,
        "boot_default": True,
        "primary_enemy": None,
        "doctrine": {
            "defence_tiers": [
                {"tier": "mothership_first", "trigger": "command_centre"},
                {"tier": "medium_recall", "trigger": "zone_breach"},
                {"tier": "hardcore_recall", "trigger": "bg_annihilation"},
            ],
            "harass_cadence": {"interval_ticks": 160, "standoff": 25000},
            "aggression": {"overkill_factor": 1.4},
            "recall_thresholds": {"sphere_of_influence": 25000},
        },
        "build_split": dict(BUILD_SPLIT_DEFAULT),
        "standing_orders": [],
        "attentions": {"topics": {
            "economy": {"watch": True, "loudness": 1},
            "enemy_blobs": {"watch": True, "loudness": 2},
            "own_losses": {"watch": True, "loudness": 2},
            "alliance": {"watch": True, "loudness": 1},
        }},
    }


TOPIC_KEYS = ("attack", "defence", "economy")


def validate_directive(d):
    """Schema validation (protocol §4); returns list of error strings."""
    errs = []
    if not isinstance(d, dict):
        return ["directive must be an object"]
    v = d.get("version")
    if not isinstance(v, int) or isinstance(v, bool) or v < 1:
        errs.append("version must be int ≥ 1")
    pe = d.get("primary_enemy")
    if pe is not None and (not isinstance(pe, int) or isinstance(pe, bool)
                           or pe < 0):
        errs.append("primary_enemy must be a player id or null")
    split = d.get("build_split")
    if not isinstance(split, dict) or set(split) != set(BUILD_SPLIT_DEFAULT):
        errs.append("build_split must have exactly attack/defence/economy")
    elif sum(split.values()) != 100:
        errs.append("build_split must sum to 100")
    doctrine = d.get("doctrine")
    if not isinstance(doctrine, dict):
        errs.append("doctrine must be an object")
    else:
        ag = doctrine.get("aggression") or {}
        ov = ag.get("overkill_factor", 0)
        if not isinstance(ov, (int, float)) or isinstance(ov, bool) \
                or ov <= 1.0:
            errs.append("doctrine.aggression.overkill_factor must be > 1")
        hc = doctrine.get("harass_cadence") or {}
        if not isinstance(hc.get("interval_ticks", 0), int) or \
                hc.get("interval_ticks", 0) < 0:
            errs.append("doctrine.harass_cadence.interval_ticks must be ≥ 0")
    att = d.get("attentions")
    if not isinstance(att, dict) or not isinstance(att.get("topics"), dict):
        errs.append("attentions.topics must be an object")
    else:
        for topic, spec in att["topics"].items():
            loud = (spec or {}).get("loudness")
            if not isinstance(loud, int) or not 0 <= loud <= 2:
                errs.append("attentions.%s.loudness must be 0-2" % topic)
    orders = d.get("standing_orders")
    if not isinstance(orders, list):
        errs.append("standing_orders must be an array")
    else:
        seen = set()
        for o in orders:
            so = validate_standing_order(o)
            errs.extend("standing_orders: %s" % e for e in so)
            if o.get("id") in seen:
                errs.append("standing_orders: duplicate id %s" % o.get("id"))
            seen.add(o.get("id"))
    if d.get("boot_default") and d.get("version", 0) != 1:
        errs.append("boot_default must be version 1")
    return errs


def validate_standing_order(o):
    """Standing order shape per data-model.md."""
    errs = []
    if not isinstance(o, dict):
        return ["order must be an object"]
    if not isinstance(o.get("id"), str) or not o.get("id"):
        errs.append("id must be a non-empty string")
    verb = o.get("verb")
    if verb not in P.REGISTRY["order_tools"]:
        errs.append("verb %r not in registry" % verb)
    subj = o.get("subject")
    if not isinstance(subj, dict) or not subj:
        errs.append("subject must be a non-empty object")
    if not isinstance(o.get("priority", 0), int):
        errs.append("priority must be int")
    ttl = o.get("ttl_ticks")
    if not isinstance(ttl, int) or ttl < 0:
        errs.append("ttl_ticks must be int ≥ 0 (0 = until aborted)")
    if o.get("veto", True) is False and o.get("id") and o.get("priority", 0) \
            > 9:
        errs.append("non-vetoable orders must have priority ≤ 9")
    return errs


def make_standing_order(id, verb, subject, priority=0, ttl_ticks=3600,
                        constraints=None, abort=None, veto=True):
    return {
        "id": id, "verb": verb, "subject": subject,
        "constraints": constraints or [], "priority": priority,
        "ttl_ticks": ttl_ticks, "abort": abort or "none",
        "veto": veto,
    }


ALLOWED_PATCH_PATHS = (
    "primary_enemy",
    "build_split.attack", "build_split.defence", "build_split.economy",
    "doctrine.defence_tiers",
    "doctrine.harass_cadence.interval_ticks",
    "doctrine.harass_cadence.standoff",
    "doctrine.aggression.overkill_factor",
    "doctrine.recall_thresholds",
    "attentions.topics",
)

FORBIDDEN_PATCH_KEYS = ("version", "boot_default")

FIELD_TYPES = {
    "primary_enemy": (int, type(None)),
    "build_split": dict,
    "doctrine": dict,
    "attentions": dict,
    "standing_orders": list,
}


def apply_patch(directive, patch, expect_audit=False):
    """Semantic dotted-path edit list only; version bump on accept (or
    RuntimeError with the audit line when rejected — packet.py logs it)."""
    new = copy.deepcopy(directive)
    audit = []
    if not isinstance(patch, dict) or not patch:
        raise PatchRejected("patch must be a non-empty object")

    for path, value in patch.items():
        if path in FORBIDDEN_PATCH_KEYS:
            raise PatchRejected("patch may not touch %s" % path)
        if path not in ALLOWED_PATCH_PATHS and \
                not path.startswith(tuple(p + "." for p in
                                          ALLOWED_PATCH_PATHS)):
            raise PatchRejected("unknown patch path %r" % path)
        target = _walk_path(new, path, create=True)
        leaf = path.split(".")[-1]
        if leaf == "build_split":
            total = sum(value.values()) if isinstance(value, dict) else -1
            if total != 100:
                raise PatchRejected("build_split must sum to 100")
        if leaf == "aggression":
            ov = (value or {}).get("overkill_factor")
            if not isinstance(ov, (int, float)) or ov <= 1.0:
                raise PatchRejected("overkill_factor must be > 1")
        target[leaf] = value
        audit.append("patch %s = %r" % (path, value))

    new["version"] = new.get("version", 0) + 1
    new["boot_default"] = False  # patches end the boot doctrine
    errs = validate_directive(new)
    if errs:
        raise PatchRejected("patch produced invalid directive: %s"
                            % "; ".join(errs[:3]))
    if expect_audit:
        return new, audit
    return new


class PatchRejected(RuntimeError):
    pass


def _walk_path(d, dotted, create=False):
    parts = dotted.split(".")
    node = d
    for p in parts[:-1]:
        nxt = node.get(p)
        if not isinstance(nxt, dict):
            if not create:
                return None
            nxt = node[p] = {}
        node = nxt
    return node


class StickyGate:
    """FR-017: a primary-enemy change needs accumulated digest evidence
    (≥ evidence_min corroborations across ≥ evidence_min distinct pulses,
    with the most recent corroboration pointing at the proposed enemy) plus
    the CURRENT pin held ≥ hold_ticks. Anchors at boot / last change."""

    def __init__(self, evidence_min=2, hold_ticks=1800, since_tick=0):
        self.evidence_min = evidence_min
        self.hold_ticks = hold_ticks
        self.since_tick = since_tick
        self._pulses = set()
        self._latest = None
        self._per_target = {}

    def corroborate(self, enemy, pulse_id):
        self._per_target.setdefault(enemy, set()).add(pulse_id)
        self._pulses.add(pulse_id)
        self._latest = enemy

    def can_change(self, enemy, tick):
        n = len(self._pulses)
        if n < self.evidence_min:
            return False, ("evidence %d < %d across %d distinct pulses"
                           % (n, self.evidence_min, n))
        if self._latest != enemy and \
                len(self._per_target.get(enemy, ())) < self.evidence_min:
            return False, ("evidence points at %r, not %r"
                           % (self._latest, enemy))
        held = tick - self.since_tick
        if held < self.hold_ticks:
            return False, ("hold %d < %d ticks" % (held, self.hold_ticks))
        return True, "evidence held %d pulses, hold elapsed" % n

    def changed(self, enemy, tick):
        """Record the applied pin (spends the corroborations)."""
        self.since_tick = tick
        self._pulses.clear()
        self._latest = None
        self._per_target.clear()