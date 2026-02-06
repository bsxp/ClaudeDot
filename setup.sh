#!/usr/bin/env bash
# setup.sh — Install dependencies, configure hooks, create state directories
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STATE_DIR="$HOME/.claude-helper"
VENV_DIR="$STATE_DIR/venv"
CLAUDE_SETTINGS="$HOME/.claude/settings.json"

echo "=== Claude Helper Setup ==="

# 1. Create virtual environment and install dependencies
echo ""
echo "[1/4] Setting up Python virtual environment..."
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip -q
"$VENV_DIR/bin/pip" install rumps -q
echo "  ✓ venv created at $VENV_DIR"
echo "  ✓ rumps installed"

# 2. Create state directories
echo ""
echo "[2/4] Creating state directories..."
mkdir -p "$STATE_DIR/sessions"
mkdir -p "$STATE_DIR/responses"
echo "  ✓ $STATE_DIR/sessions"
echo "  ✓ $STATE_DIR/responses"

# 3. Make hook scripts executable
echo ""
echo "[3/4] Making hooks executable..."
chmod +x "$SCRIPT_DIR/hooks/session_start.sh"
chmod +x "$SCRIPT_DIR/hooks/session_end.sh"
chmod +x "$SCRIPT_DIR/hooks/prompt_submit.sh"
chmod +x "$SCRIPT_DIR/hooks/permission_request.py"
chmod +x "$SCRIPT_DIR/hooks/elicitation_request.py"
chmod +x "$SCRIPT_DIR/hooks/elicitation_cleanup.sh"
chmod +x "$SCRIPT_DIR/hooks/tool_activity.sh"
chmod +x "$SCRIPT_DIR/hooks/notification.sh"
chmod +x "$SCRIPT_DIR/hooks/stop.sh"
echo "  ✓ All hooks are executable"

# 4. Merge hook configuration into Claude settings
echo ""
echo "[4/4] Configuring Claude Code hooks..."

# Ensure settings directory and file exist
mkdir -p "$(dirname "$CLAUDE_SETTINGS")"
if [ ! -f "$CLAUDE_SETTINGS" ]; then
    echo '{}' > "$CLAUDE_SETTINGS"
fi

# Build the hooks config JSON
HOOKS_CONFIG=$(cat <<ENDJSON
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup|resume",
        "hooks": [
          {
            "type": "command",
            "command": "$SCRIPT_DIR/hooks/session_start.sh"
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "$SCRIPT_DIR/hooks/session_end.sh"
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "$SCRIPT_DIR/hooks/prompt_submit.sh"
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "AskUserQuestion",
        "hooks": [
          {
            "type": "command",
            "command": "python3 $SCRIPT_DIR/hooks/elicitation_request.py"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "AskUserQuestion",
        "hooks": [
          {
            "type": "command",
            "command": "$SCRIPT_DIR/hooks/elicitation_cleanup.sh"
          }
        ]
      },
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "$SCRIPT_DIR/hooks/tool_activity.sh"
          }
        ]
      }
    ],
    "PermissionRequest": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python3 $SCRIPT_DIR/hooks/permission_request.py",
            "timeout": 310
          }
        ]
      }
    ],
    "Notification": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "$SCRIPT_DIR/hooks/notification.sh",
            "async": true
          }
        ]
      }
    ],
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "$SCRIPT_DIR/hooks/stop.sh",
            "async": true
          }
        ]
      }
    ]
  }
}
ENDJSON
)

# Check if jq is available for merging
if command -v jq &>/dev/null; then
    # Deep merge: existing settings + hook config
    # Preserve any existing hooks by merging at the hooks level
    EXISTING=$(cat "$CLAUDE_SETTINGS")
    MERGED=$(echo "$EXISTING" | jq --argjson hooks "$(echo "$HOOKS_CONFIG" | jq '.hooks')" '
        .hooks = ((.hooks // {}) * $hooks)
    ')
    echo "$MERGED" | jq '.' > "$CLAUDE_SETTINGS"
    echo "  ✓ Hooks merged into $CLAUDE_SETTINGS (existing settings preserved)"
else
    # No jq — warn and write if no hooks exist
    if grep -q '"hooks"' "$CLAUDE_SETTINGS" 2>/dev/null; then
        echo "  ⚠ jq not found. Cannot safely merge hooks."
        echo "  Please install jq (brew install jq) and re-run setup,"
        echo "  or manually add hooks to $CLAUDE_SETTINGS"
        echo ""
        echo "  Hooks config to add:"
        echo "$HOOKS_CONFIG"
        exit 1
    else
        # Safe to write — no existing hooks
        EXISTING=$(cat "$CLAUDE_SETTINGS")
        python3 -c "
import json, sys
existing = json.loads('''$EXISTING''')
hooks = json.loads('''$HOOKS_CONFIG''')
existing.update(hooks)
json.dump(existing, sys.stdout, indent=2)
" > "$CLAUDE_SETTINGS"
        echo "  ✓ Hooks written to $CLAUDE_SETTINGS"
    fi
fi

echo ""
echo "=== Setup Complete ==="
echo ""
echo "To start the menu bar app:"
echo "  $VENV_DIR/bin/python $SCRIPT_DIR/claude_helper.py"
echo ""
echo "To enable auto-start on login:"
echo "  Click 'Auto-start: Off' in the menu bar dropdown"
echo ""
echo "To verify:"
echo "  1. Start a Claude Code session"
echo "  2. Ask Claude to run a bash command"
echo "  3. Check the menu bar for 'CC !'"
