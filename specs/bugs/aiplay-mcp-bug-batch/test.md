# Bug Verification: aiplay MCP order pipeline + snapshot bug batch

- **Slug**: aiplay-mcp-bug-batch
- **Tested**: 2026-10-21
- **Assessment**: ./assessment.md
- **Fix**: ./fix.md
- **Result**: verified (code-level; live-match checks pending — engine killed for rebuild)

## Summary

No source reproduces any of the four fixed bugs: the encoder now ships
`"on"`/`"off"` (never JSON booleans), shipless `order_build` passes the
protocol layer, `COMMAND_NULL` no longer masquerades as `"order?"`, and
order-target retouches no longer poison the id-map owner. All suites green and
the engine relinks clean. Live in-engine confirmation (autolaunch queuing,
`order?`-free re-tasking, asteroid owners) requires a fresh match and is
pending — the running match was killed to rebuild the binary.

## Checks Performed

| Check | Command / Action | Result | Notes |
|-------|------------------|--------|-------|
| New autolaunch/regression tests (Bug A wire) | `python3 tools/tests/test_aiplay_protocol.py` | pass | includes `test_onoff_stringified_on_wire` (autolaunch/alliance/kamikaze/parade booleans + string/number forms) |
| Shipless build through protocol (Bug B) | same suite | pass | `test_build_shipless_waits_for_engine_default` |
| Protocol suite unchanged behavior | same suite | pass | 36/36 (round-trip SC002, validator, coverage SC001) |
| Agent toolchain regression | `python3 tools/tests/test_agent_multi.py` | pass | 35/35 |
| MCP server schema/regression | `python3 tools/tests/test_aiplay_mcp.py` | pass | 3/3 (stale pre-existing `test_tool_schema` restored to dict format) |
| Engine C unit suite | `meson test -C build` | pass | aiplay-tests OK (compiles new `aiplay_orders.c`/`aiplay_engine.c`) |
| Full engine relink | `ninja -C build` | pass | clean, no warnings in output |
| Live in-engine repro of Bug A | launch match, `autolaunch {onOff: true}` | skipped | engine process killed for rebuild; see checklist below |
| Live repro of Bug C/D | reload autosave, observe owners + order verbs | skipped | same reason |

## Output Excerpts

```
python3 tools/tests/test_aiplay_protocol.py  → Ran 36 tests in 0.004s  OK
python3 tools/tests/test_agent_multi.py      → Ran 35 tests in 0.027s  OK
python3 tools/tests/test_aiplay_mcp.py       → Ran 3 tests    OK
ninja -C build                               → [6/6] Linking target homeworld
meson test -C build                          → Ok: 1  Fail: 0  (aiplay-tests)
```

## Live-Match Verification Checklist (pending)

Run after relaunching the engine
(`build/homeworld /aiPlay /aiPlayLoadMP ai-autosave-1792603832` — note ids
remap after load, so rebind player 0 and re-observe before ordering):

1. `autolaunch {onOff: true}` → expect `queued: true` (no E_BAD_REQUEST);
   confirm a subsequent fighter build deploys without manual `order_launch`.
2. Attack/move a ship, let its order consume (target docks/dies) → snapshot
   must show `null`/`idle` or the next real order — never `"order?"` stuck.
3. While an enemy collector harvests and any ship attacks anything:
   asteroid `owner` stays `-1`; enemy ships keep their real owner index; an
   `owner=0` observe pull contains no enemy-owned ships.
4. `order_build {type: <hull>}` without `ships` → accepted with the Mothership
   as builder (`E_NO_BUILDER` only if the mothership is genuinely absent).

## Residual Risks

- Bug E (docked fighters destroyed, aux 0) is deliberately unfixed — needs a
  live repro to separate user-initiated launch from an engine launch-collision.
- `aiplayIdTouchEx` still overwrites owner on touch (only the retouch sites
  were fixed); if any future call site passes a wrong owner the same class
  returns. Consider an owner-overwrite-only-from-syncShips hardening later.
- The ~+10k/3-min RU windfall (both players) remains unexplained.

## Recommendation

Close bugs A–D as verified at code level; keep bug E open with the live
repro plan. On relaunch, run the pending checklist (5 minutes) and only then
treat the batch as end-to-end verified.