#!/usr/bin/env python3
"""Spec 004 tests: tools/twincore/llm.py (T015).

OpenAI-compatible chat-completions clients: S1 tool-calling + S2 structured
JSON output, bounded retries, usage metering, truncation-before-send output
budget. Stub HTTP endpoint; URLs config-only (FR-028).
"""
import http.server
import json
import os
import random
import sys
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "twincore"))

import twincore.llm as L  # noqa: E402


def completions(body):
    return {
        "id": "cmpl-1", "object": "chat.completion",
        "model": "stub",
        "choices": [{
            "index": 0,
            "message": body,
            "finish_reason": body.get("_finish", "tool_calls")
        }],
        "usage": {"prompt_tokens": 900, "completion_tokens": 80,
                  "total_tokens": 980},
    }


class StubLLM(http.server.BaseHTTPRequestHandler):
    script = []      # list of (status_code, body) — pops per request
    requests = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(length) or b"{}")
        StubLLM.requests.append(req)
        status, body = StubLLM.script.pop(0) if StubLLM.script else (200, {})
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class EndpointCase(unittest.TestCase):
    def setUp(self):
        port = random.randint(20000, 45000)
        StubLLM.script = []
        StubLLM.requests = []
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port),
                                                     StubLLM)
        threading.Thread(target=self.httpd.serve_forever,
                         daemon=True).start()
        self.url = "http://127.0.0.1:%d/v1/chat/completions" % port

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class S1Case(EndpointCase):
    def setUp(self):
        super().setUp()
        self.ch = L.S1Channel(self.url, model="s1-stub", max_output=200)

    def test_tool_call_request_shape(self):
        StubLLM.script = [(200, completions({"role": "assistant",
                                             "tool_calls": [
                                                 {"id": "c1",
                                                  "type": "function",
                                                  "function": {"name":
                                                               "order_move",
                                                               "arguments":
                                                               "{\"ships\": [1], \"target\": [1,2,0]}"},
                                             }]}))]
        calls, usage = self.ch.propose(
            messages=[{"role": "system", "content": "card"}],
            tools=[{"name": "order_move", "desc": "move ships"}])
        req = StubLLM.requests[0]
        self.assertEqual(req["messages"][0]["role"], "system")
        self.assertEqual(req["model"], "s1-stub")
        tools = req["tools"]
        self.assertEqual(tools[0]["type"], "function")
        self.assertEqual(tools[0]["function"]["name"], "order_move")
        self.assertEqual(calls, [{"verb": "order_move",
                                  "params": {"ships": [1],
                                             "target": [1, 2, 0]}}])
        self.assertEqual(usage["total"], 980)

    def test_content_only_response(self):
        StubLLM.script = [(200, completions({"role": "assistant",
                                             "content": "thinking...",
                                             "_finish": "stop"}))]
        calls, usage = self.ch.propose(messages=[], tools=[])
        self.assertEqual(calls, [])  # freeform prose discarded (protocol §1)

    def test_retry_on_5xx_then_success(self):
        StubLLM.script = [
            (500, {"error": "boom"}),
            (200, completions({"role": "assistant", "tool_calls": []})),
        ]
        calls, usage = self.ch.propose(messages=[], tools=[])
        self.assertEqual(calls, [])
        self.assertEqual(len(StubLLM.requests), 2)

    def test_retry_budget_exhaustion_raises(self):
        StubLLM.script = [(500, {"error": "boom"})] * 3
        with self.assertRaises(L.LLMError):
            self.ch.propose(messages=[], tools=[])


class S2Case(EndpointCase):
    def setUp(self):
        super().setUp()
        self.ch = L.S2Channel(self.url, model="s2-stub", max_output=2048)

    def test_json_output_parsed(self):
        packet = {"directive": {"patch": {}}, "orders": [],
                  "history_op": None, "digest_config": {},
                  "questions": []}
        StubLLM.script = [(200, completions(
            {"role": "assistant", "content": json.dumps(packet),
             "_finish": "stop"}))]
        got, usage = self.ch.propose(messages=[])
        self.assertEqual(got["packet"], packet)
        req = StubLLM.requests[0]
        self.assertEqual(req["response_format"], {"type": "json_object"})

    def test_unparseable_json_flagged(self):
        StubLLM.script = [(200, completions(
            {"role": "assistant", "content": "{broken", "_finish": "stop"}))]
        got, usage = self.ch.propose(messages=[])
        self.assertIsNone(got["packet"]) if isinstance(got, dict) \
            else None
        self.assertIn("error", got)

    def test_usage_metering_shape(self):
        StubLLM.script = [(200, completions(
            {"role": "assistant", "content": "{}", "_finish": "stop"}))]
        _, usage = self.ch.propose(messages=[])
        self.assertEqual(set(usage), {"prompt", "completion", "total"})


if __name__ == "__main__":
    unittest.main()