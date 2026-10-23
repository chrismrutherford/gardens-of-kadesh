# Feature Specification: Twin-Core Two-Model LLM CPU Player

**Feature**: `004-twin-core-two-model-llm-cpu-player`

**Created**: 2026-10-23

**Status**: Draft

**Input**: User request: "Make a design for these — the docs added to the
documentation folder. This is a 3rd iteration, so the existing agents are not
part of this." The source documents are
`documentation/community/090746_llm-cpu-player-two-model-design.md`
(the Twin-Core architecture proposal, with its PlantUML diagram) and
`documentation/community/090802_cpu-player-playbook.md` (the CPU strategy
playbook extracted from the game's documented computer-player intelligence).

## Summary

A CPU player driven by two cooperating language models, replacing the agent
stacks of iterations 1 and 2 (agent-multi LLM fleet; monolith fleet agent) with
a fresh architecture:

- **S1 — tactical core**: a fast small model that is the ONLY component bound
  to a match session. It watches a curated event window and issues the actual
  game commands every tick.
- **S2 — strategist**: a large model that NEVER touches the game and never
  sees raw event streams. On pulses it manages S1's intent: rewriting a
  versioned directive document, issuing standing orders, trimming or resetting
  S1's memory, and tuning what S1 pays attention to next.
- **Orchestrator**: deterministic code between them — clocks, an importance
  classifying digest engine, a canonical state store, a command validator, and
  an append-only recorder. No model ever bypasses it.

S1 is reflexes; S2 is intent. The behaviour this system re-embodies is the
1999 game's documented computer-player intelligence (the playbook: economy
saturation with recon discipline, a weighted attack menu, overkill-threshold
aggression, layered sphere defence, the hyperspace wallet).

**Out of scope (explicit)**: the iteration-1 and iteration-2 agent
implementations are NOT reused, extended, or maintained by this spec (their
specs — 001-agent-multi-llm-fleet, 001-fleet-refactor,
001-peer-instruction-queue — remain historical record). The MCP/aiplay layer
(specs 001-mcp-server-layer, 002, 003) is REQUIRED unchanged as the command
and event substrate. The LLM CPU is a peer player CLIENT only (see US4
orientation); any in-lobby integration, engine player-type registration,
match-visualization tooling, and tournament loops remain out of scope.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - An LLM CPU plays a complete, sane match from a boot directive (Priority: P1)

A host adds an LLM CPU to a skirmish. Without any strategist connected, the
tactical core alone runs the match on the boot playbook directive: it
dispatches collectors first, keeps guards over resource ships, scouts before
raiding, and applies the playbook's harassment discipline through a steady
stream of validated commands at the configured tick cadence. Every decision —
commands issued, rejections, their outcomes — lands in an append-only
recorder, so an offline observer can replay the whole match command-by-command.

**Why this priority**: this is the minimum viable CPU. It alone delivers an
LLM-driven opponent that plays the documented CPU playbook, and everything
later (strategist, evaluation) stands on it.

**Independent Test**: launch a match with one LLM CPU bound to a player seat
(strategist disconnected). The player opens (resource ships dispatched before
military units), issues commands every tick at the configured game-time
cadence, and reaches a fixed match end or natural match end without the seat
freezing or stalling. The recorder contains the full decision trail.

**Acceptance Scenarios**:

1. **Given** a fresh skirmish with an LLM CPU seat, **When** the match starts,
   **Then** within the first five minutes all initial collectors are
   dispatched to harvest (playbook opening A1) before offensive units are
   built, and the recorder shows every command that caused this.
2. **Given** the tactical core is running, **When** any tick elapses,
   **Then** the only effects on the game are ordinary validated player
   commands on the normal command stream — no privileged simulation access,
   and exactly one process owns the seat binding.
3. **Given** the tactical core proposes a command that violates bounds,
   alliance protection, or duplicates an in-flight order, **When** the
   validator reviews it, **Then** the command is rejected or clamped with a
   reason, the reason is returned to the tactical core as a first-class error,
   and nothing reaches the game.
