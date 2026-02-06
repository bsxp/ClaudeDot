#!/usr/bin/env python3
"""
Hook: PermissionRequest — BLOCKING hook that polls for menu bar response.

Writes pending request details to the session's pending directory,
then polls for a response file written by the menu bar app.
Times out after 5 minutes, causing Claude Code to fall back to the terminal dialog.
"""

import json
import os
import signal
import sys
import time
import uuid

STATE_DIR = os.path.expanduser("~/.claude-helper")
RESPONSES_DIR = os.path.join(STATE_DIR, "responses")
POLL_INTERVAL = 0.5  # seconds
TIMEOUT = 300  # 5 minutes


def main():
    try:
        input_data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        sys.exit(1)

    session_id = input_data.get("session_id", "")
    if not session_id:
        sys.exit(1)

    session_dir = os.path.join(STATE_DIR, "sessions", session_id)
    pending_dir = os.path.join(session_dir, "pending")

    if not os.path.isdir(session_dir):
        # Session not registered — let terminal handle it
        sys.exit(1)

    os.makedirs(pending_dir, exist_ok=True)
    os.makedirs(RESPONSES_DIR, exist_ok=True)

    # Generate a unique request ID
    request_id = str(uuid.uuid4())

    # Extract permission request details
    tool_name = input_data.get("tool_name", "unknown")
    tool_input = input_data.get("tool_input", {})

    # Build a human-readable description of the request
    description = _describe_request(tool_name, tool_input)

    pending_file = os.path.join(pending_dir, f"{request_id}.json")
    response_file = os.path.join(RESPONSES_DIR, f"{request_id}.json")
    info_file = os.path.join(session_dir, "info.json")

    # Register signal handlers so cleanup runs even on SIGTERM
    def _handle_signal(signum, frame):
        _cleanup(pending_file, response_file)
        _update_session_status(info_file, "working")
        sys.exit(1)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGHUP, _handle_signal)

    # Write pending request (include PID so menu bar can detect stale requests)
    request_data = {
        "id": request_id,
        "session_id": session_id,
        "pid": os.getpid(),
        "tool_name": tool_name,
        "tool_input": tool_input,
        "description": description,
        "timestamp": time.time(),
    }

    with open(pending_file, "w") as f:
        json.dump(request_data, f)

    # Update session info to reflect permission-needed status
    if os.path.isfile(info_file):
        try:
            with open(info_file, "r") as f:
                info = json.load(f)
            info["status"] = "permission"
            info["waiting_for"] = "permission"
            info["last_updated"] = int(time.time())
            with open(info_file, "w") as f:
                json.dump(info, f)
        except (json.JSONDecodeError, IOError):
            pass

    # Poll for response
    start_time = time.time()

    try:
        while time.time() - start_time < TIMEOUT:
            if os.path.isfile(response_file):
                try:
                    with open(response_file, "r") as f:
                        response = json.load(f)
                except (json.JSONDecodeError, IOError):
                    time.sleep(POLL_INTERVAL)
                    continue

                # Clean up
                _cleanup(pending_file, response_file)

                # Restore session status
                _update_session_status(info_file, "working")

                # Output the decision in Claude Code's expected format
                decision = response.get("decision", "deny")
                # Map menu bar decisions to Claude Code's permissionDecision values
                decision_map = {
                    "allow": "allow",
                    "always_allow": "allow",
                    "deny": "deny",
                }
                perm_decision = decision_map.get(decision, "deny")
                reason = "Approved via Claude Helper menu bar" if perm_decision == "allow" else "Denied via Claude Helper menu bar"
                output = {
                    "hookSpecificOutput": {
                        "hookEventName": "PermissionRequest",
                        "permissionDecision": perm_decision,
                        "permissionDecisionReason": reason,
                    }
                }
                print(json.dumps(output))
                sys.exit(0)

            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        pass
    finally:
        # Clean up pending file on timeout or interruption
        _cleanup(pending_file, response_file)
        _update_session_status(info_file, "working")

    # Timeout — exit 1 so Claude Code falls back to terminal
    sys.exit(1)


def _describe_request(tool_name, tool_input):
    """Build a human-readable description of the permission request."""
    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        desc = tool_input.get("description", "")
        if desc:
            return f"[Bash] {desc}"
        # Show first 80 chars of command
        if len(cmd) > 80:
            return f"[Bash] {cmd[:80]}..."
        return f"[Bash] {cmd}"
    elif tool_name == "Edit":
        path = tool_input.get("file_path", "unknown")
        return f"[Edit] {os.path.basename(path)}"
    elif tool_name == "Write":
        path = tool_input.get("file_path", "unknown")
        return f"[Write] {os.path.basename(path)}"
    elif tool_name == "Read":
        path = tool_input.get("file_path", "unknown")
        return f"[Read] {os.path.basename(path)}"
    else:
        return f"[{tool_name}]"


def _cleanup(pending_file, response_file):
    """Remove pending and response files."""
    for f in (pending_file, response_file):
        try:
            os.unlink(f)
        except FileNotFoundError:
            pass


def _update_session_status(info_file, status):
    """Update session info status."""
    if not os.path.isfile(info_file):
        return
    try:
        with open(info_file, "r") as f:
            info = json.load(f)
        info["status"] = status
        info["waiting_for"] = None
        info["last_updated"] = int(time.time())
        with open(info_file, "w") as f:
            json.dump(info, f)
    except (json.JSONDecodeError, IOError):
        pass


if __name__ == "__main__":
    main()
