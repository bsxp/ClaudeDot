#!/usr/bin/env python3
"""
Claude Helper — macOS menu bar utility for Claude Code.

Polls ~/.claude-helper/ for session state and pending permission requests.
Lets you respond to permission prompts (Allow/Deny/Always Allow) without
switching to the terminal. Shows elicitation questions as notifications.
"""

import json
import math
import os
import struct
import subprocess
import sys
import time
import zlib

import rumps

STATE_DIR = os.path.expanduser("~/.claude-helper")
SESSIONS_DIR = os.path.join(STATE_DIR, "sessions")
RESPONSES_DIR = os.path.join(STATE_DIR, "responses")
ICONS_DIR = os.path.join(STATE_DIR, "icons")
CLAUDE_PROJECTS_DIR = os.path.expanduser("~/.claude/projects")
LAUNCHD_LABEL = "com.claude-helper"
PLIST_DEST = os.path.expanduser(f"~/Library/LaunchAgents/{LAUNCHD_LABEL}.plist")

CONFIG_FILE = os.path.join(STATE_DIR, "config.json")
POLL_INTERVAL = 2  # seconds
STALE_THRESHOLD = 86400  # 24 hours
DISCOVER_EVERY = 5  # run process discovery every N poll cycles


def _read_config():
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, IOError):
        return {}


def _write_config(config):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def _generate_dot_png(path, color, filled, size=18):
    """Generate a circle dot icon as a PNG file."""
    center = size / 2
    radius = size * 0.35
    stroke = 1.5
    rows = []
    for y in range(size):
        row = []
        for x in range(size):
            dx = x - center + 0.5
            dy = y - center + 0.5
            dist = math.sqrt(dx * dx + dy * dy)

            if filled:
                # Solid filled circle with anti-aliased edge
                if dist <= radius:
                    edge_dist = radius - dist
                    alpha = min(1.0, edge_dist * 2.0)
                    row.extend([color[0], color[1], color[2], int(alpha * color[3])])
                else:
                    row.extend([0, 0, 0, 0])
            else:
                # Ring / hollow circle
                ring_dist = abs(dist - radius)
                if ring_dist <= stroke:
                    alpha = min(1.0, (stroke - ring_dist) * 1.5)
                    row.extend([color[0], color[1], color[2], int(alpha * color[3])])
                else:
                    row.extend([0, 0, 0, 0])

        rows.append(bytes([0] + row))  # PNG filter byte + RGBA row

    raw_data = b"".join(rows)
    compressed = zlib.compress(raw_data)

    def chunk(ctype, data):
        c = ctype + data
        crc = struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
        return struct.pack(">I", len(data)) + c + crc

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", compressed)
    png += chunk(b"IEND", b"")

    with open(path, "wb") as f:
        f.write(png)


def _ensure_icons():
    """Generate menu bar icons. Returns (empty, blue, yellow) paths."""
    os.makedirs(ICONS_DIR, exist_ok=True)
    empty_path = os.path.join(ICONS_DIR, "dot_empty.png")
    blue_path = os.path.join(ICONS_DIR, "dot_blue.png")
    yellow_path = os.path.join(ICONS_DIR, "dot_yellow.png")

    # Regenerate all if any old icons exist or any are missing
    needs_regen = (
        not os.path.isfile(empty_path)
        or not os.path.isfile(blue_path)
        or not os.path.isfile(yellow_path)
        or os.path.isfile(os.path.join(ICONS_DIR, "icon_default.png"))
        or os.path.isfile(os.path.join(ICONS_DIR, "dot_filled.png"))
    )
    if needs_regen:
        _generate_dot_png(empty_path, (0, 0, 0, 255), filled=False, size=18)
        _generate_dot_png(blue_path, (59, 130, 246, 255), filled=True, size=18)
        _generate_dot_png(yellow_path, (234, 179, 8, 255), filled=True, size=18)
        # Clean up old icons
        for old in ("icon_default.png", "icon_blue.png", "dot_filled.png"):
            try:
                os.unlink(os.path.join(ICONS_DIR, old))
            except FileNotFoundError:
                pass

    return empty_path, blue_path, yellow_path


