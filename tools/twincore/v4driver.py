"""Spec 005 v4: driver assembly + the S2 strategist pulse loop
(T008/T009; FR-002/003/007/008/009/011/012).

Program semantics (v4 flips v3): the S2 channel proposes a WHOLE program
packet per pulse; the driver applies it at the tick boundary (all-or-
nothing via program.validate_packet) and the executor translates it. The
pulse is a pure CLIENT process: async two-phase (start_pulse spawns the
HTTP call in a worker thread; harvest delivers), never blocking the tick
loop; spawn anchor advances AT SPAWN so a lagging call can never re-due
the same boundary (spawn-storm guard, carried from v3's S2PulseLoop).

Degradation (FR-012): S2 unreachable/malformed → the executor keeps the
LAST valid program, the failure is recorded (`packet_rejected` +
`degradation` transitions) and NO procedural doctrine is invented. The v3
control modules (s1tick/economy/s2pulse/directive) are NOT referenced.
"""
import json
import threading
import time

from twincore.config import seconds_to_ticks
from twincore.llm import S2Channel
from twincore.program import ProgramError, build_pulse_input, validate_packet
from twincore.client import Client
from twincore.recorder import Recorder, now_ms
from twincore.state_store import Ledger
from twincore.validator import Validator
from twincore.executor import Executor


DEFAULT_PULSE_TICKS = seconds_to_ticks(120.0)
DEFAULT_S2_IN = 12288
DEFAULT_S2_OUT = 3072
DEFAULT_MAX_REPAIRS = 1

STATIC_CHARTER = (
    "You are S2, the strategist of seat {seat}. Reply with ONLY one JSON "
    "object, the program packet:\n"
    "{\n"
    "  \"version\": <int strictly greater than your previous packet>,\n"
    "  \"issued_tick\": <int>,\n"
    "  \"posture\": \"<short note for your own next pulse, optional>\",\n"
    "  \"delegate\": [ <GRANTS for S1, your fast tactical helper — or []>\n"
    "  ],\n"
    "  \"production\": [<order items>], \"tasking\": [<fleet items>]\n"
    "}\n"
    "delegate grant shape (every grant needs a grant_id exactly like "
    "d1/d2/d3 in this form):\n"
    "  {\"grant_id\": \"d1\", \"verbs\": [\"order_attack\", "
    "\"order_guard\"], \"ships\": \"scope:defense\", "
    "\"override_tasking\": true}\n"
    "  ships is either \"scope:defense\"|\"scope:fleet\" or a list of "
    "ship ids; optional \"zone\": [cx,cy,cz,r], \"expires_tick\": <int>.\n"
    "production items:\n"
    "  {\"id\": \"p1\", \"verb\": \"order_build\", \"params\": "
    "{\"type\": \"ResourceCollector\", \"quantity\": 1}}\n"
    "  {\"id\": \"p2\", \"verb\": \"order_research\", \"params\": "
    "{\"tech\": \"MassDrive1Kt\"}, \"once\": true}\n"
    "tasking items (verbs: order_attack order_move order_guard "
    "order_patrol order_formation order_halt order_tactics order_harvest "
    "order_dock):\n"
    "  {\"id\": \"t1\", \"verb\": \"order_attack\", \"ships\": [104, "
    "105], \"target\": 881}\n"
    "  {\"id\": \"t2\", \"verb\": \"order_move\", \"ships\": [104], "
    "\"pos\": [-1200, 0, 640]}\n"
    "item ids pN/tN are 1-8 chars [a-z0-9], all unique; ships ids come "
    "from the LEDGER section below (match-scoped); target 0/missing ids "
    "are rejected. Whole packet or nothing: any schema violation rejects "
    "everything and your previous packet stays in force.\n")


