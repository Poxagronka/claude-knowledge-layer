"""Background self-update and self-heal. Stdlib only, Python 3.9.

The hook spawns this file detached, at most once an hour, and never waits for it. The child checks the
installed files against integrity.json. If one is missing or changed, it reinstalls the plugin in place
(the data dir stays). Otherwise it updates the plugin. Both paths update the marketplace first and stop if that fails. The new version loads on the next launch.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

import config
import plugin_state
import switches

INTERVAL = 3600
FAIL_BACKOFF = 600  # after a failed run, wait this long before the next try
TIMEOUT = 300
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_MARK = os.sep + os.path.join("plugins", "cache") + os.sep


def broken(root=None):
    """True when a file listed in integrity.json is missing or differs. No list means not broken."""
    root = root or ROOT
    # A missing or unreadable list counts as intact: a reinstall loop is worse than a skipped heal.
    try:
        with open(os.path.join(root, "integrity.json")) as f:
            expected = json.load(f)
    except (OSError, ValueError):
        return False
    for name, digest in expected.items():
        try:
            with open(os.path.join(root, name), "rb") as f:
                # CRLF to LF: a Windows checkout must hash like the LF repo files.
                if hashlib.sha256(f.read().replace(b"\r\n", b"\n")).hexdigest() != digest:
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
    """Update or heal. Return False when a step failed, so the caller retries sooner than the hourly stamp."""
    # Offline or GitHub down: do nothing. Never touch the install without proof the source is reachable.
    if not call(claude, ["plugin", "marketplace", "update", config.plugin_id().split("@", 1)[1]]):
        log_failure("marketplace update failed or timed out")
        return False
    if broken():
        heal(claude)  # logs its own failure and keeps the hourly retry
    elif not call(claude, ["plugin", "update", config.plugin_id()]):
        log_failure("plugin update failed or timed out")
        return False
    return True


def installed_ok(aside):
    """True when a version dir other than `aside` has integrity.json and matches it. Install may pick a newer version."""
    parent = os.path.dirname(ROOT)
    try:
        names = [os.path.join(parent, n) for n in os.listdir(parent)]
    except OSError:
        return False
    return any(p != aside and os.path.isfile(os.path.join(p, "integrity.json")) and not broken(p) for p in names)


def heal(claude):
    """Reinstall in place: move the version dir aside, then install. Never uninstall.

    Verified with claude 2.1.306: install and update skip a cached dir that exists, but install restores
    a dir that is gone. A failed or unverified install puts the old dir back, so the plugin and this
    updater stay installed. The stamp stays, so the next hourly run retries.
    """
    aside = ROOT + ".heal-bak"
    plugin = config.plugin_id()
    try:
        shutil.rmtree(aside, ignore_errors=True)
        os.rename(ROOT, aside)
    except OSError:
        log_failure("heal skipped: cannot move " + ROOT + " aside")
        return
    if call(claude, ["plugin", "install", plugin]) and installed_ok(aside):
        shutil.rmtree(aside, ignore_errors=True)
        return
    shutil.rmtree(ROOT, ignore_errors=True)
    try:
        os.rename(aside, ROOT)
    except OSError:
        log_failure("heal failed and the old dir is at " + aside + "; run: claude plugin install " + plugin)
        return
    log_failure("heal failed, old files kept; retry next hour or run: claude plugin install " + plugin)


def log_failure(message):
    """Append one line to autoupdate.log in the data dir. Never raises."""
    try:
        with open(os.path.join(os.environ["CLAUDE_PLUGIN_DATA"], "autoupdate.log"), "a") as f:
            f.write(time.strftime("%Y-%m-%dT%H:%M:%S ") + message + "\n")
    except (OSError, KeyError):
        pass


def live_versions():
    """Version folder names that an unsent session file records. The hook sweep drops these files after 7 days."""
    sdir = os.path.join(os.environ.get("CLAUDE_PLUGIN_DATA", ""), "sessions")
    try:
        names = [n for n in os.listdir(sdir) if n.endswith(".json")]
    except OSError:
        return set()
    sessions = [plugin_state.read_json(os.path.join(sdir, n), {}) for n in names]
    return {s.get("plugin_version") for s in sessions if isinstance(s, dict)}


def prune_cache():
    """Keep the running version, the newest other one and every version an open session uses. Delete the rest."""
    root = os.path.normpath(os.environ.get("CLAUDE_PLUGIN_ROOT", ""))
    parent = os.path.dirname(root)
    if CACHE_MARK not in root:
        return
    others = [os.path.join(parent, n) for n in os.listdir(parent) if os.path.join(parent, n) != root]
    others = sorted((p for p in others if os.path.isdir(p)), key=os.path.getmtime, reverse=True)
    live = live_versions()
    for path in others[1:]:
        if os.path.basename(path) not in live:
            shutil.rmtree(path, ignore_errors=True)


def back_off(stamp):
    """Age the stamp so the next try comes after FAIL_BACKOFF seconds, not after INTERVAL."""
    past = time.time() - (INTERVAL - FAIL_BACKOFF)
    os.utime(stamp, (past, past))


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
            plugin_state.flock(lock, wait=False)
        except OSError:
            return
        stamp = os.path.join(data, "autoupdate.stamp")
        if fresh(stamp):
            return
        with open(stamp, "a"):
            os.utime(stamp, None)
        if not act(claude):
            back_off(stamp)
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
        root = os.path.normpath(os.environ.get("CLAUDE_PLUGIN_ROOT", ""))
        data = os.environ.get("CLAUDE_PLUGIN_DATA", "")
        claude = shutil.which("claude")
        if not (data and CACHE_MARK in root):
            return
        stamp = os.path.join(data, "autoupdate.stamp")
        if fresh(stamp):
            return
        if not claude:
            os.makedirs(data, exist_ok=True)
            log_failure("claude is not on PATH: no update")
            with open(stamp, "a"):
                pass
            return
        subprocess.Popen(
            [sys.executable, "-X", "utf8", os.path.abspath(__file__), "--run", claude],
            **plugin_state.detached(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


if __name__ == "__main__" and len(sys.argv) == 3 and sys.argv[1] == "--run":
    run(sys.argv[2])
