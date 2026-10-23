# Contract: Delegation Envelope + S1 micro-packets

Feature: `005-v4-strategist-driven-llm-cpu-player-s2-d` | Date: 2026-10-23
Amendment: decision 17 (three-tier). Companions: [program-schema.md](program-schema.md), [executor-contract.md](executor-contract.md).

S2 owns strategy; S1 owns fast tactical reaction — bounded by the
envelope S2 writes into its Program. Nothing in code ever decides WHAT:
the envelope (data, from the model) decides the bounds; the executor just
enforces them.

## 1. Envelope in the Program packet

```json
{
  "version": 7,
  "posture": "defensive-expansion",
  "delegate": [
    {
      "grant_id": "d1",
      "verbs": ["order_attack", "order_guard", "order_tactics", "order_halt"],
      "ships": "scope:defense",
      "zone": [-800, 0, 400, 3000],
      "override_tasking": true,
      "expires_tick": 3200
    },
    {
      "grant_id": "d2",
      "verbs": ["order_move"],
      "ships": "scope:fleet",
      "zone": [-800, 0, 400, 3000]
    }
  ],
  "production": [ "...": "as program-schema.md" ],
  "tasking":   [ "...": "as program-schema.md" ]
}
```

Rules (FR-017): validated with the whole packet (same all-or-nothing
discipline); `verbs` may not include `order_build`/`order_research` unless
explicitly granted; no `delegate[]` ⇒ S1 advisory-only that program
generation; grants die at `expires_tick`.

## 2. Micro-packet (S1 output)

```json
{
  "program_version": 7,
  "seq": 12,
  "tasking": [
    {"id": "m1", "grant": "d1", "verb": "order_attack",
     "ships": [104, 108], "target": 881},
    {"id": "m2", "grant": "d1", "verb": "order_tactics",
     "ships": [104], "tactics": "evasive"}
  ]
}
```

Validation (FR-018), whole-packet at the tick boundary:
- schema as in program-schema.md (tasking items, ≤8 items);
- every item's `grant` must reference a LIVE grant (`d*` in the current
  program, not expired);
- item verb ∈ grant.verbs; item ships within grant.ships scope;
  moves/engagements within grant.zone (when present);
- allied targets rejected (carried);
- production verbs without explicit grant → items blocked.
Valid remainder applies; blocked items are recorded with reasons AND
verdict text (naming the licensed verbs/ships instead) and fed to S1's
next micro-input — the verdict section is how S1 learns the live
rulebook (FR-020): recent denials/coercions render under
`# EXECUTOR VERDICTS` with their allowed alternatives. ≤1 bounded repair
per micro-pulse; second failure aborts the micro-pulse (S2's program
stands).

S1's rolling history (FR-022): the micro-input also carries up to
`s1_context.max_exchanges` previous micro-packets (with their
MicroStatus) from within `s1_context.ticks` — the window the CURRENT
program allocated, truncated mechanically, FIRST pruned under the
micro-budget breach. No `s1_context` in the program ⇒ S1 stays
stateless per pulse.

## 3. Priority law on shared ships (executor enforcement)

1. Live program item covering the ship (from the active Program) — unless
   a live grant on that ship has `override_tasking: true` and S1
   re-tasks (override recorded).
2. Live S1 envelope command.
3. Reflex — runs only for ships covered by neither.

Every S1 command cites `{program_version, seq, grant_id, decision_id}` and
lands as a `micro` trail row; the ≤6-commands-per-tick cap counts ALL
channels combined.

## 4. Cadences & budgets (config `v4:`)

| Channel | Cadence | In budget | Out budget |
|---|---|---|---|
| S2 pulse | 90–240 s game time (default 120) — P0 wakes early | ≤12k | ≤3k |
| S1 micro-pulse | 5–15 s game time (default 8) — P0 wakes early | ≤4k | ≤900 |
| executor tick | ~1 s | — | ≤6 commands/tick total |

## 5. Degradation (FR-012, three-tier ladder)

| Failure | Effect |
|---|---|
| S1 unreachable | program + reflexes stand; S1's duties are NOT absorbed by executor code |
| S2 unreachable | last program stands; S1 keeps acting strictly inside its existing envelope; transitions recorded |
| S2 + S1 unreachable | last program + reflexes only |
| envelope expired | S1 advisory-only until the next program arrives |
| interface down | all idle |
| recovery | fresh observation only, both channels |