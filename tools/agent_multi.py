#!/usr/bin/env python3
"""agent_multi: shared machinery for the multi-agent fleet AI.

Roles and artifact contracts live in specs/001-agent-multi-llm-fleet/
(contract: contracts/agent-multi.md, data: data-model.md).

The COMMANDER is the only binder/orderer (constitution VI). Peer loops are
read-only advisors that publish JSON artifacts to a shared directory; the
commander merges the freshest artifacts into its round prompt.
"""

import argparse
import json
import os
import tempfile
import threading
import time
import urllib.request

import agent

# read-only sidecar methods allowed for peers (order tools NEVER allowed)
PEER_READONLY_METHODS = {"observe", "get_events", "game_status"}
STALE_DEFAULT_SECS = 60
PEER_ROLES = ("strategist", "analyst", "economist", "critic")
MAX_ARTIFACT_BYTES = 16384
MAX_PEERS_TEXT = 6000


def now():
    return time.time()


class SidecarReadOnlyError(Exception):
    pass


class PeerSidecar(agent.MCP):
    """Sidecar client that refuses bind/order tools by construction."""

    def call(self, tool, args):
        if tool not in PEER_READONLY_METHODS or \
                str(tool).startswith("order"):
            raise SidecarReadOnlyError("peer attempted tool: %r" % tool)
        return agent.MCP.call(self, tool, args)


# ------------------------------------------------------------------ artifacts

