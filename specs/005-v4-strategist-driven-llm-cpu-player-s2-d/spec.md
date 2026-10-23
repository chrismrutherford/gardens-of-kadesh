# Feature Specification: V4 — Strategist-Driven LLM CPU Player

# Feature Specification: v4-strategist-driven-llm-cpu-player (short:
# strategist-drives)

**Feature**: `005-v4-strategist-driven-llm-cpu-player-s2-d`

**Created**: 2026-10-23

**Status**: Draft v3 — amended from LIVE-ROUND evidence (2026-10-23, session
18/19): the live round proved the data plumbing, not the tier design, was
the failure (S2's pulse input carried LESS state than S1's: no enemy
contacts, no asteroid ledger, no build menu, no research tree; async engine
rejections were invisible; the strategist guessed a nonexistent tech name
and never learned; bank ~187k sat unspent while the built-in CPU teched to
IonCannonFrigates). Draft v3 adds the teaching firewall, the S2 arc, and
the S1 history budget; supersedes Draft v2. Earlier amendment (decision 17):
three-tier control architecture — executor (code) + S1 small/fast tactical
model inside a delegation envelope + S2 strategist with widened (~120 s)
pulse cadence.

**Input**: User directive: "I think we got the design wrong. Make a spec for
V4. Less procedural, more S2 to drive the program."

This supersedes the control architecture of v3/spec 004 (twin-core
S1-drives-with-S2-advising). V3's live evidence — validated command flow,
postgres trail, digests, patches — is REUSED as substrate; v3's procedural
economy (build lanes, ratios, research chains in code) and the
required-every-tick S1 model are the parts judged wrong: they made
predictable-but-dumb moves and buried the strategist's judgement under a
protocol (digests → patches → sticky gates). V4 flips the relationship:
the strategist PLANS, the code OBEYS.

## Summary

