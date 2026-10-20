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
                 peer_stores=(), queue=None):
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
        self.queue = queue
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
        if self.queue is not None:
            parts.append(self.queue.prompt_block())
            parts.append(EDITOR_HOWTO)
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
        if self.queue is not None:
            try:
                edits = payload.get("edits") if isinstance(payload, dict) \
                    else None
                if edits:
                    acc, rej = self.queue.apply(edits, self.role)
                    if rej:
                        # feed the rejection history back to this peer
                        for h in self.queue.history()[-4:]:
                            if h.get("by") == self.role and \
                                    not h.get("accepted"):
                                self.history.append(
                                    "editRejected: %s" % h.get("reason"))
            except Exception as exc:
                self.logger.log("queue edit error: %r" % exc)
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


import re as _re
_BOILERPLATE = _re.compile(
    r"^\s*(we need|i need|need |user:|user :|you are|okay|all right|let me)",
    _re.IGNORECASE)


def _clean_reply(text):
    """Drop Qwen-style leading meta commentary before the real content."""
    lines = text.strip().splitlines()
    start = 0
    while start < len(lines) and (not lines[start].strip() or
                                  _BOILERPLATE.match(lines[start])):
        start += 1
    return "\n".join(lines[start:])


def _peer_payload(reply):
    """Peer replies are freeform; try a JSON envelope, fall back to raw."""
    reply = _clean_reply(reply)
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


# ------------------------------------------------------- instruction queue

QUEUE_CAP = 12
HISTORY_CAP = 100
INSTRUCTION_MIN, INSTRUCTION_MAX = 8, 200


