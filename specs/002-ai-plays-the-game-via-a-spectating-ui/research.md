# Research: AI Plays + Spectating UI (Feature 002)

**Date**: 2026-10-18. Evidence gathered by deep code inspection (file:line refs included).

## R-1: How the simulation ticks (and how to not break rendering)

**Decision**: Integrate the AI-play layer into the existing cooperative task system; never spawn
a competing thread that touches world state.

**Findings**:
- `main()` (`src/SDL/main.c:2135`) loops on SDL events then `utyTasksDispatch()` (`main.c:2299`).
  `utyTasksDispatch` (`src/SDL/utility.c:4671`) converts `SDL_GetTicks()` into ticks and runs
  `taskExecuteAllPending(ticks)` (`src/Game/Task.c:280`) over coroutines.
- Simulation is `universeUpdateTask` (`DEFINE_TASK`, `src/Game/Universe.c:3786`) → `univUpdate()`
  (`src/Game/UnivUpdate.c:7271`); rendering is `rndRenderTask` (`src/SDL/render.c:3781`). Both are
  cooperative tasks; only one runs at a time.
- Sim rate: `UNIVERSE_UPDATE_RATE = 16 << UNIVERSE_UPDATE_RATE_SHIFT` (`src/Game/Universe.h:44–49`).

**Rationale**: A single-threaded design means snapshots and order injection can read/write the
world directly with zero locking, and rendering cannot be blocked by the AI (frame renderer has
its own task slot).

**Alternatives**: worker thread + mutex (rejected: racy against coroutine state, violates
constitution II determinism); separate headless process fed by replays (rejected: cannot produce
the live spectated window this spec requires).

## R-2: Order injection — the shared funnel

**Decision**: The AI issues orders exclusively through the `clWrap*` wrappers on
`universe.mainCommandLayer` — the same functions the human UI and the built-in CPU player use.

**Findings**:
- Human input: keyboard/mouse handlers call `clWrapDock`, `clWrapMove`, `clWrapCollectResource`,
  etc. (`src/SDL/mainrgn.c:545–2566`, `src/Game/Sensors.c:3318/3348`).
- Built-in CPU: `aiplayerUpdateAll()` (`src/Game/AIPlayer.c:828`) → `aifFleetCommand`
  (`src/Game/AIFleetMan.c:1252`) → also `clWrap*` (`src/Game/AIUtilities.c:4120–4293`).
- Pipeline: `clWrap*` (`src/Game/CommandWrap.c`) → `clSend*` (`src/Game/CommandNetwork.h:506–540`)
  → queued packets → `clProcess(&universe.mainCommandLayer)` (`src/Game/CommandLayer.c:8224`,
  called at `src/Game/UnivUpdate.c:7521`) applies them during `univUpdate`.
- Selection is global state (`selSelected`, `src/Game/Select.h:90`), not a networked command.

**Rationale**: Zero engine-semantics changes (constitution I): AI orders are byte-for-byte the
same events a human produces; validation, error feedback (`clCommandMessage`) and determinism
come free.

## R-3: Pause & time control seam

**Decision**: Use `universePause` for pause/resume + the documented `opPauseOrders` allowance for
orders-while-paused; expose stepping as "N paused ticks". No new time system.

**Findings**:
- `universePause` (`src/Game/Universe.c:104`, `src/Game/Universe.h:241`): `universeUpdateTask`
  early-outs (after refreshing render lists, so the world keeps rendering) —
  `src/Game/Universe.c:4010–4013`.
- Every `clWrap*` gate is `if (playPackets || (universePause && !opPauseOrders)) return;`
  (`src/Game/CommandWrap.c:91,130,170,…`). With `opPauseOrders` enabled, orders queue while
  paused and process on resume — exactly the AI "think then act" flow.
- ESC menu toggles pause (`src/SDL/mainrgn.c:1888`); the PAUSE key is registered but unused
  (`src/Game/Key.h:132`), so binding it (or the AI protocol) does not collide with gameplay.
- Snapshot stability trick: while `universePause == TRUE`, `univUpdateCounter` (`Universe.h:168`)
  does not advance ⇒ identical repeated observations (spec FR-002/SC-005).

