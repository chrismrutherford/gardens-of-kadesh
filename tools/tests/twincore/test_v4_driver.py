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
import twincore.program as P  # noqa: E402


import copy  # noqa: E402


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

    def propose(self, messages, tools=None):
        self.calls.append(list(messages))
        if self.delay:
            time.sleep(self.delay)
        if not self.script:
            return {"packet": None, "calls": None,
                    "error": "exhausted script"}, {}
        out, usage = self.script.pop(0)
        # Draft v3 tool lane: a scripted packet rides a submit_program call
        if isinstance(out, dict) and out.get("packet") is not None:
            return {"packet": None,
                    "calls": [{"verb": "submit_program",
                               "params": out["packet"]}],
                    "raw": {}, "error": None}, usage
        out.setdefault("calls", None)
        return out, usage


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
        # mix may include reflex gap-fillers (version None) at the same
        # tick the program landed — program-cited items are v1
        self.assertTrue(all(m["program_version"] in (1, None) for m in out))
        self.assertTrue(any(m["program_version"] == 1 for m in out))
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
        spawned = []
        orig_start = drv.loop.start_pulse
        drv.loop.start_pulse = (lambda tick, paused=False:
                                (spawned.append(tick),
                                 orig_start(tick, paused=paused))[1])
        for t in range(1, 13):               # ticks advance while lagging
            drv.on_tick(t)
            drv.loop.harvest()
        # ≤1 spawn per boundary (anchor advances AT spawn; the lagging
        # pulse only ever REDUCES the count, never storms it). Each pulse
        # may make ≤ max_repairs+1 propose attempts.
        self.assertLessEqual(len(spawned), 6)
        self.assertLessEqual(len(ch.calls), len(spawned) * 2)


if __name__ == "__main__":
    unittest.main()

