#!/usr/bin/env bash
# Hook: PostToolUse (catch-all) — resets stale "question" / "permission" status
# If Claude is using tools, it's working, not waiting for user input.

set -euo pipefail

STATE_DIR="$HOME/.claude-helper"
INPUT=$(cat)

SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')
if [ -z "$SESSION_ID" ]; then
    exit 0
fi

INFO_FILE="$STATE_DIR/sessions/$SESSION_ID/info.json"
if [ ! -f "$INFO_FILE" ]; then
    exit 0
fi

# Reset any stale interactive status — if a tool is running, Claude is working
CURRENT_STATUS=$(jq -r '.status // empty' "$INFO_FILE" 2>/dev/null)
if [ "$CURRENT_STATUS" = "question" ] || [ "$CURRENT_STATUS" = "permission" ]; then
    TMP=$(mktemp)
    jq '.status = "working" | .waiting_for = null | .last_updated = now' "$INFO_FILE" > "$TMP" && mv "$TMP" "$INFO_FILE"
fi