class PulseLoop:
    """S2 program-pulse: async two-phase, spawn-storm guarded."""

    def __init__(self, channel, executor, recorder, ledger,
                 pulse_ticks=None, s2_in=DEFAULT_S2_IN,
                 s2_out=DEFAULT_S2_OUT, max_repairs=DEFAULT_MAX_REPAIRS,
                 charter_lines=None, own_player=0):
        self.channel = channel
        self.executor = executor
        self.recorder = recorder
        self.ledger = ledger
        self.pulse_ticks = int(pulse_ticks or DEFAULT_PULSE_TICKS)
        self.s2_in = s2_in
        self.s2_out = s2_out
        self.max_repairs = max_repairs
        self.charter_lines = list(charter_lines or [])
        self.own_player = own_player
        self.next_pulse_at = self.pulse_ticks
        self._wake = False
        self.last_version = -1
        self._worker = None
        self._result = None
        self._pulse_count = 0

    # -- scheduling (game time only, FR-003/FR-007) --------------------
    def due(self, tick, paused=False):
        if paused:
            return False
        if self._wake:
            return True
        if self._worker is not None and self._worker.is_alive():
            return False
        return tick >= self.next_pulse_at

    def wake(self):
        self._wake = True   # P0 break-interrupt (wired in increment D)

    # -- async two-phase -------------------------------------------------
    def start_pulse(self, tick, paused=False):
        """Phase 1: build the input, spawn the channel call in a worker.
        The cadence anchor advances AT SPAWN (lag can not re-due the
        boundary). Returns False when the boundary was skipped (paused or
        a pulse already in flight)."""
        if paused:
            return False
        self.next_pulse_at = tick + self.pulse_ticks
        if self._worker is not None and self._worker.is_alive():
            return False                    # ≤1 packet per cycle boundary
        msgs, in_meta = self.pulse_input(tick)
        if self.recorder:
            self.recorder.record("usage", tick=tick, channel="s2_in",
                                 total=in_meta["units"], alerts=in_meta
                                 ["alerts"], pruned=in_meta["pruned"])
        self._result = None
        self._pulse_count += 1
        self._worker = threading.Thread(
            target=self._call, args=(msgs, tick), daemon=True)
        self._worker.start()
        return True

    def harvest(self, timeout=None):
        """Phase 2: deliver a finished pulse result (or None). Non-
        blocking with timeout=None; joins briefly when a timeout given."""
        if self._worker is None:
            return None
        if timeout is not None:
            self._worker.join(timeout)
        if self._worker.is_alive():
            return None                     # still in flight
        self._worker = None
        return self._result

    # -- input (FR-009 layout, budget FR-013) -----------------------------
    def pulse_input(self, tick):
        view = self.ledger.as_dict() if self.ledger is not None else {}
        charter = self.charter_lines or [
            STATIC_CHARTER.replace("{seat}", str(self.own_player))]
        prev = self.executor.program if self.executor else None
        prev_lines, status_lines = [], []
        if prev is not None:
            prev_lines = [json.dumps(prev.raw, sort_keys=True,
                                     separators=(",", ":"))]
            status_lines = [
                "%s %s cmds=%s tick=%s aux=%s" % (
                    s["item_id"], s["resolution"], s["command_ids"],
                    s["issued_tick"], s["aux"])
                for s in (self.executor.statuses() if self.executor
                          else [])]
        text, meta = build_pulse_input(
            charter_lines=charter,
            p0_items=list(self._p0_items() or []),
            ledger_lines=self._ledger_echo(view),
            rollup_lines=[],
            prev_program_lines=prev_lines,
            prev_status_lines=status_lines,
            micro_lines=[], reflex_lines=[],
            budget_units=self.s2_in)
        msgs = [{"role": "system", "content": text},
                {"role": "user", "content": "tick %d: emit the JSON "
                 "program packet." % tick}]
        return msgs, meta

    def _ledger_echo(self, view):
        own = self.own_player
        counts = {}
        for s in (view.get("ships_all") or {}).values():
            if s.get("owner") != own:
                continue
            key = s.get("type") or "?"
            counts[key] = counts.get(key, 0) + 1
        lines = ["%s x%d" % (t, n) for t, n in sorted(counts.items())]
        if view.get("bank") is not None:
            lines.append("bank %s" % (view["bank"],))
        lines.append("resources %d" % len(view.get("resources") or {}))
        return lines or ["(no ships observed)"]

    def _p0_items(self):
        return list(getattr(self, "p0_items", []) or [])

    # -- the channel call (worker body; ≤1 bounded repair) ----------------
    def _call(self, msgs, tick):
        attempts = 1 + max(0, int(self.max_repairs))
        reason = None
        for attempt in range(1, attempts + 1):
            out, usage = self.channel.propose(msgs)
            if self.recorder:
                self.recorder.record("usage", tick=tick, channel="s2_out",
                                     prompt=usage.get("prompt"),
                                     completion=usage.get("completion"))
            packet = out.get("packet")
            if packet is not None:
                try:
                    program = validate_packet(packet, self.last_version)
                except ProgramError as e:
                    reason = e.reason
                else:
                    self.last_version = program.version
                    self._wake = False
                    self._result = {"ok": True, "program": program,
                                    "error": None, "tick": tick,
                                    "pulse": self._pulse_count}
                    return self._result
            else:
                reason = out.get("error") or "empty packet"
            if attempt <= attempts - 1:
                # bounded repair: same input, error appended verbatim
                msgs = msgs + [
                    {"role": "assistant", "content": str(reason)},
                    {"role": "user", "content": (
                        "That was not one valid program packet (%s). "
                        "Reply again with only the JSON packet." % reason)}]
        self._wake = False
        self._result = {"ok": False, "program": None, "error": reason,
                        "tick": tick, "pulse": self._pulse_count}
        if self.recorder:
            self.recorder.record("packet_rejected", tick=tick,
                                 reason=reason, pulse=self._pulse_count)
        return self._result

    def call_pulse(self, tick, msgs=None):
        """Synchronous pulse (tests / degraded serial mode)."""
        if msgs is None:
            msgs, _ = self.pulse_input(tick)
        return self._call(msgs, tick)


