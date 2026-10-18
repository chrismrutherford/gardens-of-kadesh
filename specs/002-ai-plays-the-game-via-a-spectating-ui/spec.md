# Feature Specification: AI Plays the Game While the User Spectates in the Live UI

**Feature**: `002-ai-plays-the-game-via-a-spectating-ui`

**Created**: 2026-10-18

**Status**: Draft

**Input**: User description: "make a design and capture requirements for this game so an AI can
play it, I would like to see the UI whilst the UI controls it" — i.e. an external AI agent plays
Gardens of Kadesh while the human watches the normal, live game window.

Relationship to prior work: spec `001-mcp-server-layer-for-ai-to-play-the-game` defined the
observe/order interface for a **headless** session. This spec extends the same capability to a
**visible** game session: the rendering window stays open and normal, the human watches, and the
AI is the acting player.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Human watches a live game the AI is playing (Priority: P1)

The user launches the game with the AI-play feature enabled. The normal game window opens —
title flow, skirmish setup, in-game HUD, camera, everything renders exactly as in a normal
human-played session. An external AI agent connects and plays one side of a skirmish match. The
user simply watches: they see the AI's ships fly, fight, harvest and build in real time in the
ordinary UI, with no second window, no overlay replacing the HUD, and no rendering degradation.

**Why this priority**: This is the headline experience ("I would like to see the UI whilst the
AI controls it"). Everything else is refinement of what the spectator sees and what the AI can do
while it is being watched.

**Independent Test**: Start a skirmish with AI-play enabled on a known map, connect an AI agent,
issue one scripted movement order, and verify (a) the game window remains visible and interactive
to the human as a spectator and (b) the ships belonging to the AI move in the on-screen world.

**Acceptance Scenarios**:

1. **Given** the game is running with AI-play enabled, **When** the AI orders a ship to move,
   **Then** the user watching the window sees that ship travel within one second of the order
   being acknowledged, using the normal 3-D view.
2. **Given** a running spectator session, **When** the AI is disconnected, **Then** the game
   continues rendering normally and the session does not crash or freeze.
3. **Given** AI-play is enabled, **When** the user looks at the HUD, **Then** the HUD shows the
   AI player's state (resources, selection, research, build queue) just as it would for a human
   player of that side.

---

### User Story 2 - AI observes the complete live battlefield (Priority: P2)

While the visible session runs, the AI agent can query the current state of the match through its
play interface at any moment: the map, every ship's owner/type/position/health, resource
containers and each player's harvested resources, build queues and research state, and the match
status (running, paused, victory/defeat for each player). The AI sees the full battlefield —
Homeworld (and GoK) skirmish has no fog-of-war, so no visibility filtering is applied — and the
snapshot it receives matches what the game world actually contains at that moment, i.e. what the
spectating user is seeing on screen.

**Why this priority**: An AI cannot make believable moves without ground-truth state; this is the
observation half of "the AI plays it", now required to agree with what the human sees.

**Independent Test**: With the game visible (not headless), pause the match, query state through
the play interface, and verify the response matches a reference fleet table for the map/seed —
the same check as spec 001's observation story, now running against a rendered session.

**Acceptance Scenarios**:

1. **Given** a visible mid-battle session, **When** the AI requests the state snapshot,
   **Then** every player's ships are listed with owner, type, position, health and current
   orders, and resource counts match the HUD display the human sees.
2. **Given** the same visible session paused, **When** the AI requests the snapshot twice with
   no intervening actions, **Then** both responses are identical (stability of paused state).
3. **Given** a match concludes, **When** the AI requests the match status, **Then** the response
   names the winner and loses flags for each player.

---

### User Story 3 - AI issues the full command vocabulary while the human watches (Priority: P2)

The AI issues orders in the normal Homeworld command vocabulary — select ships or formational
groups, move, attack, guard, dock (including harvesting), build ships, and research — addressing
ships by the identifiers it observed. Engine behavior is identical to a human issuing the same
orders through the UI: the same pathfinding, the same attack logic, the same build times. Invalid
orders reference-free without disturbing the state. Every order the AI issues is visible to the
spectator as ordinary in-game behavior (ships move, builds appear, research completes).

**Why this priority**: Action is what makes it "the AI plays" rather than "the AI watches";
equal priority with Story 2 because observation and action together form the minimal playable
pair.

**Independent Test**: In a visible skirmish, have the AI perform a scripted but representative
turn-based-on-observation sequence (move a fighter to a coordinate, harvest from an asteroid,
build a ship), and verify on-screen and state-query outcomes match normal gameplay rules.

**Acceptance Scenarios**:

1. **Given** a fighter under the AI's control, **When** the AI orders it to move,
   **Then** the observed (and on-screen) position converges on the ordered coordinate.
2. **Given** a resource collector, **When** the AI orders harvesting from an asteroid,
   **Then** the player's resource count rises in subsequent snapshots and the on-screen collector docks.
3. **Given** a build-capable vessel, **When** the AI orders a build it can afford,
   **Then** the build appears in the queue and snapshot, resources decrease, and the ship
   eventually materializes on screen.
4. **Given** an order naming a nonexistent ship id, **When** submitted, **Then** an error
   describing the invalid reference is returned and the game state is unchanged.

---

### User Story 4 - Human-friendly pacing: the AI thinks, the human still sees a coherent game (Priority: P3)

Turn-free real-time strategy is hostile to an AI that needs thinking time. During a spectated
session, time control is available: the AI can pause the simulation, examine state, issue orders,
and resume; the spectator experiences this as a coherent, non-flickering view (a paused world is
simply a paused world). The default operating mode is: run unpaused in real time, with the AI
acting on a periodic cadence; pause/step is an AI-chosen helper, not a required choreography.
The spectator can always see from the HUD whether the game is paused.

**Why this priority**: Improves AI competence and spectating comfort, but the feature is
demonstrable end-to-end in real time without it.

**Independent Test**: Run a visible session where the AI pauses, queries state, orders, resumes;
verify the pause boundary is visible in both the snapshot data (tick does not advance while
paused) and the on-screen clock/HUD, and that no inputs by the AI are lost across the boundary.

**Acceptance Scenarios**:

1. **Given** a running session, **When** the AI pauses and then queries state twice,
   **Then** no in-game time passes between the two snapshots.
2. **Given** a paused session, **When** the AI resumes,
   **Then** the simulation continues from the paused tick with prior orders intact.

---

### Edge Cases

- What happens when the AI is mid-order-stream when the match ends (victory/defeat)? The system
  rejects further gameplay orders with a match-over indication, observation continues (final
  state is queryable), and the visible session stays open until the human closes it.
- What happens when the AI submits multiple orders faster than the engine can process them? The
  system accepts them into the same queue the UI uses and applies them in submission order; no
  order is silently dropped.
- What happens when the spectating human interacts with the window (moving the camera, changing
  view)? Camera and UI navigation remain the human's; they MUST NOT affect simulation state, so
  AI-observed coordinates are unaffected by spectator camera movement.
- What happens when the AI connection drops mid-match? The game continues running unattended;
  on reconnect the AI re-observes state and continues playing; nothing is rolled back.

## Requirements *(mandatory)*

### Functional Requirements

**Observation (AI side)**

- **FR-001**: The system MUST provide the AI a state snapshot of the live visible session
  containing: map identity, every ship (owner, type, position, health, current order), resource
  containers, per-player harvested resources, build and research queues, and match status
  (running/paused, per-player victory/defeat), consistent with what the spectator sees on screen.
- **FR-002**: The system MUST make paused-state snapshots deterministic and stable: identical
  queries with no intervening orders return identical data.
- **FR-003**: The system MUST expose match lifecycle events (match started, won, lost) to the AI.

**Action (AI side)**

- **FR-004**: The AI MUST be able to command exactly one side of the match per session; the
  opposing side behaves under the engine's normal computer-opponent or human control.
- **FR-005**: The AI MUST issue orders from the standard Homeworld vocabulary: select/move,
  attack, guard, dock (incl. harvesting), build, research. Orders apply exactly as the same
  orders issued by a human through the UI.
- **FR-006**: The system MUST reject invalid orders (bad ids, unaffordable builds, illegal
  targets) with a descriptive error and no state change.
- **FR-007**: The system MUST apply AI orders in submission order without dropping any that the
  engine itself would have accepted.

**Spectating (human side)**

- **FR-008**: With AI-play enabled, the game MUST run as a normal visible session: full
  rendering, HUD, camera control for the human, and normal performance.
- **FR-009**: The human's spectator interactions (camera, UI navigation) MUST have no effect on
  simulation state or on the data the AI observes.
- **FR-010**: The session MUST survive AI disconnection and continue running unattended until
  the human ends it; reconnect must be possible without rollback.

**Time control**

- **FR-011**: The AI MUST be able to pause, resume, and advance the simulation, with the paused
  state evident to the spectator through the normal HUD.
- **FR-012**: The default mode MUST be real-time play at normal game speed; pause/step MUST be
  optional aids chosen by the AI, never imposed on the spectator as a required interaction model.

**Boundary conditions**

- **FR-013**: After match end, gameplay orders MUST be rejected with a match-over indication;
  observation MUST remain available.
- **FR-014**: The AI play interface MUST be local-only: no network exposure by default.
- **FR-015**: The feature MUST NOT alter gameplay semantics, unit stats, or campaign content
  relative to the normal build (i.e. it reuses the engine's own command pipeline).

### Key Entities

- **Game Session**: one running visible match; owner of the tick clock, the players, and the
  world; carries match status and lifecycle events.
- **Ship**: an in-world unit with an owner, type, position, health and current order set;
  addressable by a stable identifier that the AI receives from observation.
- **Player**: one side of the match; has harvested resources, a build queue, a research tree
  state, and win/lose status; exactly one player may be bound to the AI.
- **Order**: an AI-issued command referencing ships/players/targets by observed identifiers;
  accepted, rejected (with reason), or queued by the engine.
- **Time Control State**: paused/running/stepping flag of the session, visible to spectator and AI.
- **Spectator**: the human watching; holds camera/UI authority, no gameplay authority over the
  AI-bound side.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A spectator can watch a complete skirmish (start to victory) played by the AI in
  the normal game window with no crash, no second window and no HUD replacement.
- **SC-002**: State snapshots agree with on-screen reality: for any queried snapshot, every ship
  position shown matches the world at that tick, and resource counts match the HUD.
- **SC-003**: The AI can execute a representative gameplay loop — observe, harvest, build, move,
  attack — without any order being silently dropped and with ≤1s perceived latency between an
  order's acknowledgment and its on-screen result for the spectator.
- **SC-004**: Spectator interactions (camera moves) produce zero changes in AI-observed state
  other than world simulation itself.
- **SC-005**: Pausing is coherent: while paused, repeated snapshots are byte-identical and the
  spectator's HUD clearly shows the paused state.

## Assumptions

- Skirmish (vs. computer opponent) is the target match type; single-player campaign play by an
  AI is out of scope for this spec.
- The AI plays as a regular player side using the engine's existing command processing; there is
  no new "AI-only" game mechanic.
- Homeworld/GoK skirmish exposes full battlefield information (no fog-of-war), so the AI's
  observation equals the ground truth; this is treated as a game property, not a cheat setting.
- The spectating human does not issue gameplay orders to either side during the match; if they
  want to take over, the AI session ends (out of scope for this spec's v1).
- Machine-assisted pacing (pause/step) is acceptable to the spectator; the default remains
  real-time.
- The AI-facing interface rides on the layer specified in
  `001-mcp-server-layer-for-ai-to-play-the-game`; it must work identically whether or not a
  window is open (headless mode remains supported for testing).