#!/usr/bin/env bash
# Hook: SessionStart — registers a new Claude Code session
# Non-blocking. Reads hook input from stdin.

set -euo pipefail

STATE_DIR="$HOME/.claude-helper"
INPUT=$(cat)

SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')
if [ -z "$SESSION_ID" ]; then
    exit 0
fi

CWD=$(echo "$INPUT" | jq -r '.cwd // "unknown"')
PROJECT_NAME=$(basename "$CWD")

SESSION_DIR="$STATE_DIR/sessions/$SESSION_ID"
mkdir -p "$SESSION_DIR/pending"

# Capture the parent PID (Claude Code process) for liveness checking
PARENT_PID=$PPID

cat > "$SESSION_DIR/info.json" <<EOF
{
    "session_id": "$SESSION_ID",
    "cwd": "$CWD",
    "project_name": "$PROJECT_NAME",
    "parent_pid": $PARENT_PID,
    "status": "working",
    "waiting_for": null,
    "last_updated": $(date +%s)
}
EOF
