"""Platform switches of the local ranker: supported(), venv and requirements paths, the Windows port-file transport.

Runs on every OS: the platform is patched, nothing here needs the models.
"""
import hashlib
import json
import os
import re
import socket
import sys
import threading

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(ROOT, "plugins", "knowledge")
sys.path.insert(0, os.path.join(PLUGIN, "scripts"))
import local_rank as lr  # noqa: E402

REPLY = {"ids": ["entities/product-example-app"], "pins": [], "scores": {"entities/product-example-app": 0.9}}


@pytest.fixture
def plugin_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", PLUGIN)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    monkeypatch.delenv(lr.OPT_OUT_ENV, raising=False)
    return tmp_path


@pytest.mark.parametrize("platform_name, machine, expected", [
    ("darwin", "arm64", True),
    ("darwin", "x86_64", False),
    ("win32", "AMD64", True),
    ("win32", "x86_64", True),
    ("win32", "ARM64", False),
    ("linux", "x86_64", False),
    ("linux", "aarch64", False),
])
def test_supported_matrix(monkeypatch, platform_name, machine, expected):
    monkeypatch.setattr(sys, "platform", platform_name)
    monkeypatch.setattr(lr.platform, "machine", lambda: machine)
    assert lr.supported() is expected
    monkeypatch.delenv(lr.OPT_OUT_ENV, raising=False)
    assert lr.enabled() is False  # opt-in: off by default on every platform
    monkeypatch.setenv(lr.OPT_OUT_ENV, "1")
    assert lr.enabled() is expected
    monkeypatch.setenv(lr.OPT_OUT_ENV, "off")
    assert lr.enabled() is False


def test_install_state_unsupported_on_linux(plugin_env, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert lr.install_state(PLUGIN) == "unsupported"


def test_platform_paths(plugin_env, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert lr.venv_python() == os.path.join(str(plugin_env), "venv", "Scripts", "python.exe")
    assert lr.requirements_path() == os.path.join(PLUGIN, "runtime", "requirements-windows.txt")
    monkeypatch.setattr(sys, "platform", "darwin")
    assert lr.venv_python() == os.path.join(str(plugin_env), "venv", "bin", "python")
    assert lr.requirements_path() == os.path.join(PLUGIN, "runtime", "requirements.txt")


def test_macos_stamp_is_unchanged(plugin_env, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    h = hashlib.sha256()
    for name in ("requirements.txt", "manifest.json"):
        with open(os.path.join(PLUGIN, "runtime", name), "rb") as fh:
            h.update(fh.read())
    assert lr.stamp() == h.hexdigest()[:16]
    monkeypatch.setattr(sys, "platform", "win32")
    assert lr.stamp() != h.hexdigest()[:16]


def serve_port_file(reply_bytes):
    """Tiny TCP daemon on loopback: writes its port to the socket file, answers one request. Return the thread and the requests."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    os.makedirs(os.path.dirname(lr.sock_path()), exist_ok=True)
    with open(lr.sock_path(), "w") as fh:
        fh.write(str(srv.getsockname()[1]))
    seen = []

    def loop():
        conn, _ = srv.accept()
        with conn, srv:
            raw = b""
            while not raw.endswith(b"\n"):
                raw += conn.recv(65536)
            seen.append(raw)
            conn.sendall(reply_bytes)

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    return thread, seen


def test_windows_port_file_roundtrip(plugin_env, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    thread, seen = serve_port_file(json.dumps(REPLY).encode())
    assert lr.ask_daemon(json.dumps({"prompt": "Example App", "seen": []})) == REPLY
    thread.join(5)
    assert json.loads(seen[0])["prompt"] == "Example App"


def test_windows_missing_or_bad_port_file_is_a_dead_daemon(plugin_env, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(OSError):
        lr.ask_daemon("{}")
    os.makedirs(os.path.dirname(lr.sock_path()))
    with open(lr.sock_path(), "w") as fh:
        fh.write("not a port")
    with pytest.raises(OSError):
        lr.ask_daemon("{}")


def parse_requirements(path):
    """Return {package: (version, [sha256])} for a pip requirements file with --hash lines."""
    with open(path) as fh:
        text = fh.read().replace("\\\n", " ")
    found = {}
    for line in text.splitlines():
        if line.strip() and not line.startswith("#"):
            name, version = line.split()[0].split("==")
            found[name] = (version, re.findall(r"--hash=sha256:([0-9a-f]{64})", line))
    return found


def test_windows_requirements_pin_every_package_with_hashes():
    found = parse_requirements(os.path.join(PLUGIN, "runtime", "requirements-windows.txt"))
    assert set(found) == {"onnxruntime", "numpy", "tokenizers"}
    for name, (version, hashes) in found.items():
        assert re.fullmatch(r"\d+\.\d+\.\d+", version), name
        assert hashes and len(set(hashes)) == len(hashes), name
    assert len(found["onnxruntime"][1]) == 4 and len(found["numpy"][1]) == 4  # cp311 to cp314 win_amd64
    assert len(found["tokenizers"][1]) == 1  # abi3
