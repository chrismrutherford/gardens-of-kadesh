# Quickstart: V4 driver — run + validate

Feature: `005-v4-strategist-driven-llm-cpu-player-s2-d` | Date: 2026-10-23

## Prerequisites

- GoK engine running with the spec-003 sidecar (MCP layer) reachable; the
  established multiplayer savegame workflow (cash-inject save for live
  rounds).
- PostgreSQL reachable with `twincore_q.messages` (spec-004 FR-030 schema).
- S2 endpoint on the fast rack (config; local/LAN only), e.g.
  `http://192.168.2.106:9931` — must serve chat completions.
- Python env with the tools deps (same as v3: `tools/twincore/`).

## Unit tests (fast, deterministic — no engine, no postgres, no LLM)

```bash
python -m pytest tools/tests/twincore/ -q        # v3+v4 suites, all green
python -m pytest tools/tests/twincore/test_program.py     -q   # schema/envelope/whole-apply/history
python -m pytest tools/tests/twincore/test_executor.py    -q   # translation+reflexes+micro lane+priority law
python -m pytest tools/tests/twincore/test_s1_micro.py    -q   # S1 loop, budgets, repairs, degradation tier
python -m pytest tools/tests/twincore/test_v4_driver.py   -q   # dual pulse loops, degradation, precedences, P0 wake
```

Expected: all green; import-graph test proves `s1tick`/`economy`/`s2pulse`
are NOT reachable from `v4driver` (FR-014).

## Live round (launch-and-poll, US1 independent test)

1. Load the cash-inject savegame in a headless/engine window.
2. Start the driver:
   ```bash
   python -m tools.twincore.v4driver --config tools/twincore/configs/*.json
   ```
3. Poll the trail, waiting for the FIRST program pulse (~20 s game time):
   ```bash
   tail -n 40 $(python -m tools.twincore.tail --last-match -k program -k packet)
   ```
   Expect: `program` row (version bump ≥1) citing items covering
   production + research + ≥1 fleet tasking; subsequent command rows cite
   `{program_version, item_id}` (SC-002 traceability).
4. Watch reflex + micro activity between S2 pulses: `micro` rows carry
   `{program_version, seq, grant_id, decision_id}` citations and must be
   in-envelope (violations only as blocked rows); `reflex_harvest`/
   `reflex_deliver` rows only for collectors covered by neither program
   nor S1 (FR-006/FR-018).
5. Degrade deliberately: stop the S1 endpoint — program + reflexes
   continue, S1 duties NOT absorbed by code. Restore. Then stop the S2
   endpoint — last program + S1 (in its existing envelope) + reflexes
   continue, transitions logged; nothing invented (FR-012). Restore the
   endpoint: next valid program applies at a tick boundary as a new
   version.
6. Inject P0: attack the seat's defended zone. Expect BOTH channels woken
   out-of-cycle: S1 acting in-envelope within one micro-pulse, S2
   re-planning; wake latency ≤1 tick in the trail (SC-004).

## Series + report (US5 / SC-006)

```bash
python -m tools.twincore.harness --spec 005 --series 3 --difficulty beginner
```

Report (same shape as v3) adds: program-adherence (commands per program
item), S1-adherence (micro-commands per grant) + envelope violation and
override counts, revision counts, pulse-cadence adherence (both
channels), validator rejection rates, reflex activity — all computed from
the postgres trail (SC-005, SC-007 replayable).