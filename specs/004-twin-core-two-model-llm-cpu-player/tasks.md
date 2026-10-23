# Tasks: Twin-Core Two-Model LLM CPU Player

**Input**: design docs from `specs/004-twin-core-two-model-llm-cpu-player/`
(plan.md, spec.md, research.md, data-model.md, contracts/twin-core-protocol.md,
contracts/driver-runtime.md, quickstart.md)

**Tests**: constitution III (test-first, NON-NEGOTIABLE): every module lands
with/behind its suite in `tools/tests/twincore/`; live-simulation checks follow
the scripted launch-and-poll protocol (research D10) and are recorded as
verification evidence.

Phases map 1:1 to plan.md milestones (A substrate · B S1 floor · C twin-core ·
D evaluation). Retired agent stacks (`agent.py`, `agent_tools.py`,
`agent-multi.*`) are NEVER imported (research D7); game engine and sidecar are
never modified.

## Phase 1: Setup

- [x] T001 Create package skeleton `tools/twincore/` (all 15 modules from
  driver-runtime.md §1 as docstring-only stubs; `__init__.py` exports),
  `tools/tests/twincore/` (per-module suite stubs marked skipped until their
  task lands, so the suite is green but failing-ready), and
  `tools/twincore/configs/` (empty dirs); no logic yet
- [x] T002 Verify the substrate imports cleanly: `tools/twincore/` imports
  `aiplay_protocol.REGISTRY` (order verbs + schema access) with zero sidecar
  imports reaching old agent stacks; smoke test collects all suites
  (`python3 -m pytest tools/tests/twincore -q`, green with skips)

## Phase 2: M-A substrate (recorder · config · client · events · clocks) 🎯

**Goal**: full-match recording + replay, deterministic game-time cadence, and
the seat lifecycle with degradation lines — no models involved.

**Independent test**: stub-endpoint unit suites green; live: launch cmr1 +
boot a driver that binds, records a full match trace, and replays it offline.

- [x] T003 [P] `recorder.py`: append-only JSONL, one file per kind (protocol
  §6 list: event_raw, classification, delta, rollup, digest, packet,
  packet_rejected, revision, decision, command, validation, usage, breach,
  degradation, question); every line carries {match_id, tick, wall_ts};
  in-memory ring (cap 512, D9) for event_raw only; `--kinds/--ticks` scan
  filters; crash-safe appends. Tests first: tmp-dir suite covering all kinds,
  ring eviction, filter scans (FR-021, SC-007)
- [x] T004 [P] `config.py`: JSON schema + loader + typed accessor (defaults
  per driver-runtime.md §2); difficulty preset resolution (beginner/
  intermediate/advanced → tick cadence ~2.1s/1s/0.5s game time, budget split,
  advanced-only play flags per playbook B3 — parameters only, FR-026);
  validation errors loud. Tests first (FR-020 budget constants present)
- [x] T005 `client.py`: MCP HTTP session client to the sidecar (bind, status,
  observe, get_events, pause/resume, step, save-as-diagnostic) encoding via
  `aiplay_protocol` only (D2/D3); re-observe policy triggers on: bind, match
  load/restart, id-mismatch error surge (edge case contract); returns
  snapshots/events/CommandRecords. Tests with in-process stub HTTP server
  (no real endpoint) covering bind-once semantics and re-observe triggers
- [x] T006 `events.py`: raw event consumption → EventDelta stream (deltas vs
  last-seen; created/destroyed/damaged/moved/formed kinds per data-model);
  feed for recorder `event_raw` and downstream classifier. Tests on spec-003
  fixture events + synthetic edge sequences (alliance events, id renumber)
- [x] T007 `driver.py` core: seat lifecycle UNBOUND→BOUND_ACTIVE→DEGRADED_*→
  UNBOUND (protocol §8); degradation ladder with recorder `degradation` lines
  on every transition; game-time clock (tick deltas from observations; paused
  match ⇒ no model calls, FR-003/FR-029); combat-intensity gating hooks
  (config). Tests: transition matrix vs driver-runtime.md §5 rows 1,3,4 with
  a stub client + fake snapshot feed

