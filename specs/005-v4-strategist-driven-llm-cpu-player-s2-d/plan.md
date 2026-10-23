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
units / out ≤3k; S1 micro-input ≤4k / out ≤900 (FR-013)

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
├── spec.md              # Draft v2 (three-tier amendment, decision 17)
├── checklists/requirements.md  # all-pass quality review (pre-amendment)
├── plan.md              # This file
├── research.md          # Phase 0 output (D1–D8)
├── data-model.md        # Phase 1 output
├── contracts/
│   ├── program-schema.md   # Program packet JSON contract + validation
│   ├── s1-envelope.md      # Delegation envelope + S1 micro-packet contract
│   └── executor-contract.md # translation/reflex/ordering semantics
├── quickstart.md        # Phase 1 output (run + validation guide)
└── tasks.md             # Phase 2 output (tasks step, next)
```

### Source Code (repository root)

```text
tools/twincore/
├── program.py           # NEW — program schema validation, versioning,
│                        #   delegation-envelope validation, program-history,
│                        #   both input builders (FR-008/009/017/018)
├── executor.py          # NEW — deterministic translator + reflex set +
│                        #   S1 micro-command lane (FR-005/006/018)
├── s1micro.py           # NEW — S1 micro-pulse loop (async two-phase,
│                        #   ≤1 bounded repair, budget enforcement FR-019)
├── v4driver.py          # NEW — v4 driver assembly: S2 pulse loop (async
│                        #   two-phase, carried), dual-channel degradation
│                        #   ladder (FR-012), GameClock reuse, single binding
├── driver.py            # UNTOUCHED (v3 entry; retired-not-deleted)
├── s1tick.py, economy.py, s2pulse.py, directive.py   # UNREFERENCED by v4
├── client.py, validator.py, state_store.py, events.py  # REUSED unchanged
├── digest.py            # REUSED: classify_deltas/P0Tracker/rollup (FR-010)
├── recorder.py, queue.py # REUSED: + new kinds `program`, `micro`
├── llm.py, config.py    # REUSED: config gains a `v4:` section (dual-channel)
tools/tests/twincore/
├── conftest.py          # EXTENDED: program/executor/S1 fixtures
├── test_program.py      # NEW (incl. envelope validation)
├── test_executor.py     # NEW (incl. micro lane + law: program > S1 > reflex)
├── test_s1_micro.py     # NEW (micro loop, budgets, repairs, degradation tier)
├── test_v4_driver.py    # NEW (pulse loop, dual degradation, reflex/program
│                        #   precedence, dual P0 wake, budget pruning)
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

- Phase 0 (research.md): decisions D1–D8 with rejected alternatives
- Phase 1 (data-model.md, contracts/, quickstart.md): program packet
  schema, delegation-envelope + S1 micro-packet contract, executor
  semantics, run/validate guide
- Post-design constitution check: PASS (executor contains zero doctrine;
  playbook enters as prompt material loaded from the documented playbook
  markdown into S2's charter and S1's micro-charter — data, not code;
  single binding intact despite two model channels)