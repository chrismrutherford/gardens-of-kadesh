# Research: Twin-Core Two-Model LLM CPU Player (spec 004)

**Date**: 2026-10-23 · Resolves technical context for plan.md. All questions had
identifiable answers from repo ground truth; none remain open.

## D1. Driver runtime and placement

**Decision**: A new Python 3 package at `tools/twincore/` (peer-client process,
run with `python3 tools/twincore/driver.py`), std-lib first, pytest-tested in
`tools/tests/twincore/` following the existing harness layout.

**Rationale**: iterations 1-2 established the peer-client-as-python-tool
convention (`tools/agent.py`, `tools/agent-multi.py`, sidecar
`tools/aiplay_mcp.py`), plus a single-source registry at
`tools/aiplay_protocol.py` that MUST be imported, not duplicated. Python keeps
the driver out of the meson engine build (constitution: no second build system;
peer clients are ordinary processes).

**Alternatives**: C driver inside the engine build (rejected: couples the
strategist stack to engine code, constitution II); dedicated venv/deps
(rejected: std-lib suffices — the existing sidecar ships std-lib HTTP).

## D2. Command-surface ground truth for the validator

**Decision**: the validator enumerates acceptance from
`aiplay_protocol.REGISTRY['order_tools']` (**20 order verbs**, verified today:
move, attack, guard, dock, harvest, special, halt, build, research, formation,
tactics, launch, autolaunch, scuttle, kamikaze, parade, ru_transfer, alliance,
hyperspace, select) plus session/state methods (bind, status, snapshot,
events, pause/resume, step, save).

**Rationale**: spec 003 made this registry the single source (registry↔clWrap
1:1 check SC-001); the design doc's "~121" is an external estimate and is
superseded by the repo's own enumeration. Keeping the import single-source
keeps validator coverage automatic as the registry grows.

**Alternatives**: hand-copied tool list in the driver (rejected: drifts from
registry); treating the external spec's ~121 as authoritative (rejected:
not this repo's surface).

## D3. Transport: MCP sidecar client

**Decision**: the driver binds as an MCP HTTP client to the existing sidecar
(`http://127.0.0.1:8302/mcp`), exactly like iteration-2's agent; raw UDS
remains a diagnostic path only.

**Rationale**: spec 003 hardened the tool schemas, error envelopes (E_* sync,
aux 1..9 async), and returns documentation at this layer; reusing it gives the
validator its tri-state inputs for free and avoids any engine change
(constitution I/II). Local-only exposure rules already enforced (chmod socket,
127.0.0.1 bind).

**Alternatives**: direct UDS wire (rejected: duplicates schema knowledge the
sidecar already owns); new engine surface (forbidden by constitution).

## D4. Clocks are game-time, never wall-clock (constitution IV)