**Checkpoint**: recording+replay proven offline; lifecycle green; engine and
sidecar untouched. Live batch L1 (T014) exercises it for real.

---

## Phase 3: M-B S1 tactical floor (ledger · digest · validator · economy · S1 loop)

**Goal**: S1-only seat plays the playbook opening to match end on the boot
directive (US1; P0 pinning part of US2-3).

**Independent test**: stub-LLM unit suites green; live: Scenario A (S1-only
full match, boot-default.json).

- [x] T008 `state_store.py`: canonical ledger — own fleet inventory + unit
  states (hp, position, formation, tactics, dock status), RU bank + income
  projection, per-hull-class enemy knowledge (ever-observed, counts,
  last-seen), blob ratings (own/enemy spatial groups, strength scores; the
  1.4× overkill input), alliance state, pending commands + statuses, directive
  version ref; full rebuild from a snapshot; delta-apply from EventDelta
  stream. Tests: rebuild == incremental correctness on fixtures (FR-013)
- [x] T009 `digest.py`: P0/P1/P2 classifier (config rules; D9 defaults: seat
  command centre attacked/lost, defended-zone breach by rated force ≥
  threshold, battle group annihilated, RU flow zeroed while bank high),
  rollups at rollup-interval ticks, interval rollup builder (1-2 lines +
  class histogram), digest builder per protocol §5 (p0 verbatim, changes,
  order_status incl. veto reports, s1_notes, ledger-cited projections;
  budget 300-800 with one reduced-scope regeneration, else gap logged).
  Tests first: classification table, P0 never evicted (FR-011), no-eviction
  under trimming, rollup compression (FR-010/FR-012)
- [x] T010 `validator.py`: tri-state over `REGISTRY['order_tools']` per
  protocol §3 in order: 1 schema/schema-bounds → E_BAD_REQUEST; 2 ids in
  ledger last observation → E_BAD_ID + re-observe hint; 3 alliance protection
  (attack-family vs ally/ally asset) → E_ALLIED_PROTECTION; 4 affordability
  (build/research vs bank) → E_UNAFFORDABLE; 5 dedupe/clamp vs in-flight +
  standing orders, per-tick cap ≤6, clamp(original→corrected). Fail-loud on
  registry drift. Tests first: per-check tables incl. clamp output shape
  (FR-005/FR-006), zero-invalid-at-game-boundary property
- [x] T011 `directive.py`: schema validation (protocol §4 field list), boot
  default directive (FR-023 playbook conservative opening: collectors dispatch
  first, guards on resource ships, no early aggression), monotonic versioning
  accepting only semantic patches, sticky-goal gate helper (evidence ≥2
  corroborations across ≥2 digests + hold ≥60s game time; configuration).
  Tests: patch apply/reject matrix, sticky gate negatives (FR-016/FR-017),
  revision history written to recorder
- [x] T012 `economy.py`: deterministic resource manager — playbook A1 opening
  (all collectors → harvest before any offensive build), collectors→
  controllers→support ratio, 30/30/40 budget policy as build priorities,
  research delay per preset; emits commands through the SAME validator/binding
  (FR-027). Tests: opening sequence on synthetic ledger, budget-split
  arbitration, saturation stop conditions (playbook A6)
- [x] T013 `s1tick.py`: tick loop per protocol §1 — context assembly
  static-first ([1 role card][2 directive vN][3 event window: P0 top, rollups
  then last-K deltas][4 ledger echo][5 in-flight orders]), budget ≤8k with
  breach discipline (trim deltas→rollups→seed-reset + `breach` line, FR-020);
  parse ≤6 tool-call proposals → validator in order → accepted issued via
  client (sole binding owner) → CommandRecord; rejected/clamped returned as
  tool errors appended to NEXT tick input (FR-007); malformed/empty output
  retried ≤2/tick then idle tick + streak recorded. Static-first prompt
  layout verified (FR-022). Tests with stub LLM channel (FR-008: no durable
  S1 memory; trim/reset safe)
