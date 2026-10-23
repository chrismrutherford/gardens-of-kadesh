"""Spec 004 twin-core: deterministic economy module (T012, FR-027).

Orchestration code mirroring the game's resource-manager doctrine (playbook
A1/A4/A6): collectors dispatched immediately, guards over resource ships,
collectors→controllers→support ratio, demand-driven research ship, the
fixed 30/30/40 build split as budget lanes, saturation stops. All economy
commands are ordinary proposals fed through the SAME validator and binding
as everything else — the economy module never issues orders itself.
"""

COSTS = {
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
    "ResearchShip": 700,
    "AssaultFrigate": 1800,
    "IonCannonFrigate": 1800,
    "MissileDestroyer": 2600,
    "StandardDestroyer": 2600,
}

LANE_OF = {
    "LightInterceptor": "attack", "HeavyInterceptor": "attack",
    "AttackBomber": "attack", "LightCorvette": "attack",
    "HeavyCorvette": "attack", "MultiGunCorvette": "attack",
    "SalvageCorvette": "defence", "RepairCorvette": "defence",
    "SupportFrigate": "defence",
    "ResourceCollector": "economy", "ResourceController": "economy",
    "SupportFrigate_": "defence", "ResearchShip": "economy",
    "AssaultFrigate": "attack", "IonCannonFrigate": "attack",
    "MissileDestroyer": "attack", "StandardDestroyer": "attack",
}
LANE_OF["SupportFrigate"] = "defence"

RESEARCH_CHAIN = ("MassDrive1Kt", "Chassis1", "MassDrive10Kt", "Chassis2",
                  "MassDrive100Kt", "Chassis3", "IonWeapons")

ATTACK_UNITS = ("LightInterceptor", "HeavyInterceptor", "AttackBomber",
                "LightCorvette", "HeavyCorvette", "MultiGunCorvette",
                "AssaultFrigate", "IonCannonFrigate")
DEFENCE_UNITS = ("RepairCorvette", "SalvageCorvette", "SupportFrigate")

ECONOMY_TARGETS = {
    "collectors_target": 6,
    "collectors_per_controller": 4,
    "strike_per_support": 6,
    "harvest_radius": 25000.0,
}

TICKS_PER_SEC = 16.0


def seconds_to_ticks(seconds):
    return max(1, int(round(seconds * TICKS_PER_SEC)))


def _dist(a, b):
    return sum((x - y) ** 2 for x, y in zip(a or (0, 0, 0),
                                            b or (0, 0, 0))) ** 0.5


