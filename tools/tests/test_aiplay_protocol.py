#!/usr/bin/env python3
"""Spec 003 contract tests for tools/aiplay_protocol.py.

Covers: SC-001 (clWrap funnel coverage 1:1), SC-002 (round-trip identity),
SC-003 (schema conformance valid/invalid per verb). Constitution III:
tests land with/before the code.
"""
import copy
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import aiplay_protocol as P  # noqa: E402

CLWRAP_FUNNELS = [
    "clWrapMove", "clWrapMpHyperspace", "clWrapAttack", "clWrapFormation",
    "clWrapDock", "clWrapDeterministicBuild", "clWrapCreateShip",
    "clWrapBuildShip", "clWrapCollectResource", "clWrapProtect",
    "clWrapSpecial", "clWrapHalt", "clWrapScuttle", "clWrapAutoLaunch",
    "clWrapSetAlliance", "clWrapLaunchMultipleShips", "clWrapSetTactics",
    "clWrapRUTransfer", "clWrapSetKamikaze", "clWrapSetMilitaryParade",
    "clWrapResearch",
]

UDS_METHODS = [
    "aiSession.bind", "aiSession.status", "state.snapshot", "state.events",
    "time.pause", "time.resume", "time.step", "game.save",
]

VALID_CALLS = {
    "bind_session": {"playerIndex": 0},
    "game_status": {},
    "observe": {},
    "observe_filtered": {"sections": ["ships"], "owner": 0},  # via observe
    "get_events": {},
    "pause": {},
    "resume": {},
    "step": {"ticks": 10},
    "save_game": {"name": "test-save_1"},
    "order_move": {"ships": [1, 2], "target": [100, 200, 0], "id": 7},
    "order_attack": {"ships": [1], "target": 42, "id": 8},
    "order_guard": {"ships": [1], "target": 42, "id": 9},
    "order_dock": {"ships": [1], "dockWith": 30, "id": 10},
    "order_harvest": {"ships": [7], "target": 405, "id": 11},
    "order_special": {"ships": [1], "id": 12},
    "order_halt": {"ships": [1, 2, 3], "id": 13},
    "order_build": {"type": "ResourceCollector", "quantity": 2, "id": 14},
    "order_build_mothership": {"type": "Interceptor", "id": 15},  # ships optional
    "order_research": {"tech": "Chassis3", "id": 16},             # ships optional
    "order_formation": {"ships": [1], "formation": "delta", "id": 17},
    "order_tactics": {"ships": [1], "tactics": "evasive", "id": 18},
    "order_launch": {"ships": [1], "id": 19},
    "autolaunch": {"onOff": True, "id": 20},                      # bool coerces
    "order_scuttle": {"ships": [5], "id": 21},
    "order_kamikaze": {"ships": [1, 2], "id": 22},                # NEW
    "order_parade": {"ships": [1], "onOff": False, "id": 23},     # NEW
    "order_ru_transfer": {"to": 1, "amount": 500, "id": 24},      # NEW
    "order_alliance": {"player": 1, "onOff": True, "id": 25},     # NEW
    "order_hyperspace": {"ships": [1], "target": [10, 20, 30], "id": 26},  # NEW
}

INVALID_CALLS = [
    ("order_move", {"ships": [1]}),                       # move needs coord
    ("order_move", {"target": [1, 2, 3]}),                # move needs ships
    ("order_attack", {"ships": [1]}),                     # attack needs target
    ("order_build", {"quantity": 2}),                     # build needs type
    ("order_research", {}),                               # research needs tech
    ("order_build", {"type": "X", "quantity": 0}),        # quantity >= 1
    ("order_dock", {"ships": [1]}),                       # dock needs dockWith
    ("order_alliance", {"onOff": True}),                  # NEW needs player
    ("order_ru_transfer", {"to": 1}),                     # NEW needs amount
    ("order_hyperspace", {"ships": [1]}),                 # NEW needs coord
    ("step", {"ticks": 0}),                               # ticks >= 1
]


class TestCoverageSC001(unittest.TestCase):
    def test_all_clwrap_funnels_covered(self):
        covered = {v["funnel"] for v in P.REGISTRY["order_tools"].values()
                   if v.get("funnel")} | set(
            P.REGISTRY["funnel_overrides"])
        missing = set(CLWRAP_FUNNELS) - covered
        self.assertEqual(missing, set(),
                         "funnels not reachable via MCP: %s" % missing)

    def test_all_uds_methods_covered(self):
        covered = set(P.REGISTRY["state_tools"].values())
        self.assertEqual(set(UDS_METHODS) - covered, set())

    def test_no_invented_tools(self):
        for name, v in P.REGISTRY["order_tools"].items():
            if v.get("funnel"):
                self.assertIn(v["funnel"], CLWRAP_FUNNELS, name)


