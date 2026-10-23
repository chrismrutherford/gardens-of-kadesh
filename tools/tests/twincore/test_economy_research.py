#!/usr/bin/env python3
"""Spec 004 tests: economy research lane (T012 extension).

Deterministic research chain per the playbook/guide doctrine
(MassDrive1Kt → Chassis1 → MassDrive10Kt → Chassis2 → MassDrive100Kt →
Chassis3 → IonWeapons), delay-gated per difficulty preset, blocked while
a lab is busy, all through the same validator.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "twincore"))

import twincore.economy as EC  # noqa: E402
import twincore.recorder as R  # noqa: E402  (now_ms for unique ids)
from test_economy import led, mothership, ship  # noqa: E402


def fleet_with_lab():
    return [mothership(), ship(2, typ="ResourceCollector"),
            ship(9, typ="ResearchShip")]


class TestResearchLane(unittest.TestCase):
    def setUp(self):
        self.mgr = EC.EconomyManager()

    def _research_proposals(self, ledger_view):
        return [p for p in self.mgr.proposals(ledger_view)
                if p["verb"] == "order_research"]

    def test_chain_next_when_lab_free(self):
        s = led(ships=fleet_with_lab(), bank=9000)
        v = s.as_dict()
        v["techTree"] = {"labs": 1, "canResearch": True,
                         "researched": ["MassDrive1Kt"]}
        ps = self._research_proposals(v)
        self.assertEqual(len(ps), 1)
        self.assertEqual(ps[0]["params"]["tech"], "Chassis1")

    def test_chain_order(self):
        for researched, want in (
                ([], "MassDrive1Kt"),
                (["MassDrive1Kt"], "Chassis1"),
                (["MassDrive1Kt", "Chassis1"], "MassDrive10Kt"),
                (["MassDrive1Kt", "Chassis1", "MassDrive10Kt"], "Chassis2"),
                (["MassDrive1Kt", "Chassis1", "MassDrive10Kt", "Chassis2",
                  "MassDrive100Kt", "Chassis3"], "IonWeapons")):
            self.mgr = EC.EconomyManager()
            s = led(ships=fleet_with_lab(), bank=9000)
            v = s.as_dict()
            v["techTree"] = {"labs": 1, "canResearch": True,
                             "researched": list(researched)}
            ps = self._research_proposals(v)
            self.assertEqual([p["params"]["tech"] for p in ps],
                             [want], researched)

    def test_no_lab_no_research(self):
        s = led(ships=[mothership(), ship(2, typ="ResourceCollector")],
                bank=9000)
        v = s.as_dict()
        v["techTree"] = {"labs": 0, "canResearch": False, "researched": []}
        self.assertEqual(self._research_proposals(v), [])

    def test_lab_busy_blocks(self):
        s = led(ships=fleet_with_lab(), bank=9000)
        v = s.as_dict()
        v["techTree"] = {"labs": 1, "canResearch": True,
                         "researched": []}
        v["research"] = [{"tech": "Chassis1", "labs": 1, "timeLeft": 500}]
        self.assertEqual(self._research_proposals(v), [])

    def test_delay_gating(self):
        s = led(ships=fleet_with_lab(), bank=9000)  # ledger tick 100
        v = s.as_dict()
        v["techTree"] = {"labs": 1, "canResearch": True, "researched": []}
        got1 = self._research_proposals(v)
        self.assertEqual(len(got1), 1)
        self.mgr.accept("order_research", cost=0)  # proposal issued
        # immediately again: delay blocks (intermediate preset 45s = 720)
        self.assertEqual(self._research_proposals(v), [])
        # after the delay passes: next chain tech proposed
        v2 = s.as_dict()
        v2["tick"] = v2["tick"] + EC.seconds_to_ticks(46.0)
        v2["techTree"] = {"labs": 1, "canResearch": True,
                          "researched": ["MassDrive1Kt", "Chassis1"]}
        ps = self._research_proposals(v2)
        self.assertEqual([p["params"]["tech"] for p in ps],
                         ["MassDrive10Kt"])

    def test_all_researched_stops(self):
        s = led(ships=fleet_with_lab(), bank=9000)
        v = s.as_dict()
        v["techTree"] = {"labs": 1, "canResearch": True,
                         "researched": list(EC.RESEARCH_CHAIN)}
        self.assertEqual(self._research_proposals(v), [])

    def test_research_proposals_pass_validator(self):
        import twincore.validator as V
        s = led(ships=fleet_with_lab(), bank=9000)
        v = s.as_dict()
        v["techTree"] = {"labs": 1, "canResearch": True, "researched": []}
        val = V.Validator()
        for p in self.mgr.proposals(v):
            res = val.validate({"verb": p["verb"], "params": p["params"]}, s)
            self.assertEqual(res["status"], "ok", p)


if __name__ == "__main__":
    unittest.main()