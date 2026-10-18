# Quickstart: AI Plays + Spectating UI

End-to-end validation scenarios for feature 002. Prereqs: built assets in `$ASSETS_FOLDER`
(see `documentation/community/installation-manual.md`), `meson setup build && meson compile -C build`.

## Scenario A — Spectated AI skirmish (US-1..3)

```sh
meson compile -C build
HW_Data=$ASSETS_FOLDER ./build/homeworld --ai-play=/tmp/hw-ai.sock
```
1. Through the normal UI, start a skirmish (player 0 = Kushan, CPU opponent).
2. In a second terminal, drive the layer directly:
   ```sh
   python3 tools/aiplay-mcp.py --socket /tmp/hw-ai.sock            # MCP server (stdio)
   # or raw test client during converge:
   python3 tools/aiplay-rawtest.py bind 0 && ... state.snapshot ...
   ```
3. Assert through the window: the AI-ordered ships visibly move/fight within ~1s
   (SC-003); HUD shows player 0 RUs building correctly; no crashes.

## Scenario B — Pause coherence (US-4, SC-005)

1. `time.pause` → take `state.snapshot` twice → byte-identical (diff).
2. HUD/screen shows paused world; render continues; ships frozen.
3. `order.move` a fighter while paused → `orderApplied` event after `time.resume`,
   ship moves on screen from the same position it was observed at.

## Scenario C — Contract invariants (FR-006, FR-007, FR-013)

1. `order.move` with unknown id 999999 → `E_BAD_ID`, re-snapshot byte-identical.
2. Burst-submit 50 valid orders → all appear in `state.events` in submission order.
3. Play to victory (or use a small test map) → `matchOver` event; subsequent orders →
   `E_MATCH_OVER`; `state.snapshot` still served; window stays open.

## Scenario D — Normal-play regression (FR-008, FR-015)

1. Launch WITHOUT `--ai-play` → behavior identical to vanilla build (this is the default).
2. Launch with `--ai-play` but no agent connected → game fully playable/deterministic; CPU
   opponent behaves per its normal difficulty.

Unit-level proof (pre-live): `meson test -C build aiplay-tests`.