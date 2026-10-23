#!/usr/bin/env python3
"""Spec 005 tests: tools/twincore/executor.py — deterministic translator
(T006/T007, FR-005, contracts/executor-contract.md §1–2).

Translation discipline: fixed order (production → tasking, packet order),
≤6 commands/tick all channels combined, one-shot retires / standing
re-issues, ProgramStatus recorded with {version, item_id} citations, zero
doctrine in the executor.
"""
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "twincore"))

import conftest as CF  # noqa: E402
import twincore.program as P  # noqa: E402
import twincore.validator as V  # noqa: E402


def make_executor(cls, ledger=None, client=None, recorder=None, **kw):
    return cls(client=client or CF.FakeClient(),
               ledger=ledger or CF.FakeLedger(
                   CF.v4_ledger(idle_collectors=1, full_collectors=1,
                                builders=1)),
               recorder=recorder, **kw)


class ExecutorBase(unittest.TestCase):
    def make_prog(self, **over):
        packet = CF.v4_packet(7, **over)
        return P.validate_packet(packet, 0)

    def wire_tools(self, client):
        return [c["tool"] for c in client.issued]


class TestTranslation(ExecutorBase):
    def setUp(self):
        import twincore.executor as E
        self.E = E

    def test_fixed_order_production_then_tasking(self):
        d = {"production": [
                {"id": "p1", "verb": "order_build",
                 "params": {"type": "ResourceCollector"}},
                {"id": "p2", "verb": "order_build",
                 "params": {"type": "LightInterceptor"}}],
             "tasking": [
                {"id": "t1", "verb": "order_guard", "ships": [100],
                 "target": 90},
                {"id": "t2", "verb": "order_halt", "ships": [101]}]}
        ex = make_executor(self.E.Executor)
        ex.set_program(self.make_prog(**d), tick=1)
        issued = ex.on_tick(1)
        self.assertEqual([i["item_id"] for i in issued],
                         ["p1", "p2", "t1", "t2"])
        self.assertEqual(self.wire_tools(ex.client),
                         ["order_build", "order_build",
                          "order_guard", "order_halt"])

    def test_cap_six_per_tick_with_carry_over(self):
        types = ["LightInterceptor", "HeavyInterceptor", "AttackBomber",
                 "LightCorvette", "MultiGunCorvette", "SalvageCorvette",
                 "RepairCorvette", "ResourceController"]   # 8 distinct fp
        d = {"production": []}
        d["production"] = [{"id": "q%d" % i, "verb": "order_build",
                            "params": {"type": t}, "once": True}
                           for i, t in enumerate(types)]
        ex = make_executor(self.E.Executor,
                           reflexes={"harvest": False, "deliver": False})
        ex.set_program(self.make_prog(**d), tick=1)
        issued = ex.on_tick(1)
        self.assertEqual(len(issued), 6)               # cap (all channels)
        self.assertEqual(ex.item_status("q6")["resolution"], "pending")
        self.assertEqual(ex.item_status("q7")["resolution"], "pending")
        issued2 = ex.on_tick(2)                        # carry-over
        self.assertEqual([i["item_id"] for i in issued2], ["q6", "q7"])

    def test_once_retires_standing_reissues(self):
        d = {"production": [{"id": "pr", "verb": "order_research",
                             "params": {"tech": "MassDrive1Kt"},
                             "once": True}],
             "tasking": [{"id": "ts", "verb": "order_guard", "ships": [100],
                          "target": 90}]}
        client = CF.FakeClient()
        led = CF.FakeLedger(CF.v4_ledger(idle_collectors=1, builders=1))
        ex = make_executor(self.E.Executor, ledger=led, client=client)
        ex.set_program(self.make_prog(**d), tick=1)
        ex.on_tick(1)
        self.assertEqual(ex.item_status("pr")["resolution"], "applied")
        self.assertEqual(ex.item_status("ts")["resolution"], "applied")
        ex.on_tick(2)
        self.assertEqual(self.wire_tools(client).count("order_research"), 1)
        self.assertEqual(ex.item_status("pr")["resolution"], "retired")

    def test_status_citations(self):
        d = {"production": [],
             "tasking": [{"id": "t1", "verb": "order_move", "ships": [100],
                          "pos": [10.0, 0.0, 0.0]}]}
        ex = make_executor(self.E.Executor)
        ex.set_program(self.make_prog(**d), tick=1)
        issued = ex.on_tick(3)
        self.assertEqual(issued[0]["program_version"], 7)
        st = ex.item_status("t1")
        self.assertEqual(st["issued_tick"], 3)
        self.assertEqual(st["command_ids"], [issued[0]["command_id"]])
        self.assertEqual(st["resolution"], "applied")

    def test_command_params_verb_shaped(self):
        d = {"production": [],
             "tasking": [
                {"id": "t1", "verb": "order_dock", "ships": [100],
                 "target": 90},
                {"id": "t2", "verb": "order_tactics", "ships": [101],
                 "tactics": "evasive"},
                {"id": "t3", "verb": "order_harvest", "ships": [101],
                 "target": 500},
                {"id": "t4", "verb": "order_move", "ships": [101],
                 "pos": [5.0, 0.0, 5.0]}]}
        client = CF.FakeClient()
        ex = make_executor(self.E.Executor, client=client)
        ex.set_program(self.make_prog(**d), tick=1)
        ex.on_tick(1)
        params = [c["params"] for c in client.issued]
        self.assertEqual(params[0].get("dockWith"), 90)
        self.assertEqual(params[1].get("tactics"), "evasive")
        self.assertEqual(params[2].get("target"), 500)
        self.assertEqual(params[3].get("target"), [5.0, 0.0, 5.0])
        for p in params:
            self.assertNotIn("once", p)
            self.assertNotIn("priority", p)
            self.assertNotIn("grant", p)

    def test_builder_discovery(self):
        led = CF.FakeLedger(CF.v4_ledger(builders=1))
        client = CF.FakeClient()
        ex = make_executor(self.E.Executor, ledger=led, client=client)
        ex.set_program(self.make_prog(
            tasking=[],
            production=[{"id": "p1", "verb": "order_build",
                         "params": {"type": "ResourceCollector"}}]), tick=1)
        ex.on_tick(1)
        self.assertEqual(client.issued[0]["params"].get("ships"), [90])

    def test_no_builder_blocks_production(self):
        led = CF.FakeLedger(CF.v4_ledger(builders=0))
        ex = make_executor(self.E.Executor, ledger=led)
        ex.set_program(self.make_prog(
            tasking=[],
            production=[{"id": "p1", "verb": "order_build",
                         "params": {"type": "ResourceCollector"}}]), tick=1)
        issued = ex.on_tick(1)
        self.assertEqual(issued, [])
        self.assertEqual(ex.item_status("p1")["resolution"], "blocked")
        self.assertIn("NO_BUILDER", ex.item_status("p1")["reason"])


