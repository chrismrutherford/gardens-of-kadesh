# Quickstart — validating the full-coverage MCP

Prerequisites: build (`meson compile -C build`), assets, then
`./launch-multi.sh latest` (game + sidecar :8302 + fleet).

## 1. Reachability from opencode (JSON over HTTP)

```sh
curl -s http://127.0.0.1:8302/mcp -X POST \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python3 -m json.tool
# expect: every session/state/order/audit tool (data-model table), none missing
```

In opencode the MCP server is registered as
`http://127.0.0.1:8302/mcp`; every tool in the registry is callable with
plain JSON arguments.

## 2. Observation parity

```sh
python3 tools/aiplay_rawtest.py '{"id":1,"method":"state.snapshot"}'   # raw UDS
# vs the observe tool over :8302 — must be field-identical (SC-001/002)
```

## 3. Order path incl. new funnels

- positive: order_kamikaze on a fighter group → queued:true + orderApplied event
- sync rejection: order_build with an unknown type for the race → error
  E_BAD_REQUEST immediately (no queued:true)
- fleet-wide: order_alliance / order_ru_transfer in a multiplayer skirmish →
  queued:true, orderApplied; in single-player → clean documented error
- lab gating: order_research with labs busy → event orderRejected aux=5

## 4. Determinism (SC-002, paused tick)

```
pause → observe → observe → compare byte-identical → resume → step 10 →
observe: tick advanced by exactly 10
```

## 5. Test gates

```sh
python3 tools/tests/test_aiplay_protocol.py   # round-trip + conformance (new)
python3 tools/tests/test_agent_multi.py       # 35 unit tests, must stay green
```

Artifacts to cross-check: `contracts/mcp-surface.md` (tool↔wire↔funnel
registry), `data-model.md` (schemas, validation, errors).