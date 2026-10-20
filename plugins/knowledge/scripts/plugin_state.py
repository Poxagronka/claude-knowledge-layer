"""State shared by the plugin scripts: env switches, paths, JSON helpers, the session file and the error counter."""
import contextlib
import fcntl
import json
import os
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
    os.replace(tmp, path)


@contextlib.contextmanager
def locked(sid):
    """Serialize read-modify-write of one session file across hook processes."""
    with open(session_path(sid) + ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
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
    keys = ("hits", "misses", "proposals", "captures", "worker_captures", "comments", "selected")
    return any(sess.get(k) for k in keys)


def in_vault(path):
    base = os.path.realpath(vault())
    real = os.path.realpath(path)
    return real == base or real.startswith(base + os.sep)


def vault_rel(path):
    """Vault-relative path of a file inside the vault, or None."""
    return os.path.relpath(os.path.realpath(path), os.path.realpath(vault())) if in_vault(path) else None


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
