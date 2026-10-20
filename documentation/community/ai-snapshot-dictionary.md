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
| `ships`    | array ≤80       | every live ship (either side) |
| `resources`| array ≤40       | asteroids: `id`, `value` (RU), `pos` |
| `buildQueue`| array          | one entry per queued ship build: `type` (ship name), `quantity`, `ticksRemaining` |
| `research` | array           | one entry per ACTIVE research: `tech`, `labs` (# labs assigned), `timeLeft` |
| `techTree` | object          | `labs` (# ResearchShips in fleet = research slots), `canResearch`, `locked[]`, per locked: `type` + `missing[]` |

## Per-ship fields

| Field        | Meaning |
|--------------|---------|
| `id`         | ship id used in every order tool; invalid after any match reset |
| `owner`      | player index (equal to bound player = your side) |
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
- `{"verb":"passive-attack"}` — moving but set to return fire
- `{"verb":"order?"}` — unrecognized command (treat as busy-unknown;
  use formation/scuttle awareness)
- `"STUCK-move-to-origin"` (compressed view) — the move order points near
  [0,0,0]: a bad order; re-issue

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
| `orderRejected` | order id FAILED; aux = engine error code (2=E_BAD_ID, 3=E_UNAFFORDABLE, 4=E_NO_BUILDER, 5=E_NO_LABS) |
| `shipDestroyed` | id destroyed; aux = killer owner +1, owner = victim |
| `matchOver` | match ended |

Order results carry error strings directly: `E_NO_SESSION`, `E_BAD_ID`
(stale id after a reset — re-observe), `E_UNAFFORDABLE` (insufficient
RU), `E_NO_BUILDER`, `E_NO_LABS (build ResearchShips first)`,
`E_MATCH_OVER`, `E_QUEUE_FULL`, `E_BAD_REQUEST`.