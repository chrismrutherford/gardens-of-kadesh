#!/usr/bin/env python3
"""Spec 004 tests: tools/twincore/s1tick.py (T013).

S1 tick loop: static-first context anatomy (protocol §1), ≤8k budget with
breach discipline, malformed-output retry ≤2 then idle (streak recorded),
validator tri-state gating, economy proposals precede model proposals,
rejected→next-tick loop-back (FR-007), CommandRecords. Stub model channel.
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

import twincore.s1tick as S1  # noqa: E402
import twincore.state_store as S  # noqa: E402
import twincore.economy as EC  # noqa: E402
import twincore.validator as V  # noqa: E402


def ledger(ships=(), bank=9000, resources=(91,), tick=100):
    s = S.Ledger(own_player=0)
    s.rebuild({
        "tick": tick, "paused": False, "income": 10.0,
        "ships": list(ships),
        "players": [{"index": 0, "resourceUnits": bank, "alive": True}],
        "resources": [{"id": r, "owner": -1, "type": "Asteroid",
                       "pos": [0, 0, 0], "vel": [0, 0, 0], "value": 500}
                      for r in resources],
        "buildQueue": [], "research": [],
        "techTree": {"labs": 2, "canResearch": True, "locked": [],
                     "researched": []},
        "matchStatus": {"running": True, "boundPlayerIndex": 0,
                        "winner": None, "perPlayer": []},
    })
    return s


def ship(sid, owner=0, typ="LightInterceptor", pos=(0, 0, 0), order=None):
    return {"id": sid, "owner": owner, "type": typ, "pos": list(pos),
            "vel": [0, 0, 0], "health": 100.0, "healthMax": 100.0,
            "dps": 5.0, "guns": 1, "range": 4000.0, "order": order}


class FakeChannel:
    """S1 model double: returns scripted tool-call batches per call."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.usage_last = {"units": 0, "calls": 0}

    def propose(self, context, tools):
        self.calls.append({"context": context, "tools": tools})
        r = self.responses.pop(0) if self.responses else None
        return r, {"units": 700, "calls": 1}


class FakeIssuingClient:
    def __init__(self):
        self.orders = []
        self.last_tick = 100
        self.order_id_next = 1000

    def order(self, tool, params):
        self.orders.append((tool, params))
        self.order_id_next += 1
        return {"id": params.get("id"), "result": {"queued": True,
                                                   "orderId": params.get("id")}}


class Harness(unittest.TestCase):
    def setUp(self):
        self.led = ledger(ships=[ship(1, typ="PlayerMothership"),
                                 ship(2, typ="ResourceCollector"),
                                 ship(3, typ="LightInterceptor")])
        self.rec_dir = tempfile.mkdtemp(prefix="twincore-s1-")
        import twincore.recorder as R
        self.recorder = R.Recorder(match_id="m-%d" % R.now_ms())

    def loop(self, responses, economy=True):
        return S1.S1TickLoop(
            ledger=self.led,
            validator=V.Validator(),
            economy=EC.EconomyManager() if economy else None,
            channel=FakeChannel(responses),
            client=FakeIssuingClient(),
            recorder=self.recorder,
            directive_id="v1")


    def loop(self, responses=None, economy=True):
        return S1.S1TickLoop(
            ledger=self.led,
            validator=V.Validator(),
            economy=EC.EconomyManager() if economy else None,
            channel=FakeChannel(responses or []),
            client=FakeIssuingClient(),
            recorder=self.recorder,
            interval_ticks=16)


class TestContextAssembly(Harness):
    def test_static_first_sections(self):
        loop = self.loop([])
        msgs, section_titles = loop.assemble(tick=100, directive=DIR_BOOT())
        titles = [t for t, _ in msgs if t]
        self.assertEqual(titles, ["role_card", "directive", "event_window",
                                  "ledger_echo", "in_flight"])

    def test_echo_lines_bounded(self):
        ships = [ship(i) for i in range(200)]
        self.led.rebuild(snap_of(100, ships))
        loop = self.loop([])
        msgs, _ = loop.assemble(tick=100, directive=DIR_BOOT())
        echo = dict(msgs)["ledger_echo"]
        self.assertLessEqual(len(echo.split("\n")), S1.ECHO_MAX_LINES)

    def test_p0_pinne_top_of_window(self):
        loop = self.loop([])
        p0 = [{"tick": 99, "kind": "defended_zone_breach",
               "detail": {}, "outcome": "open"}]
        msgs, _ = loop.assemble(tick=100, directive=DIR_BOOT(), p0=p0)
        win = dict(msgs)["event_window"]
        self.assertEqual(win.split("\n")[0].count("zone_breach"), 1)


class TestBudget(Harness):
    def test_breach_trims_then_records(self):
        big_deltas = [{"tick": i, "kinds": ["damaged"],
                       "damaged": ["x%d" % j for j in range(50)]}
                      for i in range(50)]
        loop = self.loop([])
        text = loop.window(big_deltas)
        self.assertLessEqual(loop.usage_units(text), 1200)


