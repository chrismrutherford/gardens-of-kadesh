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
import math
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))   # tools/ (aiplay_protocol lives there)

from twincore.config import seconds_to_ticks
from twincore.llm import S1Channel, S2Channel
from twincore.program import (ProgramError, build_micro_input,
                              build_pulse_input, packet_from_tool_call,
                              submit_program_schema, validate_micro_packet,
                              validate_packet)
import twincore.program as P
from twincore.client import Client
from twincore.recorder import Recorder, now_ms
from twincore.state_store import Ledger
from twincore.validator import Validator
from twincore.executor import Executor
import aiplay_protocol as AIP


def _coerce_ship_ids(v):
    """Models pass ships as string/json-string/scalar sometimes — coerce,
    never guess ids."""
    if v is None:
        return []
    if isinstance(v, str):
        t = v.strip()
        try:
            v = json.loads(t) if t and t[0] in "[{0123456789" else None
        except (ValueError, TypeError):
            v = None
        if isinstance(v, list):
            v = [x.get("id") if isinstance(x, dict) else x for x in v]
    if isinstance(v, (int, float)):
        v = [v]
    out = []
    for x in v if isinstance(v, list) else []:
        try:
            xi = int(x)
        except (TypeError, ValueError):
            continue
        if xi > 0:
            out.append(xi)
    return out[:64]


