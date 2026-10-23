#!/usr/bin/env python3
"""Spec 004 tests: tools/twincore/driver.py (T007).

Covers: seat lifecycle state machine (protocol §8), degradation transitions
recorded, game-time clock (paused matches produce no cadence progress,
FR-003), matchOver → UNBOUND, interface-down → DEGRADED_IFACE_DOWN with
recovery. Stub client (duck-typed; no real endpoint).
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "twincore"))

import twincore.config as config  # noqa: E402
import twincore.driver as D  # noqa: E402
from twincore.s1tick import ChannelFailure  # noqa: E402


class FakeClient:
    """Session client double: drives driver states without a game."""

    def __init__(self):
        self.bound = False
        self.fail = False
        self.status_out = {"running": True, "paused": False}
        self.events_out = {"tick": 0, "events": []}
        self.snapshot_out = {"tick": 0, "paused": False, "income": 4.0,
                             "ships": [], "resources": []}
        self.calls = []
        self.last_tick = 0
        self.bound_player = None
        self._id_errors = 0
        self._reobserve = False

    def bind(self, player_index=0):
        if self.fail:
            raise D.InterfaceError("unreachable")
        self.bound = True
        self.bound_player = player_index
        self._reobserve = True
        return {"sessionId": 1, "boundPlayer": player_index}

    def status(self):
        if self.fail:
            raise D.InterfaceError("unreachable")
        return dict(self.status_out)

    def observe(self, sections=None, owner=None):
        if self.fail:
            raise D.InterfaceError("unreachable")
        self.calls.append("observe")
        self.last_tick = self.snapshot_out["tick"]
        return dict(self.snapshot_out)

    def get_events(self, after=None):
        if self.fail:
            raise D.InterfaceError("unreachable")
        self.calls.append("get_events")
        return dict(self.events_out)

    def note_id_error(self, code, tick=None):
        self._id_errors += 1

    def note_bind_reset(self):
        self._reobserve = True

    def should_reobserve(self):
        return self._reobserve

    def reobserved(self):
        self._reobserve = False


class TestClock(unittest.TestCase):
    def test_ready_and_advance(self):
        clock = D.GameClock(interval_ticks=16)
        self.assertFalse(clock.ready(0))            # no tick observed yet
        self.assertTrue(clock.ready(16, paused=False))
        self.assertTrue(clock.ready(31, paused=False))  # same boundary
        clock.advance(31)
        self.assertFalse(clock.ready(32, paused=False))  # consumed
        self.assertFalse(clock.ready(31, paused=False))  # past boundary

    def test_pause_freezes_cadence(self):
        clock = D.GameClock(interval_ticks=16)
        clock.advance(16)
        # paused match: observed ticks stop advancing → never ready again
        self.assertTrue(clock.ready(32))
        clock.advance(32)
        self.assertFalse(clock.ready(32, paused=True))
        self.assertFalse(clock.ready(100, paused=True))

    def test_renembered_tick_never_regresses(self):
        clock = D.GameClock(interval_ticks=16)
        clock.advance(100)
        self.assertFalse(clock.ready(50))  # stale observation ignored


class DriverHarness(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="twincore-drv-")
        cfg = config.load_obj({"seat": {"player_index": 0},
                               "recorder": {"dir": self.dir}})
        self.client = FakeClient()
        self.drv = D.Driver(cfg, client=self.client,
                            match_id="test-match")

    def degradation_lines(self):
        import twincore.recorder as R
        return R.scan(self.drv.recorder.match_dir, kinds=["degradation"])

    def delta_records(self):
        import twincore.recorder as R
        return R.scan(self.drv.recorder.match_dir, kinds=["delta"])

    def test_bind_transition_recorded(self):
        self.drv.bind()
        self.assertEqual(self.drv.state, "BOUND_ACTIVE")
        lines = [l for l in self.degradation_lines()
                 if l["to"] == "BOUND_ACTIVE"]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["from"], "UNBOUND")

    def test_step_consumes_events_into_recorder(self):
        self.drv.bind()
        self.client.events_out = {"tick": 130, "events": [
            {"type": "shipDestroyed", "tick": 128, "id": 7, "aux": 0,
             "owner": 1}]}
        deltas = self.drv.step()
        self.assertEqual(deltas[0]["destroyed"], {7: 0})
        rec = self.delta_records()
        self.assertEqual(len(rec), 1)
        self.assertEqual(rec[0]["destroyed"], {"7": 0})  # JSONL: int keys→str

    def test_match_over_unbinds(self):
        self.drv.bind()
        self.client.events_out = {"tick": 5000, "events": [
            {"type": "matchOver", "tick": 5000, "id": 0, "aux": 1,
             "owner": -1}]}
        self.drv.step()
        self.assertEqual(self.drv.state, "UNBOUND")
        tos = [l["to"] for l in self.degradation_lines()]
        self.assertIn("UNBOUND", tos)

    def test_iface_down_and_recovery(self):
        self.drv.bind()
        self.client.fail = True
        with self.assertRaises(D.InterfaceError):
            self.drv.step()
        self.assertEqual(self.drv.state, "DEGRADED_IFACE_DOWN")
        self.client.fail = False
        self.drv.step()
        self.assertEqual(self.drv.state, "BOUND_ACTIVE")
        tos = [l["to"] for l in self.degradation_lines()]
        self.assertIn("DEGRADED_IFACE_DOWN", tos)
        self.assertIn("BOUND_ACTIVE", tos)  # recovery transition recorded

    def test_bind_failure_leaves_unbound(self):
        self.client.fail = True
        with self.assertRaises(D.InterfaceError):
            self.drv.bind()
        self.assertEqual(self.drv.state, "UNBOUND")

    def test_reobserve_on_bind_consumed_after_step(self):
        self.drv.bind()
        self.assertTrue(self.client.should_reobserve())
        self.drv.step()
        self.assertFalse(self.client.should_reobserve())


class TestS1Wiring(unittest.TestCase):
    """M-B integration: driver drives the S1 tick loop at game-time
    cadence; S1 channel failure → DEGRADED_S1_DOWN (FR-024)."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="twincore-drv-")
        self.cfg = config.load_obj(
            {"seat": {"player_index": 0}, "recorder": {"dir": self.dir}})
        self.client = FakeClient()
        self.drv = D.Driver(self.cfg, client=self.client,
                            match_id="test-s1")
        self.ticks = []
        self.drv.s1_loop = _FakeS1Loop(self.ticks)
        self.drv.ledger = _FakeLedger()

    def test_tick_fires_on_cadence(self):
        self.drv.bind()
        self.client.snapshot_out["tick"] = 16
        self.client.last_tick = 16
        self.client.status_out = {"running": True, "paused": False}
        self.drv.step()
        self.assertEqual(self.ticks, [16])
        # same tick window again → no double-fire (clock consumed)
        self.drv.step()
        self.assertEqual(self.ticks, [16])
        self.client.snapshot_out["tick"] = 32
        self.client.last_tick = 32
        self.drv.step()
        self.assertEqual(self.ticks, [16, 32])

    def test_paused_holds_ticks(self):
        self.drv.bind()
        self.client.status_out = {"running": True, "paused": True}
        self.drv.step()
        self.assertEqual(self.ticks, [])

    def test_s1_channel_down_degrades(self):
        self.drv.bind()
        self.drv.s1_loop.fail = RuntimeError("llm down")
        self.client.snapshot_out["tick"] = 16
        self.client.last_tick = 16
        self.client.status_out = {"running": True, "paused": False}
        self.drv.step()
        self.assertEqual(self.drv.state, "DEGRADED_S1_DOWN")
        self.drv.s1_loop.fail = None          # channel recovers
        self.client.snapshot_out["tick"] = 32
        self.client.last_tick = 32
        self.drv.step()
        self.assertEqual(self.drv.state, "BOUND_ACTIVE")

    def test_idle_when_unconfigured(self):
        drv = D.Driver(self.cfg, client=FakeClient(), match_id="x")
        drv.bind()
        drv.step()   # no s1_loop wired: seat simply doesn't act (no crash)


class _FakeS1Loop:
    def __init__(self, ticks):
        self.ticks = ticks
        self.ledger = None
        self.fail = None

    def run_tick(self, observed_tick, paused=False, **kw):
        if self.fail:
            raise ChannelFailure("s1 channel failed: %s" % self.fail)
        self.ticks.append(observed_tick)
        return {"mode": "decide", "issued": []}

    def ledger_apply(self, deltas, snapshot):
        pass


class _FakeLedger:
    def rebuild(self, snap):
        pass

    def apply_snapshot(self, snap):
        pass

    def as_dict(self):
        return {"tick": 16, "own": {"ships": {}}, "bank": 0,
                "income": None, "enemy": {"by_class": {}}, "blobs": [],
                "alliances": {}, "pending": {}, "directive_version": 1,
                "last_rejections": []}


if __name__ == "__main__":
    unittest.main()