- [ ] T014 **[LIVE]** M-B verification on cmr1 (Scenario A, quickstart §A):
  S1-only boot; collectors dispatched inside first five minutes before
  offensive units; guards over resource ships; steady game-time cadence; zero
  invalid commands at the game boundary; recorder trail complete; replay of
  the segment from recorder alone. Evidence recorded (SC-001, SC-002)
  *Progress 2026-10-23: live rounds on `ai-cash-inject` (fast S1 endpoint
  192.168.1.106:9931 qwen3.8-27B, thinking off, ~1-5s/tick): bind → cadence
  ticks (1s game time) → economy + S1 issuing guard/build/tactics/harvest/
  move; validation ok-rate 93→100% (fresh round, zero rejections); all 10
  collectors harvesting after parade re-dispatch fix; fleet grew 2→10
  collectors + 4 controllers via the deterministic lane. Remaining for the
  checkbox: SC-002 needs a FRESH skirmish opening (this save starts
  mid-match) + replay-from-recorder proof pass.*

**Checkpoint**: US1 complete — an LLM CPU plays a sane full match unprompted.

---

## Phase 4: M-C twin-core (S2 · packets · sticky · history ops · P0 wake · ladder)

**Goal**: strategist visibly steers doctrine mid-match with hysteresis
(US2/US3) and degradation holds (US4).

**Independent test**: stub-endpoint suites green (incl. degradation matrix);
live: Scenarios B+D.

- [ ] T015 `llm.py`: OpenAI-compatible chat-completions client per channel —
  S1 native tool-calling; S2 structured JSON output; output budget enforced
  truncation-before-send (never mid-parse); bounded retries; usage metering
  per call → recorder `usage` lines. Tests against a stub HTTP endpoint
  (chat completions + tool-call + JSON-mode paths; FR-028: URLs config-only)
- [ ] T016 `packet.py`: control-packet validation + apply order (validate
  whole → directive patch (bump) → orders → history op → digest config);
  whole-patch-failure ⇒ zero elements applied + `packet_rejected` line + old
  directive stays, no same-pulse retry (US3-5); sticky-goal enforcement drops
  an ungated primary-enemy change but applies the rest (flagged audit line);
  order subjects validated at issue time vs ledger. Tests first per
  data-model apply semantics (FR-015/FR-016/FR-017)
- [ ] T017 `s2pulse.py`: pulse loop — cadence 30-90s game time (config) +
  immediate async P0 wake (p0_wake flag); input assembly per protocol §2
  (charter, ledger projections, pinned goals w/ age stamps + hold timers, last
  M=8 digests + milestones, own last packet + outcomes, question answers);
  ≤1 packet per pulse; question queue answered next pulse from ledger only
  (FR-014). Tests: pulse pacing in game time, P0 wake asyncness vs tick loop,
  question round-trip (FR-019)
- [ ] T018 History ops + veto path: trim{scope} drops listed window contents;
  reset{seed} collapses S1 conversation to [role card + directive vN + state
  seed + in-flight orders] (in-flight never lost, data-model US3-2); standing
  order suspend/resume under declared emergency with mandatory s1_notes veto
  report; former-ally standing orders aborted with reason in next digest
  order_status (protocol §7 edge case). Tests per scenario
- [ ] T019 Degradation ladder integration (stub endpoints, driver-runtime §5
  matrix): S2 killed → S1 continues last directive, no invented pulses, retry
  backoff in game time, recovery on re-attach; S1 killed → zero commands,
  S2 never binds; interface down → both idle; budget breach → channel reset
  discipline + `breach` + `degradation` lines. Tests: every matrix row
  (FR-024, US4)
