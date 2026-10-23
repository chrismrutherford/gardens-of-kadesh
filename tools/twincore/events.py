"""Spec 004 twin-core: event consumption → deltas vs last-seen (T006).

Two inputs (FR-009):
- consume_events(): raw engine events (aiplayEvent wire: matchStarted,
  matchOver, orderApplied, orderRejected, shipDestroyed, bindChanged,
  matchContinued; aux semantics per spec 003 data-model) → per-tick deltas.
- consume_snapshot(): diff vs the last-seen snapshot → created (new ids),
  damaged (health dropped), healed, vanished (id gone without a death
  event).

Raw high-frequency streams never go to a model from here — deltas only;
importance classification is digest.py's job (FR-010).
"""

EPS_DAMAGE = 0.5  # health below (last - EPS) counts as damage

ENGINE_EVENT_TYPES = (
    "matchStarted", "matchOver", "orderApplied", "orderRejected",
    "shipDestroyed", "bindChanged", "matchContinued",  # aiplay.h:109-118
)

LIFECYCLE = {
    "matchStarted", "matchOver", "matchContinued",
}

DELTA_KEYS = ("created", "damaged", "healed", "vanished", "destroyed",
              "orders_applied", "orders_rejected", "lifecycle")


def _new_delta(tick):
    d = {"tick": int(tick), "kinds": []}
    for k in DELTA_KEYS:
        d[k] = None
    return d


def _ensure_kind(d, kind):
    if kind not in d["kinds"]:
        # keep the fixed kind order readable ("kinds" ordered by DELTA_KEYS)
        d["kinds"] = sorted(d["kinds"] + [kind], key=DELTA_KEYS.index)


def combine(deltas):
    """Merge deltas (any order) into one per tick, kinds unioned."""
    by_tick = {}
    order = []
    for d in deltas:
        if not d:
            continue
        t = d["tick"]
        if t not in by_tick:
            by_tick[t] = d
            order.append(t)
            continue
        dst = by_tick[t]
        for k in DELTA_KEYS:
            src = d.get(k)
            if src is None:
                continue
            if dst.get(k) is None:
                dst[k] = src if k == "destroyed" else list(src)
            elif k == "destroyed":
                dst[k].update(src)
            else:
                dst[k] = list(dst[k]) + list(src)
            _ensure_kind(dst, k)
    return [by_tick[t] for t in sorted(order)]


class DeltaConsumer:
    """Stateful delta maker; one instance per bound seat. The engine's
    event log is a fixed 256-deep ring with no pagination, so each poll
    re-delivers overlapping history: dedupe by (tick,type,id,aux,owner)."""

    SEEN_CAP = 4096

    def __init__(self):
        self._last_ships = {}   # id -> (health, owner, type)
        self._last_tick = None
        self._seen = set()

    def _seen_reset(self):
        self._seen.clear()

    # -- engine event path ---------------------------------------------------
    def consume_events(self, events_response):
        tick = events_response.get("tick") or (
            events_response.get("events", [{}])[0].get("tick", 0)
            if events_response.get("events") else 0)
        by_tick = {}
        batch = events_response.get("events") or []
        for ev in batch:  # ring re-delivery handled by the (tick,type,id,…)
                          # seen-set below; order independent
            etype = ev.get("type")
            if etype not in ENGINE_EVENT_TYPES:
                raise ValueError("unknown engine event type %r" % (etype,))
            key = (ev.get("tick", 0), etype, ev.get("id"),
                   ev.get("aux", 0), ev.get("owner", -1))
            if key in self._seen:
                continue
            self._seen.add(key)
            if len(self._seen) > self.SEEN_CAP:
                self._seen_reset()
            t = ev.get("tick", tick)
            d = by_tick.setdefault(t, _new_delta(t))
            if etype == "shipDestroyed":
                d["destroyed"] = dict((d["destroyed"] or {}))
                d["destroyed"].setdefault(ev["id"], ev.get("aux", 0) or 0)
                _ensure_kind(d, "destroyed")
            elif etype == "orderApplied":
                d["orders_applied"] = list(d["orders_applied"] or [])
                d["orders_applied"].append(ev.get("id"))
                _ensure_kind(d, "orders_applied")
            elif etype == "orderRejected":
                d["orders_rejected"] = list(d["orders_rejected"] or [])
                d["orders_rejected"].append(
                    {"id": ev.get("id"), "aux": ev.get("aux", 0)})
                _ensure_kind(d, "orders_rejected")
            elif etype in LIFECYCLE:
                d["lifecycle"] = etype
                _ensure_kind(d, "lifecycle")
            # bindChanged: match continuity; tick regression ⇒ restart
        tick_now = [e.get("tick", 0) for e in batch]
        if tick_now and self._last_tick is not None \
                and max(tick_now) < self._last_tick:
            self.reset()  # match restart: identifiers renumber (edge case)
        return [by_tick[t] for t in sorted(by_tick)]

    def reset(self):
        """Full re-observe (ledger rebuild): forget baseline + ring memory."""
        self._last_ships = {}
        self._last_tick = None
        self._seen_reset()

    # -- snapshot diff path ----------------------------------------------------
    def consume_snapshot(self, snapshot):
        tick = snapshot.get("tick")
        if tick is None:
            return []
        ships = snapshot.get("ships") or []
        current = {}
        for s in ships:
            current[s["id"]] = (s.get("health", 0.0), s.get("owner"),
                                s.get("type"))

        if self._last_tick is None:
            # baseline observation: state store rebuild owns it (no deltas)
            self._last_ships = current
            self._last_tick = tick
            return []

        d = _new_delta(tick)
        for sid, (health, owner, typ) in current.items():
            old = self._last_ships.get(sid)
            if old is None:
                d["created"] = list(d["created"] or [])
                d["created"].append(sid)
                _ensure_kind(d, "created")
                continue
            if health < old[0] - EPS_DAMAGE:
                d["damaged"] = list(d["damaged"] or [])
                d["damaged"].append(sid)
                _ensure_kind(d, "damaged")
            elif health > old[0] + EPS_DAMAGE:
                d["healed"] = list(d["healed"] or [])
                d["healed"].append(sid)
                _ensure_kind(d, "healed")
        gone = [sid for sid in self._last_ships if sid not in current]
        if gone:
            d["vanished"] = gone
            _ensure_kind(d, "vanished")
        if self._last_tick is None or tick >= self._last_tick:
            self._last_ships = current
        self._last_tick = tick
        return [d] if d["kinds"] else []