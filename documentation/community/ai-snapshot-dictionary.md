# AI Snapshot Data Dictionary (authoritative — what observe/state.snapshot returns)

The compressed snapshot the LLM agents see is a JSON object with these
fields (compression in tools/agent.py `compress_snapshot`).

## Top-level fields

| Field      | Type            | Meaning |
|------------|-----------------|---------|
| `tick`     | int             | engine simulation tick (~6/s); ticks DECREASE on match reset/reload |
| `paused`   | bool            | simulation paused (orders still queue) |
| `matchStatus.running` | bool | false = match loading/over |
| `players`  | array           | per player: `index` (0=human side unless bound other), `isAI`, `resourceUnits` (RU bank), `alive`, `lost` |
| `ships`    | array ≤80       | every live ship (either side); the MCP `observe` tool returns ALL ships (no cap — 187 in test fixtures); the ≤80 cap is the agent-fleet client compression, opt-in |
| `resources`| array ≤40       | asteroids: `id`, `value` (RU), `pos`; MCP `observe` returns all (119 in test fixtures); `sections` selects ships and/or resources |
| `buildQueue`| array          | one entry per queued ship build: `type` (ship name), `quantity`, `ticksRemaining` |
| `research` | array           | one entry per ACTIVE research: `tech`, `labs` (# labs assigned), `timeLeft` (empty while no lab is researching) |
| `techTree` | object          | `labs` (# ResearchShips in fleet = research slots), `canResearch` |, `locked[]`, per locked: `type` + `missing[]`, `researched[]` (known techs) |

## Per-ship fields

| Field        | Meaning |
|--------------|---------|
| `id`         | ship id used in every order tool; invalid after any match reset |
| `owner`      | player index (equal to bound player = your side); resources/asteroids are always `-1` (unowned — owner is a ship-only field) |
| `type`       | ship class name (matches Build Manager list) |
| `pos`,`vel`  | position/velocity [x,y,z] |
| `health`,`healthMax` | current/max hit points |
| `dps`        | gun damage per second (aggressive tactics), 0 = unarmed |
| `guns`,`range` | gun count, max engagement distance — range asymmetry wins fights |
| `order`      | what the ship is doing right now (see below) |

## order values (per ship)

- `null` — truly idle: no queued command
- `{"verb":"move","target":[x,y,z]}` — moving to coordinates
- `{"verb":"attack","targetId":N}` — attacking ship N
- `{"verb":"guard","targetId":N}` — guarding/protecting ship N (a
  protecting move surfaces as guard)
- `{"verb":"dock"}` / `{"verb":"launch"}` — docking / docked, launching
- `{"verb":"harvest","targetId":N}` — collecting from asteroid N
- `{"verb":"parade"}` — NOT idle-idle but inactive: military parade
  (default stance near Mothership); warships still auto-engage, but
  ResourceCollectors in parade are NOT harvesting — treat as idle economy
- `{"verb":"special"}` — special ability (cloaking, salvage, grav-well…)
- `{"verb":"halt"}` — holding position
- `{"verb":"formation"}` — in a formation (delta/broad/sphere/…)
- `{"verb":"passive-attack"}` — moving but set to return fire; a ship
  RETURNING FIRE also carries `targetId` = the attacker id: the
  under-attack signal (see FR-014 spec 003)
- `{"verb":"order?"}` — unrecognized command: genuinely outside the mapped
  verb set (COMMAND_NULL orphans are SKIPPED and no longer emitted here:
  `null` and the next real order win; only engine commands without a mapping
  surface this now). Treat as busy-unknown and re-task.
- `{"verb":"hyperspace"}` — the ship is mid near-jump (MP)
- `"STUCK-move-to-origin"` (compressed view) — the move order points near
  [0,0,0]: a bad order; re-issue

## order targetIds are mapped ids (spec 003)

Since spec 003, `order.targetId` for attack/harvest/passive-attack is the
same mapped id space as the snapshot `id`s (an earlier build serialized raw
engine pointers here — >2^48 values; treat any such value as stale data).

## New order verbs (spec 003 MCP tools)

| tool | semantics |
|---|---|
| `order_kamikaze` {ships} | one-way ramming run (ship usually destroyed); piecemeal > waves; target collectors at the field, not dropping off |
| `order_parade` {ships} | military parade stance (warships still auto-engage; idle economy) |
| `order_ru_transfer` {to,amount} | RU transfer (MP; E_UNAFFORDABLE precheck) |
| `order_alliance` {player,onOff} | alliance on/off (MP; self → E_BAD_ID) |
| `order_hyperspace` {ships,target[3]} | near-jump to coordinate (MP funnel) |

## techTree.locked semantics (build gating — same rule as the Build UI)

- locked ship with **non-empty `missing`** → researchable: those techs
  unlock it
- locked ship with **EMPTY `missing`** → DISABLED for this match/race
  (e.g. cross-race hulls in a single-race lobby): never researchable,
  never buildable — trying researches nothing and can crash the engine
- everything NOT in `locked` → buildable RIGHT NOW; no research needed
  (an "all-unlocked" match shows a short/no `locked` list)

## Events (state.events; per agent round deltas)

| type | aux / meaning |
|---|---|
| `matchStarted` / `matchContinued` | fresh match / keepalive (aux 0) |
| `bindChanged` | binding state changed |
| `orderApplied` | order id executed |
| `orderRejected` | order id FAILED; aux = engine error enum: 2=E_BAD_ID, 3=E_UNAFFORDABLE, 4=E_NO_BUILDER, 5=E_NO_LABS, 6=E_MATCH_OVER, 7=E_QUEUE_FULL, 8=E_BAD_REQUEST, 9=E_INTERNAL (see tools/aiplay_protocol.py ERROR_ENUM_TO_CODE) |
| `shipDestroyed` | id destroyed; aux = killer owner +1, owner = victim; aux 0 = no killer attributed (non-combat death — collision, stale/unseen eviction) |
| `matchOver` | match ended; aux = winner index + 1 (0 = draw/unknown) |

Order results carry error strings directly: `E_NO_SESSION`, `E_BAD_ID`
(stale id after a reset — re-observe), `E_UNAFFORDABLE` (insufficient
RU), `E_NO_BUILDER`, `E_NO_LABS (build ResearchShips first)`,
`E_MATCH_OVER`, `E_QUEUE_FULL`, `E_BAD_REQUEST`, `E_INTERNAL`
(map: tools/aiplay_protocol.py ERROR_ENUM_TO_CODE — the single source).