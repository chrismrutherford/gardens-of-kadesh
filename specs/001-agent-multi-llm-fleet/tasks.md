# Tasks: agent-multi (001-agent-multi-llm-fleet)

## Phase 1: Setup

- [ ] T001 Create artifact store module (atomic JSON read/write, staleness check)
      in tools/agent_multi.py per data-model.md validation rules
- [ ] T002 Create session dir/log plumbing: shared session id
      (`/tmp/aiplay-multi-<id>/`), per-role log helpers, PERF line
      emission (contracts/agent-multi.md log contract)

## Phase 2: Foundational

- [ ] T003 Refactor tools/agent.py: expose `LLM`, `MCP`, `compress_snapshot`,
      `guard_call`, TOOLS, nags via import-safe access (no behavior change;
      guarded `main()`; keep CLI identical) — the single-writer reuse seam
- [ ] T004 Fake-LLM + fake-sidecar test harness (in-process HTTP servers with
      scripted responses and request recording) in
      tools/tests/test_agent_multi.py
- [ ] T005 Peer loop base class in tools/agent_multi.py: back-to-back rounds
      (no pacing sleep), backoff 1s→30s on connection error, 5s poll when
      match not running, reset detection (tick decrease), matchOver idle,
      rolling context (last 3 summaries), no `tools` array in requests,
      discard+log tool-call responses (FR-004, FR-005, FR-007, FR-010)
- [ ] T006 Commander artifact writer in tools/agent_multi.py: orders.json
      ring (~20 entries: round, tool, args, result, tick) written each
      commander round (FR-003 input for critic)
- [ ] T007 Commander artifact reader + prompt merge in tools/agent_multi.py:
      read 4 peer artifacts each round, drop > stale-secs, cap total merged
      text, inject with role + age into the round prompt (FR-003, FR-006)

## Phase 3: US1 Commander commands continuously (P1)

- [ ] T008 [P] [US1] Commander loop in tools/agent_multi.py: run agent.py's
      round pipeline with peer merge and zero peer LLM calls; verify round
      cadence unaffected in test (US1 acceptance 1)
- [ ] T009 [P] [US1] Tests: peer-unreachable → identical cadence;
      artifact-merge appears in next round prompt; stale artifacts excluded
      (US1 acceptances 1–3) in tools/tests/test_agent_multi.py

## Phase 3b: US6 4B executor drives the command loop (P1)

- [ ] T022 [P] [US6] Executor prompt + --llm-commander flag (default 4B
      endpoint) in tools/agent-multi.py; commander-sidecar bind unchanged
- [ ] T023 [US6] Tests: empty build queue → order_build next round; null
      order → move/attack/harvest issued; research gap → order_research or
      ResearchShip build (US6 acceptances 1-3; FR-012/013 audit prompt)

## Phase 4: US2 Peer agents work flat out (P1)

- [ ] T010 [P] [US2] Strategist + Economist role prompts and loops
      (full snapshot, tech tree for economist; plan/production payloads per
      data-model.md) in tools/agent_multi.py
- [ ] T011 [P] [US2] Analyst role (fresh events slice, compressed snapshot,
      reactions payload) in tools/agent_multi.py
- [ ] T012 [US2] Tests: continuous rounds with fake LLMs (≥1 round per poll
      cycle, no sleeps), concurrent loops don't block each other, reset
      rebase clears context (US2 acceptances 1, 3)

## Phase 5: US3 Peers never mutate the match (P1)

- [ ] T013 [P] [US3] Sidecar client wrapper for peers in tools/agent_multi.py:
      allow-list of read-only methods; bind/order attempts raise + log
      (defense in depth beyond FR-010) 
- [ ] T014 [US3] Tests: peer request log contains zero bind/order calls;
      duplicated peer processes don't rebind or re-issue (US3 acceptances)

## Phase 6: US4 Critic gates the commander (P2)

- [ ] T015 [P] [US4] Critic role in tools/agent_multi.py: reads orders.json +
      peer artifacts + compressed snapshot; objections payload; non-noisy
      when orders consistent (data-model.md envelope)
- [ ] T016 [US4] Tests: repeated orderRejected → critic names root cause;
      consistent orders → no objection (US4 acceptance 1–2)

## Phase 7: US5 Observable and launch-safe (P2)

- [ ] T017 [US5] Launcher CLI in tools/agent-multi.py: role endpoint flags
      with 9931–9934 defaults, passthrough agent flags, Ctrl-C join with
      2s grace, exit codes 0/1 per contracts/agent-multi.md
- [ ] T018 [US5] Self-restart parity: peer loops rebind after match outages
      (US5 acceptance 2); final summary on matchOver; autosave behavior
      inherited (FR-009)
- [ ] T019 [US5] Test: --help output lists role flags; startup fatal-error
      exit code 1 (bad sidecar URL)

## Phase 8: Polish

- [ ] T020 Documentation: add "Multi-agent mode" section to
      documentation/community/ai-playbook.md + llm-quickstart.md link
- [ ] T021 Converge: run unit tests green + live quickstart scenario on a
      real match; record verification evidence (SC-001..SC-005) in
      specs/001-agent-multi-llm-fleet/verification.md

## Dependencies

- T001..T002 → T003..T007 (foundation first)
- T004 (harness) blocks all test tasks (T009, T012, T014, T016, T019)
- T007 blocks T008/T009 (commander needs the merge)
- T005 blocks T010–T013 (peer loop base)
- T006 blocks T015 (critic needs orders.json)
- T010–T016 → T017 (launcher wires all roles)
- T019 → T020 → T021

## Parallel execution examples

- Setup+Foundational: T001, then T003 ∥ T004, then T005 ∥ T006 ∥ T007
- US phases: T008 ∥ T009 (after T007); T010 ∥ T011 ∥ T013 (after T005)
- MVP: complete Phases 1–3 (T001–T009). Value: commander unblocked +
  merge machinery ready; peers light up incrementally per phase.

## Implementation strategy

- MVP first: commander + merge (US1) — match playable exactly as today.
- Then peers one story at a time; each leaves the system in a working state
  (a dead/absent peer degrades to fewer artifacts, never blocks).
- All tests run in-process (no real LLM needed in CI).