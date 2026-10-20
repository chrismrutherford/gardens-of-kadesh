# Data Model: peer-instruction-queue

## instruction-queue.json (current state; atomic replace)

```jsonc
{
  "role": "queue",
  "version": 7,                  // bumped on every accepted edit set
  "instructions": [              // priority order = array order
    {"id": 3, "text": "build 2 Ion Cannon Frigates",
     "state": "active",          // pending|active|done|failed|stalled
     "by": "strategist", "at": 1792441234,
     "notes": ["round 41: ordered hull 1"]}
  ]
}
```

## instruction-history.json (append-trimmed ring, ≤100)

```jsonc
{
  "role": "queue-history",
  "entries": [
    {"op": "add"|"remove"|"move", "id": 3, "priority": 1,
     "text": "...",              // for add
     "by": "strategist",
     "at": 1792441234,
     "accepted": true,
     "reason": "duplicate id"    // when rejected
    }
  ]
}
```

## Peer edit payload (inside the peer's normal artifact)

```jsonc
"edits": [
  {"op": "add",  "text": "build 2 Ion Cannon Frigates", "priority": 1},
  {"op": "remove", "id": 7},
  {"op": "move",  "id": 5, "priority": 1}
]
```

## Validation rules (InstructionQueue.apply)

- text: 8..200 chars, one imperative line
- priority: 1..(len+1) for add; 1..len for move
- id: must exist for remove/move; add assigns a fresh monotonic id
- duplicate concurrent adds: two adds same round → both queued, order by
  author arrival; conflicts on SAME id same round → newest writer wins
- queue cap 12: further adds rejected "queue full"
- history: every op (accepted or rejected) recorded with reason
- match reset: queue cleared (history keeps entries); matchOver: frozen

## States and transitions

- pending (queued behind) → active (topmost, executor working)
- active → done (executor reports success) | failed (engine error /
  impossible) | stalled (no progress ≥ 10 executor rounds)
- done instructions stay visible for one peer cycle, then pruned (op
  queue prune, not peer-required)