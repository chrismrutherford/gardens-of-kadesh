# Implementation Plan: peer-authored priority instruction queue

**Feature**: `001-peer-instruction-queue` | **Date**: 2026-10-20 | **Spec**: [spec.md](spec.md)

## Summary

Peers (big models) gain a queue-editor contract: instead of only publishing
advisory artifacts, each peer may ADD / REMOVE / RE-ORDER instructions in a
shared priority instruction queue. The commander (4B executor) reads the
queue each round, executes instructions top-first (marking done / failed /
stalled), and falls back to its standing invariants when the queue is empty.
Everything else from the fleet feature stays as-is.

## Technical Context

**Language/Version**: Python 3 std-lib (existing tools/ pattern)

**Primary Dependencies**: threading/json/os (atomic os.replace); imports
LLM/MCP/compress_snapshot/guard_call from tools/agent.py (unchanged)

**Storage**: session-dir JSON artifacts (same convention as the fleet
feature): `instruction-queue.json` (current version-ordered queue),
`instruction-history.json` (append-trimmed edit log)

**Testing**: unittest in tools/tests/ (fake LLM + fake sidecar harness
already proven in test_agent_multi.py)

**Target Platform**: Linux (same box / LAN to LLM servers)

**Project Type**: companion CLI tooling (single process, 5 loops)

**Performance Goals**: editor edits applied/validated in microseconds;
commander queue read zero-cost per round; peer edit round-trip ≤ 1 peer
round

**Constraints**: only peers edit; only commander executes (const. VI);
illegal edits rejected with reason; local-only transport

**Scale/Scope**: queue cap 12 instructions; history ring 100 entries

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|---|---|---|
| I. Faithfulness to Homeworld | PASS | No engine changes; tooling only |
| II. Layered, non-invasive | PASS | tools/ only |
| III. Test-first | PASS | queue editor/executor unit tests; fake-model harness |
| IV. Determinism | PASS | single order stream; queue edits are deterministic ops with newest-wins |
| V. Simplicity (YAGNI) | PASS | three ops (add/remove/move), one queue, no subscriptions/multi-queue |
| VI. Single Command Binding | PASS | peers edit instructions (shared-state writes, same channel as artifacts), never engine calls; commander sole binder/orderer |

## Project Structure

```text
specs/001-peer-instruction-queue/
├── plan.md            # this file
├── research.md        # decisions (queue ops + conflict rules)
├── data-model.md      # instruction/queue/history schema
├── contracts/queue.md # editor op contract + executor contract
├── quickstart.md      # validation scenario
├── tasks.md           # tasks step
└── checklists/

tools/
├── agent_multi.py     # + InstructionQueue (validate/apply ops), peer editor
│                      #   prompts + editor loop wiring, commander queue read
└── tests/
    └── test_agent_multi.py   # + queue tests
```

**Structure Decision**: extend the existing modules; no new files except
docs — the queue is an artifact-store concern plus role prompts.

## Key design decisions (research digest)

- **Where edits happen**: peers already write one artifact per round. The
  queue editor is a NEW artifact: peers emit an `edits` payload;
  `InstructionQueue.apply(edits)` validates + applies in received order
  (newest-edit-wins per id) and writes the updated queue + history.
- **Executor consumption**: commander reads `instruction-queue.json`; if
  non-empty, its prompt shows the top instructions with state; it works
  the ACTIVE (topmost non-done) instruction first; standing invariants
  only fill empty-queue rounds. Progress notes appended per round.
- **Safety**: instruction text ≤ 200 chars; queue ≤ 12; history ≤ 100;
  unknown ops/duplicates/out-of-range priorities rejected with reason
  (written to history so editors adapt).

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| (none) | | |