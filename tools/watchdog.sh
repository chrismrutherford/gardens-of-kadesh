#!/usr/bin/env bash
# watchdog.sh — relaunch the game whenever its AI socket disappears.
# The agent (tools/agent.py) survives outages on its own and rebinds when the
# socket reappears. The watchdog only ever starts the game; it never kills it.
#
# Usage: tools/watchdog.sh [<savegame> ...]   (default: cmr1)
set -u
SAVE="${1:-cmr1}"
DIR="$(cd "$(dirname "$0")/.." && pwd)"
SOCK="/tmp/homeworld-aiplay.sock"

while true; do
    if ! python3 - "ok" <<'PYEOF' 2>/dev/null
import socket, sys
s = socket.socket(socket.AF_UNIX)
try:
    s.connect("/tmp/homeworld-aiplay.sock")
    sys.exit(0)
except Exception:
    sys.exit(1)
PYEOF
    then
        echo "$(date '+%H:%M:%S') watchdog: socket down — relaunching $SAVE"
        (cd "$DIR" && ./launch.sh "$SAVE" >/tmp/aiplay-watchdog.log 2>&1) &
    fi
    sleep 15
done