class TestRoundTripSC002(unittest.TestCase):
    def test_encode_decode_identity(self):
        alias = {"order_build_mothership": "order_build",
                 "observe_filtered": "observe"}
        for name, params in VALID_CALLS.items():
            tool = alias.get(name, name)
            wire = P.encode_call(tool, params)
            self.assertEqual(wire["id"], self._envelope_id(params),
                             "envelope id rule broken for %s" % name)
            decoded_tool, decoded_params = P.decode_call(wire)
            self.assertEqual(decoded_tool, tool, name)
            wants = copy.deepcopy(params)
            if tool.startswith("order_") or tool == "autolaunch":
                # onOff coercion must round-trip via normalized form
                if "onOff" in wants:
                    wants["onOff"] = P.on_off(wants["onOff"])
            else:
                # non-order tools drop the order-namespace id
                wants.pop("id", None)
            self.assertEqual(decoded_params, wants, "drift on %s" % name)

    def test_result_round_trip(self):
        self.assertEqual(P.decode_result(
            {"id": 1, "result": {"queued": True, "orderId": 5}}),
            ("ok", {"queued": True, "orderId": 5}))
        self.assertEqual(P.decode_result(
            {"id": 1, "error": {"code": "E_BAD_ID", "message": "order rejected"}}),
            ("error", {"code": "E_BAD_ID", "message": "order rejected"}))

    @staticmethod
    def _envelope_id(params):
        rid = params.get("id")
        return rid if isinstance(rid, (int, float)) else 1


class TestSchemaConformanceSC003(unittest.TestCase):
    def test_invalid_calls_rejected(self):
        for name, params in INVALID_CALLS:
            wire = P.encode_call(name, copy.deepcopy(params))
            got_tool, got_params = P.decode_call(wire)
            with self.subTest(tool=name, params=params):
                self.assertEqual(got_tool, "reject",
                                 "invalid %s accepted" % name)
                self.assertIn("code", got_params)

    def test_error_table_complete(self):
        want = {1: "E_NO_SESSION", 2: "E_BAD_ID", 3: "E_UNAFFORDABLE",
                4: "E_NO_BUILDER", 5: "E_NO_LABS", 6: "E_MATCH_OVER",
                7: "E_QUEUE_FULL", 8: "E_BAD_REQUEST", 9: "E_INTERNAL"}
        self.assertEqual(P.ERROR_ENUM_TO_CODE, want)
        self.assertEqual(P.ERROR_CODE_TO_ENUM,
                         {v: k for k, v in want.items()})


if __name__ == "__main__":
    unittest.main(verbosity=2)

class TestParityFixtures(unittest.TestCase):
    """US1 T011/T009: fixtures from a live 500+ object tick validate against
    the authoritative schema; raw UDS == MCP observe passthrough."""
    FDIR = os.path.join(os.path.dirname(__file__), "fixtures", "aiplay")

    def _load(self, name):
        with open(os.path.join(self.FDIR, name)) as f:
            return json.load(f)

    def _snap(self, name):
        d = self._load(name)
        return d["result"] if "result" in d else d

    def test_snapshots_validate(self):
        for name in ("snapshot_live1.json", "snapshot_owner0.json",
                     "snapshot_ships_only.json"):
            errs = P.validate_snapshot(self._snap(name))
            self.assertEqual(errs, [], "%s drift: %s" % (name, errs[:4]))

    def test_events_validate(self):
        errs = P.validate_events(self._snap("events_live.json"))
        self.assertEqual(errs, [], "event drift: %s" % errs[:4])

    def test_filter_semantics(self):
        snap = self._snap("snapshot_owner0.json")
        self.assertTrue(all(s["owner"] == 0 for s in snap["ships"]))
        only = self._snap("snapshot_ships_only.json")
        self.assertEqual(only["resources"], [])
        full = self._snap("snapshot_live1.json")
        self.assertGreater(len(full["ships"]), 40)      # 62 owned + enemies

    def test_no_raw_pointer_targetIds(self):
        # FR-013a regression guard: harvest targets are mappable ids
        snap = self._snap("snapshot_live1.json")
        bad = [s["id"] for s in snap["ships"]
               if s.get("order") and s["order"].get("targetId", 0) > 2**48]
        self.assertEqual(bad, [], "raw pointer targetIds: %s" % bad)


if __name__ == "__main__":
    unittest.main(verbosity=2)
