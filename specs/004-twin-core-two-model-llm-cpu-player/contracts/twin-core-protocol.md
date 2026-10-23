# Contract: Twin-Core Internal Protocol (S1 tick · S2 pulse · validator)

Scope: contracts between the twin-core driver's deterministic orchestrator and
its two bounded-model channels, and between the orchestrator and the game's
command/event surface (whose wire format is owned by spec 003 — see
`specs/003-full-coverage-aiplay-mcp-engine-i-o-full/contracts/mcp-surface.md`;
hereby reused, registry `tools/aiplay_protocol.py` = single source of the
enumerated command surface, 20 order verbs at time of writing).

## 1. S1 tick contract (fast lane)

Cadence: game-time ticks, config default ~1s, combat-intensity gated (D4);
never wall-clock; paused/stepped matches produce no calls.

**Tick input** — fixed anatomy, static-first order (KV-friendly, FR-022):

```text
[1 role card]      static, versioned; interface cheatsheet, constraints, veto rule
[2 directive vN]   rendered current directive (schema §4)
[3 event window]   newest interval rollups, then last-K deltas (newest first);
                   any P0 items pinned at top
[4 ledger echo]    one line per fleet group / economy metric (from ledger)
[5 in-flight]      open commands + standing orders with statuses
```

Budget: ≤8k units (config). Breach ⇒ trim deltas first, then rollups, then
seed-reset; breach recorded (FR-020).

**Tick output**: ONLY tool-call proposals drawn from the registry order verbs
(≤6 per tick). Any freeform prose outside tool calls is discarded and logged.

**Sequencing**:

```text
tick gate → assemble input (§ above) → model call
  → parse tool calls → validator (§3) per call, in order
  → accepted: issue via command path (one binding owner) → CommandRecord
  → rejected/clamped: collect as tool errors → append to NEXT tick input (FR-007)
  → outcomes (orderApplied/orderRejected/orphan) resolve CommandRecords;
    unresolved-orphan rule: re-task, never assume busy (ledger)
```

Failure discipline: malformed/empty model output retried ≤2 per tick with
validator/marshal reason attached; still bad ⇒ tick idles, streak recorded
(no guessing, FR-024 in spirit).

## 2. S2 pulse contract (slow lane)

Cadence: game-time 30-90s (config); immediate async wake on P0; ≤1 packet
per pulse; pause-safe.

**Pulse input** (≤24k units):

```text
[charter]          static strategist charter (role, protocol, hard rules)
[ledger projections] cited ledger numbers
[pinned goals]     primary enemy (age stamp), pinned objectives, hold timers
[last M digests]   default M=8 pulses (config) + milestones
[own last packet]  previous plan + observed outcomes (order_status from ledger)
[answers]          responses to its previous questions (ledger-only, FR-014)
```

**Pulse output — ControlPacket** (schema in data-model.md; ≤2k units):

| element | apply semantics |
|---|---|
| directive.patch | semantic edits validated against directive schema; accept ⇒ monotonic version bump; applied at tick boundary |
| orders[] | new/revoke StandingOrders; subjects checked against ledger |
| history_op | trim{scope} = drop listed window contents; reset{seed} = S1 conversation collapses to [role card + directive vN + state seed + in-flight orders] |
| digest.config | emphasis/suppression sets merged into next cycle's classifier |
| questions[] | queued; answered next pulse from ledger only |

Validation gate: whole-packet schema check BEFORE any element applies
(partial packets are rejected whole, logged as packet_rejected, old directive
stays force, no retry same pulse — US3-5). Sticky-goal enforcement (FR-017)
runs at apply time: a primary-enemy change without evidence-corroboration
count ≥2 across ≥2 distinct digests AND hold-time ≥60s game time elapsed is
DROPPED from the packet (rest applies), flagged in the audit line.

## 3. Command validator contract (tri-state)

Input: proposed tool call {verb ∈ registry, params per registry schema}.
Checks, in order (all config-extensible; defaults here):

