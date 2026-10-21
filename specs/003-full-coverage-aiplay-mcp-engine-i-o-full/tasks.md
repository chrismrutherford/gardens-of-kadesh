# Tasks: Full-Coverage AIPlay MCP

**Input**: design docs from `specs/003-full-coverage-aiplay-mcp-engine-i-o-full/`
(plan.md, spec.md, data-model.md, contracts/mcp-surface.md, quickstart.md)

**Tests**: explicitly required by the spec (SC-002 round-trip, SC-003
conformance, SC-001 coverage) and constitution III (test-first, NON-NEGOTIABLE).

## Phase 1: Setup

- [x] T001 Commit the carried session fixes (pid files, 16-call cap,
  thinking-off retry, analyst full snapshot) as a clean baseline commit on
  feat/full-coverage-mcp
- [x] T002 Create `tools/aiplay_protocol.py` skeleton: module doc, exports
  (REGISTRY, encode_call, decode_call, encode_result, decode_result,
  ERROR_CODES, VERBS), no logic yet per plan.md structure

## Phase 2: Foundational (blocking)

- [x] T003 In `tools/aiplay_protocol.py` build REGISTRY: complete tool table
  (session/state/time per contracts/mcp-surface.md §1; 15 existing order verbs
  + 5 new verbs §2) each with UDS method, funnel name, param schema
  (types/optionality/defaults per data-model.md), read_only flag, mp_only flag
- [x] T004 In `tools/aiplay_protocol.py` implement encode_call/decode_call:
  MCP tool call → UDS `{id,method,params}` wire message; envelope-id vs
  order-id namespace split (order tools keep `id` in params and use it as
  envelope id only when numeric, per sidecar relay() current semantics);
  onOff boolean→"on"/"off" coercion
- [x] T005 In `tools/aiplay_protocol.py` implement encode_result/decode_result:
  UDS `{id,result}|{id,error}` → MCP result payload; expose
  ERROR_CODES map enum↔string (0 none … 9 E_INTERNAL, strings per aiplay.c:31)
- [x] T006 Create `tools/tests/test_aiplay_protocol.py`: (a) coverage test —
  REGISTRY covers every clWrap funnel (list hardcoded from CommandWrap.h) 1:1
  (SC-001); (b) round-trip test — decode(encode(x)) identity for every
  registry tool call (SC-002); (c) schema conformance — valid/invalid param
  samples per verb (≥2 valid, ≥3 invalid, SC-003). Tests must FAIL before
  T003–T005 land (constitution III)
- [x] T007 Refactor `tools/aiplay_mcp.py` to source TOOLS + relay() mapping
  from aiplay_protocol (delete duplicated hand table; FR-008 no-drift); keep
  HTTP surface behavior identical; run `tools/tests/test_agent_multi.py` green

**Checkpoint**: sidecar serves the full existing surface from the single
registry; all python tests green (game untouched).

---

## Phase 3: US1 — Complete observation over MCP (P1) 🎯 MVP

**Goal**: MCP observe/get_events/game_status serve the full authoritative
snapshot/event schemas with no caps or hand filters on the layer side.

**Independent test**: raw UDS vs MCP observe field-parity on a live match
(quickstart §2); determinism check paused (quickstart §4).

- [x] T008 [US1] Add fixtures: capture live state.snapshot/state.events
  responses into `tools/tests/fixtures/aiplay/*.json` (5+ samples incl. mid-battle)
- [x] T009 [P] [US1] In `tools/aiplay_protocol.py` add snapshot schema
  validator (validate_snapshot/validate_events per data-model.md field tables,
  incl. health clamp rule and order-verb enum)
- [x] T010 [US1] Verify observe passthrough returns filtered/complete results;
  remove any sidecar-side truncation; wire validator into sidecar response
  path (fail loud on schema drift)
- [x] T011 [US1] Parity test tool in `tools/tests/test_aiplay_protocol.py`:
  MCP observe == raw UDS snapshot (fields, values) on recorded fixtures

**Checkpoint**: US1 complete — opencode sees the whole world faithfully.

---

## Phase 4: US2 — Every clWrap command reachable (P1)

**Goal**: all 21 funnels reachable via MCP order tools; submit-time
validation parity (FR-009); new verbs end-to-end.

**Independent test**: quickstart §3 scripted order scenarios on a live match.

### C backend

- [x] T012 [P] [US2] `src/AIPlay/aiplay.h`: add verbs
  kamikaze, parade, ru_transfer, alliance, hyperspace to aiplayVerb + names