class TestRejections(ExecutorBase):
    def setUp(self):
        import twincore.executor as E
        self.E = E

    def test_validator_rejection_blocks_item_for_pulse_cycle(self):
        # bank too low for the build: validator rejects → item suppressed
        # for the remainder of this program generation (FR-001-AS3)
        led = CF.FakeLedger(CF.v4_ledger(builders=1, bank=10))
        ex = make_executor(self.E.Executor, ledger=led)
        ex.set_program(self.make_prog(
            tasking=[],
            production=[{"id": "p1", "verb": "order_build",
                         "params": {"type": "MissileDestroyer"}}]), tick=1)
        issued = ex.on_tick(1)
        self.assertEqual(issued, [])
        st = ex.item_status("p1")
        self.assertEqual(st["resolution"], "blocked")
        self.assertEqual(st["reason"], "E_UNAFFORDABLE")
        ex.on_tick(2)
        self.assertEqual(ex.item_status("p1")["attempts"], 1)  # suppressed

    def test_engine_wire_error_not_suppressed(self):
        # busy labs (E_NO_LABS aux=5): retryable next tick, contract §1
        client = CF.FakeClient()
        client.fail_once("order_research", {"code": 5, "message": "busy"})
        ex = make_executor(self.E.Executor, client=client)
        ex.set_program(self.make_prog(
            production=[{"id": "pr", "verb": "order_research",
                         "params": {"tech": "MassDrive1Kt"}}], tasking=[]),
            tick=1)
        issued = ex.on_tick(1)
        # FR-023 Draft v3: issued ≠ accepted — the engine wire error marks
        # engine_denied (retryable; NOT suppressed) + a legible verdict
        self.assertEqual(issued[0]["resolution"], "engine_denied")
        self.assertEqual(issued[0]["aux"], 5)
        self.assertIn("E_NO_LABS", issued[0].get("verdict", "") or
                      ex.item_status("pr")["verdict"])
        self.assertTrue(ex.verdicts())   # verdict ring recorded
        ex.on_tick(2)
        self.assertEqual(self.wire_tools(client).count("order_research"), 2)

    def test_new_program_resets_suppression(self):
        led = CF.FakeLedger(CF.v4_ledger(builders=1, bank=10))
        ex = make_executor(self.E.Executor, ledger=led)
        ex.set_program(self.make_prog(
            tasking=[],
            production=[{"id": "p1", "verb": "order_build",
                         "params": {"type": "MissileDestroyer"}}]), tick=1)
        ex.on_tick(1)
        self.assertEqual(ex.item_status("p1")["resolution"], "blocked")
        led._state["bank"] = 5000.0
        ex.set_program(P.validate_packet(
            copy.deepcopy(CF.v4_packet(8, tasking=[],
                production=[{"id": "p1", "verb": "order_build",
                             "params": {"type": "MissileDestroyer"}}])), 7),
            tick=20)
        ex.on_tick(21)     # new generation: p1 attempts again → applies
        self.assertEqual(ex.item_status("p1")["resolution"], "applied")


