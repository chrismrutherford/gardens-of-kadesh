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
        llm = FakeLLM(["advice"], cycle=False)
        loop = self._mkloop(side, llm)
        self._steps(loop, 1)
        self.assertTrue(all(not kw.get("expect_tools", True)
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
        self.assertIn("PRODUCTION", prompt)
        self.assertIn("NO IDLE SHIPS", prompt)
        self.assertIn("RESEARCH", prompt)
        self.assertIn("one tool call per round", prompt.lower())

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


if __name__ == "__main__":
    unittest.main(verbosity=2)