class TestModelFailure(Harness):
    def test_malformed_retried_max_then_idle_tick(self):
        # 1 attempt + ≤2 retries within the tick, then the tick idles
        loop = self.loop(["bad", {"junk": 1}, "also bad",
                          {"tool_calls": []}])
        r1 = loop.run_tick(observed_tick=16)
        self.assertEqual(r1["mode"], "idle")
        self.assertEqual(r1["issued"], [])
        self.assertEqual(len(loop.channel.calls), 3)

    def test_recovery_next_tick(self):
        out = {"tool_calls": [{"verb": "order_harvest",
                               "params": {"ships": [3], "target": 91}}]}
        loop = self.loop(["bad", None, None, out])
        s = ledger(ships=[ship(3, typ="ResourceCollector")])
        loop.ledger = s
        r1 = loop.run_tick(observed_tick=16)
        self.assertEqual(r1["mode"], "idle")
        r2 = loop.run_tick(observed_tick=32)
        self.assertEqual(r2["mode"], "decide")
        self.assertEqual(len(r2["issued"]), 1)

    def test_streak_recorded(self):
        loop = self.loop([None, None])
        loop.run_tick(observed_tick=16)
        loop.run_tick(observed_tick=32)
        self.assertGreaterEqual(loop.idle_streak, 2)


class TestValidationGating(Harness):
    def test_rejected_never_issued_loops_back(self):
        # unknown verb → rejected → tool error queued for next tick;
        # economy disabled so the assertion tracks the model's proposals
        loop = self.loop([{"tool_calls":
                           [{"verb": "order_warp_party", "params": {}}]},
                          ], economy=False)
        r = loop.run_tick(observed_tick=16)
        client = loop.client
        self.assertEqual(r["issued"], [])
        # next tick input contains the rejection as a first-class error
        ctx, _ = loop.assemble(tick=32, directive=DIR_BOOT())
        in_flight = dict(ctx)["in_flight"]
        self.assertIn("order_warp_party", in_flight)

    def test_accepted_issued_in_order(self):
        out = {"tool_calls": [
            {"verb": "order_move", "params": {"ships": [3],
                                              "target": [50, 0, 0]}},
            {"verb": "order_attack", "params": {"ships": [3],
                                                "target": 2}},
        ]}
        loop = self.loop([out])
        ledger_ = ledger(ships=[ship(3), ship(2, owner=1)])
        loop.ledger = ledger_
        r = loop.run_tick(observed_tick=16, budget=10000)
        self.assertEqual([c["tool"] for c in r["issued"]],
                         ["order_move", "order_attack"])
        ids = [c["params"]["id"] for c in r["issued"]]
        self.assertEqual(len(set(ids)), 2)  # unique order ids minted

    def test_cap_shared_with_economy(self):
        # economy saturates the tick budget (≤6): model proposals dropped
        out = {"tool_calls": [
            {"verb": "order_move", "params": {"ships": [3],
                                              "target": [i, 0, 0]}}
            for i in range(6)]}
        loop = self.loop([out])
        s = ledger(ships=[ship(1, typ="PlayerMothership"),
                          ship(4, typ="ResourceCollector"),
                          ship(3, typ="LightInterceptor")])
        loop.ledger = s
        r = loop.run_tick(observed_tick=16, budget=10000)
        issued = r["issued"]
        self.assertLessEqual(len(issued), 6)


class TestRecords(Harness):
    def test_command_records_written(self):
        out = {"tool_calls": [{"verb": "order_harvest",
                               "params": {"ships": [3], "target": 91}}]}
        loop = self.loop([out])
        s = ledger(ships=[ship(3, typ="ResourceCollector")])
        loop.ledger = s
        r = loop.run_tick(observed_tick=16)
        import twincore.recorder as R
        cmds = self.recorder.scan(kinds=["command"])
        self.assertTrue(any(c["tool"] == "order_harvest" for c in cmds))
        vals = self.recorder.scan(kinds=["validation"])
        self.assertTrue(all(v["status"] in ("ok", "clamped", "rejected")
                            for v in vals))


def snap_of(tick, ships):
    return {
        "tick": tick, "paused": False, "income": 10.0,
        "ships": list(ships),
        "players": [{"index": 0, "resourceUnits": 9000, "alive": True}],
        "resources": [{"id": 91, "owner": -1, "type": "Asteroid",
                       "pos": [0, 0, 0], "vel": [0, 0, 0], "value": 500},
                      {"id": 92, "owner": -1, "type": "Asteroid",
                       "pos": [10, 0, 0], "vel": [0, 0, 0], "value": 500}],
        "buildQueue": [], "research": [],
        "techTree": {"labs": 2, "canResearch": True, "locked": [],
                     "researched": []},
        "matchStatus": {"running": True, "boundPlayerIndex": 0,
                        "winner": None, "perPlayer": []},
    }


def DIR_BOOT():
    import twincore.directive as DIR
    return DIR.boot_directive()


if __name__ == "__main__":
    unittest.main()