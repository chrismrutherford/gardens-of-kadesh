# Implementation Plan: Full-Coverage AIPlay MCP

**Feature**: `003-full-coverage-aiplay-mcp-engine-i-o-full` | **Date**: 2026-10-21 | **Spec**: spec.md

**Input**: Feature specification from `specs/003-full-coverage-aiplay-mcp-engine-i-o-full/spec.md`

## Summary

Extend the existing MCP sidecar (`tools/aiplay_mcp.py`, HTTP `:8302` over the
engine's UDS line protocol) from a 22-tool hand mapping to a complete,
schema-driven surface: every `aiSession.*`/`state.*`/`order.*`/`time.*`/
`game.save` line method, every clWrap funnel (21 entry points — the 6 missing
ones gain aiplay order verbs + backend dispatch), all 9 error codes, all 7
event types, and the full snapshot schema — with one encoder/decoder module as
the single schema source of truth so opencode (JSON over HTTP) and the agent
fleet consume identical data. `order.select` stays a documented no-op;
alliance/hyperspace/RU-transfer carry documented MP-mode constraints.

## Technical Context

**Language/Version**: C (pure core `src/AIPlay`, engine backend) + Python 3.11 (sidecar + fleet)
**Primary Dependencies**: existing UDS line transport (`aiplay_transport.c`); Python sidecar uses only stdlib (http.server, json, socket) — no new deps introduced
**Storage**: none (stateless proxies; engine holds all state)
**Testing**: existing unittest suite `tools/tests/test_agent_multi.py` (35 tests) + new contract tests; C unit tests via `src/AIPlay/tests` (stub backend)
**Target Platform**: Linux, localhost HTTP + UDS
**Project Type**: engine feature + tool facade
**Performance Goals**: SC-006 — full snapshot of 500+ objects < 1 s/request
**Constraints**: localhost-only transport (constitution Additional Constraints); no second build system; orders through human funnels only (R-2); one binder (FR-004)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Faithfulness: new verbs call only clWrap* funnels humans already have → PASS
- II. Layered integration: backend additions live in `aiplay_engine.c` behind the existing vtable (`dispatchOrder` + verbs table); zero engine-internal twisting → PASS
- III. Test-first: every verb gains contract tests (stub backend + live fixtures) written with the code → PASS (gated in tasks)
- IV. Determinism: orders stay tick-boundary-queued; no wall-clock gameplay → PASS
- V. Simplicity/YAGNI: this is the "spec demands them" case (user explicitly requested full coverage); the surface is derived, not speculative → PASS
- VI. Single binding: no change to bind semantics; new funnels ride the same order queue → PASS

## Project Structure

### Documentation (this feature)

```text
specs/003-full-coverage-aiplay-mcp-engine-i-o-full/
├── plan.md              # this file
├── research.md          # Phase 0
├── data-model.md        # Phase 1
├── contracts/
│   └── mcp-surface.md   # Phase 1: complete tool↔wire↔funnel registry
├── quickstart.md        # Phase 1
└── tasks.md             # tasks step
```

### Source Code (repository root)

```text
src/AIPlay/
├── aiplay.h                       # + order verbs (kamikaze, parade, ruTransfer,
│                                  #   alliance, hyperspace), error codes unchanged
├── aiplay_orders.c                # + verb table entries + parse rules
├── aiplay_engine.c                # + backendDispatchOrder cases per new funnel
├── shiptype_names.inc             # unchanged (name registry, FR-009)
tools/
├── aiplay_mcp.py                  # sidecar: tools list regenerated from the schema
├── aiplay_protocol.py             # NEW: single-source encoder/decoder (schema registry);
│                                  #   sidecar + agent fleet import it (FR-008)
tools/tests/
├── test_aiplay_protocol.py        # NEW: round-trip + conformance tests (SC-002/003)
└── test_agent_multi.py            # unchanged behavior, green
specs/002.../contracts/ai-play.md  # original wire contract (reference)
```

**Structure Decision**: extend the two existing units (C backend verbs, python
sidecar tools) and add exactly one new module (`tools/aiplay_protocol.py`) as
the schema/encoder-decoder. No new services, targets, or transports.

## Research Notes (Phase 0)

Resolved from source (no open NEEDS CLARIFICATION):

- **New funnel signatures** (`src/Game/CommandWrap.c`):
  `clWrapSetKamikaze(layer, select)`; `clWrapSetMilitaryParade(layer, select)`;
  `clWrapRUTransfer(layer, fromIndex, toIndex, units, flags)`; `clWrapSetAlliance(type, p1, p2)` (player-index only, no selection); `clWrapMpHyperspace(layer, select, from, to)`; `clWrapBuildShip(layer, type, race, playerIndex, creator)` / `clWrapCreateShip(...)` same signature (CreateShip = instant materialize, BuildShip = queued; deterministic build path already used for AI builds).
- **Transport**: MCP is opencode-compatible today (HTTP JSON-RPC at
  `/mcp`); extension = larger tools list + one dispatch module, no protocol change.
- **Error surfacing**: sync validation already returns engine codes; new
  funnels get submit-time checks per FR-009 (e.g. alliance requires valid
  player indexes; ruTransfer requires sufficient RU) → fewer async surprises.
- **MP constraints**: `SetAlliance`/`MpHyperspace`/`RUTransfer` are
  network/funnel-backed in multiplayer and documented no-ops or clean errors
  in single-player — the mapping records the mode constraint (FR-015).

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| (none) | | New verbs are 1:1 funnel mappings; no patterns added |