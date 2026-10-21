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

    def test_onoff_stringified_on_wire(self):
        # regression: the engine's parseOrder reads onOff with a string getter;
        # a raw JSON boolean on the wire was rejected (E_BAD_REQUEST) submit-side
        for tool, params in (("autolaunch", {"onOff": True, "id": 21}),
                             ("autolaunch", {"onOff": False, "id": 22}),
                             ("order_alliance", {"player": 1, "onOff": True, "id": 23}),
                             ("order_kamikaze", {"ships": [1], "onOff": True, "id": 24}),
                             ("order_parade", {"ships": [1], "onOff": False, "id": 25})):
            wire = P.encode_call(tool, params)
            # no validation error + params carry the string form
            self.assertNotIn("error", wire)
            self.assertIn(wire["params"]["onOff"], ("on", "off"), tool)
        self.assertEqual(P.encode_call("autolaunch", {"onOff": True})["params"]["onOff"], "on")
        self.assertEqual(P.encode_call("autolaunch", {"onOff": False})["params"]["onOff"], "off")

    def test_build_shipless_waits_for_engine_default(self):
        # regression: build without ships must pass the protocol layer (the
        # engine defaults the builder to the bound player's Mothership)
        wire = P.encode_call("order_build", {"type": "ResourceCollector", "id": 26})
        self.assertNotIn("error", wire)
        self.assertEqual(wire["method"], "order.build")

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




class TestValidatorBranches(unittest.TestCase):
    """Branch completion for validate_snapshot/validate_events/_validate."""
    SNAP = {
        "tick": 1, "paused": False, "income": -1,
        "players": [{"index": 0, "isAI": False, "resourceUnits": 1,
                     "alive": True, "lost": False}],
        "ships": [{"id": 1, "owner": 0, "type": "Interceptor", "pos": [0, 0, 0],
                   "vel": [0, 0, 0], "health": 10, "healthMax": 10,
                   "dps": 1.0, "guns": 1, "range": 1.0, "order": None}],
        "resources": [{"id": 9, "value": 100, "pos": [0, 0, 0]}],
        "buildQueue": [], "research": [],
        "techTree": {"labs": 1, "canResearch": True, "locked": [],
                     "researched": []},
        "matchStatus": {"running": True, "boundPlayerIndex": 0,
                        "winner": None, "perPlayer": []},
    }

    def test_valid_baseline(self):
        self.assertEqual(P.validate_snapshot(self.SNAP), [])

    def test_missing_sections(self):
        bad = {k: v for k, v in self.SNAP.items() if k not in
               ("players", "ships", "resources", "buildQueue", "research")}
        errs = P.validate_snapshot(bad)
        self.assertEqual(len(errs), 5)

    def test_none_snapshot(self):
        self.assertEqual(P.validate_snapshot(None),
                         ["snapshot must be an object"])

    def test_top_level_types(self):
        bad = dict(self.SNAP, tick=None)
        self.assertEqual(P.validate_snapshot(bad), ["tick wrong type"])

    def test_player_and_ship_missing_fields(self):
        bad = dict(self.SNAP, players=[{"index": 0}],
                   ships=[{"id": 5}])
        errs = P.validate_snapshot(bad)
        self.assertEqual(len(errs), 14)

    def test_unclamped_health(self):
        ship = dict(self.SNAP["ships"][0], health=-5)
        errs = P.validate_snapshot(dict(self.SNAP, ships=[ship]))
        self.assertEqual(errs, ["ship 1 health unclamped"])

    def test_bad_order_verbs(self):
        ship = dict(self.SNAP["ships"][0], order={"verb": "warp"})
        errs = P.validate_snapshot(dict(self.SNAP, ships=[ship]))
        self.assertEqual(len(errs), 1)

    def test_techtree_and_matchstatus_missing(self):
        bad = dict(self.SNAP)
        bad["techTree"] = {"labs": 1}
        bad["matchStatus"] = {"running": True}
        errs = P.validate_snapshot(bad)
        self.assertEqual(len(errs), 6)

    def test_events_missing_shape(self):
        self.assertEqual(P.validate_events(None), ["events must be list"])
        errs = P.validate_events({"events": [{"type": "orderRejected",
                                              "tick": 1, "id": 1}]})
        self.assertEqual(len(errs), 3)   # aux+owner missing + bad aux

    def test_unknown_event_type(self):
        errs = P.validate_events({"events": [{"type": "laserShow",
                                              "tick": 1, "id": 0, "aux": 0,
                                              "owner": 0}]})
        self.assertEqual(errs, ["unknown event type 'laserShow'"])

    def test_order_rejected_aux_codes(self):
        for aux, code in P.ERROR_ENUM_TO_CODE.items():
            if aux == 0:
                continue
            errs = P.validate_events({"events": [{"type": "orderRejected",
                                                  "tick": 1, "id": 1,
                                                  "aux": aux, "owner": 0}]})
            self.assertEqual(errs, [], "aux %d -> %s" % (aux, code))


