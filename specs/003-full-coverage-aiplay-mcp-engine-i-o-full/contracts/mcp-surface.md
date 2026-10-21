# Contract: MCP Surface ↔ UDS Wire ↔ clWrap Funnels

Base: MCP over HTTP `http://127.0.0.1:8302/mcp` (JSON-RPC tools/call; also
raw `tools/<name>` passthrough). Underlying: UDS line
`/tmp/homeworld-aiplay.sock`, envelope `{id, method, params}` →
`{id, result}|{id, error:{code,message}}`.

## Session / state / time (METHOD_FOR_TOOL)
| MCP tool | UDS method | params → result |
|---|---|---|
| bind_session | aiSession.bind | playerIndex≥0 (default 0) → {playerIndex, sessionId, mapName} |
| game_status | aiSession.status | — → {state, boundPlayerIndex, sessionId, mapName, matchStatus} |
| observe | state.snapshot | sections[,], owner→ {full snapshot per data-model} |
| get_events | state.events | — → {events[]} (≤256, newest first) |
| pause / resume | time.pause / time.resume | — → {paused} |
| step | time.step | ticks≥1 (default 1) → {stepped, tick} |
| save_game | game.save | name≤64 (default aiplay-autosave-<tick>) → {saved} |

## Orders (order.<verb>), all params carry integer `id` echoed in events
Existing tools (schema tightened per data-model):
order_move{ships,target[3]}, order_attack{ships,target:shipId},
order_guard{ships,target:shipId}, order_dock{ships,dockWith},
order_harvest{ships,target:resourceId}, order_special{ships,target?},
order_halt{ships}, order_build{type,quantity?,ships?},
order_research{tech}, order_formation{ships,formation},
order_tactics{ships,tactics∈[aggressive,neutral,evasive]},
order_launch{ships}, autolaunch{onOff}, order_scuttle{ships}, order_select{ships}.

New verbs (FR-001) — sidecar tools + backend dispatch:
| tool | params | funnel / notes |
|---|---|---|
| order_kamikaze | ships | clWrapSetKamikaze(layer, select) |
| order_parade | ships, onOff? (default on) | clWrapSetMilitaryParade |
| order_ru_transfer | to(player)*, amount* | clWrapRUTransfer(from=bound, to, amount, flags=0); MP-semantic |
| order_alliance | player*, onOff* | clWrapSetAlliance(type[on/off], bound, player); MP-semantic |
| order_hyperspace | ships*, target[3]* | clWrapMpHyperspace(layer, select, from=lead pos, to); MP funnel |

Error envelope unchanged: sync codes E_*; async orderRejected aux=enum 1..9.

## Invariants
- tools/aiplay_protocol.py owns: TOOLS registry (generated), wire encoding
  (MCP call ↔ {id,method,params}), envelope-id vs order-id namespace split,
  onOff coercion, result unwrapping.
- Read-only tool set (observe, get_events, game_status) never mutates state
  (FR-011); all order tools mutate through the single queue.
- Coverage check: tools list + clWrap funnel registry must be 1:1 (SC-001).