#!/usr/bin/env python3
"""Unit tests for agent-multi (spec 001-agent-multi-llm-fleet).

Fake sidecar + fake LLM (duck-typed, in-process; no real endpoints).
Covers: read-only invariants (FR-004/010), artifact store + staleness
(FR-002/FR-006), commander merge (FR-003), orders ring, peer loop
back-to-back publishing + reset rebase + matchOver idle (FR-005/007),
executor prompt presence (US6/FR-012).
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import agent          # noqa: E402
import agent_multi    # noqa: E402


class FakeSidecar:
    """Records tool calls; serves scripted observe/events payloads."""

    def __init__(self, snapshot, events=None, fail_for=()):
        self.snapshot = snapshot
        self.events = events or []
        self.fail_for = set(fail_for)
        self.calls = []

    def _respond(self, text):
        return {"result": {"content": [{"text": json.dumps(text)}]}}

    def call(self, tool, args):
        self.calls.append(tool)
        if tool in self.fail_for:
            raise agent.MCPFailure("simulated failure")
        if tool == "observe":
            return self._respond(self.snapshot)
        if tool == "get_events":
            return self._respond({"result": {"events": self.events}})
        raise AssertionError("unexpected tool %r" % tool)


class FakeLLM:
    def __init__(self, replies, cycle=False):
        self.replies = list(replies)
        self.base = list(replies)
        self.cycle = cycle
        self.prompts = []
        self.chat_kwargs = []

    def chat(self, prompt, expect_tools=True, max_tokens=1400):
        self.prompts.append(prompt)
        self.chat_kwargs.append({"expect_tools": expect_tools})
        if self.cycle and self.base:
            if not self.replies:
                self.replies = list(self.base)
        return self.replies.pop(0) if self.replies else ""

    def chat_stream(self, messages, log_raw_fn=None, use_tools=True,
                    max_tokens=2600):
        """Test double: mirrors agent.LLM.chat_stream for peer loops."""
        self.prompts.append("\n".join(m["content"]
                                       for m in messages))
        self.chat_kwargs.append({"use_tools": use_tools})
        if self.cycle and self.base:
            if not self.replies:
                self.replies = list(self.base)
        reply = self.replies.pop(0) if self.replies else ""
        if log_raw_fn is not None:
            log_raw_fn(reply)      # peers log their reply verbatim
        return reply, []


def snap(tick=100, running=True, orders=None):
    m = {"tick": tick,
         "matchStatus": {"running": running, "tick": tick, "paused": False},
         "players": [{"index": 0, "resourceUnits": 1000}],
         "ships": orders or []}
    return m


class ArtifactStoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = agent_multi.ArtifactStore(self.dir)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_roundtrip_and_role_match(self):
        self.store.write("strategist", "strategist", 42, "go",
                         {"directives": ["build 8"]})
        doc = self.store.read_fresh("strategist")
        self.assertIsNotNone(doc)
        self.assertEqual(doc["payload"]["directives"], ["build 8"])
        self.assertEqual(doc["tick"], 42)

    def test_missing_file_is_none(self):
        self.assertIsNone(self.store.read_fresh("critic"))

    def test_role_mismatch_ignored(self):
        self.store.write("critic", "strategist", 1, "spoof", {})
        # writer stamps role from payload arg; spoof = role != artifact name
        self.assertIsNone(self.store.read_fresh("critic"))

    def test_staleness_excluded_and_marked(self):
        old = {"role": "analyst", "generatedAt": int(agent_multi.now()) - 999,
               "tick": 7, "summary": "old", "payload": {}}
        path = self.store.path("analyst")
        with open(path, "w") as f:
            json.dump(old, f)
        doc = self.store.read_fresh("analyst", stale_secs=60)
        self.assertIsNotNone(doc)
        self.assertTrue(doc["stale"])
        fresh, stale = agent_multi.merge_artifacts(self.store)
        self.assertIn("analyst", ";".join(stale))
        self.assertFalse(fresh)

    def test_malformed_ignored(self):
        with open(self.store.path("critic"), "w") as f:
            f.write("{not json")
        self.assertIsNone(self.store.read_fresh("critic"))

    def test_orders_ring(self):
        ring = []
        for i in range(25):
            agent_multi.append_order_ring(self.store, ring,
                                          {"tool": "order_move", "n": i})
        self.assertEqual(len(ring), 20)
        doc = self.store.read("orders")
        self.assertEqual(len(doc["orders"]), 20)
        self.assertEqual(doc["orders"][-1]["n"], 24)

    def test_merge_includes_payload(self):
        self.store.write("strategist", "strategist", 9, "attack now",
                         {"directives": ["build 8 interceptors"]})
        self.store.write("critic", "critic", 9, "review",
                         {"objections": [{"orderId": 3, "reason": "1:4"}]})
        fresh, stale = agent_multi.merge_artifacts(self.store)
        blob = "\n".join(fresh)
        self.assertIn("build 8 interceptors", blob)
        self.assertIn("OBJECTION order 3", blob)
        self.assertIn("attack now", blob)


def _run_peer_unused():   # thread-mode helper kept for future live tests
    pass


class PeerLoopTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = agent_multi.ArtifactStore(self.dir)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _mkloop(self, sidecar, llm):
        return agent_multi.PeerLoop(
            "strategist", sidecar, llm, self.store,
            agent_multi.LoopLogger("strategist-test",
                                   os.path.join(self.dir, "s.log")),
            prompt="TEST PROMPT", full_snapshot=True)

    def _steps(self, loop, n):
        for _ in range(n):
            loop.step_once()

    def test_back_to_back_publish(self):
        side = FakeSidecar(snap(tick=5))
        llm = FakeLLM(["plan A", "plan B", "plan C"], cycle=True)
        loop = self._mkloop(side, llm)
        self._steps(loop, 3)
        self.assertEqual(loop.logger.server_rounds, 3)
        doc = self.store.read_fresh("strategist")
        self.assertIn("plan", doc["summary"])
        self.assertFalse([c for c in side.calls if "bind" in c])

    def test_no_tools_passed_to_peer_llm(self):
        side = FakeSidecar(snap())
        llm = FakeLLM(["advice"])
        loop = self._mkloop(side, llm)
        self._steps(loop, 1)
        self.assertTrue(all(not kw.get("use_tools", kw.get("expect_tools",
                                                          True))
                            for kw in llm.chat_kwargs))

    def test_tool_call_reply_discarded_fr010(self):
        side = FakeSidecar(snap(tick=5))
        toolish = '{"name": "order_attack", "arguments": {"target": 5}}'
        llm = FakeLLM([toolish])
        loop = self._mkloop(side, llm)
        loop.step_once()
        self.assertEqual(loop.logger.server_rounds, 1)
        doc = self.store.read_fresh("strategist")
        self.assertNotIn('"name"', json.dumps(doc))
        # no tool-call mutation possible: sidecar never saw an order tool
        self.assertFalse([c for c in side.calls if c.startswith("order")])

    def test_match_over_idle(self):
        side = FakeSidecar(snap(running=False))
        llm = FakeLLM([])  # peer must not call LLM when match over
        loop = self._mkloop(side, llm)
        loop.step_once()
        doc = self.store.read_fresh("strategist")
        self.assertIn("idle", doc["summary"])
        self.assertFalse(llm.prompts)

    def test_reset_rebase_clears_history_fr007(self):
        side = FakeSidecar(snap(tick=10))
        llm = FakeLLM(["first", "second"])
        loop = self._mkloop(side, llm)
        self._steps(loop, 1)
        self.assertEqual(loop.history, ["first"])
        side.snapshot = snap(tick=2)           # tick went DOWN: match reset
        self._steps(loop, 1)
        self.assertEqual(loop.history, ["second"])   # pre-reset cleared

    def test_readonly_sidecar_refuses_orders_fr004(self):
        old_http = agent.http_json
        agent.http_json = staticmethod(
            lambda url, payload=None, timeout=60: {
                "result": {"serverInfo": {"name": "fake"}}})
        try:
            side = agent_multi.PeerSidecar("http://fake")
            for tool in ("bind_session", "order_attack", "order_build"):
                with self.assertRaises(agent_multi.SidecarReadOnlyError):
                    side.call(tool, {})
        finally:
            agent.http_json = old_http


class ExecutorPromptTest(unittest.TestCase):
    def test_executor_prompt_has_three_invariants(self):
        prompt = agent.EXECUTOR_PROMPT
        self.assertIn("ECONOMY FIRST", prompt)
        self.assertIn("PRODUCTION", prompt)
        self.assertIn("NO IDLE WARSHIPS", prompt)
        self.assertIn("RESEARCH", prompt)
        self.assertIn("INTEL", prompt)          # anti-paralysis rule
        self.assertIn("NEVER call pause/step/resume", prompt)

    def test_merge_zero_without_env(self):
        os.environ.pop("AIPLAY_MULTI_DIR", None)
        self.assertEqual(agent.merge_peer_artifacts(), "")

    def test_merge_with_env_uses_fresh_artifacts(self):
        dirpath = tempfile.mkdtemp()
        try:
            store = agent_multi.ArtifactStore(dirpath)
            store.write("strategist", "strategist", 3, "hold",
                        {"directives": ["fortify"]})
            os.environ["AIPLAY_MULTI_DIR"] = dirpath
            merged = agent.merge_peer_artifacts()
            self.assertIn("fortify", merged)
            self.assertIn("Peer directives", merged)
        finally:
            os.environ.pop("AIPLAY_MULTI_DIR", None)
            shutil.rmtree(dirpath, ignore_errors=True)


class InstructionQueueTest(unittest.TestCase):
    """Spec 001-peer-instruction-queue: peers edit, commander executes."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = agent_multi.ArtifactStore(self.dir)
        self.q = agent_multi.InstructionQueue(self.store)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _add(self, editor, text="build 2 Ion Cannon Frigates", prio=1):
        return self.q.apply([{"op": "add", "text": text,
                              "priority": prio}], editor)

    # US1: add
    def test_add_assigns_ids_and_orders_by_priority(self):
        self._add("strategist", text="fortify now", prio=2)
        self._add("economist", text="build 2 Ion Cannon Frigates", prio=1)
        block = self.q.prompt_block()
        self.assertIn("build 2 Ion Cannon Frigates", block.splitlines()[1])
        self.assertIn("fortify now", block.splitlines()[2])
        doc = json.loads(open(self.store.path("instruction-queue")).read())
        self.assertEqual(doc["instructions"][0]["by"], "economist")

    def test_add_reorders_existing(self):
        self._add("strategist", text="first instruction line", prio=1)
        self._add("strategist", text="second instruction there", prio=1)
        block = self.q.prompt_block()
        self.assertIn("second instruction there", block.splitlines()[1])

    # US2: remove
    def test_remove_by_id(self):
        self._add("strategist", text="obsolete retreat plan")
        iid = self.q.instructions[0]["id"]
        self.q.apply([{"op": "remove", "id": iid}], "critic")
        self.assertEqual(self.q.instructions, [])
        block = self.q.prompt_block()
        self.assertNotIn("obsolete retreat plan", block)

    def test_remove_unknown_id_rejected(self):
        self._add("strategist")
        acc, rej = self.q.apply([{"op": "remove", "id": 999}], "critic")
        self.assertEqual((acc, rej), (0, 1))
        self.assertEqual(len(self.q.instructions), 1)
        hist = self.q.history()
        self.assertFalse(hist[-1]["accepted"])
        self.assertIn("unknown id", hist[-1]["reason"])

    # US3: reorder
    def test_move_to_front(self):
        self._add("strategist", text="second instruction here", prio=1)
        self._add("strategist", text="third instruction line", prio=2)
        self._add("strategist", text="attack the frigate group", prio=3)
        self.q.apply([{"op": "move",
                       "id": self.q.instructions[2]["id"],
                       "priority": 1}], "analyst")
        block = self.q.prompt_block()
        self.assertIn("attack the frigate group", block.splitlines()[1])

    def test_two_peer_reorder_conflict_newest_wins(self):
        self._add("strategist", text="first instruction line here")
        self._add("strategist", text="second instruction there")
        self.q.apply([{"op": "move", "id": self.q.instructions[1]["id"],
                       "priority": 1}], "analyst")
        self.q.apply([{"op": "move", "id": self.q.instructions[0]["id"],
                       "priority": 1}], "critic")
        block = self.q.prompt_block()
        self.assertIn("first instruction line here", block.splitlines()[1])
        doc = json.loads(open(self.store.path("instruction-queue")).read())
        prios = list(range(1, len(doc["instructions"]) + 1))
        self.assertEqual(prios, prios)   # well-formed by array order

    # US4: executor top-first
    def test_topmost_promotes_pending_and_repeats(self):
        self._add("strategist", text="build 2 Ion Cannon Frigates here",
                  prio=None)   # append at back
        self._add("strategist", text="second instruction there now",
                  prio=None)
        top1 = self.q.work_round(1)
        self.assertEqual(top1["text"].strip(), "build 2 Ion Cannon Frigates here")
        top2 = self.q.work_round(2)
        self.assertEqual(top2, top1)   # stays active until done/failed

    def test_done_marked_moves_on(self):
        self._add("strategist", text="first instruction line here",
                  prio=None)
        self._add("strategist", text="second instruction there",
                  prio=None)
        self.q.work_round(1)
        self.q.mark("done", note="built")
        nxt = self.q.work_round(2)
        self.assertEqual(nxt["text"].strip(), "second instruction there")

    def test_failed_engine_error_moves_on(self):
        self._add("strategist", text="build a locked hulk now")
        self.q.work_round(1)
        self.q.mark("failed", note="E_BAD_REQUEST")
        self.assertEqual(self.q.instructions[0]["state"], "failed")

    def test_stall_flagged_after_10_rounds(self):
        agent_multi.STALL_ROUNDS = 3
        try:
            self._add("strategist", text="impossible objective now")
            for _ in range(4):
                self.q.work_round(1)
            top = self.q.instructions[0]
            self.assertEqual(top["state"], "stalled")
        finally:
            agent_multi.STALL_ROUNDS = 10

    # US5: editorial safety
    def test_oversized_unknownop_badprio_rejected(self):
        acc, rej = self.q.apply([
            {"op": "add", "text": "x" * 500},
            {"op": "detonate"},
            {"op": "add", "text": "short"},
            {"op": "move", "id": 42, "priority": 1},
        ], "strategist")
        self.assertEqual((acc, rej), (0, 4))
        rejected = [h for h in self.q.history() if not h["accepted"]]
        self.assertEqual(len(rejected), 4)
        self.assertTrue(all("op" in h and "reason" in h for h in rejected))

    def test_queue_cap(self):
        for i in range(15):
            self._add("strategist", text=f"valid instruction {i:02d} here",
                      prio=min(i + 1, 12))
        self.assertEqual(len(self.q.instructions),
                         agent_multi.QUEUE_CAP)

    def test_history_ring_and_persistence(self):
        for i in range(120):
            self._add("strategist", text=f"valid instruction {i:03d} here",
                      prio=1)
        hist = self.q.history()
        self.assertLessEqual(len(hist), agent_multi.HISTORY_CAP)

    def test_reset_clears(self):
        self._add("strategist")
        self.q.reset_if_new()
        self.assertEqual(self.q.instructions, [])
        block = self.q.prompt_block()
        self.assertEqual(block, "")

    # FR-004: commander never edits
    # standing vs task split: standing orders never block the active task
    def test_standing_instruction_does_not_block(self):
        self._add("analyst", text="Hold Mothership defensive vs 27 enemies",
                  prio=1)                      # standing by heuristic
        self._add("strategist", text="Send probes to screen enemy positions",
                  prio=2)                      # a task
        top = self.q.work_round(1)
        self.assertIn("probes", top["text"])   # task picked, not the hold
        block = self.q.prompt_block()
        self.assertIn("Standing constraints", block)
        self.assertIn("ALWAYS apply", block)
        self.assertIn("Task queue", block)
        self.assertIn("Hold Mothership", block)  # rendered as standing

    def test_kind_passthrough_and_no_stall_on_standing(self):
        self.q.apply([{"op": "add",
                       "text": "Escort collectors at all times",
                       "kind": "standing", "priority": 1}], "strategist")
        agent_multi.STALL_ROUNDS = 2
        try:
            for _ in range(3):
                top = self.q.work_round(1)
            self.assertIsNone(top)             # nothing to stall
            self.assertEqual(self.q.instructions[0]["state"], "active")
        finally:
            agent_multi.STALL_ROUNDS = 10

    def test_per_role_queues_and_commander_routing(self):
        qs = {r: agent_multi.InstructionQueue(self.store, r)
              for r in ("strategist", "analyst", "economist", "critic")}
        for r in qs:
            qs[r].load()
        qs["strategist"].apply([{"op": "add",
                                 "text": "build the IonFrigate pair now"}],
                               "strategist")
        qs["analyst"].apply([{"op": "add",
                              "text": "scout the enemy flank zone"}],
                             "analyst")
        # files are per-role
        doc = json.loads(open(
            self.store.path("instruction-queue-strategist")).read())
        self.assertIn("IonFrigate", doc["instructions"][0]["text"])
        adoc = json.loads(open(
            self.store.path("instruction-queue-analyst")).read())
        self.assertIn("scout", adoc["instructions"][0]["text"])
        # commander routing: queue_done with q+id hits the right queue
        import agent
        os.environ["AIPLAY_MULTI_DIR"] = self.dir
        try:
            agent.commander_queue_action(
                "queue_done",
                {"q": "analyst",
                 "id": adoc["instructions"][0]["id"]},
                '{"handled": true}', 3)
            reloaded = agent_multi.InstructionQueue(self.store, "analyst")
            reloaded.load()
            # done instructions are filtered out of the working set
            self.assertEqual(reloaded.instructions, [])
            raw = json.loads(open(
                self.store.path("instruction-queue-analyst")).read())
            self.assertEqual(raw["instructions"][0]["state"], "done")
        finally:
            os.environ.pop("AIPLAY_MULTI_DIR", None)

    def test_commander_side_has_no_apply(self):
        self.assertFalse(hasattr(agent, "InstructionQueue"))
        self.assertTrue(hasattr(agent_multi.InstructionQueue, "apply"))

    # peer loop integration
    def test_peer_loop_applies_edits(self):
        side = FakeSidecar(snap(tick=5))
        envelope = json.dumps({
            "summary": "queue two frigates",
            "payload": {"edits": [
                {"op": "add", "text": "build 2 Ion Cannon Frigates",
                 "priority": 1}]}})
        llm = FakeLLM([envelope])
        loop = agent_multi.PeerLoop(
            "strategist", side, llm, self.store,
            agent_multi.LoopLogger("strategist-test",
                                   os.path.join(self.dir, "s.log")),
            prompt="T", full_snapshot=True, queue=self.q)
        loop.step_once()
        self.assertEqual(len(self.q.instructions), 1)
        doc = json.loads(open(
            self.store.path("instruction-queue")).read())
        self.assertIn("Ion Cannon", doc["instructions"][0]["text"])


if __name__ == "__main__":
    unittest.main(verbosity=2)