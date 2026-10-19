# Quickstart: agent-multi validation

Prereqs: running match via `./launch.sh <save> --port 8302`; four LLM servers
on `:9931`-`:9934` (`--cache-reuse 256` recommended on each).

## Unit tests (no live match needed)

```sh
python3 tools/tests/test_agent_multi.py
```
Uses a fake sidecar + fake LLM servers in-process. Covers: read-only peer
invariants, artifact read/write/staleness, commander prompt merge, reset
rebase, peer discard of tool-call responses, atomic writes.

## Live validation

```sh
python3 tools/agent-multi.py
```
1. Five loops attach; `/tmp/aiplay-multi-<id>/` appears with the 4 peer
   artifacts + orders.json within 60 s.
2. `tail -f /tmp/aiplay-multi-<id>/strategist.log` — continuous rounds
   (a new LLM round every few seconds while the match runs).
3. Game window: only the commander's orders take effect (build screen fills
   via the commander; no double builds).
4. Kill one peer server for 2 min → commander keeps ordering (its log shows
   no increase in round spacing); restore → peer artifacts fresh again.
5. `/tmp/aiplay-multi-<id>/*.log` grep `PERF`:
   - `server-rounds` grows ≈ every <30 s per peer (SC-001)
   - commander `round-seconds` within ±20% of single-agent baseline (SC-002)
6. Match over → peers idle with a final summary; relaunch a save → peers
   resume automatically.