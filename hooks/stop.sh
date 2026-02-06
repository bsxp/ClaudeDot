#!/usr/bin/env bash
# Hook: Stop — marks session as "done"
# Non-blocking (async). Reads hook input from stdin.

set -euo pipefail

STATE_DIR="$HOME/.claude-helper"
INPUT=$(cat)

SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')
if [ -z "$SESSION_ID" ]; then
    exit 0
fi

SESSION_DIR="$STATE_DIR/sessions/$SESSION_ID"
INFO_FILE="$SESSION_DIR/info.json"

if [ ! -f "$INFO_FILE" ]; then
    exit 0
fi

TMP=$(mktemp)
jq '.status = "done" | .waiting_for = "input" | .last_updated = now' "$INFO_FILE" > "$TMP" && mv "$TMP" "$INFO_FILE"
