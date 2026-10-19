# Feature Specification: multi-agent fleet AI (agent-multi)

**Feature**: `001-agent-multi-llm-fleet`

**Created**: 2026-10-19

**Status**: Draft

**Input**: 4 LLM servers (192.168.2.72:9931-9934) run as peer agents, each
working flat out in its own continuous loop; the commander keeps the sole
session binding and order queue; peers publish advice via shared state;
launched via `tools/agent-multi.py`.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Commander commands continuously (Priority: P1)

A spectator launches `tools/agent-multi.py` against a running match. The
commander runs the same agentic loop as today (observe → decide → order →
feedback) at full cadence, but never waits on any other model: each round it
reads the freshest published peer artifacts instantly (local file reads). A
stale or dead peer never slows or blocks ordering.

**Why this priority**: without an unblocked commander the match is lost; this
is the MVP on its own.

**Independent Test**: run agent-multi with zero peer servers reachable — the
commander still commands at the same cadence as the existing single-node
agent and issues orders every round.

**Acceptance Scenarios**:

1. **Given** a live match, **When** all 4 peers are unreachable, **Then** the
   commander's per-round decision latency is the same as the single-agent
   baseline (±20%) and orders still flow every round.
2. **Given** the strategist loop publishes a plan, **When** the commander
   takes its next snapshot, **Then** the plan's directives appear in the
   commander's round prompt with no extra LLM call.
3. **Given** a peer artifact is older than 60 s, **When** the commander
   assembles its prompt, **Then** that artifact is marked stale and ignored.

---

### User Story 2 - Peer agents work flat out (Priority: P1)

Three peer loops run continuously, each against its own LLM server: the
strategist recomputes long-horizon plans, the economist refines the build /
research schedule, the analyst reacts to the battle-event stream. Each peer
loop is an in-process thread with its own LLM client; the four servers are
hammered simultaneously and none of them idles while the match runs.

**Why this priority**: this is the core value — continuous parallel
reasoning instead of occasional advisories.

**Independent Test**: with the commander idle between rounds, each peer's
server log shows uninterrupted request→response completion; four servers
process requests concurrently without any peer blocking another.

**Acceptance Scenarios**:

1. **Given** all 4 servers healthy, **When** the match runs for 10 minutes,
   **Then** each peer log shows ≥ 10 completed reasoning rounds (i.e., each
   server served continuously, not only on commander ticks).
2. **Given** one peer server dies mid-match, **When** it recovers or fails
   over, **Then** the other peers never stall and the commander is
   unaffected.
3. **Given** a match reset, **When** peers detect it (tick decrease / new
   ids), **Then** they rebase without holding orders or stale claims.

---

### User Story 3 - Peers never mutate the match (Priority: P1)

Peers are read-only observers. Only the commander holds the session binding
and issues orders. A peer crash, restart, or duplicate cannot steal the
binding, race order ids, or double-build.

**Why this priority**: a mutating peer corrupts determinism
(constitution IV) and binding exclusivity (constitution VI).

**Independent Test**: run two simultaneous peer instances — no
`bind_session` attempt appears in either peer's request log; total `order.*`
calls during a match equal the commander's issued orders only.

**Acceptance Scenarios**:

1. **Given** two peer processes running concurrently, **When** any snapshot
   or order tool is called by a peer, **Then** the engine log shows zero
   `bind_session` and zero `order.*` tool invocations from peer connections.
2. **Given** a peer dies and restarts, **When** it re-observes, **Then** it
   neither re-binds nor re-issues any previously published order.

---

### User Story 4 - Critic gates the commander (Priority: P2)

The critic peer continuously reviews the commander's recent orders and fleet
state against published plans; when it objects (e.g., a doomed attack, a
rejected-order pattern, an obvious economy hole), it publishes an objection
that the commander's next prompt surfaces. The commander MAY incorporate or
ignore objections — the critic cannot veto mid-flight; its value is a second
opinion at full speed.

**Why this priority**: high value for match quality, but the match is
playable without it.

**Independent Test**: force the commander to issue an order violating the
strategist plan (e.g., attack while outnumbered >3:1 with no defense) — the
critic's next artifact flags it within one cycle and the objection appears
in the commander's prompt.

**Acceptance Scenarios**:

1. **Given** an order marked `orderRejected` twice for the same reason,
   **When** the critic cycle runs, **Then** the published criticism names
   the root cause (e.g., stale ids, dead target) and suggests a correction.
2. **Given** orders consistent with the live plan, **When** the critic
   reviews them, **Then** it publishes no objection (non-noisy).

---

### User Story 5 - Observable and launch-safe (Priority: P2)

One command starts the whole system (single script, sensible defaults,
explicit role ports as CLI flags). All five loops log to separate files with
a shared session id, so a human watching sees each agent's reasoning, its
published artifact, and when an artifact was consumed. Autosave/restart
semantics inherit from the existing agent (epoch-named saves, self-restart
after outages).

**Why this priority**: usability around the core loop.

**Independent Test**: `python3 tools/agent-multi.py --help` lists the
role/endpoint flags; after launch, per-role log files appear under
/tmp with the session id and each peer's artifact file is timestamped.

**Acceptance Scenarios**:

1. **Given** default invocation during a live match, **When** the operator
   launches agent-multi, **Then** four roles attach, publish their first
   artifacts within 60 s, and all logs share one session id.
2. **Given** an abrupt match outage, **When** the game returns and the
   commander rebinding succeeds, **Then** peers resume from fresh state
   within one cycle.

---

### User Story 6 - 4B executor drives the command loop (Priority: P1)

