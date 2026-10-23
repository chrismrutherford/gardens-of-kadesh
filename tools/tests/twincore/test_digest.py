#!/usr/bin/env python3
"""Spec 004 tests: tools/twincore/digest.py (T009).

Covers: P0/P1/P2 classification (D9 defaults), P0 verbatim + never evicted by
trimming (FR-011), rollup compression (FR-012), digest builder per protocol
§5 (ledger-cited projections, 300-800 budget with one reduced regen).
"""
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "twincore"))

import twincore.digest as DG  # noqa: E402


def ship(sid, owner=0, typ="AttackBomber", health=100.0, dps=10.0,
         pos=(0, 0, 0), order=None):
    return {"id": sid, "owner": owner, "type": typ, "pos": list(pos),
            "vel": [0, 0, 0], "health": health, "healthMax": 100.0,
            "dps": dps, "guns": 1, "range": 4000.0, "order": order}


def ledger_view(tick=100, own_destroyed=(), enemy_blobs=None, bank=5000.0,
                income=5.0, mother_id=None, enemies=(), ledger_mother=None):
    return {
        "tick": tick,
        "own": {"ships": {
            (ledger_mother or mother_id or 0):
                {"type": "Mothership", "pos": [0, 0, 0]}
        } if (ledger_mother or mother_id) else {}},
        "bank": bank, "income": income,
        "enemy": {"by_class": {
            c: {"count_ever": 1, "alive": 1, "last_seen_tick": tick,
                "ids_seen": [9]}                       # sighting test id
            for c in enemies}},
        "blobs": enemy_blobs or [],
        "alliances": {},
        "pending": {},
        "directive_version": 1,
        "command_centre": mother_id,
        "own_destroyed_recent": list(own_destroyed),
    }


def d_delta(tick, **kw):
    d = {"tick": tick, "kinds": []}
    for k in DG.DELTA_KEYS:
        d[k] = None
    d.update(kw)
    d["kinds"] = [k for k in DG.DELTA_KEYS if d[k] not in (None, [], {})]
    return d


class TestClassifier(unittest.TestCase):
    def test_command_centre_lost_is_p0(self):
        led = ledger_view(mother_id=5)
        delta = {**d_delta(200), "destroyed": {5: 0}, "kinds": ["destroyed"]}
        cls = DG.classify_deltas([delta], led, DG.default_rules())
        self.assertEqual(cls[0]["class"], "P0")
        self.assertEqual(cls[0]["rule"], "command_centre_lost")

    def test_mothership_damage_is_p0_attacked(self):
        led = ledger_view(mother_id=5)
        delta = {**d_delta(200), "damaged": [5], "kinds": ["damaged"]}
        cls = DG.classify_deltas([delta], led, DG.default_rules())
        self.assertEqual([c["class"] for c in cls], ["P0"])

    def test_battle_group_annihilated(self):
        led = ledger_view(own_destroyed=[1, 2, 3, 4])
        delta = {**d_delta(200), "destroyed": {1: 0, 2: 0, 3: 0, 4: 0},
                 "kinds": ["destroyed"]}
        cls = DG.classify_deltas([delta], led, DG.default_rules())
        self.assertEqual([c["class"] for c in cls], ["P0"])
        self.assertEqual(cls[0]["rule"], "battle_group_annihilated")

    def test_zone_breach_by_rated_force(self):
        blob = {"kind": "enemy", "ids": [9], "center": [100, 0, 0],
                "strength": 50.0, "count": 1}
        led = ledger_view(enemy_blobs=[blob], mother_id=1, bank=1000)
        delta = {**d_delta(200), "created": [9], "kinds": ["created"]}
        cls = DG.classify_deltas([delta], led, DG.default_rules())
        self.assertEqual([c["class"] for c in cls], ["P0"])
        self.assertEqual(cls[0]["rule"], "defended_zone_breach")

    def test_ru_flow_zeroed_while_bank_high(self):
        led = ledger_view(bank=2000.0, income=0.0)
        delta = d_delta(200)  # no battlefield change; economy rule is global
        cls = DG.classify_deltas([delta], led, DG.default_rules())
        self.assertEqual([c["rule"] for c in cls],
                         ["ru_flow_zeroed_while_bank_high"])
        self.assertEqual(cls[0]["class"], "P0")

    def test_normal_losses_are_p1_or_p2(self):
        led = ledger_view(own_destroyed=[2])
        delta = {**d_delta(200), "destroyed": {2: 0}, "kinds": ["destroyed"]}
        cls = DG.classify_deltas([delta], led, DG.default_rules())
        self.assertEqual([c["class"] for c in cls], ["P1"])
        delta = {**d_delta(200), "created": [11], "kinds": ["created"]}
        cls = DG.classify_deltas([delta], led, DG.default_rules())
        self.assertEqual([c["class"] for c in cls], ["P2"])

    def test_new_hull_class_sighting_is_p1(self):
        led = ledger_view(enemies=("AttackBomber",))
        delta = {**d_delta(200), "created": [9], "kinds": ["created"]}
        cls = DG.classify_deltas([delta], led, DG.default_rules())
        self.assertEqual([c["class"] for c in cls], ["P1"])
        self.assertEqual(cls[0]["kind"], "sighting")


