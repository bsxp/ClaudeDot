#!/usr/bin/env python3
"""Hook: Notification — tracks idle/elicitation notifications.

Non-blocking. Reads hook input from stdin.
"""

import json
import os
import sys
import time

STATE_DIR = os.path.expanduser("~/.claude-helper")


def main():
    try:
        input_data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    session_id = input_data.get("session_id", "")
    if not session_id:
        sys.exit(0)

    info_file = os.path.join(STATE_DIR, "sessions", session_id, "info.json")
    if not os.path.isfile(info_file):
        sys.exit(0)

    ntype = input_data.get("notification_type", "")
    title = input_data.get("title", "notification")

    # Determine waiting_for based on notification type or title
    match = (ntype or title).lower()
    if "idle" in match or "input" in match:
        waiting_for = "input"
    elif "elicitation" in match:
        waiting_for = "elicitation"
    else:
        waiting_for = title

    try:
        with open(info_file, "r") as f:
            info = json.load(f)
        # Don't overwrite active permission/question status —
        # those are managed by their own hooks and should persist
        # until explicitly cleared by tool_activity or the tray app.
        if info.get("status") in ("permission", "question"):
            sys.exit(0)
        info["status"] = "idle"
        info["waiting_for"] = waiting_for
        info["last_updated"] = int(time.time())
        with open(info_file, "w") as f:
            json.dump(info, f, indent=4)
    except (json.JSONDecodeError, IOError):
        pass


if __name__ == "__main__":
    main()