class ArtifactStore:
    """Atomic JSON artifacts in a per-session directory."""

    def __init__(self, session_dir):
        self.dir = session_dir
        os.makedirs(self.dir, exist_ok=True)

    def path(self, name):
        return os.path.join(self.dir, name + ".json")

    def write(self, name, role, tick, summary, payload):
        doc = {
            "role": role,
            "generatedAt": int(now()),
            "tick": tick,
            "summary": summary[:200],
            "payload": payload,
        }
        text = json.dumps(doc)[:MAX_ARTIFACT_BYTES]
        fd, tmp = tempfile.mkstemp(dir=self.dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(text)
            os.replace(tmp, self.path(name))
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def write_raw(self, name, doc):
        doc = dict(doc)
        doc["generatedAt"] = int(now())
        fd, tmp = tempfile.mkstemp(dir=self.dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(json.dumps(doc)[:MAX_ARTIFACT_BYTES])
            os.replace(tmp, self.path(name))
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def read(self, name):
        return json.loads(_slurp(self.path(name)))

    def read_fresh(self, name, stale_secs=STALE_DEFAULT_SECS, now_fn=now):
        """Latest valid artifact or None; malformed files ignored."""
        try:
            doc = json.loads(_slurp(self.path(name)))
        except (OSError, ValueError):
            return None
        if not isinstance(doc, dict) or doc.get("role") != name:
            return None
        if now_fn() - doc.get("generatedAt", 0) > stale_secs:
            doc["stale"] = True
        return doc


def _slurp(path):
    with open(path) as f:
        return f.read()


def append_order_ring(store, orders, entry, keep=20):
    """Update the commander's orders.json ring (latest `keep` entries)."""
    orders.append(entry)
    orders[:] = orders[-keep:]
    store.write_raw("orders",
                    {"role": "commander", "orders": orders[-keep:]})


# ------------------------------------------------------------------ logging

class LoopLogger:
    """Per-role log file; same line style as agent.py."""

    def __init__(self, role, logfile):
        self.role = role
        self.path = logfile
        self.server_rounds = 0
        self._lock = threading.Lock()

    def log(self, msg):
        line = time.strftime("[%H:%M:%S]") + " " + str(msg).replace("\n", " ")
        with self._lock:
            print(("[%s] " % self.role) + line, flush=True)
            try:
                with open(self.path, "a") as f:
                    f.write(("[%s] " % self.role) + line + "\n")
            except OSError:
                pass

    def perf(self):
        self.log("PERF server-rounds=%d" % self.server_rounds)


def session_dir(session_id):
    return "/tmp/aiplay-multi-%s" % session_id


# ------------------------------------------------------------------ peer loop

class PeerLoop(threading.Thread):
    """Continuous read-only advisory loop against one LLM server."""

    def __init__(self, role, mcp, llm, store, logger, prompt,
                 full_snapshot=False, use_events=False, orders_store=None,
                 peer_stores=()):
        super().__init__(name=role, daemon=True)
        self.role = role
        self.mcp = mcp
        self.llm = llm
        self.store = store
        self.logger = logger
        self.prompt = prompt
        self.full_snapshot = full_snapshot
        self.use_events = use_events
        self.orders_store = orders_store
        self.peer_stores = list(peer_stores)
        self.round = 0
        self.tick_seen = -1
        self.event_idx = 0
        self.history = []        # last 3 short summaries
        self.stop_flag = threading.Event()

    def stop(self):
        self.stop_flag.set()

    # -- state fetch helpers --------------------------------------------

    def _observe(self):
        snap = json.loads(
            self.mcp.call("observe", {})["result"]["content"][0]["text"])
        if isinstance(snap, dict) and "result" in snap and "ships" not in snap:
            snap = snap["result"]
        if "error" in snap:
            return None
        return snap

    def _events(self):
        evs = json.loads(
            self.mcp.call("get_events", {})["result"]["content"][0]["text"])
        try:
            events = evs["result"]["events"]
        except (KeyError, TypeError):
            return []
        fresh = events[max(0, len(events) - self.event_idx):]
        self.event_idx = len(events)
        return fresh

    def _context(self, snap, running):
        view = agent.compress_snapshot(snap)
        if not self.full_snapshot:
            summary = agent.world_summary(view)
        else:
            summary = json.dumps(view)
        parts = [
            self.prompt,
            "tick=%s running=%s" % (view.get("tick"), running),
            summary,
        ]
        if self.use_events:
            parts.append("events=" + json.dumps(self._events()[-4:]))
        for peer in self.peer_stores:
            for role in ("strategist", "economist"):
                art = peer.read_fresh(role)
                if art and art["role"] == role:
                    parts.append("%s: %s" % (role, art.get("summary", "")))
        if self.orders_store is not None:
            try:
                ring = self.orders_store.read("orders").get("orders", [])
                parts.append("recentOrders=" + json.dumps(ring[-6:]))
            except (OSError, ValueError, AttributeError):
                pass
        if self.history:
            parts.append("recent thoughts: " + " | ".join(self.history))
        return "\n".join(p[:3000] for p in parts)

    @staticmethod
    def has_tool_call(text):
        return '"name"' in text and ('"function"' in text or '"tool_c' in text)

    # -- main loop -------------------------------------------------------

    def step_once(self):
        """One advisory round: observe → reason → publish. Raises on error."""
        snap = self._observe()
        if snap is None:
            raise agent.MCPFailure("observe failed")
        running = snap.get("matchStatus", {}).get("running", False)
        tick = snap.get("tick") or snap.get("matchStatus", {}).get("tick", 0)
        if tick < self.tick_seen:
            self.history = []           # reset rebase (FR-007), before use
        if running is False:
            self.store.write(self.role, self.role, tick,
                             "match over — peer idle",
                             {"directives": []})
            return "idle"
        ctx = self._context(snap, running)
        reply = self.llm.chat(ctx, expect_tools=False, max_tokens=4000)
        if self.has_tool_call(reply):
            self.logger.log("discarded tool-call reply (FR-010)")
        elif reply.strip():
            self.history.append(reply[:120])
            self.history = self.history[-3:]
        summary, payload = _peer_payload(reply)
        self.store.write(self.role, self.role, tick, summary, payload)
        self.logger.server_rounds += 1
        self.tick_seen = max(self.tick_seen, tick)
        self.logger.log("peer round %d published: %s" % (self.round,
                                                         summary))
        return summary

    def run(self):
        backoff = 1.0
        while not self.stop_flag.is_set():
            self.round += 1
            try:
                self.step_once()
                backoff = 1.0       # healthy: continue back-to-back
            except Exception as exc:
                self.logger.log("peer round %d error (backoff %.0fs): %r"
                                % (self.round, backoff, exc))
                if isinstance(exc, SidecarReadOnlyError):
                    pass    # peer violation logged; still never mutates
                time.sleep(backoff)
                backoff = min(backoff * 2, 30.0)


def _peer_payload(reply):
    """Peer replies are freeform; try a JSON envelope, fall back to raw."""
    try:
        doc = json.loads(reply)
        if isinstance(doc, dict) and "payload" in doc:
            return (str(doc.get("summary", reply[:200]))[:200],
                    doc["payload"])
    except ValueError:
        pass
    return reply[:200], {"directives": [], "notes": reply[:2000]}


# ------------------------------------------------------------------ merge

def merge_artifacts(store, stale_secs=STALE_DEFAULT_SECS, now_fn=now):
    """Collect fresh peer artifacts for the commander's prompt (max text)."""
    fresh, stale = [], []
    budget = MAX_PEERS_TEXT
    for role in PEER_ROLES:
        art = store.read_fresh(role, stale_secs, now_fn)
        if art is None:
            continue
        if art.get("stale"):
            stale.append("%s[stale, tick %s]"
                         % (role, art.get("tick")))
            continue
        if budget <= 0:
            break
        lines = ["%s[fresh, tick %s]: %s"
                 % (role, art.get("tick"), art.get("summary", ""))]
        payload = art.get("payload") or {}
        for key in ("directives", "reactions", "advice", "buildQueue"):
            vals = payload.get(key)
            if vals:
                lines.append("  %s: %s" % (key, json.dumps(vals)[:400]))
        for ob in payload.get("objections", [])[:3]:
            lines.append("  OBJECTION order %s: %s"
                         % (ob.get("orderId"), ob.get("reason", "")))
        text = "\n".join(lines)[:budget + 200]
        fresh.append(text[:budget])
        budget -= len(text)
    return fresh, stale


# --------------------------------------------------------------- launcher glue


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="agent_multi internals")
    print("library module; see tools/agent-multi.py")