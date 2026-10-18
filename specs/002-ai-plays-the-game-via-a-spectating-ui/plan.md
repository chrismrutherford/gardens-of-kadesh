# Implementation Plan: AI Plays + Spectating UI

**Feature**: `002-ai-plays-the-game-via-a-spectating-ui` | **Date**: 2026-10-18 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/002-ai-plays-the-game-via-a-spectating-ui/spec.md`

## Summary

Enable an external AI agent to play one side of a **rendered** (spectated) GoK skirmish by
injecting orders through the engine's own command funnel (`clWrap*` → `clProcess`), serving the
agent state snapshots built from the engine's live lists over a local-only transport, while the
game window, HUD, camera and rendering remain entirely normal for the watching human. Pause/step
reuse `universePause` (+ `opPauseOrders` allowance) so the AI can think without the spectator
losing a live, coherent view.

## Technical Context

**Language/Version**: C (gnu11) — existing toolchain; sidecar MCP server may be C or Python
( decided in tasks; sidecar may be a thin Python script under `tools/` per constitution "no
second build system for the engine" — the sidecar itself is a tool, not engine code)
**Primary Dependencies**: SDL2 (existing), OpenSSL not needed; Unix domain sockets (libc);
Python3 + std lib for MCP sidecar (no new vendored deps)
**Storage**: none (event log & id maps are in-memory session state)
**Testing**: unit tests compiled into the meson build (`meson test`) for the layer's pure parts
(id map, order validation, snapshot serialization); scripted soak for live sessions
**Target Platform**: Linux first (constitution) — X11/SDL2 desktop
**Project Type**: desktop game + local AI IPC layer (in-engine) + stdio MCP sidecar (tools/)
**Performance Goals**: snapshot serialize < 5 ms for 800 ships; order dispatch < 1 ms; zero
dropped frames attributable to the layer (spectator SC-003 ≤ 1 s order→screen)
**Constraints**: no TCP (constitution: local IPC only); no gameplay semantic changes (FR-015);
build inside existing meson; `-Werror` clean
**Scale/Scope**: skirmish fleets ≤ ~800 ships; single local AI agent; 1 bound player/session

## Constitution Check (gate: pass required pre-research; re-checked post-design)

| Principle | Verdict | Evidence |
|---|---|---|
| I. Faithfulness to Homeworld | PASS | AI orders = same `clWrap*` calls as human UI (research R-2); FR-015 |
| II. Layered, non-invasive | PASS | new `src/AIPlay/` dir + `tools/` sidecar; engine touch = 1 loop hook + 1 tick hook, justified: no alternative way to run code inside the task system (R-1, R-5) |
| III. Test-first | PASS | pure-logic module split (id map, validation, serializer) unit-tested; live-session behavior via scripted verification in converge |
| IV. Determinism | PASS | orders processed only at tick boundary; `universePause` freezes tick counter ⇒ stable snapshots (R-3) |
| V. Simplicity (YAGNI) | PASS | no replays/training/Tournament modes; UI = spectator guarantees only; MCP tool surface = the contract in contracts/ai-play.md |

Post-design re-check: **PASS** — no gate violations recorded in Complexity Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/002-ai-plays-the-game-via-a-spectating-ui/
├── spec.md            # requirements (specify step)
├── plan.md            # this file
├── research.md        # engine evidence & decisions (R-1..R-7)
├── data-model.md      # session/order/snapshot entities
├── contracts/ai-play.md  # transport + tool contract
├── quickstart.md      # end-to-end validation guide
├── checklists/requirements.md
└── tasks.md           # (tasks step, later)
```

### Source Code (repository root)

```text
src/AIPlay/                     # NEW: the in-engine AI-play layer (compiled into `homeworld`)
├── aiplay.h / aiplay.c         #   session lifecycle, loop hook, id map, event log
├── aiplay_orders.c             #   order validation + clWrap* dispatch (the ONLY place clWrap is called)
├── aiplay_snapshot.c           #   snapshot builder (walks ShipList/ResourceList/players)
├── aiplay_transport.c          #   non-blocking UDS listener; JSON framing
└── tests/                      #   unit tests (meson test target `aiplay-tests`)
tools/aiplay-mcp.py             # NEW: HTTP MCP sidecar (binds 127.0.0.1, Streamable HTTP MCP
                                #   binding; relays tool calls to the game over local UDS)
src/SDL/main.c                  # MODIFIED: +2 lines — init/shutdown + per-frame poll hook
src/Game/UnivUpdate.c           # MODIFIED: +1 line — tick-boundary drain hook before aiplayerUpdateAll
meson.build / src/meson.build   # MODIFIED: include src/AIPlay, aiplay-tests target
```

**Structure Decision**: single existing executable; layer lives in one new directory so the
engine touch surface stays two one-line hooks (constitution II). No separate game binary; the
feature is enabled by a command-line switch (`--ai-play[=socketpath]`), default off ⇒ zero
behavior change for normal play.

## Design Notes (key mechanisms)

1. **Order path reuse** (FR-005, FR-006): `aiplay_orders.c` validates an `aiOrderRequest`, then
   calls the identical `clWrap*` functions the UI uses (research R-2 table). Rejection happens
   in the layer using cheap pre-checks (ownership/affordability/existence) AND relies on the
   engine's own validation; engine-side `clCommandMessage` errors are surfaced as `orderRejected`
   events. No new order semantics.
2. **Tick-boundary discipline** (FR-002/FR-007): requests are queue-only; the drain hook runs at
   the very start of `univUpdate()` (before `aiplayerUpdateAll`, `UnivUpdate.c:7512`) so a queued
   order set applies atomically within one deterministic tick; snapshots are taken in the same
   slot when paused (stable bytes) or at tick end between `clProcess` and render.
3. **AI player binding** (FR-004): `aiSession.bind` clears `universe.players[i].aiPlayer` so the
   built-in CPU module skips it (research R-4) — no stat/tactic changes anywhere.
4. **Pause/step** (FR-011/FR-012): `time.pause`/`time.resume` sets the same `universePause` the
   ESC menu uses; with `opPauseOrders` on, orders queue while paused (engine-approved loophole,
   R-3). `time.step` performs N bounded `univUpdate` invocations from the paused coroutine point
   (implementation: reuses the task system's tick counter, no thread).
5. **Transport** (FR-014, constitution security): one non-blocking Unix-socket listener polled
   from the existing per-frame loop hook (`main.c`); JSON per contracts/ai-play.md; the MCP
   sidecar is optional (raw socket usable for tests).
6. **Spectator invariants** (FR-008/FR-009): layer never renders, never touches camera or
   `selSelected` (orders address ships by id, not selection), never blocks a frame.

## Complexity Tracking

> No constitution violations require justification.