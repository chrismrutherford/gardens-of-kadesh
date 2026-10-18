# Feature Specification: MCP Server Layer for AI Play

**Feature**: `001-mcp-server-layer-for-ai-to-play-the-game`

**Created**: 2026-10-18

**Status**: Draft

**Input**: User description: "MCP server layer for AI to play GoK: an MCP interface exposing game state and issuing commands to the Homeworld SDL engine headlessly"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - AI agent connects and observes a running game (Priority: P1)

An AI agent (via any MCP-capable client) connects to the game's MCP interface
and, given a running/loaded game session, queries the current state of the
match: which ships exist, which player controls them, fleet resources
(RUs), and game progress. The agent receives a structured, human-readable
snapshot of that state without any render window being opened.

**Why this priority**: Without reliable observation there is nothing an AI can
act on; observation alone already enables analysis, bot development, and
testing of GoK builds.

**Independent Test**: Start a game session headlessly, call the "observe" tool,
and verify the returned state matches known reference conditions (e.g., the
starting fleet of a known skirmish map).

**Acceptance Scenarios**:

1. **Given** a headless game session loaded on a known map, **When** the agent
   requests the game state, **Then** the response includes the complete list of
   ships with owner, type, position, and health, plus each player's resource
   count.
2. **Given** the same seed and map loaded twice, **When** the agent requests
   state at the same in-game tick, **Then** both responses are identical
   (determinism, per constitution Principle IV).

---

### User Story 2 - AI agent issues gameplay commands (Priority: P2)

The AI agent issues orders using standard Homeworld command vocabulary:
move, attack, guard, dock, build units, and research, addressing ships/
groups by the identifiers observed in Story 1. The engine applies the orders
the same way it would if a human player had issued them, without altering
gameplay semantics (constitution Principle I).

**Why this priority**: Acting on state is what turns observation into play;
it is the core value of "an AI can play it."

**Independent Test**: In a headless two-player match, issue a scripted sequence
of orders (move then attack a known target) and verify fleet positions and
outcomes change as they would under documented gameplay rules.

**Acceptance Scenarios**:

1. **Given** a fighter under the agent's control, **When** the agent orders it
   to move to a coordinate, **Then** the ship's subsequent observed state shows
   it traveling to that coordinate.
2. **Given** a build-capable vessel, **When** the agent orders the
   construction of a unit the player can afford, **Then** the build appears in
   observed state and the player's resources decrease accordingly.
3. **Given** an order targeting a nonexistent or invalid object, **When** the
   agent submits it, **Then** the system returns a clear error describing the
   invalid reference without altering the game state.

---

### User Story 3 - Time control and session lifecycle (Priority: P3)

The agent interacts with (rather than fights) the game clock: it can step or
tick the simulation forward, pause, and resume, so decisions can be made
between ticks rather than under real-time pressure. The agent can also
load/restart a scenario and end a session cleanly.

**Why this priority**: Required for practical agent decision-making and
reproducible tests, but only valuable once observe and act exist.

**Independent Test**: Load a map, advance the simulation N ticks, and verify
state progression is identical to N ticks of free-running play from the same
seed.

**Acceptance Scenarios**:

1. **Given** a paused session, **When** the agent steps forward one tick,
   **Then** the observed state advances by exactly one tick's worth of
   simulation.
2. **Given** seed S and map M, **When** the agent loads M with seed S and
   runs 100 ticks in step mode, **Then** the final state matches 100 ticks of
   non-stepped play from the same seed.

---

### Edge Cases

- What happens when the agent requests state with no session loaded?
  The interface returns a clear "no active session" error, not a crash.
- What happens when the simulation runs faster than the agent reads updates?
  The interface serializes state per request; no state loss or torn reads.
- What happens when two commands arrive while previous ones are in flight?
  Commands are applied in a defined order and each returns a distinct result.