Three tiers, one intent authority. A CPU seat is driven by a large
strategist model (S2) whose **Program** — emitted each pulse (default
~120 s game time, configuration; the large endpoint delivers in ~2 min
measured) — is the seat's strategic authority: production (build/research),
fleet tasking, and a posture that also declares a **delegation envelope**:
what the small fast tactical model (S1) may do between pulses. S1 runs a
fast micro-pulse (default ~8 s game time) and may issue commands ONLY
inside that envelope (e.g., defense micro: retarget focus-fire, retreat,
perimeter response); any attempted action outside the envelope is blocked
at validation and recorded. Between and beneath both, a thin deterministic
**executor** translates the current program into validated commands at the
tick cadence (≤6 per tick) and keeps exactly two reflexes alive (idle
collectors harvest, full collectors deliver). Priority law is total:
program > S1-envelope commands > reflexes. No economy doctrine, build
ratios, or research chains are hard-coded anywhere: the v3-era procedural
economy is RETIRED; playbook behaviour enters as prompt material for S2
(and S1's charter), steered by the models' decisions, not code constants.
The playbook's intelligence is the models' job; the code's job is to obey
promptly, validate strictly (including envelope legality), record everything,
and EXPLAIN itself: the executor is a teaching firewall (FR-020) — every
deny, coercion or clamp produces an actionable verdict line (what was
refused, and what IS currently allowed), fed back to both models so the
next attempt starts from the live rulebook, not a guess. S2's own memory is
compacted by the arc (FR-021): a model-authored running summary, code-stored
and budget-enforced, so raw match history never re-enters the strategist's
limited window. S1's memory is a budget S2 allocates (FR-022). Awareness is
a superset contract (FR-009): S2's pulse input carries the FULL world view
(own ledger, enemy contacts, asteroid ledger, build menu, research tree)
and S1's live view is the grant-filtered subset.

**Out of scope (explicit)**: v4 does NOT change the game engine or the
spec-003 command/event layer (imported unchanged); does not re-import or
maintain v1/v2 agent stacks; keeps v3's s1tick/economy modules out of the
driver — those v3 MODULES remain in-tree, unreferenced (both superseded by
v4's own executor and S1 channel; cleanup is a separate decision); no
lobby/UI integration (v4 is a peer PLAYER via session-bind — see spec 004's
orientation, carried over); no tournament loops/personality presets in the
lobby.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — The strategist plans, the executor obeys (P1) 🎯 MVP

A host loads a savegame with cash and starts the v4 driver for one seat.
Within a couple of pulses the strategist has read the compact observation
and emitted a Program: which ships to build, what to research, where the
fleet goes. The executor turns those items into validated commands at the
tick cadence. The seat's production (collectors, strike craft, frigates),
research and fleet motion are all visibly S2-decided: nothing about WHAT to
do lives in code.

**Why this priority**: this is the whole point of v4 — intelligence in the
model, obedience in the code.

**Independent Test**: launch a fresh cash-inject match; within the first
two S2 pulses a program applies (version bump recorded) covering
production + research + at least one fleet tasking; the postgres trail
shows each production/research command traced to the program item that
caused it. No hard-coded build type appears that the program didn't name.

**Acceptance Scenarios**:

1. **Given** a fresh match, **When** the driver binds, **Then** the seat
   idles at most until the first S2 pulse (~120 s game time), S2 emits a
   valid program, and the executor's first commands are traceable to
   program items — before then only reflexes run (collectors harvesting);
   from the first program onward S1 micro-manages only inside the
   envelope.
2. **Given** the executor's tick loop, **When** it runs, **Then** every
   command it issues is a mechanical translation of the current program or
   an enumerated reflex — and nothing else; the executor has no notion of
   build priorities, ratios, or research chains.
3. **Given** a program item the validator rejects (bad id, unaffordable,
   allied target), **When** the executor processes it, **Then** the item is
   marked failed/blocker with the validator reason and the failure is in
   S2's next pulse input — the executor retries at most per pulse cycle.
4. **Given** a complete match, **When** the trail is read, **Then** every
   production/research/fleet decision cites the program version and item
   that authorised it.

---

### User Story 2 — Reflexes keep the economy alive between brains (P1)

Between pulses, the S1 tactical channel handles fast reactions inside its
delegation envelope (model-driven, bounded — see FR-017). The reflexes are
the floor BENEATH both brain channels: full collectors deliver their
cargo; idle collectors find the nearest asteroid and harvest them. These
reflexes are small, enumerable, deterministic, and are the ONLY things
left autonomous when S1 is unreachable AND the program covers nothing —
the base keeps breathing however far up the stack failure reaches.

**Why this priority**: a strategist-only CPU that freezes when its pulse
is late is unusable; the reflexes bound the damage of any S2 failure.

**Independent Test**: with S2 disconnected at boot, the seat still keeps
all owned collectors cycling (harvest→deliver→resume) from observation
state alone; the postgres trail records every reflex action.

**Acceptance Scenarios**:

1. **Given** collectors sitting idle (parade or unloaded), **When** the
   tick loop runs, **Then** each idle collector is dispatched to the
   nearest asteroid and full ones are sent to deliver.
2. **Given** the strategist unreachable mid-match, **When** S2 pulses fail,
   **Then** the executor keeps running the LAST valid program (unchanged,
   same version), S1 (if alive) keeps acting strictly inside its existing
   envelope, and reflexes continue; nothing is invented on anyone's behalf.
3. **Given** a reflex and a program item collide (program says halt, reflex
   wants harvest), **When** the executor runs, **Then** the program wins —
   and where an S1 envelope command covers those ships, S1 wins over the
   reflex the same way (program > S1 > reflex; reflexes fill gaps only).

---

### User Story 3 — New information → new program (P2)

P0 events (base invaded, force annihilated, flow zeroed, command centre
threatened) wake the strategist out of cycle; ordinary state changes
accumulate into the next scheduled pulse. Either way, S2 re-plans within
one pulse and the new program applies whole (schema-validated); the old
program stays until a valid one lands. Program history is recorded
version-by-version.

**Why this priority**: reaction to game reality is what makes this a CPU
player rather than a demo; but it must not introduce procedural
classification logic back into the driver.

**Independent Test**: with a P0 injected (via a real attack observed in the
live match), the strategist's next pulse arrives out-of-cycle and the
resulting program (if any) applies at the next tick boundary; the
revision is in the trail with its reason.

**Acceptance Scenarios**:

1. **Given** an attack on the seat's defended zone observed in the delta
   stream, **When** the event is classified P0, **Then** the strategist is
   woken immediately with the P0 verbatim at the top of its input.
2. **Given** a valid new program, **When** it applies, **Then** it applies
   at a tick boundary as a new version; in-flight commands from the old
   program are left running (not rescinded) unless the new program says
   otherwise.
3. **Given** an invalid program (schema violation), **When** it is
   validated, **Then** nothing applies, the reason loops to S2 in the next
   input, and the previous version stays authoritative.

---

### User Story 4 — Same safety rails, thinner code (P3)

Everything v3 proved about safety carries: one binding (the executor's
client), tri-state validation grounded in the spec-003 registry, game-time
cadences only, postgres record store as the single trail (no files), the
degradation ladder, and the built-in CPUs coexisting unchanged. The v3-era
modules (s1 tick loop, economy lanes, research chain) are simply not part
of the v4 driver.

**Why this priority**: v4 must not regress the guarantees v3 established.

**Independent Test**: four short matches as in spec 004's T025 matrix
(strategist killed, driver killed, built-in-CPU-only, rematch) with the
same observables; plus the full unit suite green.

**Acceptance Scenarios**:

1. **Given** S2 unreachable at boot, **When** the match runs, **Then** the
   seat still harvests (reflexes) and idles otherwise — no procedural
   fallback doctrine appears.
2. **Given** the built-in CPU seats, **When** the v4 seat joins, **Then**
   built-in behaviour is unchanged (coexistence, FR carried).
3. **Given** any postgres outage, **When** recording fails, **Then** the
   in-memory ring keeps the tail, the driver continues, and the gap is
   counted.

---

### User Story 5 — Measurable (P4)

The harness reads the postgres trail: per-series reports with win/loss
shape vs the built-in CPU at each difficulty, program-adherence (commands
per program item), S1-adherence (micro-commands per grant) with envelope
violation and override counts, pulse cadence adherence, budget usage,
validator rejection rates, revision counts, reflex activity. Same entry
points and report shape as v3 (reused).

**Independent Test**: run a series (≥3 matches) and receive one report
with the measures above, computed from the trail, no manual tallying.

**Acceptance Scenarios**:

1. **Given** a series at a difficulty, **When** it completes, **Then** the
   report includes program-adherence and revision counts (v4-specific
   measures) alongside v3's report measures.
2. **Given** an anomaly, **When** an analyst replays the tick range,
   **Then** events, programs, decisions and outcomes for exactly that
   range reconstruct in minutes (SC-007 behaviour carried).

---

### User Story 6 — Fast tactical tier: S1 reacts inside S2's envelope (P1)

The strategist's calls take minutes; raids take seconds. Between S2
pulses, the small fast model S1 micro-pulses and reacts to threats — but
ONLY within the delegation envelope the current program wrote into
`delegate[]` (allowed verbs, subject ships/scope, zones, expiry, optional
override rights). Outside the envelope S1 is a silent adviser. The
executor enforces the priority law program > S1 > reflex on every shared
ship, and refuses everything the envelope doesn't license. This closes
the strategist's ~2-min blind window without giving up single intent
authority.

**Why this priority**: with the measured ~2 min strategist latency, a
P1-tier seat cannot leave ~2 minutes ungoverned between pulses; but the
bound must be S2's own data, never driver doctrine.

**Independent Test**: with a P0 attack observed mid-gap between S2
pulses, S1's next micro-pulse issues in-envelope defense commands citing
program version + grant + decision id (`micro` trail rows); an
out-of-envelope attempt is blocked with the reason recorded and fed to
S1's next input; S2's next program shows the reaction reflected.

**Acceptance Scenarios**:

1. **Given** a program with a defense grant (`order_attack/guard` on
   scope `defense`), **When** a strike hits the defended zone between S2
   pulses, **Then** S1 retargets focus-fire / retreats within the grant
   within one micro-pulse, every command a `micro` row citing the grant.
2. **Given** no `delegate[]` in the current program, **When** attacks
   occur between pulses, **Then** S1 issues nothing (advisory only);
   P0 still wakes S2 for the re-plan (FR-019).
3. **Given** S1 attempting an action outside its grants (forbidden verb,
   foreign ship, out-of-zone), **When** validated, **Then** the item is
   blocked with an actionable verdict naming the licensed verbs/ships
   instead (FR-020), recorded as a violation, and the verdict feeds S1's
   next micro-input — never executed.

## Edge Cases

- Strategist pulse LATE (slow endpoint): the executor continues the last
  program; pulses never block the tick loop (async two-phase, carried from
  v3).
- Model output malformed: ≤1 bounded repair attempt per pulse; then the
  pulse aborts (last program stands) and the failure is recorded.
- Program subject ids stale after a match load: the executor revalidates
   subjects against a fresh ledger; unrecoverable items are marked failed
   with re-issue hints; no busy-assumptions. Same law for S1 micro-packets.
- S1 micro-command vs program item conflict: program wins UNLESS the
   envelope grant explicitly grants override for those ships/grants —
   every override recorded.
- Envelope violation (S1 attempts outside its grants): blocked at
   validation, actionable verdict recorded (FR-020), fed to S1's next
   micro-input; never executed.
- S2 names a nonexistent tech or hull (live-round: `MassDrive2Kt`): the
   executor denies the item with the LEGAL alternatives in the verdict
   (`researchable now: [...]` / `buildable now: [...]`); the async engine
   rejection (orderRejected aux) folds into the same verdict stream (FR-023
   behaviour of FR-020's sinks) — a guessed name is learned once, never
   repeated silently.
- Arc missing, stale, or over budget: driver code seeds/reseeds the arc
   from mechanical rollups, or clamps it with a verdict; the pulse input
   always renders a non-empty arc line (FR-021).
- Reflex floor vs parade collectors: parade/idle ResourceCollectors are
   continuously re-dispatched to the nearest-value asteroid by the reflex
   path (live-round: an RC sat in parade ~20k ticks producing nothing);
   reflex enforcement is per-tick, not one-shot (FR-006 clarified).
- Paused match: no pulses, no model calls on EITHER channel (game-time
   cadence, carried).
- Multiple v4 seats: separate drivers; one binding each (carried).
- Enemy invisible: S2 decides with empty knowledge; the observation says
  "nothing observed" (no fabrication).
- Program referencing allied players as targets: rejected at validation
  (allied protection, carried).
- Reflex set conflicting with a program's explicit item covering the same
  ship: program wins (reflexes are gap-fillers only).

## Requirements *(mandatory)*

### Functional Requirements

**Foundations (carried from spec 004 unchanged)**

- **FR-001**: Peer client only; every effect on the game is an ordinary
  validated player command; no privileged access; engine and sidecar
  untouched.
- **FR-002**: Exactly ONE binding owner — the v4 driver's client (the
  executor lane); all commands flow through the tri-state validator
  (registry-grounded, spec-003 single source).
- **FR-003**: All cadences in game time; paused matches produce no model
  calls; wall timestamps observability-only.

**V4 control architecture**

- **FR-004**: The S2 strategist's PROGRAM is the seat's sole strategic
  intent authority. The executor MUST NOT embody production priorities,
  build splits, research chains, attack thresholds, or any v3-era
  procedural doctrine: playbook behaviour MUST enter via prompt material
  for S2/S1 or configuration data that the models (not the executor)
  interpret. Fast tactical initiative belongs to S1 ONLY inside the
  program's delegation envelope (FR-017).
- **FR-005**: The executor is a deterministic translator: current program
   items → validated commands at the tick cadence (≤6 per tick, per-tick
   order ids), plus the enumerated reflex set and the S1 channel's
   validated micro-commands (FR-018). Program semantics are fixed
   order: production (build/research), tasking (fleet verbs), posture not
   directly executable (rendered into S2-style context for the NEXT pulse
   input, and its `delegate` field governs S1 legality). The executor also
   carries the teach duty (FR-020): it explains its own refuses, coercions
   and clamps as actionable verdicts — it never invents orders, it only
   explains its own decisions.
- **FR-006**: Reflex set (EXACTLY, v4): (a) idle collector → nearest
   asteroid harvest; (b) full collector → deliver/dock. "Idle" includes
   parade/no-harvest-order collectors — the reflex path re-checks and
   re-dispatches EVERY tick (continuous enforcement, not one-shot), unless
   a program item AND a live S1 envelope command covers those ships —
   program > S1 > reflex.
- **FR-007**: S2 program pulses every 90–240 s game time
  (configuration; default 120 s — sized to the measured ~2 min large-model
  latency) with immediate async wake on P0; S1 micro-pulses every 5–15 s
  game time (default 8 s) against the CURRENT program; ≤1 program per S2
  pulse and ≤1 micro-packet per S1 pulse; model calls never block the tick
  loop (the v3 async two-phase pattern, carried, both channels).
- **FR-008**: Program packets are schema-validated WHOLE before apply
  (all-or-nothing; previous program stands on failure with the reason
  fed to S2's next input); versions monotonic; full history recorded.
- **FR-009**: Pulse input is a budgeted observation (default ≤12k units)
   in which S2's view is the SUPERSET (live-round lesson: the strategist
   had LESS state than S1 and could not name targets, techs or hulls).
   Sections, in render order: arc + last posture (FR-021); P0 verbatim
   pinned; mechanical rollups since last pulse (builds materialized,
   kills, researches, spend — code-computed from the trail); program
   statuses per item incl. async engine orderRejected aux codes (FR-023);
   own ledger echo (per-ship ids/posture/cargo); ENEMY CONTACTS
   (id/hull/pos/dist-to-base — same view S1 receives); asteroid ledger
   (id/pos/value, nearest-first, top-N + totals); build menu (unlocked
   hulls + costs + current buildQueue state); research menu (researchable
   techs with missing prerequisites, labs count, lab-busy state). Static
   prefix layout for cache-friendly serving; budget enforcement per FR-013.
- **FR-010**: P0 classification (the v3 digest classifier's rule set,
  carried as config) wakes the strategist immediately; P0 items survive
  until superseded.
- **FR-011**: Postgres record store + exchange (spec 004 FR-021/FR-030
  carried): all messages/logging in `twincore_q.messages`; no local files;
  match ids unique per driver run; replayable kind/tick.
- **FR-012**: Degradation ladder (carried, with v4 wording): S2
  unreachable → the executor continues the last valid program, S1 (if
  alive) keeps acting strictly inside its existing envelope, plus
  reflexes (NO procedural doctrine fills in); S1 unreachable → last
  valid program + reflexes (executor does NOT absorb S1 duties);
  executor/driver down → seat idles; interface down → both idle; every
  transition recorded; recovery only via re-observe.
- **FR-013**: Budgets per channel are configuration (S2 in ≤12k; S2 out
   ≤3k; S1 in ≤4k; S1 out ≤900), enforced on the deliverable packet; breach
   → prune with recorded alert; never silent growth. PRUNE ORDER (both
   channels, least-to-most valuable, live-round lesson: memory/echo material
   is cut before world state): micro-history/s1_context first (FR-022),
   then arc clamping (length, not presence — FR-021), then event rollup
   history, then program echo, then ledger/contacts/menus — the world view
   is the last thing that ever gets cut, and the arc is clamped in length
   rather than dropped.
- **FR-014**: The v3 S1 implementation (s1tick/economy modules and their
  digest-window/directive protocol) stays retired in-tree, unreferenced.
  The v4 S1 channel is NEW code with its own contract (FR-018/FR-019) —
  no reuse of v3's s1 module semantics; same declared-retired discipline
  as v3's D7 for the old modules.
- **FR-015**: Model endpoints config-only per channel; local/LAN only.
- **FR-016**: The playbook document (A1–A7/B1–B4 behaviours) is
   playbook MATERIAL for S2's charter and S1's micro-charter (data,
   retunable), verified by the harness; it MUST NOT appear as code
   constants in the v4 driver.
- **FR-017**: The DELEGATION ENVELOPE is a field of the Program packet:
   a list of grants (allowed verbs, subject ships or scope classes,
   optional zones/conditions, optional override-of-tasking grants),
   versioned with the program. S1 commands are validated against the
   CURRENT envelope: outside it → blocked with an actionable verdict
   (FR-020), reason recorded, never executed; the envelope cannot grant
   build/research unless explicitly listed; no envelope ⇒ S1 is
   advisory-only that pulse. The envelope is also S1's filter on what it
   SEES: S1's live view shows scope membership per ship (in-scope markers).
- **FR-018**: S1 micro-packets are schema-validated WHOLE before apply
   (all-or-nothing; ≤1 bounded repair attempt per micro-pulse); each S1
   command cites the program version + envelope grant + S1 decision id,
   and is recorded as `micro` trail rows. S1 input is a budgeted compact
   observation (static micro-charter prefix, P0 verbatim pinned, live
   program + envelope echo, own previous micro-packet status) PLUS the
   rolling verdict section: recent executor denials/coercions so denied
   attempts teach the live rulebook instead of repeating by silence
   (FR-020), PLUS the S1 history window S2 allocated (FR-022).
- **FR-019**: P0 events wake BOTH channels out-of-cycle: S1 acts
   immediately within the envelope (defense micro), S2 is woken to
   re-plan at the next tick boundary's pulse slot; P0 items survive until
   superseded in both channels' inputs.
- **FR-020**: THE EXECUTOR IS A TEACHING FIREWALL. Every deny, coercion,
   or clamp it performs emits an ACTIONABLE VERDICT LINE naming what was
   refused and what IS currently allowed (licensed verbs/ships of the
   active grant; legal techs/hulls with costs; the cap that clamped).
   Verdict text streams to three sinks: (a) trail `validation` rows
   (measurable), (b) S2's per-item PROGRAM STATUS in the next pulse input,
   (c) S1's next micro input as a rolling verdict section. Engine-side
   asynchronous orderRejected/ship events fold into the same stream with
   their aux codes (FR-023) so nothing fails invisibly. The executor NEVER
   invents orders — it explains only its own decisions. Verdicts are
   bounded text within the channel's byte budget.
- **FR-021**: THE ARC — S2's compacted memory. The program packet carries
   an optional `arc` field (default budget ~600 chars): S2's own running
   high-level story (match phase, enemy shape shown, tried/failed, current
   plan), rewritten by S2 each pulse. The driver stores only the LATEST
   arc; the next pulse input renders it back as "# ARC (your summary so
   far)"; raw match history NEVER re-enters S2's window (the postgres
   trail keeps history for replay/harness, not for the model). A missing
   or stale arc is seeded/reseeded by code from mechanical rollups; an
   over-budget arc is length-clamped with a verdict (presence never
   dropped). Posture text folds into the same memory lane.
