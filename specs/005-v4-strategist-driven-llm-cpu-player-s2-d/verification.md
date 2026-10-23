# Verification: Increment A (converge half, mini-V 1)

Feature: `005-v4-strategist-driven-llm-cpu-player-s2-d` | Date: 2026-10-23
Increment: implement A (commits 2e35d74a, 93584ab0 on
`feat/v4-strategist-driven-cpu-player`) — T001–T005 + T024.

Method: Constitution III (unit-level) + the converge contract (verify from
the outside — no new code written during this step). Live/engine-inclusive
scenarios remain out of scope until the driver increments land (T008–T010+).

## What was run

```bash
python3 -m pytest tools/tests/twincore/ -q
#  → 228 passed, 2 skipped (skips: test_harness.py / test_replay.py
#    placeholders, pre-existing "pending: not implemented yet")
python3 -m pytest tools/tests/twincore/test_recorder.py \
                   tools/tests/twincore/test_config.py \
                   tools/tests/twincore/test_program.py -q
#  → 60 passed (recorder 10 incl. PG round-trip, config 12, program 37)
```

## Criterion-by-criterion (increment A slice)

| Criterion | Evidence | Result |
|---|---|---|
| FR-008 whole-packet apply-or-nothing, monotonic versions | `test_program.py::TestWholePacket` (8 tests: schema aborts, JSON parse, version regression bool/str, issued_tick, posture cap) | PASS |
| FR-008 id uniqueness, caps | `TestItems` (6 tests) | PASS |
| FR-017 delegate[] schema, scopes/zones/expiry | `TestDelegate` (8 tests) + `validate_packet` delegate branch | PASS |
| FR-018 micro-packet whole-packet schema abort, per-item envelope blocking, advisory-only with no program, production only via explicit grant | `TestMicroPacket` (8 tests) | PASS |
| FR-009 pulse-input layout static-first, P0 verbatim pinned | `TestPulseInput::test_layout_static_first_and_pinned_p0` | PASS |
| FR-013 budget breach → history-first prune + recorded alerts; pinned floor | `test_breach_prunes_history_first`, `test_breach_sequence_then_pinned_floor`; `TestMicroInput::test_layout_and_over_budget_alert` | PASS |
| FR-013 v4 budgets/ceilings + cadence clamps as config | `test_config.py::TestV4Config` (7 tests) | PASS |
| FR-005 ≤6 cmds/tick, ≤1 repair — as config constants | `v4.max_commands_per_tick == 6`, `v4.max_repair_attempts == 1` asserted (enforcement lands T006–T009) | PASS (config level) |
| FR-006 exact reflex set as config | `v4.reflexes == {harvest, deliver}` asserted (behaviour lands T011–T012) | PASS (config level) |
| FR-011 `program`/`micro` trail kinds + replayable kind filters | `test_recorder.py::test_record_kinds_partitioned` over ALL_KINDS incl. both new kinds (PG round-trip); tail/replay filters share the KINDS list | PASS |
| FR-014 v3-era modules unreferenced by v4 control path | import-graph task T015 not yet run (wiring test pending until v4driver exists) — deferred by design | PENDING (tracked) |
| FR-002 single binding, executor-only issuance | executor/driver not yet in scope — deferred (T006–T010) | PENDING (tracked) |

## Remaining work

Already fully enumerated in tasks.md (T006–T028, Phase 3–9) — no new
convergence tasks appended (append-only contract respected: tasks.md
unchanged this step). Next mini-V iteration: implement B = US1 MVP
(executor translation T006–T007, driver pulse loop T008–T009, regression
gate T010), then US2 reflexes, US3 P0 wake, US6 micro lane completion
(T023 executor/driver cases + T025–T028), US4/US5 hardening, polish.

## Spec-reconciliation note

One wording refinement surfaced during testing (not a code defect):
envelope grants are live while `tick <= expires_tick` and blocked when
`tick > expires_tick` ("dies at" reads off-by-one); contract language was
written to match the implementation's inclusive-live rule. Spec text needs
no change (FR-017 says "expires" without the boundary semantics).