# --------------------------------------------------------------------------
# driver assembly
# --------------------------------------------------------------------------

class V4Driver:
    """Peer-player assembly: ONE binding (executor's client), game-time
    cadence, program apply at tick boundaries, degradation ladder."""

    def __init__(self, client=None, channel=None, recorder=None,
                 ledger=None, executor=None, validator=None,
                 pulse_ticks=None, s2_in=DEFAULT_S2_IN,
                 s2_out=DEFAULT_S2_OUT, max_repairs=DEFAULT_MAX_REPAIRS,
                 charter_lines=None, own_player=0):
        self.client = client if client is not None else Client(
            mcp_url="http://127.0.0.1:8302/mcp")
        self.channel = channel
        self.recorder = recorder
        self.own_player = own_player
        self.ledger = ledger if ledger is not None else Ledger(own_player)
        self.validator = validator if validator is not None else Validator()
        self.executor = executor if executor is not None else Executor(
            client=self.client, ledger=self.ledger, recorder=self.recorder,
            validator=self.validator, own_player=own_player)
        self.loop = PulseLoop(channel=self.channel,
                              executor=self.executor, recorder=self.recorder,
                              ledger=self.ledger, pulse_ticks=pulse_ticks,
                              s2_in=s2_in, s2_out=s2_out,
                              max_repairs=max_repairs,
                              charter_lines=charter_lines,
                              own_player=own_player)
        self.model_state = "online"

    # -- binding (FR-002: exactly ONE owner) --------------------------------
    def bind(self):
        if self.client is not None:
            return self.client.bind(self.own_player)
        return None

    # -- the game-time tick --------------------------------------------------
    def on_tick(self, tick, paused=False):
        """One driver tick: harvest a finished pulse → apply WHOLE at this
        boundary → run the executor → schedule the next pulse."""
        if paused:
            return []
        # small grace join: collect a worker that finished between ticks
        # (50 ms cap — material lag keeps the tick loop unblocked, FR-007)
        done = self.loop.harvest(timeout=0.05)
        if done is not None:
            self._digest_pulse_result(done, tick)
        issued = self.executor.on_tick(tick)
        if self.loop.due(tick, paused=False):
            self.loop.start_pulse(tick)
        return issued

    def _digest_pulse_result(self, done, tick):
        if done.get("ok"):
            if self.model_state != "online":
                self._transition("online", "strategist responded")
            self._apply_program(done["program"], tick)
        else:
            if self.model_state == "online":
                self._transition("s2_down", done.get("error") or
                                 "pulse failed")

    def _apply_program(self, program, tick):
        prev = self.executor.program
        self.executor.set_program(program, tick)
        if self.recorder:
            self.recorder.record(
                "program", tick=tick, event="applied",
                version=program.version,
                prev_version=(prev.version if prev else None),
                items=[i["id"] for i in
                       program.production + program.tasking],
                has_envelope=program.has_envelope(),
                posture=program.posture)

    def _transition(self, to_state, reason):
        prev = self.model_state
        self.model_state = to_state
        if self.recorder:
            self.recorder.record("degradation", tick=None,
                                 **{"from": prev, "to": to_state,
                                    "reason": reason})

    # -- lifecycle -------------------------------------------------------------
    def pause(self):
        if self.client is not None:
            return self.client.pause()
        return None

    def resume(self):
        if self.client is not None:
            return self.client.resume()
        return None

    # -- observation + run loop (live plumbing) ------------------------------
    def observe(self, snapshot=None):
        """Pull an observation into the ledger (spec-003 shape). Returns
        the snapshot; sets self.engine_tick."""
        if snapshot is None:
            snapshot = self.client.observe()
        ledger = self.ledger
        if hasattr(ledger, "apply_snapshot"):
            ledger.apply_snapshot(snapshot)
        ms = snapshot.get("matchStatus") or {}
        self.engine_tick = ms.get("tick") or snapshot.get("tick") or 0
        return snapshot

    def run(self, max_polls=None, poll_seconds=1.0):
        """Live loop: observe → on_tick, in engine game time. Paused or
        match-over states issue nothing (FR-003). Returns poll count."""
        try:
            self.bind()
        except Exception as e:
            if self.recorder:
                self.recorder.record("degradation", tick=None,
                                     **{"from": "offline",
                                        "to": "interface_down",
                                        "reason": "bind failed: %s" % e})
            return 0
        polls = 0
        while max_polls is None or polls < max_polls:
            try:
                snap = self.observe()
            except Exception as e:
                if self.recorder:
                    self.recorder.record("degradation", tick=None,
                                         **{"from": "online",
                                            "to": "interface_down",
                                            "reason": "observe failed: %s"
                                                      % e})
                self._sleep(poll_seconds)
                continue
            ms = snap.get("matchStatus") or {}
            paused = (not ms.get("running", True)) or \
                bool(snap.get("paused"))
            self.on_tick(self.engine_tick or 0, paused=paused)
            polls += 1
            self._sleep(poll_seconds)
        return polls

    @staticmethod
    def _sleep(seconds):
        if seconds and seconds > 0:
            time.sleep(seconds)


