"""Spec 004 twin-core: S2 strategist pulse loop (T017, protocol §2).

Cadence in game time (30-90s config) with immediate async wake on P0
(FR-011/FR-019); pause-safe. One pulse = one ControlPacket attempt
(≤1 bounded repair), validated+applied by packet.py at the boundary.
Input ≤24k units, static-first: [charter][ledger projections][pinned goals
with age/hold timers][last-M digests][own last packet + outcomes][answers]
— answers come from the LEDGER ONLY (FR-014), unknown questions answered
honestly with "no ledger source". Questions are queued from the accepted
packet and answered at the next pulse.
"""

CHARTER = (
    "/no_think\n"
    "You are the strategist (S2) of a twin-core Homeworld CPU player.\n"
    "You NEVER touch the game: no raw events, no commands, no binding.\n"
    "You shape the tactical core (S1) exclusively through control packets:\n"
    "- directive.patch: semantic edits (dotted paths; no wholesale rewrites)\n"
    "- orders[]: standing orders (add/revoke with abort conditions)\n"
    "- history_op: trim or reset S1's conversation when it drifts\n"
    "- digest_config: emphasis/suppression for the next digest cycle\n"
    "- questions: answered next pulse from the canonical ledger\n"
    "Sticky goals: primary-enemy changes need digest evidence across 2+ "
    "distinct pulses plus a hold time; uncited changes are dropped with "
    "an audit note. Cite ONLY numbers from the provided ledger material."
)

QUESTION_HANDLERS = ("income", "bank", "fleet", "enemy", "alliance",
                     "directive", "orders", "blob", "threat")

# STATIC: the chartered output shape lives in the earliest prompt bytes,
# so llama-server KV-cache (cache-reuse) reuses this prefix every pulse —
# only the mutable tail changes (user's cache-reuse directive).
PACKET_EXAMPLE = (
    '# OUTPUT SHAPE (exactly one JSON object; [] for nothing)\n'
    '{\n'
    '  "directive": {"patch": {"<dotted.path>": <value>}},\n'
    '  "orders": [{"id": "so-1", "verb": "order_guard", "subject": '
    '{"ships": [1]}, "priority": 1, "ttl_ticks": 400, "veto": true} | '
    '{"revoke": "so-1"}],\n'
    '  "history_op": null | {"trim": {"scope": "deltas"}} | '
    '{"reset": {"seed": true}},\n'
    '  "digest_config": {"emphasis": ["<topic>"], "suppression": []},\n'
    '  "questions": ["<question>"]\n'
    '}\n'
    '# ALLOWED patch paths\n'
    'primary_enemy | build_split (object: attack+defence+economy=100) | '
    'doctrine.harass_cadence.interval_ticks | '
    'doctrine.harass_cadence.standoff | '
    'doctrine.aggression.overkill_factor | doctrine.recall_thresholds | '
    'attentions.topics = {"economy": {"watch": true, "loudness": 1}} '
    '(object mapping topic → {watch, loudness 0-2})\n'
    '# RULES\n'
    'primary_enemy changes need digest-evidence corroboration (sticky '
    'gate; uncited changes are dropped with an audit note). Cite ONLY '
    'numbers present in MATCH STATE.\n'
    '# VALUE SHAPES\n'
    'primary_enemy = a NUMERIC player id (0-7, e.g. 1) or null — never a '
    'name string. To PIN a new primary enemy: propose the SAME numeric id '
    'consistently across pulses; corroborations accumulate (evidence ≥2 '
    'distinct pulses + the hold time) after which the pin applies. '
    'Non-cited or non-numeric changes are dropped with an audit note.\n'
)

STATIC_CHARTER = CHARTER + "\n\n" + PACKET_EXAMPLE


