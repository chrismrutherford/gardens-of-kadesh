# Quickstart: Twin-Core LLM CPU Player (spec 004)

End-to-end validation scenarios proving the spec's user stories. Prerequisites
and layout: see contracts/driver-runtime.md; behaviour contracts:
contracts/twin-core-protocol.md. Unit suites run without a game or models;
live scenarios need the stack below.

## Prerequisites

- Game built (`meson compile -C build`), assets extracted (`output/game/`).
- MCP sidecar reachable (`./launch.sh cmr1 --port 8302` →
  `http://127.0.0.1:8302/mcp`).
- Model endpoints configured in the chosen config's `channels`: a small
  tool-calling model (S1) and a larger model (S2), both OpenAI-compatible
  HTTP; URLs local/LAN only.
- Unit tests: `python3 -m pytest tools/tests/twincore -q` → all green.

## Scenario A — S1-only match floor (US1, SC-001/SC-002)

```sh
./launch.sh cmr1 --port 8302 &
python3 tools/twincore/driver.py --config tools/twincore/configs/boot-default.json
# strategist.enabled = false in boot-default.json
```

Expect (record store: postgres `twincore_q.messages`, query via
`python3 tools/twincore/dbg.py --match <match_id>`):

1. `degradation: UNBOUND→BOUND_ACTIVE` on bind; exactly one binding owner.
2. Opening playbook: collectors dispatched to harvest inside the first five
   minutes, before offensive units; guards over resource ships (decision +
   command lines; SC-002).
3. Steady cadence: command decisions each game-time tick; validator results
   all `ok`-or-clamped at the game boundary (zero invalid commands reach the
   game); any `rejected` loops back as tool errors next tick.
4. Match runs to a fixed observation horizon or natural end without seat
   stall. `replay.py --kinds decision,validation` reconstructs the trail.

## Scenario B — Emergencies break the cadence (US2, SC-003)

1. Continue from Scenario A with the strategist enabled
   (`twin-core.json`); confirm scheduled pulses every 30-90s game time.
2. Send a strike force at the LLM CPU's resource zone (or wait for the
   opponent to).
3. Expect: `classification` P0 line; P0 verbatim at TOP of the next tick's
   event window (decision line references it); an out-of-cycle pulse
   immediately (digest carries the P0 verbatim).

## Scenario C — Doctrine adaptation, sticky (US3, SC-004)

1. Sustain enemy economy raids (harness scenario or live play) past the
   evidence threshold for a doctrine change.
2. Expect: within one pulse a `packet` line with `directive.patch`; directive
   `revision` line bumps version; S1 behaviour shifts accordingly
   (attentions/response posture); `history_op` lines show trim or reset with
   in-flight orders preserved.
3. Negative check: no primary-enemy change inside the minimum hold time;
   packets lacking schema validity are `packet_rejected` with the old
   directive vN still in force.

## Scenario D — Degradation & coexistence (US4, SC-008)

1. Kill the S2 process mid-match → S1 continues on last directive
   (`degradation` line); no invented pulses.
2. Kill S1 mid-match → seat issues zero commands; match unaffected.
3. Kill the whole driver → seat fully idle; re-attach re-observes (ledger
   rebuild; no stale busy assumptions).
4. Skirmish with built-in CPU seat(s) alongside the LLM seat (driver        \
   attached to an unoccupied slot, no engine/lobby integration) → built-in
   CPU behaviour unchanged; match completes (SC-008).

## Scenario E — Evaluation harness (US5, SC-006)

```sh
python3 tools/twincore/harness.py --opponent builtin --difficulty beginner --matches 3
python3 tools/twincore/harness.py --opponent builtin --difficulty intermediate --matches 3
python3 tools/twincore/harness.py --opponent builtin --difficulty advanced --matches 3
```

Expect one report per series (`--out` default: stdout
+ one postgres `harness_log` record per series) with win/loss
shape, opening adherence, P0 next-tick rate, cadence/budget adherence
(observed vs configured), rejection/clamp rates, digest volumes, and any
degradation events. SC-006 reads these reports.

## Debugging

- Trace decisions: `python3 tools/twincore/replay.py --match <dir> --ticks a-b`.
- Live rounds follow repo rules: background launch, poll, never block (see
  `~/.config/opencode` pinned rules; savegame `cmr1` convention).
- Engine-side troubleshooting: `documentation/community/installation-manual.md`,
  sidecar logs `/tmp/aiplay-game-*.log`.