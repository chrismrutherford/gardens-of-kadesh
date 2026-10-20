# Feature Specification: peer-authored priority instruction queue

**Feature**: `001-peer-instruction-queue`

**Created**: 2026-10-20

**Status**: Draft

**Input**: the big-model peers may edit the commander's instruction list —
ADD new instructions, REMOVE ones, and RE-ORDER them — via a shared
priority instruction queue; the commander (small executor model) executes
the queue's instructions in priority order. Only the big models edit
instructions; only the commander executes them.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Peers add instructions (Priority: P1)

A strategist/economist/critic/analyst (big model) can add a new instruction
to the commander's queue, with a priority. The instruction appears in the
commander's next round prompt at its priority position, without any
developer intervention.

**Why this priority**: this is the core delegation — big-model planning
becomes direct control of the small executor's work list.

**Independent Test**: a peer publishes a queue edit adding "build 2 Ion
Cannon Frigates" at priority 1 → the commander's following round prompt
shows it at the top; the commander issues an order_build for them.

**Acceptance Scenarios**:

1. **Given** an empty queue, **When** a peer adds an instruction at
   priority 1, **Then** the commander's next round prompt shows it first
   and unmarked by any other instruction.
2. **Given** a queue with 3 instructions, **When** a peer adds at priority
   1, **Then** the previous priority-1 instruction moves down a slot.

---

### User Story 2 - Peers remove instructions (Priority: P1)

Peers can remove any instruction from the queue (by id), e.g. an obsolete
"retreat" once the danger passes, or its own completed plan step.

**Why this priority**: a queue that only grows would poison the executor.

**Independent Test**: with two instructions queued, an editor removes one
by id → the commander's next prompt no longer contains it.

**Acceptance Scenarios**:

1. **Given** instruction `#7` queued, **When** a peer publishes a removal
   for `#7`, **Then** the next commander round prompt omits it and the
   queue file shows it gone.
2. **Given** a removal of a non-existent id, **When** processed, **Then**
   the queue is unchanged and the editor is told (no error loop).

---

### User Story 3 - Peers reorder instructions (Priority: P1)

Peers can re-order the queue — promote/demote/move instructions — so the
commander always works the most important thing first.

**Why this priority**: priority order is the mechanism by which peers drive
the executor's choice at any moment.

**Independent Test**: with "attack the frigate" at priority 3, a peer
promotes it to priority 1 → the commander's next round executes against it
first.

**Acceptance Scenarios**:

1. **Given** instructions A(1) B(2) C(3), **When** a peer moves C to 1,
   **Then** the order in the commander's prompt is C, A, B.
2. **Given** a reorder conflict (two peers reorder simultaneously),
   **When** resolved, **Then** the newest edit wins and the queue stays
   well-formed (1..N contiguous priorities).

---

### User Story 4 - Commander executes in priority order (Priority: P1)

The commander (executor) works down the queue top-first: it executes the
highest-priority instruction before lower ones, marks work-through (done /
failed / needs-more-rounds) and shows progress so the big models can act on
it.

**Why this priority**: this is the executor contract that makes the other
stories meaningful.

**Independent Test**: seed a queue with 3 instructions; observe the
commander's prompts and orders follow top-to-bottom; a done instruction is
removed by the commander (or marked done for an editor to purge).

**Acceptance Scenarios**:

1. **Given** instructions A(1) and B(2) where A needs multiple rounds,
   **When** the commander runs, **Then** A keeps being executed (its
   sub-orders issued) until done/failed, and B is not touched first.
2. **Given** an instruction that cannot be executed (e.g. build a locked
   hull), **When** the executor attempts it, **Then** it marks the
   instruction failed with the engine's error and moves on.

---

### User Story 5 - Editorial safety and auditability (Priority: P2)

Queue edits are logged with author, timestamp, and before/after; illegal
edits (duplicate ids, bad priorities, giant instruction text, order-tool
impersonation) are rejected with feedback to the editor; the human can read
the live queue at any time.

**Why this priority**: trust and debuggability around delegated control.

**Independent Test**: inject malformed/oversized/impersonating edits →
rejected with feedback; a legal edit chain (add/move/remove) leaves an
auditable history.

**Acceptance Scenarios**:

1. **Given** an edit payload over the size cap or with an unknown op,
   **When** applied, **Then** it is rejected (queue unchanged) and the
   editing peer's log carries the rejection.
2. **Given** any accepted edit, **When** the human inspects the session
   dir, **Then** the queue history file shows who did what, when.

---

### Edge Cases

- What if two peers publish contradictory edits in the same tick?
  Newest-edit-wins per instruction id; conflicts logged.
- What if the queue becomes empty? The commander resumes its standing
  invariants (economy first / production / no idle / research) — the
  queue is advisory-but-binding only when non-empty; standing safety
  rules (defense-before-offense) always apply.
- What if an instruction stalls (executor can't progress it for K
  rounds)? The commander marks it stalled; peers decide keep/drop/split.
- What if the queue file is corrupted? Ignored; previous good queue
  recomputed from history; commander falls back to invariants.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The queue MUST support ADD (text ≤ ~200 chars, priority
  1..N), REMOVE (by id), and REORDER (move to position) — issued by any
  peer; the commander MUST NOT edit the queue.
- **FR-002**: The commander's round prompt MUST present the queue in
  priority order, in place of / above the advisory peer merge.
- **FR-003**: The commander MUST work instructions top-first, marking each
  done/failed/stalled (≥1 observation per instruction), and MUST NOT skip
  ahead while a higher instruction is being worked.
- **FR-004**: Only peers may edit; only the commander executes. Any
  edit-carrying message from the commander side is ignored.
- **FR-005**: Illegal edits (oversized text, unknown ops, duplicate ids,
  out-of-range priority) MUST be rejected with a per-editor rejection
  reason (no silent corruption).
- **FR-006**: The queue and its full edit history MUST be persisted in the
  session dir (JSON, atomic writes) and readable at any time.
- **FR-007**: A new empty queue MUST fall the commander back to its
  standing invariants without peer prompts being lost.
- **FR-008**: Instructions that stall (no progress for N commander rounds,
  default 10) MUST be flagged stalled so peers can re-plan.
- **FR-009**: At match reset the queue MUST be cleared; at matchOver it is
  frozen as history.

### Key Entities *(include if data involves data)*

- **Instruction**: {id, priority, text, state(pending|active|done|failed|
  stalled), created-by peer, created-at, progress notes}
- **instruction-queue.json**: current queue (priority-ordered) + version
- **instruction-history.json**: every accepted/rejected edit (author,
  action, before/after, reason)
- **Editors**: the four peer roles; **Executor**: the commander.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Peers edit the queue continuously: add/reorder/remove edits
  arrive over the match without developer intervention (≥1 accepted edit
  per 2 min under normal peer activity).
- **SC-002**: The commander works the queue top-first: in ≥95% of sampled
  rounds, the order(s) issued match the topmost non-done instruction's
  intent (or the standing invariants when the queue is empty).
- **SC-003**: Zero illegal edits corrupt the queue (all rejections logged
  and queue stays well-formed); queue survives any single peer crash.
- **SC-004**: The full audit chain (queue + history) is recoverable from
  the session dir.

## Assumptions

- The previous feature (agent-multi fleet, artifact files, executor
  prompt, read-only peers) is in place; this feature changes the merge
  from "advisory directives" to "queue the peers own and the executor
  works".
- The commander stays a small fast model; big models keep their read-only
  engine access; instruction text is freeform (one imperative line each).
- Stall threshold (10 rounds) and queue size cap (12) are defaults,
  flag-overridable.