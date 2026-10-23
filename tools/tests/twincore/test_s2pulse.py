#!/usr/bin/env python3
"""Spec 004 tests: tools/twincore/s2pulse.py (T017).

Strategist pulse loop: 30-90s game-time cadence (paused-safe), immediate
async P0 wake, ≤1 packet per pulse with ≤1 bounded repair (US3-5), input
≤24k static-first (charter first, last-M digests), questions answered next
pulse from ledger only (FR-014).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "twincore"))

import twincore.s2pulse as S2  # noqa: E402
import twincore.digest as DG  # noqa: E402
import twincore.directive as DIR  # noqa: E402
import twincore.state_store as S  # noqa: E402


class FakeS2Channel:
    responses = []      # pops per call; entries = packet dicts or "bad"

    def propose(self, messages):
        r = FakeS2Channel.responses.pop(0) if \
            FakeS2Channel.responses else "bad"
        if r == "bad":
            return {"packet": None, "error": "unparseable JSON"}, {}
        return {"packet": r}, {"prompt": 1000, "completion": 300,
                               "total": 1300}


def led(tick=100, bank=5000.0, income=12.0, enemies=()):
    s = S.Ledger(own_player=0)
    s.rebuild({
        "tick": tick, "paused": False, "income": income,
        "ships": [{"id": 1, "owner": 0, "type": "PlayerMothership",
                   "pos": [0, 0, 0], "vel": [0, 0, 0], "health": 100.0,
                   "healthMax": 160000.0, "dps": 0.0, "guns": 0,
                   "range": 0.0, "order": None}],
        "players": [{"index": 0, "resourceUnits": bank, "alive": True}],
        "resources": [], "buildQueue": [], "research": [],
        "techTree": {"labs": 2, "canResearch": True, "locked": [],
                     "researched": []},
        "matchStatus": {"running": True, "boundPlayerIndex": 0,
                        "winner": None, "perPlayer": []},
    })
    if enemies:
        for c in enemies:
            s.apply_snapshot({"tick": tick, "paused": False, "income": income,
                              "ships": [{"id": 900, "owner": 1, "type": c,
                                         "pos": [0, 0, 0], "vel": [0, 0, 0],
                                         "health": 100.0,
                                         "healthMax": 100.0, "dps": 5.0,
                                         "guns": 1, "range": 4000.0,
                                         "order": None}]})
    return s


class Harness(unittest.TestCase):
    def setUp(self):
        FakeS2Channel.responses = []
        self.led = led()
        self.sticky = DIR.StickyGate()
        self.digests = [DG.build_digest(
            pulse_id=i, tick_range=(i * 100, i * 100 + 99), p0_items=[],
            rollups=[], changes=[], order_status=[], s1_notes=[],
            ledger=self.led.as_dict()) for i in range(1, 11)]
        self.ch = FakeS2Channel()
        import twincore.recorder as R
        import tempfile
        self.rec_dir = tempfile.mkdtemp(prefix="twincore-s2-")
        self.recorder = R.Recorder(match_id="m-%d" % R.now_ms())
        self.loop = S2.S2PulseLoop(
            ledger=self.led, channel=self.ch, recorder=self.recorder,
            sticky=self.sticky, digests=self.digests,
            pulse_ticks=900, window_pulses=8, s2_in=24000, s2_out=2048)

    def test_pulse_cadence(self):
        self.assertFalse(self.loop.due(observed_tick=100, paused=False))
        self.assertTrue(self.loop.due(observed_tick=1000, paused=False))
        self.assertFalse(self.loop.due(observed_tick=5000, paused=True))

    def test_p0_wake_forces_pulse(self):
        self.loop.wake()
        self.assertTrue(self.loop.due(observed_tick=300, paused=False))

    def test_charter_first_static_prefix(self):
        FakeS2Channel.responses = [{}]
        msgs, _ = self.loop.pulse_input(observed_tick=1000)
        self.assertEqual(len(msgs), 2)               # system + user only
        self.assertIn("strategist",
                      msgs[0]["content"].lower())    # charter first
        self.assertIn("json", msgs[1]["content"].lower())

    def test_digest_window_capped_at_M(self):
        msg, _ = self.loop.pulse_input(observed_tick=1000)
        system = "".join(m["content"] for m in msg
                         if m["role"] == "system")
        self.assertEqual(system.count("interval_pulse"), 8)

    def test_questions_answered_from_ledger_only(self):
        FakeS2Channel.responses = [
            {"questions": ["income?", "enemy blobs?", "unfounded-guess?"]},
            {},
        ]
        FakeS2Channel._last_messages = []
        real_propose = self.ch.propose

        def recorder_propose(messages):
            FakeS2Channel._last_messages = messages
            return real_propose(messages)

        self.loop.channel.propose = recorder_propose
        r1 = self.loop.run_pulse(observed_tick=1000)
        self.assertEqual(sorted(r1["questions"]),
                         ["enemy blobs?", "income?", "unfounded-guess?"])
        # next pulse input carries the ledger-only answers
        r2 = self.loop.run_pulse(observed_tick=2000)
        system = "".join(m["content"] for m in FakeS2Channel._last_messages
                         if m["role"] == "system")
        self.assertIn("income", system)
        self.assertIn("no ledger source", system)


class TestPulseApply(Harness):
    def test_valid_packet_applies_at_pulse(self):
        h = Harness()
        h.setUp()
        FakeS2Channel.responses = [
            {"directive": {"patch": {"doctrine.aggression"
                                     ".overkill_factor": 1.6}},
             "orders": [], "history_op": None, "digest_config": {},
             "questions": []}]
        r = h.loop.run_pulse(observed_tick=1000)
        self.assertTrue(r["ok"])
        self.assertEqual(h.loop.directive["version"], 2)
        import twincore.recorder as R
        pk = h.loop.recorder.scan(kinds=["packet"])
        self.assertEqual(len(pk), 1)

    def test_repair_attempt_then_reject(self):
        FakeS2Channel.responses = ["bad", "bad"]   # 1 attempt + 1 repair
        r = self.loop.run_pulse(observed_tick=1000)
        self.assertFalse(r["ok"])
        import twincore.recorder as R
        rej = self.recorder.scan(kinds=["packet_rejected"])
        self.assertEqual(len(rej), 1)
        # no more attempts: 1 + ≤1 repair = 2 messages consumed
        self.assertEqual(len(FakeS2Channel.responses), 0)


if __name__ == "__main__":
    unittest.main()