The commander is a small, fast LLM (default a 4B endpoint) running an
executor prompt that mechanically enforces three invariants every round:
(1) any build the plan needs IS in progress, (2) every ship is tasked —
no idle hulls, (3) any research the plan needs IS in progress. It executes
the directives the big-model peers publish, at 1-2 s cadence.

**Why this priority**: turns big-model advice into fast, always-on command
throughput; big-model latency no longer gates the match.

**Independent Test**: point the commander at a fast small model with the
peers offline — the three-invariant audit still runs every round
(snapshot + buildQueue + techTree are self-sufficient).

**Acceptance Scenarios**:

1. **Given** an empty build queue while the production plan requests
   units, **When** the executor's next round runs, **Then** an
   `order_build` is issued within one round.
2. **Given** a ship with `"order": null` in the snapshot, **When** the
   executor's next round runs, **Then** the idle ship receives move/
   attack/harvest within one round.
3. **Given** free research slots and a research path directive, **When**
   the executor audits, **Then** missing research is started within one
   round (or E_NO_LABS handled by building a ResearchShip).

---

---

### Edge Cases

- What happens when two peers publish contradictory advice? The commander's
  prompt shows both with timestamps; newer wins in the merge, both visible
  for the next reasoning pass.
- What happens when the match ends (matchOver)? All loops publish a final
  summary and go idle (no more requests) instead of spinning on errors.
- What happens when a peer's artifact is malformed JSON? It is ignored, the
  previous artifact remains current, and the peer's loop logs the failure
  and retries.
- What happens with very large snapshots for four concurrent consumers?
  The game engine serves multiple read-only clients; peers use the same
  socket/sidecar as the commander (read-only methods are concurrency-safe;
  order methods are simply never called by peers).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: A single launch script MUST start five loops (commander +
  four peers), each pinned to its own role endpoint (strategist 9931,
  analyst 9932, economist 9933, critic 9934 by default), and each
  overridable by CLI flag.
- **FR-002**: Peers MUST publish their latest artifact to shared state in
  JSON files (one per role) containing: role, generated-at timestamp,
  tick observed, and role-specific payload (plan / reaction / production
  plan / objection).
- **FR-003**: The commander MUST read peer artifacts every round and inject
  them into its round prompt with timestamps, without any additional
  LLM round-trip.
- **FR-004**: Peers MUST be strictly read-only: no session binding, no
  order-type tool calls; violations fail the loop with an error log.
- **FR-005**: Each peer MUST run its reasoning loop continuously and as
  fast as its server allows (no sleeps beyond retry/backoff), back-to-back
  rounds.
- **FR-006**: Peer artifacts older than 60 s MUST be marked stale and
  excluded from the commander prompt.
- **FR-007**: On match reset/end, all loops MUST rebase: peers observe fresh
  state, clear event indexes, publish fresh artifacts; commander keeps
  binding semantics of the existing agent.
- **FR-008**: Every loop MUST log to its own file with round/hits/errors
  including its server endpoint; a shared session id ties the logs together.
- **FR-009**: Autosave cadence and self-restart from saves MUST be inherited
  from the existing agent loop (epoch-named saves, ~10 min cadence).
- **FR-010**: When a peer LLM returns a tool call despite being read-only,
  the peer MUST discard it and log the violation (defense in depth).
- **FR-011**: The commander endpoint MUST be overridable (`--llm-commander`)
  and default to the user's 4B endpoint; it keeps sole binding + orders.
- **FR-012**: The commander's executor prompt MUST instruct a mechanical
  audit each round: builds in the production plan must be `building` in
  the snapshot; every ship must have a non-`null` order; research on the
  plan path must be `researching` — gaps become tool calls this round.
- **FR-013**: Peer directives MUST be advisory to the executor (it fills
  audit gaps mechanically; conflicts resolve in favor of invariant safety:
  economy + defense before offense).

### Key Entities *(include if feature involves data)*

- **Commander**: sole bound agent; consumes peer artifacts; issues all orders.
- **Strategist**: owns long-horizon plan (research path, fleet composition
  targets, attack timing).
- **Economist**: owns production plan (build queue recommendations from
  tech tree + RU income).
- **Analyst**: owns battle reactions (event-stream interpretation,
  immediate-response suggestions).
- **Critic**: owns objections/corrections on recent orders vs plans.
- **Shared state (artifact files)**: per-role JSON documents with
  generated-at, tick, payload; atomic overwrite; consumed by commander.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All four LLM servers process a peer round at least every 30 s
  during active play (measured per-role log request rate) — the "flat out"
  requirement.
- **SC-002**: Commander round cadence matches the single-agent baseline
  within ±20% when all peers are healthy.
- **SC-003**: Zero peer-initiated binding attempts or order calls in engine
  logs over a full match (measured via engine trace/sidecar logs).
- **SC-004**: A published strategist artifact influences the commander's
  prompt in the following round 100% of the time (artifact freshness ≤ 60 s).
- **SC-005**: Survivability: kill any one peer server for 2 min — the
  commander issues orders continuously throughout; the peer catches up on
  restore without manual intervention.
- **SC-006**: The executor (4B commander) sustains ≥ 1 decision round per
  2 s while peers run flat out; zero idle ships by audit in ≥ 90% of
  rounds on an active match.

## Assumptions

- The four LLM servers are identical llama.cpp (OpenAI-compatible) endpoints
  with KV-cache reuse already enabled; URLs are configuration only.
- The engine sidecar's read-only methods (observe, get_events, game_status,
  tech tree) are safe for concurrent read clients; only binding/order calls
  are exclusive — peers are structured so they never call those.
- The existing single-agent loop (tools/agent.py) remains untouched and
  operational; agent-multi is a new tool, not its replacement.
- Staleness threshold 60 s is a pragmatic default; pacing remains
  configurable via flags.