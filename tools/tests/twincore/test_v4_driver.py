#!/usr/bin/env python3
"""Spec 005 tests: tools/twincore/v4driver.py — driver assembly + S2 pulse
loop (T008/T009, FR-003/007/008/012/013).

Async two-phase pulses never block the tick loop; ≤1 program per pulse;
malformed output → ≤1 bounded repair then abort (last program stands);
whole-packet apply at the tick boundary; game-time cadence only (paused ⇒
no model calls); S2 down → last program + recorded transitions, no
procedural filler.
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "twincore"))

import conftest as CF  # noqa: E402


class MemRecorder:
    """In-memory recorder double (kinds validated like recorder.py)."""

    def __init__(self):
        import twincore.recorder as R
        self._R = R
        self.rows = []

    def record(self, kind, tick=None, **payload):
        if kind not in self._R.KINDS:
            raise ValueError("unknown recorder kind: %r" % (kind,))
        self.rows.append(dict(payload, kind=kind, tick=tick))

    def scan(self, kinds=None):
        return [dict(r) for r in self.rows
                if not kinds or r["kind"] in kinds]


class FakeChannel:
    """S2 channel double: scriptable propose() results + call counting."""

    def __init__(self, script=None):
        self.script = list(script or [])
        self.calls = []
        self.delay = 0.0

    def propose(self, messages):
        self.calls.append(list(messages))
        if self.delay:
            time.sleep(self.delay)
        if not self.script:
            return {"packet": None, "error": "exhausted script"}, {}
        return self.script.pop(0)


def valid(version=1, **over):
    return CF.v4_packet(version, **over)


def make_driver(cls, script=None, pulse_ticks=1, **kw):
    import twincore.v4driver as VD
    kw.setdefault("client", CF.FakeClient())
    return cls(channel=FakeChannel(script), recorder=MemRecorder(),
               pulse_ticks=pulse_ticks,
               ledger=CF.FakeLedger(CF.v4_ledger(idle_collectors=1,
                                                 full_collectors=1,
                                                 builders=1)),
               **kw)


class PulseBase(unittest.TestCase):
    def pump(self, drv, from_tick, to_tick, paused=False):
        out = []
        for t in range(from_tick, to_tick + 1):
            out.extend(drv.on_tick(t, paused=paused))
            time.sleep(0.01)      # let instant workers finish → deterministic
        return out


class TestAsyncTwoPhase(PulseBase):
    def setUp(self):
        import twincore.v4driver as VD
        self.VD = VD

    def test_pulse_never_blocks_and_applies_at_boundary(self):
        ch = FakeChannel([({"packet": valid(1)}, {})])
        ch.delay = 0.30                       # strategist latency
        drv = self.VD.V4Driver(channel=ch, recorder=MemRecorder(),
                               pulse_ticks=1,
                               ledger=CF.FakeLedger(
                                   CF.v4_ledger(idle_collectors=1,
                                                full_collectors=1,
                                                builders=1)),
                               client=CF.FakeClient())
        t0 = time.time()
        out = drv.on_tick(1)                  # spawn: returns immediately
        self.assertLess(time.time() - t0, 0.15)   # never blocks the tick
        # no program yet ⇒ reflexes only (FR-006); nothing program-cited
        self.assertTrue(all(m["item_id"].startswith("reflex")
                            for m in out))
        for t in range(2, 30):                # pump until applied
            out = drv.on_tick(t)
            if drv.executor.program is not None:
                break
        self.assertEqual(drv.executor.program.version, 1)
        self.assertTrue(out)                  # program v1 commands minted
        self.assertTrue(all(m["program_version"] == 1 for m in out))
        self.assertGreaterEqual(len(ch.calls), 1)
        rows = drv.recorder.scan(kinds=["program"])
        self.assertTrue(any(r.get("event") == "applied" and
                            r.get("version") == 1 for r in rows))

    def test_version_bump_whole_packet_apply(self):
        drv = make_driver(self.VD.V4Driver,
                          script=[({"packet": valid(1)}, {}),
                                  ({"packet": valid(2,
                                    production=[{"id": "p9",
                                                 "verb": "order_build",
                                                 "params": {
                                                     "type":
                                                     "LightInterceptor"}}])},
                                   {})],
                          pulse_ticks=4)
        out = self.pump(drv, 1, 12)
        self.assertEqual(drv.executor.program.version, 2)
        st = drv.executor.item_status("p9")
        self.assertEqual(st["program_version"], 2)
        rows = drv.recorder.scan(kinds=["program"])
        self.assertTrue(any(r.get("event") == "applied" and
                            r.get("version") == 2 for r in rows))
        cmds = [c for c in drv.recorder.scan(kinds=["command"])
                if c["program_version"] == 2]
        self.assertTrue(cmds)

    def test_one_program_per_pulse_boundary(self):
        ch = FakeChannel([({"packet": None, "error": "slow"}, {})])
        ch.delay = 0.2
        drv = self.VD.V4Driver(channel=ch, recorder=MemRecorder(),
                               pulse_ticks=2, ledger=CF.FakeLedger(),
                               client=CF.FakeClient())
        self.assertTrue(drv.loop.start_pulse(2))
        self.assertFalse(drv.loop.start_pulse(3))    # worker still alive
        self.assertEqual(len(ch.calls), 1)           # exactly one in flight
        self.assertFalse(drv.loop.due(2))            # anchor moved to 4
        drv.loop.harvest(timeout=5.0)


class TestMalformed(PulseBase):
    def setUp(self):
        import twincore.v4driver as VD
        self.VD = VD

    def test_one_bounded_repair_then_success(self):
        drv = make_driver(self.VD.V4Driver,
                          script=[({"packet": None, "error": "unparseable"},
                                   {}),
                                  ({"packet": valid(1)}, {})],
                          pulse_ticks=4)
        out = self.pump(drv, 1, 7)               # spawn@4 only
        self.assertEqual(len(drv.channel.calls), 2)   # original + repair
        repair_users = [m for m in drv.channel.calls[1]
                        if m.get("role") == "user"]
        self.assertIn("unparseable", str(repair_users[-1].get("content")))
        self.assertEqual(drv.executor.program.version, 1)
        self.assertTrue(out)

    def test_repair_exhausted_last_program_stands(self):
        drv = make_driver(self.VD.V4Driver,
                          script=[({"packet": valid(1)}, {}),
                                  ({"packet": None, "error": "x"}, {}),
                                  ({"packet": None, "error": "x"}, {})],
                          pulse_ticks=4)
        self.pump(drv, 1, 5)                     # pulse 1 @tick4: v1 applies
        self.assertEqual(drv.executor.program.version, 1)
        self.pump(drv, 6, 10)                    # pulse 2 @tick8: fail×2
        self.assertEqual(drv.executor.program.version, 1)   # unchanged
        rejected = drv.recorder.scan(kinds=["packet_rejected"])
        self.assertEqual(len(rejected), 1)

    def test_version_regression_rejected(self):
        drv = make_driver(self.VD.V4Driver,
                          script=[({"packet": valid(1)}, {}),
                                  ({"packet": valid(1)}, {}),
                                  ({"packet": valid(1)}, {})],
                          pulse_ticks=4)
        self.pump(drv, 1, 2)
        self.pump(drv, 3, 8)
        self.assertEqual(drv.executor.program.version, 1)


class TestCadenceAndDegradation(PulseBase):
    def setUp(self):
        import twincore.v4driver as VD
        self.VD = VD

    def test_paused_no_model_calls(self):
        ch = FakeChannel([({"packet": None, "error": "x"}, {})])
        drv = self.VD.V4Driver(channel=ch, recorder=MemRecorder(),
                               pulse_ticks=1, ledger=CF.FakeLedger())
        drv.on_tick(1, paused=True)
        drv.on_tick(2, paused=True)
        self.assertEqual(len(ch.calls), 0)
        self.assertFalse(drv.loop.due(3, paused=True))

    def test_s2_down_transition_recorded_no_filler(self):
        drv = make_driver(self.VD.V4Driver,
                          script=[({"packet": valid(1)}, {}),
                                  ({"packet": None, "error": "down"}, {})])
        self.pump(drv, 1, 10)
        transitions = drv.recorder.scan(kinds=["degradation"])
        self.assertTrue(any(t.get("to") == "s2_down" for t in transitions))
        # no procedural filler: the LAST program keeps running unchanged
        self.assertEqual(drv.executor.program.version, 1)

    def test_recovery_transitions_back_online(self):
        drv = make_driver(self.VD.V4Driver,
                          script=[({"packet": valid(1)}, {}),
                                  ({"packet": None, "error": "down"}, {}),
                                  ({"packet": None, "error": "down"}, {}),
                                  ({"packet": valid(2)}, {})],
                          pulse_ticks=4)
        self.pump(drv, 1, 20)   # p1@4 ok; p2@8 fails twice (repair pops #3); p3@12 recovers
        self.assertTrue(any(t.get("to") == "s2_down" for t in
                            drv.recorder.scan(kinds=["degradation"])))
        self.assertTrue(any(t.get("to") == "online" for t in
                            drv.recorder.scan(kinds=["degradation"])))
        self.assertEqual(drv.executor.program.version, 2)

    def test_spawn_anchor_no_storm(self):
        ch = FakeChannel([({"packet": None, "error": "down"}, {})] * 10)
        ch.delay = 0.15
        drv = self.VD.V4Driver(channel=ch, recorder=MemRecorder(),
                               pulse_ticks=2, ledger=CF.FakeLedger())
        for t in range(1, 13):               # ticks advance while lagging
            drv.on_tick(t)
            drv.loop.harvest()
        self.assertLessEqual(len(ch.calls), 6)   # ≤1 per boundary


if __name__ == "__main__":
    unittest.main()