# Quickstart: fleet refactor validation

## Unit tests (no live match)

```sh
python3 tools/tests/test_agent_multi.py        # updated imports
python3 tools/tests/test_fleet_refactor.py     # NEW: JSONL, status,
                                               # doctrine markers, tool
                                               # description checks
```

## Live validation

```sh
./launch-multi.sh latest
python3 tools/fleet-status.py          # the one-glance view (SC-001)
tail -1 /tmp/aiplay-multi-*/rounds-commander.jsonl   # structured rounds
python3 tools/agent.py                 # single-agent mode still works (FR-008)
```

1. fleet-status shows all roles fresh with rising round counts; kill a
   peer server → role flagged STALE within 60 s, commander unaffected.
2. JSONL line counts == logged round counts for a 10-minute run (SC-002).
3. `grep -rn '"""' tools/*.py | grep -v agent_prompts.py` shows no prompt
   strings outside agent_prompts.py (SC-003 spot check).
4. Compare E_BAD_ID/wrong-target rates vs a pre-refactor baseline log
   (SC-005) via `grep -c E_BAD_ID` over equal-length runs.