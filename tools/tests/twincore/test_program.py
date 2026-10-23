#!/usr/bin/env python3
"""Spec 005 tests: tools/twincore/program.py — whole-packet Program
validation, delegation envelope, micro-packets, input builders.

T003/T024 (spec 005): whole-packet all-or-nothing (FR-008), monotonic
versions, delegate[] schema (FR-017), micro legality (FR-018), budgeted
pulse input with history-first prune (FR-009/FR-013). Pure unit tests —
no engine, no postgres, no LLM (Constitution III).
"""
import copy
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "twincore"))

import conftest as CF  # noqa: E402  (same dir; pytest prepends test dir)
import twincore.program as P  # noqa: E402


def raises_reason(fn, fragment):
    try:
        fn()
    except P.ProgramError as e:
        v = fragment.lower() in e.reason.lower()
        assert v, "reason %r lacks %r" % (e.reason, fragment)
        return e.reason
    raise AssertionError("ProgramError(%r) not raised" % fragment)


class TestWholePacket(unittest.TestCase):
    def test_valid_full_packet(self):
        prog = P.validate_packet(copy.deepcopy(CF.v4_packet(version=1)),
                                 last_accepted_version=-1)
        self.assertEqual(prog.version, 1)
        self.assertEqual(prog.issued_tick, 100)
        self.assertTrue(prog.has_envelope())
        self.assertEqual(len(prog.production), 1)
        self.assertEqual(len(prog.tasking), 1)
        self.assertIsNotNone(prog.grant_for("d1"))
        self.assertIsNone(prog.grant_for("d9"))

    def test_empty_program_valid_reflexes_only(self):
        prog = P.validate_packet({"version": 3}, last_accepted_version=2)
        self.assertEqual(prog.production, [])
        self.assertEqual(prog.tasking, [])
        self.assertFalse(prog.has_envelope())   # advisory-only tier
        self.assertEqual(prog.live_grants(0), [])

    def test_malformed_json_string_rejected(self):
        raises_reason(lambda: P.validate_packet("{version:", -1), "json")

    def test_non_object_rejected(self):
        raises_reason(lambda: P.validate_packet([1, 2], -1), "object")

    def test_version_must_exceed_last_accepted(self):
        raises_reason(lambda: P.validate_packet(
            {"version": 5}, last_accepted_version=5), "exceed")
        raises_reason(lambda: P.validate_packet(
            {"version": 4}, last_accepted_version=5), "exceed")

    def test_version_int_only(self):
        raises_reason(lambda: P.validate_packet(
            {"version": True}, -1), "version")
        raises_reason(lambda: P.validate_packet(
            {"version": "7"}, -1), "version")

    def test_issued_tick_nonnegative(self):
        raises_reason(lambda: P.validate_packet(
            {"version": 2, "issued_tick": -1}, -1), "issued_tick")

    def test_posture_length_cap(self):
        p = {"version": 2, "posture": "x" * 401}
        raises_reason(lambda: P.validate_packet(p, -1), "posture")
        ok = P.validate_packet({"version": 2, "posture": "y" * 400}, -1)
        self.assertEqual(ok.posture, "y" * 400)


