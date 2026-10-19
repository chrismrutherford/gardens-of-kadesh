# Research: agent-multi

## R1: Threading model — threads vs processes

**Decision**: Python threads in one process (peer + commander), each peer
loop with its own `urllib` client class instance.
**Rationale**: five loops are I/O bound (LLM HTTP calls); the GIL has no
impact on concurrent HTTP waits. One process keeps shared session id, logs,
and teardown trivial. Daemon threads; SIGINT in the main thread stops all.
**Alternatives**: separate processes (better fault isolation, but 5× log
wiring + no shared session id in-memory); asyncio (rewrite of the proven
agent loop for zero gameplay benefit).

## R2: Shared state — how the commander reads peer artifacts

**Decision**: per-role JSON artifact files under
`/tmp/aiplay-multi-<epoch-id>/`: `strategist.json`, `analyst.json`,
`economist.json`, `critic.json`, `orders.json` (commander-written).
Written via `os.replace` (atomic on same fs). Commander does a plain file
read per round (µs) — no LLM call, no locks; readers tolerate a torn-free
atomic replace. Artifact schema owned by data-model.md.
**Alternatives**: sqlite (heavier, no benefit at 4 writers/1 reader);
socket pub/sub (more moving parts for no durability need — artifacts must
survive nothing but the session).

## R3: Reuse vs fork of the agent loop

**Decision**: `agent_multi.py` imports from `tools/agent.py` (`LLM`, `MCP`,
`compress_snapshot`, `guard_call`, TOOLS, nags list shape). Where agent.py
uses module-level globals (`LOGFILE`), wrap with an `Agent`-style class or
pass context explicitly. Peer prompts are new module constants.
**Alternatives**: copy-paste fork (drift bugs, double maintenance) —
rejected.

## R4: Peer loop pacing ("flat out")

**Decision**: each peer runs back-to-back: after an LLM response → publish
artifact → immediately observe fresh state → next round. No sleep except
exponential backoff (1 s → 30 s cap) on connection failures; on
`E_NO_SESSION`/match-not-running, 5 s poll like the commander's rebind wait.
**Rationale**: SC-001 demands ≥1 round/30 s per server — back-to-back beats
that by an order of magnitude on local GPUs.
**Alternatives**: fixed 10 s cadence (wastes server capacity; still
compliant but slower to react) — rejected.

## R5: Peer prompt design & context discipline

**Decision**: each peer gets a fixed role system prompt + rolling context:
full snapshot only for strategist/economist (compress snapshot for analyst +
critic), last N=3 peer-round summaries for continuity, never the commander's
chat history. Peer responses are freeform Markdown; only the JSON envelope
(schema in data-model.md) is enforced — malformed payload → discard +
retry (edge case in spec).
**Rationale**: peers reason better with long, stable, self-owned contexts
(best KV-cache reuse per server); no `tools` array is passed to peers at
all — enforcement of read-only by construction (plus FR-010 runtime check:
a tool-looking response is logged and dropped).

## R6: Match reset & matchOver handling

**Decision**: peers detect reset via `tick` decrease in observe (same rule
as agent.py) and via `matchStatus.running == false`. On reset: clear rolling
context, publish a fresh minimal artifact; on matchOver: publish final
summary, then poll every 30 s until running again (no error spam).
Commander behavior unchanged from agent.py.

## R7: Launch & failure modes

**Decision**: `agent-multi.py` CLI: `--llm-strategist/--llm-analyst/
--llm-economist/--llm-critic` (defaults 9931-9934) + forward-compatible
passthrough of the single-agent flags (`--mcp`, `--player`, `--interval`,
`--max-rounds`). Each loop logs to `/tmp/aiplay-multi-<id>/<role>.log`.
A dead peer server: loop backs off, never crashes the process; commander
independent (US1 scenario 1). Ctrl-C: main thread joins peers with 2 s
grace.
**Alternatives**: supervisor/health-check daemon — rejected (YAGNI).

## R8: Critic input — orders without mutating

**Decision**: commander writes `orders.json` (append-trimmed ring of last
~20 issued orders with results + rejections from events). Critic reads
orders.json + latest strategist/economist artifacts + compressed snapshot;
publishes `critic.json` objections. Commander merges objections into the
next round prompt (visible, non-binding).
**Alternatives**: critic subscribes to engine events directly (duplicate
event consumption logic) — rejected; reuse commander's event parse instead.