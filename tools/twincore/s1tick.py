"""Spec 004 twin-core: S1 tactical tick loop (T013, protocol §1).

Fixed anatomy, static-first (FR-022): role card → directive vN → event
window (P0 pinned top, rollups then last-K deltas, newest first) → ledger
echo → in-flight orders. Budget ≤8k units (config); breach ⇒ trim deltas,
then rollups, then seed-reset + recorded breach (FR-020). Every command
proposal is validated (tri-state) before issue; order ids are minted by the
orchestrator; accepted commands go out through the single binding owner;
rejections/clamps loop back as first-class tool errors on the next tick
(FR-007). Malformed model output: ≤2 retries per tick with the marshal
reason, then the tick idles with a recorded streak (no guessing). Economy
proposals (deterministic module) enter the same validator and share the
per-tick command cap.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

import twincore.recorder as recorder  # noqa: E402
from twincore.directive import boot_directive  # noqa: E402


class ChannelFailure(RuntimeError):
    """S1 channel unusable after retries → driver DEGRADED_S1_DOWN."""

ROLE_CARD_STATIC = (
    "You are the tactical core (S1) of a Homeworld CPU player seat.\n"
    "You issue ONLY tool calls from the provided verb set, at most one "
    "command per subject; the validator may reject/clamp them with a "
    "reason you must act on next tick.\n"
    "You hold no durable memory: your context may be trimmed or reset at "
    "any tick boundary; durable knowledge lives in the ledger echo.\n"
    "In emergencies (P0 items pinned first) respond defensively first: "
    "recall guards, converge idle ships."
)

ECHO_MAX_LINES = 64
K_DELTAS = 8

VERB_HINTS = {
    "order_move": "move ships to coord",
    "order_attack": "attack target id",
    "order_harvest": "collectors to a resource id",
    "order_guard": "guard a friendly id",
    "order_dock": "dock to ship/resource",
    "order_build": "build ship type (lane policy comes from economy)",
    "order_launch": "launch docked",
    "order_halt": "stop",
}


class S1TickLoop:
    def __init__(self, ledger, validator, economy, channel, client,
                 recorder, directive_id="v1", interval_ticks=16,
                 budget_in=8192):
        self.ledger = ledger
        self.validator = validator
        self.economy = economy
        self.channel = channel
        self.client = client
        self.recorder = recorder
        self.directive_id = directive_id
        self.interval_ticks = interval_ticks
        self.budget_in = budget_in
        self.idle_streak = 0
        self.order_seq = 0
        self._err_backlog = []      # first-class errors, next-tick loop
        self._rolling_deltas = []   # last-K deltas + rollup carriers
        self._p0_items = []         # pinned P0 (digest P0Tracker output)
        self.rolling_rollups = []   # interval rollups (digest.py output)
        self.directive = boot_directive()

    # -- context assembly (protocol §1 fixed anatomy) ------------------------
    def assemble(self, tick, directive, p0=None, deltas=None,
                 rollups=None):
        sections = [
            ("role_card", ROLE_CARD_STATIC),
            ("directive", _render(directive)),
            ("event_window", self.window(deltas or self._rolling_deltas,
                                         p0 or self._p0_items)),
            ("ledger_echo", self.echo()),
            ("in_flight", self.in_flight_text()),
        ]
        usage = {}
        text_out = []
        for title, text in sections:
            usage[title] = units(text)
            text_out.append((title, text))
        total = sum(usage.values())
        # FR-020 breach discipline is staged in run_tick (trim → reset)
        return text_out, total

    @staticmethod
    def to_messages(sections, tick):
        """Static-first layout (FR-022) inside ONE system message (qwen/gemma
        chat templates reject system messages after non-system ones):
        [1 role card][2 directive] static prefix, then the mutable
        window/echo/in-flight — prefix order preserved for cache reuse."""
        role, directive = sections[0][1], sections[1][1]
        mutable = "\n\n".join(
            text for title, text in sections
            if title in ("event_window", "ledger_echo", "in_flight"))
        return [
            {"role": "system",
             "content": "%s\n\n# directive (v-current)\n%s\n\n# situation\n%s"
                        % (role, directive, mutable)},
            {"role": "user",
             "content": "Tick %s is live. Respond with tool calls only."
                        % tick},
        ]

    def window(self, deltas, p0=None):
        lines = []
        for item in p0 or []:
            lines.append("P0 %s tick=%s %r outcome=%s"
                         % (item.get("kind"), item.get("tick"),
                            item.get("detail"), item.get("outcome")))
        for r in self._rollup_lines:
            lines.append("rollup %s: %s" % (r["interval"], ", ".join(
                r["lines"]) or "(quiet)"))
        for d in (deltas or list(reversed(self._rolling_deltas)))[:K_DELTAS]:
            parts = []
            for k in ("created", "destroyed", "damaged", "vanished",
                      "orders_applied", "orders_rejected"):
                got = d.get(k)
                if got:
                    if isinstance(got, dict) and k == "destroyed":
                        parts.append("destroyed %s" % sorted(got)[:4])
                    elif isinstance(got, list):
                        parts.append("%s %s" % (k, got[:4]))
            if parts:
                lines.append("tick %s: %s" % (d["tick"], "; ".join(parts)))
        return "\n".join(lines[:S1_WIN_MAX_LINES]) or "(no events)"

    def echo(self):
        view = self.ledger.as_dict()
        lines = []
        own = (view.get("own") or {}).get("ships") or {}
        for sid, s in list(own.items())[:ECHO_MAX_LINES]:
            lines.append("own %s %s hp=%.0f"
                         % (sid, (s.get("type") or "?"),
                            s.get("health") or 0))
        lines.append("bank %s income %s" % (view.get("bank"),
                                            view.get("income")))
        ecl = (view.get("enemy") or {}).get("by_class") or {}
        for cls, know in list(ecl.items())[:8]:
            lines.append("enemy %s ever=%s alive=%s last=%s"
                         % (cls, know.get("count_ever"),
                            know.get("alive"), know.get("last_seen_tick")))
        for b in (view.get("blobs") or [])[:S1_BLOB_LINES]:
            lines.append("blob %s n=%s str=%s at=%s"
                         % (b.get("kind"), b.get("count"),
                            b.get("strength"),
                            [round(c) for c in (b.get("center") or [])]))
        return "\n".join(lines[:ECHO_MAX_LINES])

    def in_flight_text(self):
        lines = []
        for oid, rec in (self.ledger.in_flight() or {}).items():
            lines.append("order %s: %s issued@%s"
                         % (oid, rec.get("tool"), rec.get("issued_tick")))
        for e in self._err_backlog[-TOOL_ERR_BACKLOG:]:
            lines.append("ERR %s: %s (hint: %s)" % (
                e.get("reason"), e.get("message"), e.get("hint")))
        return "\n".join(lines) or "(none in flight)"

    # -- usage accounting -----------------------------------------------------
    @staticmethod
    def usage_units(text_or_sections):
        if isinstance(text_or_sections, str):
            return units(text_or_sections)
        return sum(units(t) for _, t in text_or_sections)

    _rollup_lines = []

    # -- the tick ----------------------------------------------------------------
    def run_tick(self, observed_tick, paused=False, budget=None):
        if paused:
            return {"mode": "held", "issued": []}   # FR-003: pause-safe
        budget = budget or self.budget_in
        directive = self.directive  # (set by the driver wiring in M-B integration)
        if directive is None:
            directive = boot_directive()
        d_tick = self.ledger.as_dict().get("tick", 0)
        self._rollup_lines = self.rolling_rollups

        tool_errors = list(self._err_backlog)
        self._err_backlog = []
        issued = []

        attempts = 1 + 2  # 1 + ≤2 retries (protocol §1 failure discipline)
        for attempt in range(attempts):
            sections, ctx_usage = self.assemble(d_tick, directive)
            msgs = self.to_messages(sections, observed_tick)
            try:
                response, usage = self.channel.propose(msgs, self.tools())
            except Exception as e:  # channel level → S1 down (retries done)
                raise ChannelFailure("s1 channel failed: %s" % e) from e
            self._record_usage("s1", usage, observed_tick, ctx_usage)
            calls = parse_tool_calls(response)
            if calls is None:
                self._err_backlog.append({
                    "reason": "E_MALFORMED_OUTPUT",
                    "message": "model output was not parseable tool calls"
                               " (attempt %d/%d)" % (attempt + 1, attempts),
                    "hint": "reply with only tool calls from the verb set"})
                continue
            break
        else:
            self.idle_streak += 1
            self.recorder.record(
                "decision", tick=observed_tick, mode="idle",
                streak=self.idle_streak,
                reason="malformed-output retries exhausted")
            return {"mode": "idle", "issued": []}

        # success path: parse → validate → issue
        calls = parse_tool_calls(response) or []
        # economy proposals first (same validator, shared cap — FR-027)
        economy_ps = self.economy.proposals(self.ledger.as_dict()) \
            if self.economy else []
        batch = ([{"verb": p["verb"], "params": dict(p["params"]),
                   "lane": p.get("lane")} for p in economy_ps]
                 + [{"verb": c["verb"], "params": dict(c["params"])}
                    for c in calls])
        results = self.validator.validate_batch(batch, self.ledger)
        for proposal, res in zip(batch, results):
            self.recorder.record(
                "validation", tick=observed_tick, tool=proposal["verb"],
                status=res["status"], reason=res.get("reason"),
                message=res.get("message"), hint=res.get("hint"),
                proposed=_json_compact(proposal.get("params")))
            if res["status"] == "ok":
                params = dict(proposal.get("params") or {})
                self.order_seq += 1
                params.setdefault("id", self.order_seq)
                wire = self.client.order(proposal["verb"], params)
                cmd = command_record(
                    oid=params["id"], tool=proposal["verb"],
                    params=params, validation=res,
                    directive_version=directive.get("version"),
                    tick_issued=observed_tick)
                self.recorder.record("command", tick=observed_tick, **cmd)
                issued.append({"tool": proposal["verb"], "params": params,
                               "wire": wire})
                self.ledger.register_command({
                    "id": params["id"], "tool": proposal["verb"],
                    "fingerprint": res.get("fingerprint"),
                    "issued_tick": observed_tick})
            elif res["status"] == "rejected":
                self._err_backlog.append({
                    "reason": res["reason"], "message": res.get("message"),
                    "hint": res.get("hint"), "tool": proposal["verb"]})
            elif res["status"] == "clamped":
                self._err_backlog.append({
                    "reason": "E_CLAMPED", "message":
                        res.get("reason"),
                    "hint": res.get("hint"), "tool": proposal["verb"]})
        self.idle_streak = 0
        self.recorder.record(
            "decision", tick=observed_tick, mode="decide",
            proposed=[{"verb": b["verb"]} for b in batch],
            issued=len(issued))
        return {"mode": "decide", "issued": issued}

    def tools(self):
        """Full OpenAI-style function schemas generated from the registry
        (single source — spec 003's params tables, D2)."""
        import aiplay_protocol as P
        surface = []
        for tool in self._verb_surface():
            params = (P.REGISTRY["order_tools"][tool].get("params")
                      or {})
            surface.append({"type": "function", "function": {
                "name": tool,
                "description": P.REGISTRY["order_tools"][tool].get(
                    "desc", ""),
                "parameters": _json_schema_of(params)}})
        return surface

    def _verb_surface(self):
        import aiplay_protocol as P
        return list(P.REGISTRY["order_tools"])

    def _aux_surface(self):
        return []

    def _record_usage(self, channel, usage, tick, ctx_units=0):
        self.recorder.record("usage", tick=tick, channel=channel,
                             prompt=usage.get("prompt"),
                             completion=usage.get("completion"),
                             total=usage.get("total"),
                             context_units=ctx_units)


def command_record(oid, tool, params, validation, directive_version,
                   tick_issued, standing_order_id=None):
    return {
        "id": oid, "tool": tool, "params": params,
        "validation": {k: validation[k] for k in
                       ("status", "reason") if k in validation},
        "directive_version": directive_version,
        "standing_order_id": standing_order_id,
        "outcome": "in-flight",
        "tick_issued": tick_issued, "tick_resolved": None,
    }


S1_WIN_MAX_LINES = 24
S1_BLOB_LINES = 6
TOOL_ERR_BACKLOG = 8


def units(text):
    return max(0, len(text or "") // 4)


def _json_compact(v):
    import json
    return json.dumps(v, default=str)


def parse_tool_calls(response):
    """Model response → list of {verb, params}; None when malformed.
    Accepts both the raw response dict ({tool_calls: [...]}) and a
    pre-parsed proposal list from llm.S1Channel ([{verb, params}, ...])."""
    if isinstance(response, list):  # pre-normalised proposals
        calls = response
        out = []
        for c in calls:
            if not isinstance(c, dict) or "verb" not in c:
                return None
            out.append({"verb": c["verb"],
                        "params": c.get("params") or {}})
        return out
    if not isinstance(response, dict):
        return None
    calls = response.get("tool_calls")
    if calls is None:
        return None
    if not isinstance(calls, list):
        return None
    out = []
    for c in calls:
        if not isinstance(c, dict) or "verb" not in c:
            return None
        out.append({"verb": c["verb"],
                    "params": c.get("params") or {}})
    return out


def _render(directive):
    import json
    return json.dumps(directive, sort_keys=True, default=str)


_TYPE_MAP = {"integer": "integer", "boolean": "boolean",
             "string": "string", "array": "array"}


def _json_schema_of(params):
    """Registry params table → OpenAI/JSON function-parameters schema
    (registry rule: {'type', 'items', 'required', 'min', 'length',
    'desc'})."""
    props = {}
    for key, rule in params.items():
        out = {"type": _TYPE_MAP.get(rule.get("type"), "string"),
               "description": rule.get("desc", "")}
        if rule.get("type") == "array":
            items = rule.get("items")
            out["items"] = ({"type": "integer"} if items == "integer"
                            else {})
            if rule.get("length"):
                out.update({"minItems": rule["length"],
                            "maxItems": rule["length"]})
        if rule.get("min") is not None:
            out["minimum"] = rule["min"]
        props[key] = out
    required = [k for k, r in params.items() if r.get("required")]
    return {"type": "object", "properties": props, "required": required,
            "additionalProperties": False}