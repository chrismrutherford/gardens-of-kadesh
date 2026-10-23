#!/usr/bin/env python3
"""Spec 004 tests: driver digest feed + P0 wake routing (M-C completion).

Events → classification lines; P0 → s2 wake (out-of-cycle pulse same
step); digest built per pulse with p0 verbatim + rollups.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "twincore"))

import twincore.driver as D  # noqa: E402
from twincore.llm import LLMError  # noqa: E402
from test_driver import FakeClient, _FakeLedger  # noqa: E402
from test_degradation import StubS2Loop  # noqa: E402


class HarnessW(unittest.TestCase):
    def setUp(self):
        import tempfile
        import twincore.config as config
        self.dir = tempfile.mkdtemp(prefix="twincore-w-")
        cfg = config.load_obj({"seat": {"player_index": 0},
                               "recorder": {"dir": self.dir},
                               "strategist": {"enabled": True}})
        self.client = FakeClient()
        self.drv = D.Driver(cfg, client=self.client, match_id="w-%d" % __import__("time").time_ns())
        self.drv.ledger = _FakeWLedger()
        self.s2 = StubS2Loop()
        self.drv.s2_loop = self.s2
        self.drv.bind()

    def scan(self, kind):
        import twincore.recorder as R
        return self.drv.recorder.scan(kinds=[kind])


class _FakeWLedger(_FakeLedger):
    def apply_delta(self, *a, **kw):
        pass

    def last_rejections(self):
        return []

    _s = {"last_rejections": []}

    def as_dict(self):
        base = dict(_FakeLedger.as_dict(self))
        base["own"] = {"ships": {1: {"id": 1, "type": "PlayerMothership",
                                     "pos": [0, 0, 0], "health": 100.0,
                                     "dps": 0.0}}}
        base["command_centre"] = 1
        base["own_destroyed_recent"] = [11, 12, 13, 14]
        return base


class TestP0Wake(HarnessW):
    def test_p0_event_wakes_strategist_out_of_cycle(self):
        self.client.status_out = {"running": True, "paused": False}
        # battle group annihilated: 4 own ships destroyed in one delta
        self.client.events_out = {"tick": 500, "events": [
            {"type": "shipDestroyed", "tick": 500, "id": sid, "aux": 0,
             "owner": 0} for sid in (11, 12, 13, 14)]}
        self.client.snapshot_out["tick"] = 500
        self.client.last_tick = 500
        self.drv.step()
        cls = self.scan("classification")
        self.assertTrue(any(c["class"] == "P0" and
                            c["rule"] == "battle_group_annihilated"
                            for c in cls))
        # out-of-cycle pulse happened the SAME step (wake forced)
        self.assertEqual(self.s2.calls, [500])

    def test_digest_recorded_with_p0_verbatim(self):
        self.test_p0_event_wakes_strategist_out_of_cycle()
        digests = self.scan("digest")
        self.assertTrue(digests)
        dig = digests[0]
        kinds = [p.get("kind") for p in dig["p0"]]
        self.assertIn("battle_group_annihilated", kinds)


if __name__ == "__main__":
    unittest.main()