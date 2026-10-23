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

## Logbook convention (gameplay tactics)

Record every gameplay tactic/doctrine learned while commanding the match in
the engineer-logbook as a NOTE tagged **`hw-play-book`** (same tag in title
keywords). Retrieve with `list_notes tag=hw-play-book` or
`search('hw-play-book')` — tactics persist across sessions this way.

## The v3 twin-core driver (spec 004) + the postgres debug tool (spec 004 runs)

All v3 messages/logging live in **Postgres 192.168.1.152/twincore_q**, table
`messages` (kind/ match_id/ tick/ payload/…) — NO local files for messages or
logging. The record store, the inbox exchange and the debug reads are all SQL.

```sh
python3 tools/twincore/dbg.py                      # recent runs (rows, tick, age)
python3 tools/twincore/dbg.py --match <id>         # run overview: digest → cadence, commands, ok-rate, p0 open
python3 tools/twincore/dbg.py --match <id> --state # live-ish state (bank/income/threat/last command)
python3 tools/twincore/dbg.py --match <id> --digests 2   # last digests VERBATIM (p0/changes/order_status)
python3 tools/twincore/dbg.py --match <id> --tail 30 --kinds decision,validation
python3 tools/twincore/dbg.py --inbox              # unread wake/note messages (peek, no consume)
```

Run a match through the driver (peer PLAYER — not an engine/lobby mode):

```sh
./launch.sh cash-inject --port 8302      # wait for the sidecar; verify `dbg.py --inbox` shows engine reachable via observe
nohup python3 tools/twincore/driver.py --config tools/twincore/configs/live-cash.json \
      --max-cycles 200 --poll-seconds 1.0 > /tmp/twincore.out 2>&1 &
```

Operator rules for v3 rounds:

- **One driver per seat. Always.** Check `ps aux | grep twincore/driver` first
  and kill orphans by PID — a surviving zombie re-attaches and its 2048-token
  reasoning outputs will confuse the trail.
- S1 = fast rack 192.168.1.106:9931 (qwen3.8-27B, thinking OFF). S2 = slow rack
  192.168.2.72:9931 (GLM-5.3-Flash; pulses take 15-180 s — they are async,
  S1 never waits; `s2_out` usage lines with completion=2048 mean an old
  driver or thinking still enabled).
- The queue is NOT a command path: only `wake`/`note` inbox kinds are
  consumable (FR-002/FR-030). Commands exist only in the driver's S1 lane.
- Test rows leak: `dbg.py --list` shows them; a sessionfinish purge in
  `tools/tests/twincore/conftest.py` clears old ones. Purge manually:
  `psql host=192.168.1.152 dbname=twincore_q -c "delete from messages where
  match_id like 'test-%';"`.
- Everything else reads like the fleet section above: never block, poll,
  `observation → recorder rows (postgres) → decide → command → validator →
  engine`. The replay contract lives in `tools/twincore/replay.py`
  (kinds/ticks extraction from postgres — SC-007).

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