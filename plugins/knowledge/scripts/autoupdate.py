"""Background self-update and self-heal. Stdlib only, Python 3.9.

The hook spawns this file detached, at most once an hour, and never waits for it. The child checks the
installed files against integrity.json. If one is missing or changed, it reinstalls the plugin (keeping
the data dir). Otherwise it updates the plugin. Both paths update the marketplace first and stop if that fails. The new version loads on the next launch.
"""
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

import config
import switches

INTERVAL = 3600
TIMEOUT = 300
RETRY_SLEEPS = (20, 60, 180)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_MARK = os.sep + os.path.join("plugins", "cache") + os.sep


def broken():
    """True when a file listed in integrity.json is missing or differs. No list means not broken."""
    try:
        with open(os.path.join(ROOT, "integrity.json")) as f:
            expected = json.load(f)
    except (OSError, ValueError):
        return False
    for name, digest in expected.items():
        try:
            with open(os.path.join(ROOT, name), "rb") as f:
                if hashlib.sha256(f.read()).hexdigest() != digest:
                    return True
        except OSError:
            return True
    return False


def call(claude, cmd):
    """Run one claude command. A non-zero exit, a timeout or a missing binary counts as failure."""
    try:
        return subprocess.call([claude] + cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=TIMEOUT) == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def act(claude):
    # Offline or GitHub down: do nothing. Never uninstall without proof the source is reachable.
    plugin = config.plugin_id()
    if not call(claude, ["plugin", "marketplace", "update", plugin.split("@", 1)[1]]):
        return
    if not broken():
        call(claude, ["plugin", "update", plugin])
    elif call(claude, ["plugin", "uninstall", plugin, "--keep-data"]):
        # A failed install leaves the plugin removed, so retry with growing sleeps.
        for delay in (0,) + RETRY_SLEEPS:
            if delay:
                time.sleep(delay)
            if call(claude, ["plugin", "install", plugin]):
                return


def prune_cache():
    """Keep the running version and the newest other one. Delete older version folders of this plugin."""
    root = os.path.normpath(os.environ.get("CLAUDE_PLUGIN_ROOT", ""))
    parent = os.path.dirname(root)
    if CACHE_MARK not in root:
        return
    others = [os.path.join(parent, n) for n in os.listdir(parent) if os.path.join(parent, n) != root]
    others = sorted((p for p in others if os.path.isdir(p)), key=os.path.getmtime, reverse=True)
    for path in others[1:]:
        shutil.rmtree(path, ignore_errors=True)


def fresh(stamp):
    try:
        return time.time() - os.path.getmtime(stamp) < INTERVAL
    except OSError:
        return False


def run(claude):
    """Take the lock for the whole run. A second child exits at once. The stamp throttles to once an hour."""
    data = os.environ.get("CLAUDE_PLUGIN_DATA", "")
    if not data:
        return
    os.makedirs(data, exist_ok=True)
    with open(os.path.join(data, "autoupdate.lock"), "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return
        stamp = os.path.join(data, "autoupdate.stamp")
        if fresh(stamp):
            return
        with open(stamp, "a"):
            os.utime(stamp, None)
        act(claude)
        try:
            prune_cache()
        except OSError:
            pass


def maybe_update():
    try:
        if switches.env_off("KNOWLEDGE_AUTOUPDATE"):
            return
        if os.environ.get("KNOWLEDGE_SELECT_NESTED"):
            return
        root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
        data = os.environ.get("CLAUDE_PLUGIN_DATA", "")
        claude = shutil.which("claude")
        if not (claude and data and CACHE_MARK in root):
            return
        stamp = os.path.join(data, "autoupdate.stamp")
        if fresh(stamp):
            return
        subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "--run", claude],
            start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


if __name__ == "__main__" and len(sys.argv) == 3 and sys.argv[1] == "--run":
    run(sys.argv[2])