## R-4: Disambiguating the AI player from the built-in CPU

**Decision**: The AI-play session binds exactly one `Player` (default player 0); for that player
the built-in `aiPlayer` module is neutralized (set `universe.players[N].aiPlayer = NULL` and skip
`aiplayerUpdateAll` for N), and all gameplay behavior comes from injected `clWrap*` orders.

**Findings**:
- `Player` (`src/Game/Universe.h:104–127`) carries `resourceUnits`, `playerState`
  (`PLAYER_ALIVE/PLAYER_DEAD`), `aiPlayer`, `researchinfo`, `Allies`, `PlayerMothership`.
- `aiplayerUpdateAll` skips players whose `aiPlayer` is NULL (`src/Game/AIPlayer.c:826–855`) and
  the whole module is bypassed under `playPackets` (lockstep MP), which does not matter here
  (skirmish, local only).
- Victory: no simple flag; `univUpdate` maintains survivor sets, `universe.wintime`/`quittime`
  (`src/Game/UnivUpdate.c:4140–4225`), `DeclarePlayerWin()` (~:4228; early-outs for
  `singlePlayerGame`), and the game-over path sets `gameIsRunning = FALSE` (`Globals.h:78`) with
  the `SP_Game_Over` screen. Match status for the AI = per-player `playerState` + alliance
  survivor computation + `gameIsRunning`.

## R-5: AI ↔ engine transport (local only)

**Decision**: JSON-RPC-ish request/response over **stdio** of a sidecar MCP server process that
talks to the game process over a local Unix domain socket; the game never opens a TCP port.

**Findings / constraints**:
- Engine is C with `-Werror`, sanitizers on by default (meson), no test infrastructure yet;
  `src/Game` is compiled straight into the `homeworld` executable (`meson.build`, `src/meson.build`).
- A C-side socket listener polled from `utyTasksDispatch` (non-blocking) is simple and keeps
  everything in-process; heavy parsing lives in the sidecar (constitution II: layering).
- Spec 001 (`specs/001-mcp-server-layer-for-ai-to-play-the-game`) defines observe/order/step
  MCP tools for headless mode; this feature reuses that contract and adds the visible-session
  mode. No MCP code exists in the tree yet — this feature's implementation includes it.

**Alternatives**: in-process stdio (rejected: stdin/stdout of the game window process is noisy
with logs); TCP localhost (rejected by constitution security constraint: network socket default).

## R-6: Snapshot data sources (what the AI observes)

**Decision**: Build snapshots by walking the engine's own lists at a tick boundary: `ShipList`,
`ResourceList` (`src/Game/Universe.h` LinkedLists), each `Player.resourceUnits`,
`researchinfo`/build queues from player state, match status via R-4. Ship identity = pointer-
stable `SpaceObj` with a generated 64-bit id map maintained by the layer.

**Findings**:
- `Ship` (`src/Game/SpaceObj.h:1220–1362`): position via `obj.posinfo`, `shiptype/shiprace`,
  `playerowner`, `staticinfo->health` statics, current order via the `mainCommandLayer`
  `CommandToDo` list, not the ship struct.
- Resources: `ResourceList`; player RU counters are `Player.resourceUnits`.

## R-7: Spectator guarantees

**Decision**: The AI layer adds nothing to the render path and never blocks it: order injection
and snapshot building happen inside the simulation task boundary; the render task is untouched.
One HUD addition is allowed: reuse the existing pause indicator (ESC-menu pause already shows the
system-modal menu; a lightweight "AI" indicator in HUD is spec'd in tasks, minimal touch).

**Findings**: `universePause` early-out keeps rendering running while paused (`Universe.c:3999–4013`);
turbo/render lists unaffected. Spectator camera input is wholly in `mainrgn.c`/`Sensors.c` camera
code and mutates only camera state ⇒ cannot affect simulation (spec FR-009 holds by construction).

## Constitution check result

All gates pass: no gameplay-semantics change (FR-015 via R-2), layering respected (R-1/R-5), no
network exposure (R-5), determinism via pause + lockstep-safe entry points (R-3), local-only
build in existing meson (new `src/AIPlay/` dir + one meson include).