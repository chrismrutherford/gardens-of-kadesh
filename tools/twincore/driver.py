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


def _fresh_digest_state():
    return {"p0": [], "rollups": [], "changes": [], "order_status": []}


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
        # postgres record store (FR-021/FR-030): match id unique per RUN so
        # restarts never interleave trails
        match = match_id or "seat%d-%d" % (player, recorder.now_ms()
                                           if hasattr(recorder, "now_ms")
                                           else int(__import__("time").time()))
        self.recorder = recorder.Recorder(
            match_id=match,
            dsn=cfg.get("recorder.dsn") or recorder.DEFAULT_DSN)
        self.match_id = match
        self.consumer = events.DeltaConsumer()
        self.tick_clock = GameClock(cfg.get("cadence.tick_ticks"))
        self._match_over = False
        self.pulse_seq = 0
        self._last_pulse_tick = None
        self._digest_state = _fresh_digest_state()
        # exchange inbox (FR-030): polling wake/note one-shot; never a
        # command path. Absent/degraded postgres → None (no mailbox).
        import twincore.queue as _q
        try:
            self.queue = _q.Queue(cfg.get("recorder.dsn") or
                                  recorder.DEFAULT_DSN)
        except Exception:
            self.queue = None
        # M-B wiring: ledger + S1 loop built from config when a channel URL
        # exists; injection point for tests/degradation runs (D10)
        self.ledger = None
        self.validator = None
        self.s1_loop = None
        self.s2_loop = None
        self.sticky_gate = None
        self._wire_s1()
        self._wire_s2()

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

    def _wire_s2(self):
        """Strategist lane (M-C): S2 channel + sticky gate + digest window.
        Wired only when strategist.enabled AND a channel URL exists."""
        if self.s2_loop is not None or not self.cfg.get(
                "strategist.enabled"):
            return self.s2_loop
        url = self.cfg.get("channels.s2.url")
        if not url:
            return None
        import twincore.s2pulse as s2pulse
        import twincore.llm as llm
        from twincore.directive import StickyGate
        self.sticky_gate = StickyGate(
            evidence_min=self.cfg.get("sticky.evidence_min"),
            hold_ticks=self.cfg.get("sticky.hold_ticks"), since_tick=0)
        channel = llm.S2Channel(
            url, model=self.cfg.get("channels.s2.model", ""),
            max_output=self.cfg.get("channels.s2.max_output", 2048),
            temperature=self.cfg.get("channels.s2.temperature", 0.4),
            timeout=self.cfg.get("channels.s2.timeout_seconds", 120.0),
            thinking=self.cfg.get("channels.s2.thinking"),
            request_max_tokens=self.cfg.get("channels.s2.max_tokens"))
        self.s2_loop = s2pulse.S2PulseLoop(
            ledger=self.ledger, channel=channel, recorder=self.recorder,
            sticky=self.sticky_gate,
            pulse_ticks=self.cfg.get("cadence.pulse_ticks"),
            window_pulses=self.cfg.get("digest.window_pulses"),
            s2_in=self.cfg.get("budgets.s2_in"),
            s2_out=self.cfg.get("budgets.s2_out"))
        return self.s2_loop

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
        if reobserving:
            self.recorder.record(
                "decision", tick=self.client.last_tick or 0,
                mode="reobserve",
                note="ledger rebuilt; stale-id in-flight orders cleared "
                     "(re-issued on demand, never assumed busy)")

        paused = bool(status.get("paused"))
        last = self.client.last_tick or 0
        self._poll_inbox(last)
        if self.ledger:
            self._classify_step(merged, last)
        # S1 lane: game-time cadence gate; runs in BOUND_ACTIVE and
        # DEGRADED_S2_DOWN (strategist down ⇒ S1 keeps last directive,
        # FR-024); a DEGRADED_S1_DOWN seat keeps probing to recover
        if self.s1_loop is not None and \
                self.state in (self.BOUND_ACTIVE, self.DEGRADED_S1_DOWN,
                               self.DEGRADED_S2_DOWN):
            if self.tick_clock.ready(last, paused):
                try:
                    self.s1_loop.run_tick(observed_tick=last,
                                          paused=paused)
                except (LLMError, ChannelFailure) as e:
                    self._transition(self.DEGRADED_S1_DOWN, str(e))
                else:
                    if self.state == self.DEGRADED_S1_DOWN:
                        self._transition(self.BOUND_ACTIVE,
                                         "s1 recovered")
                    self.tick_clock.advance(last)
                    if self.state == self.BOUND_ACTIVE:
                        self._post_tick(last)

        # S2 lane (async two-phase, US2-3): harvest any landed packet FIRST,
        # then spawn when due. Cadence anchors at SPAWN (a failed/lagging
        # call cannot re-due the same boundary → no spawn storms when the
        # model server is reloading).
        if self.s2_loop is not None and \
                self.state in (self.BOUND_ACTIVE, self.DEGRADED_S1_DOWN,
                               self.DEGRADED_S2_DOWN):
            try:
                landed, res = self.s2_loop.harvest_pulse(last)
            except LLMError as e:
                self._transition(self.DEGRADED_S2_DOWN, str(e))
            else:
                if landed and self.state == self.DEGRADED_S2_DOWN:
                    self._transition(self.BOUND_ACTIVE, "s2 recovered")
            if not self.s2_loop.worker_alive() and \
                    self.s2_loop.due(last, paused):
                try:
                    self._pulse_digest(last)   # digest feeds the pulse
                except Exception as e:         # digest gap ≠ seat down
                    self.recorder.record("decision", tick=last,
                                         mode="digest_gap", reason=str(e))
                try:
                    self.s2_loop.start_pulse(last, paused=paused)
                except LLMError as e:
                    self._transition(self.DEGRADED_S2_DOWN, str(e))
                else:
                    if self.state == self.DEGRADED_S2_DOWN:
                        self._transition(self.BOUND_ACTIVE, "s2 recovered")

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



    def _classify_step(self, merged, observed_tick):
        """Digest feed (M-C): classification → P0 tracking → wake routing.
        Must never stall the tick loop (digest failures recorded only)."""
        try:
            from twincore import digest as DIG
            view = self.ledger.as_dict() if self.ledger else None
            for c in DIG.classify_deltas(merged, view, DIG.default_rules()):
                line = dict(c)
                line["event_kind"] = line.pop("kind")
                self.recorder.record("classification", **line)
                if c["class"] == "P0" and c.get("rule") != "match_over":
                    self._digest_state["p0"].append(
                        {"tick": c.get("tick"), "kind": c["rule"],
                         "detail": c.get("detail"), "outcome": "open"})
                    if self.cfg.get("cadence.p0_wake") and self.s2_loop:
                        self.s2_loop.wake()
                for key in ("created", "damaged", "healed", "vanished"):
                    got = c.get("detail", {}).get(key)
                    if got:
                        self._digest_state["changes"].append(
                            "+%s %s" % (key, got if not isinstance(
                                got, list) else len(got)))
            self._digest_state["rollups"].append(
                DIG.rollup(merged,
                           self.cfg.get("digest.rollup_interval_ticks")))
        except Exception as e:
            self.recorder.record("decision", tick=observed_tick,
                                 mode="classification_gap", reason=str(e))

    def _poll_inbox(self, tick):
        """Exchange-lane poll (FR-030): consume wake/note messages; wake
        triggers the strategist's out-of-cycle pulse; notes are logged.
        NOT a command path (FR-002)."""
        if self.queue is None:
            return
        try:
            msgs = self.queue.consume_inbox(
                "inbox:seat%d" % self.cfg.get("seat.player_index"),
                match_id=self.match_id)
        except Exception as e:
            self.recorder.record("decision", tick=tick, mode="inbox_gap",
                                 reason=str(e))
            return
        for m in msgs:
            self.recorder.record(
                "note", tick=tick, sender=m.get("sender"),
                message_kind=m.get("kind"), payload=m.get("payload"),
                consumed=True)
            if m.get("kind") == "wake" and self.s2_loop is not None:
                self.s2_loop.wake()

    def _run_s2_pulse(self, tick):
        """Digest build + strategist pulse (M-C integration)."""
        try:
            self._pulse_digest(tick)
        except Exception as e:  # digest building must never stall the seat
            self.recorder.record("decision", tick=tick, mode="digest_gap",
                                 reason=str(e))
        return self.s2_loop.run_pulse(observed_tick=tick)

    def _post_tick(self, tick):
        """Standing-order lifecycle per tick (T018): TTL / veto / allies."""
        try:
            self.s1_loop.evaluate_standing_orders(tick)
        except Exception as e:
            self.recorder.record("decision", tick=tick,
                                 mode="standing_order_gap", reason=str(e))

    def _pulse_digest(self, tick):
        """Build the strategist digest from the interval + record it."""
        from twincore.digest import build_digest
        now = tick
        last = self._last_pulse_tick
        self.pulse_seq += 1
        dg = build_digest(
            pulse_id=self.pulse_seq,
            tick_range=(last if last is not None else 0, now),
            p0_items=self._digest_state["p0"],
            rollups=self._digest_state["rollups"],
            changes=self._digest_state["changes"],
            order_status=self._drain_order_status(),
            s1_notes=self._drain_s1_notes(),
            ledger=self.ledger.as_dict() if self.ledger else {},
            budget=(self.cfg.get("budgets.digest_min"),
                    self.cfg.get("budgets.digest_max")))
        self._digest_state = _fresh_digest_state()
        self.recorder.record("digest", tick=now, **dg)
        self.s2_loop.digests.append(dg)
        overflow = len(self.s2_loop.digests) - \
            self.cfg.get("digest.window_pulses")
        if overflow > 0:
            del self.s2_loop.digests[:overflow]
        self._last_pulse_tick = now

    def _drain_order_status(self):
        got = list(self._digest_state["order_status"] or [])
        if self.s1_loop is not None and \
                hasattr(self.s1_loop, "order_status_feed"):
            got = got + list(self.s1_loop.order_status_feed())
        if self.ledger:
            for rej in self.ledger.last_rejections():
                got.append({"order": rej.get("id"),
                            "status": "orderRejected",
                            "reason": "engine aux enum %s" % rej.get("aux")})
            self.ledger._s["last_rejections"] = []
        return got

    def _drain_s1_notes(self):
        if self.s1_loop is not None and \
                hasattr(self.s1_loop, "s1_notes_feed"):
            return self.s1_loop.s1_notes_feed()
        return []


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