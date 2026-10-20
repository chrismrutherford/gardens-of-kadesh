# Tasks: fleet refactor (001-fleet-refactor)

## Phase 1: Setup (no behavior change)

- [ ] T001 Create agent_core.py (LLM/MCP/PeerSidecar/http_json/LoopLogger
      + JSONL round-event writer) and agent_snapshot.py (compress/
      world_summary/order_detail/ships_already_executing/guard_call) —
      move code, keep agent.py re-exports for compatibility
- [ ] T002 Create agent_prompts.py: DOCTRINE constants + all role prompts
      per contracts/prompts-and-telemetry.md + playbook/dictionary loader;
      delete prompts from agent.py/agent-multi.py
- [ ] T003 FleetContext dataclass + explicit wiring; remove AIPLAY_MULTI_*
      env vars + argv injection; agent.py CLI becomes thin wrapper (FR-004,
      FR-008)

## Phase 2: Commander pipeline extraction

- [ ] T004 agent_rounds.py: extract observe→merge→events→dispatch loop,
      nags, autosave, dedupe from agent.py main(); behavior-identical
- [ ] T005 Update tests for new module map (imports only); all green

## Phase 3: Telemetry + status (observability)

- [ ] T006 JSONL round events for all 5 loops (FR-001) + tests
- [ ] T007 fleet-status.py renderer + STALE flags + tests (FR-002, SC-001)

## Phase 4: Doctrine + descriptions (behavior change — reviewed first)

- [ ] T008 Rewrite tool descriptions per contracts §3 (FR-006) + tests
      (every tool: purpose + when + constraints)
- [ ] T009 Wire DOCTRINE into role prompts per contracts §2 (FR-005) +
      doctrine-marker tests; live-run SC-005 comparison

## Phase 5: Polish

- [ ] T010 Docs: playbook cross-links, fleet-status in llm-quickstart,
      update agent-multi.puml/infoflow for the new module map
- [ ] T011 Converge: full test suite green; live quickstart run; evidence
      (SC-001..SC-005) in specs/001-fleet-refactor/verification.md

## Dependencies

T001→T004→T005; T002/T003 parallel after T001; T006/T007 after T004;
T008/T009 after T002 + user review of contracts; T010→T011 last.

## MVP

T001–T005 (structure, zero behavior change) — then telemetry, then the
reviewed prompts/descriptions.