- [x] T013 [P] [US2] `src/AIPlay/aiplay_orders.c`: verb table entries +
  parseOrder rules (kamikaze/parade take name onOff; ru_transfer params
  `to`,`amount`; alliance `player`,`onOff`; hyperspace target coord[3])
- [x] T014 [US2] `src/AIPlay/aiplay_engine.c` backendDispatchOrder cases:
  clWrapSetKamikaze(layer,select); clWrapSetMilitaryParade;
  clWrapRUTransfer(from=bound,to,amount,0) with RU precheck → E_UNAFFORDABLE,
  valid player → E_BAD_ID; clWrapSetAlliance(type,bound,player); 
  clWrapMpHyperspace(layer,select,from=lead pos,to) (depends T012,T013)
- [x] T015 [US2] Build engine (`meson compile -C build`) +
  `src/AIPlay/tests` unit tests for new verb dispatch (stub backend); green

### Python sidecar

- [x] T016 [P] [US2] `tools/aiplay_mcp.py` via REGISTRY: expose
  order_kamikaze, order_parade, order_ru_transfer, order_alliance,
  order_hyperspace with full JSON Schemas
- [ ] T017 [US2] Submit-time validation (FR-009) in aiplay_protocol +
  sidecar: required params per verb table; unknown/invalid ship type names
  rejected synchronously vs ship-type registry; fleet-wide exemptions
  (research/select/autolaunch/ru_transfer/alliance); ships≤64 — test cases added
- [x] T018 [US2] Live verification per quickstart §3 (each new verb exercised;
  sync rejection vs async orderRejected audited — SC-004)

**Checkpoint**: US2 complete — nothing a human can do that the AI cannot.

---

## Phase 5: US3 — Complete event stream (P1)

**Goal**: all 7 event types correctly surfaced; per-ship damage/under-attack
signal (FR-014).

- [ ] T019 [P] [US3] `src/AIPlay/aiplay_engine.c`: expose per-ship attacker
  signal in shipInfo — set orderTargetId/target on the ship's current
  damage source where the engine tracks it (passive-attack path) so analyst
  peers see "being attacked by id X" (FR-014)
- [ ] T020 [US3] `tools/aiplay_protocol.py`: event schema per data-model
  (aux semantics table); validator reused in get_events path; tests: fixture
  events decode and validate (depends T019)
- [ ] T021 [US3] Fix known quirks (FR-013): (a) harvest targetId raw-pointer
  serialization → mapped resource id (engine writer must id-resolve before
  emit); (b) research field overlap documented + consumers updated; live
  check: collector harvest shows sane resource ids (depends T019)

**Checkpoint**: US3 complete — analysts can prioritize imminent danger.

---

## Phase 6: US4 — Session + time control (P2)

**Goal**: bind/status/save/pause/resume/step fully verified over MCP.

**Independent test**: quickstart §4 cycle on live match.

- [ ] T022 [US4] Contract tests: bind default 0, double-bind rejection,
  matchOver gating, step-while-running rejection, save-name sanitation
  (extends test_aiplay_protocol.py)
- [ ] T023 [US4] Live binding/time-control cycle per quickstart §4; confirm
  byte-identical paused snapshots (determinism, SC-002)

**Checkpoint**: US4 complete.

---

## Phase 7: Polish & cross-cutting

- [ ] T024 [P] Update `OPERATING.md` + `documentation/community/ai-snapshot-dictionary.md`
  pointers to the new registry/spec
- [ ] T025 [US-final] Convergence: verification report against every spec
  criterion SC-001..SC-006 with evidence (test outputs, fixture diffs,
  timings for SC-006 on a 500+ object tick)
- [ ] T026 Full suite green: `tools/tests/test_agent_multi.py` +
  `tools/tests/test_aiplay_protocol.py` + C unit tests; launch-multi smoke

## Dependencies & Execution Order

- Phase 1 → 2 → (3, 4, 5, 6 in priority order; 4 needs C build toolchain) → 7
- T007 blocks all US phases (registry is the source of truth)
- T012/T013/T016 parallelizable (different files); T014/T015 chain
- Live-match tasks (T008, T018, T021b, T023) need a running game — batch them

## Parallel Example: Phase 4

```bash
# T012 (aiplay.h), T013 (aiplay_orders.c), T016 (tools) — no file overlap
# then T014 → T015 → T018 (C build + tests chain)
```

## Implementation Strategy

MVP = Phase 3 (US1): full faithful observation, zero engine changes required.
Then US2+US3 (C backend + sidecar), then US4. Each checkpoint is
independently demoable from opencode via JSON against `:8302/mcp`.