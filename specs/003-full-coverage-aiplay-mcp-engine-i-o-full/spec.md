# Feature Specification: Fully Fledged MCP for Engine I/O

**Feature**: `003-full-coverage-aiplay-mcp-engine-i-o-full`

**Created**: 2026-10-21

**Status**: Draft

**Input**: User description: "produce a spec for a fully fledged MCP for every
possible input and output from the gaming engine, properly mapped to JSON with
an encoder/decoder. Cover everything by carefully working through all the
source."

## User Scenarios & Testing *(mandatory)*

The "users" of this feature are AI agents and tooling (the LLM fleet: commander,
strategist, analyst, economist, critic) plus human operators who inspect the
protocol. The engine (source under `src/`, AIPlay layer in `src/AIPlay/`)
already exposes everything the game state can express; today only a partial
subset is reachable through MCP (`tools/aiplay_mcp.py`, 22 tools over a
hand-mapped UDS line protocol). This feature makes the mapping complete,
systematic, and loss-less: every method, every order verb, every error code,
every event type, every snapshot field the engine can emit has a defined MCP
representation, and there is a bidirectional encoder/decoder between the two
protocol layers so nothing is hand-transcribed per-tool.

### User Story 1 - AI agent observes complete game state over MCP (Priority: P1)

An AI agent asks the MCP server for the current world picture and receives, in
one structured response, everything the engine's AIPlay layer can serialize:
match state, players and RUs, ships with health/weapons/current order, harvest
resources with values, build queue, active research, the exact tech tree
(owned labs, researchable flag, locked hulls with missing techs, researched
techs), and match status (running/paused/bound player/winner/per-player
alive-lost). The agent can request sections and owner filters, and can rely on
the same schema regardless of transport (UDS line protocol or MCP tool).

**Why this priority**: observation is the foundation; every other capability
(analysts reacting to danger, economy planning, research paths) depends on
complete, loss-less observation. Today's mapping already covers most of this
but leaks detail (ship cap, missing attack-context) and duplicates schema
knowledge in two places (C writer + python compressor).

**Independent Test**: connect an MCP client, call the snapshot tool with and
without section/owner filters, and assert every field of the engine's
authoritative schema is present, typed, and consistent with the raw UDS
response.

**Acceptance Scenarios**:

1. **Given** a running match with the AIPlay layer enabled, **When** any MCP
   client requests a full snapshot, **Then** the response contains all
   authoritative sections (status, players, ships, resources, buildQueue,
   research, techTree, matchStatus, income) with the exact field set defined
   in Key Entities, and no section silently lost to a hard-coded filter.
2. **Given** the same request re-issued in the same tick, **When** the engine
   is paused, **Then** the two responses are byte-identical (determinism).
3. **Given** a parametrized request (`sections: ["ships"]`,
   `owner: 0`), **When** executed, **Then** only the requested section appears
   and only objects of the given owner are present, mirroring the engine's
   snapshot filter semantics.
4. **Given** more ships exist than the current client-side compression cap
   of 80, **When** the full snapshot is requested without compression
   parameters, **Then** all ships are present (compression is a client-side,
   opt-in behavior, never an engine-faithfulness limit).

---

### User Story 2 - AI agent issues every order verb over MCP (Priority: P1)

An AI agent can execute every command the engine backend supports — move,
attack, guard, dock, harvest, special, halt, build, research, formation,
tactics, launch, autolaunch, scuttle, select — with typed parameters matching
the engine's order model, and receive a synchronous, truthful acknowledgment.
Rejections carry the exact engine error code (E_NO_SESSION, E_BAD_ID,
E_UNAFFORDABLE, E_NO_BUILDER, E_NO_LABS, E_MATCH_OVER, E_QUEUE_FULL,
E_BAD_REQUEST, E_INTERNAL) both synchronously (submit-time validation) and
asynchronously (orderRejected events with the numeric error as `aux`).

**Why this priority**: commanding is the point of the layer. Async/submit
splits are what burned the fleet during play (a build rejected at the tick
boundary after a "queued: true" response).

