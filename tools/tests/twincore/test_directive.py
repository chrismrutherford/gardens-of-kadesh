#!/usr/bin/env python3
"""Spec 004 tests: tools/twincore/directive.py (T011).

Covers: directive schema (protocol §4), boot default (FR-023), monotonic
versioning of semantic-only patches (FR-016), sticky goal gate (FR-017),
standing order validation (data-model).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "twincore"))

import twincore.directive as DIR  # noqa: E402


class TestBoot(unittest.TestCase):
    def test_boot_directive_shape(self):
        d = DIR.boot_directive()
        self.assertEqual(d["version"], 1)
        self.assertTrue(d["boot_default"])
        self.assertIsNone(d["primary_enemy"])          # ≤1; none pinned
        self.assertEqual(d["build_split"],
                         {"attack": 30, "defence": 30, "economy": 40})
        self.assertEqual(d["doctrine"]["aggression"]["overkill_factor"], 1.4)
        self.assertEqual(d["standing_orders"], [])     # conservative opening
        self.assertIn("topics", d["attentions"])

    def test_validation_round_trip(self):
        self.assertEqual(DIR.validate_directive(DIR.boot_directive()), [])

    def test_schema_rejects(self):
        for mutate in (
            lambda d: d.update(version=0),
            lambda d: d.update(version="x"),
            lambda d: d.update(build_split={"attack": 50, "defence": 50,
                                            "economy": 40}),
            lambda d: d.update(attentions={"economy": {"loudness": 5}}),
            lambda d: d["doctrine"].update(aggression={"overkill_factor": 0}),
        ):
            d = DIR.boot_directive()
            mutate(d)
            errs = DIR.validate_directive(d)
            self.assertTrue(errs, "mutation should fail: %r" % (mutate,))


class TestPatches(unittest.TestCase):
    def test_semantic_patch_bumps_version(self):
        d = DIR.boot_directive()
        new = DIR.apply_patch(d, {"primary_enemy": 2})
        self.assertEqual(new["version"], 2)
        self.assertEqual(new["primary_enemy"], 2)
        self.assertIs(new, None) if False else None

    def test_patch_nested_paths(self):
        d = DIR.boot_directive()
        new = DIR.apply_patch(d, {"doctrine.aggression.overkill_factor": 1.6})
        self.assertEqual(new["doctrine"]["aggression"]["overkill_factor"],
                         1.6)
        self.assertEqual(new["version"], 2)

    def test_illegal_patch_rejected(self):
        d = DIR.boot_directive()
        for patch in ({"version": 9}, {"boot_default": True},
                      {"build_split.economy": 101},
                      {"doctrine.nonsense": 1}):
            with self.assertRaises(RuntimeError, msg=repr(patch)):
                DIR.apply_patch(d, patch)
        self.assertEqual(d["version"], 1)   # rejected patches never bump
        self.assertTrue(d["boot_default"])


class TestStickyGate(unittest.TestCase):
    def test_requires_two_pulses_evidence_and_hold(self):
        gate = DIR.StickyGate(evidence_min=2, hold_ticks=1800)
        ok, why = gate.can_change(3, tick=1000)   # no evidence
        self.assertFalse(ok, why)
        gate.corroborate(3, pulse_id=1)
        ok, _ = gate.can_change(3, tick=1100)
        self.assertFalse(ok)                      # only 1 distinct pulse
        gate.corroborate(3, pulse_id=2)
        ok, why = gate.can_change(3, tick=1400)
        self.assertFalse(ok)                      # hold not elapsed
        ok, _ = gate.can_change(3, tick=3000)
        self.assertTrue(ok)

    def test_same_pulse_corroboration_counts_once(self):
        gate = DIR.StickyGate(evidence_min=2, hold_ticks=0)
        gate.corroborate(5, pulse_id=1)
        gate.corroborate(5, pulse_id=1)
        ok, _ = gate.can_change(5, tick=10)
        self.assertFalse(ok)

    def test_corroboration_resets_on_different_enemy(self):
        gate = DIR.StickyGate(evidence_min=2, hold_ticks=0)
        gate.corroborate(5, pulse_id=1)
        gate.corroborate(7, pulse_id=2)
        ok, _ = gate.can_change(5, tick=10)
        self.assertFalse(ok)                      # 5 only got 1 pulse
        ok, _ = gate.can_change(7, tick=10)
        self.assertTrue(ok)


class TestStandingOrders(unittest.TestCase):
    def test_validate_order(self):
        o = DIR.make_standing_order(
            id="so-1", verb="order_move", subject={"ships": [1]},
            priority=3, ttl_ticks=100)
        self.assertEqual(DIR.validate_standing_order(o), [])

    def test_bad_orders(self):
        bad = [
            DIR.make_standing_order(id="x", verb="nonsense", subject={},
                                    priority=1, ttl_ticks=10),
            DIR.make_standing_order(id="x", verb="order_move", subject={},
                                    priority=1, ttl_ticks=-5),
            DIR.make_standing_order(id="x", verb="order_move", subject={},
                                    priority=1, ttl_ticks=10, veto=False),
        ]
        got = [DIR.validate_standing_order(o) for o in bad]
        self.assertTrue(all(got))


if __name__ == "__main__":
    unittest.main()