# Data Model: V4 — Strategist-Driven LLM CPU Player

Feature: `005-v4-strategist-driven-llm-cpu-player-s2-d` | Date: 2026-10-23
Companion to [plan.md](plan.md) and [contracts/program-schema.md](contracts/program-schema.md).

## Entities

### Program (version document — the seat's sole intent authority)

| Field | Type | Rules |
|---|---|---|
| `version` | int | monotonic per match, > previous accepted version |
| `issued_tick` | int | game tick the pulse input was built at |
| `issued_pulse` | int | pulse counter, monotonic |
| `posture` | string? | advisory text; NOT executable — rendered into NEXT pulse input only (FR-005) |
| `arc` | string? | S2's running compacted story, rewritten each pulse; driver stores LATEST only and re-renders it into every pulse input; ≤600 chars default (config `v4.arc_chars`) — over-budget arc is LENGTH-CLAMPED with a verdict, never rejected (FR-021) |
| `s1_context` | object? | `{max_exchanges: int 0–8, ticks: int}` — S2-allocated S1 history window; the executor truncates S1's rolling micro-history mechanically; FIRST thing pruned under budget breach; absent ⇒ S1 stays stateless (FR-022) |
| `delegate[]` | list[Grant] | the DELEGATION ENVELOPE (FR-017): what S1 may do between pulses; validated with the packet |
| `production[]` | list[ProductionItem] | build/research intents, fixed priority order |
| `tasking[]` | list[TaskingItem] | fleet verbs, fixed priority order |
| `subject` | string? | optional S2 self-note, logged not acted on |

State: `active` (applied whole at a tick boundary) → `superseded` (newer
version applied) / `stale` (subjects unrecoverable after match load).
Previous version remains authoritative until a valid new one lands (FR-008).

### ProductionItem

| Field | Type | Rules |
|---|---|---|
| `id` | string (e.g. `p1`) | unique within packet; cite key for trail rows |
| `verb` | enum | `order_build` \| `order_research` |
| `params` | object | verb-shaped: build → `{type, quantity?}`; research → `{tech}` |
| `priority` | int? | 0 highest; executor honours fixed order regardless |
| `once` | bool | one-shot (retire on first apply) vs standing (default: standing) |

### TaskingItem

| Field | Type | Rules |
|---|---|---|
| `id` | string (e.g. `t1`) | unique within packet |
| `verb` | enum | `order_attack` \| `order_move` \| `order_guard` \| `order_patrol` \| `order_formation` \| `order_halt` \| `order_harvest` \| `order_dock` |
| `ships` | list[int] | subject ship ids, revalidated against fresh ledger at executor time |
| `target` | int? | target ship/resource id (verb-dependent) |
| `pos` | [x,y,z]? | for move/formation |
| `tactics` | enum? | `aggressive` \| `neutral` \| `evasive` |
| `priority` | int? | advisory ordering |
| `once` | bool | one-shot vs standing (default: standing) |

### ProgramStatus (executor → S2 feedback, recorded each application attempt)

| Field | Type | Rules |
|---|---|---|
| `program_version` | int | the authorising version |
| `item_id` | string | which item caused the command(s) |
| `issued_tick` | int | tick the commands were minted |
| `command_ids[]` | list[int] | commands minted this attempt |
| `resolution` | enum | `applied` (executor issued = NOT engine-acked) \| `engine_denied` (orderRejected `aux=<code>` from the event consumer) \| `blocked` (validator reason, verdict text attached) \| `pending` \| `unrecoverable` (stale subjects + re-issue hint) |
| `verdict` | string? | the FR-020 actionable line for non-`applied` outcomes (what was refused + what IS allowed) |

ISSUED ≠ ACCEPTED (FR-023): the engine-event consumer folds asynchronous
`orderRejected`/`shipDestroyed`/research events into the matching item's
status; an item the executor issued but the engine denied is
`engine_denied: aux` in the NEXT pulse input with a verdict naming the
legal alternatives (e.g. `unknown tech 'MassDrive2Kt'; researchable now:
[Chassis1, TargetingSystems, …]`).

### Verdict (executor/engine decision made legible — FR-020)