- [ ] T020 **[LIVE]** M-C verification on cmr1 (Scenarios B+C, quickstart):
  scheduled pulses on cadence + out-of-cycle P0 pulse; packet applies at tick
  boundary with version bump; doctrine change within one pulse past evidence
  threshold; no primary-enemy oscillation inside hold time; invalid packets
  logged not applied. Evidence recorded (SC-003, SC-004)

**Checkpoint**: US2/US3/US4 complete — doctrine visibly adapts, sticky.

---

## Phase 5: M-D evaluation (replay CLI · harness · presets · live series)

**Goal**: quality measurable; every spec criterion has recorder-grounded
evidence (US5).

- [ ] T021 [P] `replay.py` CLI: `--match <dir> --kinds ... --ticks a-b`
  extraction + decision-trace pretty-print (events, digests, directive
  versions, decisions, outcomes for that range). Tests on synthetic
  recordings + a recorded-match fixture (SC-007)
- [ ] T022 [P] `harness.py`: series runner (--config/--opponent builtin/
  --difficulty/--matches N) with per-series report.md generation — win/loss/
  draw, opening adherence, P0 next-tick rate, out-of-cycle pulses, cadence
  adherence, per-channel observed-vs-budget + breach list, rejection/clamp
  rates, digest counts/sizes, degradation events (driver-runtime.md §4).
  Tests with synthetic recorder dirs (no model calls — reports from fixtures;
  SC-006 measures defined)
- [ ] T023 Difficulty presets finalisation: beginner/intermediate/advanced
  parameter sets over the ONE directive schema (playbook B3; cadences;
  advanced-only flags) exercised through s1tick+economy config paths with
  tests proving one schema/parameters-only (FR-026)
- [ ] T024 **[LIVE]** Harness series per quickstart §E at each difficulty
  (≥3 matches each): reports landed; SC-006 evidence attached to this file
- [ ] T025 **[LIVE]** Degradation + coexistence runs (Scenario D): four
  short matches (builtin-only, S2 killed, S1 killed, driver killed) meeting
  driver-runtime.md §5 observables; recorded evidence (SC-008)

**Checkpoint**: US5 complete — acceptance reads reports, not hunches.

---

## Phase 6: Polish & convergence

- [ ] T026 [P] Documentation pointers: `documentation/community/README.md`
  + `installation-manual.md` + `llm-quickstart.md` gain the twin-core entry
  points (driver/replay/harness) and configs; OPERATING.md pointer if present
- [ ] T027 Convergence: verification report vs SC-001..SC-008 with test
  output/file evidence per criterion; full suite green
  (`python3 -m pytest tools/tests/twincore -q` + existing
  `tools/tests/` suites unbroken); constitution re-check (I/II/III/IV/V/VI)
  recorded

## Dependencies & Execution Order

- Phase 1 → 2 → 3 → 4 → 5 → 6 (milestones are strictly layered: M-B needs the
  recorder/client; M-C needs the S1 floor; M-D needs live matches)
- Within Phase 2: T003/T004 parallel → T005 → T006/T007
- Within Phase 3: T008 → T009 → T008+T009 → T010/T011/T012 parallel → T013
  (T010 gate) → T014 live batch
- Within Phase 4: T015 → T016/T017 → T018 → T019 → T020 live batch
- Phase 5: T021/T022/T023 parallel → T024/T025 live batch
- LIVE tasks (T014, T020, T024, T025) follow the launch-and-poll protocol on
  `./launch.sh cmr1 --port 8302`; batch them per session

## Parallel Example: Phase 3

```bash
# T010 (validator), T011 (directive), T012 (economy) — disjoint files, all
# depend only on T008's ledger interface; T013 integrates all three.
```

## Implementation Strategy

MVP = Phases 1-3 (M-A + M-B): a recording, degrading, S1-only CPU playing the
playbook opening — demoable live per Scenario A. Twin-core (Phase 4) and the
harness (Phase 5) then ride on a stable floor. Every phase ends with its
checkpoint before the next starts; live tasks are batched and never block the
console.