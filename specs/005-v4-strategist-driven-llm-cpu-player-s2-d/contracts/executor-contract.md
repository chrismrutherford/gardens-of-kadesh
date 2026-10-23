# Contract: Executor semantics (deterministic translator)

Feature: `005-v4-strategist-driven-llm-cpu-player-s2-d` | Date: 2026-10-23

`executor.Executor` — the ONLY issuer of commands in v4 (its `client.py` is
the one binding owner, FR-002). Doctrine-free by construction: the executor
must contain NO build priorities, ratios, research chains, or attack
thresholds (FR-004). Its entire behaviour is:

```python
def on_tick(tick, state):
    issue_program_items(tick, state)   # mechanical translation
    run_reflexes(tick, state)          # gap-fillers ONLY (re-checked every tick)
    emit_verdicts(tick)                # explain THIS tick's denies/coerces/clamps
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
- ISSUED ≠ ACCEPTED (FR-023): an engine `orderRejected` (async) resolves
  the item to `engine_denied: aux` via the driver's event consumer and
  emits a verdict naming the legal alternatives (e.g. `order_research
  tech 'MassDrive2Kt' unknown; researchable now: [Chassis1, ...]`). The
  busy-labs engine rejection stays `rejected` with aux=5 + verdict
  ("lab busy; in progress: <tech>"), item reissues next tick.

## 2. Item lifecycle

| Class | First apply | After |
|---|---|---|
| `once: true` | issue commands | item retires (`applied`) |
| standing (default) | issue commands | re-issues each tick while valid; retires when superseded |
| validator/engine rejection | record `blocked`/`rejected` + reason | no auto-retry within the pulse cycle (S2 sees the failure next pulse) |
| stale/unrecoverable subjects | record `unrecoverable` + re-issue hint | skipped until a new program covers them |

## 3. Reflexes (FR-006 — EXACTLY two)

```
reflex_harvest: collector idle (no live order, incl. parade/no-harvest
              state) → order_harvest(nearest-value asteroid)
              # nearest by euclidean distance; tie-break: lower resource
              # id (determinism, IV). Re-checked EVERY tick — a collector
              # that drifted back to idle/parade is re-dispatched again
              # (≤1 tick from idle/parade to a harvest order; the reflex
              # is a floor condition, not a one-shot — live-round lesson:
              # an RC idled ~20k ticks in parade).
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

## 6. Teaching firewall (FR-020 — the teach duty)

Every deny, coercion, or clamp this executor performs — validator
blocking, subject coercion (e.g. scalar → array ships), per-tick cap
clamping, envelope refusal, engine-denied resolution — emits ONE
actionable verdict line:

```
kind=deny       subject=s1:m7   order_attack ships[250]: 250 not in grant
                d1 scope:defense; defense ships: [2,3,4,5,6,253,254]
kind=engine_denied item=p2      order_research 'MassDrive2Kt' unknown;
                researchable now: [Chassis1, TargetingSystems, ...]
kind=coerce     subject=s1:m2   order_guard ships=2 coerced to [2]
kind=clamp      subject=p1      per-tick cap reached (6); item waits
```

Rules:
- The text names what was refused AND what IS currently allowed (≥1
  alternative — legibility is a SC-006 measure). Bounded text (config
  `v4.verdict_chars`, default ~200); the cap counts inside the channel
  budget breach rules (FR-013).
- Sinks (all three, every verdict): (a) a `validation` trail row;
  (b) S2's per-item ProgramStatus next pulse; (c) S1's rolling verdict
  section next micro-input (last K verdicts, budget-bounded).
- The executor NEVER invents orders — verdicts explain only its own
  decisions; no recommendation beyond restating current legality.
- Engine-side asynchronous rejections fold into the same stream with
  their aux codes (FR-023) — nothing fails invisibly, and the same
  denial text must NOT recur unchanged forever (repeated-identical-
  denial rate is a SC-006 measure; if it does repeat, the verdict gains
  a `# repeat` marker counting recurrences so S2 sees the loop).

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