| Field | Type | Rules |
|---|---|---|
| `kind` | enum | `deny` \| `coerce` \| `clamp` \| `engine_denied` |
| `subject` | string | item/reflex id the verdict concerns (`p2`, `t3`, `reflex_harvest`, `s1:m7`) |
| `text` | string | ONE actionable line: what was refused + what IS currently allowed (licensed verbs/ships of the active grant; legal techs/hulls with costs; the clamped cap). Bounded (hundreds of bytes, config `v4.verdict_chars`) |
| `tick` | int | verdict time |

Sinks (all three, every verdict): (a) `validation` trail row; (b) S2's
per-item ProgramStatus in the next pulse input; (c) S1's rolling verdict
section (last K verdicts, budget-bounded) in the next micro-input. The
verdict emitter NEVER invents orders — it explains only the executor's
own decisions.

Reflex-produced commands are recorded with the same row shape, `item_id`
naming the reflex (`reflex_harvest` / `reflex_deliver`) or the S1 lane
(`s1:<decision_id>` citing program version + `grant` id) — one taxonomy
for all command paths (Recorder rows, spec Key Entities).

### Reflex (enumerated set — FR-006, EXACTLY)

| Reflex | Trigger (from state alone, re-checked EVERY tick) | Action |
|---|---|---|
| `reflex_harvest` | owned ResourceCollector idle — no live order OR parade/no-harvest-order state | harvest nearest-value asteroid (euclidean, deterministic tie-break by id) |
| `reflex_deliver` | owned ResourceCollector full | dock Mothership/Carrier to deliver |

CONTINUOUS ENFORCEMENT (live-round lesson: an RC sat in parade ~20k
ticks): the reflex pass re-evaluates every owned collector EVERY tick and
re-dispatches again if a ship drifted back to idle/parade — the reflex is
a floor condition, not a one-shot event. Precondition for both: no live
program item AND no live S1 envelope command covers those ships
(program > S1 > reflex; reflexes are the bottom of the priority law).

### DelegationEnvelope (Program.`delegate[]` — FR-017)

| Field | Type | Rules |
|---|---|---|
| `grant_id` | string (`d1`, `d2`, …) | unique within packet; citation key for S1 commands |
| `verbs` | list[enum] | tasking verbs S1 may use (order_attack/move/guard/patrol/halt/formation/tactics/harvest/dock); `order_build`/`order_research` ONLY if explicitly granted |
| `ships` | list[int] or `scope:defense`/`scope:fleet` | subjects S1 may command |
| `zone` | [cx,cy,cz,r]? | optional spatial bound for moves/engagements |
| `override_tasking` | bool | if true, S1 may re-task ships named by standing program items (retreat/retarget) — every override recorded |
| `expires_tick` | int? | grant dies at this tick; afterwards S1 is advisory-only |

No `delegate[]` in a program ⇒ S1 is advisory-only that program-generation.

### MicroPacket (S1's per-micro-pulse deliverable — FR-018)

Same whole-packet discipline as a Program (all-or-nothing applies at the
tick boundary, ≤1 bounded repair), but: tasking items only (production
forbidden unless an explicit grant exists); each item carries `grant` =
the grant_id it acts under; ≤8 items; references `program_version` it
acts under + own `seq` counter. Validated against the CURRENT envelope
(version + expiry): verbs/subjects outside grants → those items blocked
with reasons recorded (never executed), valid remainder applies.
**MicroStatus** rows mirror ProgramStatus (issued tick, command ids,
resolution incl. `blocked: outside-envelope` + verdict text naming the
licensed verbs/ships instead). S1's ROLLING HISTORY = previous
micro-packets + their outcomes, kept to the `s1_context` window the
current program allocated (`max_exchanges` items / `ticks` span),
truncated mechanically; first pruned under the micro budget breach
(FR-022).

### PulseInput (budgeted observation — FR-009 SUPERSET layout)

Render order (Draft v3):

1. static charter prefix (seat, race, playbook-material block)
2. `# ARC (your summary so far)` — latest stored arc + last posture;
   NEVER absent (code-seeded from rollups if missing/stale; length-
   clamped if oversize)
3. P0 events verbatim, pinned (`digest.P0Tracker` items until superseded)
4. mechanical rollups since last pulse (builds materialized, kills,
   researches, spend — code-computed from the trail)
