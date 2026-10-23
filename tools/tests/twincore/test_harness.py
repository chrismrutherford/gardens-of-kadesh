#!/usr/bin/env python3
"""Spec 005 tests: Draft v3 harness measures (T038; SC-002/005/006).

Pure functions over trail rows — synthetic seeded fixtures, no postgres,
no engine (Constitution III). The series/report CLI is T018/T019.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "twincore"))

import twincore.harness as H  # noqa: E402


class TestV4DraftV3Measures(unittest.TestCase):
    def rows(self):
        return [
            # verdicts: 3 total, one repeat, one illegible
            {"kind": "validation", "tick": 1, "status": "verdict",
             "verdict_kind": "deny", "subject": "p2",
             "verdict_text": "order_research tech 'X' unknown; "
                             "researchable now: [Chassis1]", "repeat": 1},
            {"kind": "validation", "tick": 2, "status": "verdict",
             "verdict_kind": "deny", "subject": "s1:m7",
             "verdict_text": "ships outside grant d1; allowed: ships "
                             "[2,3,4]", "repeat": 2},
            {"kind": "validation", "tick": 3, "status": "verdict",
             "verdict_kind": "deny", "subject": "w1",
             "verdict_text": "refused", "repeat": 1},
            # commands: 5 applied, 1 wire-denied
            *[{"kind": "command", "tick": i, "status": "applied",
               "item_id": "t%d" % i} for i in range(1, 6)],
            {"kind": "command", "tick": 6, "status": "engine_denied",
             "item_id": "p2", "aux": 8},
            # programs: 4 applied, 3 with arc
            {"kind": "program", "tick": 100, "event": "applied",
             "arc": True, "version": 1},
            {"kind": "program", "tick": 250, "event": "applied",
             "arc": True, "version": 2},
            {"kind": "program", "tick": 700, "event": "applied",
             "arc": True, "version": 3},
            {"kind": "program", "tick": 900, "event": "applied",
             "arc": False, "version": 4},
            # reflex hygiene: one stale redispatch
            {"kind": "validation", "tick": 55, "status":
             "stale_inflight_dropped", "oids": [777]},
            # s2_in usage: 2 rows, one full superset, one missing menus
            {"kind": "usage", "tick": 10, "channel": "s2_in", "total": 900,
             "sections": {"arc": 1, "enemy": 3, "asteroids": 32,
                          "build_menu": 2, "research_menu": 3}},
            {"kind": "usage", "tick": 250, "channel": "s2_in", "total": 900,
             "sections": {"arc": 1, "enemy": 3, "asteroids": 32,
                          "build_menu": 0, "research_menu": 0}},
        ]

    def test_measures_shape_and_values(self):
        m = H.v4_draftv3_measures(self.rows())
        self.assertEqual(m["verdicts"], 3)
        # deny rate = verdicts / (verdicts + applied commands) = 3/(3+5)
        self.assertAlmostEqual(m["deny_rate"], 3 / 8, places=2)
        # 1 of 3 verdict repeats
        self.assertAlmostEqual(m["repeated_identical_denial_rate"], 1 / 3,
                               places=2)
        # legibility: 2 of 3 name an allowed alternative
        self.assertAlmostEqual(m["verdict_legibility"], 2 / 3, places=2)
        self.assertEqual(m["verdict_legibility_violations"], 1)
        # arc coverage 3/4; gap 700-250=450 vs 250-100=150 → 450
        self.assertAlmostEqual(m["arc_coverage"], 0.75, places=2)
        self.assertEqual(m["arc_max_gap_ticks"], 450)
        self.assertEqual(m["reflex_stale_redispatches"], 1)
        # superset: 1 of 2 s2_in usage rows carried every FR-009 section
        self.assertAlmostEqual(m["superset_presence"], 0.5, places=2)
        self.assertEqual(m["superset_inputs"], 2)

    def test_empty_trail_zero_division_safe(self):
        m = H.v4_draftv3_measures([])
        self.assertEqual(m["deny_rate"], 0.0)
        self.assertEqual(m["verdict_legibility"], 1.0)   # vacuous pass
        self.assertEqual(m["arc_coverage"], 0.0)
        self.assertEqual(m["superset_presence"], 0.0)


if __name__ == "__main__":
    unittest.main()
