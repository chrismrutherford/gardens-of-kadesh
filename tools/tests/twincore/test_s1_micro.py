#!/usr/bin/env python3
"""Spec 005 tests: S1 micro loop + executor micro lane (T023 micro half,
T025–T027; FR-017/FR-018, contracts/s1-envelope.md).

S1 tool calls → envelope-validated micro items → commands citing
s1:mN + program version; out-of-envelope verbs blocked with reasons
(recorded, never executed); program wins on shared ships unless the
grant carries override_tasking; paused ⇒ no calls.
"""
import copy
import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "twincore"))

import conftest as CF  # noqa: E402
import twincore.program as P  # noqa: E402

from test_v4_driver import MemRecorder  # noqa: E402


class FakeS1Channel:
    def __init__(self, script=None, delay=0.0):
        self.script = list(script or [])
        self.calls = []
        self.delay = delay

    def propose(self, messages, tools):
        self.calls.append((list(messages), [t["function"]["name"]
                                            for t in tools]))
        if self.delay:
            time.sleep(self.delay)
        calls = self.script.pop(0) if self.script else []
        return copy.deepcopy(calls), {"prompt": 10, "completion": 20,
                                      "total": 30}


def mk_loop(cls, prog=None, script=None, micro_pulse_ticks=4, **kw):
    led = CF.FakeLedger(CF.v4_ledger(idle_collectors=1, full_collectors=1,
                                     asteroids=2, builders=1))
    ex = cls.executor_class(client=CF.FakeClient(), ledger=led,
                            recorder=kw.pop("recorder", MemRecorder()))
    if prog is not None:
        ex.set_program(prog, tick=1)
    loop = cls(channel=FakeS1Channel(script), executor=ex,
               recorder=ex.recorder, ledger=led,
               micro_pulse_ticks=micro_pulse_ticks,
               **kw)
    return loop, ex


def make_env(spec_version=7):
    import twincore.executor as E
    pkg = CF.v4_packet(spec_version)
    prog = P.validate_packet(copy.deepcopy(pkg), 0)
    return E, prog


