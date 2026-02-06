#!/usr/bin/env bash
# Hook: SessionEnd — cleans up a finished session
# Non-blocking. Reads hook input from stdin.

set -euo pipefail

STATE_DIR="$HOME/.claude-helper"
INPUT=$(cat)

SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')
if [ -z "$SESSION_ID" ]; then
    exit 0
fi

SESSION_DIR="$STATE_DIR/sessions/$SESSION_ID"
if [ -d "$SESSION_DIR" ]; then
    rm -rf "$SESSION_DIR"
fi