class InstructionQueue:
    """Shared priority queue: peers edit, the commander executes."""

    def __init__(self, store, logger=None):
        self.store = store
        self.lock = threading.Lock()
        self.logger = logger
        self.next_id = 1
        self.instructions = []
        self.state = {"active": None, "rounds_no_progress": 0}

    # -- persistence ------------------------------------------------------

    def _write_history(self, entry):
        try:
            hist = self.store.read("instruction-history")
        except (OSError, ValueError):
            hist = {"role": "queue-history", "entries": []}
        entries = hist.get("entries", [])
        entries.append(entry)
        with open(self.store.path("instruction-history"), "w") as f:
            f.write(json.dumps({"role": "queue-history",
                                "entries": entries[-HISTORY_CAP:]}))
        return entries[-1]

    def load(self):
        try:
            q = self.store.read("instruction-queue")
            self.instructions = [
                i for i in q.get("instructions", [])
                if isinstance(i, dict) and i.get("state") != "done"]
            self.next_id = max(
                [i["id"] for i in self.instructions], default=0) + 1
        except (OSError, ValueError):
            pass
        return self.instructions

    def save(self):
        self.store.write_raw("instruction-queue", {
            "role": "queue",
            "version": (self.store.read("instruction-queue").get(
                "version", 0) + 1
                if _json_ok(self.store.path("instruction-queue")) else 1)
            if os.path.exists(self.store.path("instruction-queue")) else 1,
            "instructions": self.instructions,
        })

    # -- editor ops (peers only) -----------------------------------------

    def apply(self, edits, editor):
        """Validate + apply one peer's edit list; returns accepted/rejected."""
        accepted, rejected = 0, 0
        for e in edits or []:
            try:
                if not isinstance(e, dict):
                    raise ValueError("edit not an object")
                op = e.get("op")
                if op == "add":
                    text = str(e.get("text", "")).strip()
                    if not (INSTRUCTION_MIN <= len(text) <= INSTRUCTION_MAX):
                        raise ValueError("text length %d..%d"
                                         % (INSTRUCTION_MIN, INSTRUCTION_MAX))
                    if "\n" in text:
                        raise ValueError("one instruction per line")
                    if len(self.instructions) >= QUEUE_CAP:
                        raise ValueError("queue full")
                    prio = int(e.get("priority") or len(self.instructions) + 1)
                    prio = max(1, min(prio,          # clamp, not reject:
                                      len(self.instructions) + 1))
                    if len(self.instructions) >= QUEUE_CAP:
                        raise ValueError("queue full")
                    item = {"id": self.next_id, "text": text,
                            "state": "pending", "by": editor,
                            "at": int(now()), "notes": []}
                    self.instructions.insert(int(prio) - 1, item)
                    self.next_id += 1
                    self._write_history({"op": "add", "id": item["id"],
                                         "priority": prio, "text": text,
                                         "by": editor, "at": item["at"],
                                         "accepted": True})
                    accepted += 1
                elif op == "remove":
                    i = self._indexOf(e.get("id"))
                    if i is None:
                        raise ValueError("unknown id %r" % e.get("id"))
                    self.instructions.pop(i)
                    self._write_history({"op": "remove",
                                         "id": e.get("id"), "by": editor,
                                         "at": int(now()), "accepted": True})
                    accepted += 1
                elif op == "move":
                    i = self._indexOf(e.get("id"))
                    if i is None:
                        raise ValueError("unknown id %r" % e.get("id"))
                    prio = max(1, min(int(e.get("priority")),
                                      max(1, len(self.instructions))))
                    item = self.instructions.pop(i)
                    self.instructions.insert(prio - 1, item)
                    self._write_history({"op": "move", "id": item["id"],
                                         "priority": prio, "by": editor,
                                         "at": int(now()), "accepted": True})
                    accepted += 1
                elif op == "prune":
                    n_before = len(self.instructions)
                    self.instructions = [it for it in self.instructions
                                         if it.get("state") in
                                         ("pending", "active")]
                    self._write_history({"op": "prune",
                                         "removed": n_before -
                                         len(self.instructions),
                                         "by": editor, "at": int(now()),
                                         "accepted": True})
                    accepted += 1
                else:
                    raise ValueError("unknown op %r" % op)
            except ValueError as exc:
                rejected += 1
                self._write_history({"op": str((e or {}).get("op")),
                                     "by": editor, "at": int(now()),
                                     "accepted": False,
                                     "reason": str(exc)})
        if accepted:
            self.save()
        if accepted or rejected:
            self._log("edits by %s: %d accepted, %d rejected"
                      % (editor, accepted, rejected))
        return accepted, rejected

    def reset_if_new(self):
        """Called by the commander when it detects a match reset/clear."""
        with self.lock:
            self.instructions = []
            self.state = {"active": None, "rounds_no_progress": 0}
            self.save()

    def history(self):
        try:
            return self.store.read("instruction-history").get("entries", [])
        except (OSError, ValueError):
            return []

    def clear(self):
        with self.lock:
            self.instructions = []
            self.state = {"active": None, "rounds_no_progress": 0}
            self.save()

    # -- executor (commander) side ---------------------------------------

    def topmost(self):
        """First non-done instruction; promotes to active."""
        for item in self.instructions:
            if item.get("state") in ("pending", "active"):
                item["state"] = "active"
                return item
        return None

    def note(self, text):
        top = self.state.get("active_ref")
        if top is not None:
            top.setdefault("notes", []).append(text[:160])
            top["notes"] = top["notes"][-5:]

    def work_round(self, cmd_round):
        top = self.topmost()
        self.state["active_ref"] = top
        if top is None:
            self.state["rounds_no_progress"] = 0
            return None
        if top.get("state") == "active":
            self.state["rounds_no_progress"] += 1
            if self.state["rounds_no_progress"] >= STALL_ROUNDS:
                top["state"] = "stalled"
                self.state["rounds_no_progress"] = 0
                self._log("instruction #%d stalled" % top["id"])
                self.save()
        return top

    def mark(self, state, note=None):
        top = self.state.get("active_ref")
        if top is not None:
            top["state"] = state
            if note:
                top.setdefault("notes", []).append(note[:160])
            self.state["rounds_no_progress"] = 0
            self._log("instruction #%d -> %s" % (top["id"], state))
            self.save()

    def _log(self, msg):
        if self.logger is not None:
            self.logger.log(msg)

    def _indexOf(self, iid):
        try:
            iid = int(iid)
        except (TypeError, ValueError):
            return None
        for i, item in enumerate(self.instructions):
            if item["id"] == iid:
                return i
        return None

    # -- formatter -------------------------------------------------------

    def prompt_block(self):
        if not self.instructions:
            return ""
        lines = ["Instruction queue (execute TOP-FIRST; mark progress in"
                 " notes):"]
        for prio, item in enumerate(self.instructions, 1):
            lines.append("  %d. [#%d %s|by %s] %s%s" % (
                prio, item["id"], item["state"], item.get("by"),
                item["text"],
                (" recent: " + "; ".join(item.get("notes", [])[-2:]))
                if item.get("notes") else ""))
        return lines[0] + "\n" + "\n".join(lines[1:]) + "\n"


def _json_ok(path):
    try:
        with open(path) as f:
            json.load(f)
        return True
    except (OSError, ValueError):
        return False


STALL_ROUNDS = 10

EDITOR_HOWTO = (
    "As an editor you MAY include an \"edits\" array in your reply payload "
    '(JSON: {"summary": "...", "payload": {"edits": [...]}}) with ops: '
    '{"op":"add","text":"<one imperative, 8-200 chars>","priority":N} / '
    '{"op":"remove","id":N} / {"op":"move","id":N,"priority":N}. The '
    "commander executes the queue top-first. Rejections for your edits "
    "appear in your context as editRejected notes — correct and resubmit.")

# --------------------------------------------------------------- launcher glue


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="agent_multi internals")
    print("library module; see tools/agent-multi.py")