- **FR-022**: S1's HISTORY WINDOW IS S2-ALLOCATED. The program packet may
   carry `s1_context: {max_exchanges: int, ticks: int}`; the executor keeps
   S1 a rolling micro-history (previous micro-packets + their outcomes)
   truncated mechanically to exactly that window, subject to the FR-013
   ceiling (s1_context is the FIRST thing pruned under breach). Absent
   s1_context ⇒ S1 stays stateless per pulse (current behaviour).
   Widening/crushing the window is S2's tactical call (stale-threat memory
   vs fresh eyes), never driver doctrine.
- **FR-023**: ASYNC FEEDBACK. Engine events (orderRejected with aux codes,
   shipDestroyed, research completion) are consumed into per-item program
   status and the S1 micro statuses; a command the engine rejected is
   reported as `denied: <aux + reason>` through the FR-020 verdict sinks
   in the NEXT relevant input — issued (= executor applied) and accepted
   (= engine acked) are distinct outcomes, never conflated.

### Key Entities

- **Program**: version document — production[] (order_build/order_research
   items with params), tasking[] (verb + ships/target + optional
   schedule/priority), posture (advisory text rendered for the NEXT pulse
   input only), delegate[] (the delegation envelope: grants S2 gives S1 —
   verbs, subjects/scope, zones, override flags), arc (optional: S2's
   running compacted story, FR-021), s1_context (optional: S1's history
   window budget, FR-022), version (monotonic), issued_tick/pulse.
