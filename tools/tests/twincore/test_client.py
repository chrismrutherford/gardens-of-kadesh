#!/usr/bin/env python3
"""Spec 004 tests: tools/twincore/client.py (T005).

Covers: MCP HTTP session client against an in-process stub sidecar —
jsonrpc envelope, tools/call relaying, sync error envelopes (E_*), interface
unreachable → InterfaceError, re-observe policy triggers (bind, id-surge).
D2/D3, edge-case contract.
"""
import http.server
import json
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "twincore"))

import twincore.client as C  # noqa: E402


class StubMCP(http.server.BaseHTTPRequestHandler):
    calls = []           # (name, arguments)
    snapshot = {"tick": 100, "paused": False, "income": 12.5}
    events = {"events": [], "tick": 100}
    bind_result = {"sessionId": 1}
    order_result = {"applied": True}

    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(length) or b"{}")
        StubMCP.calls.append((req.get("method"), req.get("params")))
        rid = req.get("id", 1)
        method = req.get("method")
        if method == "initialize":
            resp = {"jsonrpc": "2.0", "id": rid,
                    "result": {"protocolVersion": "2025-06-18",
                               "capabilities": {"tools": {}},
                               "serverInfo": {"name": "stub"}}}
        elif method == "tools/call" or method.startswith("tools/") or True:
            name = (req.get("params") or {}).get("name")
            args = (req.get("params") or {}).get("arguments", {})
            if name == "bind_session":
                StubMCP.bind_result["boundPlayer"] = args.get("playerIndex")
                text = {"id": rid, "result": StubMCP.bind_result}
            elif name == "observe":
                text = {"id": rid, "result": StubMCP.snapshot}
            elif name == "get_events":
                text = {"id": rid, "result": StubMCP.events}
            elif name == "game_status":
                text = {"id": rid, "result": {"running": True}}
            elif name == "order_move":
                text = {"id": args.get("id", rid), "result": StubMCP.order_result}
            elif name == "sidecar_error":
                text = {"ok": False, "error": "ai-play layer unreachable: x"}
            else:
                text = {"id": rid, "error": {"code": "E_BAD_REQUEST",
                                             "message": "unknown tool %s" % name}}
            resp = {"jsonrpc": "2.0", "id": rid,
                    "result": {"content": [{"type": "text",
                                            "text": json.dumps(text)}]}}
        body = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class TestCaseBase(unittest.TestCase):
    def setUp(self):
        import random
        port = random.randint(20000, 45000)
        StubMCP.calls = []
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), StubMCP)
        self.t = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.t.start()
        self.client = C.Client("http://127.0.0.1:%d/mcp" % port)

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class TestClient(TestCaseBase):
    def test_bind_and_observe(self):
        res = self.client.bind(player_index=0)
        self.assertEqual(res["boundPlayer"], 0)
        self.assertEqual(res["sessionId"], 1)
        snap = self.client.observe()
        self.assertEqual(snap["tick"], 100)
        self.assertEqual(self.client.last_tick, 100)

    def test_events(self):
        self.client.get_events()
        (name, args), = StubMCP.calls
        self.assertEqual(name, "tools/call")
        self.assertEqual(args["name"], "get_events")

    def test_order_echoes_id(self):
        res = self.client.order("order_move", {"id": 41,
                                               "ships": [1, 2],
                                               "target": [10, 20, 0]})
        self.assertEqual(res["id"], 41)
        self.assertEqual(res["result"], {"applied": True})

    def test_sync_error_envelope_returned_not_raised(self):
        res = self.client.order("order_bogus_verb", {})
        self.assertEqual(res["error"]["code"], "E_BAD_REQUEST")

    def test_interface_down(self):
        self.client2 = C.Client("http://127.0.0.1:1/mcp")
        with self.assertRaises(C.InterfaceError):
            self.client2.observe()

    def test_engine_unreachable_is_iface_error(self):
        # sidecar {ok:false} = engine layer unreachable → InterfaceError
        # (degradation ladder FR-024), never surfaced as a normal result
        with self.assertRaises(C.InterfaceError):
            self.client._rpc("sidecar_error", {})

    def test_reobserve_policy_helpers(self):
        # id-surge counter: threshold triggers re-observe flag
        for _ in range(C.ID_SURGE_THRESHOLD):
            self.client.note_id_error("E_BAD_ID", tick=1)
        self.assertTrue(self.client.should_reobserve())
        self.client.reobserved()
        self.assertFalse(self.client.should_reobserve())
        self.client.note_bind_reset()
        self.assertTrue(self.client.should_reobserve())

    def test_last_tick_tracks_observation(self):
        c = self.client
        c.get_events()
        self.assertEqual(c.last_tick, 100)
        StubMCP.events = {"events": [], "tick": 150}
        c.get_events()
        self.assertEqual(c.last_tick, 150)


if __name__ == "__main__":
    unittest.main()