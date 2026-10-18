#!/usr/bin/env bash
# launch.sh — start Gardens of Kadesh with the AI-play spectating layer.
#
# Usage:
#   ./launch.sh                      # main menu (AI-play socket ready)
#   ./launch.sh "Savegame Name"      # auto-load a saved game (skips menus+cinematics)
#   ./launch.sh --port 8302          # also start the MCP HTTP sidecar
#
# The AI-play layer serves /tmp/homeworld-aiplay.sock (local-only). With --port,
# an MCP sidecar is started on http://127.0.0.1:<port>/mcp (17 tools).
#
# Assets: HW_DATA env var or ./output/game (created by tools/extract-disc-assets.sh).
set -euo pipefail

HW_DATA="${HW_DATA:-$(dirname "$0")/output/game}"
SAVE=""
MCPPORT=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --port) MCPPORT="$2"; shift 2 ;;
        --port=*) MCPPORT="${1#*=}"; shift ;;
        -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
        *) SAVE="$1"; shift ;;
    esac
done

if [[ ! -f "$HW_DATA/Homeworld.big" ]]; then
    echo "launch.sh: assets not at $HW_DATA (Homeworld.big missing)" >&2
    echo "  run tools/extract-disc-assets.sh first, or set HW_DATA" >&2
    exit 1
fi

if [[ ! -x "$(dirname "$0")/build/homeworld" ]]; then
    echo "launch.sh: build/homeworld not found — compiling first" >&2
    (cd "$(dirname "$0")" && meson setup build 2>/dev/null; meson compile -C build) \
        || meson compile -C build
fi

BIN="$(cd "$(dirname "$0")" && pwd)/build/homeworld"
cd "$(dirname "$0")"

if [[ -n "$MCPPORT" ]]; then
    setsid nohup python3 tools/aiplay_mcp.py --port "$MCPPORT" \
        >/tmp/aiplay-mcp.log 2>&1 < /dev/null & disown
    echo "aiplay-mcp: http://127.0.0.1:$MCPPORT/mcp"
fi

# /aiPlay enables the layer; auto-skips all cinematics in spectating mode.
# The savegame is looked up in SinglePlayer first, then MultiPlayer (per ~/.homeworld).
ARGS=(/aiPlay)
if [[ -n "$SAVE" ]]; then
    SAVEHOME="${HOME}/.homeworld"
    if [[ -f "$SAVEHOME/SavedGames/SinglePlayer/$SAVE" ]]; then
        ARGS+=(/aiPlayLoad "$SAVE")
        echo "launch: single-player save '$SAVE'"
    elif [[ -f "$SAVEHOME/SavedGames/MultiPlayer/$SAVE" ]]; then
        ARGS+=(/aiPlayLoadMP "$SAVE")
        echo "launch: multi-player save '$SAVE'"
    else
        echo "launch.sh: savegame '$SAVE' not found in $SAVEHOME/SavedGames/{SinglePlayer,MultiPlayer}" >&2
        exit 1
    fi
fi

echo "launch: HW_Data=$HW_DATA ${ARGS[*]}"
setsid nohup env HW_Data="$HW_DATA" "$BIN" "${ARGS[@]}" \
    >/tmp/aiplay-game.log 2>&1 < /dev/null & disown
echo "launched; game log /tmp/aiplay-game.log; socket /tmp/homeworld-aiplay.sock (~10s)"