- **Arc**: the latest S2-authored compacted summary (budgeted); stored by
   the driver, rendered into every pulse input; never absent (code seeds
   from rollups); history lives in the trail, not in the window.
- **Verdict**: one actionable executor/engine-decision line (deny/coerce/
   clamp/denied-by-engine + what IS allowed); recorded as `validation`
   rows; rendered into S2's program status and S1's verdict section (FR-020).
- **DelegationEnvelope**: the program's `delegate[]` — validated together
   with the packet; the ONLY license S1 has to issue commands this
   program-generation; recorded for audit with every S1 action.
- **MicroPacket**: S1's per-micro-pulse deliverable — validated WHOLE
  against the envelope; tasking verbs only (production forbidden unless a
  grant explicitly lists order_build/order_research); each command cites
  program version + grant + decision id.
- **ProgramItem**: one item: id, verb (registry order verb), params,
  priority, optional one-shot flag (done when applied) vs standing
  (until revoked/superseded by a new program).
- **ProgramStatus / MicroStatus**: per-item (and per-micro-command)
  executor outcomes reported back each respective pulse-input: issued
  tick, command ids minted, resolution (applied / engine orderRejected
  aux / blocked by validator incl. outside-envelope), cited in the trail.
- **Reflex**: the enumerated set (FR-006); its actions recorded as
  command/validation rows (same as any other command path).
