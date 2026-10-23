#!/usr/bin/env python3
"""Spec 004 tests: s2pulse corroboration feed (D9/FR-017).

Repeated numeric proposals accumulate gate corroboration; string labels
never corroborate; the third pulse's pin may apply (evidence ≥2 distinct
pulses + hold).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "twincore"))

import twincore.s2pulse as S2  # noqa: E402
import twincore.directive as DIR  # noqa: E402
import twincore.state_store as SS  # noqa: E402
import twincore.recorder as R  # noqa: E402

from test_s2pulse import led  # noqa: E402


class FakeChan:
    def __init__(self, script):
        self.script = list(script)

    def propose(self, msgs):
        r = self.script.pop(0) if self.script else {}
        return {"packet": r}, {"prompt": 100, "completion": 50,
                               "total": 150}


class TestCorroboration(unittest.TestCase):
    def setUp(self):
        self.match = "test-corb-%d" % (os.getpid() * 1000 + 3)
        self.recorder = R.Recorder(self.match)
        self.loop = S2.S2PulseLoop(
            ledger=led(), channel=None, recorder=self.recorder,
            sticky=DIR.StickyGate(since_tick=0),
            pulse_ticks=10, window_pulses=8, s2_in=24000, s2_out=2048)

    def tearDown(self):
        import twincore.queue as Q
        Q.Queue(R.DEFAULT_DSN).delete_match(self.match)
        self.recorder.close()

    def _packet(self, enemy):
        return {"directive": {"patch": {"primary_enemy": enemy}},
                "orders": [], "history_op": None, "digest_config": {},
                "questions": []}

    def _swap(self, script):
        self.loop.channel = FakeChan(script)

    def test_string_labels_never_corroborate(self):
        self._swap([self._packet("enemy_mothership"),
                    self._packet("enemy_mothership")])
        self.loop.run_pulse(observed_tick=100)
        self.loop.run_pulse(observed_tick=200)
        self.assertIsNone(self.loop.directive.get("primary_enemy"))

    def test_repeated_numeric_pins_apply_after_evidence(self):
        self._swap([self._packet(1), self._packet(1), self._packet(1)])
        self.loop.run_pulse(observed_tick=600)    # corroborate
        self.loop.run_pulse(observed_tick=1200)   # corroborate (2 pulses)
        self.loop.run_pulse(observed_tick=2000)   # hold 125s ≥ 60s → apply
        self.assertEqual(self.loop.directive.get("primary_enemy"), 1)


if __name__ == "__main__":
    unittest.main()