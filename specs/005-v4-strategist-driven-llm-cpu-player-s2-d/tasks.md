# Tasks: V4 — Strategist-Driven LLM CPU Player

**Input**: Design documents from `/specs/005-v4-strategist-driven-llm-cpu-player-s2-d/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/program-schema.md, contracts/s1-envelope.md, contracts/executor-contract.md

**Tests**: Constitution III is NON-NEGOTIABLE — every implementation task lands WITH tests that run green (test-first inside each story). No module is "done" with red or missing tests.

**Organization**: grouped by user story (US1–US6); each story independently implementable/testable. Implementation units are the implement/converge mini-Vs of the sdd workflow. US6 = S1 fast tactical tier in a delegation envelope (decision 17). Phase 10 = Draft v3 amendment (decision 19, 2026-10-23): teaching firewall (FR-020), arc (FR-021), s1_context (FR-022), async feedback (FR-023), superset input (FR-009), continuous reflex floor (FR-006).

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

- [X] T011 [US2] Write reflex tests FIRST in `tools/tests/twincore/test_executor.py`: idle collector → order_harvest(nearest asteroid, tie-break by id); full collector → order_dock(nearest dock-capable); program-wins gate (ship named by live item ⇒ no reflex); S2-disconnected-at-boot scenario (reflexes only, trail-logged)
- [X] T012 [US2] Implement reflexes in `tools/twincore/executor.py` per contracts/executor-contract.md §3 (`reflex_harvest`, `reflex_deliver`; commands cite item_id=reflex_*; same validator lane)

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
- [X] T025 [US6] S1 micro-pulse loop: async two-phase, P0 wake shared with S2, ≤1 bounded repair, whole-packet apply at tick boundary, `micro` trail rows via recorder+queue — IMPLEMENTED as `S1MicroLoop` INSIDE `tools/twincore/v4driver.py` (planned filename s1micro.py deviated; role per contract unchanged; 8 live-cycle tests in test_v4_driver.py green, commit 5a805d65)
- [X] T026 [US6] Micro command lane in `tools/twincore/executor.py`: validated S1 commands citing `{program_version, seq, grant_id, decision_id}` (item_id `s1:<decision_id>`), envelope legality checks, override recording, reflex subordination (lands with 5a805d65; live-round hardening: scope resolution mechanical at executor, ships coercion for string/scalar args, dacc3d0bd)
- [X] T027 [US6] Dual loops wired in `tools/twincore/v4driver.py`: one GameClock, both async loops, single binding preserved, config `v4.budgets.s1_in=4096/s1_out=900`, cadences (s2 120 s default, s1 8 s default); test_config.py extended (lands with 5a805d65)
- [ ] T028 [US6] Harness measures for the tier (extends T018/T019): S1-adherence (micro-commands per grant), envelope violation counts, override counts, micro cadence adherence

**Checkpoint**: between S2 pulses the seat defends itself inside S2-licensed bounds, and nothing else.

---

## Phase 9: Polish & Convergence

- [ ] T020 [P] Charter/playbook load: render `documentation/community/cpu-player-playbook.md` verbatim into the S2 charter prefix AND S1's micro-charter (materials, retunable; harness verifies per FR-016). v3 charter adds the arc rewrite instruction to S2 ("rewrite `arc` every pulse — running story: phase, enemy shape, tried/failed, plan") and the verdict-reading note to S1's micro-charter
- [ ] T021 Run `quickstart.md` end-to-end (unit suites + launch-and-poll live round + series report); collect SC evidence
- [ ] T022 Convergence verification report against spec.md SC-001…SC-008 with trail citations; user review + sign-off; `spec_finish`
- [X] T039 [P] Hygiene: delete stale `tools/tests/test_agent_multi.py` (v4-only prune leftover; imports the deleted `agent` module and breaks FULL `tools/tests` collection) and any other dead imports so `python -m pytest tools/tests -q` collects clean

---

## Phase 10: Draft v3 — teaching firewall + awareness (decision 19)

**Goal**: fix the live-round failure class — blind inputs, silent denials, one-shot reflexes, unbounded memory. Every change is doctrine-free plumbing: verdicts explain, arcs remember, windows truncate, events report.

**Independent Test** (composite of US1/US2/US3/US6 v3 criteria): a live round where the pulse input provably contains enemy contacts/asteroid/menus; a guessed tech name returns as `engine_denied` + verdict once and does not recur; the arc renders every pulse; a parade collector re-harvests ≤1 tick; Qwen denials teach (repeat-identical-denial rate → 0 on the same cause).

- [X] T029 [P] [US1] Write superset-input tests FIRST in `tools/tests/twincore/test_program.py`: `build_pulse_input` renders `# ARC`, ENEMY CONTACTS (same builder as S1's live view), asteroid ledger (nearest-first, top-N + totals), build menu (unlocked hulls + costs + buildQueue), research menu (researchable + missing prereqs + labs/busy + researched); prune order applies (world sections last; arc length-clamped not dropped); empty contacts → "none observed" line, no fabrication
- [X] T030 [US1] (lands with commit: superset builders) Implement superset builders: `program.py` renders the new sections from the driver-assembled view (enemy contacts reused from v4driver's S1 builder); `v4driver.py` ledger assembly passes enemy contacts + asteroids + unlocked hulls/techs + buildQueue/lab state into `build_pulse_input` (FR-009); config `v4.asteroid_top_n`/`v4.arc_chars`/`v4.verdict_chars` + test_config.py cases
- [X] T031 [P] [US2] Write verdict tests FIRST in `tools/tests/twincore/test_executor.py` + `test_program.py`: envelope deny verdict names licensed verbs/ships; unknown tech/hull verdict names legal alternatives; coercion verdicts (scalar→array); cap-clamp verdicts; every verdict → `validation` row + appears in S2 program-status render AND S1 verdict-section render (≤K verdicts, budget-bounded); verdict text ≤ `v4.verdict_chars`
- [X] T032 [US2] Implement the verdict stream: `executor.py` emits verdicts for deny/coerce/clamp (recorder `validation` rows + in-memory last-K list per channel); `program.py` renders verdict sections into pulse/micro inputs; `v4driver.py` engine-event consumer folds orderRejected/shipDestroyed/research events into per-item statuses (`engine_denied: aux`) + verdicts with `# repeat` recurrence marker (FR-020/FR-023)
- [X] T033 [P] [US3] Write arc tests FIRST in `tools/tests/twincore/test_program.py` + `test_v4_driver.py`: packet `arc` field accepted; driver stores latest arc, next pulse renders `# ARC (your summary so far)`; missing/stale arc → code-seeded from mechanical rollups (pulse input NEVER renders an empty arc line); oversize arc → length-clamped + clamp verdict; schema: malformed `s1_context` shape rejects packet, oversize `arc` does not
- [X] T034 [US3] Implement the arc store: program-schema validation (arc optional/any-length; s1_context `{max_exchanges: 0–8, ticks: 60–100000}` shape); `v4driver.py` PulseLoop stores latest program arc, passes into `build_pulse_input`; rollup seeder for missing arc (FR-021)
- [X] T035 [P] [US6] Write s1_context tests FIRST in `tools/tests/twincore/test_program.py` + `test_v4_driver.py`: micro-input carries up to `max_exchanges` previous packets+statuses within `ticks`; truncation is mechanical and deterministic; absent s1_context ⇒ stateless input (current behaviour); budget breach prunes history FIRST (arc/verdicts/world sections survive) (FR-022/FR-013)
- [X] T036 [US6] Implement the history truncator: micro lane keeps rolling history keyed by the CURRENT program's s1_context; prune order enforced in `build_micro_input`; `# EXECUTOR VERDICTS` section precedes history (FR-018)
- [X] T037 [US2] Continuous reflex floor: test (parade/no-harvest collector re-dispatched EVERY tick; program-covered ships skipped; determinism preserved) + implement re-check loop in `executor.py` reflex pass (FR-006; live: RC 273 idled ~20k ticks in parade)
- [X] T038 [US5] Harness measures as pure trail-row functions in `tools/twincore/harness.py` `v4_draftv3_measures()` (deny rate, repeated-identical-denial rate, verdict legibility, arc coverage/max-gap, reflex stale re-dispatches, superset presence from sections meta) — series/report CLI assembly stays in T018/T019 (harness stub; seeded-trail fixtures in test_harness.py): deny rate per channel, repeated-identical-denial rate, verdict legibility (≥1 allowed alternative), arc freshness (byte-age since last S2 rewrite), reflex idle-RC re-dispatch latency (idle/parade → harvest order ≤1 tick); SC-002 superset-presence measure from archived input rows

**Checkpoint**: the strategist sees the whole game, learns from every refusal it triggers, remembers via the arc, and the floor under everything keeps the economy moving — verified by tests and the next live round.

---

## Dependencies & Execution Order

- T001–T002 → T003–T005 → US1 (T006–T010) → US2 (T011–T012) → US3 (T013–T014) → US6 (T023–T028; needs T004 envelope schema + T007 executor lanes) → US4 (T015–T017) → US5 (T018–T019) → **Phase 10 (T029–T038; needs T004/T007/T009 + executor lanes)** → T020–T022 (polish, now includes US6 measures in T028 + convergence T022 covers FR-017–FR-023).
- Within each story: test tasks before implementation tasks (Constitution III); each story lands as one implement+converge mini-V with green tests
- Parallel-safe: T001/T002/T005/T015/T029/T031/T033/T035/T039; T003–T004 pair; T006–T007 and T008–T009 pairs; T029→T030, T031→T032, T033→T034, T035→T036 pairs
- MVP = US1 + US2 (both P1): the seat lives and obeys; US6 closes the strategist blind window; Phase 10 closes the live-round failure class; US3/US4/US5 harden it

## Notes

- Game boundary = the v4 driver's own tick loop; all cadences game-time only.
- Live rounds use the launch-and-poll convention (cash-inject savegame); live evidence tasks (T017, live leg of T021) are scripted procedures recorded in converge, per Constitution III.
- Never block on long waits: run engine/driver in background, poll the trail.