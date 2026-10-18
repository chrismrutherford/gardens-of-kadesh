# Interface Contract: AI Play Transport & Tools (Features 001+002)

Local-only. Game process listens on a **Unix domain socket** (path printed to a file and console
at startup when enabled). The MCP sidecar runs an **HTTP MCP server bound to 127.0.0.1** (default
port 8000, `--port` override) using the standard MCP Streamable HTTP binding, and relays to the
game over the UDS. The engine itself never opens a TCP port; the sidecar never binds to anything
but loopback.

Envelope: JSON-RPC 2.0 style — `{"id", "method", "params"}` → `{"id", "result"}` or
`{"id", "error": {"code", "message"}}`. All tool calls idempotent-safe against tick races:
state reads are taken at a tick boundary or while paused.

## Session methods

### `aiSession.bind` → `{playerIndex, sessionId, mapName}`
Binds ONE player to the AI (default 0), neutralizes the built-in CPU Player for it.
Error if already bound or bound index dead/absent.

### `aiSession.status` → `{state, paused, tick, timeElapsed, matchStatus:{running,winner,perPlayer:[{index,alive,won,lost}]}}`
## Observation

### `state.snapshot` → snapshot JSON (data-model.md "Snapshot" section, all sections)
Fields (guaranteed present):
```jsonc
{
  "tick": 0, "timeElapsed": 0.0, "paused": false,
  "players": [ {"index":0, "isAI":false, "race":"Kushan", "resourceUnits":8000,
                "alive":true, "lost":false} ],
  "ships":   [ {"id":42, "owner":0, "type":"Interceptor", "race":"Kushan",
                "pos":[x,y,z], "vel":[x,y,z], "health":100.0, "healthMax":100.0,
                "order":{"verb":"move","target":[x,y,z]}} ],
  "resources": [ {"id":7, "type":"Asteroid", "pos":[x,y,z], "value":1500} ],
  "buildQueue": [ {"type":"Interceptor", "progress":0.25} ],
  "research":   [ {"tech":"PlasmaBomb", "progress":0.0, "labsIdle":2} ],
  "matchStatus": {"running":true, "winner":null, "perPlayer":[{"index":0,"alive":true,"lost":false}]}
}
```
Filters for large fleets: `{"sections":["ships"], "owner":0}`. Deterministic at paused ticks
(FR-002/SC-005).

### `state.events` → `{events:[{type, tick, data...}]}`   // FR-003, FR-007 acks
Event types: `matchStarted`, `matchOver{winner,perPlayer}`, `orderApplied{id}`,
`orderRejected{id,reason}`, `shipDestroyed{id,owner,type,killer?}`.

## Orders (FR-005–FR-007; rejected → descriptive error, no state change)

All verbs take `{"ships":[ids...]}` plus verb fields. Aliases of homeworld vocabulary:
| Tool call / verb | Params | Notes |
|---|---|---|
| `order.move` | `target:[x,y,z]` | same as human move order |
| `order.attack` | `targets:[shipIds...]` | also accepts derelict ids |
| `order.guard` | `target:shipId` | protect behaviour |
| `order.dock` | `dockWith:shipId` optional | includes harvesting dock |
| `order.harvest` | `target:resourceId` | collector must be in ships[] |
| `order.special` | `targets:[ids...]`, optional `targetId` | special abilities |
| `order.halt` | — | |
| `order.build` | `type:"Interceptor", race?, quantity` | build-capable vessel(s) in ships[] |
| `order.research` | `tech:"name"` | labs availability validated engine-side |
| `order.formation` | `formation:"delta"\|"broad"\|"claw"\|"sphere"\|"delta3d"` | |
| `order.select` | — | sets the layer-side selection used to group subsequent verbs; groups optional |

Standard error codes: `E_NO_SESSION`, `E_BAD_ID` (unknown/foreign/dead ship or resource),
`E_UNAFFORDABLE`, `E_NO_BUILDER`, `E_MATCH_OVER`, `E_QUEUE_FULL`.

## Time control (FR-011, FR-012)

| Method | Effect |
|---|---|
| `time.pause` / `time.resume` | toggles `universePause`; HUD pause surface remains visible |
| `time.step` | advances exactly N (default 1) simulation ticks while held paused |
- Orders submitted while paused are accepted and queued (processed on resume/step) — the spec's
"think then act" loop. No method forces the spectator to pause; default flow is real-time.

## Invariants (checked at contract-test level)

1. Snapshot ⇔ screen agreement: every ship in a snapshot taken during pause has an on-screen
   counterpart; resource counts equal HUD display.
2. Rejected orders mutate nothing (byte-identical re-snapshot).
3. Pause ⇒ repeated `state.snapshot` byte-identical.
4. Disconnect/reconnect: `aiSession.bind` of the same player reattaches; no rollback; ids
   regenerated on reconnect (AI must re-observe).
5. Transport is local-only; no TCP listener ever opened.