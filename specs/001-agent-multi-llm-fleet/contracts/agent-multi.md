# Contract: agent-multi

## CLI contract (`tools/agent-multi.py`)

```text
usage: agent-multi.py [--llm-strategist URL] [--llm-analyst URL]
                      [--llm-economist URL] [--llm-critic URL]
                      [--mcp URL] [--player N] [--interval S]
                      [--max-rounds N] [--stale-secs S] [--session-id ID]
defaults: 4 role URLs -> 192.168.2.72:9931..9934 (strategist..critic),
          --mcp http://127.0.0.1:8302/mcp, --player 0,
          --interval 4.0, --stale-secs 60
```

Exit codes: 0 clean stop (max rounds / matchOver / SIGINT), 1 fatal startup
(errors before looping: bad sidecar URL, no models).

## Role contract (behavioral)

| Role | Server default | Reads | Writes | Tool calls allowed |
|---|---|---|---|---|
| commander   | (sidecar mcp arg) | artifacts | orders.json | all (sole binder) |
| strategist  | :9931 | observe, events | strategist.json | NONE |
| analyst     | :9932 | observe, events (fresh slice) | analyst.json | NONE |
| economist   | :9933 | observe, techTree | economist.json | NONE |
| critic      | :9934 | observe, events, orders.json, peer artifacts | critic.json | NONE |

Invariants (test-enforced):
1. Peers never pass a `tools` array in chat completions requests.
2. Peers never call `bind_session` or `order.*` via the sidecar.
3. All artifact writes are atomic replaces of whole JSON documents.
4. Commander prompt merge contains each artifact's `generatedAt` and drops
   those older than `--stale-secs`.

## Shared state contract

Per data-model.md; artifact dir = `/tmp/aiplay-multi-<session-id>/`. Files
are the ONLY cross-loop interface (no in-memory coupling between roles
beyond the shared process handles created at launch).

## Log contract

`/tmp/aiplay-multi-<session>/<role>.log` per loop + the commander's
telemetry path (inherits agent.py flag). Line format identical to agent.py
(`[HH:MM:SS] [round N] ...`), plus `PERF` lines (`server-rounds`,
`round-seconds`) consumed by SC-001/SC-002 checks in quickstart.