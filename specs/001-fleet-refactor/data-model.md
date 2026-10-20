# Data Model: fleet refactor (telemetry + status)

## rounds-<role>.jsonl (append-only, one line per round)

| Field | Type | Notes |
|---|---|---|
| role | str | commander / strategist / analyst / economist / critic |
| round | int | loop-local round number |
| tick | int | engine tick observed (null if unreachable) |
| at | epoch s | wall clock of round completion |
| latency_s | float | observe→dispatch→result total |
| actions | array | [{tool, args, result}] — commander orders; peers publish summary |
| error | str? | first error of the round (exception or E_* result) |
| queue_edits | {accepted, rejected} | peers only |

Writers: each loop appends one line (buffered write + flush; tolerate
OSError by logging + skipping — never crash a loop).

## fleet-status view (rendered, derived)

Per role: round, age = now - last event `at`, rate = rounds over last 5 min
window, last error, artifact freshness (from artifact `generatedAt`),
commander last 3 actions, queue (id/text/state) from instruction-queue.json.
STALE threshold 60 s (role or artifact).

## FleetContext (explicit wiring object)

```jsonc
{"store": ArtifactStore,          // session dir artifacts
 "queue": InstructionQueue,      // shared instruction queue
 "roles": [{name, llm, prompt}], // peer configs
 "opts": {interval, stale_secs, max_rounds, queue_cap, stall_rounds},
 "logdir": "/tmp/aiplay-multi-<id>"}
```
No env vars. Single-agent mode constructs a minimal FleetContext (no
queue/roles) and runs the same pipeline.