**Independent Test**: for each verb, issue a valid and an invalid order over
MCP and assert the response and subsequent events match the engine's
documented behavior.

**Acceptance Scenarios**:

1. **Given** a running match, bound player, and owned ships, **When** a valid
   order of any verb is issued over MCP, **Then** the response is
   `{queued: true, orderId: <id>}` and later an `orderApplied` event echoes
   the same `id`.
2. **Given** an order that the engine will reject at dispatch (bad ship
   type name, unaffordable build, no free lab, dead targets), **When**
   possible for the sidecar to know at submit time (static name/type checks),
   **Then** the submission is rejected synchronously with the engine's error
   code instead of returning "queued" and only surfacing in an asynchronous
   `orderRejected` event.
3. **Given** all rejection paths, **When** an order is rejected at any stage,
   **Then** the error string in the response or the `aux` field of the
   orderRejected event is exactly one of the enumerated engine codes, with a
   documented mapping from enum value to code name.
4. **Given** the order queue, **When** more than the capacity of orders is
   submitted between ticks, **Then** the overflow is rejected with
   E_QUEUE_FULL and the queue drains in submission order.

---

### User Story 3 - AI agent receives complete event stream (Priority: P1)

An AI agent can subscribe to/poll the event log over MCP and see every event
the engine pushes — matchStarted, matchOver (with winner in aux),
orderApplied, orderRejected (with error code in aux), shipDestroyed (with
killer owner+1 in aux), bindChanged, matchContinued — in newest-first order,
bounded by the engine's 256-entry ring, with stable envelope ids.

**Why this priority**: without complete asynchronous truth, agents mis-plan
(the exact "analyst can't see the danger" failure observed in play).

**Independent Test**: drive a scripted scenario (issue an order that will be
applied, one that will be rejected, destroy a ship, pause/resume, rebind) and
assert the events appear in order with correct fields.

**Acceptance Scenarios**:

1. **Given** any of the seven event types occurring, **When** the agent later
   polls events over MCP, **Then** the event is present with its
   authoritative `{type, tick, id, aux, owner}` tuple.
2. **Given** a destroyed ship, **When** polled, **Then** a shipDestroyed
   event exists where `aux` identifies the killer owner (killer+1, 0 for
   unknown/evicted) — the only "under attack" signal currently surfaced; the
   spec additionally requires documented, per-ship damage visibility
   (see FR-303).
3. **Given** 300+ events, **When** polled, **Then** only the newest 256 are
   returned (ring semantics documented, no truncation error).

---

### User Story 4 - AI agent controls session state and time (Priority: P2)

An AI agent can bind/unbind-verify session state, save the match with a safe
name, and control simulation time: pause, resume, and step N ticks while
paused. Responses mirror the engine's exactly (bind result, status fields,
stepped/tick, saved name after filename sanitation).

**Why this priority**: needed for deterministic testing, match lifecycle edge
handling, and the operator save loop, but less frequently exercised than
observe/order.

**Independent Test**: run a binding/time-control cycle over MCP and compare
against the raw UDS responses.

**Acceptance Scenarios**:

1. **Given** an unbound session, **When** binding to a valid player index,
   **Then** the response carries `playerIndex`, `sessionId` (protocol
   version), and `mapName`, and a `bindChanged` event follows.
2. **Given** a bound session, **When** another bind to a different player is
   attempted, **Then** it is rejected synchronously (exactly one binding) and
   re-binding the same player is a no-op that emits matchContinued.
3. **Given** a running match, **When** `step` is called unpause, **Then**
   E_BAD_REQUEST is returned; **When** called in paused state, **Then**
   `{stepped: N, tick: <new tick>}` is returned.
4. **Given** `game.save` with or without a name, **Then** the name is
   restricted to safe characters (sanitized, engine-side) and the response
   returns the final saved name.

---

### User Story 5 - Single schema, dual transport, no drift (Priority: P1)

