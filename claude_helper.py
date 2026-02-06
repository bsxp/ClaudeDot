#!/usr/bin/env python3
"""
Claude Helper — cross-platform system tray utility for Claude Code.

Polls ~/.claude-helper/ for session state and pending permission requests.
Lets you respond to permission prompts (Allow/Deny) without switching
to the terminal. Shows elicitation questions as notifications.

Supports macOS (menu bar) and Windows (system tray).
"""

import json
import os
import shutil
import sys
import threading
import time

import psutil
import pystray
from PIL import Image, ImageDraw, ImageFont

STATE_DIR = os.path.expanduser("~/.claude-helper")
SESSIONS_DIR = os.path.join(STATE_DIR, "sessions")
RESPONSES_DIR = os.path.join(STATE_DIR, "responses")
CLAUDE_PROJECTS_DIR = os.path.expanduser("~/.claude/projects")

CONFIG_FILE = os.path.join(STATE_DIR, "config.json")
POLL_INTERVAL = 2  # seconds
STALE_THRESHOLD = 86400  # 24 hours
DISCOVER_EVERY = 5  # run process discovery every N poll cycles

IS_MACOS = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"

# Status icon defaults and choices
DEFAULT_STATUS_ICONS = {
    "working": "\u231b",       # ⌛
    "question": "\U0001F535",  # 🔵
    "permission": "\U0001F534",# 🔴
    "done": "\U0001F7E1",      # 🟡
    "idle": "\U0001F7E1",      # 🟡
}
STATUS_ICON_OPTIONS = {
    "working": ["\u231b", "\u2699\ufe0f", "\U0001F528", "\U0001F4BB", "\U0001F504", "\U0001F3D7\ufe0f"],
    "question": ["\U0001F535", "\u2753", "\U0001F4AC", "\U0001F5E8\ufe0f", "\U0001F914", "\U0001F4A1"],
    "permission": ["\U0001F534", "\U0001F512", "\u26a0\ufe0f", "\U0001F6D1", "\U0001F6A8", "\U0001F6E1\ufe0f"],
    "done": ["\U0001F7E1", "\u2705", "\U0001F7E2", "\U0001F389", "\U0001F44D", "\u2714\ufe0f"],
    "idle": ["\U0001F7E1", "\U0001F4A4", "\u23F8\ufe0f", "\U0001F7E2", "\U0001F311", "\u26AA"],
}
STATUS_LABELS = {
    "working": "Working",
    "question": "Question",
    "permission": "Permission",
    "done": "Done",
    "idle": "Idle",
}

# Auto-start constants (platform-specific)
if IS_MACOS:
    LAUNCHD_LABEL = "com.claude-helper"
    PLIST_DEST = os.path.expanduser(f"~/Library/LaunchAgents/{LAUNCHD_LABEL}.plist")
elif IS_WINDOWS:
    AUTOSTART_REG_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
    AUTOSTART_REG_NAME = "ClaudeHelper"


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


