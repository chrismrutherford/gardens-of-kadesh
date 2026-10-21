# Bug Assessment: aiplay MCP order pipeline + snapshot bug batch

- **Slug**: aiplay-mcp-bug-batch
- **Created**: 2026-10-21
- **Source**: pasted text (BUGS.md at repo root + live-match observations logged in the engineer logbook session 8)
- **Verdict**: valid
- **Severity**: high (batch; per-bug severity below)

Single-engine-process multiplayer: both players' ships share one id map, one
command layer, and one snapshot writer — that context links bugs 4 and 5
(ownership + order-verb serialization) and is why "enemy shows my ids" was
observed. All bugs below were observed live in a 1v1 skirmish driven entirely
through the kadesh MCP tools; the match engine was later killed (SIGTERM PID
103439) for this fix session.

Note: id-map "owner 0" poisoning also explains why enemy ships appeared inside
an `owner=0` observe filter (bug 5b) — the attack/collect retouches (bug 5a)
rewrote enemy ships' stored owner to 0.

---

## Bug A — `autolaunch` onOff boolean rejected (E_BAD_REQUEST every call)

**Severity**: high (blocks post-build launch flow; workaround exists)

### Symptom
`kadesh_autolaunch {id: N, onOff: true}` returns `{"code": "E_BAD_REQUEST",
"message": "order rejected"}` synchronously. Three attempts (order ids 104,
107, 108) all failed identically. Expected: toggle stored auto-launch and
return `queued: true`.

### Reproduction
1. Bind a session (`aiSession.bind`).
2. Call `autolaunch` with `onOff: true` (any positive `id`).
3. Observe synchronous E_BAD_REQUEST; no event, no state change.

### Suspected code paths
- `tools/aiplay_mcp.py:relay()` — encodes and forwards params as-is; no coercion.
- `tools/aiplay_protocol.py:encode_call()` (`~539`) — calls `_validate()` only;
  the `on_off()` coercion (`~452`) exists only in `decode_call()` (`~586`),
  which the UDS engine path does not use.
- `src/AIPlay/aiplay_orders.c:119` — `parseOrder()` reads `"onOff"` with
  `aiplayJsonGetString`.
- `src/AIPlay/aiplay_json.c:545` — `aiplayJsonGetString` returns false unless
  the node type is `aiplayJson_string`; a JSON **boolean** fails, `out->name`
  stays `'\0'`.
- `src/AIPlay/aiplay_orders.c:258` — `validateOrder()` `case
  aiplayVerb_autolaunch:` rejects empty `o->name` with `aiplayError_badRequest`.

### Root cause
Confirmed (high confidence, code + live behavior). The MCP schema declares
`onOff` as boolean; `encode_call` ships the boolean to the engine; the engine's
string-only getter leaves `name` empty; submit-time validation rejects.

### Proposed remediation
**Preferred (both sides, defense in depth):**
1. Python: in `encode_call()` (or `relay()`), coerce params for any schema rule
   with `"coerce": "on_off"` before building the wire — reuse the existing
   `on_off()` helper so `true` becomes `"on"` on the wire.
2. Engine: in `parseOrder()` handle non-string `onOff`: if `aiplayJsonGetString`
   fails, fall back to a boolean/integer read (`onOff` node type
   number/boolean) and map `>0` → `"on"`, else `"off"` (matches the existing
   "1"/"0" special-casing at aiplay_orders.c:120-124).

**Alternatives**: engine-side only (Python keeps sending booleans) — works but
leaves every other bool-order (`order_kamikaze {onOff}`, `order_alliance
{onOff}`) exposed to the same defect; or docs-only (change the MCP schema to
string) — breaks the already-published tool contract.

**Files likely to change**: `tools/aiplay_protocol.py`,
`src/AIPlay/aiplay_orders.c`, `tools/tests/test_aiplay_protocol.py`,
`src/AIPlay/tests/` (new/updated C test if a harness exists for parseOrder).

**Tests to add**: `_validate`-then-`encode_call("autolaunch", {"onOff": True})`
→ wire params contain `"onOff": "on"`; engine parseOrder unit: `{"onOff":
true}` → name "on", `false` → "off", `"on"` → "on", `1` → "on".

