# Tasks: AI Plays the Game While the User Spectates

**Input**: Design documents from `/specs/002-ai-plays-the-game-via-a-spectating-ui/`

**Prerequisites**: plan.md (structure + 2 engine hooks), spec.md (US1–US4), research.md
(engine evidence R-1..R-7), data-model.md (entities), contracts/ai-play.md (UDS JSON methods;
MCP sidecar = HTTP on 127.0.0.1 per user decision), quickstart.md (validation scenarios).

**Tests**: Requested (constitution III is non-negotiable): every implementation task that adds
pure logic ships with unit tests written first; live-session behavior is verified per
quickstart.md scenarios in converge.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1–US4)
- Exact file paths included in every description

## Phase 1: Setup (Shared Infrastructure)

- [X] T001 Create `src/AIPlay/` with empty module skeletons per plan.md structure:
      `aiplay.h`, `aiplay.c`, `aiplay_orders.c`, `aiplay_snapshot.c`,
      `aiplay_transport.c`, `tests/` subdir
- [X] T002 Add `src/AIPlay` to `src/meson.build` compilation list and register
      `aiplay-tests` test target in the meson build (no engine behavior change yet;
      `meson compile -C build && meson test -C build aiplay-tests` green)
- [X] T003 [P] Add `--ai-play[=socketpath]` command-line parsing in `src/SDL/main.c`
      (default socket path `~/.homeworld/aiplay.sock`; default state disabled so vanilla
      behavior is untouched) and pass the flag to `aiplayInit`/`aiplayShutdown` hooks
- [X] T004 [P] Write `tools/aiplay_rawtest.py` raw UDS test client skeleton
      (send/receive JSON-RPC frames, print responses) for converge evidence

## Phase 2: Foundational (Blocking Prerequisites)

- [X] T005 Implement session lifecycle in `src/AIPlay/aiplay.c` per data-model.md:
      `aiPlaySession` singleton (state enum `disabled→lobby→playing→matchOver`,
      `boundPlayerIndex`, `aiPlayerNeutralized`, orderQueue ring buffer, event log),
      with validation — bind requires `boundPlayerIndex < universe.numPlayers`,
      no rebind mid-match, exactly one bound player (FR-004)
- [X] T006 Implement the stable id map (`Ship* → uint64`) and `uint64` id
      generation/eviction (entries evicted on ship death; dead ids must report
      `E_BAD_ID` afterwards) in `src/AIPlay/aiplay.c` — pure logic, unit-testable
- [X] T007 [P] Add engine hooks: per-frame `aiplayFramePoll()` in the `utyTasksDispatch`
      call region of `src/SDL/main.c` (main loop after event pump, ~main.c:2299) and
      tick-boundary `aiplayTickBegin()`/`aiplayTickEnd()` calls at the top of
      `univUpdate()` / just before `clProcess` site in `src/Game/UnivUpdate.c`
      (~:7512 area) — both guarded by the disabled state so they cost nothing when off
- [X] T008 Implement the non-blocking Unix-domain-socket transport in
      `src/AIPlay/aiplay_transport.c`: create/bind/listen, accept multiple sequential
      client connections (reconnect per FR-010), non-blocking read/write, JSON-RPC
      frame parse (per contracts/ai-play.md envelope), loopback-only guarantee
      (no TCP listener ever created); unit test the frame parser (pure) in
      `src/AIPlay/tests/test_transport.c`
- [X] T009 Implement AI-player binding mechanics: on bind set
      `universe.players[i].aiPlayer = NULL` (built-in CPU neutralized, research R-4)
      and record `mapName`, restore on unbind; unit test state transitions in
      `src/AIPlay/tests/test_session.c` (with engine types stubbed)
- [X] T010 Define response/event structs for all contract methods
      (`aiSession.bind`, `aiSession.status`, `state.snapshot`, `state.events`,
      `order.*`, `time.*`) in `src/AIPlay/aiplay.h` matching
      `specs/002-ai-plays-the-game-via-a-spectating-ui/contracts/ai-play.md` field names
      exactly; error codes `E_NO_SESSION`, `E_BAD_ID`, `E_UNAFFORDABLE`, `E_NO_BUILDER`,
      `E_MATCH_OVER`, `E_QUEUE_FULL`

**Checkpoint**: skeleton compiles, hooks are no-ops when disabled, frame parser and session
logic are unit-tested (`meson test -C build aiplay-tests` green).

---

## Phase 3: User Story 1 — Human watches a live game the AI is playing (P1) 🎯 MVP

**Goal**: A visible, normal game session that an AI can drive while the human spectates.

**Independent Test**: Start skirmish with `--ai-play`, connect via
`tools/aiplay_rawtest.py`, send one `order.move`, watch the AI's ship travel on screen
(spec acceptance 1) — plus disconnect mid-match without crash (acceptance 2).

