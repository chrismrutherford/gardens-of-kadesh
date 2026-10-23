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
| `resolution` | enum | `applied` \| `rejected` (engine orderRejected aux=<code>) \| `blocked` (validator reason string) \| `pending` \| `unrecoverable` (stale subjects + re-issue hint) |

Reflex-produced commands are recorded with the same row shape, `item_id`
naming the reflex (`reflex_harvest` / `reflex_deliver`) or the S1 lane
(`s1:<decision_id>` citing program version + `grant` id) — one taxonomy
for all command paths (Recorder rows, spec Key Entities).

### Reflex (enumerated set — FR-006, EXACTLY)

| Reflex | Trigger (from state alone) | Action |
|---|---|---|
| `reflex_harvest` | owned ResourceCollector idle (no live order / parade) | harvest nearest asteroid (euclidean, deterministic tie-break by id) |
| `reflex_deliver` | owned ResourceCollector full | dock Mothership/Carrier to deliver |

Precondition for both: no live program item AND no live S1 envelope
command covers those ships (program > S1 > reflex; reflexes are the
bottom of the priority law).

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
resolution incl. `blocked: outside-envelope`).

### PulseInput (budgeted observation — FR-009 layout)

1. static charter prefix (seat, race, playbook-material block)
2. P0 events verbatim, pinned (`digest.P0Tracker` items until superseded)
3. ledger echo — one line per unit group
4. event rollups (`digest.rollup`, 160-tick interval)
5. previous program + per-item ProgramStatus
6. S1 micro digest (adherence, violations) + reflex digest

Budget ≤12k units (config `v4.budgets.s2_in`); breach → prune oldest
program history from the input + recorded alert (FR-013). Static
prefix first for cache-friendly serving.

### MicroInput (S1's budgeted observation — FR-018 layout, ≤4k units)

1. static micro-charter prefix (seat, playbook excerpt, current envelope
   grants echoed verbatim)
2. P0 events verbatim, pinned
3. compact live view of envelope-relevant ships (subjects, their orders,
   threats in/entering zone)
4. own previous MicroPacket + MicroStatus
5. program tasking items naming the envelope's ships (so S1 knows what
   it may override; only when `override_tasking` grants it)

### Trail rows (postgres `twincore_q.messages`, FR-011)

Reused kinds: `packet`, `revision`, `event_raw`, log kinds as in v3.
New kinds: `program` — program apply/fail/supersede events;
`micro` — S1 packet apply/violation events (cites program version +
grant + decision id). All rows: `match_id`, `tick`, `kind`, payload;
replayable kind/tick (SC-007).

## Validation rules (cross-check against spec FRs)

- Whole-packet validation before apply; invalid → nothing applies, the
  reason loops to S2's next input, old version stands (FR-008, US3-AS3).
  Same law per micro-packet against the CURRENT envelope (FR-018).
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
- Allied players as targets rejected at validation (Edge Cases).
- Retired v1/v2 agent stacks, and v3's s1tick/economy/s2pulse modules
  unreachable from the v4 driver — the v4 S1 channel is NEW code
  (FR-014, enforced by import-graph test).
- Playbook numbers appear only in prompt material/config, never as driver
  code constants (FR-004/FR-016).