def _sid(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _int_(v):
    """int or None (bank/rollup values may be absent)."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _dist2(a, b3):
    """Squared distance between point a [x,y,z] and point b3 [x,y,z]."""
    ax, ay, az = (a + [0, 0, 0])[:3] if isinstance(a, list) else (0, 0, 0)
    bx, by, bz = (b3 + [0, 0, 0])[:3] if isinstance(b3, list) else (0, 0, 0)
    return (ax - bx) ** 2 + (ay - by) ** 2 + (az - bz) ** 2


IO_LOG_PATHS = {"s1": "/tmp/s1-io.log", "s2": "/tmp/s2-io.log",
                "ex": "/tmp/ex-io.log"}
ENV_KEYS = {"s1": "V4_S1_IO_LOG", "s2": "V4_S2_IO_LOG",
            "ex": "V4_EX_IO_LOG"}


def _io_append(name, text):
    """Real-time human IO stream (append + flush). Observability ONLY —
    never allowed to break gameplay (Constitution IV)."""
    import os
    path = os.environ.get(ENV_KEYS.get(name, ""), "") or \
        IO_LOG_PATHS.get(name)
    try:
        with open(path, "a") as fh:
            fh.write(text)
            fh.flush()
    except Exception:
        pass

DEFAULT_PULSE_TICKS = seconds_to_ticks(120.0)
DEFAULT_S2_IN = 12288
DEFAULT_S2_OUT = 3072
DEFAULT_MAX_REPAIRS = 1


# --------------------------------------------------------------------------
# world-section builders (FR-009 superset; shared by S2 ledger + S1 view)
# --------------------------------------------------------------------------

def own_base_pos(view, own_player=0):
    for s in (view.get("ships_all") or {}).values():
        if s.get("owner") == own_player and (
                "Mothership" in (s.get("type") or "")
                or "Carrier" in (s.get("type") or "")):
            return list(s.get("pos") or [0, 0, 0])
    return None


def enemy_lines(view, own_player=0, base=None, cap=16):
    """ENEMY CONTACTS rows: `enemy <id> <hull> pos[..] dist_to_base=<m>`
    (nearest first). Empty contacts ⇒ explicit 'none' — no fabrication."""
    base = base if base is not None else own_base_pos(view, own_player)
    rows = []
    for sid, s in sorted((view.get("ships_all") or {}).items(),
                         key=lambda kv: _sid(kv[0])):
        if s.get("owner") == own_player:
            continue
        pos = list(s.get("pos") or [0, 0, 0])
        dist = int(math.sqrt(_dist2(pos, base))) if base else -1
        rows.append((dist, "enemy %s %s pos%s dist_to_base=%d"
                     % (sid, s.get("type") or "?",
                        [int(v) for v in pos[:3]], dist)))
    rows.sort(key=lambda r: (r[0], r[1]))
    if not rows:
        return ["ENEMY CONTACTS: none observed"]
    out = [t for _d, t in rows[:cap]]
    if len(rows) > cap:
        out.append("(+%d more enemy contacts)" % (len(rows) - cap))
    return out


def asteroid_lines(view, own_player=0, base=None, top_n=32):
    """ASTEROID LEDGER rows: `ast <id> pos[..] value=<RU>` nearest-first,
    plus totals (S2 must be able to NAME harvest targets)."""
    base = base if base is not None else own_base_pos(view, own_player)
    asts = []
    for rid, r in ((view.get("resources") or {}).items()):
        pos = list(r.get("pos") or [0, 0, 0])
        val = r.get("value", r.get("ru", 0)) or 0
        dist = _dist2(pos, base) if base else 0
        asts.append((dist, int(_sid(rid)), pos, val))
    asts.sort(key=lambda t: (t[0], t[1]))
    total = sum(int(t[3]) for t in asts)
    lines = ["asteroids in view: %d (total %d RU)" % (len(asts), total)]
    for _d, rid, pos, val in asts[:max(1, int(top_n))]:
        lines.append("ast %d pos%s value=%d"
                     % (rid, [int(v) for v in pos[:3]], int(val)))
    if len(asts) > top_n:
        lines.append("(+%d more, value sorted omitted)" % (len(asts) - top_n))
    return lines


def build_menu_lines(view):
    """BUILD MENU: unlocked hulls + currently locked-with-needs + queue
    (no costs — the engine snapshot does not expose per-hull costs; we
    never fabricate)."""
    tt = view.get("techTree") or {}
    unlocked, locked = [], []
    seen_ishull = set()
    for s in (view.get("ships_all") or {}).values():
        hull = s.get("type")
        if hull:
            seen_ishull.add(hull)
    for ent in tt.get("locked") or []:
        hull = ent.get("type")
        seen_ishull.add(hull)
        needs = list(ent.get("missing") or [])
        if needs:
            locked.append((hull, needs))
        else:
            unlocked.append(hull)
    for hull in sorted(seen_ishull):
        if hull not in unlocked and hull not in [h for h, _ in locked]:
            unlocked.append(hull)     # observed hull without lock entry
    lines = []
    if unlocked:
        lines.append("buildable now: %s" % ", ".join(sorted(set(unlocked))))
    for hull, needs in sorted(locked, key=lambda t: t[0]):
        lines.append("locked: %s (needs research: %s)" % (hull, needs))
    bq = view.get("buildQueue") or []
    if bq:
        lines.append("buildQueue: %s" % json.dumps(bq, default=str)[:220])
    return lines or ["build menu: no techTree data observed"]


def research_menu_lines(view):
    """RESEARCH MENU: researchable techs (union of missing prereqs, minus
    researched) with the hulls that need them, labs and busy state."""
    tt = view.get("techTree") or {}
    researched = set(tt.get("researched") or [])
    needers = {}
    for ent in tt.get("locked") or []:
        for tech in ent.get("missing") or []:
            needers.setdefault(tech, []).append(ent.get("type"))
    researchable = sorted(set(needers) - researched)
    lines = ["labs=%s lab_busy=%s (canResearch)"
             % (tt.get("labs"), tt.get("canResearch"))]
    for tech in researchable:
        lines.append("tech %s (needed for: %s)"
                     % (tech, ", ".join(sorted(set(needers[tech])))))
    lines.append("researched: %s" % sorted(researched))
    return lines

STATIC_CHARTER = (
    "You are S2, the strategist of seat {seat}. Think as long as you need, "
    "then call the `submit_program` tool EXACTLY ONCE per pulse — its "
    "arguments are the WHOLE program packet (all-or-nothing at the tick "
    "boundary):\n"
    "  version: <int strictly greater than your previous program>\n"
    "  issued_tick: <int>\n"
    "  posture: \"<short note for your own next pulse, optional>\"\n"
    "  arc: \"<REWRITE EVERY PULSE — your running high-level story: match "
    "phase, enemy shape shown, what was tried/failed, current plan; "
    "≤600 chars>\"\n"
    "  s1_context: {\"max_exchanges\": <int 0-8>, \"ticks\": <int 60-"
    "100000>}  // optional: how much history S1 may keep\n"
    "  delegate: [ grants for S1, your fast tactical helper — or [] ]\n"
    "    {\"grant_id\": \"d1\", \"verbs\": [\"order_attack\", "
    "\"order_guard\"], \"ships\": \"scope:defense\", "
    "\"override_tasking\": true} — ships is \"scope:defense\"|"
    "\"scope:fleet\" or a list of ship ids; optional \"zone\": [cx,cy,cz,r]"
    ", \"expires_tick\": <int>\n"
    "  production: [ {\"id\": \"p1\", \"verb\": \"order_build\", "
    "\"params\": {\"type\": \"ResourceCollector\", \"quantity\": 1}} — or "
    "order_research {\"tech\": ...} with \"once\": true for one-shots ]\n"
    "  tasking: [ {\"id\": \"t1\", \"verb\": \"order_attack\", "
    "\"ships\": [104, 105], \"target\": 881} — verbs: attack move guard "
    "patrol formation halt tactics harvest dock ]\n"
    "tech and hull names MUST come from the RESEARCH MENU / BUILD MENU "
    "sections below (an unknown name is denied with a verdict naming the "
    "legal alternatives); ship/resource ids come from the LEDGER / "
    "ENEMY CONTACTS / ASTEROID LEDGER sections (match-scoped); target "
    "0/missing ids are rejected. Whole packet or nothing: any schema "
    "violation rejects everything and your previous program stays in "
    "force. Executor verdicts in PROGRAM STATUS / # VERDICTS tell you "
    "what was refused and what is currently allowed — never repeat a "
    "denied item unchanged.\n")


class PulseLoop:
    """S2 program-pulse: async two-phase, spawn-storm guarded."""

    def __init__(self, channel, executor, recorder, ledger,
                 pulse_ticks=None, s2_in=DEFAULT_S2_IN,
                 s2_out=DEFAULT_S2_OUT, max_repairs=DEFAULT_MAX_REPAIRS,
                 charter_lines=None, own_player=0, arc_chars=600,
                 asteroid_top_n=32):
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
        # FR-021 arc store: latest model-authored arc (length-clamped),
        # re-rendered into EVERY pulse input; never absent.
        self.arc_chars = int(arc_chars)
        self.arc_text = None
        self.arc_tick = None
        self.asteroid_top_n = int(asteroid_top_n)
        # mechanical rollups since last pulse (code-computed truth; FR-009)
        self._rollup_prev = None
        self.kills_since_pulse = 0
        self.next_pulse_at = self.pulse_ticks
        self._wake = False
        self.last_version = -1
        self._worker = None
        self._result = None
        self._pulse_count = 0

    # -- ARC store (FR-021) -----------------------------------------------
    def set_arc(self, arc, tick):
        """Store the LATEST arc from an accepted program; length-clamp
        with a verdict when over budget (never dropped, never rejected)."""
        if arc is None:
            return False
        text = str(arc)
        clamped = False
        if len(text) > self.arc_chars:
            text = text[:self.arc_chars]
            clamped = True
        self.arc_text = text
        self.arc_tick = tick
        return clamped

    def _arc_line(self, view):
        """The arc the pulse input ALWAYS renders (FR-021): latest stored
        arc (with its stamp — freshness, SC-006), else the code-seeded
        opening line (mechanical, no doctrine)."""
        if self.arc_text:
            return "[stored at tick %s] %s" % (self.arc_tick, self.arc_text)
        bank = view.get("bank")
        n_own = sum(1 for s in (view.get("ships_all") or {}).values()
                    if s.get("owner") == self.own_player)
        return ("(seeded by code — write yours this pulse) match opening; "
                "bank %s; own ships %d" % (bank, n_own))

    def _mechanical_rollups(self, view):
        """Rollups since the last pulse — code-computed truth (FR-009):
        bank delta, builds materialized (new own ship ids), research
        accepted (new researched techs), kills (from the event consumer).
        NO doctrine: numbers only."""
        ships = view.get("ships_all") or {}
        own_ids = {k for k, s in ships.items()
                   if s.get("owner") == self.own_player}
        researched = set((view.get("techTree") or {}).get("researched")
                         or [])
        cur = {"bank": view.get("bank"), "own": own_ids,
               "tech": researched}
        if self._rollup_prev is None:
            self._rollup_prev = cur
            return ["since last pulse: (first observation) bank %s; own "
                    "ships %d" % (view.get("bank"), len(own_ids))]
        bank0 = _int_(self._rollup_prev["bank"])
        bank1 = _int_(cur["bank"])
        new_ships = len(cur["own"] - self._rollup_prev["own"])
        new_tech = sorted(cur["tech"] - self._rollup_prev["tech"])
        kills = self.kills_since_pulse
        self.kills_since_pulse = 0
        delta = "%s%s" % ("+" if (bank0 is not None and bank1 is not None
                                  and bank1 >= bank0) else "",
                          (bank1 - bank0) if bank0 is not None
                          and bank1 is not None else "?")
        lines = ["since last pulse: bank %s→%s (%s); builds materialized: "
                 "+%d; research accepted: %s; kills (enemy lost): +%d"
                 % (bank0 if bank0 is not None else "?",
                    bank1 if bank1 is not None else "?", delta,
                    new_ships, new_tech or "none", kills)]
        self._rollup_prev = cur
        return lines

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
                                 ["alerts"], pruned=in_meta["pruned"],
                                 sections=in_meta.get("sections") or {})
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

    # -- input (FR-009 SUPERSET layout, budget FR-013) ---------------------
    def pulse_input(self, tick):
        view = self.ledger.as_dict() if self.ledger is not None else {}
        charter = self.charter_lines or [
            STATIC_CHARTER.replace("{seat}", str(self.own_player))]
        prev = self.executor.program if self.executor else None
        prev_lines, status_lines = [], []
        if prev is not None:
            for g in prev.delegate:
                exp = g.get("expires_tick")
                if exp is not None and tick > exp:
                    status_lines.append(
                        "envelope: grant %s EXPIRED (at %d, now %d) — "
                        "re-issue a wider window if S1 should act"
                        % (g["grant_id"], exp, tick))
            prev_lines = [json.dumps(prev.raw, sort_keys=True,
                                     separators=(",", ":"))]
            status_lines = [
                "%s %s cmds=%s tick=%s aux=%s%s" % (
                    s["item_id"], s["resolution"], s["command_ids"],
                    s["issued_tick"], s["aux"],
                    (" | verdict: " + s["verdict"]) if s.get("verdict")
                    else "")
                for s in (self.executor.statuses() if self.executor
                          else [])]
        base = own_base_pos(view, self.own_player)
        verdict_lines = []
        if self.executor is not None:
            verdict_lines = ["[%s] %s: %s" % (v.get("kind"), v["subject"],
                                              v["text"])
                             for v in self.executor.verdicts()]
        text, meta = build_pulse_input(
            charter_lines=charter,
            p0_items=list(self._p0_items() or []),
            ledger_lines=self._ledger_echo(view),
            rollup_lines=self._mechanical_rollups(view),
            prev_program_lines=prev_lines,
            prev_status_lines=status_lines,
            micro_lines=[], reflex_lines=[],
            budget_units=self.s2_in,
            arc_lines=[self._arc_line(view)],
            enemy_lines=enemy_lines(view, self.own_player, base),
            asteroid_lines=asteroid_lines(view, self.own_player, base,
                                          self.asteroid_top_n),
            build_menu_lines=build_menu_lines(view),
            research_menu_lines=research_menu_lines(view),
            verdict_lines=verdict_lines)
        msgs = [{"role": "system", "content": text},
                {"role": "user", "content": "tick %d: emit the JSON "
                 "program packet." % tick}]
        return msgs, meta

    def _ledger_echo(self, view):
        own = self.own_player
        lines = []
        # per-ship identity lines: ids are tasking's vocabulary (S2 must
        # be able to NAME ships; counts alone made it skip tasking)
        for sid, s in sorted((view.get("ships_all") or {}).items(),
                             key=lambda kv: _sid(kv[0])):
            if s.get("owner") != own:
                continue
            o = s.get("order")
            verb = o.get("verb") if isinstance(o, dict) else o
            pos = s.get("pos") or [0, 0, 0]
            lines.append("ship %s %s pos[%.0f,%.0f,%.0f] order=%s cargo=%s"
                         % (sid, s.get("type") or "?",
                             pos[0] if len(pos) > 0 else 0,
                             pos[1] if len(pos) > 1 else 0,
                             pos[2] if len(pos) > 2 else 0,
                             verb or "-", s.get("cargo", "-")))
        if view.get("bank") is not None:
            lines.append("bank %s" % (view["bank"],))
        # enemies/asteroids/menus render as their own SUPERSET sections
        # (FR-009); the ledger stays per-own-ship + bank.
        return lines or ["(no ships observed)"]

    def _p0_items(self):
        return list(getattr(self, "p0_items", []) or [])

    def _debug_io(self, tick, attempt, msgs, out):
        """Observability tap: when V4_DEBUG_IO is a directory, dump the
        pulse input messages + raw completion + parsed packet there.
        Observability ONLY — never gameplay-affecting (Constitution IV)."""
        import os
        d = os.environ.get("V4_DEBUG_IO")
        if not d:
            return
        try:
            os.makedirs(d, exist_ok=True)
            n = getattr(self, "_io_seq", 0) + 1
            self._io_seq = n
            base = os.path.join(d, "pulse-%s-%02d-a%d" % (tick, n, attempt))
            with open(base + ".input.json", "w") as fh:
                json.dump(msgs, fh, indent=1, default=str)
            with open(base + ".raw.txt", "w") as fh:
                raw = out.get("raw")
                if isinstance(raw, dict):
                    fh.write("[content]\n%s\n\n[reasoning]\n%s"
                             % (raw.get("content"), raw.get("reasoning")))
                else:
                    fh.write(str(raw or ""))
            with open(base + ".packet.json", "w") as fh:
                json.dump(out.get("packet") or {"error": out.get("error")},
                          fh, indent=1, default=str)
        except Exception:
            pass   # observability must never break the driver

    def _call(self, msgs, tick):
        try:
            self._call_inner(msgs, tick)
        except Exception as e:   # worker crash → visible failure, never a
            # ghost pulse (FR-012: every transition recorded)
            self._io_log_block(tick, [], {"error": str(e)}, None)
            self._wake = False
            self._result = {"ok": False, "program": None,
                            "error": "pulse worker crashed: %s" % e,
                            "tick": tick, "pulse": self._pulse_count}
            if self.recorder:
                self.recorder.record("packet_rejected", tick=tick,
                                     reason=self._result["error"],
                                     pulse=self._pulse_count)
        return self._result

    def _io_log_block(self, tick, msgs, out, extra):
        """Append the full S2 exchange to the s2 io stream (real-time)."""
        raw = (out or {}).get("raw") if isinstance(out, dict) else None
        raw = raw or {}
        block = ("\n=== S2 | tick=%s pulse=%s ===\n[INPUT]\n%s\n"
                 "[RAW content]\n%s\n[RAW reasoning]\n%s\n"
                 "[finish=%s ctok=%s]\n[PARSED]\n%s\n%s" % (
                     tick, self._pulse_count,
                     json.dumps(msgs, indent=1, default=str)[:60000],
                     str(raw.get("content") or "<empty>")[:60000],
                     str(raw.get("reasoning") or "<none>")[:60000],
                     raw.get("finish_reason"), raw.get("completion_tokens"),
                     json.dumps(P.packet_from_tool_call(out.get("calls"))
                                or out.get("packet"),
                                indent=1, default=str)[:20000],
                     extra or ""))
        _io_append("s2", block)

    def _call_inner(self, msgs, tick):
        attempts = 1 + max(0, int(self.max_repairs))
        reason = None
        for attempt in range(1, attempts + 1):
            out, usage = self.channel.propose(
                msgs, tools=[P.submit_program_schema()])
            self._io_log_block(tick, msgs, out, "attempt=%d/%d"
                               % (attempt, attempts))
            if self.recorder:
                self.recorder.record("usage", tick=tick, channel="s2_out",
                                     prompt=usage.get("prompt"),
                                     completion=usage.get("completion"))
            packet = P.packet_from_tool_call(out.get("calls"))
            if out.get("error"):
                # FR-012: a TRANSPORT failure (connection/503/empty
                # stream) is not a model mistake — it must NOT consume
                # the bounded repair (live lesson). Abort; the next
                # scheduled or P0-woken pulse retries.
                reason = out.get("error")
                if reason and reason.startswith("strategist channel error"):
                    break
            elif packet is not None:
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
            elif not (out.get("calls") or []):
                reason = "no submit_program tool call — call submit_program " \
                         "EXACTLY once per pulse with the whole program " \
                         "(version, production, tasking, delegate, arc)"
            else:
                reason = "submit_program arguments were not one parseable " \
                         "packet (malformed tool-call args JSON)"
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


MICRO_CHARTER = (
    "You are S1, the fast tactical model of seat {seat}. You react EVERY "
    "{cadence}s within the delegation envelope your strategist licensed. "
    "Reply ONLY with tool calls using the provided order tools (tasking "
    "verbs). Every tool call MUST pass ships as an ARRAY of integer ship "
    "ids from the LIVE VIEW (e.g. ships: [2, 3]); a call with no valid "
    "ship ids is dropped. Ship ids come from the LIVE VIEW. If nothing "
    "needs reacting "
    "to, reply with NO tool calls. Production/build tools are NOT yours "
    "unless explicitly granted."
)

S1_TASKING_VERBS = ("order_attack", "order_move", "order_guard",
                    "order_patrol", "order_formation", "order_halt",
                    "order_tactics", "order_harvest", "order_dock")


def _s1_tools():
    tools = []
    for verb in S1_TASKING_VERBS:
        spec = (AIP.REGISTRY.get("order_tools") or {}).get(verb) or {}
        tools.append({"type": "function", "function": {
            "name": verb,
            "description": spec.get("desc", verb),
            "parameters": {"type": "object", "properties": {},
                           "additionalProperties": True}}})
    return tools


class S1MicroLoop:
    """S1 micro-pulse: async two-phase like the S2 loop, shorter cadence.
    Tool-call output → envelope-validated micro items → executor micro
    lane (commands cite s1:mN). Violations recorded, never executed
    (FR-018)."""

    def __init__(self, channel, executor, recorder, ledger,
                 micro_pulse_ticks=128, s1_in=4096, charter_lines=None,
                 own_player=0):
        from twincore.llm import LLMError
        self.channel = channel
        self.executor = executor
        self.recorder = recorder
        self.ledger = ledger
        self.micro_pulse_ticks = micro_pulse_ticks
        self.s1_in = s1_in
        self.charter_lines = list(charter_lines or [])
        self.own_player = own_player
        self.next_pulse_at = self.micro_pulse_ticks
        self.seq = 0
        self.last_seq = 0
        self._wake = False
        self._worker = None
        self._result = None
        self._tools = _s1_tools()
        self._llm_error_cls = LLMError
        # FR-022: rolling micro-history (S2-allocated window); entries are
        # {"tick","seq","applied","blocked","note"} — truncated to the
        # CURRENT program's s1_context at every micro_input().
        self._history = []

    def due(self, tick, paused=False):
        if paused:
            return False
        if self._wake:
            return True
        if self._worker is not None and self._worker.is_alive():
            return False
        return tick >= self.next_pulse_at

    def wake(self):
        self._wake = True

    def start_micro(self, tick, paused=False):
        if paused:
            return False
        self.next_pulse_at = tick + self.micro_pulse_ticks
        if self._worker is not None and self._worker.is_alive():
            return False
        msgs, meta = self.micro_input(tick)
        if self.recorder:
            self.recorder.record("usage", tick=tick, channel="s1_in",
                                 total=meta["units"], alerts=meta["alerts"])
        self._result = None
        self._worker = threading.Thread(
            target=self._call, args=(msgs, tick), daemon=True)
        self._worker.start()
        return True

    def harvest(self, timeout=None):
        if self._worker is None:
            return None
        if timeout is not None:
            self._worker.join(timeout)
        if self._worker.is_alive():
            return None
        self._worker = None
        return self._result

    def micro_input(self, tick):
        view = self.ledger.as_dict() if self.ledger is not None else {}
        prog = self.executor.program if self.executor else None
        if self.charter_lines:
            charter = list(self.charter_lines)
        else:
            cadence = self.micro_pulse_ticks // 16
            charter = [MICRO_CHARTER.replace("{seat}", str(self.own_player))
                       .replace("{cadence}", str(max(1, cadence)))]
        grants = json.dumps(prog.delegate, sort_keys=True) \
            if prog is not None else "[]"
        if prog is None or not prog.delegate:
            grants = ("NO ACTIVE LICENSE — advisory-only, reply with no "
                      "tool calls: %s" % grants)
        status_lines = ["%s %s cmds=%s%s" % (
            s["item_id"], s["resolution"], s["command_ids"],
            (" | verdict: " + s["verdict"]) if s.get("verdict") else "")
            for s in (self.executor.statuses() or [])
            if s["item_id"].startswith("s1:")]
        verdict_lines = ["[%s] %s: %s" % (v.get("kind"), v["subject"],
                                          v["text"])
                         for v in self.executor.verdicts()]
        history_lines = self._history_lines(prog, tick)
        text, meta = build_micro_input(
            charter_lines=charter,
            p0_items=[],
            envelope_lines=[grants] + self._grant_status(prog, tick),
            live_lines=self._live_view(view, prog),
            prev_micro_lines=[],
            prev_status_lines=status_lines,
            budget_units=self.s1_in,
            verdict_lines=verdict_lines,
            history_lines=history_lines)
        msgs = [{"role": "system", "content": text},
                {"role": "user", "content": "tick %d: react or stay "
                 "silent." % tick}]
        return msgs, meta

    def _history_lines(self, prog, tick):
        """The S1 rolling history window: at most s1_context.max_exchanges
        entries spanning s1_context.ticks (mechanical truncation, FR-022).
        Absent s1_context ⇒ stateless (empty lines)."""
        ctx = (prog.s1_context if prog is not None else None) or None
        if not ctx:
            self._history = []
            return []
        max_exc = ctx.get("max_exchanges")
        ticks_span = ctx.get("ticks")
        keep = []
        for h in reversed(self._history):
            if max_exc is not None and len(keep) >= max_exc:
                break
            if ticks_span is not None and tick - h["tick"] > ticks_span:
                continue
            keep.append(h)
        keep.reverse()
        self._history = keep          # oldest-first, ready for append
        return ["seq=%s tick=%s applied=%d blocked=%d %s"
                % (h["seq"], h["tick"], h["applied"], h["blocked"],
                   h.get("note") or "")
                for h in self._history]

    def _grant_status(self, prog, tick):
        """License state made explicit: expired grants must reach BOTH
        models' next inputs (S2 re-issues windows, S1 stays silent)."""
        out = []
        if prog is None:
            return out
        for g in prog.delegate:
            exp = g.get("expires_tick")
            if exp is not None and tick > exp:
                out.append("grant %s EXPIRED (at %d, now %d) — no license "
                           "until the strategist renews it"
                           % (g["grant_id"], exp, tick))
        if not any(g.get("expires_tick") is not None for g in
                   prog.delegate):
            out.append("grants have no expiry window")
        return out

    def _live_view(self, view, prog=None, tick=None):
        """Tactical view: own ships with positions, base distance, scope
        membership, plus enemy contacts (id/type/pos/dist)."""
        g = (prog.delegate[0] if prog and prog.delegate else None) or {}
        zone = g.get("zone")
        cx, cy, cz = (zone[0], zone[1], zone[2]) if zone else \
            (None, None, None)
        lines = []
        base = None
        own = []
        enemies = []
        for sid, s in sorted((view.get("ships_all") or {}).items(),
                             key=lambda kv: _sid(kv[0])):
            pos = s.get("pos") or [0, 0, 0]
            if s.get("owner") != self.own_player:
                enemies.append((sid, s, pos))
                continue
            s = dict(s, id=sid)
            own.append(s)
            if "Mothership" in (s.get("type") or "") or \
                    "Carrier" in (s.get("type") or ""):
                base = pos
        d2 = _dist2
        for s in own:
            pos = s.get("pos") or [0, 0, 0]
            o = s.get("order")
            verb = o.get("verb") if isinstance(o, dict) else o
            war = "ResourceCollector" not in (s.get("type") or "")
            near = (d2(pos, [cx, cy, cz]) <= zone[3] ** 2) if zone else \
                (d2(pos, base) <= 22500 ** 2) if (war and base) else None
            tag = ("in-scope" if near else "out-of-scope") if near is not None \
                and war else ""
            dist = int(d2(pos, base) ** 0.5) if base else -1
            lines.append("ship %s %s pos%s %s%s dist_to_base=%s"
                         % (s["id"], s.get("type") or "?",
                            [int(v) for v in pos],
                            ("order=%s" % (verb or "none")),
                            (" [%s]" % tag) if tag else "", dist))
        if enemies:
            lines.append("ENEMY CONTACTS:")
            for sid, s, pos in enemies[:12]:
                dist = int(d2(pos, base) ** 0.5) if base else -1
                lines.append("  enemy %s %s pos%s dist_to_base=%s"
                             % (sid, s.get("type") or "?",
                                [int(v) for v in pos[:3]], dist))
        else:
            lines.append("ENEMY CONTACTS: none")
        lines.append("resources %d" % len(view.get("resources") or {}))
        return lines

    # -- the call: tool calls → envelope-validated micro items ---------------
    def _call(self, msgs, tick):
        self._io_log_block(tick, msgs, None, "s1 pulse (awaiting calls)")
        try:
            calls, usage = self.channel.propose(msgs, self._tools)
            self._io_log_block(tick, [], {"raw": getattr(
                self.channel, "last_raw", None)}, "s1 calls=%s" %
                json.dumps(calls, default=str)[:4000])
            if self.recorder:
                self.recorder.record("usage", tick=tick, channel="s1_out",
                                     prompt=usage.get("prompt"),
                                     completion=usage.get("completion"))
        except Exception as e:   # LLMError or transport: degrade (FR-012)
            self._result = {"ok": False, "error": str(e), "seq": None,
                            "tick": tick}
            if self.recorder:
                self.recorder.record("packet_rejected", tick=tick,
                                     reason="s1 channel: %s" % e)
            self._io_log_block(tick, [], None, "s1 channel error: %s" % e)
            return self._result
        prog = self.executor.program if self.executor else None
        items = self._items_from_calls(calls, prog)
        self.seq += 1
        try:
            applied, blocked = validate_micro_packet(
                {"seq": self.seq, "program_version":
                 (prog.version if prog is not None else 0),
                 "tasking": items}, prog, last_seq=self.last_seq, tick=tick)
            self.last_seq = self.seq
            # FR-020: envelope blocks TEACH — verdict names the licensed
            # verbs/ships instead of dropping by silence.
            for iid, reason in blocked:
                allowed = self._allowed_for(prog)
                self.executor.add_verdict(
                    "deny", "s1:%s" % iid, "%s; allowed: %s"
                    % (reason, allowed), tick)
            if applied:
                pass   # executor queues in-envelope items (driver on_tick)
            self._history.append({
                "tick": tick, "seq": self.seq, "applied": len(applied),
                "blocked": len(blocked),
                "note": "program_version=%s" % (
                    prog.version if prog is not None else 0)})
            self._trim_history(prog, tick)
            self._result = {"ok": True, "applied": applied,
                            "blocked": blocked, "seq": self.seq,
                            "tick": tick}
            if self.recorder:
                self.recorder.record("micro", tick=tick, event="applied",
                                     seq=self.seq, n=len(applied),
                                     blocked=len(blocked),
                                     program_version=(prog.version if
                                                      prog else None))
            return self._result
        except ProgramError as e:
            self._result = {"ok": False, "error": e.reason,
                            "seq": self.seq, "tick": tick}
            self.executor.add_verdict(
                "deny", "s1:packet",
                "micro-packet rejected: %s; reply with tasking tool calls "
                "with ships arrays from the LIVE VIEW" % e.reason, tick)
            self._history.append({
                "tick": tick, "seq": self.seq, "applied": 0, "blocked": 0,
                "note": "rejected: %s" % e.reason})
            self._trim_history(prog, tick)
            if self.recorder:
                self.recorder.record("micro", tick=tick, event="rejected",
                                     seq=self.seq, reason=e.reason,
                                     program_version=(prog.version if
                                                      prog else None))
            return self._result

    def _allowed_for(self, prog):
        """The active license in one legible phrase (FR-020): grant verbs
        + the mechanically-resolved scope ship ids."""
        if prog is None or not prog.delegate:
            return "no active license — advisory-only"
        out = []
        for g in prog.delegate:
            ships = g["ships"]
            if isinstance(ships, str):
                resolved = self.executor._scope_ships(ships, 0) \
                    if self.executor is not None else []
                ships = "%s (%s)" % (ships, resolved or "none resolved")
            out.append("grant %s: verbs %s on ships %s"
                       % (g["grant_id"], g["verbs"], ships))
        return "; ".join(out)

    def _trim_history(self, prog, tick):
        ctx = (prog.s1_context if prog is not None else None)
        max_exc = ctx.get("max_exchanges") if ctx else 0
        if max_exc:
            self._history = self._history[-max_exc:]
        else:
            self._history = []

    def _items_from_calls(self, calls, prog, tick=None):
        items = []
        for i, c in enumerate(calls or []):
            verb = c.get("verb")
            if verb not in S1_TASKING_VERBS:
                # FR-020: attempted production tools TEACH too — they are
                # S2's decision, never silently swallowed
                import twincore.program as _P
                if verb in _P.PRODUCTION_VERBS:
                    self.executor.add_verdict(
                        "deny", "s1:m%d" % (i + 1),
                        "%r is a PRODUCTION verb — build/research are the "
                        "strategist's decisions (S2); S1 issue tasking "
                        "verbs only (see # ENVELOPE grants)" % verb,
                        tick)
                continue
            params = dict(c.get("params") or {})
            if not params:
                # FR-020: param-less calls (Qwen guard-spam) TEACH too
                self.executor.add_verdict(
                    "deny", "s1:params",
                    "call %r with NO params dropped — pass ships as an "
                    "ARRAY of ship ids from the LIVE VIEW, or reply with "
                    "no tool calls" % verb, tick)
                continue
            item = {"id": "m%d" % (i + 1), "verb": verb}
            gid = self._licensing_grant(verb, prog)
            if gid:
                item["grant"] = gid
            ships_raw = params.get("ships")
            ships = _coerce_ship_ids(ships_raw)
            if ships_raw is not None and not isinstance(ships_raw, list) \
                    and ships:
                self.executor.add_verdict(
                    "coerce", "s1:m%d" % (i + 1),
                    "ships %s coerced to array %s (always pass an array "
                    "of ship ids)" % (ships_raw, ships), tick)
            if ships:
                item["ships"] = ships
            for k in ("target", "tactics"):
                if params.get(k) is not None:
                    item[k] = params[k]
            if params.get("dockWith") is not None:
                item["target"] = params["dockWith"]
            if params.get("pos") is not None:
                item["pos"] = list(params["pos"])
            items.append(item)
        return items

    @staticmethod
    def _licensing_grant(verb, prog):
        if prog is None:
            return None
        for g in prog.delegate:
            if verb in g["verbs"]:
                return g["grant_id"]
        return None

    def _io_log_block(self, tick, msgs, out, extra):
        """Append the S1 exchange to the s1 io stream (real-time)."""
        raw = (out or {}).get("raw") if isinstance(out, dict) else None
        raw = raw or {}
        block = ("\n=== S1 | tick=%s seq=%s ===\n[INPUT]\n%s\n"
                 "[RAW content]\n%s\n[RAW reasoning]\n%s\n%s" % (
                     tick, self.seq,
                     json.dumps(msgs, indent=1, default=str)[:30000],
                     str(raw.get("content") or "<empty>")[:20000],
                     str(raw.get("reasoning") or "<empty>")[:20000],
                     extra or ""))
        _io_append("s1", block)


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
                 charter_lines=None, own_player=0, s1_channel=None,
                 micro_pulse_ticks=128, s1_in=4096,
                 s1_charter_lines=None, arc_chars=600, asteroid_top_n=32):
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
                              own_player=own_player, arc_chars=arc_chars,
                              asteroid_top_n=asteroid_top_n)
        self.s1_loop = None
        if s1_channel is not None:
            self.s1_loop = S1MicroLoop(
                channel=s1_channel, executor=self.executor,
                recorder=self.recorder, ledger=self.ledger,
                micro_pulse_ticks=micro_pulse_ticks, s1_in=s1_in,
                charter_lines=s1_charter_lines, own_player=own_player)
        self.model_state = "online"
        # engine-event consumer (FR-023): dedupe ring (the engine event
        # log re-delivers history; session-004 lesson), applied/denied
        # mapping, kill counting for mechanical rollups.
        self._events_seen = set()

    def s1_state(self):
        return "online" if self.s1_loop else "off"

    # -- binding (FR-002: exactly ONE owner) --------------------------------
    def bind(self):
        if self.client is not None:
            res = self.client.bind(self.own_player)
            if isinstance(res, dict) and res.get("error"):
                raise RuntimeError("bind failed: %s"
                                   % res["error"].get("message", res["error"]))
            return res
        return None

    # -- the game-time tick --------------------------------------------------
    def on_tick(self, tick, paused=False):
        """One driver tick: harvest a finished pulse → apply WHOLE at this
        boundary → run the executor → schedule the next pulse."""
        if paused:
            return []
        evs = self._poll_events()
        if evs is not None:
            self.consume_events(evs, tick=tick)
        # small grace join: collect a worker that finished between ticks
        # (50 ms cap — material lag keeps the tick loop unblocked, FR-007)
        done = self.loop.harvest(timeout=0.05)
        if done is not None:
            self._digest_pulse_result(done, tick)
        if self.s1_loop is not None:
            micro = self.s1_loop.harvest(timeout=0.05)
            if micro is not None and micro.get("ok"):
                self.executor.feed_micro(micro["applied"], None,
                                         micro["seq"], tick)
        issued = self.executor.on_tick(tick)
        if self.loop.due(tick, paused=False):
            self.loop.start_pulse(tick)
        if self.s1_loop is not None and self.s1_loop.due(
                tick, paused=False):
            self.s1_loop.start_micro(tick)
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
        clamped = self.loop.set_arc(program.arc, tick)
        if clamped and self.recorder:
            self.executor.add_verdict(
                "clamp", "arc", "arc over %d chars — trimmed; keep the "
                "running story tighter" % self.loop.arc_chars)
        if self.recorder:
            self.recorder.record(
                "program", tick=tick, event="applied",
                version=program.version,
                prev_version=(prev.version if prev else None),
                items=[i["id"] for i in
                       program.production + program.tasking],
                has_envelope=program.has_envelope(),
                posture=program.posture,
                arc=bool(program.arc),
                s1_context=program.s1_context)

    def _transition(self, to_state, reason):
        prev = self.model_state
        self.model_state = to_state
        if self.recorder:
            self.recorder.record("degradation", tick=None,
                                 **{"from": prev, "to": to_state,
                                    "reason": reason})

    # -- events (FR-023: issued ≠ accepted) ----------------------------------
    def consume_events(self, events, tick=None):
        """Fold engine events into statuses/verdicts/rollups. Dedupe key:
        (tick, type, id, aux, owner) — the ring re-delivers history."""
        n = 0
        for ev in events or []:
            key = (ev.get("tick"), ev.get("type"), ev.get("id"),
                   ev.get("aux"), ev.get("owner"))
            if key in self._events_seen:
                continue
            self._events_seen.add(key)
            if len(self._events_seen) > 2048:
                self._events_seen = set(sorted(self._events_seen,
                                               key=repr)[-1024:])
            etype = ev.get("type")
            if etype == "shipDestroyed":
                try:
                    killer = int(ev.get("aux", 0)) - 1
                    victim = int(ev.get("owner", self.own_player))
                except (TypeError, ValueError):
                    continue
                if killer == self.own_player and victim != self.own_player:
                    self.loop.kills_since_pulse += 1
                elif victim == self.own_player:
                    pass   # P0 wake wiring owns own-losses (T014 hooks)
                continue
            if etype in ("orderRejected", "orderApplied"):
                if self.executor.on_engine_event(ev, tick=tick):
                    n += 1
        return n

    def _poll_events(self):
        """Poll the engine event ring (observability-safe; never breaks
        the tick loop)."""
        if self.client is None or not hasattr(self.client, "get_events"):
            return None
        try:
            res = self.client.get_events()
            return (res or {}).get("events") or []
        except Exception:
            return None

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
            thinking=_cfgget(config, "channels.s2.thinking", False),
            # GLM-style models reason in reasoning_content BEFORE the JSON
            # deliverable — the completion budget must fit the CoT + the
            # packet (live lesson: 12k choked mid-CoT, [PARSED] null).
            request_max_tokens=int(_cfgget(config,
                                           "channels.s2.max_tokens", 0)
                                   or 0) or None)
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
                        reflexes=reflexes,
                        verdict_budget=int(v4.get("verdict_chars", 200)),
                        verdict_feed_k=int(v4.get("verdict_feed_k", 6)))
    drv = V4Driver(client=client, channel=channel, recorder=recorder,
                   ledger=ledger, executor=executor, validator=validator,
                   pulse_ticks=pulse_ticks,
                   s2_in=int(budgets.get("s2_in", DEFAULT_S2_IN)),
                   s2_out=int(budgets.get("s2_out", DEFAULT_S2_OUT)),
                   max_repairs=int(v4.get("max_repair_attempts",
                                          DEFAULT_MAX_REPAIRS)),
                   own_player=seat,
                   arc_chars=int(v4.get("arc_chars", 600)),
                   asteroid_top_n=int(v4.get("asteroid_top_n", 32)))
    if bool((_cfgget(config, "v4.s1_channel.enabled", True))):
        url1 = _cfgget(config, "channels.s1.url") or ""
        if url1:
            drv.s1_loop = S1MicroLoop(
                channel=S1Channel(
                    url=url1, model=_cfgget(config, "channels.s1.model", ""),
                    max_output=int(_cfgget(config, "channels.s1.max_output",
                                           400)),
                    timeout=float(_cfgget(config, "channels.s1.timeout_seconds",
                                          60.0)),
                    thinking=_cfgget(config, "channels.s1.thinking", False)),
                executor=drv.executor, recorder=recorder, ledger=ledger,
                micro_pulse_ticks=seconds_to_ticks(
                    v4.get("micro_pulse_seconds", 8)),
                s1_in=int(budgets.get("s1_in", 4096)), own_player=seat)
    drv._v4cfg = dict(v4)
    return drv


def main(argv=None):
    """CLI: python -m tools.twincore.v4driver --config <config.json>"""
    import argparse
    import faulthandler
    import os
    import sys
    faulthandler.enable()
    faulthandler.dump_traceback_later(120, repeat=True)  # wedge evidence
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