4. **Given** a full recorded match, **When** an analyst replays the recorder
   contents, **Then** every tactical decision, its raw event context, and its
   outcome can be reconstructed without the live game.

---

### User Story 2 - Emergencies interrupt the machine immediately (Priority: P2)

While the steady loop runs, critical events — the player's base invaded, a
whole battle group annihilated, the command centre destroyed — break through
the normal cadence: the tactical core sees them at the very next tick at the
top of its context, and the strategist (if connected) is woken immediately
instead of waiting for its next scheduled pulse.

**Why this priority**: without interrupt handling the CPU plays polite-by-turn
while its base burns; every other CPU behaviour in the game reacts to distress
within its cadence, and so must this one.

**Independent Test**: in a running match, inject (via the game) an attack on
the LLM CPU's resource zone. Observe both lanes react: a defensive reaction at
the next tick from the tactical core, and (with strategist connected) a
strategist pulse arriving far sooner than the normal pulse interval.

**Acceptance Scenarios**:

1. **Given** a hostile force entering the LLM CPU's defended zone, **When**
   the event is classified, **Then** it is marked critical, reaches the
   tactical core at the next tick ahead of ordinary chatter, and the defensive
   reaction (recall guards / converge idle ships, playbook A4) follows.
2. **Given** a strategist connected and a critical event, **When** the event
   arrives, **Then** the strategist wakes immediately (async), reads a
   critical-classified digest, and its resulting control packet applies at a
   tick boundary — without delaying the tactical loop.
3. **Given** no critical events, **When** the digest engine trims old
   information, **Then** critical events are never dropped by trimming, only
   ordinary chatter is.

---

### User Story 3 - The strategist visibly steers doctrine mid-match (Priority: P3)

With the full twin-core connected, the large model receives compact,
schema-shaped digests of the match and issues control packets: a patched
directive (build priorities, aggression posture, doctrine), standing orders
with expiry and abort conditions, memory operations for the tactical core, and
targeted questions answered next pulse. Doctrine changes are sticky — a
primary enemy change requires accumulated evidence and a minimum hold time —
and the tactical core holds an emergency veto it must report honestly.

