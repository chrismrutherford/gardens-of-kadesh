# Feature Specification: fleet refactor — observability, maintainability, doctrine

**Feature**: `001-fleet-refactor`

**Created**: 2026-10-20 | **Status**: Draft — awaiting user review

**Input**: refactor the multi-agent system (commander + 4 peers) to improve
observability and maintainability; improve prompts and tool descriptions,
mining `gameplay.txt` (competitive Homeworld strategy thread) for doctrine.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - One-glance fleet health (Priority: P1)

An operator can ask "how is the fleet doing?" and get a single command
answer: per-role freshness (last round, age), the instruction queue, recent
errors, per-server round rates, and the commander's last actions — without
reading five different logs.

**Why this priority**: today debugging means grepping ad-hoc logs; without
this, every other improvement is unverifiable.

**Independent Test**: `tools/fleet-status.py` (new) run against a live
session prints a compact table; every field is derived from machine-readable
round events (US2), not log scraping.

**Acceptance Scenarios**:

1. **Given** a running fleet, **When** the operator runs
   `tools/fleet-status.py`, **Then** it shows per role: round #, age (s),
   rounds/min, last error, artifact freshness, queue state, and exits 0.
2. **Given** a dead peer (server down 2 min), **When** status runs,
   **Then** that role is flagged STALE/ERROR and the others unaffected.

---

### User Story 2 - Structured round events (Priority: P1)

Every loop writes machine-readable JSONL round events (commander and peers):
round #, tick, actions taken, results, latency, tokens context age. Human
logs stay as-is; JSONL is the audit/telemetry channel.

**Why this priority**: observability foundation (US1 depends on it) and the
only reliable way to measure SC-criteria of prior specs.

**Independent Test**: after N rounds, `rounds-<role>.jsonl` has N lines;
each parses; `fleet-status.py` reads them.

**Acceptance Scenarios**:

1. **Given** any commander round, **When** it completes, **Then** one JSONL
   line exists with the round's tool calls + results + round latency.
2. **Given** any peer round, **When** it completes, **Then** one JSONL line
   exists with publish latency + queue edits accepted/rejected.

---

### User Story 3 - Maintainable module structure (Priority: P1)

The fleet is split into focused modules with no env-var globals: transport
clients, snapshot compression, prompts, commander pipeline, peer loops,
queue, launcher. No behavior change, all existing tests still pass (updated
for imports only), and new code has obvious home.

**Why this priority**: agent.py is a 716-line monolith with env-gated hooks
(`merge_peer_artifacts`, `merge_orders_ring`, `commander_queue*`) — every
new feature wedges into main().

**Independent Test**: module map below (plan.md); `python3 -c "import ..."`
for each; tests green without env-var setup.

**Acceptance Scenarios**:

1. **Given** the refactor, **When** a reviewer reads
   `tools/agent_prompts.py`, **Then** every prompt/string the models see is
   there and nowhere else.
2. **Given** the fleet runs, **When** no AIPLAY_MULTI_* env var is set,
   **Then** it still works (explicit context objects, no hidden globals).

---

### User Story 4 - Doctrine-driven prompts (Priority: P1)

All agent prompts (executor, system, four peers) are rewritten from a
shared doctrine distilled from gameplay.txt (classic-HW applicable items),
with the data dictionary referenced — each role gets a short, focused
doctrine card instead of duplicated generic text.

**Why this priority**: prompt quality is the biggest gameplay lever.

**Independent Test**: unit tests assert each role prompt contains its
doctrine markers (e.g. economist: collector target + controller dropoff;
analyst: kill-enemy-scouts; executor: attack priority list).

**Acceptance Scenarios**:

1. **Given** the new prompts, **When** a reviewer reads them, **Then** each
   role's doctrine card is ≤ ~25 lines, mentions its specific gameplay.txt
   -derived rules, and no role duplicates another's text.
2. **Given** a live match, **When** the strategist/economist publish, **Then**
   their artifacts reference doctrine concepts (collector counts, counter
   relationships, attack priorities) in at least half of publications.

---