- What happens when an order races with a destroyed ship?
  The engine resolves it with normal gameplay rules; the response reports the
  outcome.
- What happens when the agent disconnects mid-match?
  Game session behavior is unaffected; the interface may be reconnected.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST expose an MCP-compatible tool surface (list
  tools, call tools) that a standard MCP client can use without custom glue.
- **FR-002**: The system MUST run the game engine with no rendering/UI window
  (headless) so an AI can play on machines without displays.
- **FR-003**: The system MUST provide a tool to load a game session
  (skirmish/scenario) with a chosen map and player configuration.
- **FR-004**: The system MUST provide a tool returning a structured snapshot
  of game state: ships (owner, type, position, orientation, health, orders),
  resource units per player, build/research status, and game tick/elapsed time.
- **FR-005**: The system MUST provide tools to issue Homeworld gameplay
  commands: move, attack, guard, dock, undock, build, research — with the same
  effects as those commands issued by a human player.
- **FR-006**: The system MUST provide tools to control simulation time: pause,
  resume, step N ticks, and set simulation speed.
- **FR-007**: The system MUST respond to state queries within a bound that
  lets an agent take at least one decision per simulated second of gameplay
  (target: state snapshot request answered in under 1 second for a full
  2-player skirmish map).
- **FR-008**: The system MUST return structured, agent-parseable results with
  stable identifier references (ship IDs persistable across ticks within a
  session).
- **FR-009**: The system MUST reject and clearly report invalid commands
  (unknown IDs, unaffordable builds, forbidden actions) without mutating
  state.
- **FR-010**: The interface MUST NOT be reachable over a network socket by
  default; it communicates only over a local channel with the game process.
- **FR-011**: The system MUST NOT change visible gameplay semantics: a human
  and an AI playing the same orders on the same seed MUST see identical
  outcomes.
- **FR-012**: Every MCP tool MUST have a description sufficient for an agent
  with no prior knowledge of GoK internals to use it.
- **FR-013**: The system MUST allow ending a session and releasing the game
  cleanly so the process is not left running (no orphan processes or zombie
  sessions).

### Key Entities *(include if feature involves data)*

- **Game Session**: A running headless match; attributes include map, seed,
  player roster, current tick, and paused/running status.
- **Ship**: A controllable unit; attributes include persistent ID, owning
  player, type, position/orientation, health, current orders, docked/building
  status.
- **Player**: A game participant; attributes include player index, resource
  units, faction, researched technology state.
- **Command**: A single agent-issued order; attributes include order type,
  target ship/group, destination/target parameters, and result status.
- **State Snapshot**: The serialized state of a session at a tick, as
  returned by the observe tool.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An AI agent with zero prior knowledge of GoK internals can, from
  only the tool descriptions, connect, load a map, observe state, and issue at
  least a move and an attack order successfully.
- **SC-002**: A full state snapshot of a standard 2-player skirmish at a
  mid-game moment (≈50 ships) is returned in under 1 second.
- **SC-003**: The same seed + map + order sequence produces byte-identical
  state snapshots at matching ticks across at least 3 full replays.
- **SC-004**: An AI agent playing a scripted full game (load, build, move,
  attack) completes a match without any human intervention or rendering
  window.
- **SC-005**: 100% of deliberately invalid commands in a test suite return
  descriptive errors and produce no state mutation.

## Assumptions

- The existing GoK engine can run headless (or can with minimal, justified
  engine-side support); the MCP layer itself is a separate layer, not an
  engine rewrite.
- v1 targets Linux builds and skirmish (non-campaign) matches; campaign play
  and macOS/Windows parity are out of scope for v1.
- Real-time reflexive micro-management is out of scope; time-control tools
  (Story 3) are the intended decision cadence.
- The MCP layer communicates over a local channel only; there are no remote
  participants and hence no authentication in v1.
- Ship IDs and snapshot schema are internal to this layer; human gameplay is
  completely unaffected by its existence.