class S2PulseLoop:
    _last_input = None

    def __init__(self, ledger, channel, recorder, sticky, digests=None,
                 pulse_ticks=900, window_pulses=8, s2_in=24000, s2_out=2048,
                 directive=None):
        self.ledger = ledger
        self.channel = channel
        self.recorder = recorder
        self.sticky = sticky
        self.digests = list(digests or [])   # recent window, caller-fed
        self.pulse_ticks = pulse_ticks
        self.window_pulses = window_pulses
        self.s2_in = s2_in
        self.s2_out = s2_out
        self.directive = directive
        self._next_pulse_at = pulse_ticks  # first pulse after one interval
        self._wake = False
        self._last_packet = None
        self._answer_queue = []              # pending questions
        self._answers_for_next = []          # (question, answer) tuples
        self._last_input = []
        self._last_call = 0
        self._worker = None                  # async pulse thread (US2-3)
        self._pending_out = None
        self._last_reject_reason = None
        self._pulse_started_at = None

    # -- scheduling (game time only, FR-019) ----------------------------------
    def due(self, observed_tick, paused=False):
        if paused:
            return False
        if self._wake:
            return True
        if self._next_pulse_at is None:
            return False                     # first pulse after first digest
        return observed_tick >= self._next_pulse_at

    def wake(self):
        """P0 break-interrupt: fire the next pulse out of cycle."""
        self._wake = True

    def note_digest(self, digest):
        self.digests.append(digest)
        overflow = len(self.digests) - self.window_pulses
        if overflow > 0:
            del self.digests[:overflow]
        if self._next_pulse_at is None:
            self._next_pulse_at = None  # set at first run_pulse
        self._next_pulse_seen = digest.get("interval_pulse")

    # -- input assembly (≤24k, static-prefix optimal) --------------------------
    def pulse_input(self, observed_tick):
        """Cache-friendly layout (llama-server cache-reuse): byte-static
        prefix (charter + output shape), then mutable match state ordered
        [pinned][projections][own last packet][answers], DIGEST LOG LAST —
        append-only rendering (old digests never move; trims drop from the
        FRONT only when the input budget forces it). User message: tick."""
        view = self.ledger.as_dict()
        pinned = _pinned_goals(view, self.directive, self.sticky,
                               observed_tick)
        answers = self._answers_for_next
        digests = list(self.digests[-self.window_pulses:])  # M window
        last_reject = self._last_reject_reason

        def render_with(dgs):
            mutable = "\n\n".join([
                "# PINNED\n" + _pinned_text(pinned),
                "# LAST PACKET OUTCOME\n%s" % (
                    last_reject or "(last packet applied or none sent)"),
                "# PROJECTIONS\n" + _projections_text(view),
                "# OWN LAST PACKET\n" + _json(self._last_packet or {}),
                "# PENDING ANSWERS\n" + _answers_text(answers),
            ])
            dig = "\n".join(_digest_text(d) for d in dgs) or "(none yet)"
            return "%s\n\n%s\n\n# DIGEST LOG\n%s" % (STATIC_CHARTER,
                                                     mutable, dig)

        text = render_with(digests)
        while units(text) > self.s2_in and digests:
            digests.pop(0)                      # oldest first shift
            text = render_with(digests)
        if len(self.digests) > len(digests):
            self.digests = digests              # permanent compaction
            self.recorder.record("breach", tick=observed_tick,
                                 channel="s2_in", policy="digest-trim",
                                 retained=len(digests))
        msgs = [
            {"role": "system", "content": text},
            {"role": "user",
             "content": "Game tick %s. Reply with ONLY the JSON object."
                        % observed_tick},
        ]
        self._last_input = msgs
        return msgs, units(text)

    # -- the pulse -----------------------------------------------------------------
    def run_pulse(self, observed_tick, paused=False):
        """Synchronous pulse (tests/stubs). See start_pulse/harvest_pulse
        for the async two-phase the driver uses (US2-3: S1 never waits)."""
        if paused:
            return {"ok": False, "skipped": "paused"}   # FR-003/FR-019
        msgs, in_usage = self.pulse_input(observed_tick)
        self.recorder.record("usage", tick=observed_tick, channel="s2_in",
                             context_units=in_usage)

        attempts = 1 + 1    # 1 attempt + ≤1 bounded repair (D6/US3-5)
        packet = None
        err = None
        for attempt in range(attempts):
            out, usage = self.channel.propose(msgs)
            self.recorder.record(
                "usage", tick=observed_tick, channel="s2_out",
                prompt=usage.get("prompt"),
                completion=usage.get("completion"))
            packet = out.get("packet")
            if packet is not None:
                break
            err = out.get("error") or "no packet"
            # repair attempt: same input, bounded; strategist sees the error
            msgs = msgs + [{"role": "assistant", "content": err},
                           {"role": "user",
                            "content": ("That was not one valid control "
                                        "packet (%s). Reply again with only "
                                        "the JSON packet." % err)}]

        if packet is None:
            self.recorder.record("packet_rejected", tick=observed_tick,
                                 reason=err, pulse=self._last_call)
            self._last_call += 1
            return {"ok": False, "error": err, "applied": []}

        import twincore.packet as PK
        # D9 corroboration (shared by both pulse paths)
        proposed_enemy = ((packet.get("directive") or {}).get("patch")
                          or {}).get("primary_enemy")
        if isinstance(proposed_enemy, int) and proposed_enemy != \
                (self.directive or {}).get("primary_enemy"):
            self.sticky.corroborate(proposed_enemy,
                                    pulse_id=self._last_call)
        res = PK.apply_packet(
            packet, self.directive or self._directive_from_ledger(),
            self.ledger, self.sticky, tick=observed_tick, pulse_id=observed_tick)
        return self._finalize_pulse(res, packet, observed_tick)

    # -- async two-phase (US2-3: the strategist must never delay S1) ----------
    def start_pulse(self, observed_tick, paused=False):
        """Phase 1: build input + spawn the HTTP call in a worker thread.
        Nothing gameplay-bearing happens here; returns True when spawned.
        The cadence anchor advances AT SPAWN — a failed/lagging call can
        never re-due the same boundary (spawn-storm guard)."""
        if paused:
            return False
        self._next_pulse_at = observed_tick + self.pulse_ticks
        if self._worker is not None and self._worker.is_alive():
            return False                      # ≤1 packet per cycle boundary
        self._pulse_started_at = observed_tick
        msgs, in_usage = self.pulse_input(observed_tick)
        self.recorder.record("usage", tick=observed_tick, channel="s2_in",
                             context_units=in_usage)
        import threading
        self._worker = threading.Thread(
            target=self._call_out,
            args=(msgs, observed_tick), daemon=True)
        self._worker_started = threading.Event()
        self._worker_started.set()
        self._worker.start()
        return True

    def _call_out(self, msgs, observed_tick):
        """Worker: the model HTTP call + usage metering only — no game
        access, no ledger writes, no commands (FR-002)."""
        try:
            out, usage = self.channel.propose(msgs)
        except Exception as e:  # noqa: BLE001 — worker must not die silently
            self._pending_out = ({"packet": None, "error": str(e)}, {},
                                 observed_tick)
            return
        self._pending_out = (out, usage, observed_tick)

    def harvest_pulse(self, observed_tick):
        """Phase 2 at a later tick boundary: validate+apply whatever the
        worker produced (deterministic apply point). Nothing pending →
        (False, None)."""
        out = self._pending_out
        if out is None:
            return False, None
        self._pending_out = None
        packet_out, usage, started_at = out
        self.recorder.record(
            "usage", tick=observed_tick, channel="s2_out",
            prompt=usage.get("prompt"),
            completion=usage.get("completion"))
        packet = packet_out.get("packet")
        if packet is None:
            err = packet_out.get("error") or "no packet"
            self._last_reject_reason = "packet_rejected: %s" % err
            self.recorder.record("packet_rejected", tick=observed_tick,
                                 reason=err)
            return True, {"ok": False, "error": err, "applied": []}
        import twincore.packet as PK
        packet_size = units(PK.packet_json(packet))
        if packet_size > self.s2_out:
            self.recorder.record("breach", tick=observed_tick,
                                 channel="s2_out", observed=packet_size,
                                 **{"max": self.s2_out})
            self.recorder.record("packet_rejected", tick=observed_tick,
                                 reason="packet exceeds s2_out budget")
            self._last_reject_reason = \
                "packet_rejected: exceeds s2_out budget"
            return True, {"ok": False,
                          "error": "packet exceeds s2_out budget",
                          "applied": []}
        # D9: repeated proposals accumulate digest-driven corroboration —
        # the strategist's own consistent requests ARE the evidence, and
        # the gate (hold time) does the rest
        proposed_enemy = ((packet.get("directive") or {}).get("patch")
                          or {}).get("primary_enemy")
        if isinstance(proposed_enemy, int) and proposed_enemy != \
                (self.directive or {}).get("primary_enemy"):
            self.sticky.corroborate(proposed_enemy,
                                    pulse_id=self._last_call)
        res = PK.apply_packet(
            packet, self.directive or self._directive_from_ledger(),
            self.ledger, self.sticky, tick=observed_tick,
            pulse_id=started_at)
        return True, self._finalize_pulse(res, packet, observed_tick,
                                          started_at)

    def worker_alive(self):
        return self._worker is not None and self._worker.is_alive()

    def _finalize_pulse(self, res, packet, observed_tick, started_at=None):
        if res["ok"]:
            self._last_reject_reason = None
            self.directive = res["directive"]
            self._last_packet = packet
            import twincore.packet as _PK
            content = _PK.packet_json(packet)
            self.recorder.record(
                "packet", tick=observed_tick, pulse=self._last_call,
                applied=res["applied"], dropped=res["dropped"],
                audits=res["audits"], content=content[:4000])
            if (packet.get("directive") or {}).get("patch"):
                self.recorder.record(
                    "revision", tick=observed_tick,
                    directive_version=res["directive"].get("version"),
                    patch=packet["directive"]["patch"],
                    content=content[:4000])
            if res["questions"]:
                self._answer_queue = list(res["questions"])
                self.recorder.record(
                    "question", tick=observed_tick,
                    questions=list(res["questions"]))
            self._prepare_answers()
        else:
            self._last_reject_reason = ("packet_rejected: %s"
                                        % res.get("rejected_reason"))
            self.recorder.record("packet_rejected", tick=observed_tick,
                                 reason=res.get("rejected_reason"))
        self._last_call += 1
        self._wake = False
        self._next_pulse_at = observed_tick + self.pulse_ticks
        return res

    # -- questions → ledger-only answers (FR-014) -------------------------------
    def _prepare_answers(self):
        view = self.ledger.as_dict()
        pairs = []
        for q in self._answer_queue:
            pairs.append((q, _answer_from_ledger(q, view)))
        self._answers_for_next = pairs
        self._answer_queue = []

    def _directive_from_ledger(self):
        from twincore.directive import boot_directive
        return boot_directive()