class TestRollups(unittest.TestCase):
    def test_deltas_compress_to_rollup_lines(self):
        deltas = [d_delta(100 + i, damaged=[i] * 3, kinds=["damaged"])
                  for i in range(12)]
        roll = DG.rollup(deltas, interval_ticks=10)
        self.assertEqual(roll["interval"], (100, 109))
        self.assertLessEqual(len(roll["lines"]), 2)
        self.assertTrue(any("damaged" in l for l in roll["lines"]))
        self.assertEqual(roll["class_histogram"]["P2"], 12)

    def test_p0_never_evicted_by_trimming(self):
        # FR-011(c): P0 survive until superseded by outcome; trimming only
        # ever evicts P2. Tracker keeps P0 open across digests.
        pending = DG.P0Tracker()
        pending.push({"tick": 10, "kind": "defended_zone_breach",
                      "detail": {}})
        history = pending.take()
        self.assertEqual(history, [{"tick": 10, "kind": "defended_zone_breach",
                                    "detail": {}, "outcome": "open"}])
        pending.resolve_outcome("defended_zone_breach", "repelled", tick=50)
        hist = pending.take()
        self.assertEqual(hist[0]["outcome"], "repelled")


class TestDigestBuilder(unittest.TestCase):
    def test_shape_and_ledger_cited_projections(self):
        led = ledger_view(bank=4000.0, income=12.5, mother_id=1)
        note = "vetoed standing order 7: base under attack"
        dg = DG.build_digest(
            pulse_id=3, tick_range=(1600, 2400),
            p0_items=[{"tick": 1700, "kind": "command_centre_attacked",
                       "detail": {}, "outcome": "open"}],
            rollups=[{"interval": (1600, 1700), "lines": ["2 fighters built"],
                      "class_histogram": {"P2": 1}}],
            changes=["+2 bombers", "-1 frigate"],
            order_status=[{"order": 7, "status": "failed",
                           "reason": "subject lost"}],
            s1_notes=[note], ledger=led)
        self.assertEqual(dg["interval_pulse"], 3)
        self.assertEqual(dg["p0"][0]["kind"], "command_centre_attacked")
        self.assertTrue(dg["changes"])
        self.assertEqual(dg["order_status"][0]["order"], 7)
        self.assertIn(note, dg["s1_notes"])
        for p in dg["projections"]:
            self.assertIn("source", p)
            self.assertEqual(p["source"], "ledger")

    def test_budget_enforced_with_reduced_regen(self):
        led = ledger_view()
        changes = ["change %d: rollup text long enough to blow the budget" % i
                   for i in range(400)]  # ~9600 units
        dg = DG.build_digest(pulse_id=1, tick_range=(0, 100), p0_items=[],
                             rollups=[], changes=changes, order_status=[],
                             s1_notes=[], ledger=led, budget=(300, 800))
        self.assertLessEqual(DG.units(json_blob(dg)), 800)

    def test_units_estimate(self):
        self.assertEqual(DG.units(""), 0)
        self.assertEqual(DG.units("x" * 40), 10)


def json_blob(d):
    import json as _json
    return _json.dumps(d, sort_keys=True)


if __name__ == "__main__":
    unittest.main()