def _cfgget(config, path, default=None):
    """Path access for Config objects AND plain dicts (test doubles)."""
    try:
        if not isinstance(config, dict):
            return config.get(path, default)
    except AttributeError:
        pass
    node = config
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def driver_from_config(config, recorder=None, client=None, channel=None):
    """Build a V4Driver from a validated Config (or config-shaped dict).

    Only OBSERVATION of configuration — no playbook material may appear as
    code constants here; endpoints/cadences/budgets are config, FR-015/013.
    """
    seat = int(_cfgget(config, "seat.player_index", 0) or 0)
    if client is None:
        client = Client(mcp_url=_cfgget(
            config, "transport.mcp_url", "http://127.0.0.1:8302/mcp"))
    if channel is None:
        url = _cfgget(config, "channels.s2.url") or ""
        if not url:
            raise ValueError("channels.s2.url is required (FR-015)")
        channel = S2Channel(
            url=url, model=_cfgget(config, "channels.s2.model", ""),
            max_output=int(_cfgget(config, "channels.s2.max_output",
                                   DEFAULT_S2_OUT)),
            timeout=float(_cfgget(config, "channels.s2.timeout_seconds",
                                  300.0)),
            thinking=_cfgget(config, "channels.s2.thinking", False))
    v4 = _cfgget(config, "v4", {}) or {}
    budgets = v4.get("budgets") or {}
    pulse_ticks = seconds_to_ticks(v4.get("pulse_seconds", 120))
    if recorder is None:
        dsn = config.get("recorder.dsn") or {}
        recorder = Recorder(match_id="v4-%d" % now_ms(), dsn=dsn or None)
    ledger = Ledger(own_player=seat)
    validator = Validator()
    reflexes = v4.get("reflexes") or {"harvest": True, "deliver": True}
    executor = Executor(client=client, ledger=ledger, recorder=recorder,
                        validator=validator, own_player=seat,
                        max_per_tick=int(v4.get("max_commands_per_tick", 6)),
                        reflexes=reflexes)
    drv = V4Driver(client=client, channel=channel, recorder=recorder,
                   ledger=ledger, executor=executor, validator=validator,
                   pulse_ticks=pulse_ticks,
                   s2_in=int(budgets.get("s2_in", DEFAULT_S2_IN)),
                   s2_out=int(budgets.get("s2_out", DEFAULT_S2_OUT)),
                   max_repairs=int(v4.get("max_repair_attempts",
                                          DEFAULT_MAX_REPAIRS)),
                   own_player=seat)
    drv._v4cfg = dict(v4)
    return drv


def main(argv=None):
    """CLI: python -m tools.twincore.v4driver --config <config.json>"""
    import argparse
    import os
    import sys
    ap = argparse.ArgumentParser(description="v4 strategist-driven driver")
    ap.add_argument("--config", required=True)
    ap.add_argument("--poll", type=float, default=1.0,
                    help="wall poll interval (observability only)")
    ap.add_argument("--polls", type=int, default=None,
                    help="stop after N observe polls (default: forever)")
    a = ap.parse_args(argv)
    sys.path.insert(0, os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    from twincore.config import load
    cfg = load(a.config)
    drv = driver_from_config(cfg)
    print("v4driver: seat %d pulse_ticks %d → %s"
          % (drv.own_player, drv.loop.pulse_ticks, a.config), flush=True)
    try:
        drv.run(poll_seconds=a.poll, max_polls=a.polls)
    except KeyboardInterrupt:
        pass
    finally:
        if drv.recorder is not None:
            drv.recorder.close()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
