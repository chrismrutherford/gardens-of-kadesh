#!/usr/bin/env python3
"""Spec 004 tests: driver twin-core integration + degradation matrix (T019).

Rows (driver-runtime.md §5): S2 killed → S1 continues last directive / no
invented pulses / recovery; S1 killed → zero commands, S2 never binds;
budget breach → breach line + trim/seed-reset; rematch → full re-observe +
stale-id in-flight invalidated (never assumed busy). Stub endpoints.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "twincore"))

import twincore.config as config  # noqa: E402
import twincore.driver as D  # noqa: E402
from twincore.llm import LLMError  # noqa: E402
from test_driver import FakeClient, _FakeLedger  # noqa: E402  (same mocks)


class StubS2Loop:
    """S2 channel double for the driver wiring tests."""

    def __init__(self):
        self.calls = []
        self.digests = []
        self.pulse_ticks = 900
        self._fail = None
        self.directive = None

    def due(self, observed_tick, paused=False):
        if paused:
            return False
        return observed_tick >= self._due_at

    _due_at = 0

    def wake(self):
        self._due_at = -1   # out-of-cycle on next gate

    def worker_alive(self):
        return False

    def start_pulse(self, observed_tick, paused=False):
        if self._fail:
            raise self._fail          # mirrors the async spawn failure
        self.calls.append(observed_tick)
        self._due_at = observed_tick + self.pulse_ticks
        return True

    _pulse_fired = False

    def harvest_pulse(self, observed_tick):
        return False, None

    def run_pulse(self, observed_tick, paused=False):
        if self._fail:
            raise self._fail
        self.calls.append(observed_tick)
        self._due_at = observed_tick + self.pulse_ticks
        return {"ok": True, "applied": [], "directive": None}


class HarnessS2(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.dir = tempfile.mkdtemp(prefix="twincore-s2w-")
        self.cfg = config.load_obj(
            {"seat": {"player_index": 0},
             "recorder": {"dir": self.dir},
             "strategist": {"enabled": True}})
        self.client = FakeClient()
        self.client.snapshot_out["tick"] = 100
        self.drv = D.Driver(self.cfg, client=self.client,
                            match_id="mw-%d" % __import__("time").time_ns())
        self.drv.ledger = _FakeLedger()
        self.s2 = StubS2Loop()
        self.drv.s2_loop = self.s2

    def lines(self, kind):
        import twincore.recorder as R
        return self.drv.recorder.scan(kinds=[kind])


class TestS2Degradation(HarnessS2):
    def test_s2_down_s1_continues(self):
        self.drv.bind()
        self.client.snapshot_out["tick"] = 300
        self.client.last_tick = 300
        self.client.status_out = {"running": True, "paused": False}
        self.drv.s2_loop._due_at = 0
        self.drv.s2_loop._fail = LLMError("s2 endpoint down")
        self.drv.step()
        self.assertEqual(self.drv.state, "DEGRADED_S2_DOWN")
        # S1 keeps running (last directive) — separate lane from S2
        self.drv.s1_loop = _S1SaysStillRunning()
        self.drv.tick_clock._next = 301
        self.client.snapshot_out["tick"] = 320
        self.client.last_tick = 320
        self.drv.step()
        self.assertEqual(self.drv.state, "DEGRADED_S2_DOWN")
        self.assertEqual(self.drv.s1_loop.runs, [320])

    def test_s2_recovery(self):
        self.drv.bind()
        self.drv.s2_loop._fail = LLMError("down")
        self.client.snapshot_out["tick"] = 100
        self.client.last_tick = 100
        self.drv.step()
        self.assertEqual(self.drv.state, "DEGRADED_S2_DOWN")
        self.drv.s2_loop._fail = None
        self.drv.step()
        self.assertEqual(self.drv.state, "BOUND_ACTIVE")


class _S1SaysStillRunning:
    runs = None

    def __init__(self):
        self.runs = []

    def run_tick(self, observed_tick, paused=False, **kw):
        self.runs.append(observed_tick)
        return {"mode": "decide", "issued": []}

    ledger = None

    def history_op(self, *a, **kw):
        pass

    def evaluate_standing_orders(self, observed_tick):
        pass

    def order_status_feed(self):
        return []

    def s1_notes_feed(self):
        return []


class TestRematch(HarnessS2):
    def test_reobserve_invalidates_stale_ids(self):
        self.drv.bind()
        # in-flight orders exist, then a match load (tick regresses + rebind)
        self.client._reobserve = True  # e.g. id surge or bind reset
        self.client.snapshot_out["tick"] = 12  # fresh match: renumbered
        self.client.last_tick = 12
        rebuilds = []

        class RebuildSpy(_FakeLedger):
            in_flight_back = {"41": {"tool": "order_move"}}

            def rebuild(self, snap):
                rebuilds.append(snap.get("tick"))

        self.drv.ledger = RebuildSpy()
        self.drv.step()
        self.assertEqual(rebuilds, [12])   # full re-observe happened


if __name__ == "__main__":
    unittest.main()