def _generate_dot_image(color, filled, size=64):
    """Generate a circle dot icon as a PIL Image."""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    margin = int(size * 0.15)
    bbox = [margin, margin, size - margin, size - margin]

    if filled:
        draw.ellipse(bbox, fill=color)
    else:
        stroke_width = max(2, size // 16)
        draw.ellipse(bbox, outline=color, width=stroke_width)

    return image


def _ensure_icons():
    """Generate system tray icons. Returns (empty, filled, blue) PIL Images."""
    empty = _generate_dot_image((180, 180, 180, 255), filled=False)
    filled = _generate_dot_image((180, 180, 180, 255), filled=True)
    blue = _generate_dot_image((59, 130, 246, 255), filled=True)
    return empty, filled, blue


_emoji_font_cache = {}

# Apple Color Emoji is a bitmap font with fixed valid sizes.
# Other emoji fonts (e.g. Segoe UI Emoji on Windows) are scalable.
_APPLE_EMOJI_SIZES = [20, 32, 40, 48, 64, 96, 160]


def _load_emoji_font(size):
    """Load an emoji-capable font at the given size. Cached by size."""
    if size in _emoji_font_cache:
        return _emoji_font_cache[size]

    font_paths = []
    if IS_MACOS:
        font_paths.append("/System/Library/Fonts/Apple Color Emoji.ttc")
    elif IS_WINDOWS:
        windir = os.environ.get("WINDIR", r"C:\Windows")
        font_paths.append(os.path.join(windir, "Fonts", "seguiemj.ttf"))

    # For bitmap fonts (Apple Color Emoji), snap to the nearest valid size
    sizes_to_try = [size]
    if IS_MACOS:
        # Pick the largest valid size <= requested, falling back to smallest
        valid = [s for s in _APPLE_EMOJI_SIZES if s <= size]
        sizes_to_try = sorted(valid, reverse=True) if valid else _APPLE_EMOJI_SIZES[:1]

    for path in font_paths:
        for sz in sizes_to_try:
            try:
                font = ImageFont.truetype(path, sz)
                _emoji_font_cache[size] = font
                return font
            except Exception:
                continue

    _emoji_font_cache[size] = None
    return None


def _generate_emoji_ring_icon(emoji_char, ring_color, size=64):
    """Generate a ring icon with an emoji character centered inside."""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # Draw ring
    margin = int(size * 0.1)
    stroke_width = max(2, size // 16)
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        outline=ring_color, width=stroke_width,
    )

    # Try to render emoji in center
    font = _load_emoji_font(int(size * 0.55))
    if font:
        try:
            bbox = font.getbbox(emoji_char)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            x = (size - tw) // 2 - bbox[0]
            y = (size - th) // 2 - bbox[1]
            try:
                draw.text((x, y), emoji_char, font=font, embedded_color=True)
            except TypeError:
                # Pillow < 8.0 doesn't support embedded_color
                draw.text((x, y), emoji_char, font=font, fill=ring_color)
        except Exception:
            pass

    return image


def _rmtree(path):
    """Remove a directory tree, ignoring errors."""
    try:
        shutil.rmtree(path)
    except Exception:
        pass


def _pid_alive(pid):
    """Check if a process with the given PID is still running."""
    try:
        return psutil.pid_exists(int(pid))
    except (ValueError, TypeError):
        return False


class ClaudeHelperApp:
    def __init__(self):
        self.icon_empty, self.icon_filled, self.icon_blue = _ensure_icons()
        self.sessions = {}
        self.pending_requests = {}
        self._discover_counter = DISCOVER_EVERY  # trigger on first poll
        self._running = True
        self._lock = threading.Lock()
        self._icon_cache = {}  # (emoji, ring_color) → PIL Image

        # Build initial menu
        menu = self._build_menu()
        self.icon = pystray.Icon(
            "claude-helper",
            icon=self.icon_empty,
            title="Claude Helper",
            menu=menu,
        )

        self._cleanup_stale_sessions()

    def run(self):
        """Start the app. Runs the polling loop in a background thread."""
        poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        poll_thread.start()
        self.icon.run()

    def _poll_loop(self):
        """Background polling loop."""
        while self._running:
            try:
                self._poll()
            except Exception:
                pass
            time.sleep(POLL_INTERVAL)

    def _poll(self):
        with self._lock:
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
                dead.append(session_id)
            elif not _pid_alive(pid):
                dead.append(session_id)
        for session_id in dead:
            del self.sessions[session_id]
            _rmtree(os.path.join(SESSIONS_DIR, session_id))

    def _discover_unregistered_sessions(self):
        """Find running Claude processes not yet registered via hooks."""
        try:
            claude_pids = []
            for proc in psutil.process_iter(["pid", "name"]):
                try:
                    name = proc.info["name"] or ""
                    if name.lower() in ("claude.exe", "claude"):
                        claude_pids.append(proc.info["pid"])
                except (psutil.NoSuchProcess, psutil.AccessDenied):
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

            # Get working directories
            pid_cwd = {}
            for pid in unregistered:
                try:
                    proc = psutil.Process(pid)
                    pid_cwd[pid] = proc.cwd()
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue

            for pid, cwd in pid_cwd.items():
                # Map cwd to Claude project directory
                # Normalize path separators for all platforms
                project_dir_name = cwd.replace(":", "-").replace("\\", "-").replace("/", "-")
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

                # Register the discovered session (only if not already on disk)
                session_dir = os.path.join(SESSIONS_DIR, best_sid)
                info_file = os.path.join(session_dir, "info.json")
                if os.path.isfile(info_file):
                    # Hook already created this session — update PID only
                    try:
                        with open(info_file, "r") as f:
                            info = json.load(f)
                        info["parent_pid"] = pid
                        with open(info_file, "w") as f:
                            json.dump(info, f, indent=4)
                    except (json.JSONDecodeError, IOError):
                        pass
                    continue

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
                    with open(info_file, "w") as f:
                        json.dump(info, f, indent=4)
                except IOError:
                    continue

        except Exception:
            pass

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
        """Housekeeping: remove orphaned pending files. Does not affect display."""
        stale_ids = []
        for request_id, req in self.pending_requests.items():
            pid = req.get("pid")
            sid = req.get("session_id", req.get("_session_id", ""))
            session = self.sessions.get(sid, {})
            session_status = session.get("status")
            # Hook process died without cleaning up its file
            if pid and not _pid_alive(pid):
                stale_ids.append(request_id)
            # Session has moved past this request
            elif req.get("type") == "elicitation" and session_status != "question":
                stale_ids.append(request_id)
            elif req.get("type") != "elicitation" and session_status != "permission":
                stale_ids.append(request_id)
            # Session no longer exists
            elif not session:
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
        """Update tray icon based on aggregate session status.

        Blue ring + emoji = needs attention (permission / question)
        Filled gray dot   = session idle/done (user should check)
        Hollow gray ring  = no sessions or all working

        Icon is driven ONLY by info.json status — pending files never
        influence icon state (they are sub-menu content only).
        """
        # Find the highest-priority notable status
        for status in ("question", "permission", "done", "idle"):
            if any(s.get("status") == status for s in self.sessions.values()):
                if status in ("question", "permission"):
                    emoji = self._get_status_icon(status)
                    ring_color = (59, 130, 246, 255)   # blue
                    cache_key = (emoji, ring_color)
                    if cache_key not in self._icon_cache:
                        self._icon_cache[cache_key] = _generate_emoji_ring_icon(
                            emoji, ring_color,
                        )
                    self.icon.icon = self._icon_cache[cache_key]
                else:
                    # done/idle → filled dot (user should go check)
                    self.icon.icon = self.icon_filled
                return

        self.icon.icon = self.icon_empty

    def _rebuild_menu(self):
        """Rebuild the tray menu with current session state."""
        self.icon.menu = self._build_menu()

    def _build_menu(self):
        """Build the pystray menu from current state."""
        items = []

        if not self.sessions:
            items.append(pystray.MenuItem("No active sessions", None, enabled=False))
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
                items.append(self._build_session_menu(session_id, session))

        items.append(pystray.Menu.SEPARATOR)

        # Elicitation mode toggle
        elicitation_mode = _read_config().get("elicitation_mode", "terminal")
        elicitation_label = f"Questions: {'Tray' if elicitation_mode == 'menubar' else 'Terminal'}"
        items.append(pystray.MenuItem(elicitation_label, self._toggle_elicitation_mode))

        # Status icons submenu
        items.append(self._build_status_icons_menu())

        # Auto-start toggle
        autostart_label = f"Auto-start: {'On' if self._is_autostart_enabled() else 'Off'}"
        items.append(pystray.MenuItem(autostart_label, self._toggle_autostart))

        items.append(pystray.MenuItem("Quit", self._quit))

        return pystray.Menu(*items)

    def _get_status_icon(self, status):
        """Get the configured emoji for a session status."""
        config = _read_config()
        icons = config.get("status_icons", {})
        return icons.get(status, DEFAULT_STATUS_ICONS.get(status, "\u2753"))

    def _build_session_menu(self, session_id, session):
        """Build a submenu for a single session."""
        project = session.get("project_name", "unknown")
        status = session.get("status", "unknown")

        # Collect pending requests for this session
        session_requests = {
            rid: req for rid, req in self.pending_requests.items()
            if req.get("session_id", req.get("_session_id")) == session_id
        }
        elicitations = {rid: r for rid, r in session_requests.items() if r.get("type") == "elicitation"}
        permissions = {rid: r for rid, r in session_requests.items() if r.get("type") != "elicitation"}

        # Session label — driven by info.json status only
        icon = self._get_status_icon(status)
        if status == "question":
            label = f"{icon} {project} \u2014 question"
        elif status == "permission":
            label = f"{icon} {project} \u2014 permission needed"
        elif status in ("done", "idle"):
            label = f"{icon} {project} \u2014 {status}"
        else:
            label = f"{icon} {project}"

        sub_items = []

        # Elicitation questions
        elicitation_mode = _read_config().get("elicitation_mode", "terminal")
        for request_id, request in elicitations.items():
            for q in request.get("questions", []):
                q_text = q.get("question", "Question")
                q_idx = q.get("index", 0)
                options = q.get("options", [])

                if elicitation_mode == "menubar":
                    # Interactive — clickable options
                    option_items = []
                    for opt in options:
                        cb = self._make_elicitation_callback(request_id, q_idx, opt)
                        option_items.append(pystray.MenuItem(opt, cb))
                    sub_items.append(pystray.MenuItem(q_text, pystray.Menu(*option_items)))
                else:
                    # Info-only
                    info_items = [pystray.MenuItem(f"  {opt}", None, enabled=False) for opt in options]
                    info_items.append(pystray.Menu.SEPARATOR)
                    info_items.append(pystray.MenuItem("Answer in terminal", None, enabled=False))
                    sub_items.append(pystray.MenuItem(q_text, pystray.Menu(*info_items)))

        # Permission requests — interactive (Allow/Deny)
        for request_id, request in permissions.items():
            desc = request.get("description", "Permission request")
            yes_cb = self._make_decision_callback(request_id, "allow")
            no_cb = self._make_decision_callback(request_id, "deny")
            req_items = [
                pystray.MenuItem("Allow", yes_cb),
                pystray.MenuItem("Deny", no_cb),
            ]
            sub_items.append(pystray.MenuItem(desc, pystray.Menu(*req_items)))

        if not session_requests:
            if status in ("done", "idle"):
                sub_items.append(pystray.MenuItem("Waiting for your input", None, enabled=False))
            else:
                sub_items.append(pystray.MenuItem("Working...", None, enabled=False))

        cwd = session.get("cwd", "")
        if cwd:
            sub_items.append(pystray.Menu.SEPARATOR)
            sub_items.append(pystray.MenuItem(f"  {cwd}", None, enabled=False))

        return pystray.MenuItem(label, pystray.Menu(*sub_items))

    def _make_decision_callback(self, request_id, decision):
        def callback(icon, item):
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
            try:
                self.icon.notify(f"Failed to write decision: {e}", "Claude Helper")
            except Exception:
                pass

    def _make_elicitation_callback(self, request_id, question_index, selected_label):
        def callback(icon, item):
            self._write_elicitation_answer(request_id, question_index, selected_label)
        return callback

    def _write_elicitation_answer(self, request_id, question_index, selected_label):
        os.makedirs(RESPONSES_DIR, exist_ok=True)
        response_file = os.path.join(RESPONSES_DIR, f"{request_id}.json")
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
            try:
                self.icon.notify(f"Failed to write answer: {e}", "Claude Helper")
            except Exception:
                pass

    def _toggle_elicitation_mode(self, icon, item):
        config = _read_config()
        current = config.get("elicitation_mode", "terminal")
        config["elicitation_mode"] = "terminal" if current == "menubar" else "menubar"
        _write_config(config)

    def _build_status_icons_menu(self):
        """Build the 'Status Icons' settings submenu."""
        config = _read_config()
        current_icons = config.get("status_icons", {})
        state_items = []
        for status in ("working", "question", "permission", "done", "idle"):
            current = current_icons.get(status, DEFAULT_STATUS_ICONS[status])
            label = STATUS_LABELS[status]
            options = STATUS_ICON_OPTIONS[status]
            option_items = []
            for emoji in options:
                is_selected = (emoji == current)
                check_mark = "\u2714 " if is_selected else "   "
                cb = self._make_icon_callback(status, emoji)
                option_items.append(pystray.MenuItem(f"{check_mark}{emoji}", cb))
            state_items.append(
                pystray.MenuItem(f"{current}  {label}", pystray.Menu(*option_items))
            )
        return pystray.MenuItem("Status Icons", pystray.Menu(*state_items))

    def _make_icon_callback(self, status, emoji):
        def callback(icon, item):
            config = _read_config()
            icons = config.get("status_icons", {})
            icons[status] = emoji
            config["status_icons"] = icons
            _write_config(config)
        return callback

    def _toggle_autostart(self, icon, item):
        if self._is_autostart_enabled():
            self._disable_autostart()
        else:
            self._enable_autostart()

    def _is_autostart_enabled(self):
        if IS_MACOS:
            return self._is_autostart_enabled_macos()
        elif IS_WINDOWS:
            return self._is_autostart_enabled_windows()
        return False

    def _enable_autostart(self):
        if IS_MACOS:
            self._enable_autostart_macos()
        elif IS_WINDOWS:
            self._enable_autostart_windows()

    def _disable_autostart(self):
        if IS_MACOS:
            self._disable_autostart_macos()
        elif IS_WINDOWS:
            self._disable_autostart_windows()

    # --- macOS auto-start (launchd) ---

    def _is_autostart_enabled_macos(self):
        if not os.path.isfile(PLIST_DEST):
            return False
        try:
            import plistlib
            with open(PLIST_DEST, "rb") as f:
                plist = plistlib.load(f)
            return plist.get("RunAtLoad", False)
        except Exception:
            return False

    def _enable_autostart_macos(self):
        try:
            import plistlib
            import subprocess
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
            try:
                self.icon.notify(f"Failed to enable auto-start: {e}", "Claude Helper")
            except Exception:
                pass

    def _disable_autostart_macos(self):
        try:
            import subprocess
            if os.path.isfile(PLIST_DEST):
                subprocess.run(["launchctl", "unload", PLIST_DEST], check=False)
                os.unlink(PLIST_DEST)
        except Exception as e:
            try:
                self.icon.notify(f"Failed to disable auto-start: {e}", "Claude Helper")
            except Exception:
                pass

    # --- Windows auto-start (registry) ---

    def _is_autostart_enabled_windows(self):
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_REG_KEY, 0, winreg.KEY_READ)
            try:
                winreg.QueryValueEx(key, AUTOSTART_REG_NAME)
                return True
            except FileNotFoundError:
                return False
            finally:
                winreg.CloseKey(key)
        except Exception:
            return False

    def _enable_autostart_windows(self):
        try:
            import winreg
            app_path = os.path.abspath(__file__)
            venv_python = os.path.join(STATE_DIR, "venv", "Scripts", "python.exe")
            python_path = venv_python if os.path.isfile(venv_python) else sys.executable
            command = f'"{python_path}" "{app_path}"'
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_REG_KEY, 0, winreg.KEY_SET_VALUE)
            try:
                winreg.SetValueEx(key, AUTOSTART_REG_NAME, 0, winreg.REG_SZ, command)
            finally:
                winreg.CloseKey(key)
        except Exception as e:
            try:
                self.icon.notify(f"Failed to enable auto-start: {e}", "Claude Helper")
            except Exception:
                pass

    def _disable_autostart_windows(self):
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_REG_KEY, 0, winreg.KEY_SET_VALUE)
            try:
                winreg.DeleteValue(key, AUTOSTART_REG_NAME)
            except FileNotFoundError:
                pass
            finally:
                winreg.CloseKey(key)
        except Exception as e:
            try:
                self.icon.notify(f"Failed to disable auto-start: {e}", "Claude Helper")
            except Exception:
                pass

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

    def _quit(self, icon, item):
        self._running = False
        icon.stop()


if __name__ == "__main__":
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    os.makedirs(RESPONSES_DIR, exist_ok=True)

    app = ClaudeHelperApp()
    app.run()