class TestItems(unittest.TestCase):
    def test_bad_item_id(self):
        d = CF.v4_packet()
        d["production"] = [{"id": "BAD", "verb": "order_build",
                            "params": {"type": "X"}}]
        raises_reason(lambda: P.validate_packet(d, -1), "id")

    def test_duplicate_ids_across_lists(self):
        d = CF.v4_packet()
        d["tasking"] = [{"id": "p1", "verb": "order_halt", "ships": [1]}]
        raises_reason(lambda: P.validate_packet(d, -1), "twice")

    def test_build_params(self):
        d = CF.v4_packet()
        d["production"] = [{"id": "p1", "verb": "order_build", "params": {}}]
        raises_reason(lambda: P.validate_packet(d, -1), "type")
        d["production"] = [{"id": "p1", "verb": "order_build",
                            "params": {"type": "X", "quantity": 11}}]
        raises_reason(lambda: P.validate_packet(d, -1), "quantity")
        d["production"] = [{"id": "p1", "verb": "order_research",
                            "params": {}}]
        raises_reason(lambda: P.validate_packet(d, -1), "tech")

    def test_tasking_verb_and_ships(self):
        d = CF.v4_packet()
        d["tasking"] = [{"id": "t1", "verb": "order_build",
                         "params": {"type": "X"}}]
        raises_reason(lambda: P.validate_packet(d, -1), "tasking verb")
        d = CF.v4_packet()
        d["tasking"] = [{"id": "t1", "verb": "order_attack", "ships": []}]
        raises_reason(lambda: P.validate_packet(d, -1), "ships")
        d["tasking"] = [{"id": "t1", "verb": "order_attack",
                         "ships": [0, -3]}]
        raises_reason(lambda: P.validate_packet(d, -1), "ships")

    def test_target_pos_tactics(self):
        d = CF.v4_packet()
        d["tasking"] = [{"id": "t1", "verb": "order_guard", "ships": [1],
                         "target": 0}]
        raises_reason(lambda: P.validate_packet(d, -1), "target")
        d = CF.v4_packet()
        d["tasking"] = [{"id": "t1", "verb": "order_move", "ships": [1],
                         "pos": [1, 2]}]
        raises_reason(lambda: P.validate_packet(d, -1), "pos")
        d = CF.v4_packet()
        d["tasking"] = [{"id": "t1", "verb": "order_tactics", "ships": [1],
                         "tactics": "furious"}]
        raises_reason(lambda: P.validate_packet(d, -1), "tactics")

    def test_item_count_caps(self):
        d = CF.v4_packet()
        d["production"] = [{"id": "a%02d" % i, "verb": "order_research",
                            "params": {"tech": "T%d" % i}}
                           for i in range(33)]
        raises_reason(lambda: P.validate_packet(d, -1), "≤ 32")
        # per-item ships cap: 1..64 ok, 65 rejected
        d = CF.v4_packet()
        many_ships = list(range(1, 66))
        d["tasking"] = [{"id": "t1", "verb": "order_halt",
                         "ships": many_ships}]
        raises_reason(lambda: P.validate_packet(d, -1), "ships")
        d = CF.v4_packet()
        d["tasking"] = [{"id": "t1", "verb": "order_halt",
                         "ships": list(range(1, 65))}]
        self.assertEqual(len(P.validate_packet(d, -1).tasking), 1)
        # list cap: 33 tasking items rejected
        d = CF.v4_packet()
        d["tasking"] = [{"id": "b%02d" % i, "verb": "order_halt",
                         "ships": [1, i]} for i in range(2, 35)]
        raises_reason(lambda: P.validate_packet(d, -1), "≤ 32")


class TestDelegate(unittest.TestCase):
    def test_no_delegate_advisory_only(self):
        prog = P.validate_packet({"version": 1}, -1)
        self.assertFalse(prog.has_envelope())

    def test_duplicate_grant_id(self):
        d = CF.v4_packet()
        d["delegate"] = [CF.v4_grant("d1"), CF.v4_grant("d1")]
        raises_reason(lambda: P.validate_packet(d, -1), "twice")

    def test_grant_id_shape(self):
        d = CF.v4_packet()
        d["delegate"] = [CF.v4_grant("1d")]     # must start with 'd'
        raises_reason(lambda: P.validate_packet(d, -1), "grant_id")

    def test_grant_verb_not_delegable(self):
        d = CF.v4_packet()
        d["delegate"] = [CF.v4_grant("d1", verbs=["order_scuttle"])]
        raises_reason(lambda: P.validate_packet(d, -1), "delegable")

    def test_grant_scope_invalid(self):
        d = CF.v4_packet()
        d["delegate"] = [CF.v4_grant("d1", ships="scope:everything")]
        raises_reason(lambda: P.validate_packet(d, -1), "scope")

    def test_grant_zone_shape(self):
        d = CF.v4_packet()
        d["delegate"] = [CF.v4_grant("d1", zone=[1, 2, 3, 0])]
        raises_reason(lambda: P.validate_packet(d, -1), "zone")
        d["delegate"] = [CF.v4_grant("d1", zone=[1, 2, 3])]
        raises_reason(lambda: P.validate_packet(d, -1), "zone")

    def test_grant_expiry(self):
        d = CF.v4_packet()
        d["delegate"] = [CF.v4_grant("d1", expires_tick=-5)]
        raises_reason(lambda: P.validate_packet(d, -1), "expires_tick")

    def test_grants_live_and_expired(self):
        # grant is live while tick <= expires_tick; blocked when tick > exp
        d = CF.v4_packet()
        d["delegate"] = [CF.v4_grant("d1", expires_tick=10),
                         CF.v4_grant("d2", ships=[5, 7])]
        prog = P.validate_packet(d, -1)
        self.assertEqual(len(prog.live_grants(5)), 2)
        self.assertEqual(len(prog.live_grants(10)), 2)
        self.assertEqual([g["grant_id"] for g in prog.live_grants(11)], ["d2"])


