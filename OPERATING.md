# OPERATING.md — launching the game + AI agents (operator quick reference)

> Future agent (LLM operator): this is the how-to. AGENT.md RULES ALWAYS APPLY:
> keep the user informed; NEVER block — background-launch, log and poll.

## One command: everything

```sh
./launch-multi.sh latest
```

- `latest` = newest savegame in `~/.homeworld/SavedGames/{Single,Multi}Player`
  (a specific save NAME also works; no save = main menu)
- Boots: game (with AI-play spectating layer) → MCP sidecar `:8302` → waits
  for the UDS socket → the agent-multi fleet (detached)

## The pieces

| Piece | What | Where |
|---|---|---|
| **Game** | GoK build (`build/homeworld /aiPlay ...`), AI-play UDS `/tmp/homeworld-aiplay.sock` | `launch.sh` |
| **Sidecar** | MCP over HTTP, localhost-only: `http://127.0.0.1:8302/mcp` | `tools/aiplay_mcp.py` |
| **Fleet** | 1 commander (4B-class executor, owns ALL orders) + 4 read-only peers with per-role instruction queues | `tools/agent-multi.py`, config `tools/agent-multi.json` |
| **Endpoints** | commander 192.168.1.106:9931 (gemma-4 class); peers 192.168.2.72:9931-9934 (Qwen3.8-27B, `--cache-reuse 256 --swa-full` recommended) | config / CLI flags |

## Watching

```sh
tools/fleet-tail.sh            # all agents, role-prefixed (tail -F)
tools/fleet-tail.sh critic     # one role; -h for help, -l to list
tail -f /tmp/aiplay-agent.log  # commander telemetry (orders + results)
grep 'QUEUE pickup' /tmp/aiplay-agent.log   # commander's queue read per round
```

Peer logs stream the big models' reasoning live:
/tmp/aiplay-multi-&lt;session&gt;/{strategist,analyst,economist,critic}.log
(current session dir = newest under /tmp/aiplay-multi-*).

## Health

```sh
python3 tools/aiplay_rawtest.py '{"id":1,"method":"aiSession.status"}'
# {"state":"playing","boundPlayerIndex":0,...} — boundPlayerIndex MUST be 0
```

(`-1` = engine restarted and unbound; the commander self-heals by rebinding
within a round — verify with the same call.)

## Restart recipes

```sh
# everything:
./launch-multi.sh latest
# just the fleet (game untouched):
kill $(cat /tmp/gok-fleet.pid) 2>/dev/null  # then:
setsid nohup python3 tools/agent-multi.py >/tmp/agent-multi.out 2>&1 </dev/null &
# just the game (fleet rebinds automatically):
./launch.sh latest --port 8302
```

Launcher PIDs are saved to `/tmp/gok-{game,mcp,fleet}.pid`; kill with
`kill $(cat /tmp/gok-<piece>.pid)`.

## If things look broken

- observe errors "Expecting..." (JSON parse) → engine binary vs agent
  mismatch; rebuild (`meson compile -C build`) and bounce the game
- orders all E_BAD_ID → engine unbound; the commander rebinds (see Health)
- research stalls → check snapshot `techTree.researched` + `research`
  fields (docs: `documentation/community/ai-snapshot-dictionary.md`)
- crash → `coredumpctl list` (debug builds keep DWARF; analyze with
  `coredumpctl gdb`)
- stray duplicate agents racing for orders → list pids
  (`ps aux | grep tools/agent`) and kill the orphans first (multiple fleets
  must never coexist)

## Test & build

```sh
python3 tools/tests/test_agent_multi.py   # 35 unit tests (must be green)
meson compile -C build                    # engine (gcc, meson/ninja)
```

Design/docs: `specs/` (001-agent-multi-llm-fleet, 001-peer-instruction-queue,
001-sidecar-v2 [pending review], 001-fleet-refactor [pending review]);
diagrams: `documentation/community/agent-multi*.png`; doctrine source:
`gameplay.txt`; the data dictionary + playbook are injected into every
agent's system prompt from `documentation/community/`.