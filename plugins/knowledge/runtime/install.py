#!/usr/bin/env python3
"""Detached installer for the local ranker runtime. Stdlib only, Python 3.9.

Steps, all idempotent: venv with hash-locked packages, ONNX models with sha256 checks,
vault vectors, then the stamp. Only one installer runs (flock). Logs to $CLAUDE_PLUGIN_DATA/install.log.
A failure writes install.failed, so SessionStart does not retry for a few hours.
"""
import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
import local_rank as lr  # noqa: E402
import plugin_state  # noqa: E402

MAX_LOG = 200000
STEP_TIMEOUT = 900


def log(msg):
    with open(lr.data_path("install.log"), "a") as fh:
        fh.write("%s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), msg))


def run(cmd, timeout=STEP_TIMEOUT):
    log("run: " + " ".join(cmd))
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if done.returncode:
        log((done.stdout + done.stderr)[-2000:])
        raise RuntimeError("command failed: %s" % cmd[0])


def make_venv():
    if sys.platform == "win32":
        base = sys.executable
    else:
        base = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else shutil.which("python3")
    venv = lr.data_path("venv")
    for _ in range(45):  # Windows cannot delete a venv an old daemon still runs from: it exits within a minute once the stamp is gone
        shutil.rmtree(venv, ignore_errors=True)
        if not os.path.exists(venv):
            break
        time.sleep(2)
    run([base, "-m", "venv", venv])
    run([lr.venv_python(), "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
         "--require-hashes", "--no-deps", "--only-binary=:all:",
         "-r", lr.requirements_path()])
    run([lr.venv_python(), "-I", "-c", "import onnxruntime, numpy, tokenizers"])


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def fetch_models():
    with open(os.path.join(lr.runtime_dir(), "manifest.json")) as fh:
        files = json.load(fh)["files"]
    for item in files:
        dest = lr.data_path("models", item["dest"])
        if os.path.exists(dest) and os.path.getsize(dest) == item["size"] and sha256(dest) == item["sha256"]:
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp = dest + ".part"
        run(["curl", "-fsSL", "--retry", "3", "-o", tmp, item["url"]])
        if sha256(tmp) != item["sha256"]:
            os.remove(tmp)
            raise RuntimeError("sha256 mismatch for %s" % item["dest"])
        os.replace(tmp, dest)


def prune_vectors(keep):
    """Remove old vectors files, keeping `keep` and the 2 newest others."""
    d = lr.data_dir()
    def mtime(n):
        try:
            return os.path.getmtime(os.path.join(d, n))
        except OSError:
            return None

    names = [n for n in os.listdir(d) if n.startswith("vectors-") and n.endswith(".npz") and ".tmp" not in n and n != keep]
    dated = [(mtime(n), n) for n in names]
    others = [n for m, n in sorted((x for x in dated if x[0] is not None), reverse=True)]
    for name in others[2:]:
        with contextlib.suppress(OSError):
            os.remove(os.path.join(d, name))


def build_vectors():
    run([lr.venv_python(), "-I", os.path.join(HERE, "rank.py"), "--warm"])
    keep = os.path.basename(lr.vectors_path(os.path.join(lr.plugin_root(), "vault")))
    if not os.path.exists(lr.data_path(keep)):
        raise RuntimeError("vault vectors missing")
    prune_vectors(keep)


def install():
    vault = os.path.join(lr.plugin_root(), "vault")
    if not lr.installed():
        with contextlib.suppress(OSError):
            os.remove(lr.data_path("runtime.stamp"))
        make_venv()
        fetch_models()
    build_vectors()
    tmp = lr.data_path("runtime.stamp.tmp")
    with open(tmp, "w") as fh:
        fh.write(lr.stamp())
    os.replace(tmp, lr.data_path("runtime.stamp"))
    assert lr.ready(vault)


def main():
    if not (lr.enabled() and lr.data_dir() and lr.plugin_root()):
        return 0
    os.makedirs(lr.data_dir(), exist_ok=True)
    lock = open(lr.data_path("install.lock"), "a")
    try:
        plugin_state.flock(lock, wait=False)
    except OSError:
        return 0
    try:
        if os.path.exists(lr.data_path("install.log")) and os.path.getsize(lr.data_path("install.log")) > MAX_LOG:
            os.remove(lr.data_path("install.log"))
        start = time.time()
        install()
        log("ready in %.0fs" % (time.time() - start))
        with contextlib.suppress(OSError):
            os.remove(lr.data_path("install.failed"))
    except Exception as exc:
        log("FAILED: %r" % (exc,))
        with open(lr.data_path("install.failed"), "w") as fh:
            fh.write(lr.stamp())
    return 0


if __name__ == "__main__":
    sys.exit(main())
