#!/usr/bin/env python3
"""Smoke tests for tools/aiplay-mcp.py: stdlib only."""
import json, socket, sys, threading, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import aiplay_mcp as m  # noqa: E402


class TestMcp(unittest.TestCase):
    def setUp(self):
        # stub AI-play UDS server that echoes one canned response
        self.stale = m.SOCKET_PATH
        self.sockpath = "/tmp/aiplay-mcp-test.sock"
        m.SOCKET_PATH = self.sockpath
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(self.sockpath)
        srv.listen(1)
        self.srv = srv
        self.t = threading.Thread(target=self._serve, daemon=True)
        self.t.start()

    def tearDown(self):
        m.SOCKET_PATH = self.stale
        self.srv.close()
        import os
        try: os.unlink(self.sockpath)
        except OSError: pass

    def _serve(self):
        while True:
            try: conn, _ = self.srv.accept()
            except OSError: return
            data = b""
            while b"\n" not in data:
                chunk = conn.recv(4096)
                if not chunk: break
                data += chunk
            req = json.loads(data.decode())
            method = req["method"]
            if method == "time.pause":
                resp = {"id": req["id"], "result": {"paused": True}}
            elif method == "state.snapshot":
                resp = {"id": req["id"], "result": {"tick": 5, "ships": [{"id": 1, "owner": 0, "type": "Interceptor", "pos": [1,2,3], "vel": [0,0,0], "health": 100.0, "healthMax": 100.0, "order": None}], "resources": [], "players": [], "matchStatus": {"running": True, "winner": None, "perPlayer": []}, "paused": False}}
            elif method.startswith("order."):
                resp = {"id": req["id"], "result": {"queued": True, "orderId": 3}}
            else:
                resp = {"id": req["id"], "result": {}}
            conn.sendall((json.dumps(resp) + "\n").encode())
            conn.close()

    def test_relay_order_tool(self):
        out = m.call_tool("order_move", {"ships": [1], "target": [5, 0, 5], "id": 9})
        self.assertEqual(out["content"][0]["type"], "text")
        inner = json.loads(out["content"][0]["text"])
        self.assertEqual(inner["result"]["queued"], True)

    def test_relay_observe(self):
        out = m.call_tool("observe", {})
        inner = json.loads(out["content"][0]["text"])
        self.assertEqual(inner["result"]["tick"], 5)
        self.assertEqual(inner["result"]["ships"][0]["type"], "Interceptor")

    def test_tool_schema(self):
        names = [n for n, _, _ in m.TOOLS]
        self.assertIn("order_build", names)
        self.assertIn("step", names)


if __name__ == "__main__":
    unittest.main()
