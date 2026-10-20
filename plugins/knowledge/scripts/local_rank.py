"""Local ranker runtime: state checks, background install spawn, ranker call.

Stdlib only, Python 3.9. The runtime (venv, ONNX models, vault vectors) lives in
$CLAUDE_PLUGIN_DATA. Every function fails soft: no runtime means the caller injects named hubs only.
"""
import hashlib
import json
import os
import platform
import socket
import subprocess
import sys
import time

import switches

OPT_OUT_ENV = "KNOWLEDGE_LOCAL"
RANK_TIMEOUT = 5
CONNECT_TIMEOUT = 0.3
RETRY_SECONDS = 6 * 3600
HERE = os.path.dirname(os.path.abspath(__file__))


def plugin_root():
    return os.environ.get("CLAUDE_PLUGIN_ROOT", "")


def runtime_dir():
    return os.path.join(plugin_root(), "runtime")


def data_dir():
    return os.environ.get("CLAUDE_PLUGIN_DATA", "")


def data_path(*parts):
    return os.path.join(data_dir(), *parts)


def venv_python():
    return data_path("venv", "bin", "python")


def enabled():
    """Local ranking runs on macOS arm64 only, and the owner can switch it off."""
    return (
        sys.platform == "darwin"
        and platform.machine() == "arm64"
        and not switches.env_off(OPT_OUT_ENV)
    )


def _digest(chunks):
    h = hashlib.sha256()
    for chunk in chunks:
        h.update(chunk)
    return h.hexdigest()[:16]


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


def stamp():
    """Version of the runtime: changes when the pinned packages or the model manifest change."""
    return _digest(_read(os.path.join(runtime_dir(), name)) for name in ("requirements.txt", "manifest.json"))


def vault_key(vault_dir):
    """Hash of the vault contents and the model manifest. Vault vectors are cached under it."""
    chunks = [_read(os.path.join(runtime_dir(), "manifest.json"))]
    for base, dirs, files in os.walk(vault_dir):
        dirs.sort()
        for name in sorted(files):
            if name.endswith(".md"):
                path = os.path.join(base, name)
                chunks += [os.path.relpath(path, vault_dir).encode("utf-8"), _read(path)]
    return _digest(chunks)


def vectors_path(vault_dir):
    return data_path("vectors-%s.npz" % vault_key(vault_dir))


def installed():
    try:
        return _read(data_path("runtime.stamp")).decode().strip() == stamp() and os.path.exists(venv_python())
    except OSError:
        return False


def ready(vault_dir):
    try:
        return enabled() and installed() and os.path.exists(vectors_path(vault_dir))
    except OSError:
        return False


def install_state(vault_dir):
    """Ranker install state for telemetry: off, unsupported, ready, failed or installing."""
    try:
        if switches.env_off(OPT_OUT_ENV):
            return "off"
        if not (sys.platform == "darwin" and platform.machine() == "arm64"):
            return "unsupported"
        if ready(vault_dir):
            return "ready"
        try:
            if _read(data_path("install.failed")).decode() == stamp():
                return "failed"
        except OSError:
            pass
        return "installing"
    except Exception:
        return "installing"


def ensure(vault_dir):
    """Spawn the detached installer when the runtime or the vault vectors are missing. Never blocks."""
    try:
        if not enabled() or ready(vault_dir):
            return
        failed = data_path("install.failed")
        try:
            if _read(failed).decode() == stamp() and time.time() - os.path.getmtime(failed) < RETRY_SECONDS:
                return
        except OSError:
            pass
        subprocess.Popen(
            [sys.executable, os.path.join(runtime_dir(), "install.py")],
            start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def sock_path():
    """Daemon socket. Only the runtime stamp is in the name, so every cached plugin version shares one daemon."""
    return data_path("run", "rank-%s.sock" % _digest([stamp().encode()])[:8])


def _valid(reply):
    """True for a reply of shape {"ids": [str], "pins": [str], "scores": {str: number}}."""
    return (
        isinstance(reply, dict)
        and all(isinstance(reply.get(k), list) and all(isinstance(i, str) for i in reply[k]) for k in ("ids", "pins"))
        and isinstance(reply.get("scores"), dict)
        and all(isinstance(k, str) and isinstance(v, (int, float)) and not isinstance(v, bool) for k, v in reply["scores"].items())
    )


def ask_daemon(request):
    """Return the daemon's checked reply dict, None on a bad reply, or raise OSError when no daemon answers."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(CONNECT_TIMEOUT)
        conn.connect(sock_path())
        conn.settimeout(RANK_TIMEOUT)
        conn.sendall(request.encode("utf-8") + b"\n")
        raw = b""
        while True:
            chunk = conn.recv(65536)
            if not chunk:
                break
            raw += chunk
    try:
        reply = json.loads(raw)
    except ValueError:
        reply = None
    if _valid(reply):
        return reply
    if b'"error"' in raw and b'"ids"' not in raw:
        raise OSError("daemon error")  # dead daemon: rank() drops the socket and respawns
    return None


def spawn_daemon():
    subprocess.Popen(
        [venv_python(), "-I", os.path.join(runtime_dir(), "rank.py"), "--serve"],
        start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def prewarm(vault_dir):
    """Start the daemon at session start when the ranker is ready and no socket exists. Never raises."""
    try:
        if ready(vault_dir) and not os.path.exists(sock_path()):
            spawn_daemon()
    except Exception:
        pass


def rank(prompt, seen):
    """Return the ranker's reply {"ids", "pins", "scores", "warm"}, or None on any failure.

    The warm daemon answers when it runs ("warm" is True). Otherwise start one for the next prompt and rank in a one-shot process now.
    """
    request = json.dumps({"prompt": prompt, "seen": sorted(seen)})
    try:
        reply = ask_daemon(request)
        return dict(reply, warm=True) if reply else None
    except socket.timeout:
        return None
    except OSError:
        try:
            os.unlink(sock_path())
        except OSError:
            pass
        try:
            spawn_daemon()
        except Exception:
            pass
    except Exception:
        return None
    try:
        done = subprocess.run(
            [venv_python(), "-I", os.path.join(runtime_dir(), "rank.py")],
            input=request, capture_output=True, text=True, timeout=RANK_TIMEOUT,
        )
        reply = json.loads(done.stdout)
        if done.returncode == 0 and _valid(reply):
            return dict(reply, warm=False)
    except Exception:
        pass
    return None
