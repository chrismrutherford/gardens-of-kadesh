# Contract: Program packet schema

Feature: `005-v4-strategist-driven-llm-cpu-player-s2-d` | Date: 2026-10-23

The contract between S2 (model output) and `program.py` validation, and
between the pulse loop and the executor. JSON only. Whole-packet semantics:
any violation ⇒ the entire packet is rejected (`ProgramError.reason` set),
nothing applies, the previous version stays authoritative (FR-008).

## Packet shape

```json
{
  "version": 7,
  "issued_tick": 1201,
  "posture": "defensive-expansion; keep 2 collectors on roids, frigate pair forming",
  "arc": "opening→econ ramp, no contact yet. research MassDrive10Kt accepted. enemy unseen, intel says frigates. plan: interceptors + chassis research, hold near base.",
  "s1_context": {"max_exchanges": 2, "ticks": 600},
  "production": [
    {"id": "p1", "verb": "order_build",       "params": {"type": "ResourceCollector", "quantity": 1}},
    {"id": "p2", "verb": "order_build",       "params": {"type": "LightInterceptor", "quantity": 4}},
    {"id": "p3", "verb": "order_research",    "params": {"tech": "MassDrive10Kt"}, "once": true}
  ],
  "tasking": [
    {"id": "t1", "verb": "order_attack", "ships": [104, 105], "target": 881},
    {"id": "t2", "verb": "order_guard",  "ships": [104], "target": 12},
    {"id": "t3", "verb": "order_move",   "ships": [105], "pos": [-1200, 0, 640]},
    {"id": "t4", "verb": "order_tactics","ships": [104,105], "tactics": "aggressive"}
  ]
}
```

## Validation rules (checked WHOLE, in this order)

1. **Top-level**: object; `version` int > last accepted; `issued_tick` int
   ≥ 0; `posture` string ≤ 400 chars or absent; `production`/`tasking`
   lists (≤ 32 items each) or absent (empty program = valid: reflexes only);
   `arc` string any length or absent (over `v4.arc_chars`, default 600 →
   NOT rejected: the DRIVER length-clamps it with a verdict, FR-021);
   `s1_context` object `{max_exchanges: int 0–8, ticks: int 60–100000}`
   or absent — malformed SHAPE (wrong keys/types/ranges) rejects the
   packet (schema). Charter instructs S2 to rewrite the arc every pulse.
2. **Items**: `id` unique across the packet, `[a-z0-9]{1,8}`;
   `verb` in the enum per item kind; `params` verb-shaped
   (`order_build`: `type` nonempty string, `quantity` int 1–10 or absent;
   `order_research`: `tech` nonempty string; tasking verbs: `ships` = list
   of positive ints 1–64 entries; `target` positive int or absent; `pos`
   3 numbers or absent (finite, |v| ≤ 100000); `tactics` enum or absent);
   `priority` int or absent; `once` bool or absent.
3. **Liveness (executor-time, NOT packet-time)**: `ships`/`target` ids are
   checked against a fresh ledger snapshot at each application attempt;
   unrecoverable → item `unrecoverable` with re-issue hint in ProgramStatus
   (stale-ids edge case).
4. **No allied targets**: a tasking verb whose `target` resolves to an
   allied player's ship is `blocked` by the validator (allied protection).

## Error shape

```json
{"error": "schema", "reason": "tasking t3: pos must be 3 finite numbers",
 "version": 7}
```

`program.validate_packet(raw: str|dict, last_accepted_version: int) ->
Program` or raises `ProgramError(reason)`. Malformed model output (not
JSON, or wrong shape): ≤1 bounded repair attempt per pulse (re-ask with the
reason appended); second failure aborts the pulse, last program stands,
failure recorded (Edge Cases).

## Versioning & application

- Accepted packets apply WHOLE at the next tick boundary, atomically:
  active program pointer swaps only after validation passes.
- In-flight commands from the old program are left running unless the new
  program tasking covers those ships (US3-AS2).
- `version` monotonic; duplicates/regressions rejected with reason fed to
  S2 next input (FR-008).
- Full history recorded: each apply/reject is a `program` trail row;
  the pulse input carries the previous program with per-item statuses.