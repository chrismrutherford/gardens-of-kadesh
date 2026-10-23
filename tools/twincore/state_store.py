"""Spec 004 twin-core: canonical ledger / state store (T008, FR-013).

The single reference both models consult: own fleet inventory + unit states,
RU bank + income projection, per-hull-class enemy knowledge (ever observed),
blob ratings (spatial groups with strength scores — the 1.4× overkill
input), alliance state, pending commands with statuses. Rebuilt via full
re-observe on bind / load / id-surge (edge-case contract); updated
incrementally from snapshots + event deltas. Models never keep private
battlefield memory — they cite numbers from here (FR-013/FR-014).
"""
import copy

BLOB_RADIUS = 25000.0        # sphere-of-influence scale (playbook)
OVERKILL_FACTOR = 1.4        # playbook aggression default (config overridable)

DELTA_KINDS = ("created", "damaged", "healed", "vanished", "destroyed",
               "orders_applied", "orders_rejected", "lifecycle")


def _oid_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _drop_ship(state, raw_sid, tick):
    try:
        sid = int(raw_sid)
    except (TypeError, ValueError):
        return
    ship = state["ships_all"].pop(sid, None)
    state["own"]["ships"].pop(sid, None)
    if ship:
        cls = ship.get("type", "?")
        know = state["enemy"]["by_class"].get(cls)
        if know and sid in know.get("ids_seen", []):
            know["ids_seen"] = [i for i in know["ids_seen"] if i != sid]
            know["alive"] = len(know["ids_seen"])
            know["last_seen_tick"] = tick if tick is not None \
                else know["last_seen_tick"]
    state["blobs"] = [b for b in state["blobs"] if sid not in b["ids"]]


def overkill_requirement(blob, factor=OVERKILL_FACTOR):
    """Strength needed to overkill a blob at the aggression factor."""
    return blob["strength"] * factor