# ---- text builders (all ledger-cited, nothing fabricated) -------------------

def _pinned_goals(view, directive, sticky, now):
    return {
        "primary_enemy": (directive or {}).get("primary_enemy"),
        "age": (directive is not None and
                now - getattr(sticky, "since_tick", 0)) or 0,
        "hold_ticks": getattr(sticky, "hold_ticks", 0),
    }


def _pinned_text(p):
    return ("primary_enemy: %s (held %s ticks of %s)"
            % (p.get("primary_enemy"), p.get("age"), p.get("hold_ticks")))


def _projections_text(view):
    lines = [
        "bank: %s" % view.get("bank"),
        "income: %s" % view.get("income"),
        "own_units: %s" % len((view.get("own") or {}).get("ships") or {}),
    ]
    for c, know in ((view.get("enemy") or {}).get("by_class") or {}).items():
        lines.append("enemy %s ever=%s alive=%s last_seen=%s"
                     % (c, know.get("count_ever"), know.get("alive"),
                        know.get("last_seen_tick")))
    for b in (view.get("blobs") or []):
        lines.append("blob %s n=%s str=%s"
                     % (b.get("kind"), b.get("count"),
                        b.get("strength")))
    return "\n".join(lines)


def _digest_text(d):
    return ("digest interval_pulse=%s ticks=%s p0=%s changes=%s "
            "order_status=%s" % (
                d.get("interval_pulse"), d.get("tick_range"),
                len(d.get("p0") or []), _short(d.get("changes")),
                _short(d.get("order_status"))))