5. previous program + per-item ProgramStatus INCLUDING `engine_denied`
   aux codes + verdict text (FR-020/FR-023)
6. ledger echo — per-ship line (ids/posture/cargo)
7. ENEMY CONTACTS — `enemy <id> <hull> pos[..] dist_to_base=<m>` (the
   same builder S1's live view uses; empty contacts = "none observed",
   no fabrication)
8. asteroid ledger — `ast <id> pos[..] value=<RU>`, nearest-first, top-N
   (config `v4.asteroid_top_n`, default 32) + total count/RU in view
9. build menu — unlocked hulls (from techTree locked-minus-missing) with
   costs + current buildQueue contents per builder
10. research menu — researchable techs with missing prerequisites +
    `labs=<n> lab_busy=<state>`; researched list

Budget ≤12k units (config `v4.budgets.s2_in`); breach → prune by the
FR-013 order (history/echo first, arc length-clamped, world sections
LAST) + recorded alert. Static prefix first for cache-friendly serving.

### MicroInput (S1's budgeted observation — FR-018 layout, ≤4k units)

1. static micro-charter prefix (seat, playbook excerpt, current envelope
   grants echoed verbatim)
2. P0 events verbatim, pinned
3. rolling verdict section — recent executor denials/coercions with the
   allowed alternatives (FR-020; the live rulebook S1 learns from)
4. S1 history window — the `s1_context`-sized rolling micro-history
   (previous packets + outcomes; stateless when absent)
5. compact live view of envelope-relevant ships (subjects, their orders,
   in-scope markers, threats in/entering zone)
6. own previous MicroPacket + MicroStatus
7. program tasking items naming the envelope's ships (so S1 knows what
   it may override; only when `override_tasking` grants it)

### Trail rows (postgres `twincore_q.messages`, FR-011)

Reused kinds: `packet`, `revision`, `event_raw`, `validation` (now ALSO
carries FR-020 verdict lines), log kinds as in v3.
Kinds: `program` — program apply/fail/supersede events;
`micro` — S1 packet apply/violation events (cites program version +
grant + decision id). All rows: `match_id`, `tick`, `kind`, payload;
replayable kind/tick (SC-007).

## Validation rules (cross-check against spec FRs)

- Whole-packet validation before apply; invalid → nothing applies, the
  reason loops to S2's next input, old version stands (FR-008, US3-AS3).
  Same law per micro-packet against the CURRENT envelope (FR-018).
- `arc`: optional string; over budget → LENGTH-CLAMPED by the driver
  with a verdict (never rejected, never dropped); missing/stale →
  code-seeded from rollups (FR-021). `s1_context`: `{max_exchanges:
  0–8, ticks: 60–100000}` or absent; malformed SHAPE rejects the packet
  (schema, FR-008) — the ranges are policy, the shape is contract.
- Priority law on shared ships: program > S1 envelope command > reflex;
  S1 override of program-tasked ships only where `override_tasking`
  grants it, and every override is recorded (FR-017).
- Cadences: S2 pulse 90–240 s game time (default 120 s); S1 micro-pulse
  5–15 s (default 8 s); ≤1 program per S2 pulse; ≤1 micro-packet per S1
  pulse (FR-007).
- ≤1 program per pulse (FR-007); ≤1 bounded repair attempt on malformed
  output, then abort the pulse keeping the last program (Edge Cases).
- ≤6 validated commands per executor tick (FR-005, all channels
  combined).
- Reflex floor re-checked EVERY tick (idle includes parade/no-harvest
  state; re-dispatch ≤1 tick from idle/parade to harvest order) (FR-006).
- Verdicts: every deny/coerce/clamp/engine-denied emits a `validation`
  row + renders into both channels' next inputs; text names ≥1 allowed
  alternative (verdict legibility, SC-006) (FR-020).
- Allied players as targets rejected at validation (Edge Cases).
- Retired v1/v2 agent stacks, and v3's s1tick/economy/s2pulse modules
  unreachable from the v4 driver — the v4 S1 channel is NEW code
  (FR-014, enforced by import-graph test).
- Playbook numbers appear only in prompt material/config, never as driver
  code constants (FR-004/FR-016).