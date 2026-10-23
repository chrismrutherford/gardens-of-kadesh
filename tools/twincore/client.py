"""Spec 004 twin-core: MCP session client (driver-runtime.md §1, T005).

Speaks JSON-RPC `tools/call` to the spec-003 MCP sidecar over local HTTP
(D3). Wire encoding/validation ground truth is
`tools/aiplay_protocol.py` (imported single-source, D2). Unreachable
sidecar/game surface raises InterfaceError so the driver can run its
degradation ladder (FR-024). Re-observe policy: bind, match load/restart,
or an id-mismatch error surge (edge-case contract).
"""
import json
import urllib.request

ID_SURGE_THRESHOLD = 3


class InterfaceError(RuntimeError):
    """Sidecar or aiplay layer unreachable (DEGRADED_IFACE_DOWN path)."""


class Client:
    """One seat's session client. Exactly one binding owner per seat: the
    driver owns this client instance; S2 never sees it (FR-002)."""

    def __init__(self, mcp_url, timeout=10.0):
        self.mcp_url = mcp_url
        self.timeout = timeout
        self._rpc_id = 0
        self._id_errors = 0
        self._reobserve = False
        self.last_tick = None
        self.bound_player = None

    # -- rpc ---------------------------------------------------------------
    def _rpc(self, name, args=None, tool=None):
        self._rpc_id += 1
        req = {"jsonrpc": "2.0", "id": self._rpc_id,
               "method": "tools/call",
               "params": {"name": tool or name, "arguments": args or {}}}
        data = json.dumps(req).encode()
        http_req = urllib.request.Request(
            self.mcp_url, data=data,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(http_req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode())
        except (urllib.error.URLError, OSError) as e:
            raise InterfaceError("sidecar unreachable: %s" % (e,))
        if "error" in body:
            raise InterfaceError("sidecar error: %s" % body["error"])
        result = body.get("result", {})
        content = result.get("content") or []
        text = content[0].get("text", "{}") if content else "{}"
        try:
            wire = json.loads(text)
        except ValueError:
            raise InterfaceError("sidecar returned unparseable payload")
        if not isinstance(wire, dict):
            raise InterfaceError("sidecar returned non-object payload: %r"
                                 % (wire,))
        if wire.get("ok") is False:
            # sidecar wraps engine-layer transport failure as {ok:false} +
            # isError:true — this is DEGRADED_IFACE_DOWN, never retellable
            # (FR-024: no component may guess)
            raise InterfaceError("ai-play layer unreachable: %s"
                                 % wire.get("error"))
        return wire

    # -- session tools -----------------------------------------------------
    def bind(self, player_index=0):
        wire = self._rpc("bind_session", {"playerIndex": player_index},
                         tool="bind_session")
        res = wire.get("result", wire)
        self.bound_player = res.get("boundPlayer", player_index)
        self._reobserve = True
        return res

    def status(self):
        return self._rpc("game_status", tool="game_status").get("result", {})

    def observe(self, sections=None, owner=None):
        args = {}
        if sections:
            args["sections"] = sections
        if owner is not None:
            args["owner"] = owner
        res = self._rpc("observe", args, tool="observe").get("result", {})
        tick = res.get("tick")
        if isinstance(tick, int):
            self.last_tick = tick
        return res

    def get_events(self, after=None):
        args = {"after": after} if after is not None else {}
        res = self._rpc("get_events", args, tool="get_events").get("result", {})
        tick = res.get("tick")
        if isinstance(tick, int):
            self.last_tick = tick
        return res

    def pause(self):
        return self._rpc("time.pause", tool="pause").get("result", {})

    def resume(self):
        return self._rpc("time.resume", tool="resume").get("result", {})

    def step(self, ticks=1):
        return self._rpc("time.step", {"ticks": ticks}, tool="step")\
            .get("result", {})

    def save(self, name=None):
        return self._rpc("game.save",
                         {"name": name} if name else {}, tool="save_game")\
            .get("result", {})

    # -- order tools (binding owner's only effect path) ---------------------
    def order(self, tool, params=None):
        """Issue one order verb; returns the UDS wire envelope
        {id, result}|{id, error} — engine sync validation included (FR-006).
        Transport failures raise InterfaceError."""
        return self._rpc(tool, params or {})

    # -- re-observe policy (edge-case contract) ------------------------------
    def note_id_error(self, code, tick=None):
        if code == "E_BAD_ID":
            self._id_errors += 1
            if self._id_errors >= ID_SURGE_THRESHOLD:
                self._reobserve = True  # identifiers renumbered (match load)

    def note_bind_reset(self):
        self._reobserve = True

    def should_reobserve(self):
        return self._reobserve

    def reobserved(self):
        self._reobserve = False
        self._id_errors = 0