### User Story 5 - Tool descriptions that teach (Priority: P2)

Every tool description in agent_tools.py states WHAT it does, WHEN to use
it (and when not), and parameter constraints (from the snapshot data
dictionary), e.g. harvest target must be an asteroid id; guard protects one
ship; scuttle = kamikaze (cheap scouts vs enemy collectors).

**Why this priority**: small executors follow tool descriptions literally;
the current ones are one-liners.

**Independent Test**: unit test asserts every tool has description ≥ 80
chars with a "when"/"use" clause; manual: model misuse rate drops.

**Acceptance Scenarios**:

1. **Given** the new descriptions, **When** `tools/list` is inspected,
   **Then** each tool's description explains use-cases and constraints.
2. **Given** a fresh executor session, **When** it issues orders,
   **Then** argument-type errors (harvest→ship id, guard→coordinate) drop
   vs the current baseline.

---

### Edge Cases

- fleet-status with a missing/stale session dir → friendly "no live fleet"
  message, exit 1
- JSONL write failure (disk full) → loop continues, logs warning, never
  crashes the fleet
- Old sessions without JSONL → fleet-status shows "legacy" and falls back
  to log file mtimes

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Every loop MUST emit one JSONL round event per round
  (tools/rounds-<role>.jsonl): role, round, tick, actions [{tool, args,
  result}], latency_s, error, queue_edits {accepted, rejected}.
- **FR-002**: `tools/fleet-status.py` MUST render one-glance fleet health
  from JSONL + artifacts + queue + history; zero log parsing.
- **FR-003**: The fleet MUST be reorganized into: agent_core.py (LLM/MCP/
  logging/JSONL), agent_snapshot.py (compress + order_detail +
  ships_already_executing), agent_prompts.py (ALL model-facing text: system,
  executor, peers, doctrine cards, playbook loader), agent_rounds.py
  (commander pipeline: observe→merge→dispatch→report), agent_multi.py
  (peers, artifacts, instruction queue), agent-multi.py (launcher+config).
- **FR-004**: Commander/peer wiring MUST use an explicit FleetContext (no
  AIPLAY_MULTI_* env vars, no argv injection).
- **FR-005**: Prompts MUST be doctrine-driven per role, sourced from a
  DOCTRINE constant in agent_prompts.py distilled from gameplay.txt.
- **FR-006**: Tool descriptions MUST document purpose, when-to-use and
  parameter constraints; pseudo-tools (queue_done/queue_fail) included.
- **FR-007**: All existing behavior (single-agent mode, nags, autosaves,
  dedupe, read-only peers, instruction queue) MUST be preserved; tests
  adapted for the new module map, still green.
- **FR-008**: Single-agent mode (`tools/agent.py` CLI) MUST keep working as
  a thin wrapper over the refactored pipeline.

### Key Entities

- **RoundEvent** (JSONL): per-loop telemetry record
- **FleetStatus**: aggregated view rendered by fleet-status.py
- **Doctrine**: shared strategy constants (from gameplay.txt) referenced by
  role prompt cards
- **FleetContext**: explicit wiring object (store paths, queue, roles, opts)

## Success Criteria *(mandatory)*

- **SC-001**: `fleet-status.py` answers fleet health in < 1 s from a live
  session, correctly flagging any role idle > 60 s.
- **SC-002**: 100% of rounds present in JSONL for a 10-minute live run
  (commander + each peer).
- **SC-003**: No prompt strings outside agent_prompts.py (grep-verifiable).
- **SC-004**: All 32+ existing tests green after refactor (imports updated),
  plus new tests for JSONL, fleet-status, doctrine markers, tool
  descriptions.
- **SC-005**: In a live match, executor argument-error orders (E_BAD_ID on
  wrong-type targets) drop ≥ 50% vs a 30-minute baseline run.

## Assumptions

- gameplay.txt is HW:R MP advice; only classic-HW1-applicable doctrine is
  kept (no HW2 races, no modules/platforms, no shipyards).
- Engine surface (sidecar tools) is unchanged; this refactor is tools/ only.
- The user reviews this design before implementation starts (this draft).