1. interface: verb exists; params match registry schema (bounds, id-mapped
   targets) — else `rejected(E_BAD_REQUEST, hint)`.
2. ids: target ship/resource ids resolvable in ledger's last observation —
   else `rejected(E_BAD_ID, hint: re-observe)`; id-surge heuristic triggers
   ledger rebuild (edge case: id renumbering).
3. alliance: attack-family verb targeting an ally or ally asset — else
   `rejected(E_ALLIED_PROTECTION)`.
4. affordability: build/research verbs against bank state — else
   `rejected(E_UNAFFORDABLE)`.
5. dedupe/clamp: duplicate of in-flight command or standing order — collapse
   (`clamped(original→corrected)`), cap per-tick command count (≤6).

Output: `{ok} | {rejected, reason, hint} | {clamped, original, corrected}`.
Rejections/clamps ALWAYS loop back to S1 as first-class errors (same tick if
round-trippable, else next tick); nothing reaches the game unvalidated.

## 4. Directive schema (canonical field list)

```text
directive {
  version: int                         // monotonic
  boot_default: bool
  primary_enemy: player_id | null      // ≤1, sticky-gated
  doctrine {
    defence_tiers: []                  // mothership-first → medium → hardcore recall (playbook A4)
    harass_cadence: {interval_ticks, standoff}   // playbook 25,000 standoff default
    aggression: {overkill_factor}      // 1.4 default
    recall_thresholds: {...}           // tier trigger strengths
  }
  build_split {attack: 30, defence: 30, economy: 40}   // % (may be retuned; sums 100)
  standing_orders: [StandingOrder]
  attentions {topic: {watch: bool, loudness: 0-2}}     // digest.config merges here
}
```

## 5. Digest schema (S2-facing)

```text
digest {
  interval: pulse_id, tick_range: [a, b]
  p0: []          // verbatim critical items, outcome-tracked until superseded
  changes: []     // fleet deltas, RU flow, losses/built, sightings (hull classes), alliance events
  order_status: []// {command|standing_order, status, reason} incl. veto reports
  s1_notes: []    // mandatory one-liners when veto/disobedience occurred
  projections: [] // income, threat estimate, blob ratings — ledger-cited numbers only
}
```

Budget 300-800 units; P2 chatter compressed to rollup lines inside `changes`;
schema-invalid digest ⇒ regenerate once at reduced scope, else log gap.

## 6. Recorder line kinds (append-only JSONL)

`event_raw, classification, delta, rollup, digest, packet, packet_rejected,
revision, decision, command, validation, usage, breach, degradation,
question` — every line carries {match_id, tick, wall_ts(observability only)},
record-kind schema per data-model.md. Replay contract: kind+tick-range filters
reconstruct any segment (SC-007). Wall timestamps are for debugging only; no
gameplay decision may read them (FR-003).

## 7. Alliance and politics

Alliance events flow as ordinary P1 events (P0 if the PRIMARY enemy becomes an
ally or an ally declares war). Validator enforces allied protection at submit
time (§3-3). Standing orders against a former ally: next ledger pass flags
subject as allied ⇒ order aborted with reason, surfaces in next digest
order_status (edge case contract).

## 8. Session lifecycle & degradation ladder (authoritative states)

```text
UNBOUND ──bind(seat)──▶ BOUND_ACTIVE
BOUND_ACTIVE ──S2 unreachable──▶ DEGRADED_S2_DOWN (S1 runs last valid directive; pulses retried w/ backoff, game-time based)
BOUND_ACTIVE ──S1 unreachable──▶ DEGRADED_S1_DOWN (seat issues NO commands)
BOUND_ACTIVE ──interface down──▶ DEGRADED_IFACE_DOWN (both idle; no guessing)
any DEGRADED_* ──recovery──▶ BOUND_ACTIVE (record transition)
match end / driver stop ──▶ UNBOUND
```

Every transition → recorder `degradation` line. Re-bind after loss ⇒ full
re-observe (ledger rebuild) before any command re-issue; stale-id orders
invalidated with re-issue (edge case contract).