def _rmtree(path):
    """Remove a directory tree, ignoring errors."""
    import shutil
    try:
        shutil.rmtree(path)
    except Exception:
        pass


def _pid_alive(pid):
    """Check if a process with the given PID is still running."""
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


class ClaudeHelperApp(rumps.App):
    def __init__(self):
        icon_empty, icon_blue, icon_yellow = _ensure_icons()
        super().__init__("", icon=icon_empty, template=True, quit_button=None)
        self.icon_empty = icon_empty
        self.icon_blue = icon_blue
        self.icon_yellow = icon_yellow
        self.sessions = {}
        self.pending_requests = {}
        self.autostart_item = rumps.MenuItem("Auto-start: Off", callback=self._toggle_autostart)
        self._refresh_autostart_state()
        self.elicitation_mode_item = rumps.MenuItem("Questions: Terminal", callback=self._toggle_elicitation_mode)
        self._refresh_elicitation_mode()

        self._discover_counter = DISCOVER_EVERY  # trigger on first poll
        self.timer = rumps.Timer(self._poll, POLL_INTERVAL)
        self.timer.start()

        self._cleanup_stale_sessions()

    def _poll(self, _=None):
        self._discover_counter += 1
        if self._discover_counter >= DISCOVER_EVERY:
            self._discover_counter = 0
            self._discover_unregistered_sessions()
        self._read_sessions()
        self._cleanup_dead_sessions()
        self._read_pending_requests()
        self._cleanup_stale_pending()
        self._update_icon()
        self._rebuild_menu()

    def _read_sessions(self):
        self.sessions = {}
        if not os.path.isdir(SESSIONS_DIR):
            return
        for session_id in os.listdir(SESSIONS_DIR):
            info_file = os.path.join(SESSIONS_DIR, session_id, "info.json")
            if not os.path.isfile(info_file):
                continue
            try:
                with open(info_file, "r") as f:
                    self.sessions[session_id] = json.load(f)
            except (json.JSONDecodeError, IOError):
                continue

    def _cleanup_dead_sessions(self):
        """Remove sessions whose parent Claude Code process is no longer running."""
        dead = []
        for session_id, data in self.sessions.items():
            pid = data.get("parent_pid")
            if pid is None:
                # Legacy session without parent_pid — remove it
                dead.append(session_id)
            elif not _pid_alive(pid):
                dead.append(session_id)
        for session_id in dead:
            del self.sessions[session_id]
            _rmtree(os.path.join(SESSIONS_DIR, session_id))

    def _discover_unregistered_sessions(self):
        """Find running Claude processes not yet registered via hooks."""
        # Find all claude PIDs and their working directories
        try:
            ps_result = subprocess.run(
                ["ps", "-axo", "pid=,command="],
                capture_output=True, text=True, timeout=5,
            )
            if ps_result.returncode != 0:
                return
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return

        claude_pids = []
        for line in ps_result.stdout.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            pid_str, cmd = parts
            cmd_base = cmd.split()[0] if cmd else ""
            if os.path.basename(cmd_base) == "claude":
                try:
                    claude_pids.append(int(pid_str))
                except ValueError:
                    continue

        if not claude_pids:
            return

        # Filter out PIDs already registered
        registered_pids = {
            data.get("parent_pid") for data in self.sessions.values()
        }
        unregistered = [p for p in claude_pids if p not in registered_pids]
        if not unregistered:
            return

        # Get working directories via lsof
        try:
            pids_arg = ",".join(str(p) for p in unregistered)
            lsof_result = subprocess.run(
                ["lsof", "-a", "-p", pids_arg, "-d", "cwd", "-Fn"],
                capture_output=True, text=True, timeout=5,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return

        pid_cwd = {}
        current_pid = None
        for line in lsof_result.stdout.strip().split("\n"):
            if line.startswith("p"):
                try:
                    current_pid = int(line[1:])
                except ValueError:
                    current_pid = None
            elif line.startswith("n") and current_pid is not None:
                pid_cwd[current_pid] = line[1:]
                current_pid = None

        for pid, cwd in pid_cwd.items():
            # Map cwd to Claude project directory
            project_dir_name = cwd.replace("/", "-")
            project_dir = os.path.join(CLAUDE_PROJECTS_DIR, project_dir_name)
            if not os.path.isdir(project_dir):
                continue

            # Find the most recently modified session .jsonl file
            best_sid = None
            best_mtime = 0
            try:
                for fname in os.listdir(project_dir):
                    if not fname.endswith(".jsonl"):
                        continue
                    fpath = os.path.join(project_dir, fname)
                    try:
                        mtime = os.path.getmtime(fpath)
                        if mtime > best_mtime:
                            best_mtime = mtime
                            best_sid = fname[:-6]  # strip .jsonl
                    except OSError:
                        continue
            except OSError:
                continue

            if not best_sid or best_sid in self.sessions:
                continue

            # Register the discovered session
            session_dir = os.path.join(SESSIONS_DIR, best_sid)
            os.makedirs(os.path.join(session_dir, "pending"), exist_ok=True)
            info = {
                "session_id": best_sid,
                "cwd": cwd,
                "project_name": os.path.basename(cwd),
                "parent_pid": pid,
                "status": "working",
                "waiting_for": None,
                "last_updated": int(time.time()),
            }
            try:
                with open(os.path.join(session_dir, "info.json"), "w") as f:
                    json.dump(info, f, indent=4)
            except IOError:
                continue

    def _read_pending_requests(self):
        self.pending_requests = {}
        if not os.path.isdir(SESSIONS_DIR):
            return
        for session_id in os.listdir(SESSIONS_DIR):
            pending_dir = os.path.join(SESSIONS_DIR, session_id, "pending")
            if not os.path.isdir(pending_dir):
                continue
            for filename in os.listdir(pending_dir):
                if not filename.endswith(".json"):
                    continue
                filepath = os.path.join(pending_dir, filename)
                try:
                    with open(filepath, "r") as f:
                        data = json.load(f)
                    request_id = data.get("id", filename[:-5])
                    data["_session_id"] = session_id
                    self.pending_requests[request_id] = data
                except (json.JSONDecodeError, IOError):
                    continue

    def _cleanup_stale_pending(self):
        """Remove pending requests whose hook process has exited (PID-based, permissions only)."""
        stale_ids = []
        for request_id, req in self.pending_requests.items():
            pid = req.get("pid")
            # Only PID-check permission requests (elicitations have no PID)
            if pid and not _pid_alive(pid):
                stale_ids.append(request_id)
        for request_id in stale_ids:
            req = self.pending_requests.pop(request_id, None)
            if req:
                sid = req.get("session_id", req.get("_session_id", ""))
                pending_file = os.path.join(SESSIONS_DIR, sid, "pending", f"{request_id}.json")
                try:
                    os.unlink(pending_file)
                except FileNotFoundError:
                    pass

    def _update_icon(self):
        """Update menu bar icon based on session states."""
        has_actionable = (
            any(s.get("status") in ("permission", "question") for s in self.sessions.values())
            or len(self.pending_requests) > 0
        )
        has_waiting = any(
            s.get("status") in ("done", "idle") for s in self.sessions.values()
        )

        if has_actionable:
            self.icon = self.icon_blue
            self.template = False
        elif has_waiting:
            self.icon = self.icon_yellow
            self.template = False
        else:
            self.icon = self.icon_empty
            self.template = True

    def _rebuild_menu(self):
        self.menu.clear()

        if not self.sessions:
            self.menu.add(rumps.MenuItem("No active sessions", callback=None))
        else:
            sorted_sessions = sorted(
                self.sessions.items(),
                key=lambda x: (
                    0 if x[1].get("status") == "question" else
                    1 if x[1].get("status") == "permission" else
                    2 if x[1].get("status") in ("done", "idle") else 3
                ),
            )
            for session_id, session in sorted_sessions:
                self._add_session_menu(session_id, session)

        self.menu.add(rumps.separator)
        self._refresh_elicitation_mode()
        self.menu.add(self.elicitation_mode_item)
        self._refresh_autostart_state()
        self.menu.add(self.autostart_item)
        self.menu.add(rumps.MenuItem("Quit", callback=self._quit))

    def _add_session_menu(self, session_id, session):
        project = session.get("project_name", "unknown")
        status = session.get("status", "unknown")

        # Collect pending requests for this session, split by type
        session_requests = {
            rid: req for rid, req in self.pending_requests.items()
            if req.get("session_id", req.get("_session_id")) == session_id
        }
        elicitations = {rid: r for rid, r in session_requests.items() if r.get("type") == "elicitation"}
        permissions = {rid: r for rid, r in session_requests.items() if r.get("type") != "elicitation"}

        # Session label with status icons
        if elicitations:
            label = f"\U0001F535 {project} — question"
        elif status == "permission" or permissions:
            label = f"\U0001F534 {project} — permission needed"
        elif status in ("done", "idle"):
            label = f"\U0001F7E1 {project} — {status}"
        else:
            label = f"\U0001F7E2 {project}"

        session_menu = rumps.MenuItem(label)

        # Elicitation questions
        elicitation_mode = _read_config().get("elicitation_mode", "terminal")
        for request_id, request in elicitations.items():
            for q in request.get("questions", []):
                q_text = q.get("question", "Question")
                q_idx = q.get("index", 0)
                options = q.get("options", [])
                q_menu = rumps.MenuItem(q_text)

                if elicitation_mode == "menubar":
                    # Interactive — clickable options
                    for opt in options:
                        cb = self._make_elicitation_callback(request_id, q_idx, opt, request.get("questions", []))
                        q_menu.add(rumps.MenuItem(opt, callback=cb))
                else:
                    # Info-only — user answers in terminal
                    for opt in options:
                        q_menu.add(rumps.MenuItem(f"  {opt}", callback=None))
                    q_menu.add(rumps.separator)
                    q_menu.add(rumps.MenuItem("Answer in terminal", callback=None))

                session_menu.add(q_menu)

        # Permission requests — interactive (Allow/Deny/Always Allow)
        for request_id, request in permissions.items():
            desc = request.get("description", "Permission request")
            req_menu = rumps.MenuItem(desc)

            allow_cb = self._make_decision_callback(request_id, "allow")
            always_cb = self._make_decision_callback(request_id, "always_allow")
            deny_cb = self._make_decision_callback(request_id, "deny")

            req_menu.add(rumps.MenuItem("Allow", callback=allow_cb))
            req_menu.add(rumps.MenuItem("Always Allow", callback=always_cb))
            req_menu.add(rumps.MenuItem("Deny", callback=deny_cb))
            session_menu.add(req_menu)

        if not session_requests:
            if status in ("done", "idle"):
                session_menu.add(rumps.MenuItem("Waiting for your input", callback=None))
            else:
                session_menu.add(rumps.MenuItem("Working...", callback=None))

        cwd = session.get("cwd", "")
        if cwd:
            session_menu.add(rumps.separator)
            session_menu.add(rumps.MenuItem(f"  {cwd}", callback=None))

        self.menu.add(session_menu)

    def _make_decision_callback(self, request_id, decision):
        def callback(_):
            self._write_decision(request_id, decision)
        return callback

    def _write_decision(self, request_id, decision):
        os.makedirs(RESPONSES_DIR, exist_ok=True)
        response_file = os.path.join(RESPONSES_DIR, f"{request_id}.json")
        response = {"id": request_id, "decision": decision, "timestamp": time.time()}
        try:
            with open(response_file, "w") as f:
                json.dump(response, f)
        except IOError as e:
            rumps.notification("Claude Helper", "Error", f"Failed to write decision: {e}")

    def _make_elicitation_callback(self, request_id, question_index, selected_label, all_questions):
        def callback(_):
            self._write_elicitation_answer(request_id, question_index, selected_label)
        return callback

    def _write_elicitation_answer(self, request_id, question_index, selected_label):
        os.makedirs(RESPONSES_DIR, exist_ok=True)
        response_file = os.path.join(RESPONSES_DIR, f"{request_id}.json")
        # Support multi-question: merge with existing partial answers
        answers = {}
        if os.path.isfile(response_file):
            try:
                with open(response_file, "r") as f:
                    answers = json.load(f).get("answers", {})
            except (json.JSONDecodeError, IOError):
                pass
        answers[str(question_index)] = selected_label
        response = {"id": request_id, "answers": answers, "timestamp": time.time()}
        try:
            with open(response_file, "w") as f:
                json.dump(response, f)
        except IOError as e:
            rumps.notification("Claude Helper", "Error", f"Failed to write answer: {e}")

    def _toggle_elicitation_mode(self, _):
        config = _read_config()
        current = config.get("elicitation_mode", "terminal")
        config["elicitation_mode"] = "terminal" if current == "menubar" else "menubar"
        _write_config(config)
        self._refresh_elicitation_mode()

    def _refresh_elicitation_mode(self):
        mode = _read_config().get("elicitation_mode", "terminal")
        if mode == "menubar":
            self.elicitation_mode_item.title = "Questions: Menu Bar"
        else:
            self.elicitation_mode_item.title = "Questions: Terminal"

    def _cleanup_stale_sessions(self):
        if not os.path.isdir(SESSIONS_DIR):
            return
        now = time.time()
        for session_id in os.listdir(SESSIONS_DIR):
            info_file = os.path.join(SESSIONS_DIR, session_id, "info.json")
            if not os.path.isfile(info_file):
                _rmtree(os.path.join(SESSIONS_DIR, session_id))
                continue
            try:
                with open(info_file, "r") as f:
                    data = json.load(f)
                if now - data.get("last_updated", 0) > STALE_THRESHOLD:
                    _rmtree(os.path.join(SESSIONS_DIR, session_id))
            except (json.JSONDecodeError, IOError):
                _rmtree(os.path.join(SESSIONS_DIR, session_id))

    def _toggle_autostart(self, sender):
        if self._is_autostart_enabled():
            self._disable_autostart()
        else:
            self._enable_autostart()
        self._refresh_autostart_state()

    def _is_autostart_enabled(self):
        if not os.path.isfile(PLIST_DEST):
            return False
        try:
            import plistlib
            with open(PLIST_DEST, "rb") as f:
                plist = plistlib.load(f)
            return plist.get("RunAtLoad", False)
        except Exception:
            return False

    def _enable_autostart(self):
        try:
            import plistlib
            app_path = os.path.dirname(os.path.abspath(__file__))
            venv_python = os.path.join(STATE_DIR, "venv", "bin", "python")
            python_path = venv_python if os.path.isfile(venv_python) else sys.executable
            plist = {
                "Label": LAUNCHD_LABEL,
                "ProgramArguments": [python_path, os.path.join(app_path, "claude_helper.py")],
                "RunAtLoad": True,
                "KeepAlive": False,
                "StandardOutPath": os.path.join(STATE_DIR, "claude-helper.log"),
                "StandardErrorPath": os.path.join(STATE_DIR, "claude-helper.err"),
            }
            os.makedirs(os.path.dirname(PLIST_DEST), exist_ok=True)
            with open(PLIST_DEST, "wb") as f:
                plistlib.dump(plist, f)
            subprocess.run(["launchctl", "load", PLIST_DEST], check=False)
        except Exception as e:
            rumps.notification("Claude Helper", "Error", f"Failed to enable auto-start: {e}")

    def _disable_autostart(self):
        try:
            if os.path.isfile(PLIST_DEST):
                subprocess.run(["launchctl", "unload", PLIST_DEST], check=False)
                os.unlink(PLIST_DEST)
        except Exception as e:
            rumps.notification("Claude Helper", "Error", f"Failed to disable auto-start: {e}")

    def _refresh_autostart_state(self):
        if self._is_autostart_enabled():
            self.autostart_item.title = "Auto-start: On"
        else:
            self.autostart_item.title = "Auto-start: Off"

    def _quit(self, _):
        rumps.quit_application()


if __name__ == "__main__":
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    os.makedirs(RESPONSES_DIR, exist_ok=True)

    app = ClaudeHelperApp()
    app.run()