### Tests for User Story 1 (constitution III)

- [X] T011 [P] [US1] Unit test id-map stability across simulated session in
      `src/AIPlay/tests/test_session.c` (ids stable while ship lives; `E_BAD_ID`
      after death eviction)
- [X] T012 [P] [US1] Unit test event log FIFO ordering in
      `src/AIPlay/tests/test_session.c` (submission order preserved, FR-007)

### Implementation for User Story 1

- [X] T013 [US1] Implement JSON serialization framework (compact, deterministic key order)
      in `src/AIPlay/aiplay_snapshot.c` — reusable writer used by snapshot + events
      (unit test: deterministic byte output in `src/AIPlay/tests/test_snapshot.c`)
- [X] T014 [US1] Wire `aiSession.bind` / `aiSession.status` request handling end-to-end
      in `src/AIPlay/aiplay.c` + transport dispatch (mapName from engine, paused flag
      from `universePause`, tick from `universe.univUpdateCounter`)
- [X] T015 [US1] Implement disconnect/reconnect handling per FR-010: connection loss
      never pauses or stops the sim; rebind re-attaches with regenerated ids and
      event `matchContinued`; handle mid-order-stream disconnect (queued orders still
      drain) — scripted verify per quickstart Scenario A step 3 and Scenario C step 3
- [X] T016 [US1] Verify spectating invariants per FR-008/FR-009 with a manual scripted
      run (quickstart Scenario D): vanilla-launch byte-identical behavior with `--ai-play`
      absent; spectator camera movement produces no snapshot delta beyond tick advance

**Checkpoint**: MVP demonstrable: spectate an AI-driven skirmish end-to-end per quickstart
Scenario A.

---

## Phase 4: User Story 2 — AI observes the complete live battlefield (P2)

**Goal**: Ground-truth snapshots agreeing with what the spectator sees.

**Independent Test**: Pause, take two snapshots → byte-identical; snapshot ships/resources
match HUD values (spec acceptance 1–3).

### Tests for User Story 2 (constitution III)

- [X] T017 [P] [US2] Unit test snapshot section filters (`sections`, `owner` params)
      in `src/AIPlay/tests/test_snapshot.c`
- [X] T018 [P] [US2] Unit test match-status computation (survivor/alliance + `gameIsRunning`,
      wintime path per research R-4) with stubbed universe in
      `src/AIPlay/tests/test_snapshot.c`

### Implementation for User Story 2

- [X] T019 [US2] Implement full `state.snapshot` builder in `src/AIPlay/aiplay_snapshot.c`
      per data-model.md Snapshot table: ships (id, owner, type, race, pos, vel, health,
      healthMax, currentOrder verb/target), resources, players (RU, alive, lost, isAI),
      buildQueue, research, matchStatus, tick/timeElapsed/paused
- [X] T020 [US2] Implement current-order extraction from `mainCommandLayer` CommandToDo
      entries for each ship (research R-6: order state lives in the command layer, not the
      Ship struct)
- [X] T021 [US2] Implement `state.events` with event types from contracts
      (`matchStarted`, `matchOver{winner,perPlayer}`, `shipDestroyed{id,owner,type,killer?}`),
      hooking ship death via layer-side detection (walk diff between ticks; no engine change)
- [X] T022 [US2] Implement paused-state snapshot stability guarantee: snapshot taken when
      `universePause==TRUE` reads within the tick-boundary slot; repeated call returns
      byte-identical JSON (verify scripted per quickstart Scenario B step 1)

**Checkpoint**: US1 + US2 = AI can watch what the human watches, faithfully.

---

## Phase 5: User Story 3 — AI issues the full command vocabulary (P2)

**Goal**: Orders through the same funnel humans use; visible results on screen.

**Independent Test**: Scripted move→harvest→build sequence; on-screen results match
snapshots; invalid order returns descriptive error with byte-identical re-snapshot
(spec acceptance 1–4, quickstart Scenario C step 1–2).

### Tests for User Story 3 (constitution III)

- [X] T023 [P] [US3] Unit test per-verb validation matrix (ship must belong to bound
      player; harvest requires collector; build requires build-capable vessel + RU
      affordability; unknown target ids → `E_BAD_ID`; dead player → `E_MATCH_OVER`)
      in `src/AIPlay/tests/test_orders.c`
- [X] T024 [P] [US3] Unit test order-queue overflow behaviour (`E_QUEUE_FULL` at ring
      capacity) and FIFO drain in `src/AIPlay/tests/test_orders.c`

### Implementation for User Story 3

- [X] T025 [US3] Implement `aiplay_orders.c` pre-validation + dispatch to the engine's
      `clWrap*` family (move/attack/guard/dock/harvest/special/halt/build/research/
      formation/select) using exactly the same entry points the human UI uses
      (research R-2 signature table); record `orderApplied{id}` / `orderRejected{id,reason}`
      in the event log — this file is the ONLY `clWrap*` caller in the layer