class TestClampHygiene(ExecutorBase):
    """Trail hygiene: first clamp per item recorded, repeats counted,
    one summary row at generation end."""

    def setUp(self):
        import twincore.executor as E
        self.E = E

    def _rec(self):
        rows = []
        class Mem:
            def record(self, kind, tick=None, **payload):
                rows.append(dict(payload, kind=kind, tick=tick))
        return rows, Mem()

    def test_one_row_first_clamp_then_count_only(self):
        d = {"production": [],   # clamp hygiene only: no default build
             "tasking": [{"id": "ts", "verb": "order_guard", "ships": [100],
                          "target": 90}]}
        rows, mem = self._rec()
        ex = make_executor(self.E.Executor, recorder=mem,
                           reflexes={"harvest": False, "deliver": False})
        ex.set_program(self.make_prog(**d), tick=1)
        ex.on_tick(1)                     # applied (registered in-flight)
        ex.on_tick(2)                     # clamp #1 → one row
        ex.on_tick(3)                     # clamp #2 → silent
        clamped = [r for r in rows if r["kind"] == "validation" and
                   r.get("status") == "clamped"]
        self.assertEqual(len(clamped), 1)
        self.assertIn("duplicate in-flight", clamped[0]["reason"])
        self.assertEqual(ex.item_status("ts")["clamped_count"], 2)

    def test_generation_end_flushes_summary(self):
        # standing guard on the same target across versions
        d = {"tasking": [{"id": "ts", "verb": "order_guard", "ships": [100],
                          "target": 90}]}
        rows, mem = self._rec()
        led = CF.FakeLedger(CF.v4_ledger(idle_collectors=1, builders=1))
        ex = make_executor(self.E.Executor, ledger=led, recorder=mem,
                           reflexes={"harvest": False, "deliver": False})
        d["production"] = []                 # hygiene test: tasking only
        ex.set_program(self.make_prog(**d), tick=1)
        ex.on_tick(1)
        ex.on_tick(2)                     # clamp #1
        ex.set_program(P.validate_packet(
            copy.deepcopy(CF.v4_packet(8, tasking=[
                {"id": "ts", "verb": "order_guard", "ships": [100],
                 "target": 88}])), 7), tick=10)
        summary = [r for r in rows if r.get("status") == "clamped_summary"]
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["clamped"], {"ts": 1})


class TestRowsAndNoDoctrine(ExecutorBase):
    def setUp(self):
        import twincore.executor as E
        self.E = E

    def test_recorder_rows_cite_program_and_item(self):
        rows = []
        class MemRecorder:
            def record(self, kind, tick=None, **payload):
                rows.append(dict(payload, kind=kind, tick=tick))
        d = {"production": [{"id": "p1", "verb": "order_build",
                             "params": {"type": "ResourceCollector"}}],
             "tasking": [{"id": "t1", "verb": "order_guard", "ships": [100],
                          "target": 90}]}
        ex = make_executor(self.E.Executor, recorder=MemRecorder())
        ex.set_program(self.make_prog(**d), tick=5)
        ex.on_tick(5)
        kinds = [r["kind"] for r in rows]
        self.assertIn("validation", kinds)
        self.assertIn("command", kinds)
        cmd = [r for r in rows if r["kind"] == "command"
               and r["program_version"] == 7]
        self.assertTrue(cmd)                  # program-cited commands exist
        self.assertTrue(all(r["item_id"] in ("p1", "t1") for r in cmd))
        self.assertTrue(all(r.get("tick") == 5 for r in cmd))

    def test_executor_holds_no_doctrine(self):
        # FR-004: grep the module for doctrine markers — must find none
        # (word-ish boundaries: 'geneRATION' must not trip the ratio check)
        import twincore.executor as E
        import inspect
        import re
        src = inspect.getsource(E)
        for pattern in (r"build_split", r"research[_ ]chain",
                        r"attack[_ ]threshold", r"\bratio",
                        r"playbook"):
            self.assertIsNone(re.search(pattern, src, re.I),
                              "doctrine marker %r in executor" % pattern)


