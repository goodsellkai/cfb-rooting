#!/bin/sh
# The API key lives in these. Nothing automatic should rewrite them.
root=$(cd "$(dirname "$0")/../.." && pwd)
f=$(python "$root/.claude/hooks/_path.py")
case "$f" in
  *.env|*.env.*|*/.env)
    printf '%s' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"That file holds the CFBD API key. Change it by hand rather than through a tool."}}'
    ;;
esac
