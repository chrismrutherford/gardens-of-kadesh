# Verification Report: AI Plays the Game While the User Spectates

**Feature**: 002-ai-plays-the-game-via-a-spectating-ui
**Date**: 2026-10-18
**Method**: unit suite + live in-match scripting per quickstart.md scenarios

## What was run

1. `meson compile -C build && meson test -C build aiplay-tests`
   → **77 checks, 0 failures** (json/session/snapshot/transport suites;
   whole-response JSON validity hardened after envelope-brace/screenshot bugs).
2. `python3 tools/tests/test_aiplay_mcp.py` → 3/3 OK (sidecar relays, schemas).
3. Live match: `./build/homeworld /aiPlay /aiPlayLoad "01 - Kharak System"`
   (auto-load savegame, intro skipped), control via local UDS + MCP sidecar
   (`http://127.0.0.1:8010/mcp`, 17 tools listed).

## Spec criteria, evidence, verdicts

| Criterion | Evidence | Verdict |
|---|---|---|
| FR-001 snapshot data | live snapshot: 9 ships (Mothership, Interceptors, CryoTray, TargetDrones), 29 resources, per-player RUs/alive, matchStatus | PASS (buildQueue/research sections pending — T036) |
| FR-002 paused determinism | paused → two `observe` MCP calls byte-identical (live) + unit test | PASS |
| FR-003 lifecycle events | `state.events` served bindChanged/matchStarted/shipDestroyed/orderApplied (live) | PASS |
| FR-004 one bound side | bind validation unit-tested (one player, no rebind mismatch); live bind player 0 | PASS |
| FR-005 command vocabulary | move/guard/dock/harvest/special/halt/select live-verified via engine funnel; build/research/formation dispatched via clWrapBuildShip/clWrapResearch(StrToTechType)/clWrapFormation — live run pending (T037) | PARTIAL |
| FR-006 invalid orders rejected | live: unknown id → `E_BAD_ID`; pre-match → gating; unaffordable build → `E_UNAFFORDABLE` (unit) | PASS |
| FR-007 order order preserved | queue FIFO + drain at tick boundary (unit tests, whole order applied within one tick) | PASS |
| FR-008 normal visible session | live game rendered/ran through the whole session with the layer enabled; no second window; layer inert without flag (vanilla run verified separately) | PASS |
| FR-009 spectator camera can't affect state | enforced by design (camera not read anywhere in layer) — explicit live check pending (T039) | PARTIAL (design-enforced) |
| FR-010 disconnect survives | transport closes clients cleanly; reconnect path unit-covered — live disconnect test pending (T038) | PARTIAL |
| FR-011 pause/resume/step | live: pause ⇒ byte-identical snapshots; step 2 ticks ⇒ tick 4686; resume OK | PASS |
| FR-012 real-time default | layer never pauses unless asked; pause optional (live) | PASS |
| FR-013 match-over gating | `E_MATCH_OVER` before match (live) + matchOver transition (unit) | PASS |
| FR-014 local-only transport | AF_UNIX socket chmod 0600; MCP binds 127.0.0.1 only; no TCP listener in code | PASS |
| FR-015 no gameplay semantics change | every order goes through the engine's own clWrap* funnel; no stat/tactic edits | PASS |
| SC-001 full skirmish spectated start-to-victory | match ran ~10+ min with layer active; full victory run pending agent/soak (T040) | PARTIAL |
| SC-002 snapshot ⇔ screen agreement | snapshot fields sourced from the same engine lists the renderer uses; positions live-checked | PASS |
| SC-003 order → screen ≤1s | order queued→applied within one tick; 603 world units of movement observed ~6s later; applied at next tick | PASS |
| SC-004 camera delta zero | by construction; explicit test pending (T039) | PARTIAL |
| SC-005 coherent pause | byte-identical paused snapshots (live + unit) | PASS |

## Bugs found & fixed during verification

1. Snapshot array entries lacked separators/opening braces → invalid JSON over the
   wire (fixed; whole-response JSON parse now a permanent unit check).
2. bind/order/step responses missing the result object's closing brace (envelope
   validity now checked in tests).
3. Stub test backend used implicit declarations → float/double ABI mismatch
   corrupting test data (header prototypes added).
4. `/disableAVI` did not skip in-game animatics → now returns early from
   `animAviPlay` (user-requested test-loop speed-up).

## Remaining work

Appended as Phase 8 convergence tasks T036–T040 (see tasks.md). Non-blocking for
the P1 story: MVP (spectate a live AI-driven session) is demonstrable today.