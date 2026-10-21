# Implementation Plan: sidecar v2

**Feature**: `001-sidecar-v2` | **Date**: 2026-10-20 | **Spec**: [spec.md](spec.md)

## Summary

A declarative command map (one table: engine funnel → tool → typed schema
→ description → result shape) drives everything: the MCP tools/list, request
encoding/validation, response decoding to structured objects, the agents'
data dictionary, and tests. Plus the ~6 missing engine commands (kamikaze,
RU transfer, build-queue ops, research clear-lab, hyperspace, alliance) as
new engine-side verb glue ONLY.

## Technical Context

**Language/Version**: sidecar still Python 3 std-lib (aiplay_mcp.py rewrites
as aiplay_sidecar2.py? — keep the same file, add tools/aiplay_map.py +
tools/aiplay_codec.py); engine glue C (aiplay_engine.c + aiplay_orders.c
verb table)

**Primary Dependencies**: MCP 2025-06-18 structuredContent; engine funnel: clWrapSetKamikaze /
clWrapRUTransfer / clWrapDeterministicBuild(CMD_PAUSE|UNPAUSE|CANCEL) /
clWrapResearch(RESEARCH_SUBCOMMAND_STOP) / clWrapMpHyperspace / clWrapSetAlliance

**Testing**: schema round-trip corpus (recorded engine JSON, incl. the
historic malformed samples); tools/list vs map equality; live doctrine
scenario

**Performance Goals**: encode/decode < 1 ms per call; sidecar latency
unchanged

**Constraints**: no engine gameplay semantics; single writer, localhost-only

## Constitution Check

| Principle | Status | Notes |
|---|---|---|
| I. Faithfulness | PASS | kamikaze/transfer/etc are EXISTING human commands |
| II. Layered, non-invasive | PASS | sidecar tools + aiplay_engine glue only |
| III. Test-first | PASS | codec corpus + map-equality tests first |
| IV. Determinism | PASS | typed codecs remove string-guessing nondeterminism |
| V. Simplicity | PASS | map = data; no codegen frameworks |
| VI. Single Command Binding | PASS | new verbs ride the commander's session; peers still read-only |

## Engine-touch surface (II justification)

Small: aiplay_engine.c gains verb cases (kamikaze/ru-transfer/build-ops/
research-stop/hyperspace/alliance → clWrap*), aiplay.h verb enum + the
order parse (aiplay_orders.c). Justified: these are the engine's own
funnels; no alternative path exists for the sidecar layer.

## Module map

```text
tools/
├── aiplay_map.py     # NEW: THE command map (data): every tool row
├── aiplay_codec.py   # NEW: encode(req) + decode(resp) + tolerant parsing
├── aiplay_mcp.py     # rebuilt on the map (same port/URL, structuredContent)
├── agent_tools.py    # generated FROM the map (single source of truth)
└── tests/test_sidecar_v2.py   # corpus + map equality + validation tests

src/AIPlay/
├── aiplay.h          # + verbs
├── aiplay_orders.c   # + verb names in the table
└── aiplay_engine.c   # + verb cases → clWrap* funnels
```

## The command map (REVIEW TARGET — full table)

| Tool | Engine funnel | Args (typed) | Result shape |
|---|---|---|---|
| bind_session | aiSession.bind | playerIndex int≥0 | {playerIndex, sessionId, mapName} |
| game_status | aiSession.status | – | status object (running/paused/tick/bound/winner) |
| observe | state.snapshot | ownerFilter int?, wantShips/wantResources bool? | parsed: players/ships[]/resources[]/buildQueue[]/research[]/techTree |
| get_events | state.events | count int? (default 8) | events[{type,tick,id,aux,owner}] |
| order_move / attack / guard / dock / harvest / special / halt | order.<verb> | ships: int[] (yours), target: int (enemy/resource/dock id) or coord [x,y,z] for move | {queued, orderId} or {detail} |
| order_build | order.build | ships? builder int[], type enum(hulls), quantity 1..32, id? | {queued, orderId, cost, ticks} |
| order_research | order.research + START | tech enum(31 techs) | {queued, orderId, timeLeft} |
| research_clear_lab | RESEARCH STOP | labIndex int 0..5 | {cleared} |
| build_pause / build_resume / build_cancel | DeterministicBuild PAUSE/UNPAUSE/CANCEL | ships=[builder id], type enum(hulls) | {op} |
| order_formation | order.formation | ships, formation enum(delta,delta3d,broad,claw,wall,sphere) | {orderId} |
| order_tactics | order.tactics | ships, tactics enum(evasive,neutral,aggressive) | {orderId} |
| order_launch / order_autolaunch | launch / autolaunch | ships=[carrier/ms], onOff enum(on,off) for autolaunch | {launched:n} / {set} |
| order_scuttle | order.scuttle | ships (yours; permanent!) | {orderId} |
| order_kamikaze | SetKamikaze | ships (yours; sacrificial — piecemeal strikes vs collectors) | {orderId} |
| ru_transfer | RUTransfer | from int, to int, amount 1..10⁶, flags enum(gift) | {transferred} |
| order_hyperspace | MpHyperspace | ships, to [x,y,z] (cost/RU check engine-side) | {orderId, cost} |
| set_alliance | SetAlliance | type enum(per-player), newalliance bitmask | {set} (MP policy gate) |
| pause / resume / step | time.* | step: ticks 1..500 (paused only) | {paused}/{stepped} |
| save_game | game.save | name (auto epoch default) | {saved} |
| queue_done / queue_fail | (fleet pseudo) | q enum(4 roles), id int? | {marked} |

Descriptions live in the map rows (doctrine-informed, ≥1 when/constraint
clause each); the data dictionary + tools/list generate from it.

## Codec design (FR-003/004)

- encode(tool, args): schema walk → enums validated/clamped, ints ranged,
  required params enforced → engine wire JSON; client errors typed
  {code:"E_BAD_REQUEST",reason, hint} — the LLM reads the hint.
- decode(engine_reply): JSON parse (tolerant: strips the historic
  trailing-comma + unterminated-array classes with a repair pass,
  recording repairs) → normalized dicts → structuredContent.
- decode failure → {code:"E_PARSE", excerpt≤512, repairable:bool}.
- Round-trip corpus: every recorded engine response variant ships in
  tests/test_sidecar_v2.py.

## Tasks sketch

T001 aiplay_map.py (the table) → T002 aiplay_codec.py + corpus tests →
T003 rebuild aiplay_mcp.py on the map (structuredContent) → T004 engine
verbs + glue (C) → T005 agent_tools.py generation + agent/agent_multi
consumption → T006 doctrine-informed descriptions → T007 converge
(live: kamikaze + build-pause + RU-transfer scenarios).

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| engine glue (II touch) | no other path for kamikaze/transfer/etc | text-parsing the UI: impossible headless |