**Why this priority**: the differentiating behaviour (doctrine adaptation like
the game's fleet-command cycle — reframing attacks on evidence) builds on the
stable tactical floor; it must not destabilise it.

**Independent Test**: run a scripted harness scenario in which the evidence
justifies a doctrine change (e.g. sustained enemy economy raids). The
strategist's control packets change the tactical core's behaviour (watcher
emphasis, response posture) within one pulse, and the directive revision
history in the recorder shows the patch, its version bump, and the evidence
digests that preceded it.

**Acceptance Scenarios**:

1. **Given** a strategist connected, **When** a pulse fires, **Then** it
   emits exactly one schema-validated control packet and never a game command
   or raw observation request; all influence arrives through the packet
   protocol.
2. **Given** a valid directive patch, **When** it applies, **Then** it applies
   at a tactical tick boundary as a new versioned directive, and the
   tactical core's conversation history is trimmed or reset as the packet
   requests, collapsing to seed context without losing in-flight orders.
3. **Given** repeated digest evidence for a target change, **When** the
   strategist switches its pinned primary enemy, **Then** the switch required
   an evidence threshold and a minimum hold time (no oscillation between
   consecutive pulses, no reaction to anything outside digest content).
4. **Given** a standing order whose pursuit would endanger the seat (e.g.
   holding position while base burns), **When** the tactical core exercises
   its emergency veto, **Then** it may suspend the order and MUST report the
   violation and its reason in the next digest — visible in the recorder.
5. **Given** an invalid or schema-violating control packet, **When** the
   orchestrator validates it, **Then** the previous directive remains in
   force, the packet is logged with its failure reason, and the strategist
   is not retried within the same pulse.

---

### User Story 4 - Coexistence, degradation, and the built-in AI (Priority: P4)

**Orientation (read this before implementing)**: the LLM CPU is NOT a
built-in player type, engine mode, or lobby-selection feature. It is a
PLAYER: an external peer-client process (the driver) that attaches to an
unoccupied seat through the existing `aiSession.bind` interface — the same
way the game's UI or a human player does. There is no lobby work, no new
CPU kind in the engine, and no personality selection UI anywhere in scope.
Where this spec says "picks an LLM CPU seat", the operational meaning is:
the host leaves a slot open (or rebinds a slot) and STARTS THE DRIVER for
it; built-in CPU seats are ordinary match participants that stay configured
as-is.

The LLM CPU is one more player, not a replacement. The game's built-in
computer player remains unchanged. When any sub-system degrades, the seat
degrades
predictably: strategist disconnected → tactical core continues on its last
valid directive; tactical core disconnected → the seat idles (no commands);
the whole driver disconnected → the seat is idle and the match is unaffected;
under no circumstance does a remaining component guess, fabricate state, or
race for the binding.

**Why this priority**: protects the game itself (constitution: faithfulness,
single binding, determinism) and gives hosts a safe way to run the LLM CPU
experimentally alongside normal play.

**Independent Test**: run four short matches: built-in CPU only (unchanged
behaviour), LLM CPU with strategist killed mid-match (seat keeps executing
last directive), tactical core killed mid-match (seat idles, match proceeds),
driver killed mid-match (match proceeds cleanly).

**Acceptance Scenarios**:

1. **Given** a match with one or more built-in CPU seats, **When** the host
   starts the driver for another (unoccupied) seat, **Then** both coexist in
   one match with their normal semantics, and the built-in CPUs' behaviour
   is unchanged by the LLM seat's presence.
2. **Given** the strategist process lost mid-match, **When** pulses fail,
   **Then** the tactical core keeps executing the last valid directive (boot
   default playbook doctrine if none), the absence is logged, and no commands
   are invented on the strategist's behalf.
3. **Given** the tactical core lost mid-match, **When** ticks fail to
   produce decisions, **Then** the seat issues no commands (fleet idles) and
   the strategist is not silently granted binding rights.
4. **Given** the entire driver lost mid-match, **When** the match continues,
   **Then** the seat is simply idle and a later re-attach resumes from
   observed state (re-observe on any rematch/load: identifiers renumber).

---

### User Story 5 - Evaluation harness makes quality measurable and debuggable (Priority: P5)

A repeatable evaluation mode runs matches: LLM CPU versus the built-in CPU at
each difficulty, plus replay clips drawn from recorded matches. Reports cover
win/loss shape, cadence adherence, context budget adherence per channel, and
digest cost per pulse — measured, not assumed.

**Why this priority**: everything above can be wrong in ways only visible over
series of matches; the harness is how milestones get accepted and regressions
caught.

**Independent Test**: run the harness over N matches at each difficulty
preset from recorded configurations and receive one report per series with the
measures above, without any manual intervention.

**Acceptance Scenarios**:

1. **Given** the harness invoked for a difficulty preset series, **When** the
   series completes, **Then** a report exists per series containing
   win/loss/draw counts, command cadence adherence, per-channel context
   budgets (observed vs configured), rejection rates, and digest volumes.
2. **Given** a recorded anomaly reported by a human observer, **When** the
   analyst opens the replay for those ticks, **Then** the events, digests,
   directive versions, decisions and outcomes for exactly that tick range can
   be extracted in minutes.

### Edge Cases

- Player alliance formed or broken mid-match: alliance events flow as
  ordinary priority events; the validator MUST block any attack command
  against an ally at submit time, and standing orders against a former ally
  MUST surface as failed (with reason) in the next digest rather than silently
  persist.
- Match restart, load, or autosave-resume: ship and asteroid identifiers
  renumber; the state store MUST rebuild from a fresh observation, and all
  in-flight orders referencing stale identifiers MUST be invalidated with
  re-issue, not assumed busy.
- Malformed or empty tactical output: repeated invalid responses MUST be
  retried at most a bounded number of times per tick with the validator's
  reason attached; if still invalid, the tick idles (no guessing) and the
  streak is recorded.
- Context budget breach on any channel: trigger the channel's reset discipline
  (trim, then seed-reset) and alert in the recorder — never silently grow the
  context.
- Paused or single-stepped match: all cadences are expressed in game time
  (ticks), so a paused match produces NO model calls for commands and no
  wall-clock pressure; resumed play continues from observed state.
- Multiple LLM CPU seats in one match: separate driver instances, each owning
  exactly one seat binding; two drivers MUST NOT bind the same seat.
- Enemy completely invisible (no sensor contact): knowledge stays empty-class;
  the digest states "nothing observed" rather than fabricating positions.

## Requirements *(mandatory)*

### Functional Requirements

**Peer-client foundations (constitution I, II, VI)**

- **FR-001**: The twin-core MUST operate strictly as a peer client: every
  effect on the game is an ordinary player command issued on the normal
  command stream through the existing command/event interface layer; the
  system MUST NOT claim privileged simulation access or alter engine
  behaviour.
- **FR-002**: Exactly ONE component (the tactical core's command path) MUST
  own the seat binding; the strategist and all other components MUST be
  incapable of binding or issuing orders directly.
- **FR-003**: All cadences (tactical tick, strategist pulse, interrupt
  wake-ups, order expiry) MUST be expressed in game time relative to observed
  ticks; gameplay-affecting behaviour MUST NOT depend on wall-clock timing
  (determinism at a given seed and game state).

**Tactical core loop (S1)**

- **FR-004**: Every tactical tick MUST assemble the tactical context from
  fixed anatomy: static role card, current directive version, recent event
  window (newest deltas plus the newest interval rollups), live state echo,
  and in-flight orders — within the configured per-tick context budget
  (default ≤8k units of model context) — regardless of match length.
- **FR-005**: The tactical core MUST issue game commands only through the
  command validator. Valid command sets MUST be honoured in order; rejected
  commands MUST NOT reach the game.
- **FR-006**: The validator MUST check every proposed command against: the
  enumerated command surface (grounded 1:1 on the command interface's own
  registry), alliance protection (never attack allies), identifier/bounds
  validity, resource affordability surfaced by the interface, and duplicate
  suppression. Results MUST be tri-state: accept, reject (with reason and
  hint), or clamp (with original vs corrected command).
- **FR-007**: Rejections and clamps MUST return to the tactical core as
  first-class errors with actionable reasons in the SAME tick or next tick —
  never silently dropped.
- **FR-008**: The tactical core MUST NOT carry durable memory: its
  conversation MUST be safe to trim or fully reset at any tick boundary; all
  durable knowledge MUST live in the state store.

**Digest engine and knowledge**

- **FR-009**: The event stream MUST be consumed as deltas against the last
  seen state; raw high-frequency event volume MUST NOT be shipped to any
  model beyond the immediate tactical window without first passing importance
  classification.
- **FR-010**: Events MUST be classified into three importance classes: P0
  break-interrupts (base invaded, battle group annihilated, command centre
  lost/under lethal threat), P1 (report at next window/digest), P2 (rollup
  only). Class definitions MUST be configuration, with the playbook-derived
  defaults above.
- **FR-011**: P0 events MUST (a) enter the tactical core's window at top of
  the next tick and (b) wake the strategist immediately (when connected);
  (c) never be evicted by history trimming; P2 MUST evaporate into rollups,
  P1 survive one digest cycle, P0 survive until superseded by outcome.
- **FR-012**: Older raw deltas MUST be replaced by interval rollups (1–2
  summary lines per interval) so the tactical window size remains bounded as
  the match grows.
- **FR-013**: A canonical state store MUST be the single reference both
  models consult: own fleet inventory and unit states, resource bank and
  income projection, enemy knowledge accumulated per hull class ever observed
  (the game's own knowledge model), group threat/strength ratings per spatial
  blob, alliance state, and pending orders with statuses. Models MUST NOT
  make decisions from private battlefield memory claims; S2 claims MUST cite
  state-store numbers in digests (enforced by digest schema).
- **FR-014**: Every strategist question MUST be answered at its next pulse
  from the state store only — no live game queries from the strategist.

**Strategist protocol (S2)**

- **FR-015**: The strategist MUST influence the game exclusively through a
  control-packet protocol with five elements: directive patch (structured,
  schema-validated, versioned), standing orders (each with kind, subject,
  priority, time-to-live, and abort conditions), history operations (trim
  scope or full reset with state seed), digest configuration (emphasis and
  suppression for the next cycle), and observation questions. The strategist
  MUST NOT receive raw events, issue game commands, or communicate with the
  tactical core outside this protocol.
- **FR-016**: Directive patches MUST version monotonically and MUST NOT
  rewrite wholesale: patches are semantic edits validated against the
  directive schema, applied at tactical tick boundaries only, with the full
  revision history retained by the recorder.
- **FR-017**: Sticky goals: at most ONE pinned primary enemy and a small set
  of pinned objectives; changing either MUST require accumulated evidence in
  digests plus a minimum hold time (configuration; playbook-derived defaults).
  Digest-driven adaptation MUST NOT react to anything outside the digest
  stream (no rubber-banding to human mood or chat).
- **FR-018**: Standing orders MUST expire (TTL), MUST carry explicit abort
  conditions, and MUST default to emergency-vetoable; the tactical core MAY
  suspend a vetoable order under declared emergency and MUST report the
  violation plus reason in the next digest (via a dedicated notes field).
- **FR-019**: Strategist pulse cadence MUST be configuration (default range
  30–90 seconds of game time), with immediate wake on P0. A pulse emits at
  most one control packet (bounded output budget, default ≤2k units), within
  a bounded input budget (default ≤24k units) of digests, milestones, ledger,
  pinned goals with age, and its own previous plan and outcomes.

**Budgets and recording**

- **FR-020**: Context budgets per channel MUST be configuration constants
  (tactical input ≤8k, tactical output ≤200, digest 300–800, strategist input
  ≤24k, strategist output ≤2k — defaults per the design document) and budget
  breach MUST trigger that channel's reset discipline plus a recorded alert —
  never silent growth. Observed usage per channel MUST be recorded per tick
  and per pulse.
- **FR-021**: An append-only recorder MUST capture, for a running match: raw
  events (ring-buffered), importance classifications, digests, control
  packets (accepted AND rejected, with reasons), directive revisions (full
  history), tactical decisions with outcomes, command validation results, and
  per-channel usage accounting. The recorder MUST be sufficient to replay any
  match segment offline. AMENDED 2026-10-23 (FR-030 merge): the record store
  is the Postgres queue table on 192.168.1.152/twincore_q — NO local files
  for messages or logging; per-kind files are replaced by kind-filtered
  queries; rows carry {match_id, tick, wall_ts(envelope)}; match ids are
  unique per driver run. Postgres unavailability degrades to the in-memory
  ring + a recorded gap, never a gameplay-affecting failure.
- **FR-022**: Static-first prompt layout MUST be used for the tactical core
  (role card and directive versioned prefix precede the mutable window) so
  cache-friendly serving is preserved and per-tick cost stays low.

**Doctrine, difficulty, economy, coexistence**

- **FR-023**: The boot state MUST be a valid directive: the default playbook
  doctrine (dock-and-defend conservative opening: collectors dispatch,
  guards on resource ships, no early aggression) until the strategist
  (connected later) issues its first accepted patch.
- **FR-024**: Degradation ladder (MUST): strategist unreachable → tactical
  core continues on last valid directive; tactical core unreachable → seat
  issues no commands (fleet idles); entire driver unreachable → seat idle;
  interface unreachable → both go idle. No component may guess or fabricate
  state it cannot observe. Every degradation transition MUST be recorded.
- **FR-025**: The built-in deterministic computer player MUST remain
  unchanged; the LLM CPU is a peer PLAYER (an external driver process) that
  attaches to an unoccupied seat via the existing session-bind interface —
  no engine work, no lobby feature, and it MUST coexist with built-in CPU
  seats in the same match.
- **FR-026**: Difficulty presets MUST reuse the ONE directive schema, varying
  only parameters by level (build split, attack thresholds, cadence, feature
  flags derived from the game's own difficulty data — e.g. beginner cadence
  ~2s, advanced ~0.5s of game time; advanced-only plays flagged off at lower
  levels). No separate code path per level.
- **FR-027**: v1 economy MUST be deterministic orchestration-code (mirroring
  the game's resource-manager doctrine: collectors → controllers → support
  ratio, the fixed build split as budget policy), issuing economy commands
  through the SAME validator and binding. The tactical core focuses on
  combat, defence and reaction; widening the tactical core's economy
  authority is a documented decision deferred beyond v1.
- **FR-028**: Model endpoints (sizes, local or hosted) MUST be configuration
  per LLM-channel with local-IPC-only exposure; the architecture MUST NOT
  require any specific provider or model size — the design's size classes are
  recommendations, verified by the harness, not requirements.
- **FR-029**: Tactical tick cadence MUST be configuration (default ~1 second
  of game time) with combat-intensity gating allowed to temporarily
  accelerate ticks (playbook-aligned difficulty behaviour); both bounds MUST
  be configuration, never hardcoded.

**Infrastructure rider (2026-10-23)**

- **FR-030**: A Postgres-backed message queue MUST serve two purposes:
  (a) full-trail LOG — it IS the record store (FR-021 amended: every
  message/decision lands in the queue; zero logging files), and (b)
  realtime message EXCHANGE — components (and later: harness/analyst
  tooling) exchange wake/note messages through the queue (polling consume
  with SKIP LOCKED + pg_notify listeners; DSN configuration).
  The queue MUST NOT be a command path: only non-binding signal kinds
  (wake, note) may be consumed inbound; order/command effects remain the
  tactical core's sole domain (FR-002). Queue rows carry game-time tick
  and wall timestamps observability-only (FR-003). Queue unavailability
  degrades to in-memory recording + gap recording, never a
  gameplay-affecting failure.

### Key Entities *(include if feature involves data)*

- **Directive**: the strategist-owned, versioned intent document for the
  tactical core — primary enemy (≤1, sticky), doctrine (defence tiers, harass
  cadence, aggression), build split (mirroring the 30/30/40 budget policy),
  standing orders, attentions (what to watch and how loudly), version number.
- **Control packet**: one strategist pulse's complete output — directive
  patch, standing orders, history operation, digest configuration,
  observation questions — schema-validated before partial application.
- **Event delta / rollup**: a tick-scoped change list (what became visible,
  what was built, destroyed, repaired, moved, formed) and its 1–2 line
  interval summary that replaces older deltas.
- **Digest**: the strategist-facing compacted match record for a pulse
  interval — verbatim P0 items, noteworthy changes, order outcomes with
  reasons, tactical core's one-line explanations for any disobedience,
  projections (income, threat estimate).
- **State store (ledger)**: canonical world model — own fleet inventory and
  unit states, bank and income projection, per-class enemy knowledge, blob
  ratings, alliances, pending orders.
- **Standing order**: a durable strategist instruction with kind, subject,
  constraints, priority, TTL, abort condition, and vetoability.
- **Validation result**: tri-state accept/reject(with reason, hint)/clamp
  (original → corrected).
- **Recorder record**: append-only match artefacts — events, digests,
  packets, decisions, revisions, usage accounting — replayable.
- **Difficulty preset**: per-level parameter set over the one directive
  schema derived from the game's own difficulty data.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An unattended match with an LLM CPU seat completes without the
  seat stalling: commands are issued at the configured game-time cadence from
  opening to match end (or a fixed observation duration), across ≥3 test
  matches, with zero invalid commands reaching the game (validation rate 100%
  at the game boundary).
- **SC-002**: The opening matches the playbook: in ≥80% of observed openings,
  initial collectors are dispatched within the first five minutes before any
  offensive unit is ordered, and a defensive guard exists over resource
  gathering during that window (recorder-verifiable).
- **SC-003**: Emergencies break the cadence: a P0 event is visible to the
  tactical core at the next tick in ≥95% of injected cases; with the
  strategist connected, an out-of-cycle pulse occurs in the same 30–90s
  window (recorder-verifiable).
- **SC-004**: Doctrine adaptation is real but sticky: in scripted harness
  scenarios, a justified doctrine change arrives within one pulse of the
  evidence threshold being met, AND zero primary-enemy changes occur within
  the configured minimum hold time of a previous change (recorder-verifiable
  over a full series).
- **SC-005**: Context discipline holds end-to-end: per-channel token usage
  stays within configured budgets for ≥95% of observations over full
  matches, and shows no growing trend with match length; every breach event
  is recorded with its reset.
- **SC-006**: Versus the built-in computer player, the series report shows:
  majority wins at beginner difficulty; competitive play (draws or better in
  a meaningful fraction) at intermediate difficulty; advanced difficulty
  measured and reported without a pass/fail bar in v1. Cadence, budgets and
  rejection rates are reported for every series.
- **SC-007**: Replayability: from recorder contents alone, any reported
  anomaly can be reconstructed for an arbitrary tick range (events, digests,
  directive versions, decisions, outcomes) in minutes — without the live
  match.
- **SC-008**: Coexistence: matches containing both a built-in CPU and an LLM
  CPU seat run to completion with unchanged built-in CPU behaviour, and each
  degradation scenario (strategist loss, tactical loss, driver loss) matches
  the behaviour required by FR-024 in test runs.

## Assumptions

- **Iteration boundary**: the iteration-1/iteration-2 agent implementations
  are retired for this feature — not reused, not extended, not maintained by
  this spec. Any deprecation marking is a rider decision in the plan, not a
  task of this spec. The MCP/aiplay interface layer (spec 003 surface, its
  registry and error envelopes) is assumed present and unchanged as the
  substrate.
- **Command surface**: the exact valid command enumeration is taken from the
  interface layer's own registry (single source of truth; the design document
  is indifferent to the count — the repo's registry, not the external "~121"
  estimate, is authoritative).
- **External reference docs**: the behaviour inventory referenced by the
  source documents (CPU intelligence contracts and baseline data) lives
  outside this repository; the community playbook document in this repo is
  the in-repo behaviour reference for doctrine and constants, and its numbers
  are treated as retunable data, not laws.
- **Economy split (v1)**: deterministic economy layer first, as recommended
  by the source design; the tactical core owns combat/defence/reaction. This
  mirrors the game's own resource-manager separation and saves tactical
  context budget.
- **Model hosting**: strategist is hosted locally first (the design assumes a
  local GPU rack), but endpoints are configuration; hosted endpoints are
  equally legal. Size classes (small tactical, large strategist) are
  recommendations measured by the harness, not gates.
- **Cadences**: defaults (tactical ~1s game time, combat-gated; strategist
  30–90s game time) are starting points to be tuned by the harness;
  difficulty presets may alter them per the game's own cadence data.
- **Personality presets**: doctrine presets (harass-leaning, turtle,
  balanced) are expressible as directive data (config/directive patches
  only); there is NO lobby or UI exposure of personality selection in any
  iteration of this spec (out of scope outright, not deferred).
- **Testing convention**: debug/verification rounds launch against the
  established multiplayer savegame workflow used throughout the repo's
  recent testing.
- **One driver per seat**: a driver instance owns exactly one bound seat;
  multiple LLM CPU players = multiple instances. Racing for one seat is a
  contract violation, detectable by the binding layer.