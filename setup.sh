#!/usr/bin/env bash
# setup.sh — Install dependencies, configure hooks, create state directories
# Works on macOS and Linux.
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
"$VENV_DIR/bin/pip" install -r "$SCRIPT_DIR/requirements.txt" -q
echo "  + venv created at $VENV_DIR"
echo "  + dependencies installed (pystray, Pillow, psutil)"

# 2. Create state directories
echo ""
echo "[2/4] Creating state directories..."
mkdir -p "$STATE_DIR/sessions"
mkdir -p "$STATE_DIR/responses"
echo "  + $STATE_DIR/sessions"
echo "  + $STATE_DIR/responses"

# 3. Make hook scripts executable
echo ""
echo "[3/4] Making hooks executable..."
chmod +x "$SCRIPT_DIR"/hooks/*.py
echo "  + All hooks are executable"

# 4. Merge hook configuration into Claude settings
echo ""
echo "[4/4] Configuring Claude Code hooks..."

# Ensure settings directory and file exist
mkdir -p "$(dirname "$CLAUDE_SETTINGS")"
if [ ! -f "$CLAUDE_SETTINGS" ]; then
    echo '{}' > "$CLAUDE_SETTINGS"
fi

HOOKS_DIR="$SCRIPT_DIR/hooks"
VENV_PYTHON="$VENV_DIR/bin/python"

"$VENV_PYTHON" "$SCRIPT_DIR/merge_hooks.py" "$CLAUDE_SETTINGS" "$HOOKS_DIR" "$VENV_PYTHON"

echo ""
echo "=== Setup Complete ==="
echo ""
echo "To start the system tray app:"
echo "  $VENV_PYTHON $SCRIPT_DIR/claude_helper.py"
echo ""
echo "To enable auto-start on login:"
echo "  Click 'Auto-start: Off' in the system tray dropdown"
echo ""
echo "To verify:"
echo "  1. Start a Claude Code session"
echo "  2. Ask Claude to run a bash command"
echo "  3. Check the system tray for the Claude Helper icon"
