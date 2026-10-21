# Gardens of Kadesh — bugs seen during live AI-commanded match (2026-10-21)

**Fix status (2026-10-21, fix batch `specs/bugs/aiplay-mcp-bug-batch`):**
- #1 autolaunch E_BAD_REQUEST — **FIXED** (python encode coercion + engine bool fallback)
- #2 order_build default builder — **FIXED** (validateOrder exempts build; engine Mothership fallback already existed)
- #3 docked fighters destroyed aux 0 — **OPEN** (attributions improved in docs; engine collision path needs live repro)
- #4 order? stuck orders — **FIXED** (fillShipOrder skips COMMAND_NULL, maps COMMAND_MP_HYPERSPACING → "hyperspace")
- #5 owner filter mangling — **FIXED** (target retouches no longer hardcode owner 0; owner is ship-only)
- #6 income semantics — **FIXED** (docs: net bank delta, not harvest rate)

Match: skirmish 1v1 vs AI (player 1 = mirror AI), session bound as player 0.
Bugs observed while commanding through the `kadesh` MCP layer, with root-cause analysis where traced.

## 1. `autolaunch` MCP tool always rejected (E_BAD_REQUEST) — CONFIRMED ROOT CAUSE

- Symptom: `kadesh_autolaunch {onOff: true}` fails synchronously with `E_BAD_REQUEST / "order rejected"` every time (ids 104, 107, 108), including with the documented schema.
- Root cause: the MCP wire carries `onOff` as a JSON **boolean**. Engine side, `src/AIPlay/aiplay_orders.c` `parseOrder()` reads the param with `aiplayJsonGetString(doc, params, "onOff", out->name, AIPLAY_MAX_NAME)` (~line 119). A boolean never stringifies, so `out->name` stays empty (`'\0'`). Then `validateOrder()` (~line 258, `case aiplayVerb_autolaunch`) rejects any empty `name` with `aiplayError_badRequest`, which surfaces synchronously.
- The Python-side coercion (`tools/aiplay_protocol.py` `on_off()`, converting `True` → `"on"`) only runs in `decode_call()`, which the UDS path does NOT use — `encode_call()` validates but never coerces.
- Fix options: (a) coerce `onOff` to `"on"/"off"` in `encode_call()`/relay before it reaches the engine, (b) make `aiplayJsonGetString` stringify booleans (`true`→`"on"`, `false`→`"off"`, or read bools directly for `onOff`), or (c) in the engine, allow empty `name` for `aiplayVerb_autolaunch` and treat presence of `onOff` as "on".
- Workaround used during the match: `order_launch` after each fighter build instead of autolaunch.
- Note the sibling bool params `order_tactics` (string, unaffected), `order_kamikaze {onOff}` and `order_alliance {onOff}` likely share the same defect — `parseOrder` line 113-126 handles `onOff` the same way. Untested for kamikaze/alliance.

## 2. `order_build` without explicit `ships` fails (E_BAD_REQUEST) — CONFIRMED

- Symptom: `order_build {type: ResourceCollector}` (id 103) rejected synchronously; identical call with `ships: [1]` (Mothership id) accepted (id 106).
- The MCP tool docs promise "builder id (default: Mothership)" — the default is not applied in the handler, and the rejection is a misleading generic `E_BAD_REQUEST` instead of `E_NO_BUILDER`.
- Fix: apply Mothership fallback in the MCP/relay layer, or return the documented error code.

## 3. Docked fighters destroyed at own Mothership with no killer — UNRESOLVED

- Symptom: fighters 4, 5, 253 (LightInterceptors, docked at Mothership id 1 for repair at ~50% hp) died at ticks 65771/65912/66127. `shipDestroyed` events carried `aux: 0` (no killer attribution), `owner: 0` (victims mine). Mothership HP stayed 160000/160000.
- Suspected: launch/collision cycle (cf. `src/Game/LaunchMgr.c` `clWrapAutoLaunch` path) or a repair-loop bug; ships were damaged below ~50% when docked. Not reproduced deliberately.
- Impact: 3 fighters vanished inside our own dock, making "dock damaged fighters" doctrine untrustworthy.

## 4. Attack order leaves ships in `order?` (stuck) state — HIGH IMPACT

