"""Spec 004 twin-core: driver — seat lifecycle + game-time clocks (T007).

Peer-client entry point (research D1). Owns the seat state machine
(protocol §8): exactly one binding owner (the client instance, FR-002);
every transition recorded as a recorder `degradation` line. Game-time
cadence derives from observed ticks only (FR-003) — a paused match produces
no cadence progress. Events/deltas flow into the recorder (FR-021).

Entry: python3 tools/twincore/driver.py --config <cfg.json>
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

from twincore.client import Client, InterfaceError  # noqa: E402
from twincore import config as config  # noqa: E402
from twincore import events as events  # noqa: E402
from twincore import recorder as recorder  # noqa: E402
from twincore import state_store as state_store  # noqa: E402
from twincore.llm import LLMError  # noqa: E402
from twincore.s1tick import ChannelFailure  # noqa: E402


class _S1Down(RuntimeError):
    """S1 channel unusable → DEGRADED_S1_DOWN (FR-024): seat idles."""


def _Ledger(own_player):
    try:
        return state_store.Ledger(own_player=own_player)
    except Exception:  # pragma: no cover — ledger construction must never
        return None    # take the seat down; degrade to no-ledger idle


class GameClock:
    """Cadence in game time: a boundary is crossed only when observed ticks
    advance past the next boundary (paused matches freeze it, FR-003)."""

    def __init__(self, interval_ticks=1):
        self.interval_ticks = int(interval_ticks)
        self._next = 1
        self._last = 0

    def ready(self, observed_tick, paused=False):
        if paused:
            return False
        if observed_tick < self._last:
            return False  # stale observation (match restart) — wait baseline
        return observed_tick >= self._next

    def advance(self, observed_tick):
        """Cross the boundary at observed_tick; returns the boundary tick."""
        self._last = observed_tick
        crossed = self._next
        self._next = max(self._next, observed_tick) + self.interval_ticks
        return crossed


class Driver:
    """Seat owner: bind → BOUND_ACTIVE loop → degradation → UNBOUND."""

    UNBOUND = "UNBOUND"
    BOUND_ACTIVE = "BOUND_ACTIVE"
    DEGRADED_S2_DOWN = "DEGRADED_S2_DOWN"
    DEGRADED_S1_DOWN = "DEGRADED_S1_DOWN"
    DEGRADED_IFACE_DOWN = "DEGRADED_IFACE_DOWN"

    def __init__(self, cfg, client=None, match_id=None):
        if isinstance(cfg, str):
            cfg = config.load(cfg)
        self.cfg = cfg
        self.client = client if client is not None else \
            Client(cfg.get("transport.mcp_url"))
        self.state = self.UNBOUND
        player = cfg.get("seat.player_index")
        match = match_id or "seat%d" % player
        base = cfg.get("recorder.dir") or "output/twincore-recordings"
        self.recorder = recorder.Recorder(
            os.path.join(base, match), match_id=match)
        self.consumer = events.DeltaConsumer()
        self.tick_clock = GameClock(cfg.get("cadence.tick_ticks"))
        self._match_over = False
        # M-B wiring: ledger + S1 loop built from config when a channel URL
        # exists; injection point for tests/degradation runs (D10)
        self.ledger = None
        self.validator = None
        self.s1_loop = None
        self._wire_s1()

    def _wire_s1(self):
        from twincore.economy import EconomyManager
        from twincore.s1tick import S1TickLoop
        from twincore.validator import Validator
        url = self.cfg.get("channels.s1.url")
        if not url or self.s1_loop is not None:
            return self.s1_loop
        import twincore.llm as llm
        own_player = self.cfg.get("seat.player_index")
        self.ledger = _Ledger(own_player)
        channel = llm.S1Channel(
            url, model=self.cfg.get("channels.s1.model", ""),
            max_output=self.cfg.get("channels.s1.max_output", 200),
            temperature=self.cfg.get("channels.s1.temperature", 0.2),
            timeout=self.cfg.get("channels.s1.timeout_seconds", 60.0),
            thinking=self.cfg.get("channels.s1.thinking"))
        validator = Validator()
        economy = EconomyManager()
        self.validator = validator
        self.s1_loop = S1TickLoop(
            ledger=self.ledger, validator=validator, economy=economy,
            channel=channel, client=self.client, recorder=self.recorder,
            interval_ticks=self.cfg.get("cadence.tick_ticks"),
            budget_in=self.cfg.get("budgets.s1_in"))
        return self.s1_loop

    # -- lifecycle ---------------------------------------------------------
    def _transition(self, to, reason):
        self.recorder.record(
            "degradation", tick=self.client.last_tick or 0,
            **{"from": self.state, "to": to, "reason": reason})
        self.state = to

    def bind(self):
        if self.state != self.UNBOUND:
            raise RuntimeError("bind() from state %s" % self.state)
        try:
            self.client.bind(player_index=self.cfg.get("seat.player_index"))
        except InterfaceError as e:
            # protocol §8: DEGRADED_* is only reachable from BOUND_ACTIVE;
            # a failed bind stays UNBOUND, but the failure is recorded
            self._transition(self.UNBOUND, "bind failed: %s" % e)
            raise
        self._transition(self.BOUND_ACTIVE, "bound seat %d"
                         % self.cfg.get("seat.player_index"))

    # -- one observation cycle ----------------------------------------------
    def step(self):
        """Observe once: status-gate, events + snapshot diff → deltas →
        recorder. Raises InterfaceError on transport loss; degrades +
        recovers are recorded here (FR-024)."""
        if self.state == self.UNBOUND:
            raise RuntimeError("step() before bind()")
        try:
            status = self.client.status()
            if status.get("paused"):
                return []  # paused: no cadence progress (FR-003)
            raw = self.client.get_events()
            reobserving = self.client.should_reobserve()
            snapshot = self.client.observe()
            if reobserving:
                self.client.reobserved()  # baseline consumed (full rebuild)
                self.consumer.reset()  # ring re-delivery after restart
                if self.ledger:
                    self.ledger.rebuild(snapshot)
            elif self.ledger:
                self.ledger.apply_snapshot(snapshot)
            deltas = self.consumer.consume_events(raw)
            snap_deltas = self.consumer.consume_snapshot(snapshot)
            merged = events.combine(deltas + snap_deltas)
        except InterfaceError as e:
            if self.state != self.DEGRADED_IFACE_DOWN:
                self._transition(self.DEGRADED_IFACE_DOWN, str(e))
            raise
        if self.state == self.DEGRADED_IFACE_DOWN:
            self._transition(self.BOUND_ACTIVE, "interface recovered")

        for ev in raw.get("events", []):
            self.recorder.record("event_raw", **ev)
        for d in merged:
            self.recorder.record("delta", tick=d.get("tick"),
                                 **{k: v for k, v in d.items()
                                    if k != "tick"})
        if self.ledger:
            for d in merged:
                self.ledger.apply_delta(d)

        # S1 lane: game-time cadence gate; S1 channel down → seat idles,
        # but a DEGRADED_S1_DOWN seat keeps probing to recover (FR-024)
        if self.s1_loop is not None and \
                self.state in (self.BOUND_ACTIVE, self.DEGRADED_S1_DOWN):
            paused = bool(status.get("paused"))
            last = self.client.last_tick or 0
            if self.tick_clock.ready(last, paused):
                try:
                    self.s1_loop.run_tick(observed_tick=last,
                                          paused=paused)
                except (LLMError, ChannelFailure) as e:
                    self._transition(self.DEGRADED_S1_DOWN, str(e))
                    return merged
                else:
                    if self.state == self.DEGRADED_S1_DOWN:
                        self._transition(self.BOUND_ACTIVE,
                                         "s1 recovered")
                    self.tick_clock.advance(last)
        if any(d.get("lifecycle") == "matchOver" for d in merged):
            self._transition(self.UNBOUND, "matchOver")
        return merged
        if self.client.should_reobserve():
            self.client.reobserved()  # baseline consumed by this observe

        for ev in raw.get("events", []):
            self.recorder.record("event_raw", **ev)
        for d in merged:
            self.recorder.record("delta", tick=d.get("tick"),
                                 **{k: v for k, v in d.items()
                                    if k != "tick"})
        if any(d.get("lifecycle") == "matchOver" for d in merged):
            self._transition(self.UNBOUND, "matchOver")
        return merged

    # -- tick cadence gate (model calls happen from M-B loops, not here) ----
    def tick_ready(self, paused=False):
        return self.tick_clock.ready(self.client.last_tick or 0, paused)

    def tick_cross(self):
        return self.tick_clock.advance(self.client.last_tick or 0)


def main(argv=None):
    ap = argparse.ArgumentParser(description="twin-core driver (M-A)")
    ap.add_argument("--config", default=os.path.join(
        os.path.dirname(__file__), "configs", "boot-default.json"))
    ap.add_argument("--max-cycles", type=int, default=0,
                    help="0 = run until matchOver / Ctrl-C")
    ap.add_argument("--poll-seconds", type=float, default=0.5,
                    help="transport poll pacing (gameplay uses game time)")
    args = ap.parse_args(argv)
    drv = Driver(config.load(args.config))
    drv.bind()
    cycles = 0
    while args.max_cycles <= 0 or cycles < args.max_cycles:
        try:
            drv.step()
        except InterfaceError:
            time.sleep(max(args.poll_seconds, 1.0))
        cycles += 1
        if drv.state == drv.UNBOUND:
            break
        time.sleep(args.poll_seconds)
    print(json.dumps({"state": drv.state, "match": drv.recorder.match_id}))
    return 0


if __name__ == "__main__":
    sys.exit(main())