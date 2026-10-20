# Quickstart: peer-instruction-queue validation

Prereqs: running match (`./launch.sh <save> --port 8302`); fleet agents
running (`tools/agent-multi.py`).

## Unit tests

```sh
python3 tools/tests/test_agent_multi.py
```
Queue additions: add/remove/move ops, validation rejections, newest-wins,
queue cap, executor top-first selection, progress notes + stall flagging,
empty-queue fallback, reset clearing, commander-never-edits invariant.

## Live validation

```sh
tools/fleet-tail.sh                      # all agents
cat /tmp/aiplay-multi-*/instruction-queue.json | python3 -m json.tool
```
1. After the fleet attaches, the strategist/economist publish `edits` —
   the queue file fills within ~2 min; instruction ids increase monotonically.
2. `tools/fleet-tail.sh critic` shows the critic reacting to the queue.
3. The commander's log shows it working the ACTIVE instruction (its order
   lines name the instruction's target, e.g. an order_build for "build 2
   Ion Cannon Frigates").
4. Inject a manual edit (e.g. remove the stall flag) — atomic replace,
   next commander round respects it.
5. Kill one peer 2 min → the queue stays well-formed; no corruption
   (SC-003); commander keeps executing the queue.