- [X] T026 [US3] Implement tick-boundary queue drain in the `aiplayTickBegin()` hook
      (`src/Game/UnivUpdate.c` site): drain happens before `aiplayerUpdateAll`, orders
      applied atomically within one deterministic tick (FR-002/FR-007); nothing runs
      mid-tick
- [X] T027 [US3] Implement afford/existence pre-checks against the bound player
      (`resourceUnits` vs build cost from `ShipStaticInfo`, builder present from ship
      types) with engine-side fallback errors surfaced as `orderRejected` events
- [X] T028 [US3] Implement match-over gating (FR-013): `gameIsRunning == FALSE` ⇒
      gameplay orders rejected with `E_MATCH_OVER`; observation methods keep working

**Checkpoint**: US1+US2+US3 = complete observe→act loop; full quickstart Scenario C passes.

---

## Phase 6: User Story 4 — Human-friendly pacing (P3)

**Goal**: AI pause/step that reads coherently to the spectator.

**Independent Test**: Pause → two snapshots identical, HUD shows paused state; resume
continues with orders intact (spec acceptance 1–2, quickstart Scenario B).

### Implementation for User Story 4

- [X] T029 [US4] Implement `time.pause` / `time.resume` / `time.step` by setting the
      engine's own `universePause` flag (and forcing the allow-orders-while-paused
      allowance `opPauseOrders` for bound-player orders only, research R-3); PAUSE key/
      ESC menu interplay must stay functional for the human
- [X] T030 [US4] Implement `time.step N`: bounded N-tick advance from the paused state
      via the task system (no threads, no turbo); unit test the step-count bookkeeping
      in `src/AIPlay/tests/test_session.c`
- [X] T031 [US4] Verify spectator-visible pause surface: HUD/menu pause indication shows
      during AI pause (scripted check, quickstart Scenario B step 2); ensure orders
      submitted while paused queue and process on resume (quickstart Scenario B step 3)

**Checkpoint**: All four user stories functional.

---

## Phase 7: MCP HTTP sidecar + Polish

- [X] T032 Implement `tools/aiplay_mcp.py`: HTTP MCP server bound to **127.0.0.1 only**
      (default port 8000, `--port`), standard MCP Streamable HTTP binding
      (`initialize`, `tools/list`, `tools/call`), relaying each tool call to the game's
      UDS endpoint per contracts/ai-play.md; tools: bind_session, game_status,
      observe, get_events, order_move, order_attack, order_guard, order_dock,
      order_harvest, order_special, order_halt, order_build, order_research,
      order_formation, pause, resume, step; `--http` is the default mode
- [X] T033 [P] Write `tools/aiplay_mcp.py` smoke tests (spin server, list tools, call
      observe against a stub UDS server) as `tools/tests/test_aiplay_mcp.py`
- [X] T034 [P] Update `documentation/community/installation-manual.md` with an
      "AI spectating mode" section (launch flag, MCP sidecar usage, localhost-only note)
      and the run instructions from quickstart.md
- [X] T035 Run full quickstart.md validation (Scenarios A–D) and record evidence in the
      converge step; `meson test -C build` plus formatter/lint (`./.clang-format` via
      clang-format on `src/AIPlay/`)

## Dependencies & Execution Order

### Phase Dependencies
- Setup (1) → Foundational (2) → US1 (3) → US2 (4) → US3 (5) → US4 (6) → MCP/Polish (7)
- US2/US3 are technically independent of each other once Foundational lands, but both
  build on US1's session/transport; follow priority order.
- The MCP sidecar (T032) depends only on the contract (can be written against a stub UDS
  server in parallel with engine work).

### Parallel Opportunities
- T003, T004 (Setup), T011/T012 (US1 tests), T017/T018 (US2 tests), T023/T024 (US3 tests),
  T033, T034.
- Sidecar (T032/T033) parallel to engine phases via stub server.

## Implementation Strategy

- MVP = Phases 1–3 (US1): visible spectated session driven by raw UDS client.
- Then observation (US2), then action (US3) ⇒ complete playable loop, then pacing (US4),
  then the HTTP MCP surface (T032) that this feature's user will attach an agent to.
- Every phase ends with `meson test -C build aiplay-tests` green + scripted quickstart
  check before the next phase begins (constitution III).

## Notes

- Engine modifications are exactly: `--ai-play` argv handling + 2 hook call sites
  (T003, T007) and build wiring (T002) — keep it that way (constitution II).
- `src/AIPlay/aiplay_orders.c` is the only place in the layer allowed to call `clWrap*`.
- All snapshot JSON key names MUST match contracts/ai-play.md verbatim.