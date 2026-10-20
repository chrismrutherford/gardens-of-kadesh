# Contract: peer-authored priority instruction queue

## Editor contract (peers)

- Any peer MAY include `"edits": [...]` in its normal per-round artifact
  (analyst.json/critic.json/strategist.json/economist.json).
- Ops (applied in array order, newest writer wins per id):
  - `{"op":"add","text":"<8..200 chars one imperative line>","priority":1..12}`
  - `{"op":"remove","id":N}`
  - `{"op":"move","id":N,"priority":1..len}`
- Rejections: returned in the peer's NEXT context (history entry with
  reason), so a peer can correct itself. Queue untouched on rejection.
- Peers MUST NOT attempt engine calls to enforce instructions (constitution
  VI) — edits are their ONLY lever.

## Executor contract (commander)

- Each round the commander reads `instruction-queue.json`.
- If the queue is non-empty: the prompt lists instructions in priority
  order; the executor works the ACTIVE instruction (topmost non-done);
  each round appends a one-line progress note; states advance
  pending→active→done/failed/stalled (stall = 10 rounds, flag-overridable).
- If the queue is empty: standing invariants resume (economy first,
  production, no idle ships, research in progress).
- Standing safety rules ALWAYS apply: defense-before-offense; a lower
  instruction must not cause the Mothership to be sacrificed.
- The commander NEVER edits the queue (its notes are the only output).

## Files

- `/tmp/aiplay-multi-<session>/instruction-queue.json`
- `/tmp/aiplay-multi-<session>/instruction-history.json`
  (both atomic replace; human-readable; live-readable at all times)

## CLI

No new flags; queue feature active automatically when agent-multi runs
(`--queue-cap`, `--stall-rounds` optional overrides).