---

## Bug B — `order_build` without explicit `ships` fails (E_BAD_REQUEST)

**Severity**: medium (workaround known: pass `ships=[mothership_id]`)

### Symptom
`order_build {type: "ResourceCollector", quantity: 1}` (no `ships`) rejected
synchronously; the same call with `ships: [1]` accepted. The MCP tool
documentation says the builder defaults to the Mothership.

### Suspected code paths
- `src/AIPlay/aiplay_orders.c:187-192` — `validateOrder()` default case
  requires `o->numShips > 0`; `aiplayVerb_build` is not in the exempt list, so
  zero IDs → badRequest.
- `src/AIPlay/aiplay_engine.c` — build handler consumes `select` from the
  request; no Mothership fallback (checked against the tool docstring in
  `tools/aiplay_protocol.py`, which promises "default: Mothership").

### Root cause
The documented default is simply not implemented: the engine requires an
explicit builder id and the MCP layer has no fallback. The generic
`E_BAD_REQUEST` also hides the cause (docs would suggest `E_NO_BUILDER`).

### Proposed remediation
**Preferred**: engine-side — exempt `aiplayVerb_build` from the `numShips`
check in `validateOrder()`; in the build verb handler, when the selection is
empty, resolve the bound player's Mothership (backend lookup: player 0's
first Mothership) and use it as builder. Keep `E_NO_BUILDER` if none exists.
Python side already passes ships through untouched.

**Alternatives**: MCP-side fallback (relay injects `ships: [<mothership id
from last snapshot>]`) — layering violation: MCP would need match state it
doesn't own; rejected.

**Tests to add**: encode/validate test that `order_build` without `ships`
passes the protocol layer; engine-side handler test if harness permits.

---

## Bug C — snapshot order verb `order?` for COMMAND_NULL leaves attack groups "stuck"

**Severity**: critical in practice (cost 4 HeavyInterceptors — a stuck group
near the enemy base got interdicted by the enemy's entire home fleet while the
agent believed it was "busy")

### Symptom
HeavyInterceptors 267/269/272/274 showed `order: {"verb": "order?"}` across
several snapshots (ticks 71574-89515) while holding position near the enemy
base; they responded to neither attack progress nor `order_halt` surface state.
The AI dictionary documents `order?` as "unrecognized command; treat as
busy-unknown" — the agent correctly did nothing, but the ships were NOT busy:
their real state was an orphaned/idle command entry.

### Suspected code paths
- `src/AIPlay/aiplay_engine.c:232-326` (`fillShipOrder`): walks the command
  todolist, maps `todo->ordertype.order` → verb via a switch.
- `src/AIPlay/aiplay_engine.c:306-308` — `default:` emits `"order?"`.
- `src/Game/CommandDefs.h:12-22` — enumerates `COMMAND_NULL(0)`,
  `COMMAND_BUILDING_SHIP(6)` (already excluded at line 243),
  `COMMAND_MP_HYPERSPACING(10)`. The switch does NOT handle `COMMAND_NULL` or
  `COMMAND_MP_HYPERSPACING`, so both serialize as `order?`.

### Root cause
Confirmed (high confidence). The first todo containing a ship may carry
`COMMAND_NULL` (e.g. a consumed/aborted command left in the queue by the
engine's command processing — observed after an attack order against a target
that went to dock, and after `order_halt`), and `fillShipOrder` reports that
opaque `order?` instead of looking at the ship's remaining todos or reporting
idle. `order_halt` then surfaces as... nothing visible, because the top entry
still wins.

### Proposed remediation
**Preferred**: in `fillShipOrder`, treat `COMMAND_NULL` as "no order here —
keep scanning the rest of the todolist" (`continue` instead of a verdict), and
map `COMMAND_MP_HYPERSPACING` → `"hyperspace"`. `orderVerb` stays NULL when
nothing resolves → snapshot reports a clean idle, and agent-side logic
(re-task idles) recovers the ships. Additionally emit an agent-visible warning
verb (e.g. keep a distinct `"stale-order?"`)? — no: keep semantics simple, skip
silently.

