#!/usr/bin/env python3
"""Spec 005 tests: executor reflex floor (T011/T012, FR-006, contracts/
executor-contract.md §3) + driver run-loop/config plumbing (T009 live leg).

Reflexes are EXACTLY two, model-independent, deterministic (nearest
asteroid, euclidean; tie-break lower id), gated by the priority law
(program > S1 > reflex) and issued through the same validator lane:
  reflex_harvest: idle collector → order_harvest(nearest asteroid)
  reflex_deliver: full collector → order_dock(nearest dock-capable own)
"""
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "twincore"))

import time  # noqa: E402

import conftest as CF  # noqa: E402
import twincore.program as P  # noqa: E402
import twincore.v4driver as VD  # noqa: E402

from test_executor import make_executor, ExecutorBase  # noqa: E402
from test_v4_driver import MemRecorder  # noqa: E402


def make_reflex_exec(cls, idle=2, full=0, asts=3, builders=1, **kw):
    led = CF.FakeLedger(CF.v4_ledger(idle_collectors=idle,
                                     full_collectors=full, asteroids=asts,
                                     builders=builders))
    return make_executor(cls, ledger=led, **kw)


class TestReflexes(ExecutorBase):
    def setUp(self):
        import twincore.executor as E
        self.E = E

    def test_idle_collectors_harvest_nearest_asteroid(self):
        ex = make_reflex_exec(self.E.Executor, idle=2, asts=3)
        issued = ex.on_tick(1)            # no program: reflexes only
        r = [m for m in issued if m["item_id"] == "reflex_harvest"]
        self.assertEqual(len(r), 2)       # both idle collectors dispatched
        self.assertEqual([i["verb"] for i in r], ["order_harvest"] * 2)
        # nearest asteroid to pos 0: id 500 (dx 0); the second collector
        # sits at pos 10 → still 500 (dx -100 < 200 to 501)
        targets = sorted(i["params"]["target"] for i in r)
        self.assertEqual(targets, [500, 500])
        self.assertEqual([i["params"]["ships"] for i in r],
                         [[100], [101]])

    def test_tie_break_by_resource_id(self):
        # both asteroids equidistant → lower id wins (determinism, IV)
        led = CF.FakeLedger(CF.v4_ledger(idle_collectors=1, asteroids=0))
        led._resources[700] = {"id": 700, "pos": [10.0, 0.0, 0.0], "ru": 1.0}
        led._resources[699] = {"id": 699, "pos": [10.0, 0.0, 0.0], "ru": 1.0}
        ex = make_executor(self.E.Executor, ledger=led)
        issued = ex.on_tick(1)
        r = [m for m in issued if m["item_id"] == "reflex_harvest"]
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["params"]["target"], 699)

    def test_program_wins_no_reflex_on_covered_ships(self):
        ex = make_reflex_exec(self.E.Executor, idle=2, asts=3)
        ex.set_program(self.make_prog(
            tasking=[{"id": "t1", "verb": "order_guard", "ships": [100],
                      "target": 90}]), tick=1)
        issued = ex.on_tick(1)
        reflexes = [m for m in issued if m["item_id"].startswith("reflex")]
        # 100 is covered by t1; 101 is idle and uncovered → reflex for it
        self.assertEqual(len(reflexes), 1)
        self.assertEqual(reflexes[0]["params"]["ships"], [101])

    def test_full_collector_docks_nearest_dock_capable(self):
        ex = make_reflex_exec(self.E.Executor, idle=0, full=1, builders=1)
        issued = ex.on_tick(1)
        r = [m for m in issued if m["item_id"] == "reflex_deliver"]
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["verb"], "order_dock")
        self.assertEqual(r[0]["params"]["dockWith"], 90)
        self.assertEqual(r[0]["params"]["ships"], [100])

    def test_reflex_toggle_off(self):
        ex = make_reflex_exec(self.E.Executor, idle=2, asts=3,
                              reflexes={"harvest": False, "deliver": True})
        issued = ex.on_tick(1)
        self.assertEqual([m for m in issued if
                          m["item_id"] == "reflex_harvest"], [])

    def test_reflex_commands_pass_validator_lane(self):
        rows = []
        class Mem:
            def record(self, kind, tick=None, **payload):
                rows.append(dict(payload, kind=kind, tick=tick))
        ex = make_reflex_exec(self.E.Executor, idle=1, asts=3,
                              recorder=Mem())
        ex.on_tick(1)
        cmd = [r for r in rows if r["kind"] == "command"]
        self.assertTrue(cmd)
        self.assertTrue(all(c["item_id"] == "reflex_harvest" for c in cmd))
        self.assertTrue(all(c["validation"]["status"] == "ok" for c in cmd))

    def test_harvesting_collectors_not_redispatched(self):
        # collector with order {"verb": "harvest"} → not idle
        led = CF.FakeLedger(CF.v4_ledger(idle_collectors=1, asteroids=3))
        led._ships_all[100]["order"] = {"verb": "harvest"}
        ex = make_executor(self.E.Executor, ledger=led)
        issued = ex.on_tick(1)
        self.assertEqual([m for m in issued if
                          m["item_id"] == "reflex_harvest"], [])


