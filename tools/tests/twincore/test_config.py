#!/usr/bin/env python3
"""Spec 004 tests: tools/twincore/config.py (T004).

Covers: defaults per driver-runtime.md §2, difficulty preset resolution
(cadence 2.1/1/0.5 s game time — parameters only, FR-026), budget constants
(FR-020), explicit-value precedence, loud validation errors.
"""
import copy
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "twincore"))

import twincore.config as C  # noqa: E402

BASE = {
    "seat": {"player_index": 1},
    "transport": {"mcp_url": "http://127.0.0.1:8302/mcp"},
}


class TestConfig(unittest.TestCase):
    def test_load_defaults_minimal(self):
        cfg = C.load_obj(copy.deepcopy(BASE))
        self.assertEqual(cfg.get("seat.player_index"), 1)
        # FR-029 default ~1s game time = 16 engine ticks (16 ticks/s, aiplay.c)
        self.assertEqual(cfg.get("cadence.tick_ticks"), 16)
        self.assertEqual(cfg.get("cadence.combat_gate_multiplier"), 3)
        self.assertTrue(cfg.get("cadence.p0_wake"))
        # pulse cadence default inside 30-90s game time
        self.assertTrue(30 * 16 <= cfg.get("cadence.pulse_ticks") <= 90 * 16)
        # D9 constants
        self.assertEqual(cfg.get("digest.window_pulses"), 8)
        self.assertEqual(cfg.get("digest.rollup_interval_ticks"),
                         10 * 16)  # 10 tactical ticks
        self.assertEqual(cfg.get("sticky.evidence_min"), 2)
        self.assertEqual(cfg.get("sticky.hold_ticks"), 60 * 16)
        # FR-020 budgets
        self.assertEqual(cfg.get("budgets.s1_in"), 8192)
        self.assertEqual(cfg.get("budgets.s1_out"), 200)
        self.assertEqual(cfg.get("budgets.digest_min"), 300)
        self.assertEqual(cfg.get("budgets.digest_max"), 800)
        self.assertEqual(cfg.get("budgets.s2_in"), 24576)
        self.assertEqual(cfg.get("budgets.s2_out"), 2048)
        # channels
        self.assertIn("url", cfg.get("channels.s1"))
        self.assertIn("url", cfg.get("channels.s2"))
        self.assertEqual(cfg.get("channels.s1.max_context"), 8192)
        self.assertEqual(cfg.get("channels.s2.max_context"), 24576)
        # default boot mode: S1 only
        self.assertFalse(cfg.get("strategist.enabled"))

    def test_difficulty_presets_cadence(self):
        self.assertEqual(C.seconds_to_ticks(1.0), 16)
        beginner = C.load_obj({"seat": {"player_index": 0},
                               "difficulty": "beginner"})
        self.assertEqual(beginner.get("cadence.tick_ticks"),
                         C.seconds_to_ticks(2.1))
        intermediate = C.load_obj({"seat": {"player_index": 0},
                                   "difficulty": "intermediate"})
        self.assertEqual(intermediate.get("cadence.tick_ticks"),
                         C.seconds_to_ticks(1.0))
        advanced = C.load_obj({"seat": {"player_index": 0},
                               "difficulty": "advanced"})
        self.assertEqual(advanced.get("cadence.tick_ticks"),
                         C.seconds_to_ticks(0.5))

    def test_difficulty_feature_flags_parameters_only(self):
        # playbook B3: mines/cloak/gravwell/kamikaze advanced-only; armada
        # teams 0/1/2; kamikaze draw 25% advanced
        for level, teams, can in (
                ("beginner", 0, False), ("intermediate", 1, False),
                ("advanced", 2, True)):
            cfg = C.load_obj({"seat": {"player_index": 0},
                              "difficulty": level})
            self.assertEqual(cfg.get("features.armada_teams"), teams, level)
            self.assertEqual(cfg.get("features.mines"), can, level)
            self.assertEqual(cfg.get("features.cloak"), can, level)
            self.assertEqual(cfg.get("features.gravwell"), can, level)
            self.assertEqual(cfg.get("features.kamikaze_draw"),
                             0.25 if can else 0.0, level)
        # one schema: presets differ only in data
        keys = {k for cfg in (C.load_obj({"seat": {"player_index": 0},
                                          "difficulty": l})
                              for l in ("beginner", "intermediate",
                                        "advanced"))
                for k in cfg.as_dict()}
        self.assertEqual(keys, {k for k in C.load_obj(
            {"seat": {"player_index": 0},
             "difficulty": "advanced"}).as_dict()})

    def test_explicit_values_beat_preset(self):
        cfg = C.load_obj({"seat": {"player_index": 0},
                          "difficulty": "advanced",
                          "cadence": {"tick_ticks": 64},
                          "features": {"armada_teams": 0}})
        self.assertEqual(cfg.get("cadence.tick_ticks"), 64)
        self.assertEqual(cfg.get("cadence.combat_gate_multiplier"),
                         cfg.DEFAULTS["cadence"]["combat_gate_multiplier"])
        self.assertEqual(cfg.get("features.armada_teams"), 0)

    def test_validation_errors(self):
        for bad in (
                {"seat": {"player_index": "x"}},
                {"seat": {"player_index": -1}},
                {"seat": {"player_index": 0},
                 "transport": {"mcp_url": "ftp://x"}},
                {"seat": {"player_index": 0}, "difficulty": "nightmare"},
                {"seat": {"player_index": 0}, "unknown_key": 1},
                {"seat": {"player_index": 0},
                 "budgets": {"s1_in": 0}},                            # budget>0
        ):
            with self.assertRaises(C.ConfigError, msg=repr(bad)):
                C.load_obj(copy.deepcopy(bad))

    def test_load_file(self):
        path = os.path.join(tempfile.mkdtemp(prefix="twincore-cfg-"), "c.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"seat": {"player_index": 2},
                       "strategist": {"enabled": True}}, fh)
        cfg = C.load(path)
        self.assertEqual(cfg.get("seat.player_index"), 2)
        self.assertTrue(cfg.get("strategist.enabled"))


if __name__ == "__main__":
    unittest.main()