#!/usr/bin/env python3
"""Spec 004 tests: tools/twincore/state_store.py (T008).

Covers: canonical ledger per data-model.md — fleet inventory + unit states,
RU bank + income projection, per-hull-class enemy knowledge, blob ratings
(the 1.4× overkill input), alliance state, pending commands. Rebuild ==
incremental correctness on the same feed (FR-013).
"""
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "twincore"))

import twincore.state_store as S  # noqa: E402


def ship(sid, owner=0, typ="AttackBomber", health=100.0, dps=10.0,
         pos=(0, 0, 0), order=None):
    d = {"id": sid, "owner": owner, "type": typ, "pos": list(pos),
         "vel": [0, 0, 0], "health": health, "healthMax": 100.0,
         "dps": dps, "guns": 1, "range": 4000.0, "order": order}
    return d


def snap(tick, ships, players=None, income=5.0, resources=None):
    return {"tick": tick, "timeElapsed": tick / 16.0, "paused": False,
            "income": income, "ships": ships,
            "players": players if players is not None else [
                {"index": 0, "isAI": False, "resourceUnits": 5000,
                 "alive": True, "lost": 0},
                {"index": 1, "isAI": True, "resourceUnits": 3000,
                 "alive": True, "lost": 0}],
            "resources": resources or [], "buildQueue": [], "research": [],
            "techTree": {"labs": 2, "canResearch": True, "locked": [],
                         "researched": []},
            "matchStatus": {"running": True, "boundPlayerIndex": 0,
                            "winner": None, "perPlayer": []}}


class LedgerHarness(unittest.TestCase):
    def setUp(self):
        self.led = S.Ledger(own_player=0)


class TestInventoryAndBank(LedgerHarness):
    def test_rebuild_then_incremental_same_result(self):
        s1 = snap(100, [ship(1), ship(2), ship(3, owner=1)])
        inc = S.Ledger(own_player=0)
        inc.rebuild(copy.deepcopy(s1))
        inc2 = S.Ledger(own_player=0)
        inc2.rebuild(snap(0, []))
        inc2.apply_snapshot(copy.deepcopy(s1))
        base = inc.as_dict()
        self.assertEqual(base["own"]["ships"], inc2.as_dict()["own"]["ships"])
        self.assertEqual(base["bank"], 5000)
        self.assertEqual(base["income"], 5.0)

    def test_income_unknown(self):
        self.led.rebuild(snap(100, [], income=-1.0))
        self.assertIsNone(self.led.as_dict()["income"])

    def test_destroyed_removes_both_sides(self):
        self.led.rebuild(snap(100, [ship(1), ship(3, owner=1)]))
        self.led.apply_delta({"tick": 110, "kinds": ["destroyed"],
                              "destroyed": {"3": 1}})
        self.assertNotIn(3, self.led.as_dict()["ships_all"])
        self.assertIn(1, self.led.as_dict()["ships_all"])

    def test_order_states_stored(self):
        s = ship(1, order={"verb": "attack", "targetId": 9})
        self.led.rebuild(snap(100, [s]))
        got = self.led.as_dict()["own"]["ships"][1]
        self.assertEqual(got["order"]["verb"], "attack")


class TestEnemyKnowledge(LedgerHarness):
    def test_per_class_ever_observed(self):
        self.led.rebuild(snap(100, [ship(3, owner=1, typ="AttackBomber")]))
        self.led.apply_snapshot(snap(130, [
            ship(3, owner=1, typ="AttackBomber"),
            ship(4, owner=1, typ="AttackBomber"),
            ship(5, owner=1, typ="HeavyCorvette")]))
        k = self.led.as_dict()["enemy"]["by_class"]
        self.assertEqual(k["AttackBomber"]["count_ever"], 2)
        self.assertEqual(k["HeavyCorvette"]["count_ever"], 1)
        self.led.apply_delta({"tick": 140, "kinds": ["destroyed"],
                              "destroyed": {"3": 0}})
        k = self.led.as_dict()["enemy"]["by_class"]
        # ever-observed persists; alive count drops to 0, last_seen retained
        self.assertEqual(k["AttackBomber"]["count_ever"], 2)
        self.assertEqual(k["AttackBomber"]["alive"], 1)  # 4 alive, 3 destroyed
        self.assertEqual(k["AttackBomber"]["last_seen_tick"], 140)

    def test_invisible_enemy_stays_empty(self):
        # edge case: no sensor contact — knowledge stays empty-class, no
        # fabrication
        self.led.rebuild(snap(100, [ship(1)]))
        self.assertEqual(self.led.as_dict()["enemy"]["by_class"], {})


class TestBlobs(LedgerHarness):
    def test_two_clusters_apart(self):
        ships = [ship(1, pos=(0, 0, 0)), ship(2, pos=(100, 0, 0)),
                 ship(9, owner=1, pos=(100000, 0, 0))]
        self.led.rebuild(snap(100, ships))
        blobs = self.led.as_dict()["blobs"]
        own = [b for b in blobs if b["kind"] == "own"]
        enemy = [b for b in blobs if b["kind"] == "enemy"]
        self.assertEqual(len(own), 1)
        self.assertEqual(len(enemy), 1)
        self.assertEqual(own[0]["ids"], [1, 2])
        self.assertEqual(own[0]["strength"], 20.0)  # sum dps

    def test_enemy_blob_merges_when_close(self):
        ships = [ship(9, owner=1, pos=(0, 0, 0)),
                 ship(10, owner=1, pos=(1000, 0, 0))]
        self.led.rebuild(snap(100, ships))
        enemy = [b for b in self.led.as_dict()["blobs"]
                 if b["kind"] == "enemy"]
        self.assertEqual(len(enemy), 1)
        self.assertEqual(sorted(enemy[0]["ids"]), [9, 10])

    def test_overkill_rating_helper(self):
        self.led.rebuild(snap(100, [
            ship(9, owner=1, pos=(0, 0, 0), dps=30.0)]))
        b = next(b for b in self.led.blob_for(9))
        self.assertEqual(b["strength"], 30.0)
        # 1.4× threshold input: 42 needed for overkill (FR/doc aggression)
        self.assertAlmostEqual(S.overkill_requirement(b), 42.0)


class TestPendingAndAlliances(LedgerHarness):
    def test_pending_register_then_resolve(self):
        cmd = {"id": 41, "tool": "order_move", "issued_tick": 100}
        self.led.register_command(copy.deepcopy(cmd))
        self.assertIn(41, self.led.in_flight())
        self.led.apply_delta({"tick": 110, "kinds": ["orders_applied"],
                              "orders_applied": [41]})
        self.assertNotIn(41, self.led.in_flight())

    def test_rejected_resolves_pending(self):
        self.led.register_command({"id": 42, "tool": "order_attack",
                                   "issued_tick": 100})
        self.led.apply_delta({"tick": 110, "kinds": ["orders_rejected"],
                              "orders_rejected": [{"id": 42, "aux": 3}]})
        self.assertNotIn(42, self.led.in_flight())
        rej = self.led.last_rejections()
        self.assertEqual(rej, [{"id": 42, "aux": 3}])

    def test_alliance_state(self):
        self.assertFalse(self.led.is_allied(1))
        self.led.set_alliance(1, True)
        self.assertTrue(self.led.is_allied(1))
        self.led.set_alliance(1, False)
        self.assertFalse(self.led.is_allied(1))

    def test_tick_ref(self):
        self.led.rebuild(snap(321, []))
        self.assertEqual(self.led.as_dict()["tick"], 321)


if __name__ == "__main__":
    unittest.main()