# Bug Fix: aiplay MCP order pipeline + snapshot bug batch

- **Slug**: aiplay-mcp-bug-batch
- **Fixed**: 2026-10-21
- **Assessment**: ./assessment.md
- **Status**: applied (Bug E partial — attribution doc'd, engine collision path left untouched)

## Summary

Fixed the order-pipeline and snapshot bugs observed in the live MCP-commanded
match: autolaunch/alliance booleans now reach the engine as `"on"`/`"off"`
(python encode coercion + engine boolean fallback read), `order_build` without
`ships` now defaults the builder to the bound player's Mothership, orphaned
`COMMAND_NULL` command entries no longer surface as fake `"order?"` orders
(ships idle correctly and get re-tasked), order-target retouches no longer
poison the id map's `owner` field (hardcoded 0 → derived real owner), and the
snapshot-documentation contract now matches what the engine actually reports.

## Changes

| File | Change | Notes |
|------|--------|-------|
| `tools/aiplay_protocol.py` | modified | `encode_call()` now applies `on_off()` coercion for every schema rule with `"coerce": "on_off"` (autolaunch/kamikaze/parade/alliance `onOff`) after validation passes — wire carriers `"on"`/`"off"`, never booleans |
| `tools/tests/test_aiplay_protocol.py` | added tests | `test_onoff_stringified_on_wire` (regression: wire params must be string on/off), `test_build_shipless_waits_for_engine_default` (build w/o ships passes the protocol layer) |
| `tools/tests/test_aiplay_mcp.py` | modified (deviation) | stale `test_tool_schema` unpacking (`n for n,_,_ in TOOLS` → `t["name"]`); failed on clean HEAD too — pre-existing |
| `src/AIPlay/aiplay_orders.c` | modified | `parseOrder()`: for `onOff`, read `aiplayJson_bool` nodes directly (`true`→"on"/`false`→"off") and map `"true"`/`"false"` string forms too; `validateOrder()`: `aiplayVerb_build` exempt from the numShips check (engine defaults builder to `PlayerMothership` — that fallback already existed at `aiplay_engine.c:738`) |
| `src/AIPlay/aiplay_engine.c` | modified | `fillShipOrder()`: `COMMAND_NULL` → `continue` (skip opaque "order?" verdict; next real order/idle wins); `COMMAND_MP_HYPERSPACING` → `"hyperspace"`; attack/collect target retouches now use new `backendOwnerOf()` (ships → `playerowner->playerIndex`, asteroids → -1) instead of hardcoded 0 |
| `documentation/community/ai-snapshot-dictionary.md` | modified | owner field is ship-only (`asteroids always -1`), `order?` semantics rewritten (null-command orphans are skipped), `hyperspace` verb added, `shipDestroyed aux 0` = no killer attributed |
| `documentation/community/ai-playbook.md` | modified | income stat documented as NET bank delta (spends negative, map injections spike) — not harvest rate; verify harvesting via `order: harvest` verbs |
| `BUGS.md` | modified | fix-status header (fixed/open per bug) |

## Diff Highlights

```c
// aiplay_orders.c parseOrder — boolean onOff fallback
if (node >= 0 && doc->nodes[node].type == aiplayJson_bool)
{
    strcpy(out->name, doc->nodes[node].b ? "on" : "off");
}
```

```c
// aiplay_engine.c fillShipOrder — retouch with REAL owner
out->orderTargetId = aiplayIdTouch(target, backendOwnerOf(target));

// orphaned command entries no longer masquerade as an order
case COMMAND_NULL:
    continue;    // the ship's real order, if any, lives further down
```

```python
# aiplay_protocol.py encode_call — coerce at the encode path
for key, rule in _params_for(tool).items():
    if rule.get("coerce") == "on_off" and key in wire:
        wire[key] = on_off(wire[key])
```

## Tests Added or Updated

- `tools/tests/test_aiplay_protocol.py::TestRoundTripSC002::test_onoff_stringified_on_wire` — pins: `onOff` `True/False` (autolaunch), alliance, kamikaze, parade all serialize to `"on"`/`"off"` on the wire with no validation error.
- `tools/tests/test_aiplay_protocol.py::TestRoundTripSC002::test_build_shipless_waits_for_engine_default` — pins: shipless `order_build` passes the protocol layer (engine defaults to PlayerMothership).
- `tools/tests/test_aiplay_mcp.py::TestMcp::test_tool_schema` — restored to the dict tool-entry format (pre-existing failure).

## Local Verification

- `python3 tools/tests/test_aiplay_protocol.py` → 36/36 OK.
- `python3 tools/tests/test_agent_multi.py` → 35/35 OK.
- `python3 tools/tests/test_aiplay_mcp.py` → 3/3 OK (was 1 pre-existing fail on clean HEAD).
- `ninja -C build` → full engine relink clean (libaiplay.a + homeworld).
- `meson test -C build` → aiplay-tests OK.
- Engine live-verification deferred: match was killed for the rebuild; reload `/aiPlayLoadMP` autosave and confirm `autolaunch {onOff: true}` queues + no `order?` verbs on re-tasked ships.

## Deviations from Assessment

- Fixed the pre-existing stale `test_tool_schema` unpack in
  `tools/tests/test_aiplay_mcp.py` (outside the assessment's file list): it
  failed identically on a clean HEAD with an unpack error — trivially stale
  vs the TOOLS dict format introduced by commit 13ffbb37.
- Bug E: no engine code change (assessment's "preferred" was attribution
  documentation only for this batch). `shipDestroyed aux 0` semantics are now
  documented; the suspected launch-while-docked collision path remains an
  open investigation.

## Follow-ups

- Reload the autosaved match (`build/homeworld /aiPlay /aiPlayLoadMP
  ai-autosave-1792603832`), rebind, and verify: autolaunch queues cleanly,
  previously "order?" ships re-task, asteroid owners stay -1 when enemies
  harvest, and `owner=0` observe pulls contain no enemy ships.
- Investigate launch-while-docked collision death (bug E) with a live repro.
- The ~+10k/3-min RU windfall on both sides (assessment Bug F context) still
  unexplained — observe post-fix whether it ties to debris auto-collection.