class TestDraftV3Driver(unittest.TestCase):
    """Decision 19 at the driver: arc store (FR-021), mechanical rollups
    (FR-009), superset pulse input (enemy contacts/asteroid/menus), and
    the engine-event consumer wiring."""

    def setUp(self):
        import twincore.v4driver as VD
        self.VD = VD

    def techtree(self):
        return {"labs": 1, "canResearch": True,
                "researched": ["MassDrive1Kt"],
                "locked": [
                    {"type": "Carrier", "disabled": False,
                     "missing": ["MassDrive1Mt", "RepairTech"]},
                    {"type": "LightInterceptor", "disabled": False,
                     "missing": []},
                ]}

    def mk(self, **led_kw):
        led = CF.FakeLedger(CF.v4_ledger(idle_collectors=1, asteroids=3,
                                         builders=1, enemies=1, **led_kw))
        drv = self.VD.V4Driver(channel=FakeChannel([]), recorder=MemRecorder(),
                               pulse_ticks=9999, ledger=led,
                               client=CF.FakeClient())
        return drv, led

    # -- arc store (FR-021) ------------------------------------------------
    def test_arc_stored_rendered_and_clamped(self):
        drv, led = self.mk()
        msgs, meta = drv.loop.pulse_input(1)
        self.assertIn("(seeded by code", msgs[0]["content"])   # never absent
        drv.loop.set_arc("opening. econ ramp. no contact." * 20, 5)
        msgs, _ = drv.loop.pulse_input(6)
        self.assertIn("[stored at tick 5]", msgs[0]["content"])
        self.assertIn("opening. econ ramp", msgs[0]["content"])
        drv.loop.arc_chars = 40
        clamped = drv.loop.set_arc("x" * 100, 7)
        self.assertTrue(clamped)
        self.assertEqual(len(drv.loop.arc_text), 40)

    def test_apply_program_stores_arc_and_notes_verdict_on_clamp(self):
        drv, led = self.mk()
        prog = P.validate_packet(
            copy.deepcopy(CF.v4_packet(1, arc="arc " + "y" * 700)), 0)
        drv.loop.arc_chars = 100
        drv._apply_program(prog, tick=9)
        self.assertEqual(len(drv.loop.arc_text), 100)
        self.assertTrue(any(v["subject"] == "arc" and v["kind"] == "clamp"
                            for v in drv.executor.verdicts()))
        rows = drv.recorder.scan(kinds=["program"])
        self.assertTrue(any(r.get("arc") for r in rows))

    # -- rollups + superset sections (FR-009) -------------------------------
    def test_superset_sections_in_pulse_input(self):
        drv, led = self.mk(tech_tree=self.techtree(), build_queue=[
            {"builder": 90, "items": ["ResourceCollector"]}])
        msgs, meta = drv.loop.pulse_input(1)
        text = msgs[0]["content"]
        for h, frag in (("# ENEMY CONTACTS", "enemy 10 LightInterceptor"),
                        ("# ASTEROID LEDGER", "value=5000"),
                        ("# BUILD MENU", "locked: Carrier"),
                        ("# BUILD MENU", "buildQueue"),
                        ("# RESEARCH MENU", "tech MassDrive1Mt"),
                        ("# RESEARCH MENU", "researched: ['MassDrive1Kt']")):
            self.assertIn(h, text)
            self.assertIn(frag, text)

    def test_mechanical_rollups_diff_between_pulses(self):
        drv, led = self.mk()
        msgs1, _ = drv.loop.pulse_input(1)
        self.assertIn("first observation", msgs1[0]["content"])
        # world changes: new own ship, bank growth, new research
        led._ships_all[301] = {"id": 301, "type": "LightInterceptor",
                               "owner": 0, "pos": [1.0, 1.0, 1.0],
                               "order": "parade"}
        led._state["bank"] = 9000.0
        led._state["techTree"] = {"researched": ["MassDrive1Kt"],
                                  "locked": []}
        drv.loop.kills_since_pulse = 2
        msgs2, _ = drv.loop.pulse_input(2)
        text = msgs2[0]["content"]
        self.assertIn("builds materialized: +1", text)
        self.assertIn("kills (enemy lost): +2", text)
        self.assertIn("9000.0", text)
        self.assertIn("MassDrive1Kt", text)

    # -- event consumer via the driver (FR-023) ------------------------------
    def test_consume_events_counts_kills_and_rejected_orders(self):
        drv, led = self.mk()
        prog = P.validate_packet(CF.v4_packet(1), 0)
        drv._apply_program(prog, tick=1)
        issued = drv.executor.on_tick(1)
        self.assertTrue(issued)
        oid = issued[0]["command_id"]
        eaten = drv.consume_events([
            {"type": "shipDestroyed", "tick": 2, "id": 1, "aux": 1,
             "owner": 1},
            {"type": "shipDestroyed", "tick": 3, "id": 1, "aux": 1,
             "owner": 1},                       # duplicate → deduped? no
            {"type": "orderRejected", "tick": 2, "id": oid, "aux": 8,
             "owner": 0}], tick=2)
        self.assertEqual(drv.loop.kills_since_pulse, 2)
        st = drv.executor.item_status(issued[0]["item_id"])
        self.assertEqual(st["resolution"], "engine_denied")
        self.assertNotIn(oid, led.in_flight())
        self.assertTrue(any("E_BAD_REQUEST" in v["text"]
                            for v in drv.executor.verdicts()))

    # -- engine ack flow (orderApplied) --------------------------------------
    def test_order_applied_via_driver_events(self):
        drv, led = self.mk()
        prog = P.validate_packet(CF.v4_packet(1), 0)
        drv._apply_program(prog, tick=1)
        issued = drv.executor.on_tick(1)
        oid = issued[0]["command_id"]
        drv.consume_events([{"type": "orderApplied", "tick": 2,
                             "id": oid}], tick=2)
        # ack retires the oid; re-mints gate on engine state instead
        self.assertNotIn(oid, led.in_flight())
        self.assertTrue(drv.executor.item_status(
            issued[0]["item_id"])["engine_acked"])


class TestResearchMenuLabels(unittest.TestCase):
    """Live lesson: 'lab_busy=True' (canResearch mislabeled) made the
    strategist skip research every pulse. The menu must say what the lab
    CAN do right now, in plain English."""

    def setUp(self):
        import twincore.v4driver as VD
        self.VD = VD

    def test_free_lab_says_free(self):
        lines = self.VD.research_menu_lines(
            {"techTree": {"labs": 1, "canResearch": True,
                          "researched": [],
                          "locked": [{"type": "P1Fighter", "disabled":
                                      False,
                                      "missing": ["Chassis1"]}]}})
        self.assertIn("the lab is FREE", lines[0])
        self.assertNotIn("lab_busy", lines[0])
        self.assertIn("tech Chassis1", lines[1])

    def test_busy_lab_says_wait(self):
        lines = self.VD.research_menu_lines(
            {"techTree": {"labs": 1, "canResearch": False,
                          "researched": [],
                          "locked": []}})
        self.assertIn("research already in progress", lines[0])
        self.assertIn("do NOT queue another", lines[0])
