# Tasks: peer-instruction-queue (001-peer-instruction-queue)

## Phase 1: Setup

- [ ] T001 InstructionQueue core in tools/agent_multi.py: queue.json
      (version-bumped, priority-ordered) + history.json ring + apply()
      editor ops (add/remove/move) with validation (FR-001, FR-005, FR-006)

## Phase 2: Foundational

- [ ] T002 Peer editor wiring: each peer loop may include edits payload;
      valid edits applied, rejections fed back to that peer's context
      (editor sees its own rejection history) (FR-005)
- [ ] T003 Commander queue consumption in tools/agent.py merge path: state
      machine pending→active→done/failed/stalled (10-round stall), progress
      notes, top-first; empty queue → standing invariants (FR-002/003/007/008)
- [ ] T004 Reset/matching: queue cleared on match reset; frozen at matchOver
      (FR-009); commander-never-edits invariant enforced in code (FR-004)

## Phase 3: US1 - peers add instructions (P1)

- [ ] T005 [P] [US1] Test: peer add appears in commander's next prompt at
      priority position; adds assign monotonic ids
- [ ] T006 [P] [US1] Test: priority collision resolves newest-wins; queue
      re-prioritized

## Phase 4: US2/US3 - remove + reorder (P1)

- [ ] T007 [P] [US2] Test: remove by id (existing ids); non-existent id →
      rejected "unknown id", queue unchanged
- [ ] T008 [P] [US3] Test: move to front; two-peer reorder conflict →
      newest wins, queue stays well-formed

## Phase 5: US4 - commander executes top-first (P1)

- [ ] T009 [US4] Test: multi-round instruction keeps the commander on it
      until done/failed; failed instruction (impossible task) → marked
      failed with engine error and moves on
- [ ] T010 [US4] Test: stall flagging after 10 non-progress rounds

## Phase 6: US5 - editorial safety + audit (P2)

- [ ] T011 [P] [US5] Test: oversized text / unknown op / duplicate id /
      out-of-range priority → rejected in-history, no corruption
- [ ] T012 [US5] Test: history ring ≤100, survives reader crash; live file
      readable while fleet runs

## Phase 7: Polish

- [ ] T013 Docs: queue section in documentation/community/ai-playbook.md +
      llm-quickstart.md link
- [ ] T014 Converge: run unit tests green, live quickstart; verification
      evidence SC-001..SC-004 in specs/001-peer-instruction-queue
      /verification.md

## Dependencies

- T001 → T002/T003 (core first); T004 after T003
- T002/T003 → T005-T012 tests
- T012 → T013, T013 → T014

## Parallel examples

- T005∥T006∥T007∥T008 (after T002)
- T009∥T010∥T011 (after T003/T004)

## Implementation strategy

- MVP: T001-T004 (queue works end to end) — one editor (strategist) can
  control the commander.
- Then the remaining US test batches; each step leaves the fleet in a
  runnable state.