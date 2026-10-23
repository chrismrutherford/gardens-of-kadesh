# Contract: Executor semantics (deterministic translator)

Feature: `005-v4-strategist-driven-llm-cpu-player-s2-d` | Date: 2026-10-23

`executor.Executor` — the ONLY issuer of commands in v4 (its `client.py` is
the one binding owner, FR-002). Doctrine-free by construction: the executor
must contain NO build priorities, ratios, research chains, or attack
thresholds (FR-004). Its entire behaviour is:

```python
def on_tick(tick, state):
    issue_program_items(tick, state)   # mechanical translation
    run_reflexes(tick, state)          # gap-fillers ONLY
```

## 1. Program-item translation (per tick)

- Walk the ACTIVE program's items in fixed order: `production[]` items
  first (packet order), then `tasking[]` (packet order). No reordering, no
  scoring — packet order is the program's declared priority.
- Per item, at most the commands it naturally maps to; **global cap 6
  validated commands per tick** (FR-005). Items beyond the cap wait; they
  are retried on subsequent ticks (same version — this is cadence, not
  retry-on-failure).
- Production: `order_build` → build command against the builder ship
  (Mothership/Carrier discovered from state, prefer nearest idle builder —
  a discovery rule, not doctrine); `order_research` → research command
  (one lab per call; a busy-labs engine rejection is recorded as
  `rejected` with aux=5 and the item reissues next tick).
- Tasking: verbs map 1:1 to registry verbs (`order_attack/move/guard/
  patrol/halt/formation/tactics/harvest/dock`), subjects/targets from the
  packet, revalidated against the fresh ledger.
- Every minted command cites `{version, item_id}` — recorded in the
  ProgramStatus and the trail (traceability guarantee, SC-002).

## 2. Item lifecycle

| Class | First apply | After |
|---|---|---|
| `once: true` | issue commands | item retires (`applied`) |
| standing (default) | issue commands | re-issues each tick while valid; retires when superseded |
| validator/engine rejection | record `blocked`/`rejected` + reason | no auto-retry within the pulse cycle (S2 sees the failure next pulse) |
| stale/unrecoverable subjects | record `unrecoverable` + re-issue hint | skipped until a new program covers them |

## 3. Reflexes (FR-006 — EXACTLY two)

```
reflex_harvest: collector idle (no live order, incl. parade state)
              → order_harvest(nearest_asteroid)   # nearest by euclidean
              #    distance; tie-break: lower resource id (determinism, IV)
reflex_deliver: collector full → order_dock(nearest dock-capable own ship)
```

- GATE before both: the ship is NOT named by any live program item OR any
  live S1 envelope command. Priority law: program > S1 > reflex — S1
  overrides a program-tasked ship only where a live grant carries
  `override_tasking: true` (every override recorded). Reflexes fill gaps;
  they exist below both model channels.
- Reflex commands pass the same validator, cite `item_id=reflex_*`, and
  are recorded in the trail like any other command (Recorder rows).
- The S1 micro lane is carried by this same executor: validated
  micro-packet items mint commands citing `{program_version, seq,
  grant_id, decision_id}` (item_id `s1:<decision_id>`), inside the §1
  per-tick cap (all channels combined).
- No other autonomous behaviour exists — no fallback doctrine when S2 is
  unreachable (FR-012: last program + reflexes stand, nothing invented).

## 4. Degradation ladder (FR-012, v4 wording)

| Failure | Effect |
|---|---|
| S1 unreachable | program + reflexes stand; executor does NOT absorb S1 duties |
| S2 unreachable | last valid program + S1 (existing envelope) + reflexes; transitions recorded; no procedural filler |
| S2 + S1 unreachable | last valid program + reflexes only |
| pulse invalid (schema) | last program stands; reason queued for S2's next input |
| executor/driver down | seat idles (no commands at all) |
| interface down | all idle |
| recovery | only via fresh observation (re-observe); no state guessing |

## 5. Determinism guarantees (Constitution IV)

- Same (program, state) ⇒ same command sequence and ids within a tick
  (ids stable per match; per-tick order ids minted as in v3).
- Reflex target selection is pure: nearest-distance + id tie-break.
- No wall-clock reads anywhere in gameplay decisions (game tick only).