**Alternatives**: none meaningful — the alternative (emitting `order?`) is the
bug itself.

**Files likely to change**: `src/AIPlay/aiplay_engine.c` (fillShipOrder).

**Tests to add**: engine fixture: ship with a null-verb first todo shows
either its next real order or null (not `order?`).

---

## Bug D — order-path retouch poisons id-map owner (hardcoded 0) + observe owner filter consequences

**Severity**: high (snapshot owner field is wrong for targets; `owner=N`
filters become garbage; this is the umbrella fix for what looked like "the
owner filter mangling asteroids/ships")

### Symptom
1. Asteroids being harvested (by ANY player) appeared with `owner: 0` in the
   snapshot (e.g. ids 128/129/179 on the opposite side of the map), even though
   no ship of mine touched them; unclaimed asteroids showed `owner: -1`.
2. An enemy ship id (17) appeared inside an `owner=0` observe right after I
   attacked it, with owner still printed as 1.
3. `observe(owner=0, sections=["resources"])` hid every `owner=-1` asteroid —
   making a still-mined field look fully depleted and triggering a rejected
   harvest order (E_BAD_ID on a "missing" asteroid).

### Suspected code paths
- `src/AIPlay/aiplay_engine.c:275` — `aiplayIdTouch(target, 0)` in the
  COMMAND_ATTACK branch of `fillShipOrder`.
- `src/AIPlay/aiplay_engine.c:287` — `aiplayIdTouch(resource, 0)` in the
  COMMAND_COLLECT_RESOURCES branch.
- `src/AIPlay/aiplay.c:163` — `e->owner = owner;` unconditionally overwrites
  the stored owner on every touch.
- `src/AIPlay/aiplay_engine.c:217,223` — the only authoritative per-tick touch
  (ships carry their real playerIndex; resources pass -1).
- `src/AIPlay/aiplay_snapshot.c:93,105` — the owner==filter comparison that
  produces the observed filtering behavior.

### Root cause
Confirmed (high confidence). `fillShipOrder` re-touches every order TARGET
(any ship's attack target, any harvester's collect target — including the
enemy AI's ships' targets, since there is one process/id map) with a hardcoded
owner 0; `aiplayIdTouchEx` overwrites stored owner with 0. Enemy ships then
read owner 0 (bug D2), and mined asteroids read owner 0 (bug D1). The snapshot
dictionary's claim that asteroid owner reflects "claimed-by" is a
rationalization of this defect, not intended behavior.

### Proposed remediation
**Preferred**: make order-path retouches NOT overwrite owner.
- In `fillShipOrder`, derive the real owner from the live object (backend
  already encodes the rule): `OBJ_ShipType` → `((Ship*)target)->playerowner->playerIndex`;
  `OBJ_AsteroidType` → `-1`; pass that to `aiplayIdTouch`. Since
  `backendShipInfo` (aiplay_engine.c:363) proves the derivation, factor it into
  a small shared helper (e.g. `backendOwnerOf(void*)`) used by both call sites.
- Optionally harden `aiplayIdTouchEx`: accept a `bool overwriteOwner` (or a
  separate `TouchNoOwner`) so syncShips stays authoritative.

**Alternatives**: snapshot-layer only (print owner from the live object rather
than the map) — treats symptoms, leaves `aiplayIdResolve`'s owner output and
any owner-based validation poisoned; rejected.

**Files likely to change**: `src/AIPlay/aiplay_engine.c`, optional `aiplay.c`.

**Tests to add**: engine fixture: ship A (player 1) attacking ship B (player
0) → B's snapshot owner stays 0; asteroid being collected by a player-1
collector → owner stays -1.

**Follow-up docs**: update
`documentation/community/ai-snapshot-dictionary.md` ("owner on asteroids is
always -1") and the observe tool help — remove the claimed-by implication.

---

## Bug E — docked fighters destroyed at own mothership with no killer (aux 0)

**Severity**: medium (lost 3 of 9 fighters; no player-visible cause; possibly user-initiated launch mid-dock)

### Symptom
Docked LightInterceptors 4, 5, 253 (docked at Mothership id 1 for repair,
health 58/110, 78/110, 71/110) died at ticks 65771/65912/66127 with
`shipDestroyed` events carrying `aux: 0` (no killer owner) and victim owner 0.
The mothership took no damage.

### Suspected code paths
- Launch path: `src/Game/LaunchMgr.c` (`clWrapAutoLaunch`,
  `clWrapLaunchMultipleShips`) and `src/Game/ShipsLaunchShipsFromShipsInsideMe`
  call chain — a launch while the bay is occupied can collide the spawned ship
  with the carrier immediately (classic HW1 hangar collision).
- Repair path in the dock/repair code — a damaged ship launched by the GAME
  PLAYER mid-repair then collides at the mouth.
- `src/AIPlay/aiplay.c:239-250` — `aiplayIdEvictUnseen` emits shipsDestroyed
  with aux 0 (helper "no killer") when ships vanish between ticks; also
  genuinely applies to engine-internal deaths without shooter attribution.

### Root cause
Low confidence: cannot disprove user input (the human player's UI can launch
docked fighters) or an engine-internal launch-attack interaction. The most
useful deliverable is observability: attribute collision deaths to a killer
(aux = the owner of what we hit where known; "collision" hint otherwise)
instead of 0.

### Proposed remediation
**Preferred**: in `aiplay.c:239-250` (and the damage/kill hooks), where
`shipDestroyed` is emitted with no owner, extend the event with an aux
constant meaning "non-combat death (stale/unattributed)" and add a snapshot
note in the doc — plus a targeted investigation note in BUGS.md for the
launch-collision hypothesis. Do NOT blind-fix the collision path in this
batch (needs live repro; risk to launch sequencing of a working game).

**Tests**: none practical in this batch; keep an open task.

---

## Bug F — snapshot `income` semantics (raw bank delta) vs documented harvest rate

**Severity**: low (agent misreads it as harvest activity; no game impact)

### Symptom
`income` swung 76358 → 0.0 → -338 → 54490 with parade-idle/dock-cycling
collectors; both banks gained ≈ +10k RU per few ticks with no visible
collectors. The AI dictionary/playbook documents income as "RU/min (own side);
≈0 = no harvesting".

### Suspected code paths
- `src/AIPlay/aiplay.c:515-561` — income sampled from raw `resourceUnits`
  deltas over a ~12s sliding window; includes build spends (negative) and any
  map/engine RU windfalls (spikes). `-1.0` while history is cold.
- `src/AIPlay/aiplay_snapshot.c:180` — writes `income` = -1.0 when paused
  else the computed rate.

### Root cause
High confidence: the implementation reports net bank delta; the docs claim
harvest rate. The observed mid-match RU windfalls (both players, ~10k every
few ticks) are a separate engine-level behavior (possibly MP autosave
restore-side resource injection / map bounty) — beyond this batch's scope to
change game economics; fix the contract, not the game.

### Proposed remediation
**Preferred**: docs-only for the stat — update
`documentation/community/ai-snapshot-dictionary.md` +
`documentation/community/ai-playbook.md`: rename the meaning to "net RU bank
delta per minute (includes builds: negative; includes any engine/map RU
injections: spikes)". Optionally emit a `harvestIncome` later if the engine
gains a per-player harvest hook.

**Tests**: n/a (docs).

---

## Risks & Considerations (batch)

- Engine changes (aiplay_orders.c / aiplay_engine.c) require a binary rebuild;
  the running match was killed for this. Match restores use
  `/aiPlayLoadMP ai-autosave-*`; ids remap after load (doc says ids invalid
  after reset/load) — relaunch flow must re-bind and re-observe.
- `fillShipOrder` skip-NULL changes behavior other spec steps (spec 003
  artifacts) documented as "order?"; update the snapshot dictionary in the
  same commit so docs and code agree.
- Python encode coercion touches the shared order encoder used by all tools —
  add the regression tests listed above before switching anything else.

## Open Questions

- [NEEDS CLARIFICATION for Bug E]: were the docked fighters launched by human
  UI input mid-repair? (engine log/graphics unavailable post-mortem)
- The mid-match ~+10k/3-min RU windfall on both sides (Bug F context) — engine
  intentional (map design?) or leak? Out of scope here; observe after fixes.