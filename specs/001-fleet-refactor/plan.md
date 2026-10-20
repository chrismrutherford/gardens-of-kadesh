# Implementation Plan: fleet refactor

**Feature**: `001-fleet-refactor` | **Date**: 2026-10-20 | **Spec**: [spec.md](spec.md)

## Summary

Split the fleet into focused modules with explicit wiring (no env vars),
add JSONL round telemetry + a one-glance fleet-status tool, and rewrite all
model-facing text (prompts + tool descriptions) from gameplay.txt-derived
doctrine. No engine changes; single-agent mode preserved as a thin wrapper.

## Technical Context

**Language/Version**: Python 3 std-lib (unchanged)

**Testing**: unittest (existing suite + new tests for JSONL/status/doctrine/
tool descriptions); py_compile gates

**Performance Goals**: zero regression — commander round cadence unchanged;
JSONL write ≤ 1 ms; fleet-status < 1 s

**Constraints**: behavior-preserving refactor (FR-007); prompts are the
only intentional behavior change; all model-facing text in ONE file

## Constitution Check

| Principle | Status | Notes |
|---|---|---|
| I. Faithfulness | PASS | tools/ only; engine untouched |
| II. Layered, non-invasive | PASS | pure tooling refactor |
| III. Test-first | PASS | tests updated with the refactor, new tests per US |
| IV. Determinism | PASS | no timing/behavior changes beyond prompts |
| V. Simplicity | PASS | six modules, no plugin framework, JSONL not SQLite |
| VI. Single Command Binding | PASS | wiring only; binding/ordering rules untouched |

## Target module map (FR-003)

```text
tools/
├── agent_core.py       # LLM, MCP, PeerSidecar, http_json, result_text,
│                       # ensure_bound, LoopLogger (+JSONL round events)
├── agent_snapshot.py   # compress_snapshot, world_summary, order_detail,
│                       # ships_already_executing, guard_call
├── agent_prompts.py    # ALL model-facing text: DOCTRINE cards, SYSTEM,
│                       # EXECUTOR, PEER prompts, playbook+dictionary load
├── agent_rounds.py     # commander round pipeline (observe→merge→events→
│                       # dispatch→report); nags; autosave; dedupe hooks
├── agent_multi.py      # peers, ArtifactStore, InstructionQueue, merge
├── agent-multi.py      # launcher + config (unchanged UX)
├── agent.py            # thin single-agent CLI wrapper (same flags)
├── fleet-status.py     # NEW: one-glance health from JSONL/artifacts/queue
└── tests/test_agent_multi.py + test_fleet_refactor.py
```

**Wiring (FR-004)**: `FleetContext` dataclass: {store, queue, roles, opts,
logdir} — passed explicitly into the pipeline; `AIPLAY_MULTI_*` env vars
and argv injection deleted.

## Observability design (FR-001/002)

- `rounds-<role>.jsonl`, one line per round:
  `{"role","round","tick","at","latency_s","actions":[{"tool","args",
  "result"}],"error","queue_edits":{"accepted","rejected"}}`
- `fleet-status.py`: table of role | round | age | rate | last error |
  artifact freshness | queue (size + top instruction) | commander last 3
  actions; flags STALE > 60 s; exit 1 when no live session.

## Prompt redesign (FR-005, in agent_prompts.py — review target)

- `DOCTRINE` dict: economy, counters, priorities, kamikaze, scouting,
  just-in-time research, positioning (from research.md Part A)
- Role cards (each ≤ ~25 lines): EXECUTOR (invariants + doctrine targets +
  attack priority + scuttle option + queue contract), STRATEGIST, ANALYST,
  ECONOMIST, CRITIC, SYSTEM (single-agent)
- Every card references the snapshot data dictionary (already injected)

## Tool descriptions (FR-006, agent_tools.py — review target)

Each description: purpose + when-to-use + constraints, e.g.:
- order_harvest: "…target MUST be a resource id from `resources`…"
- order_scuttle: "kamikaze: piecemeal scouts vs enemy collectors…"
- queue_done/queue_fail: mark current directive complete/impossible

## Risks

- Import churn breaks tests → tests updated in the same commit, CI = the
  test run (no external CI configured)
- Prompt changes shift model behavior → doctrine markers tested; live-run
  comparison via JSONL (SC-005 baseline before/after)

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| (none) | | |