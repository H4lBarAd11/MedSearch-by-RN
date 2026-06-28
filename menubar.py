#!/usr/bin/env python3
"""
MedSearch menu-bar app (macOS)
==============================

A lightweight status-bar companion to MedSearch. Click the menu-bar icon to get
a dropdown with a quick search: type a query and the full MedSearch window opens
with results already loading, searching whichever source you've set as default.

This is a SEPARATE, standalone process from the main app. It does not run the
search itself — it hands off to the main MedSearch window (a real browser view
can show cards, PDFs, AI summaries; a menu-bar dropdown can only show text rows).
That keeps this app tiny and robust.

How it finds/launches MedSearch:
  • It shares the same config dir (~/.medsearch) as the main app.
  • When you search, it ensures the MedSearch server is running (launching the
    app if needed), then POSTs the query to the server's /queue_search endpoint.
    The native MedSearch window polls /pending_search, picks up the queued search,
    and runs it IN-WINDOW — no browser, whether the app was already open or not.

Requirements:  pip install rumps
Run:           python3 menubar.py   (or bundle with py2app — see setup.py)
"""

import sys
import json
import threading
import subprocess
import urllib.request
import urllib.parse
import webbrowser
from pathlib import Path

try:
    import rumps
except ImportError:
    sys.stderr.write(
        "\nThe menu-bar app needs 'rumps'. Install it with:\n"
        "    pip3 install rumps\n\n"
    )
    sys.exit(1)

# ── Shared config (same files the main app uses) ───────────────────────────
CONFIG_DIR  = Path.home() / ".medsearch"
CONFIG_FILE = CONFIG_DIR / "config.json"
PORT_FILE   = CONFIG_DIR / "server_port"   # main app writes its chosen port here

def _diag(msg):
    """Append a diagnostic line to ~/.medsearch/menubar.log (best-effort).
    Lets us debug launch issues even when running headless from a bundle."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_DIR / "menubar.log", "a") as f:
            f.write(str(msg) + "\n")
    except Exception:
        pass

# ── Locate our own resources and the main MedSearch app ────────────────────
# This file may run either as a plain script (from the project folder) or frozen
# inside a py2app .app bundle. Resolve paths for both cases.
FROZEN = getattr(sys, "frozen", False)
if FROZEN:
    # Inside a py2app bundle: bundled data files live in <bundle>/Contents/Resources
    RESOURCE_DIR = Path(sys.executable).resolve().parent.parent / "Resources"
else:
    RESOURCE_DIR = Path(__file__).resolve().parent
APP_DIR = RESOURCE_DIR

# The menu-bar glyph (bundled as a data file when frozen).
ICON_FILE = APP_DIR / "menubar_icon.png"

# How to launch the main MedSearch app. We prefer, in order:
#   1) a MedSearch.app bundle (self-contained, no Python needed)
#   2) app.py run with a real Python (when running from the project folder)
# We search a few likely locations so the menu-bar app works whether it lives in
# the project folder or in /Applications.
def _find_main_app():
    """Return ('bundle', path) or ('script', path) or (None, None)."""
    candidates_bundle = [
        Path("/Applications/MedSearch.app"),
        APP_DIR / "MedSearch.app",
        APP_DIR.parent / "MedSearch.app",
    ]
    # When frozen in /Applications, the project folder isn't beside us, so the
    # .app bundle is the reliable target. Check those first.
    for b in candidates_bundle:
        if b.exists():
            return ("bundle", b)
    # Otherwise look for app.py next to us or in the project folder.
    candidates_script = [
        APP_DIR / "app.py",
        Path(__file__).resolve().parent / "app.py" if not FROZEN else None,
    ]
    for s in candidates_script:
        if s and s.exists():
            return ("script", s)
    return (None, None)

# Kept for backward references; resolved lazily where used.
MAIN_APP = APP_DIR / "app.py"

# The sources the quick search can target (key → friendly label).
SOURCES = [
    ("pubmed",        "PubMed"),
    ("guidelines",    "Guidelines"),
    ("cochrane",      "Cochrane"),
    ("clinicaltrials","ClinicalTrials.gov"),
    ("arxiv",         "arXiv"),
    ("scopus",        "Scopus"),
    ("wos",           "Web of Science"),
    ("all",           "All sources"),
]
SOURCE_LABELS = dict(SOURCES)

RECENTS_MAX = 6


def load_config():
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass


def get_default_source():
    return (load_config().get("default_source") or "pubmed").strip()


def set_default_source(src):
    cfg = load_config()
    cfg["default_source"] = src
    save_config(cfg)


def _port_is_alive(port):
    """True if something answers HTTP on 127.0.0.1:<port> (our server)."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