class TestMicroPacket(unittest.TestCase):
    def setUp(self):
        self.prog = P.validate_packet(copy.deepcopy(CF.v4_packet(7)), 6)

    def test_valid_micro_applies(self):
        applied, blocked = P.validate_micro_packet(
            {"seq": 3, "program_version": 7,
             "tasking": [{"id": "m1", "grant": "d1", "verb": "order_guard",
                          "ships": [14], "target": 2}]},
            self.prog, last_seq=2, tick=1)
        self.assertEqual(len(applied), 1)
        self.assertEqual(blocked, [])

    def test_schema_violation_rejects_whole_packet(self):
        raises_reason(lambda: P.validate_micro_packet(
            {"seq": 2, "program_version": 7, "tasking": []},
            self.prog, last_seq=2), "seq")
        raises_reason(lambda: P.validate_micro_packet(
            {"seq": 3, "program_version": 6, "tasking": []},
            self.prog, last_seq=2), "does not match")
        raises_reason(lambda: P.validate_micro_packet(
            {"seq": 3, "program_version": 7,
             "tasking": [{"id": "m1", "grant": "d1", "verb": "order_move",
                          "ships": []}]}, self.prog, last_seq=2), "ships")
        raises_reason(lambda: P.validate_micro_packet(
            {"seq": 3, "program_version": 7,
             "tasking": [{"id": "m1", "grant": "d1", "verb": "order_guard",
                          "ships": [1]}, {"id": "m1", "grant": "d1",
                                          "verb": "order_halt",
                                          "ships": [2]}]},
            self.prog, last_seq=2), "twice")

    def test_no_program_advisory_only(self):
        applied, blocked = P.validate_micro_packet(
            {"seq": 1, "program_version": 0,
             "tasking": [{"id": "m1", "grant": "d1", "verb": "order_halt",
                          "ships": [3]}]},
            None, last_seq=0, tick=1)
        self.assertEqual(applied, [])
        self.assertIn("advisory-only", blocked[0][1])

    def test_unlicensed_verb_blocked_per_item(self):
        applied, blocked = P.validate_micro_packet(
            {"seq": 1, "program_version": 7,
             "tasking": [{"id": "m1", "grant": "d1", "verb": "order_move",
                          "ships": [14], "pos": [0, 0, 0]},
                         {"id": "m2", "grant": "d1", "verb": "order_guard",
                          "ships": [15], "target": 3}]},
            self.prog, last_seq=0, tick=1)
        self.assertEqual(len(applied), 1)     # m2 licensed
        self.assertEqual(blocked[0][0], "m1")  # move not licensed by d1

    def test_scope_ships_checked_for_list_grants(self):
        d = CF.v4_packet()
        d["delegate"] = [CF.v4_grant("d1", ships=[30, 31],
                                     verbs=["order_halt"])]
        prog = P.validate_packet(d, -1)
        applied, blocked = P.validate_micro_packet(
            {"seq": 1, "program_version": 1,
             "tasking": [{"id": "m1", "grant": "d1", "verb": "order_halt",
                          "ships": [30]}]},
            prog, last_seq=0, tick=1)
        self.assertEqual(len(applied), 1)
        _, blocked = P.validate_micro_packet(
            {"seq": 2, "program_version": 1,
             "tasking": [{"id": "m2", "grant": "d1", "verb": "order_halt",
                          "ships": [99]}]},
            prog, last_seq=1, tick=1)
        self.assertIn("outside grant", blocked[0][1])

    def test_expired_grant_blocked(self):
        d = CF.v4_packet()
        d["delegate"] = [CF.v4_grant("d1", expires_tick=10)]
        prog = P.validate_packet(d, -1)
        _, blocked = P.validate_micro_packet(
            {"seq": 1, "program_version": 1,
             "tasking": [{"id": "m1", "grant": "d1", "verb": "order_halt",
                          "ships": [4]}]},
            prog, last_seq=0, tick=11)
        self.assertIn("expired", blocked[0][1])

    def test_production_requires_explicit_grant(self):
        # d1 does not license order_build → blocked item
        applied, blocked = P.validate_micro_packet(
            {"seq": 1, "program_version": 7,
             "tasking": [{"id": "m1", "grant": "d1", "verb": "order_build",
                          "params": {"type": "ResourceCollector"}}]},
            self.prog, last_seq=0, tick=1)
        self.assertEqual(applied, [])
        self.assertIn("not licensed", blocked[0][1])
        # explicit grant licenses the build
        d = CF.v4_packet()
        d["delegate"] = [CF.v4_grant("d1", verbs=["order_build"])]
        prog = P.validate_packet(d, -1)
        applied, blocked = P.validate_micro_packet(
            {"seq": 1, "program_version": 1,
             "tasking": [{"id": "m1", "grant": "d1", "verb": "order_build",
                          "params": {"type": "ResourceCollector"}}]},
            prog, last_seq=0, tick=1)
        self.assertEqual(len(applied), 1)
        self.assertEqual(blocked, [])

    def test_micro_item_cap(self):
        items = [{"id": "m%02d" % i, "grant": "d1", "verb": "order_halt",
                  "ships": [i + 1]} for i in range(9)]
        raises_reason(lambda: P.validate_micro_packet(
            {"seq": 1, "program_version": 7, "tasking": items},
            self.prog, last_seq=0), "≤ 8")