def _answers_text(pairs):
    if not pairs:
        return "(no pending answers)"
    return "\n".join("Q: %s\nA: %s" % (q, a) for q, a in pairs)


def _answer_from_ledger(q, view):
    ql = (q or "").lower()
    if "income" in ql:
        return "income = %s (ledger)" % view.get("income")
    if "bank" in ql:
        return "bank = %s (ledger)" % view.get("bank")
    if "fleet" in ql or "units" in ql:
        return "own units = %s (ledger)" % \
            len((view.get("own") or {}).get("ships") or {})
    if "enemy" in ql:
        k = (view.get("enemy") or {}).get("by_class") or {}
        return "enemy classes: %s (ledger)" % json_text(k)
    if "alliance" in ql:
        return "alliances: %s (ledger)" % json_text(view.get("alliances"))
    if "blob" in ql or "threat" in ql:
        return "blobs: %s (ledger)" % json_text(view.get("blobs"))
    if "directive" in ql:
        return "directive version = %s (ledger)" % \
            view.get("directive_version")
    if "order" in ql:
        return "in-flight: %s (ledger)" % json_text(view.get("pending"))
    return "no ledger source for this question (no fabrication)"


def _short(v):
    try:
        return "%d items" % len(v or [])
    except TypeError:
        return "1 item"


def _rest(parts):
    return "\n\n".join("# %s\n%s" % (t.upper(), text)
                       for t, text in parts[1:])


def _json(v):
    import json
    try:
        return json.dumps(v, default=str)
    except (TypeError, ValueError):
        return "{}"[:512]


def json_text(v):
    import json
    return json.dumps(v, default=str)


def units(text):
    return max(0, len(text or "") // 4)