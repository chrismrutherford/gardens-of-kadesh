# Data Model — Full-Coverage AIPlay MCP

Single source of truth for both transports (UDS line protocol + MCP tools).
Derived from `src/AIPlay/*`, `src/Game/CommandWrap.{h,c}`.

## Entities

### Envelope (request/response)
- Request: `{id, method, params?}` (UDS) — MCP: tools/call with name+arguments.
- Response: `{id, result}` | `{id, error:{code, message}}`.
- id echo: integer without dot → integer; string → quoted; other → verbatim; missing → `null`.

### OrderRequest (per-verb parameter tables)
| verb (MCP tool `order_*` / `*_order`) | ships | target | extra | funnel |
|---|---|---|---|---|
| move | required | coord [3] | — | clWrapMove |
| attack | required | ship id | — | clWrapAttack |
| guard / protect | required | ship id | — | clWrapProtect |
| dock | required | dockWith (ship id) | — | clWrapDock |
| harvest | required | resource id (must be resource) | — | clWrapCollectResource |
| special | required | ship id optional | — | clWrapSpecial |
| halt | required | — | — | clWrapHalt |
| build | optional (default mothership) | — | type*, quantity≥1 (default 1) | clWrapDeterministicBuild |
| research | not required (fleet-wide) | — | tech* | clWrapResearch |
| formation | required | — | formation* | clWrapFormation |
| tactics | required | — | tactics enum | clWrapSetTactics |
| launch | required (dock-capable) | — | — | clWrapLaunchMultipleShips |
| autolaunch | not required | — | onOff | clWrapAutoLaunch |
| scuttle | required | — | — | clWrapScuttle |
| select | not required | ships list | — | (layer-side no-op) |
| **kamikaze** (new) | required | — | onOff optional | clWrapSetKamikaze |
| **parade** (new) | required | — | onOff optional (default on) | clWrapSetMilitaryParade |
| **ruTransfer** (new) | fleet-wide | — | to (player index)*, amount* | clWrapRUTransfer |
| **alliance** (new) | fleet-wide | — | player*, onOff* | clWrapSetAlliance |
| **hyperspace** (new) | required | coord [3] | — | clWrapMpHyperspace |

`*` = required. shipIds ≤64/order; name ≤48 chars; order queue 128; id echo uint64.

### Result shapes
- queued: `{queued: true, orderId}`; errors: `{error:{code,message}}` (sync) or
  event `{type:"orderRejected", aux:<enum>}` (drain stage).
- enum↔code: 0 none, 1 E_NO_SESSION, 2 E_BAD_ID, 3 E_UNAFFORDABLE,
  4 E_NO_BUILDER, 5 E_NO_LABS, 6 E_MATCH_OVER, 7 E_QUEUE_FULL,
  8 E_BAD_REQUEST, 9 E_INTERNAL.

### Snapshot (state.snapshot)
- Root: `tick`(u32), `timeElapsed`(f64), `paused`(bool), `income`(f64, -1 unknown/paused).
- `players[]`: index, isAI, resourceUnits(i64), alive, lost.
- `ships[]`: id(u64), owner, type(str), pos[3], vel[3] (f32, %.3f), health, healthMax,
  dps, guns(int), range, `order|null`: verb (move|attack|guard|dock|harvest|launch|
  special|halt|parade|passive-attack|moving|order?) + targetId(u64) or target[3].
- `resources[]`: id, owner(-1), type "Asteroid", pos, vel, value(int).
- `buildQueue[]`: type, quantity, ticksRemaining. `research[]`: tech, labs, timeLeft.
- `techTree`: labs(int), canResearch(bool), `locked[]`: type, disabled, missing[str],
  `researched[]`: tech[str].
- `matchStatus`: running, boundPlayerIndex, winner(int|null), `perPlayer[]`:
  index, alive, lost.
- Filters: `params.owner` (int ≥0), `params.sections` ⊂ {ships, resources}.

### Event (state.events) — ring 256, newest first
- matchStarted, matchOver (aux winner+1 or 0), orderApplied,
  orderRejected (aux enum), shipDestroyed (aux killer+1 or 0),
  bindChanged, matchContinued; fields: type, tick, id, aux, owner.

### Status / session control
- status: state∈{disabled,lobby,playing,matchOver}, boundPlayerIndex(-1 none),
  sessionId(=1), mapName, matchStatus{running,paused,tick}.
- bind→{playerIndex, sessionId, mapName}; pause/resume→{paused};
  step(ticks≥1, default 1)→{stepped, tick}; save(name≤64 safe chars,
  default "aiplay-autosave-<tick>")→{saved}.

## Validation rules (submit-time, FR-009)
- fleet-wide verbs exempt from ships: research, select, autolaunch, ruTransfer,
  alliance; build defaults to PlayerMothership when ships omitted.
- every shipId must resolve to a live owned object → else E_BAD_ID.
- move/hyperspace require coord target; attack/guard/dock/harvest require a
  target; build/research/formation/tactics/autolaunch/kamikaze/parade/ruTransfer/alliance
  require their name/numeric params present; harvest target must be a resource.
- build: type must resolve (race statics loaded) → E_BAD_REQUEST; cost×quantity
  vs RU → E_UNAFFORDABLE. research: known tech → E_BAD_REQUEST;
  no ship lab / CanDoResearch false → E_NO_LABS.
- health clamp: <0 or >2×healthMax → 0 (pending-removal guard).