class TestPulseInput(unittest.TestCase):
    """FR-009 layout + FR-013 budget pruning (history first)."""

    def big_inputs(self):
        ledger = ["ship-%04d %s" % (i, "x" * 40) for i in range(200)]
        rollups = ["rollup-%04d %s" % (i, "x" * 40) for i in range(200)]
        return ledger, rollups

    def test_layout_static_first_and_pinned_p0(self):
        text, meta = P.build_pulse_input(
            charter_lines=["seat 0 race:kushan"],
            p0_items=[{"text": "defended_zone_breach tick=5"}],
            ledger_lines=["rc 100 idle"], rollup_lines=["r1 3 li lost"],
            prev_program_lines=["p v1"], prev_status_lines=["p1 applied"],
            micro_lines=["m seq1 ok"], reflex_lines=["harvest 100->500"])
        self.assertTrue(text.startswith("# CHARTER"))
        order = ["# CHARTER", "# P0", "# LEDGER", "# ROLLUPS",
                 "# LAST PROGRAM", "# PROGRAM STATUS", "# S1 MICRO",
                 "# REFLEXES"]
        idxs = [text.index(h) for h in order]
        self.assertEqual(idxs, sorted(idxs))
        self.assertIn("defended_zone_breach tick=5", text)
        self.assertEqual(meta["pruned"], [])
        self.assertEqual(meta["alerts"], [])

    def test_breach_prunes_history_first(self):
        ledger, rollups = self.big_inputs()
        text, meta = P.build_pulse_input(
            charter_lines=["seat 0"], p0_items=["P0 keep"],
            ledger_lines=ledger, rollup_lines=rollups,
            prev_program_lines=["prog v9 " + "y" * 500],
            prev_status_lines=["status " + "y" * 500],
            micro_lines=[], reflex_lines=[], budget_units=1500)
        self.assertIn("history", meta["pruned"])
        self.assertNotIn("prog v9", text)
        self.assertIn("P0 keep", text)          # never pruned
        self.assertTrue(meta["alerts"])

    def test_breach_sequence_then_pinned_floor(self):
        ledger, rollups = self.big_inputs()
        # tiny budget: history → rollups → ledger, then the pinned floor
        text, meta = P.build_pulse_input(
            charter_lines=["seat 0"], p0_items=["P0 keep"],
            ledger_lines=ledger, rollup_lines=rollups,
            prev_program_lines=["prog v9"], prev_status_lines=["s1"],
            micro_lines=[], reflex_lines=[], budget_units=60)
        self.assertEqual(meta["pruned"][:3], ["history", "rollups", "ledger"])
        self.assertTrue(text.startswith("# CHARTER"))
        self.assertIn("P0 keep", text)
        self.assertTrue(any("over budget" in a for a in meta["alerts"])
                        or meta["units"] <= 2 * 60)


class TestMicroInput(unittest.TestCase):
    def test_layout_and_over_budget_alert(self):
        text, meta = P.build_micro_input(
            charter_lines=["seat 0"], p0_items=["P0 x"],
            envelope_lines=["d1 verbs [order_attack]"],
            live_lines=["rc 100 under fire"],
            prev_micro_lines=["m seq1"], prev_status_lines=["m1 applied"],
            budget_units=10 ** 6)
        self.assertTrue(text.startswith("# MICRO-CHARTER"))
        self.assertIn("d1 verbs [order_attack]", text)
        self.assertEqual(meta["alerts"], [])
        _, meta2 = P.build_micro_input(
            charter_lines=["seat 0"], p0_items=["P0 x"],
            envelope_lines=["d1 ..."], live_lines=["l"], prev_micro_lines=[],
            prev_status_lines=[], budget_units=1)
        self.assertTrue(meta2["alerts"])
        self.assertIn("P0 x", text)


class TestConftestBuilders(unittest.TestCase):
    """T001 fixtures smoke: builders produce valid packets/ledgers."""

    def test_default_packet_validates(self):
        prog = P.validate_packet(copy.deepcopy(CF.v4_packet(2)), 1)
        self.assertEqual(prog.version, 2)

    def test_ledger_builder_shape(self):
        led = CF.v4_ledger(own=0, idle_collectors=2, full_collectors=1,
                           asteroids=3)
        self.assertEqual(len(led["ships"]), 3)
        self.assertEqual(len(led["asteroids"]), 3)
        self.assertEqual(led["own_player"], 0)

    def test_micro_builder_minimal(self):
        self.assertEqual(CF.v4_micro(seq=9)["seq"], 9)


if __name__ == "__main__":
    unittest.main()