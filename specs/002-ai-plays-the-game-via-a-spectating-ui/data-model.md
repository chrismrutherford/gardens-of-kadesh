# Data Model: AI Plays + Spectating UI (Feature 002)

Entities the AI-play layer maintains or observes. Engine structs are referenced, not duplicated.

## Layer-owned entities

### aiPlaySession (singleton)
| Field | Type | Notes |
|---|---|---|
| state | enum | `disabled` → `lobby` → `playing` → `matchOver` |
| boundPlayerIndex | uword | exactly one; default 0 |
| aiPlayerNeutralized | bool | built-in AIPlayer detached for bound player |
| orderQueue | ring buffer of aiOrderRequest | drained at tick boundaries |
| shipIdMap | hash: Ship* → uint64 id | stable ids across snapshots; entries evicted on ship death |
| eventLog | FIFO | lifecycle + order-ack events for FR-003/FR-007 |

Validation: `boundPlayerIndex < universe.numPlayers`; session may bind while
`gameIsRunning` is true (spectated skirmish already started) but not rebind mid-match.

### aiOrderRequest
| Field | Type | Validation |
|---|---|---|
| id | uint64 | unique, echoed in ack/error |
| verb | enum | one of the contract verbs (see contracts/) |
| shipIds | uint64[] | every id must resolve in shipIdMap and be owned by bound player |
| target | union coord / shipId / resource / shipType / researchTech | per-verb rules from contracts/ |
| status | enum | `queued` → `applied` \| `rejected(reason)` |

Processing: drained at the start of a simulation tick inside `univUpdate`'s command window;
`applied`/`rejected` recorded to eventLog and returned to the AI on the next poll. Orders never
processed mid-tick (no tearing).

### Snapshot (derived, not stored; serialized per request)
| Section | Source (engine) |
|---|---|
| tick, univTimeElapsed | `universe.univUpdateCounter`, `universe.totaltimeelapsed` |
| paused | `universePause` |
| players[] | `universe.players[]`: playerIndex, isAI, resourceUnits, playerState, race, alliedness |
| ships[] | walk `universe.ShipList`: id, owner, shiptype, shiprace, pos (x,y,z), health, velocity, currentCommand |
| resources[] | walk `universe.ResourceList`: id, type, pos, RU value |
| buildQueue[] / research[] | bound player's build/research state |
| matchStatus | gameIsRunning, wintime-based winner computation, per-player win/lose |

## Observed engine entities (read-only references)
- **Ship**: `SpaceObjShip` in `src/Game/SpaceObj.h:1220–1362` (position, type, owner, health).
- **Player**: `src/Game/Universe.h:104–127` (RUs, state, race, research).
- **CommandLayer / CommandToDo**: `src/Game/CommandLayer.h` (order funnel + current orders).
- **Universe**: `src/Game/Universe.h:137+` (lists, tick counter, pause flag, players).

## State transitions
```
session: disabled --enable-ai-play--> lobby --match start--> playing --victory/defeat--> matchOver
order:   queued --tick boundary--> applied | rejected(reason)   (rejected is terminal; reported)
ship id: generated on first sight --ship death--> evicted (id reported as invalid after eviction)
```

Determinism note: two sessions on the same seed with the same queued order stream produce
identical snapshot sequences — the queue is processed in a deterministic tick slot (start of
`univUpdate`, before `aiplayerUpdateAll` at `UnivUpdate.c:7512` and `clProcess` at `:7521`).