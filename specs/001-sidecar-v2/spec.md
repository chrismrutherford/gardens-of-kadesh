# Feature Specification: sidecar v2 — full engine command map + typed codecs

**Feature**: `001-sidecar-v2` | **Created**: 2026-10-20 | **Status**: DRAFT — review before build

**Input**: rebuild the sidecar interface: fully map the game engine's
command interface and give every command/tool nice JSON encoders and
decoders (typed, validated, structured results instead of text blobs).

## The idea in one paragraph

The current sidecar (tools/aiplay_mcp.py) is a thin relay: ~17 tools mapped
by hand, arguments passed through raw, results returned as TEXT the LLM has
to re-parse. Meanwhile the engine's command funnel (`src/Game/CommandWrap.h`)
has **21 commands** — the sidecar doesn't expose `SetKamikaze` (true
kamikaze orders), `RUTransfer`, `SetAlliance`, `MpHyperspace`, the
**deterministic-build PAUSE/UNPAUSE/CANCEL** operations (the human Build
Manager's PAUSE/CANCEL BATCH buttons), or **research STOP (clear lab)**.
Sidecar v2 replaces the hand-rolled relay with a **single declarative
command map**: every engine funnel documented once with a typed JSON schema;
requests encoded/validated, engine replies DECODED into structured objects
(dicts), and MCP `structuredContent` results so agents get clean JSON
instead of string blobs.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Complete command surface (Priority: P1)

The sidecar exposes EVERY engine command the human UI can issue
(kamikaze, RU transfer, build pause/unpause/cancel, research clear-lab,
hyperspace, alliance) as a tool with a documented JSON schema. The agents
gain exactly the same action vocabulary as a human player.

**Why this priority**: the missing commands (kamikaze, build-queue
management) are exactly what the doctrine/research identified as gameplay
winners; the executor literally cannot follow those directives today.

**Independent Test**: enumerate engine funnels (`clWrap*`) sidecar-side;
every one maps to a tool (except human-only ones documented as excluded);
`tools/list` shows all with full schemas.

**Acceptance Scenarios**:

1. **Given** the v2 sidecar, **When** tools/list is called, **Then** all
   mapped commands appear with typed properties and descriptions
   (≥1 when/constraint clause each).
2. **Given** a fleet with a probe, **When** the commander calls
   `order_kamikaze {ships:[probe id]}`, **Then** the engine applies the
   kamikaze order (event orderApplied, aux kamikaze flag).
3. **Given** a queued batch of ships, **When** the commander calls
   `build_pause {factory}` then `build_resume`, **Then** the queue
   pauses/resumes (buildQueue JSON reflects paused state).
4. **Given** two agents with RU balances, **When** `ru_transfer` is called,
   **Then** RU moves between players (snapshot players' RU reflects it).

---

### User Story 2 - Typed codecs + structured results (Priority: P1)

Every request argument is ENCODED through the schema (validated, clamped,
enums checked) before hitting the engine; every engine response is DECODED
into a normalized Python object (dicts/lists, correct types, error codes as
data) delivered as MCP `structuredContent` — the LLM sees clean JSON, error
handling stops guessing from strings.

**Why this priority**: kills the entire class of malformed-JSON / string-
parsing failures (the researched/trailing-comma saga) at the interface
layer.

**Independent Test**: a schema round-trip test suite with RECORDED engine
responses: decode(normalize) → valid typed dict for every recorded wire
format, including the historical malformed cases (tolerant decoder).

**Acceptance Scenarios**:

1. **Given** a tool call with a bad enum value (tactics:"rage"),
   **When** encoded, **Then** it is rejected schema-side (error before
   the engine, with a reason the LLM can read and correct).
2. **Given** the engine's snapshot JSON, **When** decoded, **Then** ships/
   resources/queues/techTree are objects with parsed fields and the
   malformed-history cases decode correctly (tolerant mode).
3. **Given** any tool response, **When** delivered, **Then**
   structuredContent carries the object AND text fallback (older MCP
   clients still work).

---

### User Story 3 - One source of truth for the interface (Priority: P2)

The command map is DATA (a table in one module): tool name, engine method,
engine verb, argument schema, description, result shape. New commands =
one table row + a test; agents' docs (data dictionary) generate from it.

**Why this priority**: kills drift: the map, the tools/list, the playbook
docs and tests all derive from one definition.

**Independent Test**: regenerate the tool list from the map in a test and
compare against tools/list output (equal); regenerate the data dictionary
section and diff-check.

**Acceptance Scenarios**:

1. **Given** the command map, **When** tools/list is generated, **Then**
   it matches exactly (test-enforced).
2. **Given** a hypothetical new command row, **When** the map is reloaded
   in a test, **Then** tools/list gains it without any other code change
   (data-driven proof).

### Edge Cases

- Engine unavailable: tools return E_NO_ENGINE (typed), not HTTP 500s.
- Malformed engine JSON: tolerant decoder logs + salvages the prefix...
  (v2 decision: fail the call with E_PARSE + raw excerpt; agents retry —
  NO silent corruption).
- Unknown tool names: E_UNKNOWN_TOOL with the valid list.
- Oversized results: truncation marker in the structured result (explicit,
  not silent).

## Requirements *(mandatory)*

- **FR-001**: The sidecar MUST expose every mapped engine command
  (`clWrap*` funnels) as a tool with a typed JSON schema; the map lives in
  one declarative table.
- **FR-002**: Added commands MUST include: `order_kamikaze` (SetKamikaze),
  `ru_transfer` (RUTransfer), `build_pause`/`build_resume`/`build_cancel`
  (DeterministicBuild CMD_PAUSE/UNPAUSE/CANCEL), `research_clear_lab`
  (Research STOP), `order_hyperspace` (MpHyperspace; MP matches),
  `set_alliance` (SetAlliance; observe/MP policy gated).
- **FR-003**: Request encoding: schema validation + enum clamping + integer
  ranges BEFORE dispatch; failures return typed errors with reasons.
- **FR-004**: Response decoding: engine JSON parsed + normalized; results
  delivered as MCP structuredContent (+ text fallback); errors as typed
  {code, message, retryable, excerpt}.
- **FR-005**: Malformed engine JSON MUST fail the call (E_PARSE + excerpt
  ≤512 chars); the engine-side invalid-JSON bugs (e.g. the trailing-comma
  class) MUST be caught at the sidecar in tests against recorded responses.
- **FR-006**: Single-agent mode (tools/agent.py) MUST consume the same
  typed interface (client library shared).
- **FR-007**: The tool list + schemas MUST be test-derivable from the map
  (one source of truth).
- **FR-008**: Local-only security + single-writer rules unchanged
  (constitution VI).

## Success Criteria

- **SC-001**: tools/list shows ≥24 typed tools, all map-derived (test).
- **SC-002**: 100% of recorded engine wire responses decode (tolerant
  decoder test corpus).
- **SC-003**: Argument-type order rejections (client-side) return readable
  typed errors (test + live).
- **SC-004**: The executor kamikazes/kills an unguarded collector in the
  live doctrine scenario (order_kamikaze applied).
- **SC-005**: Round-trip: a build paused via build_pause shows in the
  buildQueue JSON and resumes on build_resume.

## Assumptions

- Engine changes: NONE — all new commands route through existing funnels
  (SetKamikaze, DeterministicBuild ops, Research STOP exist; only the
  backend dispatch (aiplay_engine.c) needs small new verb cases — engine-
  layer glue only, no gameplay semantics).
- MCP protocol stays 2025-06-18 (structuredContent supported).
- The fleet refactor (001-fleet-refactor, parked) consumes this afterwards;
  this feature is standalone-deployable.