class TestS2DisconnectedAtBoot(ExecutorBase):
    """US2 independent test (driver level): no program ever arrives; the
    seat keeps collectors cycling from observation state alone."""

    def setUp(self):
        import twincore.v4driver as VD
        self.VD = VD

    def test_reflexes_only_economy_alive(self):
        ch = FakeChannelAlwaysDown()
        drv = self.VD.V4Driver(
            channel=ch, recorder=MemRecorder(), pulse_ticks=2,
            ledger=CF.FakeLedger(CF.v4_ledger(idle_collectors=2,
                                              asteroids=2, builders=1)),
            client=CF.FakeClient())
        out = []
        for t in range(1, 9):
            out.extend(drv.on_tick(t))
            time.sleep(0.01)
        harvests = [m for m in out if m["item_id"] == "reflex_harvest"]
        self.assertTrue(harvests)         # economy alive
        self.assertEqual(drv.executor.program, None)   # nothing invented
        # every idle collector got a harvest; dedupe kept it sane
        self.assertLessEqual(len(harvests), 4)
        trans = drv.recorder.scan(kinds=["degradation"])
        self.assertTrue(any(t2.get("to") == "s2_down" for t2 in trans))


class FakeChannelAlwaysDown:
    def propose(self, messages):
        return {"packet": None, "error": "llm endpoint unreachable"}, {}


# run-loop + config plumbing (T009 live leg) --------------------------------

class TestRunloopAndConfig(ExecutorBase):
    def setUp(self):
        import twincore.v4driver as VD
        self.VD = VD

    def test_observe_updates_ledger(self):
        drv = self.VD.V4Driver(
            channel=FakeChannelAlwaysDown(), recorder=MemRecorder(),
            pulse_ticks=999,
            ledger=CF.FakeLedger(CF.v4_ledger(builders=1)),
            client=_ObservingClient())
        drv.observe()
        self.assertEqual(drv.executor.ledger.as_dict()["bank"], 4000)

    def test_run_loop_pulses_and_acts(self):
        drv = self.VD.V4Driver(
            channel=FakeChannelAlwaysDown(), recorder=MemRecorder(),
            pulse_ticks=2,
            ledger=CF.FakeLedger(CF.v4_ledger(idle_collectors=1,
                                              asteroids=2, builders=1)),
            client=_ObservingClient())
        drv.run(max_polls=4, poll_seconds=0.01)
        self.assertTrue(drv.client.issued)   # reflex orders flowed by tick

    def test_driver_from_config(self):
        cfg = C_config_minimal()
        drv = self.VD.driver_from_config(cfg, recorder=MemRecorder())
        self.assertEqual(drv.own_player, 0)
        self.assertEqual(drv.loop.pulse_ticks, 120 * 16)   # 120 s → ticks
        self.assertEqual(drv.ledger.own_player, 0)


def C_config_minimal():
    return {
        "seat": {"player_index": 0},
        "transport": {"mcp_url": "http://127.0.0.1:8302/mcp"},
        "channels": {"s2": {"url": "http://x/v1/chat/completions",
                            "model": "m", "max_output": 3072,
                            "timeout_seconds": 300, "thinking": False}},
        "recorder": {"dsn": {"host": "h", "dbname": "d", "user": "u"}},
        "v4": {"pulse_seconds": 120, "budgets": {"s2_in": 12288,
                                                 "s2_out": 3072},
               "reflexes": {"harvest": True, "deliver": True},
               "s1_channel": {"enabled": False}},
    }


class _ObservingClient(CF.FakeClient):
    """observe() returns a deterministic snapshot with an advancing tick."""

    def __init__(self):
        super().__init__()
        self._tick = 0

    def observe(self, sections=None, owner=None):
        self._tick += 1
        t = self._tick
        return {
            "tick": t * 16, "timeElapsed": t, "paused": False,
            "matchStatus": {"running": True, "tick": t * 16},
            "players": [{"index": 0, "resourceUnits": 4000}],
            "ships": [
                {"id": 100, "type": "ResourceCollector", "owner": 0,
                 "pos": [0.0, 0.0, 0.0], "cargo": 0, "cargo_max": 100,
                 "order": {"verb": "parade"}},
                {"id": 90, "type": "Mothership", "owner": 0,
                 "pos": [5.0, 0.0, 0.0]},
            ],
            "resources": [
                {"id": 500, "pos": [10.0, 0.0, 0.0], "ru": 9000.0},
                {"id": 501, "pos": [40.0, 0.0, 0.0], "ru": 9000.0},
            ],
        }


if __name__ == "__main__":
    unittest.main()