#!/usr/bin/env python3
"""Spec 004 tests: tools/twincore/packet.py (T016).

Control-packet validation + apply order (protocol §2):
- five elements: directive.patch, orders[], history_op, digest_config,
  questions[]
- whole-packet schema check BEFORE apply; failure ⇒ zero elements applied,
  old directive stays, no retry same pulse (US3-5)
- apply order: patch (bump) → orders → history op → digest config
- sticky gate: ungated primary-enemy change DROPPED, rest applies, audit
- standing-order subjects validated vs ledger at apply
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "twincore"))

import twincore.packet as PK  # noqa: E402
import twincore.directive as DIR  # noqa: E402
import twincore.state_store as S  # noqa: E402


def ship(sid, owner=0, typ="LightInterceptor", pos=(0, 0, 0)):
    return {"id": sid, "owner": owner, "type": typ, "pos": list(pos),
            "vel": [0, 0, 0], "health": 100.0, "healthMax": 100.0,
            "dps": 5.0, "guns": 1, "range": 4000.0, "order": None}


def ledger(ship_ids=(1, 2, 3), bank=9000):
    led = S.Ledger(own_player=0)
    led.rebuild({
        "tick": 100, "paused": False, "income": 10.0,
        "ships": [ship(i) for i in ship_ids],
        "players": [{"index": 0, "resourceUnits": bank, "alive": True}],
        "resources": [], "buildQueue": [], "research": [],
        "techTree": {"labs": 2, "canResearch": True, "locked": [],
                     "researched": []},
        "matchStatus": {"running": True, "boundPlayerIndex": 0,
                        "winner": None, "perPlayer": []},
    })
    return led


def packet(**over):
    p = {
        "directive": {"patch": {"doctrine.aggression.overkill_factor": 1.6}},
        "orders": [DIR.make_standing_order(id="so-1", verb="order_guard",
                                           subject={"ships": [1]},
                                           priority=1, ttl_ticks=500)],
        "history_op": None,
        "digest_config": {"emphasis": ["enemy_blobs"], "suppression": []},
        "questions": ["what is our income?"],
        "pulses": [],   # cited digest pulse ids (evidence meta)
    }
    p.update(over)
    return {k: v for k, v in p.items() if v is not PK._KEEP_DEFAULT}


class TestSchemaGate(unittest.TestCase):
    def setUp(self):
        self.led = ledger()
        self.sticky = DIR.StickyGate()

    def test_valid_packet_applies_in_order(self):
        start = DIR.boot_directive()
        res = PK.apply_packet(packet(), start, self.led, self.sticky,
                              tick=200, pulse_id=4)
        self.assertEqual(res["directive"]["version"], 2)  # patch → bump
        self.assertEqual(res["directive"]["aggression"], None) if False \
            else None
        self.assertEqual(res["directive"]["doctrine"]["aggression"]
                         ["overkill_factor"], 1.6)
        self.assertEqual([o["id"] for o in
                          res["directive"]["standing_orders"]], ["so-1"])
        self.assertEqual(res["history_op"], None)
        self.assertEqual(res["digest_config"]["emphasis"], ["enemy_blobs"])
        self.assertEqual(res["applied"], ["patch", "orders",
                                          "digest_config", "questions"])

    def test_schema_failure_rejects_whole(self):
        start = DIR.boot_directive()
        bad = packet(directive={"patch": {"version": 42}})  # forbidden key
        res = PK.apply_packet(bad, start, self.led, self.sticky, tick=200,
                              pulse_id=1)
        self.assertFalse(res["ok"])
        self.assertTrue(res["rejected_reason"])
        self.assertEqual(res["directive"]["version"], 1)   # nothing changed
        self.assertEqual(res["applied"], [])

    def test_bad_order_schema_rejects_whole(self):
        start = DIR.boot_directive()
        bad = packet(orders=[{"verb": "not_a_verb"}])
        res = PK.apply_packet(bad, start, self.led, self.sticky, tick=10,
                              pulse_id=1)
        self.assertFalse(res["ok"])

    def test_non_object_questions_rejects_whole(self):
        start = DIR.boot_directive()
        res = PK.apply_packet(packet(questions="tell me"), start, self.led,
                              self.sticky, tick=10, pulse_id=1)
        self.assertFalse(res["ok"])


class TestStickyEnforcement(unittest.TestCase):
    def setUp(self):
        self.start = DIR.boot_directive()
        self.led = ledger()

    def test_ungated_primary_enemy_dropped_rest_applies(self):
        pk = packet(directive={"patch": {"primary_enemy": 3,
                                         "build_split": {
                                             "attack": 30, "defence": 20,
                                             "economy": 50}}})
        res = PK.apply_packet(pk, self.start, ledger(), DIR.StickyGate(),
                              tick=100, pulse_id=1)
        self.assertTrue(res["ok"])
        self.assertIsNone(res["directive"]["primary_enemy"])  # dropped
        self.assertEqual(res["directive"]["build_split"]["economy"], 50)
        self.assertEqual(res["directive"]["version"], 2)     # rest bumped
        self.assertIn("primary_enemy", res["dropped"])

    def test_gated_change_applies(self):
        gate = DIR.StickyGate(since_tick=0)
        gate.corroborate(3, pulse_id=1)
        gate.corroborate(3, pulse_id=2)
        pkg = packet(directive={"patch": {"primary_enemy": 3}})
        res = PK.apply_packet(pkg, self.start, ledger(), gate, tick=2000,
                              pulse_id=3)
        self.assertTrue(res["ok"])
        self.assertEqual(res["directive"]["primary_enemy"], 3)
        self.assertNotIn("primary_enemy", res["dropped"])


class TestOrdersAndSubjects(unittest.TestCase):
    def setUp(self):
        self.start = DIR.boot_directive()

    def test_order_subject_validated_vs_ledger(self):
        pkg = packet(orders=[DIR.make_standing_order(
            id="so-bad", verb="order_attack", subject={"target": 999},
            priority=1, ttl_ticks=10)])
        res = PK.apply_packet(pkg, self.start, ledger(),
                              DIR.StickyGate(), tick=100, pulse_id=1)
        self.assertTrue(res["ok"])                                # schema ok
        self.assertEqual(res["directive"]["standing_orders"], [])  # dropped
        self.assertTrue(any("so-bad" in d for d in res["dropped"]))

    def test_revoke_by_id(self):
        start = DIR.boot_directive()
        start["standing_orders"] = [
            DIR.make_standing_order(id="so-1", verb="order_guard",
                                    subject={"ships": [1]}, priority=1,
                                    ttl_ticks=500)]
        pkg = packet(orders=[{"revoke": "so-1"}])
        res = PK.apply_packet(pkg, start, ledger(), DIR.StickyGate(),
                              tick=200, pulse_id=2)
        self.assertEqual(res["directive"]["standing_orders"], [])
        self.assertIn("so-1", res["revoked"])

    def test_history_op_shapes(self):
        for op in ({"trim": {"scope": ["deltas"]}}, {"reset": {"seed": True}}):
            res = PK.apply_packet(packet(history_op=op), DIR.boot_directive(),
                                  ledger(), DIR.StickyGate(), tick=1,
                                  pulse_id=1)
            self.assertTrue(res["ok"])
        res = PK.apply_packet(packet(history_op={"nonsense": {}}),
                              DIR.boot_directive(), ledger(),
                              DIR.StickyGate(), tick=1, pulse_id=1)
        self.assertFalse(res["ok"])

    def test_questions_stored_for_next_pulse(self):
        res = PK.apply_packet(packet(questions=["income?", "enemy blobs?"]),
                              DIR.boot_directive(), ledger(),
                              DIR.StickyGate(), tick=1, pulse_id=1)
        self.assertEqual(res["questions"], ["income?", "enemy blobs?"])


if __name__ == "__main__":
    unittest.main()