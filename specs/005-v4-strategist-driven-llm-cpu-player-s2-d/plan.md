# Implementation Plan: V4 — Strategist-Driven LLM CPU Player

**Feature**: `005-v4-strategist-driven-llm-cpu-player-s2-d` | **Date**: 2026-10-23 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/005-v4-strategist-driven-llm-cpu-player-s2-d/spec.md`

## Summary

V4 replaces v3's procedurally-driven seat with a model-driven one in
THREE tiers (decision 17): the large strategist S2 reads a compact
budgeted observation each pulse (~120 s game time, sized to its measured
~2 min latency) and emits a **Program** — versioned production orders,
fleet tasking, posture, and a **delegation envelope** licensing the small
fast model S1 (micro-pulse ~8 s game time) to issue tactical commands
ONLY inside that envelope. A thin deterministic **executor** translates the
current program into validated commands each tick plus exactly two reflexes
(idle collectors harvest, full collectors deliver), and carries the S1
micro-command lane with envelope validation. All v3-era procedural
doctrine (s1tick, economy lanes, research chains) is excluded from the v4
driver. The v3 substrate (client, validator, state_store, digest P0 rules,
queue/recorder postgres exchange, config, llm) is reused as libraries.

**Draft v3 amendment (decision 19, from live-round evidence)**: the
executor also TEACHES — every deny/coerce/clamp emits an actionable
verdict (FR-020) streamed to trail + S2 program-status + S1 verdict
section; S2's pulse input becomes the SUPERSET (enemy contacts, asteroid
ledger, build menu, research menu — FR-009); S2 self-compacts via the
budgeted `arc` (FR-021); S1's rolling history window is `s1_context`
(FR-022); engine async rejections carry aux codes into the verdict stream
(FR-023); the reflex floor re-dispatches parade/idle collectors EVERY tick
(FR-006 amended). New control surfaces (all doctrine-free): `verdict`
emitter in executor, `arc` store in the pulse loop, `history truncator`
in the micro lane, `engine-event consumer` in the driver assembly.

## Technical Context

**Language/Version**: Python 3.11+ (matches `tools/twincore/` v3 substrate)

**Primary Dependencies**: stdlib + existing v3 substrate modules only
(`client`, `validator`, `state_store`, `events`, `digest`, `recorder`,
`queue`, `llm`, `config`); httpx-style local HTTP to LLM endpoints via
existing `llm.py`; PostgreSQL for the record store (`twincore_q.messages`)

**Storage**: PostgreSQL record store + exchange (spec-004 FR-021/FR-030
carried); NO local files for trail/programs

**Testing**: pytest (`tools/tests/twincore/test_*.py` conventions, conftest
fixtures) — new tests land with each implementation unit

**Target Platform**: Linux host, peer player via session-bind against the
GoK engine sidecar (spec-003 layer unchanged)

**Project Type**: driver daemon + harness CLI (python tools, not engine code)

**Performance Goals**: executor tick ~1 s game time; program applies whole
at next tick boundary; P0 wake ≤1 tick out-of-cycle for BOTH channels
(SC-004); S1 acts in-envelope within one micro-pulse; S2 pulse input ≤12k
units / out ≤3k; S1 micro-input ≤4k / out ≤900 (FR-013); reflex re-flags
idle/parade collectors every tick (re-dispatch ≤1 tick when applicable);
verdict text bounded (hundreds of bytes, counted in channel budgets);
arc ≤600 chars default, never absent from a pulse input.

**Constraints**: ≤6 validated commands per executor tick; game-time cadence
only (no model calls while paused); one binding (executor's client);
local/LAN-only endpoints (FR-015); playbook numbers as prompt material, not
code constants (FR-004/FR-016)

**Scale/Scope**: one driver process per seat (dual model loops on one
game-time clock, one binding); series harness gains s1 measures; ~6 new
modules + ~8 test files; v3 s1tick/economy/s2pulse stay in-tree,
unreferenced by the v4 path

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|---|---|---|
| I. Faithfulness to Homeworld | PASS | Engine untouched; peer commands only |
| II. Layered, Non-Invasive Integration | PASS | No engine/sidecar changes; spec-003 layer imported unchanged |
| III. Test-First (NON-NEGOTIABLE) | PASS | Implement mini-Vs: each module lands with pytest unit tests green |
| IV. Determinism and Reproducibility | PASS | Game-time cadences; postgres trail replayable (SC-007); ≤1 bounded repair per pulse |
| V. Simplicity (YAGNI) | PASS | Executor = translation + 2 reflexes; no doctrine; three-tier per decision 17 with the smallest channel set (S2 program / S1 envelope / reflexes) — no S2-vs-S1 protocol machinery |
| VI. Single Command Binding | PASS | FR-002: executor's client is the one binding owner |
| Additional: no new build system | PASS | Pure python in existing tools tree |

## Project Structure

### Documentation (this feature)

```text
specs/005-v4-strategist-driven-llm-cpu-player-s2-d/
├── spec.md              # Draft v3 (teaching firewall + arc + s1_context +
│                        #   superset input; decision 19)
├── checklists/requirements.md  # all-pass quality review (re-validated v3)
├── plan.md              # This file
├── research.md          # Phase 0 output (D1–D8; D9/D10 amend for v3)
├── data-model.md        # Phase 1 output (arc/verdict/s1_context entities)
├── contracts/
│   ├── program-schema.md   # Program packet JSON contract + validation
│   ├── s1-envelope.md      # Delegation envelope + S1 micro-packet contract
│   └── executor-contract.md # translation/reflex/ordering + teach duty
├── quickstart.md        # Phase 1 output (run + validation guide)
└── tasks.md             # Phase 2 output (tasks step, next)
```

### Source Code (repository root)

```text
tools/twincore/
├── program.py           # program schema validation, versioning, delegation-
│                        #   envelope validation, program-history, both input
│                        #   builders — v3 adds SUPERSET sections (enemy
│                        #   contacts, asteroid ledger, build/research menus,
│                        #   arc/posture render, verdict render) (FR-008/009/
│                        #   017/018/020/021/022)
├── executor.py          # deterministic translator + reflex set (CONTINUOUS
│                        #   re-dispatch) + S1 micro-command lane + VERDICT
│                        #   EMITTER (FR-005/006/018/020)
├── s1micro loop         # lives INSIDE v4driver.py (increment D reality;
│                        #   planned as s1micro.py — file name deviates, role
│                        #   unchanged): async two-phase micro-pulse, history
│                        #   TRUNCATOR per s1_context (FR-019/022)
├── v4driver.py          # v4 driver assembly: S2 pulse loop (async two-
│                        #   phase), ARC STORE (FR-021), ENGINE-EVENT CONSUMER
│                        #   (orderRejected/ship/research → per-item statuses
│                        #   + aux verdicts, FR-023), degradation ladder
│                        #   (FR-012), GameClock reuse, single binding
├── driver.py            # UNTOUCHED (v3 entry; retired-not-deleted)
├── s1tick.py, economy.py, s2pulse.py, directive.py   # UNREFERENCED by v4
├── client.py, validator.py, state_store.py, events.py  # REUSED unchanged
├── digest.py            # REUSED: classify_deltas/P0Tracker/rollup (FR-010)
├── recorder.py, queue.py # REUSED: + new kinds `program`, `micro`
├── llm.py, config.py    # REUSED: config `v4:` section gains arc_chars,
│                        #   verdict_chars, s1_context limits
tools/tests/twincore/
├── conftest.py          # EXTENDED: program/executor/S1 fixtures
├── test_program.py      # (incl. envelope validation + v3 superset/arc cases)
├── test_executor.py     # (incl. micro lane + law + v3 verdict-stream cases)
├── test_v4_driver.py    # (pulse loops, degradation, dual P0 wake, budget
│                        #   pruning, v3 arc-store/event-consumer cases)
└── (v3 suites stay green — regression gate)
```

**Structure Decision**: v4 lives beside v3 in the existing `tools/twincore`
package: four new control modules (`program`, `executor`, `s1micro`,
`v4driver`) + concentrated reuse of the validated substrate. Retired control
modules (`s1tick`, `economy`, `s2pulse`, `directive`) are neither imported nor
deleted by the v4 driver — a separate cleanup decision (spec FR-014).
No engine changes (Constitution II), no new build targets needed (py tools).

## Complexity Tracking

> No constitution violations — nothing to justify.

## Phase summaries

- Phase 0 (research.md): decisions D1–D8 with rejected alternatives;
  v3 amendment adds D9 (verdict text as bounded teach channel) and D10
  (arc = model-authored state, code-stored/truncated)
- Phase 1 (data-model.md, contracts/, quickstart.md): program packet
  schema, delegation-envelope + S1 micro-packet contract, executor
  semantics, run/validate guide — amended for Draft v3 (arc/s1_context
  schema fields; teach duty; superset input layout)
- Post-design constitution check: PASS (executor contains zero doctrine —
  verdicts explain only the executor's OWN decisions; playbook enters as
  prompt material loaded from the documented playbook markdown into S2's
  charter and S1's micro-charter — data, not code; single binding intact
  despite two model channels; arc/s1_context are budgeted model-authored
  state with mechanical enforcement, no new authority)