# LLM Quick-Start: Launching the Game + Agent

This guide gets a Homeworld match running with the LLM fleet agent in under 5
minutes. Full details: `documentation/community/installation-manual.md`.

## Prerequisites

- **Assets**: original game disc extracted once via
  `tools/extract-disc-assets.sh` (produces `output/game/` with `Homeworld.big`,
  `HW_Comp.vce`, `HW_Music.wxd`, `Update.big`, `Movies/`). If `output/game/` is
  missing or you have a different install, set `HW_DATA=<assets-folder>`.
- **Build**: `meson setup build && meson compile -C build` (only once; rerun
  after source changes).

## One command: `./launch.sh`

```sh
./launch.sh cmr1 --port 8302
```

What happens:

1. Compiles if `build/homeworld` is missing.
2. Launches the game window with the AI-play layer (`/aiPlay`), loading
   the savegame `cmr1` from `~/.homeworld/SavedGames/{SinglePlayer,MultiPlayer}`
   (`/aiPlayLoadMP cmr1`). All cinematics are auto-skipped in spectating mode.
3. Starts the MCP sidecar: **`http://127.0.0.1:8302/mcp`** (17+ game tools).

Without a savegame argument, the game sits at the main menu (socket still live).
Without `--port`, no sidecar starts (use the raw socket:
`tools/aiplay_rawtest.py`).

## One command: the agent

```sh
python3 tools/agent.py --mcp http://127.0.0.1:8302/mcp \
                       --player 0 \
                       --llm  http://192.168.1.106:9931
```

What happens:

1. Waits for the game socket + sidecar (retries forever).
2. Binds itself to player 0 of the running match.
3. Enters the agentic loop: streamed reasoning + **native tool calls** —
   observe → decide → order (move/attack/build/harvest/research/...) →
   result feedback → repeat.
4. Autosaves every ~10 min to
   `~/.homeworld/SavedGames/MultiPlayer/ai-autosave-*.` (zero-padded, time
   sorted) and self-restarts from them after any outage.

The human spectates the normal game window (HUD + Caps Lock tactical overlay).

Useful agent flags:
- `--max-rounds 600` turn budget (default 2000)
- `--interval 4.0` seconds between decisions (smaller = quicker reactions)
- `--model X` pick a specific LLM model (default: auto-detected from the server)
- `--logfile /tmp/aiplay-agent.log` default telemetry path

## Watching it play (monitoring)

- **Game world**: the normal window; the in-game Caps Lock overlay shows RUs
  and fleet counts.
- **Command stream**: `tail -f /tmp/aiplay-agent.log` — every round shows
  world summary (tick / RU / income / fleet / enemies / build queue),
  the streamed chain-of-thought, the chosen tool call (order) and its result,
  plus events (orderApplied/Rejected, shipDestroyed, matchOver).
- **Snapshots on demand**:
  `python3 tools/aiplay_rawtest.py '{"id":1,"method":"state.snapshot"}'`
- **Battle events**:
  `python3 tools/aiplay_rawtest.py '{"id":1,"method":"state.events"}'`

## Troubleshooting

| Symptom | Fix |
|---|---|
| "waiting for game socket" | game isn't running or still loading (~15 s); check `/tmp/aiplay-game-*.log` |
| `E_NO_LABS (build ResearchShips first)` | fleet has no ResearchShip — the engine refuses research; the agent builds one |
| `E_BAD_ID` on orders | after any match reset the agent re-observes and re-issues; a stray duplicate agent may be racing — `pgrep -af agent.py` |
| Game window closed, match lost | relaunch from the latest autosave: `./launch.sh ai-autosave-0000000006165 --port 8302` |

## Safety

The AI-play transport is **local-only by design** (Unix socket chmod 0600 +
HTTP MCP bound to 127.0.0.1). No network exposure.