#!/usr/bin/env bash
# Hook: Notification — tracks idle/elicitation notifications
# Non-blocking. Reads hook input from stdin.

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

NTYPE=$(echo "$INPUT" | jq -r '.notification_type // empty')
TITLE=$(echo "$INPUT" | jq -r '.title // "notification"')

# Determine waiting_for based on notification_type or title
MATCH="${NTYPE:-$TITLE}"
case "$MATCH" in
    *permission*)
        WAITING="permission"
        STATUS="permission"
        ;;
    *idle*|*input*)
        WAITING="input"
        STATUS="idle"
        ;;
    *elicitation*)
        WAITING="elicitation"
        STATUS="idle"
        ;;
    *)
        WAITING="$TITLE"
        STATUS="idle"
        ;;
esac

TMP=$(mktemp)
jq --arg wf "$WAITING" --arg st "$STATUS" \
    '.waiting_for = $wf | .status = $st | .last_updated = now' "$INFO_FILE" > "$TMP" && mv "$TMP" "$INFO_FILE"