class EconomyManager:
    """Deterministic lane allocator; emits validator-shaped proposals."""

    def __init__(self, collectors_target=None, split=None,
                 research_delay_ticks=None):
        self.collectors_target = collectors_target or \
            ECONOMY_TARGETS["collectors_target"]
        self.split = split or {"attack": 30, "defence": 30, "economy": 40}
        self.research_delay_ticks = research_delay_ticks or \
            seconds_to_ticks(45.0)   # intermediate-preset default
        self._spent = {"attack": 0.0, "defence": 0.0, "economy": 0.0}
        self._last_research = None

    # -- accounting (fed back by the tick loop on accepted commands) --------
    def accept(self, verb, cost=0.0, **_ignored):
        lane = _lane_of_verb(verb)
        if lane:
            self._spent[lane] += cost
        return lane

    def spent(self):
        return dict(self._spent)

    # -- proposal assembly ------------------------------------------------------
    def proposals(self, ledger):
        """Economy command proposals for this tick, in order. Deterministic:
        same ledger state → same proposals (no wall clock)."""
        out = []
        ship_items = [s for s in (ledger.get("ships_all") or {}).values()
                      if s.get("owner") == ledger.get("own", {}).get(
                          "player_index", 0)]
        ships = ledger.get("own", {}).get("ships") or {}
        if ships:
            ship_items = list(ships.values())
        mother = next((s for s in ship_items
                       if "Mothership" in (s.get("type") or "")), None)
        bank = ledger.get("bank") or 0

        out += self._opening_dispatch(ship_items, ledger)
        out += self._guards(ship_items)

        if mother:
            out += self._research(ledger)
            builds = self._build_plan(ship_items, ledger, bank, mother)
            out += builds
        return out

    # -- demand-driven research chain (playbook A1-3: only ask for research
    #    when what it unlocks is needed; deterministic chain + delay) -------
    def _research(self, ledger):
        tt = ledger.get("techTree") or {}
        if not tt.get("canResearch"):
            return []
        if ledger.get("research"):
            return []                      # a lab is busy
        researched = set(tt.get("researched") or [])
        nxt = next((t for t in RESEARCH_CHAIN if t not in researched), None)
        if nxt is None:
            return []                      # doctrine chain complete
        tick = ledger.get("tick", 0) or 0
        if self._last_research is not None and \
                tick - self._last_research < self.research_delay_ticks:
            return []
        self._last_research = tick
        return [{"verb": "order_research", "params": {"tech": nxt},
                 "lane": "economy"}]

    # -- A1 opening: dispatch every immediately available collector ----------
    def _opening_dispatch(self, ship_items, ledger):
        resources = list((ledger.get("resources") or {}).values()) or []
        idle = [s for s in ship_items
                if _is_collector(s) and _idle_for_harvest(s)]
        proposals = []
        for s in idle:
            target = self._nearest_resource(s, resources)
            if target is not None:
                proposals.append({
                    "verb": "order_harvest",
                    "params": {"ships": [s["id"]], "target": target},
                    "lane": "economy"})
        return proposals

    @staticmethod
    def _nearest_resource(ship, resources):
        best = None
        best_d = None
        for r in resources:
            d = _dist(ship.get("pos"), r.get("pos"))
            if best_d is None or d < best_d or \
                    (d == best_d and r.get("id", 0) < best.get("id", 0)):
                best, best_d = r, d
        return None if best is None else best.get("id")

    # -- A4: sphere guards over resource ships --------------------------------
    def _guards(self, ship_items):
        collectors = [s for s in ship_items if _is_collector(s)]
        guard_wings = [s for s in ship_items if _is_fighter(s)
                       and _idle_for_harvest(s)]
        if not collectors or not guard_wings:
            return []
        # 1 guard wing per up-to-4 collectors; assign in id order
        proposals = []
        for s in guard_wings[:2]:
            tgt = min(collectors, key=lambda c: c.get("id", 0))
            if tgt is not None:
                proposals.append({
                    "verb": "order_guard",
                    "params": {"ships": [s["id"]],
                               "target": tgt.get("id")},
                    "lane": "defence"})
        return proposals

    # -- build plan: lanes + ratios ---------------------------------------------
    def _build_plan(self, ship_items, ledger, bank, mother):
        proposals = []
        queued = {}
        for q in (ledger.get("buildQueue") or []):
            if isinstance(q, dict) and q.get("type"):
                # in-progress builds count toward their type's targets
                queued[q["type"]] = queued.get(q["type"], 0) + \
                    int(q.get("quantity") or 1)
        total_spent = sum(self._spent.values())
        controllers = [s for s in ship_items if "Controller" in
                       (s.get("type") or "")]
        supports = [s for s in ship_items if _is_support(s)]
        strike = [s for s in ship_items if _is_fighter(s)]
        research = [s for s in ship_items if "ResearchShip" in
                    (s.get("type") or "")]

        build_type = self._next_build_type(
            bank=bank,
            n_collectors=len(_all_collectors(ship_items)) +
            queued.get("ResourceCollector", 0),
            n_controllers=len(controllers) +
            queued.get("ResourceController", 0),
            n_supports=len(supports) + queued.get("SupportFrigate", 0),
            n_strike=len(strike) + queued.get("LightInterceptor", 0),
            n_research=len(research) + queued.get("ResearchShip", 0),
            total_spent=total_spent)
        if build_type is None:
            return []
        params = {"type": build_type}
        if mother and mother.get("id") is not None and \
                _needs_builder_param(build_type):
            params.setdefault("ships", [mother["id"]])
        proposals.append({"verb": "order_build", "params": params,
                          "lane": LANE_OF.get(build_type, "attack")})
        return proposals

    def _next_build_type(self, bank, n_collectors, n_controllers, n_supports,
                         n_strike, n_research, total_spent):
        # economy lane: collectors toward saturation, then controller ratio
        if n_collectors < self.collectors_target:
            return self._affordable("ResourceCollector", bank)
        if n_collectors >= 3 and \
                n_controllers < n_collectors // \
                ECONOMY_TARGETS["collectors_per_controller"]:
            return self._affordable("ResourceController", bank)
        # demand-driven research ship (first raiding group affordable soon)
        if n_research == 0 and bank >= COSTS["ResearchShip"] and \
                self._spent["attack"] > 0 or n_collectors >= 3 and \
                n_research == 0:
            if n_research == 0:
                return self._affordable("ResearchShip", bank)
        if n_strike >= 2 and \
                n_supports * ECONOMY_TARGETS["strike_per_support"] < n_strike:
            return self._affordable("SupportFrigate", bank)
        # rotation: most under-funded non-economy lane (tie → attack first)
        for lane in ("attack", "defence"):
            share = self.split[lane] / 100.0
            others = total_spent - self._spent[lane]
            under = self._spent[lane] < others * share / max(
                1e-9, (100 - self.split[lane]) / 100.0)
            if under:
                pool = ATTACK_UNITS if lane == "attack" else \
                    ("SupportFrigate",)
                for t in pool:
                    got = self._affordable(t, bank)
                    if got:
                        return got
        return None

    def _affordable(self, typ, bank):
        cost = COSTS.get(typ)
        if cost is None:
            return None
        return typ if bank >= cost else None


def _all_collectors(ship_items):
    return [s for s in ship_items if _is_collector(s)]


def _needs_builder_param(build_type):
    return "Ship" in build_type or "corvette" in build_type.lower()


def _lane_of_verb(verb):
    return {"order_build": "economy"}.get(verb, "economy")


def _is_collector(ship):
    return "ResourceCollector" in (ship.get("type") or "")


def _is_fighter(ship):
    t = (ship.get("type") or "")
    return any(k in t for k in ("Interceptor", "Bomber", "Corvette"))


def _is_support(ship):
    return "SupportFrigate" in (ship.get("type") or "")


def _idle_for_harvest(ship):
    order = ship.get("order")
    if not order:
        return True
    verb = order.get("verb")
    # playbook: collectors sitting in parade harvest NOTHING (ECONOMY
    # NAG rule) — parade collectors are idle economy, re-dispatch them
    return verb in ("idle", "moving", "parade", "order?")


def _harvesting(ship):
    order = ship.get("order")
    return bool(order) and order.get("verb") in ("harvest", "moving")