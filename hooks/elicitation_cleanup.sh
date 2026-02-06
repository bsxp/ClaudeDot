#!/usr/bin/env bash
# Hook: PostToolUse (AskUserQuestion) — cleans up elicitation pending files
# Non-blocking. Runs after user answers the question in the terminal.

set -euo pipefail

STATE_DIR="$HOME/.claude-helper"
INPUT=$(cat)

SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')
if [ -z "$SESSION_ID" ]; then
    exit 0
fi

PENDING_DIR="$STATE_DIR/sessions/$SESSION_ID/pending"
INFO_FILE="$STATE_DIR/sessions/$SESSION_ID/info.json"

# Remove all elicitation pending files for this session
if [ -d "$PENDING_DIR" ]; then
    for f in "$PENDING_DIR"/*.json; do
        [ -f "$f" ] || continue
        TYPE=$(jq -r '.type // empty' "$f" 2>/dev/null)
        if [ "$TYPE" = "elicitation" ]; then
            rm -f "$f"
        fi
    done
fi

# Reset session status back to working
if [ -f "$INFO_FILE" ]; then
    TMP=$(mktemp)
    jq '.status = "working" | .waiting_for = null | .last_updated = now' "$INFO_FILE" > "$TMP" && mv "$TMP" "$INFO_FILE"
fi