class TestValidationBranches(unittest.TestCase):
    def test_unknown_tool(self):
        tool, err = P.decode_call(P.encode_call("order_warp", {}))
        self.assertEqual(tool, "reject")

    def test_nonobject_params(self):
        self.assertIsNotNone(P._validate("order_halt", "nonsense"))

    def test_ship_types(self):
        self.assertIsNotNone(P._validate("order_halt", {"ships": ["a"]}))
        self.assertIsNotNone(P._validate("order_halt", {"ships": "x"}))
        self.assertIsNotNone(P._validate(
            "order_halt", {"ships": list(range(1, 66))}))   # 65 > 64 cap

    def test_coord_length(self):
        self.assertIsNotNone(P._validate(
            "order_move", {"ships": [1], "target": [1, 2]}))

    def test_bool_string_coercions(self):
        for v, want in (("on", "on"), ("0", "off"), (1, "on"), (True, "on")):
            self.assertEqual(P.on_off(v), want)
        self.assertEqual(P.on_off("bogus"), "bogus")

    def test_state_tools_with_params(self):
        wire = P.encode_call("observe", {"owner": 0})
        self.assertEqual(wire["method"], "state.snapshot")
        self.assertNotIn("id", wire.get("params", {}))

    def test_encode_result(self):
        self.assertEqual(P.encode_result({"x": 1}),
                         {"id": 1, "result": {"x": 1}})



class TestRemainingBranches(unittest.TestCase):
    def test_integer_and_min_branches(self):
        self.assertIn("code", P._validate("step", {"ticks": "ten"}) or {})
        self.assertIn("code", P._validate("step", {"ticks": 0}) or {})
        self.assertIn("code", P._validate("autolaunch", {"onOff": "maybe"}) or {})
        self.assertIn("code", P._validate("order_build", {"type": ""}) or {})
        self.assertIn("code", P._validate("order_build",
                                          {"type": "X", "quantity": "many"})
                      or {})
        self.assertIn("code", P._validate("order_move",
                                          {"ships": [1], "target": 5}) or {})

    def test_ships_cap_line(self):
        err = P._validate("order_halt", {"ships": list(range(1, 70))})
        self.assertIn("code", err or {})

    def test_unknown_verb_and_method(self):
        self.assertEqual(P.decode_call({"id": 1, "method": "order.warp"}),
                         ("reject", {"code": "E_BAD_REQUEST",
                                     "message": "unknown method order.warp"}))
        self.assertEqual(P.decode_call({"id": 1, "method": "universe.destroy"}),
                         ("reject", {"code": "E_BAD_REQUEST",
                                     "message": "unknown method universe.destroy"}))

    def test_order_reject_via_decode(self):
        tool, err = P.decode_call({"id": 1, "error":
                                   {"code": "E_BAD_ID", "message": "x"}})
        self.assertEqual(tool, "reject")
        self.assertEqual(err["code"], "E_BAD_ID")

    def test_dumps_helper(self):
        self.assertEqual(P.dumps({"a": 1}), '{"a": 1}')

if __name__ == "__main__":
    unittest.main(verbosity=2)
