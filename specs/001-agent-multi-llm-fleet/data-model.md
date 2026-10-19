# Data Model: agent-multi

## Artifact files (shared state)

Directory: `/tmp/aiplay-multi-<session_epochns>/`
All writes atomic (`tempfile` + `os.replace`).

### strategist.json / economist.json (identical envelope, different payload)

```jsonc
{
  "role": "strategist",          // "economist"
  "generatedAt": 1792359999,     // epoch seconds
  "tick": 152341,                // tick the peer observed
  "summary": "one-line directive",
  "payload": {
    "directives": ["..."],       // <= 10 short imperative lines
    "researchPath": ["Chassis1","MassDrive1Kt"],   // strategist
    "attackTiming": "now|hold|retreat",            // strategist
    "buildQueue": [{"type":"LightInterceptor","qty":8}], // economist
    "notes": "freeform reasoning tail (<= ~2k chars)"
  }
}
```

### analyst.json

```jsonc
{
  "role": "analyst", "generatedAt": ..., "tick": ...,
  "payload": {
    "threats": [{"type":"shipDestroyed","id":66,"owner":0}],
    "reactions": ["<= 6 short imperative lines"],
    "notes": "..."
  }
}
```

### critic.json

```jsonc
{
  "role": "critic", "generatedAt": ..., "tick": ...,
  "payload": {
    "objections": [{"orderId": 601, "reason": "outnumbered 1:4, no escort"}],
    "advice": ["<= 5 lines"],
    "notes": "..."
  }
}
```

### orders.json (commander → critic)

```jsonc
{
  "role": "commander",
  "updated": 1792359999,
  "orders": [ // ring buffer, last ~20
    {"round": 41, "tool": "order_attack", "args": {...},
     "result": "ok|queued|E_*", "tick": 152341}
  ]
}
```

## Validation rules

- Readers (commander/critic/peers) MUST tolerate: missing file (→ no
  artifact), stale `generatedAt` (> 60 s → stale/excluded), malformed JSON
  (→ ignored), unknown `role` (→ ignored).
- Writers (peers) MUST bound `notes` (~2k chars) and directive counts so a
  single artifact stays small; commander prompt merge caps total peer text.
- Peer LLM responses may arrive without the JSON envelope → publish the
  raw text as `{"summary": <first 200 chars>, "payload":
  {"directives": [], "notes": <full text>}}` rather than dropping it.

## Entities and state transitions

- **Loop state** (per role): `attaching → running ↔ backoff → idle(matchOver)
  → running(matchStarted)` — transitions derived from observe results.
- **Commander round pipeline** (unchanged semantics):
  `observe → merge peers → events → LLM → guard_call → dispatch → log`.
- **Session id**: single epoch id shared by all loops, file names and logs.