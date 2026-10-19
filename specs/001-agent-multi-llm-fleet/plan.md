# Implementation Plan: agent-multi (4-LLM parallel fleet AI)

**Feature**: `001-agent-multi-llm-fleet` | **Date**: 2026-10-19 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-agent-multi-llm-fleet/spec.md`

## Summary

Five cooperative AI loops over one match: the commander (sole binding + order
queue) plus four read-only peer advisors (strategist, analyst, economist,
critic), each driving its own dedicated LLM server back-to-back ("flat out").
Peers publish JSON artifacts to shared state; the commander merges the freshest
peer output into each round prompt with zero extra LLM cost. Delivered as a new
tool `tools/agent-multi.py` + shared module; nothing in `tools/agent.py` or the
engine changes.

## Technical Context

**Language/Version**: Python 3 (stdlib only — reuses the `tools/` pattern;
no new vendored deps)

**Primary Dependencies**: urllib/http (existing agent patterns), threading +
json + tempfile/atomic writes; OpenAI-compatible chat completions over 4
llama.cpp servers (192.168.2.72:9931-9934, defaults only, all CLI-overridable)

**Storage**: none new — JSON artifact files in `/tmp/aiplay-multi-<session>/`
(atomic os.replace writes)

**Testing**: python std-lib `unittest` under `tools/tests/` (pattern of
`test_aiplay_mcp.py`); a fake-LLM server harness for peer-loop tests

**Target Platform**: Linux (same box as the game or LAN); localhost/LAN HTTP
to LLM servers, game sidecar/UDS unchanged

**Project Type**: desktop game companion CLI tool (single process, threads)

**Performance Goals**: every peer issues a back-to-back round against its own
server (no pacing sleeps); commander cadence within ±20% of single-agent
baseline; commander round does zero peer LLM calls

**Constraints**: peers strictly read-only (no bind/order tool calls — verified);
artifacts stale at 60 s and excluded from prompts; local-only transport

**Scale/Scope**: 5 loops, 4 artifact files, 2 new modules, ~1 new test file

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|---|---|---|
| I. Faithfulness to Homeworld | PASS | No engine/gameplay changes at all |
| II. Layered, non-invasive | PASS | Pure `tools/` addition; engine untouched |
| III. Test-first | PASS | Unit tests with fake LLM + fake sidecar; live-match validation in quickstart |
| IV. Determinism | PASS | Peers read same snapshots; single order stream; stale-artifact rule keeps prompts bounded |
| V. Simplicity (YAGNI) | PASS | No consensus/voting/UI beyond spec; roles as specced |
| VI. Single Command Binding | PASS | Commander sole binder (FR-004); peers never call order tools (FR-010 defense in depth) |

## Project Structure

### Documentation (this feature)

```text
specs/001-agent-multi-llm-fleet/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   └── agent-multi.md   # artifact-file + role contract, CLI contract
└── tasks.md             # tasks step, NOT created here
```

### Source Code (repository root)

```text
tools/
├── agent_multi.py       # NEW: shared machinery (artifact store, peer loops
│                        #     base, role prompts, LLM client re-export)
├── agent-multi.py       # NEW: launcher CLI — threads commander + peers
└── tests/
    └── test_agent_multi.py   # NEW: unit tests (fake LLM + fake sidecar)
```

**Structure Decision**: one new shared module + thin launcher + one test file;
heaviest code (LLM/MCP clients, snapshot compression, order guard, nags)
is imported from `tools/agent.py` instead of duplicated.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| (none) | | |