- **PulseInput (S2) / MicroInput (S1)**: budgeted compact state per
  channel; static-first, S2's pulse input carries program statuses,
  S1's carries micro statuses + envelope echo.
- **Recorder rows**: v3's kind taxonomy + new kinds: `program` (program
  application/status), `micro` (S1 packet application/violations);
  keep: `packet`/`revision` carry program history.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Fresh-skirmish matches (from tick 0) complete without the
  seat stalling; commands at cadence; zero invalid commands at the game
  boundary across ≥3 matches.
- **SC-002**: Production, research and fleet decisions are
   model-decided and traceable: strategic commands ≥90% cite a live
   program item, S1 micro-commands 100% cite their envelope grant +
   decision id (trail-verifiable); collectors harvesting within the first
   five minutes of every observed opening; S2's pulse input contains the
   superset sections (enemy contacts, asteroid ledger, build menu,
   research menu) in ≥95% of pulse inputs (trail-verifiable from archived
   input rows).
- **SC-003**: In ≥2 of 3 recorded matches the program versions advance
  (S2 re-plans from observed state) AND research progresses (≥1 tech
  accepted past the boot state's researched set in matches >15 min).
- **SC-004**: Reaction: P0 wake latency (event → channel input) ≤1 tick
   out-of-cycle in ≥95% of injected cases for BOTH channels (live
   injection round); S1 acts in-envelope within one micro-pulse of the
   wake.
- **SC-005**: Budget adherence ≥95% per channel (S2 in/out, S1 in/out);
   no growth trend; breaches recorded with prunes applying the FR-013
   prune order (arc length-clamped, never dropped; s1_context pruned
   first); arc present and non-empty in every pulse input; verdicts ≤
   their configured byte budget per channel.
- **SC-006**: Harness series vs the built-in CPU at each difficulty with
   the v3 report measures + program-adherence/revision counts +
   S1-envelope adherence and violation counts + NEW v3-aware measures:
   deny rate per channel (verdicts / attempted items), repeated-identical-
   denial rate (after FR-020 the same denial must not recur unchanged),
   verdict legibility (each verdict names at least one allowed
   alternative), arc freshness (arc byte-age from last S2 rewrite),
   reflex idle-RC re-dispatch latency (idle/parade collector → harvest
   order ≤1 tick): beginner majority wins; intermediate competitive
   (draws or better fraction); advanced measured+reported.
- **SC-007**: Replayability from the postgres trail alone for arbitrary
  tick ranges.
- **SC-008**: Degradation + coexistence matrix matches spec-004 §5
  observables with v4 wording (no procedural fallback).

## Assumptions

- **Substrate reuse**: v3 modules client/state_store/validator/digest/
  packet(simplified)/queue/queue-store/events/config/reorder-to-fit are
  REUSED as libraries; only the CONTROL modules (s1tick, economy, packet's
  directive semantics, s2pulse's digest-window logic) are replaced by the
  v4 executor/program loop. Retired v3 modules stay in-tree (cleanup is
  its own decision).
- **Cadence**: default S2 pulse 120 s game time (config, clamp 90–240);
   S1 micro-pulse default 8 s (config, clamp 5–15); executor tick ~1 s
   (config) — all in game time.
- **Endpoint mapping**: S2 on the large/slow endpoint (~2 min/output
   measured live) is the premise the cadence is sized to (pulse default
   120 s, config); S1 on the fast rack (.106, ~2.7 s/call with
   cache-reuse + draft-MTP measured live) for the micro-pulse tier both
   channels' endpoints stay config-only.
- **Playbook as prompt**: `documentation/community/cpu-player-playbook.md`
   feeds S2's charter and S1's micro-charter; its numbers are tuning
   suggestions, not invariants.
- **Three tiers by decision 17** (2026-10-23): v4 ships S1 inside a
   delegation envelope rather than the earlier S1-absent assumption —
   the defensive-reflex-only driver is no longer the default; if live
   rounds show the envelope tier unnecessary, config disables the S1
   channel entirely (reverting to program + reflex behaviour) without a
   spec change.
- **Draft v3 from live evidence** (2026-10-23, sessions 18/19 + user
   review): the teaching firewall (FR-020), the arc (FR-021), S1's
   history window (FR-022), async feedback (FR-023) and the FR-009
   superset input exist because the live round showed the strategist
   blind (no enemies/asteroids/menus in its input), guessing tech names
   that the engine rejected invisibly, and the seat inert (1 build item,
   no research, bank unspent) while the built-in CPU teched to frigates.
   Defaults: arc ~600 chars; s1_context absent ⇒ stateless S1; verdict
   budget small (hundreds of bytes) and counted against channel budgets.
- **The game**: unchanged; spec-003 layer unchanged; peer-player
   orientation (not an engine/lobby mode) carried from spec 004.
- **Testing convention**: launch-and-poll live rounds against the
  established multiplayer savegame workflow (cash-inject by default).