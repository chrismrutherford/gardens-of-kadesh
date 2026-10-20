#!/usr/bin/env bash
# launch-multi.sh — launch the game + MCP sidecar + the agent-multi fleet
# (4B executor commander + big-model peer advisors).
#
# Usage:
#   ./launch-multi.sh <savegame> [--extra agent-multi flags...]
#   ./launch-multi.sh latest                        # newest autosave
#   ./launch-multi.sh                               # main menu
#
# Config: tools/agent-multi.json (commander/peer endpoints).
# Logs: /tmp/aiplay-multi-<id>/<role>.log + /tmp/aiplay-agent.log.
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO"

SAVE=""
EXTRA=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        latest)
            SAVE="$( (ls -t "${HOME}/.homeworld/SavedGames/MultiPlayer/"/* \
                      "${HOME}/.homeworld/SavedGames/SinglePlayer/"/* 2>/dev/null) \
                    | head -1)"
            SAVE="$(basename "$SAVE")"
            [[ "$SAVE" == ":" || -z "$SAVE" ]] && { echo "launch-multi.sh: no savegames found" >&2; exit 1; }
            echo "launch-multi: latest save '$SAVE'"
            shift ;;
        *) SAVE="${SAVE:+$SAVE }$1"; shift ;;
    esac
done

# 1. game + sidecar (reuse launch.sh; parse out plain save args)
GAME_ARGS=()
for arg in $SAVE; do GAME_ARGS+=("$arg"); done
./launch.sh "${GAME_ARGS[@]}" --port 8302

# 2. wait for the game socket before the fleet starts hammering it
echo "launch-multi: waiting for /tmp/homeworld-aiplay.sock"
for _ in $(seq 1 60); do
    [[ -S /tmp/homeworld-aiplay.sock ]] && break
    sleep 1
done
[[ -S /tmp/homeworld-aiplay.sock ]] || { echo "launch-multi.sh: socket timeout" >&2; exit 1; }

# 3. fleet (detach; kill prior fleet of this user first)
pkill -f 'tools/agent-multi.py' 2>/dev/null || true
setsid nohup python3 tools/agent-multi.py \
    >/tmp/agent-multi.out 2>&1 </dev/null & disown
echo "launch-multi: fleet up; logs under /tmp/aiplay-multi-*, commander telemetry /tmp/aiplay-agent.log"