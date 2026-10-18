# 🛰️ MCP Server for Gardens of Kadesh — let your AI play Homeworld

We built a Model Context Protocol server into Gardens of Kadesh (the open-source
HomeworldSDL port) so ANY MCP client — Claude Desktop, your own agent scripts —
can **play the actual 1999 game from source** while you watch it live in the normal
game window. No cheats, no modified game data: the AI issues orders through the
engine's own command funnel, exactly like a human player does.

## How it works

```
your AI agent  ←HTTP MCP (127.0.0.1)→  tools/aiplay_mcp.py  ←Unix socket→  engine layer
```

- The game exposes a **local-only Unix socket** (`/tmp/homeworld-aiplay.sock`, chmod 0600)
- The sidecar serves a standard **MCP Streamable HTTP** endpoint on `127.0.0.1` only
- Everything is loopback — nothing touches the network
- The human spectates the untouched game window: normal rendering, HUD (Caps Lock
  for the vanilla tactical overlay), camera control; spectator input provably
  never affects simulation state

## 21 tools

| Group | Tools |
|---|---|
| Session | `bind_session`, `game_status`, `save_game` |
| Observation | `observe` (full battlefield: every ship, resource, players, build queue, research topics, **engine's real tech tree** with locked hulls + missing techs), `get_events` (orderApplied/Rejected, shipDestroyed, matchOver) |
| Movement/combat | `order_move`, `order_attack`, `order_guard`, `order_dock`, `order_special`, `order_halt`, `order_formation` |
| Fleet management | `order_build` (any hull with its research tree), `order_launch`, `autolaunch`, `order_tactics` (aggressive/neutral/evasive), `order_harvest`, `order_research`, `order_scuttle` |
| Time control | `pause`, `resume`, `step` (think-then-act pacing; paused snapshots are byte-identical) |

Snapshots include per-ship orders (verb + real destination/target), RU counts for
all players, and a live **RU income rate** stat.

## Quickstart

```sh
# 1. assets (original game disc) per the install manual
./tools/extract-disc-assets.sh Homeworld.bin -o output

# 2. build
meson setup build; meson compile -C build

# 3. launch: game + sidecar; auto-loads a save, skips all cinematics
./launch.sh cmr1 --port 8302

# 4. point your MCP client at  http://127.0.0.1:8302/mcp
#    then: bind_session {"playerIndex": 0}  → observe → command the fleet
```

Prefer raw? `python3 tools/aiplay_rawtest.py '{"id":1,"method":"aiSession.status"}'`

## Included debugging agent

`tools/agent.py` is a working reference commander: streaming reasoning (Qwen3-style
`reasoning_content` via any OpenAI-compatible endpoint), native tool calling, an
economy/attack/save nag scheduler, and full telemetry to
`tail -f /tmp/aiplay-agent.log`.

## Status

Spec-driven (V-model) with 78 unit checks green + live in-match verification:
orders applied and visible in-world, determinism, disconnect/reconnect resilience,
spectator-invariance, scenario-restricted tech trees. Follow-up: full start-to-
victory spectated campaign with an attached agent.

Docs: `documentation/community/installation-manual.md` (AI spectating section)
Specs: `specs/002-ai-plays-the-game-via-a-spectating-ui/` (spec, contracts, plan)
Repo: https://gitlab.com/gardens-of-kadesh/gardens-of-kadesh