def find_running_port():
    """
    Find the port the main MedSearch server is on. The main app writes its port
    to ~/.medsearch/server_port; we verify it's actually answering. Returns the
    port int, or None if the server isn't running.
    """
    try:
        if PORT_FILE.exists():
            port = int(PORT_FILE.read_text().strip())
            if _port_is_alive(port):
                return port
    except Exception:
        pass
    # Fall back to probing the common default
    for port in (5050,):
        if _port_is_alive(port):
            return port
    return None


def _wait_for_server(timeout=25):
    """Poll until the MedSearch server answers (after we launched it).
    Returns the port int once it's up, or None if it never came up in time."""
    import time as _time
    deadline = _time.time() + timeout
    while _time.time() < deadline:
        port = find_running_port()
        if port:
            return port
        _time.sleep(0.5)
    return None


def _queue_search(port, query, source):
    """POST a search to the running app; its native window picks it up and runs
    it in-window. Returns True on success."""
    try:
        body = json.dumps({"query": query, "source": source}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/queue_search",
            data=body, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status == 200
    except Exception as e:
        _diag(f"_queue_search error: {e}")
        return False


def _real_python():
    """
    Find a real (non-frozen) Python to run app.py with. When we're frozen inside
    a py2app bundle, sys.executable is OUR bundled binary, which can't run app.py
    — so we look for a Homebrew/system framework Python instead.
    """
    if not FROZEN:
        return sys.executable
    for cand in ("/opt/homebrew/bin/python3", "/usr/local/bin/python3",
                 "/usr/bin/python3"):
        if Path(cand).exists():
            return cand
    return None


def _launch(extra_args=None):
    """
    Launch the main MedSearch app, detached. Prefers the MedSearch.app bundle
    (self-contained); otherwise runs app.py with a real Python. extra_args (e.g.
    ['--query', 'x', '--source', 'pubmed']) are passed through. Returns True if
    launched. On failure, logs the reason to ~/.medsearch/menubar.log.
    """
    kind, path = _find_main_app()
    extra_args = extra_args or []
    _diag(f"_launch: kind={kind} path={path} args={extra_args}")
    if kind is None:
        _diag("_launch: could not find MedSearch.app or app.py anywhere")
        return False
    try:
        if kind == "bundle":
            # `open` launches the .app as its own process. We capture output so a
            # failure is logged rather than silently swallowed.
            cmd = ["open", str(path)]
            if extra_args:
                cmd += ["--args"] + extra_args
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                _diag(f"_launch bundle: open rc={r.returncode} err={r.stderr.strip()}")
                return False
            return True
        elif kind == "script":
            py = _real_python()
            if not py:
                _diag("_launch script: no real Python found to run app.py")
                return False
            subprocess.Popen(
                [py, str(path)] + extra_args,
                cwd=str(path.parent),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return True
    except Exception as e:
        _diag(f"_launch exception: {e}")
        return False
    return False


def launch_main_app():
    """Launch the main MedSearch app with no query (just open it)."""
    return _launch()


class MedSearchBar(rumps.App):
    def __init__(self):
        # ── Liquid Glass / native appearance ─────────────────────────────────
        # We deliberately do NOT apply any custom colours, fonts, or backgrounds
        # to the status item or its menu. rumps creates a real native
        # NSStatusItem + NSMenu, so on macOS Tahoe (26+) the system renders them
        # in Liquid Glass automatically — identical to the clock, Wi-Fi, and
        # Spotlight menus. Forcing MedSearch's sage palette here would FIGHT the
        # system and look out of place; the brand colours live in the main app
        # window, which is the macOS-correct convention for menu-bar extras.
        #
        # Icon: we ship a monochrome "menubar_icon.png" (a books + network glyph).
        # We start WITH a text title so the status item always has width and is
        # never invisible, then try to attach the icon and — only if that
        # succeeds — drop the text for an icon-only look. If the icon can't load,
        # the text title remains so the item still shows up in the bar.
        # A template image (template=True) must be black-on-transparent; macOS
        # then tints it for the bar (light/dark/Liquid Glass).
        super().__init__("MedSearch", title="MedSearch", quit_button=None)

        icon_path = ICON_FILE
        self._icon_loaded = False
        if icon_path.exists():
            try:
                # Setting .icon AFTER init is more reliable than the constructor
                # kwarg. template=True makes macOS tint it for the bar
                # (light/dark/Liquid Glass).
                self.template = True
                self.icon = str(icon_path)
                self._icon_loaded = True
                self.title = ""            # icon attached → go icon-only
            except Exception as e:
                print(f"  [menubar] icon failed to load, keeping text title: {e}")
                self.title = "MedSearch"
        else:
            print(f"  [menubar] icon not found at {icon_path}; using text title")

        self.recents = []        # list of recent query strings
        self._build_menu()

    # ── Menu construction ──────────────────────────────────────────────────
    def _build_menu(self):
        self.menu.clear()
        self.menu.add(rumps.MenuItem("Search MedSearch…", callback=self.do_search))
        self.menu.add(rumps.separator)

        # Recent searches submenu
        if self.recents:
            recent_item = rumps.MenuItem("Recent searches")
            for q in self.recents:
                recent_item.add(rumps.MenuItem(q, callback=self._make_recent_cb(q)))
            recent_item.add(rumps.separator)
            recent_item.add(rumps.MenuItem("Clear recents", callback=self.clear_recents))
            self.menu.add(recent_item)
            self.menu.add(rumps.separator)

        # Default-source picker submenu (checkmark on the active one)
        current = get_default_source()
        source_item = rumps.MenuItem("Default source")
        for key, label in SOURCES:
            mi = rumps.MenuItem(label, callback=self._make_source_cb(key))
            mi.state = 1 if key == current else 0
            source_item.add(mi)
        self.menu.add(source_item)
        self.menu.add(rumps.separator)

        self.menu.add(rumps.MenuItem("Open MedSearch window", callback=self.open_window))
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("Quit", callback=self.quit_app))

    def _make_recent_cb(self, query):
        def cb(_):
            self.run_query(query)
        return cb

    def _make_source_cb(self, key):
        def cb(_):
            set_default_source(key)
            # Also tell a running server so the main app reflects it immediately
            port = find_running_port()
            if port:
                try:
                    req = urllib.request.Request(
                        f"http://127.0.0.1:{port}/default_source",
                        data=json.dumps({"source": key}).encode(),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    urllib.request.urlopen(req, timeout=2)
                except Exception:
                    pass
            self._build_menu()   # refresh checkmarks
        return cb

    # ── Actions ─────────────────────────────────────────────────────────────
    def do_search(self, _):
        """Prompt for a query, then run it."""
        win = rumps.Window(
            title="MedSearch quick search",
            message=f"Search {SOURCE_LABELS.get(get_default_source(), 'PubMed')} "
                    f"(change the source in this menu).",
            default_text="",
            ok="Search",
            cancel="Cancel",
            dimensions=(320, 24),
        )
        resp = win.run()
        if resp.clicked and resp.text.strip():
            self.run_query(resp.text.strip())

    def run_query(self, query):
        """
        Run a quick search INSIDE the native MedSearch window (never the browser).

        Mechanism: the native window polls the server for queued searches. We POST
        the query to /queue_search; the window picks it up and runs it. If the app
        isn't running yet, we launch it (which opens its window), wait for the
        server, then queue — so the freshly-opened window runs the search.
        """
        src = get_default_source()
        self._remember(query)

        def worker():
            _diag(f"run_query: query={query!r} src={src}")
            port = find_running_port()
            _diag(f"run_query: running port={port}")
            if not port:
                _diag("run_query: launching main app (will run search in its window)")
                _launch()                      # opens the native window
                port = _wait_for_server(timeout=25)
                _diag(f"run_query: after launch, port={port}")
            if not port:
                _diag("run_query: server never came up")
                rumps.notification(
                    "MedSearch", "Couldn't reach MedSearch",
                    "The app didn't start in time — see ~/.medsearch/menubar.log",
                )
                return
            # Queue the search; the native window's poller runs it in-window.
            if not _queue_search(port, query, src):
                _diag("run_query: queue_search failed")
                rumps.notification(
                    "MedSearch", "Search couldn't be sent",
                    "See ~/.medsearch/menubar.log",
                )

        threading.Thread(target=worker, daemon=True).start()

    def open_window(self, _):
        """
        Open MedSearch. If it's not running, launch the native app (its own
        window appears — nothing else to do). If it's already running, open the
        window in the browser (we can't raise the existing native window from
        here). Mirrors run_query's launch-vs-browser logic to avoid opening two
        windows at once.
        """
        def worker():
            port = find_running_port()
            if port:
                # Already running → open in the browser (can't raise native window).
                webbrowser.open(f"http://127.0.0.1:{port}/")
                return
            # Not running → launch the native app; its own window will appear.
            if not launch_main_app():
                rumps.notification("MedSearch", "Couldn't start MedSearch",
                                   "Try opening the MedSearch app manually.")
        threading.Thread(target=worker, daemon=True).start()

    def clear_recents(self, _):
        self.recents = []
        self._build_menu()

    def quit_app(self, _):
        rumps.quit_application()

    # ── Helpers ─────────────────────────────────────────────────────────────
    def _remember(self, query):
        if query in self.recents:
            self.recents.remove(query)
        self.recents.insert(0, query)
        self.recents = self.recents[:RECENTS_MAX]
        self._build_menu()


if __name__ == "__main__":
    # The menu bar and its menu stay fully native (Liquid Glass on macOS Tahoe).
    # Drop a monochrome "menubar_icon.png" beside this file for a custom glyph;
    # otherwise a short "MedSearch" text title is shown.
    #
    # Startup diagnostics: if the status-bar item fails to appear, the cause is
    # almost always (a) running under a Python that can't access the GUI session,
    # or (b) an exception during setup. We print clear diagnostics and surface a
    # dialog so failures are visible instead of a silent background process.
    import platform
    # Log to a file too, so we can diagnose when launched from the .app bundle
    # (where stdout is hidden). If the icon doesn't appear, run in Terminal:
    #   tail -n 40 ~/.medsearch/menubar.log
    _log_lines = []
    def _log(msg):
        print(msg)
        _log_lines.append(str(msg))
    _log("─" * 60)
    _log("  MedSearch Menu Bar — starting")
    _log(f"  Python : {sys.executable}")
    _log(f"  Version: {sys.version.split()[0]}")
    _log(f"  macOS  : {platform.mac_ver()[0] or 'unknown'}")
    _log(f"  rumps  : {getattr(rumps, '__version__', 'unknown')}")
    _icon = ICON_FILE
    _log(f"  icon   : {_icon}  ({'found' if _icon.exists() else 'MISSING'})")
    _log(f"  appdir : {APP_DIR}")
    _log("  → Look for the MedSearch icon (books + network) in the menu bar (top-right).")
    _log("    If you see the word 'MedSearch' instead of the icon, the image")
    _log("    couldn't load — tell me. Ctrl-C to quit.")
    _log("─" * 60)
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        (CONFIG_DIR / "menubar.log").write_text("\n".join(_log_lines) + "\n")
    except Exception:
        pass
    try:
        MedSearchBar().run()
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(tb)
        try:
            (CONFIG_DIR / "menubar.log").write_text(
                "\n".join(_log_lines) + "\n\nCRASH:\n" + tb + "\n")
        except Exception:
            pass
        try:
            # Surface the error visibly even if launched without a terminal
            subprocess.run([
                "osascript", "-e",
                f'display alert "MedSearch Menu Bar failed to start" message "{str(e)[:300]}"'
            ], check=False)
        except Exception:
            pass
        sys.exit(1)
