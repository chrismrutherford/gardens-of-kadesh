#!/usr/bin/env bash
# fleet-tail.sh — tail the live agent-multi fleet logs with role prefixes.
#
# Usage:
#   tools/fleet-tail.sh [role...]      # tail selected agents
#   tools/fleet-tail.sh -l             # list available roles for this session
#   tools/fleet-tail.sh -h             # help
#
# Examples:
#   tools/fleet-tail.sh                          # all roles + commander
#   tools/fleet-tail.sh strategist critic        # two agents
#   tools/fleet-tail.sh analyst                  # battle reactions only
set -euo pipefail

PROG=$(basename "$0")
LIVE="$(ls -dt /tmp/aiplay-multi-*/ 2>/dev/null | head -1 || true)"

if [[ "$1" == "-h" || "$1" == "--help" ]]; then
    echo "usage: $PROG [options] [role...]"
    echo
    echo "tails agent-multi fleet logs (role-prefixed)."
    echo
    echo "options:"
    echo "  -h, --help   show this help"
    echo "  -l, --list   list the roles available in the live session"
    echo
    echo "roles: strategist  analyst  economist  critic  (plus 'commander'"
    echo "from /tmp/aiplay-agent.log)"
    if [[ -n "$LIVE" ]]; then
        echo
        echo "live session: $LIVE"
    fi
    exit 0
fi

if [[ -z "$LIVE" ]]; then
    echo "$PROG: no fleet session dir found (is agent-multi running?)" >&2
    exit 1
fi

if [[ "$1" == "-l" || "$1" == "--list" ]]; then
    echo "live session: $LIVE"
    for r in strategist analyst economist critic commander; do
        if [[ "$r" == "commander" ]]; then
            [[ -f /tmp/aiplay-agent.log ]] && echo "  $r   /tmp/aiplay-agent.log"
        elif [[ -f "${LIVE}${r}.log" ]]; then
            echo "  $r   ${LIVE}${r}.log"
        fi
    done
    exit 0
fi

echo "fleet-tail: session $LIVE (ctrl-c to stop; help: $PROG -h)"

FILES=()
for role in "$@"; do
    if [[ "$role" == "commander" ]]; then
        [[ -f /tmp/aiplay-agent.log ]] && FILES+=(/tmp/aiplay-agent.log)
    elif [[ -f "${LIVE}${role}.log" ]]; then
        FILES+=("${LIVE}${role}.log")
    else
        echo "fleet-tail: no log for '$role' (known: strategist analyst economist critic commander)" >&2
        exit 1
    fi
done
if [[ ${#FILES[@]} -eq 0 ]]; then
    FILES=("$LIVE"strategist.log "$LIVE"analyst.log "$LIVE"economist.log \
           "$LIVE"critic.log /tmp/aiplay-agent.log)
fi
tail -F "${FILES[@]}"