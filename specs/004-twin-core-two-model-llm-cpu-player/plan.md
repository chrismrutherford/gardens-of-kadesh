# Implementation Plan: Twin-Core Two-Model LLM CPU Player

**Feature**: `004-twin-core-two-model-llm-cpu-player` | **Date**: 2026-10-23 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification `specs/004-twin-core-two-model-llm-cpu-player/spec.md`

## Summary

Iteration-3 CPU player: replace the retired iteration-1/2 agent stacks with a
two-model architecture — a fast tactical core (S1) that is the only binding
owner and issues validated game commands per game-time tick; a large
strategist (S2) that never touches the game and shapes S1 through a
control-packet protocol (versioned directive patches, standing orders, history
operations, digest configuration, questions); a deterministic orchestrator
between them (game-time clocks, P0/P1/P2 digest engine with rollups, canonical
state store/ledger, tri-state command validator over the spec-003 registry,
append-only JSONL recorder), plus a deterministic economy module and an
evaluation harness. Technical approach and all decisions: [research.md](research.md);
data shapes: [data-model.md](data-model.md); internal protocol:
[contracts/twin-core-protocol.md](contracts/twin-core-protocol.md); runtime:
[contracts/driver-runtime.md](contracts/driver-runtime.md).

## Technical Context

**Language/Version**: Python 3 (std-lib first; precedent: tools/aiplay_mcp.py,
tools/aiplay_protocol.py)

**Primary Dependencies**: `tools/aiplay_protocol.py` REGISTRY (single-source
command surface, 20 order verbs); existing MCP sidecar `tools/aiplay_mcp.py`
(unchanged); OpenAI-compatible local HTTP model endpoints (config only);
existing in-repo doctrine docs (`cpu-player-playbook.md`, `ai-playbook.md`,
`ai-strategy-digest.md`, `ai-snapshot-dictionary.md`)

**Storage**: append-only JSONL recorder files (per-record-kind partitioned);
JSON configs (precedent agent-multi.json). No database.

**Testing**: pytest (`tools/tests/twincore/`, pattern of `tools/tests/`) +
recorded-fixture replay tests (spec-003 pattern) + scripted live verification
rounds per constitution III (launch-and-poll protocol, `cmr1` savegame)

**Target Platform**: Linux first (game host + local/LAN model server)

**Project Type**: peer-client driver process + offline harness (no engine code)

**Performance Goals**: S1 tick cadence ~1s game time (combat-gated, config);
S1 input constant ≤8k units across match length; S2 pulse 30-90s game time
with ≤2k-unit packets; per-channel budgets per spec FR-020

**Constraints**: zero engine/sidecar modifications; exactly one binding owner
(S1 path); game-time clocks only (constitution IV); local-only transport;
retired stacks not imported (research D7)

**Scale/Scope**: one driver per bound seat; ~15 modules + suites + configs +
harness reports; four milestones (A substrate, B S1 floor, C twin-core, D
evaluation)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Gate (principle) | Status | Evidence |
|---|---|---|
| I. Faithfulness | PASS | peer client only; zero engine changes (research D3/D8; all effects are ordinary commands) |
| II. Layered, non-invasive | PASS | driver consumes existing aiplay/MCP layer via sidecar; no engine edits; python tool placement per repo convention (D1) |
| III. Test-first | PASS | per-module pytest suites + fixture replays planned per milestone; live/manual verification protocol defined (D10, quickstart) |
| IV. Determinism | PASS | game-time clocks only (D4, protocol §1/§2); recorder lines carry no decision-relevant wall-clock data (protocol §6) |
| V. Simplicity (YAGNI) | PASS | deterministic economy in v1 (FR-027); no tournaments/UI/training loops; harness limited to series report (FR/US5); single directive schema incl. difficulties |
| VI. Single command binding | PASS | S1 command path is the sole binding owner; S2 and ledger-side components cannot bind or order (FR-002, protocol §8) |
| Additional: no 2nd build system | PASS | python tool in tools/ — no meson target needed; sidecar reused as-is |
| Additional: licensing | PASS | python under repo's existing licence headers/tools/ convention |
| Additional: security/local IPC | PASS | sidecar local-only reuse; model endpoints config = localhost/LAN (driver-runtime §2) |

Post-design re-check: no violations introduced by Phase 1 artifacts;
complexity tracking stays empty (below).

## Project Structure

### Documentation (this feature)

```text
specs/004-twin-core-two-model-llm-cpu-player/
├── plan.md                    # this file
├── research.md                # Phase 0 decisions (D1-D10)
├── data-model.md              # Phase 1 entities, transitions
├── contracts/
│   ├── twin-core-protocol.md  # S1 tick / S2 pulse / validator / schemas / lifecycle
│   └── driver-runtime.md      # modules, config, harness, degradation matrix, entry points
├── quickstart.md              # validation scenarios A-E
├── checklists/requirements.md # specify-step quality checklist (done)
└── tasks.md                   # tasks step output (next)
```

### Source Code (repository root)

```text
tools/
├── aiplay_protocol.py   # existing; imported ONLY (registry single source)
├── aiplay_mcp.py        # existing sidecar; unchanged
├── agent.py, agent_tools.py, agent-multi.*   # existing; retired inputs, untouched (D7)
└── twincore/            # NEW — the driver package
    ├── __init__.py
    ├── driver.py        # lifecycle, degradation ladder, wiring
    ├── config.py        # schema, loader, difficulty presets resolution
    ├── client.py        # session client via sidecar MCP; re-observe policy
    ├── events.py        # delta-vs-last-seen event consumption
    ├── digest.py        # P0/P1/P2 classifier, rollups, digest builder
    ├── state_store.py   # canonical ledger
    ├── validator.py     # tri-state command validation (registry-grounded)
    ├── economy.py       # deterministic resource manager (30/30/40, openings)
    ├── directive.py     # directive schema, patching, sticky-goal gate
    ├── packet.py        # control-packet validation + ordered application
    ├── s1tick.py        # S1 tick loop: context assembly, tool mapping, error loop
    ├── s2pulse.py       # S2 pulse loop: input build, emission, questions
    ├── llm.py           # chat-completions clients, tool calling, usage metering
    ├── recorder.py      # append-only JSONL, per kind
    ├── replay.py        # offline tick-range/kind extraction
    └── harness.py       # series runner + per-series report
tools/tests/twincore/    # NEW — pytest suites per module + fixture replay tests
tools/twincore/configs/  # NEW — boot-default.json, twin-core.json, presets
```

**Structure Decision**: python peer-client package under `tools/` (matches
iteration precedent and the constitution's no-second-build-system rule); game
engine and sidecar untouched; retired agent stacks remain in place untouched
and are not imported.

## Milestone decomposition (drives tasks.md; from design doc §9)

- **M-A substrate**: recorder + client + events/deltas + config + clocks
  (game-time, pause-safe) with suites. *Proves: full-match recording + replay.*
- **M-B S1 tactical floor**: state store, digest engine (classifier + rollups),
  validator, boot directive, S1 tick loop, deterministic economy. *Deliverable:
  S1-only seat plays the playbook opening to match end (US1/US2 partially:
  P0 pinning).*
- **M-C twin-core**: S2 pulse loop, control packets, directive patching +
  sticky gate, history ops, P0 async wake, veto reporting, degradation ladder.
  *Deliverable: doctrine visibly adapts with hysteresis (US2/US3/US4).*
- **M-D evaluation**: harness series CLI, reports, difficulty presets
  parameterisation, degradation matrix green, live verification rounds
  (SC-001..SC-008 evidence).

## Complexity Tracking

> Empty: no constitution violations to justify.