The protocol truth lives in one authoritative contract — a typed schema
covering every field — and both transports (raw UDS line protocol from the
engine, MCP tools) are generated or validated against it. Encoding/decoding
between MCP tool calls and UDS JSON-RPC-style line messages (id echo rules,
params shaping, result wrapping, async ordering) is centralized in one
encoder/decoder pair so new fields are added once, not transcribed twice.

**Why this priority**: the repeated session failures (duplicate-key
suppression bugs, `researching: null` vs `research: []`, "queued: true" then
async rejection) came from schema drift between the C layer writer, the sidecar
mapping, and the agent compressors.

**Independent Test**: round-trip property — every UDS response the engine can
emit, when put through the decoder and re-encoded via the MCP schema, yields
the same JSON values (field-compatible round trip), and every MCP tool call
decodes to exactly one UDS wire message.

**Acceptance Scenarios**:

1. **Given** any engine response line, **When** decoded and re-encoded,
   **Then** the result preserves all fields/values (loss-less mapping).
2. **Given** the tool surface, **When** enumerated, **Then** it covers 100% of
   the clWrap funnels and order verbs (nothing missing, nothing invented) and
   each tool's JSON Schema matches the engine's parameter parsing exactly
   (types, optionality, defaults, and the engine's fallback coercion rules).
3. **Given** a schema change (new snapshot field or event), **Then** the
   change is made in the single schema source-of-truth and both transports
   pick it up without further edits.

---

### Edge Cases

- **Stale id handling**: ship/resource ids may go stale between ticks; an
  order referencing an evicted id must yield E_BAD_ID (engine already
  enforces). Id 0 is never valid.
- **Session disabled / lobby / matchOver**: every method behaves per engine
  contract — E_NO_SESSION when the layer is off (no `--ai-play`), matchOver
  gates binding and orders; snapshot and events remain readable.
- **Malformed input**: unparseable JSON → E_BAD_REQUEST with id `null`;
  missing method → E_BAD_REQUEST; unknown method → E_BAD_REQUEST. Non-integer
  order ids are echoed verbatim; missing id echoes `null`.
- **Garbage numeric states**: ships pending removal can carry absurd health;
  the encoder must clamp such health to 0 exactly like the engine writer does
  (health < 0 or > 2x max).
- **ResearchShip-as-lab**: techTree.labs counts actual ResearchShips; killing
  one mid-research must reflect in labs count and subsequent E_NO_LABS.
- **Save name sanitation**: characters outside `[A-Za-z0-9 _-]` are replaced
  by `-` engine-side; names default to `aiplay-autosave-<tick>` when empty.
- **Ring overflow**: >256 events, >128 queued orders, >4096 id-map entries
  are all bounded behaviors with documented outcomes (eviction, E_QUEUE_FULL,
  id 0 mapping exhaustion).
- **Order id echo**: the request `id` is a caller-chosen id echoed in
  events (distinct from envelope `id`); the encoder/decoder must keep the two
  distinct id namespaces straight (source of the session's "queued: true with
  wrong id" confusion).

## Requirements *(mandatory)*

### Functional Requirements

Coverage requirements (the "everything" clause, derived from full source
inventory of `src/AIPlay/*` and `tools/aiplay_mcp.py`):

- **FR-001**: The command surface's source of truth is the human command
  funnel, `src/Game/CommandWrap.h clWrap*` (21 entry points) — "everything a
  player can do". The MCP tool surface MUST cover every AIPlay line method:
  `aiSession.bind`, `aiSession.status`, `state.snapshot`, `state.events`,
  every `order.<verb>`, `game.save`, `time.pause`, `time.resume`,
  `time.step` — and the order-verb set MUST be the clWrap superset: the 15
  verbs the AI layer exposes today (move, attack, guard, dock, harvest,
  special, halt, build, research, formation, tactics, launch, autolaunch,
  scuttle, select) PLUS the funnels not yet exposed, which the AIPlay
  backend MUST gain: kamikaze (clWrapSetKamikaze), parade
  (clWrapSetMilitaryParade), ruTransfer (clWrapRUTransfer), alliance
  (clWrapSetAlliance), hyperspace (clWrapMpHyperspace), and the raw
  non-deterministic build/create paths (clWrapBuildShip, clWrapCreateShip)
  mapped where multiplayer-consistent and documented otherwise.
- **FR-002**: The surface MUST NOT invent capabilities the engine lacks
  (engine-faithfulness over convenience): a tool exists only when a
  corresponding engine method/verb exists.
- **FR-003**: Every tool MUST declare a complete JSON Schema for its params,
  matching the engine's parser exactly: types, optionality, defaults
  (e.g. bind playerIndex defaults 0; step ticks defaults 1; build quantity
  defaults 1; snapshot owner filter default all; sections default
  ships+resources).
- **FR-004**: Snapshot mapping MUST include every engine field with exact
  names/typing: tick (int), timeElapsed (float), paused (bool),
  income (float, -1 when unknown/paused), players[] (index, isAI,
  resourceUnits, alive, lost), ships[] (id, owner, type, pos[3], vel[3],
  health, healthMax, dps, guns, range, order|null), resources[] (id, owner,
  type, pos, vel, value), buildQueue[] (type, quantity, ticksRemaining),
  research[] (tech, labs, timeLeft), techTree (labs, canResearch,
  locked[type,disabled,missing[]], researched[]), matchStatus (running,
  boundPlayerIndex, winner|null, perPlayer[index,alive,lost]).
- **FR-005**: Ship order mapping MUST cover every engine order verb string
  emitted by the backend: move, attack, guard, dock, harvest, launch, special,
  halt, parade, passive-attack, moving, and the opaque sentinel `order?`, plus
  `targetId` (attack/harvest) and `target`[3] (move), preserving the engine's
  emit-conditions.
- **FR-006**: Event mapping MUST cover all seven event types with aux-code
  semantics documented (matchOver winner = index+1 or 0; shipDestroyed killer
  = killerOwner+1 or 0; orderRejected error enum value).
- **FR-007**: All nine error codes MUST be handled identically on both
  transports: synchronous submit errors return `error.code =
  aiplayErrorString(...)`; drain-stage rejections surface as orderRejected
  events with `aux` = the enum value. A documented enum→code table MUST
  accompany the schema so aux is decodable.
- **FR-008**: Encoder/decoder: a single bidirectional mapping layer MUST
  convert MCP tool calls ↔ UDS line messages and UDS responses ↔ MCP results,
  centralizing id-echo rules (envelope id vs order id), params passthrough
  (e.g. `sections`, `owner`, `ticks`, `onOff` boolean→"on"/"off" coercion
  already present in the engine parser), and result wrapping. No hand-maintained
  duplicate schema knowledge elsewhere in agent tooling.
- **FR-009**: Submit-time validation parity: the MCP layer MUST reject
  synchronously what it can validate without engine semantics: unknown verb,
  missing required params (ships for non-fleet-wide verbs, target presence,
  name params for build/research/formation/tactics/autolaunch), non-positive
  quantity, oversize id/name/ship-list (64 ships), and known-invalid type or
  tech names for the bound player's race when that name list can be obtained
  from the layer (shiptype_names.inc, tech names). Deep gameplay checks remain
  engine-side.
- **FR-010**: Binding lifecycle MUST be fully exposed: bind (default 0),
  reconnect no-op, single-binding rejection, bindChanged/matchContinued
  events, and status fields state/boundPlayerIndex/sessionId/mapName.
- **FR-011**: Read-only tool classes MUST remain read-only (observe, events,
  status, build/research/techTree queries when exposed standalone) so peer
  agents can call them without racing the commander (constitution VI).
- **FR-012**: The schema (both transports) MUST be versioned with
  AIPLAY_PROTOCOL_VERSION and the spec's schema appendix MUST be regenerated
  from the single source of truth (a changed engine field is a spec update).
- **FR-013**: Known engine-side quirks that produce invalid or misleading
  JSON MUST be fixed at the mapping layer or engine and recorded in the spec
  as deviations: (a) harvest targetId serialization emitting raw pointers
  instead of mapped ids; (b) `research` list vs `techTree` overlap (agents
  saw `researching: null` while `research[]` was populated); (c) "queued:
  true" responses for orders the engine will asynchronously reject.
- **FR-014**: Per-ship under-attack/damage signal: the snapshot schema MUST
  expose enough to detect imminent danger (per-ship health is present; the
  layer MUST additionally expose, per ship, the attacker-facing signal
  available in the engine — the ship's current order target chain — or an
  explicit damage-event stream), so an analyst peer can prioritize threat
  response without diffing snapshots.

### Key Entities *(include if feature involves data)*

All entities below are the authoritative JSON data model (single source of
truth for both transports) derived from `src/AIPlay/{aiplay.h, aiplay.c,
aiplay_snapshot.c, aiplay_orders.c, aiplay_engine.c, aiplay_json.c}`:

- **Envelope**: `{id, method, params?}` → `{id, result}` | `{id,
  error:{code, message}}`; id echo rules (number without dot → integer echo;
  string; verbatim other; missing → null).
- **OrderRequest**: verb (15), shipIds[≤64] uint, targetKind
  (none/coord/ship/resource/name), coord[3] float, targetId uint, name[≤48],
  quantity ≥1. Per-verb parameter tables as in FR-003.
- **OrderResult**: `{queued: true, orderId}` | error response.
- **Snapshot**: full schema per FR-004/FR-005.
- **Event**: `{type (7), tick, id, aux, owner}` ring of 256, newest first.
- **Status**: `{state (disabled/lobby/playing/matchOver), boundPlayerIndex,
  sessionId (=protocol version), mapName, matchStatus{running, paused, tick}}`.
- **SessionControl**: bind/save/pause/resume/step results.
- **Errors**: enum (0=none … 9=internal) ↔ code strings per FR-007.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Coverage = 100%: for every UDS method and order verb defined in
  the AIPlay source, an MCP tool exists and is exercised by at least one test
  (18 methods + 21 clWrap funnels / their mapped verbs per FR-001; select
  included; the 6 currently-unexposed funnels must reach parity).
- **SC-002**: Round-trip fidelity: every engine response sample (recorded
  fixtures over unit-test stub backend and live captures) survives a
  decode→encode cycle without field loss — measured 0 deltas across the
  fixture corpus.
- **SC-003**: Schema match: for every tool, its declared JSON Schema admits
  exactly the param set the engine parser accepts (verified by conformance
  tests crossing valid/invalid samples per verb, ≥2 valid + ≥3 invalid each).
- **SC-004**: Error transparency: ≥95% of engine rejections are visible within
  one poll interval as either a synchronous error or an orderRejected event
  with a decodable aux code (up from the current state where submit-time
  async rejections were invisible to agents).
- **SC-005**: No schema drift: a single change to a snapshot field requires
  editing exactly one schema artifact in the implementation (verified by
  searching the implementation for duplicated literals; duplication = failure).
- **SC-006**: The full snapshot of a mid-battle tick (500+ objects) is served
  in under 1 s per request so per-round agent loops stay responsive.

## Assumptions

- The engine UDS line protocol (`aiSession.*`, `state.*`, `order.*`,
  `game.*`, `time.*`) remains the transport beneath MCP; MCP is a
  localhost HTTP/JSON-RPC facade over it (sidecar), per the existing
  architecture and security constraints.
- "Fully fledged" means complete coverage of the AIPlay layer's existing
  engine I/O — no new engine capabilities (e.g. unit production queues beyond
  today's, chat, campaign scripting) are added in this feature; those get
  their own specs.
- The `order.select` verb is included for completeness (layer-side no-op per
  engine code) but documented as such.
- Client-side compression (caps, summaries) stays in the agent fleet — the
  MCP layer serves faithful, complete data; compression is the consumer's
  choice, never a lossy default.
- The engine's ship-type/tech name registries are obtainable for FR-009
  validation (they are compile-time enumerations today).
- The existing agent fleet (commander + 4 peers) adopts the encoder/decoder;
  its hand-rolled compressors may keep operating on top, but their schemas
  derive from the single source of truth.