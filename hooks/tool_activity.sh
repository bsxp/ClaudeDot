#!/usr/bin/env bash
# Hook: PostToolUse (catch-all) — resets stale "question" status
# If Claude is using tools, it's working, not waiting for a question.
# Skips AskUserQuestion to let the specific cleanup hook handle that.

set -euo pipefail

STATE_DIR="$HOME/.claude-helper"
INPUT=$(cat)

SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')
if [ -z "$SESSION_ID" ]; then
    exit 0
fi

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
if [ "$TOOL_NAME" = "AskUserQuestion" ]; then
    exit 0
fi

INFO_FILE="$STATE_DIR/sessions/$SESSION_ID/info.json"
if [ ! -f "$INFO_FILE" ]; then
    exit 0
fi

# Only write if status is actually stale
CURRENT_STATUS=$(jq -r '.status // empty' "$INFO_FILE" 2>/dev/null)
if [ "$CURRENT_STATUS" = "question" ]; then
    TMP=$(mktemp)
    jq '.status = "working" | .waiting_for = null | .last_updated = now' "$INFO_FILE" > "$TMP" && mv "$TMP" "$INFO_FILE"
fi
