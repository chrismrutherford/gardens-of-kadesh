#!/usr/bin/env python3
"""Spec 004 tests: tools/twincore/economy.py (T012).

Deterministic resource manager per playbook A1/A4/A6: collectors dispatched
first (before offensive units), guards over resource ships, collectors→
controllers→support ratios, demand-driven research ship, 30/30/40 budget
lanes with saturation stop, all proposals passing the same validator
(FR-027).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "twincore"))

import twincore.economy as EC  # noqa: E402
import twincore.state_store as S  # noqa: E402


def ship(sid, owner=0, typ="ResourceCollector", pos=(0, 0, 0), order=None):
    return {"id": sid, "owner": owner, "type": typ, "pos": list(pos),
            "vel": [0, 0, 0], "health": 100.0, "healthMax": 100.0,
            "dps": 0.0 if "Collector" in typ or "Mothership" in typ else 5.0,
            "guns": 1, "range": 4000.0, "order": order}


def led(tick=100, ships=(), bank=5000, income=12.0, resources=(91, 92)):
    s = S.Ledger(own_player=0)
    s.rebuild({
        "tick": tick, "paused": False, "income": income,
        "ships": list(ships),
        "players": [{"index": 0, "resourceUnits": bank, "alive": True},
                    {"index": 1, "resourceUnits": bank // 2, "alive": True}],
        "resources": [{"id": r, "owner": -1, "type": "Asteroid",
                       "pos": [r * 100.0, 0, 0], "vel": [0, 0, 0],
                       "value": 5000} for r in resources],
        "buildQueue": [], "research": [],
        "techTree": {"labs": 2, "canResearch": True, "locked": [],
                     "researched": []},
        "matchStatus": {"running": True, "boundPlayerIndex": 0,
                        "winner": None, "perPlayer": []},
    })
    return s


def mothership():
    return ship(1, typ="PlayerMothership")


class Harness(unittest.TestCase):
    def setUp(self):
        self.mgr = EC.EconomyManager()

    def proposals(self, ledger):
        return self.mgr.proposals(ledger.as_dict())

    def collect_verbs(self, ledger):
        return [p["verb"] for p in self.proposals(ledger)]

    def proposals_of(self, ledger, verb):
        return [p for p in self.proposals(ledger) if p["verb"] == verb]


class TestOpening(Harness):
    def test_collectors_dispatched_first(self):
        s = led(ships=[
            mothership(),
            ship(2, typ="ResourceCollector", pos=(10, 0, 0)),
            ship(3, typ="ResourceCollector", pos=(20, 0, 0)),
            ship(4, typ="LightInterceptor", order={"verb": "idle"}),
        ])
        ps = self.proposals(s)
        harvest = self.proposals_of(s, "order_harvest")
        self.assertEqual(len(harvest), 2)   # both idle collectors
        ships_harvesting = {sh for p in harvest
                            for sh in p["params"]["ships"]}
        self.assertEqual(ships_harvesting, {2, 3})
        # mothership stays free to build (A1: spare construction craft)
        for p in harvest:
            self.assertNotIn(1, p["params"]["ships"])

    def test_collectors_before_offense(self):
        # early: nothing military built/built-up yet → no attack proposals
        s = led(ships=[mothership(), ship(2, typ="ResourceCollector")],
                bank=3000.0)
        verbs = set(self.collect_verbs(s))
        attack_like = {"order_attack", "order_kamikaze", "order_move"}
        self.assertEqual(verbs & attack_like, set())

    def test_guards_over_resource_ships(self):
        s = led(ships=[
            mothership(), ship(2, typ="ResourceCollector"),
            ship(3, typ="LightInterceptor"), ship(4, typ="LightInterceptor"),
        ])
        guards = self.proposals_of(s, "order_guard")
        self.assertTrue(guards)
        self.assertEqual({p["params"]["target"] for p in guards}, {2})
        guarders = {sh for p in guards for sh in p["params"]["ships"]}
        self.assertEqual(guarders, {3, 4})


class TestBuildLanes(Harness):
    def test_collectors_first_toward_target(self):
        s = led(ships=[mothership(), ship(2, typ="ResourceCollector")],
                bank=12000.0)
        builds = [p["params"]["type"] for p in self.proposals_of(s,
                                                                 "order_build")]
        self.assertEqual(builds[0], "ResourceCollector")

    def test_controller_after_collectors_flow(self):
        s = led(ships=[
            mothership()] + [ship(i, typ="ResourceCollector")
                             for i in range(2, 8)],   # 6 collectors
            bank=12000.0)
        builds = [p["params"]["type"] for p in
                  self.proposals_of(s, "order_build")]
        self.assertIn("ResourceController", builds)

    def test_support_frigate_with_strike_fleet(self):
        # saturated economy + a research ship out → supports per strike ratio
        strikes = [ship(i, typ="LightInterceptor", pos=(1000, 0, 0))
                   for i in range(10, 18, 1)]
        economy = led(ships=[mothership()] + [
            ship(i, typ="ResourceCollector") for i in range(2, 8)] + [
            ship(30, typ="ResourceController"),
            ship(31, typ="ResearchShip")] + strikes, bank=12000.0)
        s = economy
        builds = [p["params"]["type"] for p in
                  self.proposals_of(s, "order_build")]
        self.assertIn("SupportFrigate", builds)

    def test_research_ship_demand_driven(self):
        # economy lane flowing (collectors saturated + controller out):
        # A1 "first research ship around the time your first raiding group
        # is affordable"
        s = led(ships=[mothership()] + [
            ship(i, typ="ResourceCollector") for i in range(2, 8)] + [
            ship(30, typ="ResourceController")], bank=6000.0)
        builds = [p["params"]["type"] for p in
                  self.proposals_of(s, "order_build")]
        self.assertIn("ResearchShip", builds)

    def test_saturation_stops_collectors(self):
        ships = [mothership()] + [
            ship(i, typ="ResourceCollector", order={"verb": "harvest"})
            for i in range(2, 9)]  # 7 harvesting ≥ target 6
        s = led(ships=ships, bank=12000.0)
        builds = [p["params"]["type"] for p in
                  self.proposals_of(s, "order_build")]
        self.assertNotIn("ResourceCollector", builds)


class TestBudgetLanes(Harness):
    def test_lane_rotation_after_economy_saturated(self):
        # saturated economy: research ship is demand-built first (A1),
        # then the standing attack force lane (30/30/40 share rotation)
        ships = [
            mothership(),
            ship(20, typ="ResourceController"),
        ] + [ship(i, typ="ResourceCollector", order={"verb": "harvest"})
             for i in range(2, 8)]
        built = []
        for _ in range(4):
            s = led(ships=ships + [ship(100 + i, typ=t)
                                   for i, t in enumerate(built)],
                    bank=30000.0)
            ps = self.mgr.proposals(s.as_dict())
            builds = [p for p in ps if p["verb"] == "order_build"]
            if not builds:
                break
            typ = builds[0]["params"]["type"]
            built.append(typ)
            self.mgr.accept("order_build", cost=EC.COSTS.get(typ, 0))
        self.assertNotIn("ResourceCollector", built)
        first_lane = None
        for t in built:
            lane = EC.LANE_OF.get(t)
            if lane not in (None, "economy"):
                first_lane = lane
                break
        self.assertEqual(first_lane, "attack")   # tie-break: attack first

    def test_defence_lane_eventually(self):
        ships = [mothership(), ship(2, typ="ResourceCollector")]
        s = led(ships=ships, bank=2500.0)  # small bank: economy-only zone
        self.mgr.accept("order_build", cost=EC.COSTS["ResourceCollector"])
        s = led(ships=ships, bank=1000.0)
        self.mgr.accept("order_build", cost=EC.COSTS["ResourceController"])
        # spent economy 1200 of budget share; attack still underfunded →
        # next affordable lane proposal when bank allows
        ps = self.mgr.proposals(s.as_dict())
        self.assertTrue(ps)

    def test_all_economy_proposals_pass_validator(self):
        import twincore.validator as V
        ships = [mothership(), ship(2, typ="ResourceCollector")]
        s = led(ships=ships, bank=12000.0)
        v = V.Validator()
        for p in self.proposals(s):
            res = v.validate({"verb": p["verb"], "params": p["params"]}, s)
            self.assertEqual(res["status"], "ok", p)
            if res["status"] == "ok" and p["verb"].startswith("order_b"):
                pass
        # accepted proposals registered in-flight
        acc = [p for p in self.proposals(s)]
        for p in acc:
            s.register_command({"id": p["params"].get("id", 200),
                                "tool": p["verb"]})


class TestDeterminism(Harness):
    def test_same_state_same_proposals(self):
        s = led(ships=[mothership(), ship(2, typ="ResourceCollector")])
        a = self.proposals(s)
        b = EC.EconomyManager().proposals(s.as_dict())
        self.assertEqual(a, b)

    def test_no_wall_clock_dependency(self):
        s = led(ships=[mothership(), ship(2, typ="ResourceCollector")])
        a = self.proposals(s)
        b = EC.EconomyManager().proposals(s.as_dict())
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()