class TestS1Micro(unittest.TestCase):
    def setUp(self):
        import twincore.v4driver as VD
        self.VD = VD

    def loop(self, prog, script=None):
        class Holder:
            executor_class = None
        import twincore.executor as E
        led = CF.FakeLedger(CF.v4_ledger(idle_collectors=1,
                                         full_collectors=1, asteroids=2,
                                         builders=1))
        ex = E.Executor(client=CF.FakeClient(), ledger=led,
                        recorder=MemRecorder(),
                        reflexes={"harvest": False, "deliver": False})
        if prog is not None:
            ex.set_program(prog, tick=1)
        loop = self.VD.S1MicroLoop(channel=FakeS1Channel(script),
                                   executor=ex, recorder=ex.recorder,
                                   ledger=led, micro_pulse_ticks=4)
        return loop, ex

    def test_tool_calls_become_cited_micro_commands(self):
        prog = P.validate_packet(copy.deepcopy(CF.v4_packet(
            7, production=[], tasking=[])), 0)
        loop, ex = self.loop(prog, script=[[  # one call: guard ship 101
            {"verb": "order_guard", "params": {"ships": [100],
                                               "target": 90}}]])
        res = self._run(loop, 2)
        self.assertTrue(res["ok"])
        self.assertEqual(len(res["applied"]), 1)
        ex.feed_micro(res["applied"], None, res["seq"], 3)
        ex.on_tick(3)
        minted = [m for m in ex.client.issued]
        self.assertEqual(minted[0]["tool"], "order_guard")
        rows = ex.recorder.scan(kinds=["command"])
        self.assertEqual(rows[0]["item_id"], "s1:m1")
        self.assertEqual(rows[0]["program_version"], 7)
        self.assertEqual(rows[0]["tool"], "order_guard")

    def _run(self, loop, tick):
        msgs, meta = loop.micro_input(tick)
        return loop._call(msgs, tick)

    def test_out_of_envelope_blocked_not_executed(self):
        prog = P.validate_packet(copy.deepcopy(CF.v4_packet(
            7, production=[], tasking=[])), 0)
        # d1 licenses attack/guard/patrol — NOT halt
        loop, ex = self.loop(prog, script=[
            [{"verb": "order_halt", "params": {"ships": [102]}},
             {"verb": "order_guard", "params": {"ships": [101],
                                                "target": 90}}]])
        res = self._run(loop, 2)
        self.assertTrue(res["ok"])
        self.assertEqual([i["id"] for i in res["applied"]], ["m2"])
        self.assertEqual([b[0] for b in res["blocked"]], ["m1"])
        rows = ex.recorder.scan(kinds=["micro"])
        self.assertTrue(any(r.get("event") == "applied" and
                            r.get("n") == 1 for r in rows))
        ex.feed_micro(res["applied"], None, res["seq"], 3)
        ex.on_tick(3)
        # only the licensed order was minted
        self.assertEqual([c["tool"] for c in ex.client.issued],
                         ["order_guard"])

    def test_no_program_advisory_only(self):
        loop, ex = self.loop(None, script=[
            [{"verb": "order_guard", "params": {"ships": [101],
                                                "target": 90}}]])
        res = self._run(loop, 1)
        self.assertTrue(res["ok"])
        applied, blocked = res["applied"], res["blocked"]
        self.assertEqual(applied, [])
        self.assertIn("advisory-only", blocked[0][1])

    def test_program_wins_yield_no_override(self):
        prog = P.validate_packet(copy.deepcopy(CF.v4_packet(
            7, production=[], tasking=[
                {"id": "t1", "verb": "order_guard", "ships": [100],
                 "target": 90}])), 0)
        loop, ex = self.loop(prog, script=[
            [{"verb": "order_guard", "params": {"ships": [100],
                                                "target": 90}}]])
        res = self._run(loop, 2)
        ex.feed_micro(res["applied"], None, res["seq"], 3)
        ex.on_tick(3)
        # program item + micro both validated, but the micro lane yields:
        # only the PROGRAM's command is minted for ship 100
        tools = [c["tool"] for c in ex.client.issued]
        self.assertEqual(tools.count("order_guard"), 1)
        rows = [r for r in ex.recorder.scan(kinds=["command"])
                if r["item_id"].startswith(("s1:", "t1"))]
        self.assertEqual(rows[0]["item_id"], "t1")

    def test_override_grant_lets_s1_retask(self):
        import copy as _copy
        pkg = CF.v4_packet(7, production=[], tasking=[
            {"id": "t1", "verb": "order_guard", "ships": [100],
             "target": 90}])
        pkg["delegate"] = [CF.v4_grant("d1", verbs=["order_halt"],
                                       ships=[100], override_tasking=True)]
        prog = P.validate_packet(copy.deepcopy(pkg), 0)
        loop, ex = self.loop(prog, script=[
            [{"verb": "order_halt", "params": {"ships": [100]}}]])
        res = self._run(loop, 2)
        ex.feed_micro(res["applied"], None, res["seq"], 3)
        ex.on_tick(3)
        tools = [c["tool"] for c in ex.client.issued]
        self.assertIn("order_halt", tools)   # override flowed through

    def test_paused_no_calls(self):
        prog = P.validate_packet(copy.deepcopy(CF.v4_packet(7)), 0)
        loop, ex = self.loop(prog)
        self.assertFalse(loop.due(1, paused=True))
        self.assertFalse(loop.start_micro(1, paused=True))
        ex.recorder.scan(kinds=["usage"])

    def test_usage_row_and_cadence(self):
        prog = P.validate_packet(copy.deepcopy(CF.v4_packet(7)), 0)
        loop, ex = self.loop(prog)
        self.assertFalse(loop.due(1))           # first pulse @ cadence
        self.assertFalse(loop.due(3))
        self.assertTrue(loop.due(4))            # ≥ micro_pulse_ticks


if __name__ == "__main__":
    unittest.main()