#!/usr/bin/env python3
"""Hook: PostToolUse (AskUserQuestion) — cleans up elicitation pending files.

Non-blocking. Runs after user answers the question in the terminal.
"""

import json
import os
import re
import sys
import time

STATE_DIR = os.path.expanduser("~/.claude-helper")
_SAFE_ID = re.compile(r'^[a-zA-Z0-9_-]{1,128}$')


def main():
    try:
        input_data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    session_id = input_data.get("session_id", "")
    if not session_id or not _SAFE_ID.match(session_id):
        sys.exit(0)

    pending_dir = os.path.join(STATE_DIR, "sessions", session_id, "pending")
    info_file = os.path.join(STATE_DIR, "sessions", session_id, "info.json")

    # Remove all elicitation pending files for this session
    if os.path.isdir(pending_dir):
        for filename in os.listdir(pending_dir):
            if not filename.endswith(".json"):
                continue
            filepath = os.path.join(pending_dir, filename)
            try:
                with open(filepath, "r") as f:
                    data = json.load(f)
                if data.get("type") == "elicitation":
                    os.unlink(filepath)
            except (json.JSONDecodeError, IOError, OSError):
                continue

    # Reset session status back to working
    if os.path.isfile(info_file):
        try:
            with open(info_file, "r") as f:
                info = json.load(f)
            info["status"] = "working"
            info["waiting_for"] = None
            info["last_updated"] = int(time.time())
            with open(info_file, "w") as f:
                json.dump(info, f, indent=4)
        except (json.JSONDecodeError, IOError):
            pass


if __name__ == "__main__":
    main()