if __name__ == "__main__":
    unittest.main()

class TestDraftV3Executor(unittest.TestCase):
    """Decision 19 at the executor: teaching firewall (FR-020), semantic
    name denies, engine-event consumer (FR-023), clamp verdicts."""

    def setUp(self):
        import twincore.executor as E
        import twincore.program as Pm
        self.E = E
        self.P = Pm

    def make_prog(self, **over):
        packet = CF.v4_packet(7, **over)
        return self.P.validate_packet(packet, 0)

    def _ex(self, client=None, ledger=None, **kw):
        return make_executor(self.E.Executor, client=client or CF.FakeClient(),
                             ledger=ledger or CF.FakeLedger(
                                 CF.v4_ledger(builders=1)), **kw)

    def make_prog(self, **over):
        pkg = {"version": 7, "production": over.get("production", []),
               "tasking": over.get("tasking", [])}
        return P.validate_packet(copy.deepcopy(pkg), 0)

    # -- verdict store ----------------------------------------------------
    def test_verdict_repeat_marker(self):
        ex = self._ex()
        ex.add_verdict("deny", "t1", "ships outside ledger", 1)
        ex.add_verdict("deny", "t1", "ships outside ledger", 2)
        v = ex.verdicts()[0]
        self.assertEqual(v["n"], 2)
        self.assertIn("# repeat 2", v["text"])
        # a different text on the same subject is a NEW row... no: same
        # subject+kind dedupes only on identical text
        ex.add_verdict("deny", "t1", "different refusal", 3)
        self.assertEqual(len(ex.verdicts()), 2)

    def test_verdict_ring_capped(self):
        ex = self._ex()
        for i in range(40):
            ex.add_verdict("deny", "s%d" % i, "case %d" % i, 1)
        self.assertLessEqual(len(ex.verdicts(limit=100)), 24)
        self.assertEqual(len(ex.verdicts()), 6)   # feed_k default 6

    # -- semantic name denies (FR-020 pre-wire) ----------------------------
    def techtree(self):
        return {
            "labs": 1, "canResearch": False,
            "researched": ["MassDrive1Kt"],
            "locked": [
                {"type": "IonCannonFrigate", "disabled": False,
                 "missing": ["IonWeapons", "MassDrive100Kt"]},
                {"type": "HeavyCorvette", "disabled": False,
                 "missing": ["Chassis1", "MassDrive10Kt"]},
            ]}

    def test_unknown_tech_denied_with_alternatives(self):
        led = CF.FakeLedger(CF.v4_ledger(builders=1, tech_tree=self.techtree()))
        client = CF.FakeClient()
        ex = self._ex(client=client, ledger=led)
        ex.set_program(self.make_prog(
            production=[{"id": "p2", "verb": "order_research",
                         "params": {"tech": "MassDrive2Kt"}}]), tick=1)
        issued = ex.on_tick(1)
        self.assertEqual(issued, [])             # never reached the wire
        st = ex.item_status("p2")
        self.assertEqual(st["resolution"], "blocked")
        self.assertIn("unknown", st["verdict"])
        self.assertIn("IonWeapons", st["verdict"])   # a legal alternative
        vs = [v["text"] for v in ex.verdicts()]
        self.assertTrue(any("MassDrive2Kt" in t for t in vs))
        self.assertEqual(client.issued, [])      # zero engine calls

    def test_already_researched_denied(self):
        led = CF.FakeLedger(CF.v4_ledger(builders=1, tech_tree=self.techtree()))
        ex = self._ex(ledger=led)
        ex.set_program(self.make_prog(
            production=[{"id": "p1", "verb": "order_research",
                         "params": {"tech": "MassDrive1Kt"}}]), tick=1)
        ex.on_tick(1)
        st = ex.item_status("p1")
        self.assertEqual(st["resolution"], "blocked")
        self.assertIn("already researched", st["verdict"])

    def test_legal_tech_issues(self):
        led = CF.FakeLedger(CF.v4_ledger(builders=1, tech_tree=self.techtree()))
        client = CF.FakeClient()
        ex = self._ex(client=client, ledger=led)
        ex.set_program(self.make_prog(
            production=[{"id": "p1", "verb": "order_research",
                         "params": {"tech": "IonWeapons"}, "once": True}]),
            tick=1)
        issued = ex.on_tick(1)
        self.assertEqual(issued[0]["resolution"], "applied")
        self.assertEqual(len(client.issued), 1)

    def test_locked_hull_denied_with_needs(self):
        led = CF.FakeLedger(CF.v4_ledger(builders=1, tech_tree=self.techtree()))
        ex = self._ex(ledger=led)
        ex.set_program(self.make_prog(
            production=[{"id": "p1", "verb": "order_build",
                         "params": {"type": "IonCannonFrigate"}}]), tick=1)
        ex.on_tick(1)
        st = ex.item_status("p1")
        self.assertEqual(st["resolution"], "blocked")
        self.assertIn("LOCKED", st["verdict"])
        self.assertIn("IonWeapons", st["verdict"])

    def test_no_techtree_enforces_nothing(self):
        led = CF.FakeLedger(CF.v4_ledger(builders=1))   # no techTree
        client = CF.FakeClient()
        ex = self._ex(client=client, ledger=led)
        ex.set_program(self.make_prog(
            production=[{"id": "p1", "verb": "order_research",
                         "params": {"tech": "WhoKnows"}, "once": True}]),
            tick=1)
        issued = ex.on_tick(1)
        self.assertEqual(issued[0]["resolution"], "applied")

    # -- engine-event consumer (FR-023) -------------------------------------
    def test_order_rejected_marks_engine_denied_and_verdict(self):
        # wire ACCEPTS at issue time; the engine rejects it ASYNC later —
        # the event consumer must turn issued into engine_denied (FR-023)
        client = CF.FakeClient()
        led = CF.FakeLedger(CF.v4_ledger(builders=1))
        ex = self._ex(client=client, ledger=led)
        ex.set_program(self.make_prog(
            production=[{"id": "pr", "verb": "order_research",
                         "params": {"tech": "MassDrive1Kt"},
                         "once": True}]), tick=1)
        issued = ex.on_tick(1)     # wire ok; engine will reject async
        oid = issued[0]["command_id"]
        self.assertEqual(issued[0]["resolution"], "applied")  # issued ≠ accepted
        self.assertIn(oid, ex.ledger.in_flight())
        consumed = ex.on_engine_event({"type": "orderRejected", "id": oid,
                                       "aux": 8}, tick=2)
        self.assertTrue(consumed)
        st = ex.item_status("pr")
        self.assertEqual(st["resolution"], "engine_denied")
        self.assertEqual(st["aux"], 8)
        self.assertIn("E_BAD_REQUEST", st["verdict"])
        self.assertNotIn(oid, ex.ledger.in_flight())   # dedupe cleared
        self.assertTrue(any("E_BAD_REQUEST" in v["text"]
                            for v in ex.verdicts()))

    def test_order_applied_clears_inflight(self):
        led = CF.FakeLedger(CF.v4_ledger(builders=1))
        ex = self._ex(ledger=led)
        ex.set_program(self.make_prog(
            production=[{"id": "p1", "verb": "order_build",
                         "params": {"type": "ResourceCollector"}}]), tick=1)
        issued = ex.on_tick(1)
        oid = issued[0]["command_id"]
        self.assertIn(oid, ex.ledger.in_flight())
        self.assertTrue(ex.on_engine_event(
            {"type": "orderApplied", "id": oid}, tick=2))
        self.assertNotIn(oid, ex.ledger.in_flight())

    # -- clamp verdicts ------------------------------------------------------
    def test_clamp_emits_first_verdict(self):
        ex = self._ex(ledger=CF.FakeLedger(
            CF.v4_ledger(idle_collectors=1, builders=1)))
        ex.validator.cap = 1
        ex.set_program(self.make_prog(
            production=[],
            tasking=[{"id": "t1", "verb": "order_guard",
                      "ships": [100], "target": 90},
                     {"id": "t2", "verb": "order_dock",
                      "ships": [100], "target": 90}]), tick=1)
        ex.on_tick(1)
        st = ex.item_status("t2")
        self.assertEqual(st["clamped_count"], 1)
        self.assertIn("cap", st["verdict"])
        self.assertTrue(any(v["kind"] == "clamp" and v["subject"] == "t2"
                            for v in ex.verdicts()))