- Symptom: after `order_attack` (id 233) on enemy collector 16, HeavyInterceptors 267/269/272/274 showed `order: {"verb": "order?"}` in the snapshot and held position near the enemy base — not attacking, not moving, not responding until an explicit `order_halt`.
- The AI snapshot dictionary defines `order?` as "unrecognized command (treat as busy-unknown)" — i.e. the order verb that reached the engine wasn't in its verb table for that ship context.
- Impact: the stuck group sat within interdiction range while the enemy AI dispatched its entire home fleet (13 ICs + HCs + 2 ICFs + MissileDestroyer) onto them; all 4 were lost in seconds. Stuck orders near an enemy are fatal.
- Follow-up: hunt where "order?" strings can be emitted; make the MCP reject or surface unresolved verbs; consider a watchdog that re-halts ships stuck in `order?` > N ticks.

## 5. `observe` `owner` filter is unreliable — CONFUSING DATA

- Filtering `sections: ["resources"]` with `owner: 0` silently dropped every unclaimed (`owner -1`) asteroid, making a nearby field look fully depleted.
- Ships/asteroids whose `owner` flips (harvest-target claiming) appeared under the wrong filter: enemy collector id 17 listed inside an `owner=0` pull (tick 33824) with owner 1 semantics; asteroids id 128/129/179 on the far side of the map showed `owner 0` inside an `owner=0` filter despite no ship of mine nearby.
- Suggest documenting the owner semantics for asteroids (claimed-by-harvester?) or removing the filter from the MCP tool.

## 6. `income` snapshot stat is garbage; RU banks balloon without visible harvesting

- Symptom: `income` swung 76358/min → 0.0 → -338/min → 54490 → 3545 with collectors parade-idle or dock-cycling; both players' RU banks jumped ≈ +10k every few ticks (92k → 142k → 234k) with no visible collectors near those volumes. The snapshot dictionary says income ≈ 0 means nobody is harvesting — that rule never matched observed state.
- Suggest: audit how `income`/`resourceUnits` are computed in the snapshot path; likely a per-tick map bounty or unit mismatch (RU/min vs RU/tick) or debris auto-collection.

## 7. Unattributed chip damage on fighters far from any shooter — UNRESOLVED

- Symptom: fighters taking damage (110→58, 110→89, 110→71) during a disengagement path where no armed enemy ship was within weapons range (only unarmed collectors nearby). Possibly collision damage with asteroids/debris, but no attribution in events (`shipDestroyed` only fires on death; damage events don't exist).
- Suggest: consider surfacing a last-damage-source on ships in the snapshot, or at least document collision-only damage.

## 8. Snapshot ids go stale within a poll cycle (E_BAD_ID aux 2)

- Symptom: `order_harvest` (id 121) for asteroid 171 rejected `E_BAD_ID` seconds after the snapshot showed it (it had been depleted/removed between poll and submit).
- Design hazard: 24km-wide field depletion between polls makes any collector retask race-y; snapshot lacks a "deplete rate" or diff view. Consider documenting this hard and/or allowing collectors to auto-re-target nearby unclaimed rocks (they did self-retarget after explicit depletion — inconsistent).

## 9. Enemy research tree view does not reflect what the enemy actually has — MISLEADING INTEL

- Symptom: `techTree.researched` seen for the enemy listed only up to Chassis-chain basics, yet the enemy built MassDrive1Mt-tier hulls (StandardDestroyer 319, MissileDestroyer 325) and IonCannonFrigates without those techs ever appearing as researched in any snapshot.
- Impact: intel-based doctrine ("read enemy build intent from the snapshot") understates enemy tech. Also `dps` for enemy IonCannonFrigate read 4.17 — implausible for that hull class; dps display for enemy ICFs appears wrong or unpowered.

## 10. Chassis research mutates ResearchShip HP multiples — UNDOCUMENTED SCALE

- Symptom: ResearchShip 9 HP went 7500 → 15000 → 22500 → 30000 as chassis techs landed (no docs describe a per-chassis HP multiplier of this size; 4x hp for a civilian lab ship).
- Not necessarily a bug, but undocumented and surprising; worth a note in the snapshot dictionary.

## Low-priority / observations

- Build times are very long (~3500 ticks ≈ 10 min) for collectors/research ships — feels slower than the playbook implies; combined with RU windfalls, the economy meta is unlike the documented one.
- `order_launch` returns `E_NO_BUILDER` (aux 4) when nothing is docked — expected, but the error name is misleading (nothing built, not no builder).

---

**Session summary at time of writing:** bind ok (player 0), economy + research chain driven via MCP (MassDrive1Kt/Chassis1/MassDrive10Kt/TargetingSystems/Chassis2/MassDrive100Kt/Chassis3 done, PlasmaWeapons + IonWeapons queued), first economic raid killed 1 enemy collector with 0 fighter losses, second and third raids broke off safely when the enemy massed at home. Losses: 4x HeavyInterceptor (enemy interdiction of stuck group), 3x LightInterceptor (unknown dock deaths).