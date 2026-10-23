# Contract: Driver Runtime, Configuration & Evaluation (spec 004)

Operational contracts: where code lives, its configuration surface, module
responsibilities, degradation test matrix, replay/harness entry points.

## 1. Module layout & responsibilities (`tools/twincore/`, package)

| Module | Owns | Key inputs → outputs |
|---|---|---|
| `driver.py` | entry, wiring, seat lifecycle, degradation ladder | CLI/config → running driver |
| `config.py` | config schema+loader, budgets, cadences, preset resolution | JSON config → typed config |
| `client.py` | session client (bind, observe, events, commands) via MCP sidecar; re-observe/id-surge policy | MCP HTTP → snapshots/events/CommandRecords |
| `events.py` | raw event consumption → deltas vs last-seen | events → EventDelta stream |
| `digest.py` | importance classifier (P0/P1/P2), rollups, digest builder | deltas → rollups/digests; P0 → wake signals |
| `state_store.py` | canonical ledger (fleet, economy, knowledge, blobs, alliances, orders) | snapshots/deltas/events → ledger |
| `validator.py` | tri-state validation over registry verbs (protocol §3) | proposals → ValidationResult |
| `economy.py` | deterministic resource manager (opening, ratios, 30/30/40 budget policy) | ledger → economy command proposals |
| `directive.py` | directive schema, patch validation/versioning, sticky-goal gate, boot default | patches → directives; audit lines |
| `packet.py` | control-packet schema validation + apply order + history ops | S2 output → applied changes/rejects |
| `s1tick.py` | S1 tick loop: context assembly (static-first), tool-call parsing, error feedback | ledger/directive/window → command proposals |
| `s2pulse.py` | S2 pulse loop: packet input building, emission handling, question queue | digests/ledger → packets |
| `llm.py` | model endpoint clients (chat completions, tool calling, JSON mode, retry budgets, usage metering) | config URLs → completions + usage |
| `recorder.py` | append-only JSONL recorder, per-kind files, replay filters | everything → files |
| `replay.py` | offline extraction: kind/tick filters, decision-trace pretty-print | recorder files → segments |
| `harness.py` | series runner + per-series report generator (SC-006 measures) | config → reports |

Game/engine code and the MCP sidecar are NOT modified by this spec. Old agent
stacks (`agent.py`, `agent_tools.py`, `agent-multi.*`) are retired inputs —
not imported (research D7).

## 2. Configuration contract (JSON, `tools/twincore/configs/`)

```jsonc
{
  "seat": {"player_index": 0},
  "transport": {"mcp_url": "http://127.0.0.1:8302/mcp"},
  "channels": {
    "s1": {"url": "...", "model": "", "max_context": 8192, "max_output": 200, "temperature": 0.2},
    "s2": {"url": "...", "model": "", "max_context": 24576, "max_output": 2048, "temperature": 0.4}
  },
  "cadence": {"tick_ticks": 30, "combat_gate_multiplier": 3, "pulse_ticks": 1800, "p0_wake": true},
  "digest": {"window_pulses": 8, "rollup_interval_ticks": 300, "p0_p1_p2_rules": {...}},
  "sticky": {"evidence_min": 2, "hold_ticks": 1800},
  "budgets": {"digest_min": 300, "digest_max": 800, "s2_out": 2048, "s1_out": 200},
  "difficulty": "intermediate | advanced | beginner | custom",
  "economy": {"opening": {...}, "ratios": {...}},          // deterministic module tuning
  "recorder": {"dir": "output/twincore-recordings/<match>"},
  "strategist": {"enabled": true}                          // false ⇒ S1-only mode
}
```

Defaults derived from research D9; difficulty presets map to the game's own
difficulty data (cadences ~2.1s/1s/0.5s; advanced-only play flags) — one
schema, parameters only (FR-026). Endpoint URLs are configuration only
(constitution security: no network exposure beyond localhost/LAN model server).

## 3. LLM channel contract

- OpenAI-compatible chat-completions over HTTP; per-channel URL/model.
- S1: native tool-calling; tools = curated registry verb descriptions;
  static-first message section order (role card, directive) before mutable
  window; output budget enforced by truncation-before-send never mid-parse.
- S2: structured JSON output; schema validation by `packet.py`; ≤1 bounded
  repair attempt per pulse; usage metering per channel (`usage` lines).

## 4. Evaluation harness contract (US5 / SC-006)

`python3 tools/twincore/harness.py --config <cfg> --opponent builtin
--difficulty <level> --matches N [--config-seed S]` →
`output/twincore-reports/<ts>-<level>/report.md` containing, per series:
win/loss/draw counts, opening-playbook adherence rate, P0 next-tick rate,
out-of-cycle pulse count, cadence adherence (ticks issued vs expected),
per-channel observed-vs-budget usage + breach list, validator rejection/clamp
rates, digest counts and sizes, degradation-event list from recorder files.
Milestone acceptance reads these reports; no manual tallying. Live-match
rounds for converge follow the launch-and-poll protocol on the multiplayer
savegame convention (`./launch.sh cmr1`).

## 5. Degradation test matrix (US4 / FR-024; driver-level, stub endpoints)

| Scenario | Required observable |
|---|---|
| S2 killed mid-match | S1 continues on last directive; no invented pulses; `degradation` line; recovery on re-attach |
| S1 killed mid-match | zero commands from seat; match continues; strategist never binds |
| sidecar/engine unreachable | both lanes idle; no fabricated commands; recovery with full re-observe |
| rematch/load mid-run | re-observe; stale-id orders invalidated + re-issued; no busy-assumption |
| malformed S1 output streak | ≤2 retries then idle tick; streak recorded |
| budget breach | channel reset discipline executed; `breach` line; no silent growth |
| invalid S2 packet | no element applied; packet_rejected line; old directive vN remains |

## 6. Entry points

```sh
python3 tools/twincore/driver.py --config tools/twincore/configs/boot-default.json   # S1-only
python3 tools/twincore/driver.py --config tools/twincore/configs/twin-core.json      # full pair
python3 tools/twincore/replay.py --match <dir> --kinds decision,validation --ticks 100-400
python3 tools/twincore/harness.py --opponent builtin --difficulty beginner --matches 3
python3 -m pytest tools/tests/twincore -q                                            # unit suites
```