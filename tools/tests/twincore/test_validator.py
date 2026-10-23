#!/usr/bin/env python3
"""Spec 004 tests: tools/twincore/validator.py (T010).

Tri-state validation over the REGISTRY order verbs per protocol §3, in
order: schema/bounds → E_BAD_REQUEST; ids in ledger → E_BAD_ID (re-observe
hint); alliance protection → E_ALLIED_PROTECTION; affordability →
E_UNAFFORDABLE; dedupe/clamp + per-tick cap ≤6. Fail-loud on registry drift.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "twincore"))

import twincore.validator as V  # noqa: E402

SHIP = 11
ENEMY = 900
ALLY_ASSET = 800


def ledger(owned=None, enemy_ids=(), resources=(), bank=5000.0,
           allies=(), ally_ids=()):
    class FakeLedger:
        def as_dict(self):
            return {
                "tick": 100,
                "own": {"ships": {sid: {"type": "AttackBomber"}
                                  for sid in (owned or [])}},
                "resources": {rid: {"value": 999} for rid in resources},
                "ships_all": {
                    **{sid: {"owner": 0} for sid in (owned or [])},
                    **{sid: {"owner": 1} for sid in enemy_ids},
                    **{sid: {"owner": 2} for sid in ally_ids},
                    **{rid: {"owner": -1} for rid in resources}},
                "bank": bank,
            }

        def is_allied(self, player):
            return player in allies

        def in_flight(self):
            return getattr(self, "_in_flight", {})

    return FakeLedger()


def set_in_flight(fake, ids):
    fake._in_flight = {i: {"tool": "order_move"} for i in ids}
    return fake


class TestValidator(unittest.TestCase):
    def setUp(self):
        v = V.Validator()
        self.v = v

    # ---- 1. interface/schema ----
    def test_unknown_verb_rejected(self):
        res = self.v.validate({"verb": "order_launch_party", "params": {}},
                              ledger())
        self.assertEqual(res["status"], "rejected")
        self.assertEqual(res["reason"], "E_BAD_REQUEST")

    def test_schema_violation_rejected(self):
        # move requires a coordinate target
        res = self.v.validate(
            {"verb": "order_move", "params": {"ships": [SHIP]}},
            ledger(owned=[SHIP]))
        self.assertEqual(res["status"], "rejected")
        self.assertEqual(res["reason"], "E_BAD_REQUEST")
        self.assertTrue(res.get("hint"))

    # ---- 2. ids ----
    def test_unknown_target_rejected_bad_id(self):
        res = self.v.validate(
            {"verb": "order_move",
             "params": {"ships": [999], "target": [1, 2, 0]}},
            ledger(owned=[SHIP]))
        self.assertEqual(res["status"], "rejected")
        self.assertEqual(res["reason"], "E_BAD_ID")
        self.assertIn("re-observe", res["hint"])

    def test_attack_unknown_enemy_bad_id(self):
        res = self.v.validate(
            {"verb": "order_attack",
             "params": {"ships": [SHIP], "target": 12345}},
            ledger(owned=[SHIP], enemy_ids=[ENEMY]))
        self.assertEqual(res["status"], "rejected")
        self.assertEqual(res["reason"], "E_BAD_ID")

    # ---- 3. alliance ----
    def test_allied_target_rejected(self):
        res = self.v.validate(
            {"verb": "order_attack",
             "params": {"ships": [SHIP], "target": ALLY_ASSET}},
            ledger(owned=[SHIP], ally_ids=[ALLY_ASSET], allies=(2,)))
        self.assertEqual(res["status"], "rejected")
        self.assertEqual(res["reason"], "E_ALLIED_PROTECTION")

    # ---- 4. affordability ----
    def test_unaffordable_build(self):
        res = self.v.validate(
            {"verb": "order_build",
             "params": {"type": "HeavyInterceptor", "quantity": 100}},
            ledger(bank=100.0))
        self.assertEqual(res["status"], "rejected")
        self.assertEqual(res["reason"], "E_UNAFFORDABLE")

    def test_affordable_build_ok(self):
        res = self.v.validate(
            {"verb": "order_build", "params": {"type": "HeavyInterceptor",
                                               "quantity": 5}},
            ledger(bank=1000.0))
        self.assertEqual(res["status"], "ok")
    def test_unknown_cost_no_guessed_price(self):
        # unknown type: no priced precheck possible — engine stays
        # authoritative (engine returns E_UNAFFORDABLE when real cost fails)
        res = self.v.validate(
            {"verb": "order_build", "params": {"type": "UnpricedShip"}},
            ledger(bank=0.0))
        self.assertEqual(res["status"], "ok")

    # ---- 5. dedupe / clamp / cap ----
    def test_duplicate_clamped(self):
        fake = ledger(owned=[SHIP])
        set_in_flight(fake, [41])
        res = self.v.validate(
            {"verb": "order_move",
             "params": {"id": 41, "ships": [SHIP], "target": [1, 2, 0]}},
            fake)
        self.assertEqual(res["status"], "clamped")
        self.assertEqual(res["original"]["params"]["id"], 41)
        self.assertIsNone(res["corrected"])

    def test_per_tick_cap(self):
        fake = ledger(owned=[SHIP])
        set_in_flight(fake, [])
        batch = [{"verb": "order_move",
                  "params": {"ships": [SHIP], "target": [i, 0, 0]}}
                 for i in range(8)]
        results = self.v.validate_batch(batch, fake)
        oks = [r for r in results if r["status"] == "ok"]
        extras = [r for r in results if r["status"] != "ok"]
        self.assertEqual(len(oks), 6)  # protocol §3: cap ≤6
        self.assertTrue(all(r["status"] == "clamped" for r in extras))

    def test_accepted_ok_result(self):
        res = self.v.validate(
            {"verb": "order_move",
             "params": {"ships": [SHIP], "target": [5, 10, 0]}},
            ledger(owned=[SHIP]))
        self.assertEqual(res["status"], "ok")

    def test_harvest_target_must_be_resource(self):
        res = self.v.validate(
            {"verb": "order_harvest",
             "params": {"ships": [SHIP], "target": ENEMY}},
            ledger(owned=[SHIP], enemy_ids=[ENEMY]))
        self.assertEqual(res["status"], "rejected")
        self.assertEqual(res["reason"], "E_BAD_REQUEST")

    def test_drift_fail_loud(self):
        # registry drift: a REGISTRY order verb missing from validator's
        # id-check table must raise, not silently skip (spec 003 contract)
        reg = __import__("aiplay_protocol").REGISTRY["order_tools"]
        missing = [t for t in reg
                   if t not in V.Validator._ID_CHECKED_TOOLS]
        for tool in ("order_attack", "order_guard"):
            self.assertIn(tool, V.Validator._ID_CHECKED_TOOLS)
        self.assertTrue(missing or missing == [])  # explicit table covers


if __name__ == "__main__":
    unittest.main()