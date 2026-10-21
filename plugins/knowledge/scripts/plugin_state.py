"""State shared by the plugin scripts: env switches, paths, JSON helpers, the session file and the error counter."""
import contextlib
import json
import os
import time
from datetime import datetime, timezone

import config
import switches

OPT_OUT_ENV = "KNOWLEDGE_TELEMETRY"
DROP_SECONDS = 7 * 24 * 3600
CAPTURE_ENV = "KNOWLEDGE_CAPTURE"
WORKER_ENV = "KNOWLEDGE_WORKER"
ERRORS_LOG_MAX = 200000


def root():
    return os.environ.get("CLAUDE_PLUGIN_ROOT", "")


def vault():
    return os.path.join(root(), "vault")


def data_dir():
    path = os.environ.get("CLAUDE_PLUGIN_DATA", "")
    os.makedirs(os.path.join(path, "sessions"), exist_ok=True)
    return path


def repo():
    """The issues repo from knowledge.json. Empty means issue filing is off."""
    return config.issues_repo()


def opted_out():
    # No issues repo means no telemetry target.
    return switches.env_off(OPT_OUT_ENV) or not repo()


def capture_off():
    return switches.env_off(CAPTURE_ENV) or not repo()


def queue_dir():
    path = os.path.join(data_dir(), "queue")
    os.makedirs(path, exist_ok=True)
    return path


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def session_path(sid):
    return os.path.join(data_dir(), "sessions", "%s.json" % sid)


def read_json(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_json(path, obj):
    # Unique name: two hook processes may write the same file at once.
    tmp = path + ".tmp." + str(os.getpid())
    with open(tmp, "w") as fh:
        json.dump(obj, fh)
    for attempt in range(100):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            # Windows refuses to replace a file that another process holds open for a moment.
            if os.name != "nt" or attempt == 99:
                raise
            time.sleep(0.01)


def flock(fh, wait=True):
    """Take an exclusive lock on an open file until it closes. Raises OSError when wait is False and the lock is taken."""
    if os.name == "nt":
        import msvcrt
        fh.seek(0)
        while True:
            try:
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                if not wait:
                    raise
                time.sleep(0.01)
    import fcntl
    fcntl.flock(fh, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))


def detached():
    """Popen kwargs that detach a child from the hook process on this OS."""
    if os.name == "nt":
        return {"creationflags": 0x00000008 | 0x00000200 | 0x08000000}  # DETACHED_PROCESS | NEW_PROCESS_GROUP | NO_WINDOW
    return {"start_new_session": True}


@contextlib.contextmanager
def locked(sid):
    """Serialize read-modify-write of one session file across hook processes."""
    with open(session_path(sid) + ".lock", "a") as lock:
        flock(lock)
        yield


def remove_session(path):
    """Best-effort delete of a session file (or its .sending copy) and its lock."""
    base = path[:-len(".sending")] if path.endswith(".sending") else path
    for name in (path, base + ".lock"):
        with contextlib.suppress(OSError):
            os.remove(name)


def new_session(sid):
    return {
        "session_id": sid,
        "started_at": now_iso(),
        "plugin_version": os.path.basename(root().rstrip("/")),
        "hits": {},
        "misses": [],
        "proposals": 0,
        "captures": 0,
        "worker_captures": 0,
        "comments": 0,
        "stale_reports": [],
        "selected": {},
        "select_none": 0,
        "select_fail": 0,
        "select_levels": {},
        "ranker": {},
        "reread_after_compact": 0,
        "reinjected_after_compact": 0,
    }


def load_session(sid):
    return read_json(session_path(sid), None) or new_session(sid)


def active(sess):
    keys = ("hits", "misses", "proposals", "captures", "worker_captures", "comments", "selected", "injected")
    return any(sess.get(k) for k in keys)


def in_vault(path):
    base = os.path.realpath(vault())
    real = os.path.realpath(path)
    return real == base or real.startswith(base + os.sep)


def vault_rel(path):
    """Vault-relative path of a file inside the vault, or None."""
    if not in_vault(path):
        return None
    return os.path.relpath(os.path.realpath(path), os.path.realpath(vault())).replace(os.sep, "/")


def take_errors():
    """Move errors.json aside and return its counts. A count then goes into exactly one payload."""
    path = os.path.join(data_dir(), "errors.json")
    taken = "%s.%d" % (path, os.getpid())
    try:
        os.replace(path, taken)
    except OSError:
        return {}
    counts = read_json(taken, {})
    with contextlib.suppress(OSError):
        os.remove(taken)
    return counts if isinstance(counts, dict) else {}


def restore_errors(counts):
    """Add counts back to errors.json after a failed send. Never raises."""
    try:
        path = os.path.join(data_dir(), "errors.json")
        now = read_json(path, {})
        now = now if isinstance(now, dict) else {}
        for key, n in counts.items():
            now[key] = now.get(key, 0) + n
        write_json(path, now)
    except Exception:
        pass


def note_error(where, exc):
    """Record a swallowed error: one line in errors.log and a count in errors.json. Never raises.

    Counts are approximate under races: two hooks may read and write errors.json at once.
    """
    try:
        kind = type(exc).__name__
        log = os.path.join(data_dir(), "errors.log")
        try:
            if os.path.getsize(log) > ERRORS_LOG_MAX:
                os.replace(log, log + ".1")
        except OSError:
            pass
        with open(log, "a") as fh:
            fh.write("%s %s %s: %s\n" % (now_iso(), where, kind, str(exc)[:200].replace("\n", " ")))
        path = os.path.join(data_dir(), "errors.json")
        counts = read_json(path, {})
        if not isinstance(counts, dict):
            counts = {}
        key = "%s:%s" % (where, kind)
        counts[key] = counts.get(key, 0) + 1
        write_json(path, counts)
    except Exception:
        pass
