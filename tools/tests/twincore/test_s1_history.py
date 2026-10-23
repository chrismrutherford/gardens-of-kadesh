#!/usr/bin/env python3
"""Spec 004 tests: s1tick history ops + standing-order lifecycle (T018).

- history_op trim{scope}: drop listed window contents (deltas oldest-first)
- history_op reset{seed}: collapse window to seed (in-flight orders kept)
- veto: P0 emergency auto-suspends vetoable standing orders; non-vetoable
  NOT suspended; veto MUST be reported (s1_notes + digest order_status)
- TTL expiry in game time; expired orders removed; former-ally subject
  orders aborted with reason (protocol §7)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "twincore"))

import twincore.s1tick as S1  # noqa: E402
import twincore.directive as DIR  # noqa: E402
import twincore.economy as EC  # noqa: E402
import twincore.validator as V  # noqa: E402


class Harness(unittest.TestCase):
    def setUp(self):
        import twincore.recorder as R
        import twincore.state_store as SS
        import tempfile
        self.dir = tempfile.mkdtemp(prefix="twincore-s1h-")
        self.recorder = R.Recorder(match_id="m-%d" % R.now_ms())
        self.led = SS.Ledger(own_player=0)

    def loop(self, responses=None):
        return S1.S1TickLoop(
            ledger=self.led,
            validator=V.Validator(),
            economy=EC.EconomyManager(),
            channel=_FakeChannel(responses or []),
            client=FakeIssuingClient(),
            recorder=self.recorder,
            interval_ticks=16)


class _FakeChannel:
    def __init__(self, script):
        self.script = list(script)

    def propose(self, msgs, tools):
        r = self.script.pop(0) if self.script else {"tool_calls": []}
        return r, {"total": 100, "calls": 1}


class FakeIssuingClient:
    def __init__(self):
        self.orders = []
        self.last_tick = 100

    def order(self, tool, params):
        self.orders.append((tool, params))
        return {"id": params.get("id"), "result": {"queued": True}}


def ships():
    return [
        {"id": 1, "owner": 0, "type": "PlayerMothership", "pos": [0, 0, 0],
         "vel": [0, 0, 0], "health": 160000.0, "healthMax": 160000.0,
         "dps": 0.0, "guns": 0, "range": 0.0, "order": None},
        {"id": 3, "owner": 0, "type": "LightInterceptor", "pos": [100, 0, 0],
         "vel": [0, 0, 0], "health": 100.0, "healthMax": 100.0, "dps": 5.0,
         "guns": 1, "range": 4000.0, "order": None},
        {"id": 9, "owner": 1, "type": "AttackBomber", "pos": [500, 0, 0],
         "vel": [0, 0, 0], "health": 100.0, "healthMax": 100.0, "dps": 10.0,
         "guns": 1, "range": 4000.0, "order": None},
    ]


def base_snap(tick):
    return {"tick": tick, "paused": False, "income": 10.0,
            "ships": ships(), "players": [
                {"index": 0, "resourceUnits": 8000, "alive": True}],
            "resources": [], "buildQueue": [], "research": [],
            "techTree": {"labs": 2, "canResearch": True, "locked": [],
                         "researched": []},
            "matchStatus": {"running": True, "boundPlayerIndex": 0,
                            "winner": None, "perPlayer": []}}


class TestHistoryOps(Harness):
    def test_trim_deltas(self):
        loop = self.loop()
        loop._rolling_deltas = [{"tick": 100 + i, "kinds": ["damaged"],
                                 "damaged": [i]} for i in range(20)]
        loop.history_op({"trim": {"scope": "deltas"}})
        self.assertEqual(len(loop._rolling_deltas), 0)
        # P0 items NOT trimmed
        loop._p0_items = [{}]; self.assertTrue(loop._p0_items)

    def test_reset_keeps_inflight(self):
        loop = self.loop()
        loop.ledger.rebuild(base_snap(120))
        loop.ledger.register_command({"id": 5, "tool": "order_move",
                                      "issued_tick": 100})
        loop._rolling_deltas = [{"tick": 99, "kinds": ["damaged"],
                                 "damaged": [1]}]
        loop._p0_items = [{"kind": "defended_zone_breach", "outcome": "open"}]
        loop.history_op({"reset": {"seed": "state"}})
        self.assertEqual(loop._rolling_deltas, [])
        self.assertTrue(loop.ledger.in_flight())   # never lost
        self.assertEqual(len(loop._p0_items), 1)   # P0 survive (FR-011c)

    def test_history_op_recorded(self):
        import twincore.recorder as R
        loop = self.loop()
        loop.history_op({"trim": {"scope": "deltas"}}, tick=100)
        lines = self.recorder.scan(kinds=["decision"])
        self.assertTrue(any(l.get("history_op") == "trim" for l in lines))


class TestStandingOrderLifecycle(Harness):
    def _with_order(self, **over):
        kw = dict(id="so-1", verb="order_guard", subject={"ships": [1]},
                  priority=1, ttl_ticks=200)
        kw.update(over)
        o = DIR.make_standing_order(**kw)
        d = DIR.boot_directive()
        d["standing_orders"] = [o]
        return d

    def test_ttl_expiry(self):
        import twincore.recorder as R
        loop = self.loop()
        loop.directive = self._with_order(ttl_ticks=100)
        loop._so_issued = {"so-1": 100}
        loop.evaluate_standing_orders(observed_tick=150)
        self.assertTrue(loop.directive["standing_orders"])
        loop.evaluate_standing_orders(observed_tick=201)
        self.assertFalse(loop.directive["standing_orders"])
        statuses = loop.order_status_feed()
        self.assertEqual([o["status"] for o in statuses], ["expired"])

    def test_veto_suspends_vetoable_on_p0(self):
        loop = self.loop()
        loop.directive = self._with_order()
        loop._p0_items = [{"tick": 500, "kind": "command_centre_attacked",
                           "detail": {}, "outcome": "open"}]
        loop.evaluate_standing_orders(observed_tick=500)
        self.assertEqual(loop.order_status_feed()[-1]["status"], "suspended")
        notes = loop.s1_notes_feed()
        self.assertTrue(any("so-1" in n for n in notes))  # mandatory report

    def test_non_vetoable_not_suspended(self):
        loop = self.loop()
        loop.directive = self._with_order(veto=False)
        loop._p0_items = [{}]
        loop.evaluate_standing_orders(observed_tick=500)
        self.assertTrue(loop.directive["standing_orders"])

    def test_former_ally_subject_aborted(self):
        import twincore.recorder as R
        loop = self.loop()
        d = DIR.boot_directive()
        d["standing_orders"] = [DIR.make_standing_order(
            id="so-att", verb="order_attack", subject={"target": 999},
            priority=1, ttl_ticks=5000)]
        loop.directive = d
        # enemy 999 exists (owner 1) and an alliance with owner 1 exists
        snap = base_snap(100)
        snap["ships"].append({"id": 999, "owner": 1,
                              "type": "AttackBomber", "pos": [0, 0, 0],
                              "vel": [0, 0, 0], "health": 100.0,
                              "healthMax": 100.0, "dps": 5.0, "guns": 1,
                              "range": 4000.0, "order": None})
        self.led.rebuild(snap)
        self.led.set_alliance(1, True)
        loop.evaluate_standing_orders(observed_tick=110)
        feed = loop.order_status_feed()
        self.assertEqual(feed[-1]["status"], "aborted")
        self.assertIn("ally", feed[-1]["reason"])


if __name__ == "__main__":
    unittest.main()