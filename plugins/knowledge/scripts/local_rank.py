"""Local ranker runtime: state checks, background install spawn, ranker call.

Opt-in: nothing installs or runs unless KNOWLEDGE_LOCAL is set on (`1`, `true`, `on`, `yes`).
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

import plugin_state
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
    if sys.platform == "win32":
        return data_path("venv", "Scripts", "python.exe")
    return data_path("venv", "bin", "python")


def supported():
    """Local ranking has pinned wheels for macOS arm64 and Windows x64 only."""
    return (sys.platform == "darwin" and platform.machine() == "arm64") or (
        sys.platform == "win32" and platform.machine().lower() in ("amd64", "x86_64")
    )


def enabled():
    """Opt-in: local ranking runs on a supported platform when KNOWLEDGE_LOCAL is set on."""
    return supported() and switches.env_on(OPT_OUT_ENV)


def requirements_path():
    """Pinned packages of this platform."""
    name = "requirements-windows.txt" if sys.platform == "win32" else "requirements.txt"
    return os.path.join(runtime_dir(), name)


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
    return _digest(_read(path) for path in (requirements_path(), os.path.join(runtime_dir(), "manifest.json")))


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
        if not supported():
            return "unsupported"
        if not switches.env_on(OPT_OUT_ENV):
            return "off"
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
            [sys.executable, "-X", "utf8", os.path.join(runtime_dir(), "install.py")],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **plugin_state.detached(),
        )
    except Exception:
        pass


def sock_path():
    """Daemon socket path. Only the runtime stamp is in the name, so every cached plugin version shares one daemon.

    On Windows (no AF_UNIX) the file holds the daemon's TCP port on 127.0.0.1 instead."""
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
    if sys.platform == "win32":
        try:
            with open(sock_path()) as fh:
                address, family = ("127.0.0.1", int(fh.read().strip())), socket.AF_INET
        except ValueError as exc:
            raise OSError("bad port file") from exc
    else:
        address, family = sock_path(), socket.AF_UNIX
    with socket.socket(family, socket.SOCK_STREAM) as conn:
        conn.settimeout(CONNECT_TIMEOUT)
        conn.connect(address)
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
        raise OSError("daemon error")  # the daemon owns socket cleanup; rank() requests a restart
    return None


def spawn_daemon():
    subprocess.Popen(
        [venv_python(), "-X", "utf8", "-I", os.path.join(runtime_dir(), "rank.py"), "--serve"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **plugin_state.detached(),
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

    Only the flocked daemon loads the models ("warm" is True). When it does not answer, start one for the next prompt and return None for this prompt. The prompt process never loads the models: one process per prompt multiplied by sessions exhausted RAM.
    """
    request = json.dumps({"prompt": prompt, "seen": sorted(seen), "deadline": time.time() + RANK_TIMEOUT})
    try:
        reply = ask_daemon(request)
        return dict(reply, warm=True) if reply else None
    except socket.timeout:
        return None
    except OSError:
        try:
            spawn_daemon()
        except Exception:
            pass
        return None
    except Exception:
        return None
