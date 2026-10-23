# Data Model: Twin-Core Two-Model LLM CPU Player (spec 004)

Entities internal to the twin-core driver and their interfaces to the game's
command/event layer. Wire-level game layer mapping is spec 003's domain; this
document adds the driver-owned shapes. Field types are conceptual; concrete
encodings live in `contracts/twin-core-protocol.md`.

## Entities

### Directive (S1's mutable intent slot; S2-owned; versioned)

| Field | Type | Validation |
|---|---|---|
| version | int, monotonic | ≥1; bumped only by accepted patch |
| primary_enemy | player id or null | ≤1 pinned; change gated by sticky rule |
| doctrine | object | defence_tiers[], harass_cadence, aggression, recall_thresholds |
| build_split | object | {attack, defence, economy} summing to 100 |
| standing_orders | array of StandingOrder | ids unique; ≤ spec-configured max |
| attentions | object | per-topic watch flags + loudness (how hard to react) |
| boot_default | flag | true for the boot directive (never written by S2) |

Transitions: `boot v1 → v(n+1) via accepted patch → superseded by trim/reset
(no version change) → retired only at match end`. Rejected patches never bump
the version. Full revision history is written to the recorder.

### StandingOrder

| Field | Type | Validation |
|---|---|---|
| id | uuid-lite (recorder-stable) | unique per directive |
| verb | enum | ∈ REGISTRY order-verb set (20 verbs, D2) |
| subject | object | target ids / zone / class; validated at issue time |
| constraints | array | bound list the tactical core must respect |
| priority | int | ties broken by issuer order |
| ttl_ticks | int ≥0 | expires in game time; 0 = until aborted |
| abort | condition expr | evaluated against ledger each tick |
| veto | bool (default true) | tactical emergency veto allowed |

Transitions: `active → suspended (emergency veto, MUST report) → resumed |
active → aborted (condition hit or S2 revokes) → expired (TTL) | dead →
reported in next digest order_status`.

### EventDelta / Rollup

Delta: tick-scoped change record {tick, kinds, subject ids observed/created/
destroyed/damaged, positional summaries}. Rollup: {interval [tick_a,tick_b],
1-2 summary lines, class histogram P0/P1 counts}. Deltas evaporate to rollups
(≥10-tick intervals, config D9); P0 never evaporates before outcome reporting.

### Digest (S2-facing)

| Field | Content rule |
|---|---|
| interval, tick_range | pulse window identifiers |
| p0[] | verbatim P0 items (never summarised) |
| changes[] | fleet comp deltas, RU flow, losses/built, sightings w/ hull classes, alliance events |
| order_status[] | completed/failed/expired/overridden + reasons (incl. veto reports) |
| s1_notes | tactical core's one-line explanations (mandatory when a veto occurred) |
| projections[] | income estimate, threat estimate, ledger-cited numbers only |

Validation: schema-checked; budget 300-800 units; all figures MUST cite
ledger fields (FR-013).

### ControlPacket (S2 output; ≤1 per pulse)

| Element | Validation |
|---|---|
| directive.patch | semantic edit list vs directive schema; schema-validated; version bump on accept |
| orders[] | 0..n StandingOrders; subjects validated against ledger |
| history_op | none | trim{scope} | reset{seed_state} |
| digest.config | emphasis/suppression sets for next cycle |
| questions[] | answered next pulse from ledger ONLY |

Apply order at tick boundary: validate packet → directive patch (bump) →
orders → history op → digest config. Whole-packet schema failure = zero
elements applied, logged, old directive stays (FR-015/US3-5).

### Ledger (state store; canonical)

Own fleet inventory + unit states (hp, position, formation, tactics, dock
status) · RU bank + income projection · per-class enemy knowledge (hull
classes ever observed, counts, last-seen) · blob ratings (own/enemy spatial
groups, strength scores — the 1.4× overkill threshold input) · alliance state
per player · pending commands + statuses · directive reference (current
version). Rebuilt via full re-observe on: bind, match load/restart,
identifier-mismatch error surge (edge case: id renumbering).

### ValidationResult (tri-state)

`{status: ok} | {status: rejected, reason: E_*, hint} | {status: clamped,
original, corrected}` — checks per FR-006 against REGISTRY order verbs,
alliance protection, bounds/ids, affordability, dedupe.

### CommandRecord (per issued command)

{id (order-id namespace), directive_version, standing_order_id?, tool, params,
validation: ValidationResult, outcome: applied|orderRejected(aux enum)|orphan|
timeout, tick_issued, tick_resolved}.

### RecorderRecord kinds (JSONL, one file per kind)

event_raw (ring+file) · classification · delta · rollup · digest · packet ·
packet_rejected · revision (directive history) · decision · command ·
validation · usage (per-channel units/tick or pulse) · breach + reset ·
degradation (ladder transition) · question (S2 → next-pulse answer).

### ChannelBudget (config)

{channel: s1_in|s1_out|digest|s2_in|s2_out, max_units, observed, breach_policy:
trim|seed_reset}. Defaults per FR-020. Breach ⇒ recorded alert + policy run.

### DifficultyPreset

Per level (beginner/intermediate/advanced, derived from the game's own
difficulty data): tick cadence (~2.1s/~1s/~0.5s game time), budget split, plan
menu gating flags (mines/cloak/gravwell/kamikaze advanced-only; armada team
counts 0/1/2; kamikaze draw 25% advanced), reaction thresholds, research delay.
One schema — parameters only (FR-026).

### Seat lifecycle (driver/seat state machine)

`UNBOUND → BOUND_ACT(active loop) → DEGRADED_S2_DOWN (running last directive) |
DEGRADED_S1_DOWN (idling, no commands) | UNBOUND (match end / driver loss) —
every transition recorded. Exactly one binding owner per seat (FR-002);
re-binds only from UNBOUND with full re-observe.`