**Decision**: tick/pulse schedulers derive cadence from observed ticks
(snapshot tick deltas). A paused or single-stepped match produces no model
calls; combat-intensity gating accelerates ticks only by observed game-time
intervals. Defaults: tactical ~1s game time (difficulty presets may map to the
game's own cadences ~2.1s/~1s/~0.5s), strategist 30-90s game time.

**Rationale**: spec FR-003/FR-029; wall-clock scheduling desyncs under pause,
save/load, and replay, violating determinism.

## D5. Recorder format

**Decision (AMENDED 2026-10-23, user directive "postgres for all messages
and logging")**: the record store IS Postgres — `twincore_q.messages` on
192.168.1.152 (table `messages`: match_id, channel, sender, kind, tick,
payload jsonb, created_at envelope; one row per record).
`tools/twincore/recorder.py` is the SQL-backed store (interface unchanged:
record/scan/replay, event ring 512 in memory); `tools/twincore/queue.py` is
the exchange layer (inbox wake/note one-shot + pg_notify listeners). No
local files for messages/logging; match ids unique per driver run.
Replay: kind/tick SQL filters (SC-007). Postgres down ⇒ in-memory ring +
degraded flag; the match continues (FR-024 spirit).

**Original decision (superseded)**: append-only JSONL per kind
(fleet-telemetry precedent), sqlite rejected as heavier. The JSONL choice
was correct for iteration-1 scale; spec 004 grows into a cross-process
architecture (S1/S2/exchange/analyst) for which the shared queue-table
doubles as the log.

## D6. Model serving contract

**Decision**: both channels speak OpenAI-compatible chat-completions over
local HTTP (config URLs per channel, localhost/LAN only). S1 uses native
tool-calling (demonstrated by iteration-2 endpoints); S2 uses structured JSON
output validated against the packet schema, with at most one bounded repair
round per pulse. Message ordering is static-first (role card + directive
version prefix before the mutable window) for KV-cache friendliness per spec
FR-022.

**Rationale**: existing endpoints (e.g. the 4B/3090-rack servers) already
serve this API; endpoint URLs are configuration per constitution; size classes
are recommendations measured by the harness (spec FR-028).

**Alternatives**: per-provider SDKs (rejected: provider lock-in, new deps);
MCP servers for models (rejected: model inference is not a tool surface).

## D7. Fate of iteration-1/2 agent stacks

**Decision**: `tools/agent.py`, `tools/agent_tools.py`, `tools/agent-multi.py`
(+ json) are RETIRED inputs for this spec: not imported, not extended, not
maintained. No deprecation edits/renames ride along with spec 004 (constitution
workflow rule: no drive-by cleanups). Their specs remain historical record.
Any later removal/deprecation marking gets its own spec/decision.

**Rationale**: user directive for iteration 3 ("the existing agents are not
part of this") + repo rule against ride-along refactors.

## D8. Reuse inventory (what iteration 3 DOES inherit)

**Decision**: reused unchanged —
- `tools/aiplay_protocol.py` (registry, wire encoding, envelope semantics),
- `tools/aiplay_mcp.py` sidecar and its `guide` tool (documents served:
  `ai-playbook.md`, `ai-strategy-digest.md`, `ai-snapshot-dictionary.md`),
- engine aiplay layer (`src/aiplay*.c`, zero changes in this spec),
- the multiplayer savegame test convention (`./launch.sh cmr1`),
- pytest fixture/parity test patterns (`tools/tests/`).

The NEW doctrine sources for prompts are `documentation/community/
cpu-player-playbook.md` (A1-A7/B1-B4 behaviours, constants as data) and the
twin-core design doc §3-§6 (architecture contracts); the `guide` tool may
surface them to models but prompt assembly owns what enters context.

## D9. Determinism/anti-oscillation parameters (defaults, all configuration)

**Decision**: playbook-derived defaults for classifier and goal-stability
knobs: P0 = (seat command centre under attack/lost, defended-zone breach by
rated enemy force ≥ threshold, own battle group annihilation, RU flow
zeroed while bank high); evidence threshold for primary-enemy change =
≥2 independent digest corroborations across ≥2 pulses; minimum hold =
60s game time; digest window M = 8 pulses; rollup interval = 10 tactical
ticks; ring cap = 512 raw events in memory.

**Rationale**: matches design doc §6 failure-mode table (oscillation,
hallucination) and playbook escalation tiers; all numbers retunable data, not
laws (playbook tuning note).

## D10. Verification strategy (constitution III for live matches)

**Decision**: unit suites per module (classifier, validator, packet apply,
directive patch/sticky rules, state store, recorder, driver degradation with
stubbed model endpoints) + fixture-replay tests (recorded snapshot/event
fixtures pattern from spec 003) + scripted live verification on `cmr1` runs
for what units cannot prove (S2 pulse loop pacing, P0 wake asyncness, match
survival) — live rounds follow the launch-and-poll protocol (AGENT.md rule 2:
never block the console).

**Alternatives**: headless full-match unit tests (rejected: live simulation
isn't deterministic in wall time; harness covers it in evaluation milestone).