# Tasks: V4 — Strategist-Driven LLM CPU Player

**Input**: Design documents from `/specs/005-v4-strategist-driven-llm-cpu-player-s2-d/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/program-schema.md, contracts/s1-envelope.md, contracts/executor-contract.md

**Tests**: Constitution III is NON-NEGOTIABLE — every implementation task lands WITH tests that run green (test-first inside each story). No module is "done" with red or missing tests.

**Organization**: grouped by user story (US1–US6); each story independently implementable/testable. Implementation units are the implement/converge mini-Vs of the sdd workflow. US6 = S1 fast tactical tier in a delegation envelope (decision 17, 2026-10-23 amendment).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallel-safe (different files, no dependencies)
- **[Story]**: owning user story (US1–US6 from spec.md)

---

## Phase 1: Setup (Shared Infrastructure)

- [X] T001 [P] Extend `tools/tests/twincore/conftest.py` with v4 fixtures: `fake_client` (records minted commands + scripted validator/engine rejection aux codes), deterministic `GameClock`, `fake_llm` (canned/malformed program replay), ledger/state-view builders (collectors idle/full, asteroids, builders, labs)
- [X] T002 [P] Add `v4:` config section to `tools/twincore/config.py` (pulse_seconds default 20 clamp 10–30, tick_seconds ~1, budgets.s2_in=12288/s2_out=3072, reflex toggles, p0_rules passthrough, endpoints.s2 fast-rack default) + validation; extend `tools/tests/twincore/test_config.py`

## Phase 2: Foundational — Program data path (blocks stories)

- [X] T003 [US1] Write `tools/tests/twincore/test_program.py` FIRST: whole-packet validation cases (schema rejects, reason strings, monotonic versions, empty program valid, malformed JSON), packet-shape cases from contracts/program-schema.md
- [X] T004 [US1] Implement `tools/twincore/program.py`: `ProgramError`, `validate_packet(raw, last_accepted_version) -> Program`, `Program` model per data-model.md, `build_pulse_input()` (static charter prefix → P0 pinned → ledger echo → rollups → previous program + statuses → reflex digest; budget 12k with oldest-history prune + recorded alert)
- [X] T005 [P] Add row kind `program` to the recorder taxonomy in `tools/twincore/recorder.py` and to filter lists in `tools/twincore/replay.py`/`tools/twincore/tail.py` (no row-shape change); extend `tools/tests/twincore/test_recorder.py`

**Checkpoint**: program data path green; no doctrine code exists.

---

## Phase 3: US1 — The strategist plans, the executor obeys (P1) 🎯 MVP

**Goal**: S2's program is the seat's sole intent authority; executor = mechanical translation.

**Independent Test** (spec US1): fresh cash-inject match; within first two pulses a program applies (version bump recorded) covering production + research + ≥1 fleet tasking; trail shows every command traced to its program item.

- [X] T006 [US1] Write `tools/tests/twincore/test_executor.py` translation tests FIRST: fixed order (production then tasking, packet order), ≤6 commands/tick cap with carry-over, one-shot retires / standing re-issues, ProgramStatus (issued tick + command ids + resolution applied/blocked/rejected aux) recorded, every command cites {version, item_id}
- [X] T007 [US1] Implement `tools/twincore/executor.py`: `Executor.on_tick(tick, state)` per contracts/executor-contract.md §1–2; validator lane via `tools/twincore/validator.py`; no build priorities/ratios/chains (FR-004)
- [X] T008 [US1] Write `tools/tests/twincore/test_v4_driver.py` pulse-loop tests FIRST: async two-phase (pulse never blocks tick loop), ≤1 program/pulse, malformed output → ≤1 bounded repair then abort (last program stands, failure recorded), whole-packet apply at tick boundary
- [X] T009 [US1] Implement `tools/twincore/v4driver.py`: `PulseLoop` (async two-phase carried; `llm.py` call + ≤1 repair attempt), `v4 driver assembly` (single binding = executor's client; GameClock game-time cadence; paused ⇒ no model calls), trail rows (`program`/`packet`) via recorder+queue
- [X] T010 [US1] Regression gate: full `python -m pytest tools/tests/twincore/ -q` green (v3 suites untouched)

**Checkpoint**: US1 MVP — a seat whose production/research/fleet motion is visibly S2-decided and traceable.

---

## Phase 4: US2 — Reflexes keep the economy alive (P1)

**Goal**: exactly two deterministic reflexes (FR-006); base keeps breathing without S2; program always wins.

- [ ] T011 [US2] Write reflex tests FIRST in `tools/tests/twincore/test_executor.py`: idle collector → order_harvest(nearest asteroid, tie-break by id); full collector → order_dock(nearest dock-capable); program-wins gate (ship named by live item ⇒ no reflex); S2-disconnected-at-boot scenario (reflexes only, trail-logged)
- [ ] T012 [US2] Implement reflexes in `tools/twincore/executor.py` per contracts/executor-contract.md §3 (`reflex_harvest`, `reflex_deliver`; commands cite item_id=reflex_*; same validator lane)

**Checkpoint**: with S2 unreachable from boot the seat still harvests — and NOTHING else autonomously.

---

## Phase 5: US3 — New information → new program (P2)

**Goal**: P0 events wake S2 out-of-cycle; ordinary changes accumulate; re-plans record revisions.

- [ ] T013 [US3] Write P0-wake + revision tests FIRST in `tools/tests/twincore/test_v4_driver.py`: injected P0 (real attack delta) → out-of-cycle pulse with P0 verbatim pinned at top of input (wake ≤1 tick), revision row in trail with reason; supersede leaves old in-flight commands running unless new tasking covers the ships; invalid program ⇒ old version stands + reason loops to next input
- [ ] T014 [US3] Implement in `tools/twincore/v4driver.py`: P0 wake via `tools/twincore/digest.py` `classify_deltas`/`P0Tracker` (rules carried as config, FR-010), P0 verbatim pinning in `program.build_pulse_input()`, supersede semantics at the active-program pointer

**Checkpoint**: reaction to game reality without procedural classification in the driver (digest rule set is config-carried data).

---

## Phase 6: US4 — Same safety rails, thinner code (P3)

**Goal**: v3 guarantees carried; v3-era modules unreachable from the v4 driver.

- [ ] T015 [P] [US4] Import-graph test in `tools/tests/twincore/test_wiring.py`: `v4driver`/`executor`/`program` must NOT import `s1tick`/`economy`/`s2pulse`/`directive` (FR-014) and must not embed playbook constants (FR-016/FR-004)
- [ ] T016 [US4] Degradation matrix tests in `tools/tests/twincore/test_v4_driver.py`: S2 unreachable at boot/mid-match → last program + reflexes, transitions recorded, NO procedural filler; executor/driver down → seat idles; recovery only via re-observe; postgres outage → in-memory ring keeps tail + dropped count (extend `test_recorder.py` if needed)
- [ ] T017 [US4] Live evidence (scripted, recorded in converge): 4-match matrix per spec-004 T025 (strategist killed / driver killed / built-in-CPU-only / rematch) + built-in CPU behaviour unchanged (coexistence)

**Checkpoint**: US4 matrix matches spec-004 §5 observables in v4 wording.

---

## Phase 7: US5 — Measurable (P4)

**Goal**: harness reads the trail; v3 report measures + v4-specific ones.

- [ ] T018 [US5] Write harness measure tests FIRST (extend `tools/tests/twincore/test_harness.py` with seeded-trail fixtures): program-adherence (commands per program item), revision counts, pulse-cadence adherence, budget usage + prune alerts, validator rejection rates, reflex activity
- [ ] T019 [US5] Implement v4 measures in `tools/twincore/harness.py` (same entry points and report shape as v3; computed from `twincore_q.messages` only)

**Checkpoint**: one series run yields the full report with no manual tallying (US5 independent test).

---

## Phase 8: US6 — Fast tactical tier: S1 inside the delegation envelope (P1)

**Goal**: close the ~2-min strategist blind window: S1 reacts to threats within the bounds S2 licensed in its Program (decision 17; FR-017–FR-019).

**Independent Test**: with a P0 attack observed mid-gap between S2 pulses, S1's next micro-pulse issues in-envelope defense commands citing program version + grant + decision id; an out-of-envelope attempt is blocked with the reason recorded and fed back to S1.

- [ ] T023 [US6] Write envelope tests FIRST: `tools/tests/twincore/test_program.py` gains delegate[] schema cases (grant verbs/subjects/zones/expiry; build/research not grantable implicitly; no-delegate ⇒ advisory-only; malformed grant rejects whole packet); `tools/tests/twincore/test_executor.py` gains the priority-law cases (program > S1 > reflex; S1 override only with `override_tasking`; overrides recorded; ≤6/tick cap spans channels); `tools/tests/twincore/test_v4_driver.py` gains violation/repair/degradation cases (out-of-envelope blocked + reason to S1; malformed micro-packet ≤1 repair then abort; S2 down → S1 continues in old envelope; S1 down → program + reflexes; both down → reflexes only)
- [X] T024 [US6] Implement envelope validation in `tools/twincore/program.py` (delegate[] schema + `validate_against_envelope(micro_packet, program_version, tick)` API) + MicroInput builder (static micro-charter, P0 pinned, envelope-relevant live view, own micro statuses; ≤4k units)
- [ ] T025 [US6] Implement `tools/twincore/s1micro.py`: S1 micro-pulse loop (async two-phase, P0 wake shared with S2, ≤1 bounded repair, whole-packet apply at tick boundary) with `micro` trail rows via recorder+queue
- [ ] T026 [US6] Implement the micro command lane in `tools/twincore/executor.py`: validated S1 commands citing `{program_version, seq, grant_id, decision_id}` (item_id `s1:<decision_id>`), envelope legality checks, override recording, reflex subordination
- [ ] T027 [US6] Wire dual loops in `tools/twincore/v4driver.py`: one GameClock, both async loops, single binding preserved, config `v4.budgets.s1_in=4096/s1_out=900`, cadences (s2 120 s default, s1 8 s default); extend `tools/tests/twincore/test_config.py`
- [ ] T028 [US6] Harness measures for the tier (extends T018/T019): S1-adherence (micro-commands per grant), envelope violation counts, override counts, micro cadence adherence

**Checkpoint**: between S2 pulses the seat defends itself inside S2-licensed bounds, and nothing else.

---

## Phase 9: Polish & Convergence

- [ ] T020 [P] Charter/playbook load: render `documentation/community/cpu-player-playbook.md` verbatim into the S2 charter prefix AND S1's micro-charter (materials, retunable; harness verifies per FR-016)
- [ ] T021 Run `quickstart.md` end-to-end (unit suites + launch-and-poll live round + series report); collect SC evidence
- [ ] T022 Convergence verification report against spec.md SC-001…SC-008 with trail citations; user review + sign-off; `spec_finish`

---

## Dependencies & Execution Order

- T001–T002 → T003–T005 → US1 (T006–T010) → US2 (T011–T012) → US3 (T013–T014) → US6 (T023–T028; needs T004 envelope schema + T007 executor lanes) → US4 (T015–T017) → US5 (T018–T019) → T020–T022 (polish, now includes US6 measures in T028 + convergence T022 covers FR-017–FR-019).
- Within each story: test tasks before implementation tasks (Constitution III); each story lands as one implement+converge mini-V with green tests
- Parallel-safe: T001/T002/T005/T015; T003–T004 pair; T006–T007 and T008–T009 pairs
- MVP = US1 + US2 (both P1): the seat lives and obeys; US6 closes the strategist blind window; US3/US4/US5 harden it

## Notes

- Game boundary = the v4 driver's own tick loop; all cadences game-time only.
- Live rounds use the launch-and-poll convention (cash-inject savegame); live evidence tasks (T017, live leg of T021) are scripted procedures recorded in converge, per Constitution III.
- Never block on long waits: run engine/driver in background, poll the trail.