class Ledger:
    def __init__(self, own_player=0, blob_radius=BLOB_RADIUS):
        self.own_player = own_player
        self.blob_radius = blob_radius
        self._s = self._empty()

    @staticmethod
    def _empty():
        return {
            "tick": 0,
            "own": {"ships": {}},
            "ships_all": {},          # id -> ship dict (both sides)
            "resources": {},
            "bank": 0,
            "income": None,
            "enemy": {"by_class": {}},
            "blobs": [],
            "alliances": {},          # player id -> bool
            "pending": {},            # order-id -> command record
            "directive_version": 0,
            "last_rejections": [],
        }

    # -- writes -------------------------------------------------------------
    def rebuild(self, snapshot):
        self._s = self._empty()
        self.apply_snapshot(snapshot, baseline=True)

    def apply_snapshot(self, snapshot, baseline=False):
        """Incremental merge (idempotent with rebuild for equal input)."""
        self._s["tick"] = snapshot.get("tick", self._s["tick"])
        ships = snapshot.get("ships") or []
        seen_own = {}
        for s in ships:
            owner = s.get("owner")
            prev = self._s["ships_all"].get(s["id"])
            entry = dict(prev or {})
            entry.update(copy.deepcopy(s))
            if "type" not in entry:
                entry["type"] = "?"
            self._s["ships_all"][s["id"]] = entry
            if owner == self.own_player:
                seen_own[s["id"]] = entry
            else:
                self._knowledge_classify(entry)
        self._s["own"]["ships"] = {
            sid: self._s["ships_all"][sid]
            for sid in seen_own if sid in self._s["ships_all"]}

        for r in snapshot.get("resources") or []:
            self._s["resources"][r["id"]] = copy.deepcopy(r)

        for p in snapshot.get("players") or []:
            if p.get("index") == self.own_player:
                self._s["bank"] = p.get("resourceUnits", self._s["bank"])
        inc = snapshot.get("income")
        self._s["income"] = None if inc is None or inc < 0 else float(inc)

        self._rebuild_blobs()

    def _knowledge_classify(self, ship):
        cls = ship.get("type", "?")
        know = self._s["enemy"]["by_class"].setdefault(
            cls, {"count_ever": 0, "alive": 0, "last_seen_tick": 0})
        prev_alive_ids = know.get("ids_seen", [])
        if ship["id"] not in prev_alive_ids:
            know["count_ever"] += 1
            know.setdefault("ids_seen", []).append(ship["id"])
        know["alive"] = len(know.get("ids_seen", []))
        know["last_seen_tick"] = self._s["tick"]

    # -- deltas ---------------------------------------------------------------
    def apply_delta(self, delta):
        destroyed = delta.get("destroyed") or {}
        for raw_sid in destroyed:
            _drop_ship(self._s, raw_sid, delta.get("tick"))

        # snapshot-diff "vanished": provably absent from the last
        # observation — drop (no guessing retained in ledger)
        for raw_sid in delta.get("vanished") or []:
            _drop_ship(self._s, raw_sid, delta.get("tick"))

        applied = delta.get("orders_applied") or []
        for oid in applied:
            self._s["pending"].pop(_oid_int(oid), None)
        for rej in delta.get("orders_rejected") or []:
            self._s["last_rejections"] = self._s["last_rejections"][-15:] + \
                [{"id": _oid_int(rej.get("id")), "aux": rej.get("aux")}]
            self._s["pending"].pop(_oid_int(rej.get("id")), None)

    # -- blobs ---------------------------------------------------------------
    def _rebuild_blobs(self):
        blobs = []
        pending = [(sid, s) for sid, s in self._s["ships_all"].items()]
        used = set()
        pending.sort(key=lambda kv: kv[0])
        for sid, s in pending:
            if sid in used:
                continue
            kind = "own" if s.get("owner") == self.own_player else "enemy"
            members = [(sid, s)]
            used.add(sid)
            pos = s.get("pos") or [0, 0, 0]
            for sid2, s2 in pending:
                if sid2 in used:
                    continue
                if (s2.get("owner") == self.own_player) != (kind == "own"):
                    continue
                pos2 = s2.get("pos") or [0, 0, 0]
                dist = sum((a - b) ** 2 for a, b in zip(pos, pos2)) ** 0.5
                if dist <= self.blob_radius:
                    members.append((sid2, s2))
                    used.add(sid2)
            blobs.append({
                "kind": kind,
                "ids": sorted(m[0] for m in members),
                "center": _center(members),
                "strength": round(float(sum(m[1].get("dps", 0.0)
                                            for m in members)), 1),
                "count": len(members),
            })
        self._s["blobs"] = blobs

    def blob_for(self, sid):
        for b in self._s["blobs"]:
            if sid in b["ids"]:
                yield b

    # -- pending / alliances -------------------------------------------------
    def register_command(self, command):
        self._s["pending"][command["id"]] = {
            "tool": command.get("tool"),
            "fingerprint": command.get("fingerprint"),
            "issued_tick": command.get("issued_tick", self._s["tick"]),
        }

    def in_flight(self):
        return dict(self._s["pending"])

    def last_rejections(self):
        return copy.deepcopy(self._s["last_rejections"])

    def set_alliance(self, player, allied):
        self._s["alliances"][player] = bool(allied)

    def is_allied(self, player):
        return bool(self._s["alliances"].get(player, False))

    # -- directive ref ------------------------------------------------------
    def note_directive_version(self, version):
        self._s["directive_version"] = version

    def as_dict(self):
        # ids_seen stays public: observed id→class data, consumed by digest
        return copy.deepcopy(self._s)

    # -- convenience ---------------------------------------------------------
    def command_centre(self):
        for sid, s in self._s["own"]["ships"].items():
            if "Mothership" in (s.get("type") or ""):
                return sid
        return None


def _center(members):
    xs, ys, zs = [], [], []
    for _, s in members:
        p = s.get("pos") or [0, 0, 0]
        xs.append(p[0]); ys.append(p[1])
        zs.append(p[2] if len(p) > 2 else 0)
    n = len(members)
    return [round(sum(v) / n, 1) for v in (xs, ys, zs)]