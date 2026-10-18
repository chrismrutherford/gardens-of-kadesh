#!/usr/bin/env bash
# watchdog.sh — relaunch the game when its AI socket disappears for >30s
# (distinguishes "universe rebuild/load" from a real exit). Singleton via pidfile.
#
# Usage: tools/watchdog.sh [<savegame> ...]   (default: cmr1)
set -u
PIDFILE="/tmp/aiplay-watchdog.pid"
if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "watchdog already running (pid $(cat "$PIDFILE"))"
    exit 0
fi
echo $$ > "$PIDFILE"
trap 'rm -f "$PIDFILE"' EXIT

SAVE="${1:-cmr1}"
DIR="$(cd "$(dirname "$0")/.." && pwd)"
DOWN_SINCE=0

socket_ok() {
    python3 - <<'PYEOF' 2>/dev/null
import socket, sys
s = socket.socket(socket.AF_UNIX)
try:
    s.connect("/tmp/homeworld-aiplay.sock"); sys.exit(0)
except Exception:
    sys.exit(1)
PYEOF
}

while true; do
    if socket_ok; then
        DOWN_SINCE=0
    else
        now="$(date +%s)"
        if [[ $DOWN_SINCE -eq 0 ]]; then
            DOWN_SINCE=$now
        elif (( now - DOWN_SINCE >= 30 )); then
            echo "$(date '+%H:%M:%S') watchdog: socket down >30s — relaunching $SAVE"
            (cd "$DIR" && ./launch.sh "$SAVE" > /dev/null 2>&1) &
            DOWN_SINCE=0
            # give the game a full load window before polling again
            sleep 45
        fi
    fi
    sleep 5
done
