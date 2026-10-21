import importlib.util
import io
import json
import os
import socket
import stat
import sys
import tempfile
import threading
import time
import types

import pytest
from conftest import write_config

fcntl = pytest.importorskip("fcntl")  # POSIX-only module: sh stand-ins and Unix sockets

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(ROOT, "plugins", "knowledge")
sys.path.insert(0, os.path.join(PLUGIN, "scripts"))
import local_rank as lr  # noqa: E402
import plugin_state  # noqa: E402


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sel = load("knowledge_select", os.path.join(PLUGIN, "scripts", "knowledge_select.py"))
install = load("install", os.path.join(PLUGIN, "runtime", "install.py"))

SPAWNED = []
SID = "s1"
PROMPT = "What is the Example Service retry limit for a slow order?"
RULE_ID = "api/rules#^R1"
RULE = (
    "# Rules\n\n## R1 - Example Service retry rule\n"
    "[added:: 2026-10-17] [verify_with:: none] [verify_query:: none] ^R1\nRetry an order when processing exceeds the timeout.\n"
)
GREY = json.dumps({"ids": [RULE_ID], "pins": [], "scores": {RULE_ID: 0.5}})
HIGH = json.dumps({"ids": [RULE_ID], "pins": [], "scores": {RULE_ID: 0.95}})
HUB = "---\nid: service-example-service\ntype: entity\naliases: [ES]\n---\n# Example Service\n\nThe Example Service processes shop orders.\n"


def script(path, body):
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def env(tmp_path, monkeypatch):
    plugin, data, bin_dir = tmp_path / "plugin-abc", tmp_path / "data", tmp_path / "bin"
    (plugin / "vault" / "api").mkdir(parents=True)
    write_config(plugin)
    (plugin / "vault" / "entities").mkdir()
    (plugin / "vault" / "api" / "rules.md").write_text(RULE)
    (plugin / "vault" / "entities" / "service-example-service.md").write_text(HUB)
    (plugin / "runtime").mkdir()
    for name in ("requirements.txt", "manifest.json"):
        (plugin / "runtime" / name).write_text(name)
    (data / "venv" / "bin").mkdir(parents=True)
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", "%s%s%s" % (bin_dir, os.pathsep, os.environ["PATH"]))
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(data))
    for name in (sel.NESTED, sel.OPT_OUT_ENV, sel.TIMEOUT_ENV, lr.OPT_OUT_ENV):
        monkeypatch.delenv(name, raising=False)
    short = tempfile.mkdtemp(dir="/tmp")  # AF_UNIX paths are short
    monkeypatch.setattr(lr, "sock_path", lambda: os.path.join(short, "r.sock"))
    monkeypatch.setattr(lr, "spawn_daemon", lambda: SPAWNED.append(1))
    SPAWNED.clear()
    monkeypatch.setattr(lr, "enabled", lambda: not lr.switches.env_off(lr.OPT_OUT_ENV))
    script(bin_dir / "claude", 'cat > %s/stdin.txt; printf "%%s\\0" "$@" > %s/args.txt; echo \'{"need": 3, "picks": []}\'' % (bin_dir, bin_dir))
    return plugin, data, bin_dir


def make_ready(plugin, data, ranker_body):
    """Fake runtime: stamp, vault vectors and a venv python that stands in for rank.py."""
    (data / "runtime.stamp").write_text(lr.stamp())
    (data / ("vectors-%s.npz" % lr.vault_key(str(plugin / "vault")))).write_text("x")
    script(data / "venv" / "bin" / "python", ranker_body)


def run(capsys, monkeypatch):
    event = {"session_id": SID, "prompt": PROMPT}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    assert sel.main() == 0
    return capsys.readouterr().out


def session(data):
    return json.loads((data / "sessions" / (SID + ".json")).read_text())


def test_local_path_sends_only_ranker_candidates(env, capsys, monkeypatch):
    plugin, data, bin_dir = env
    make_ready(plugin, data, "exit 1")
    fake_daemon(GREY.encode())
    run(capsys, monkeypatch)
    sent = (bin_dir / "stdin.txt").read_text()
    assert "CANDIDATES (hubs and entries)" in sent and RULE_ID in sent
    assert "entities/service-example-service" not in sent
    assert "HUBS, one per line" not in (bin_dir / "args.txt").read_text()
    assert session(data)["ranker"] == {"local": 1, "haiku": 1, "warm": 1}


@pytest.mark.parametrize("reply", [
    b"not-json",
    b'{"ids": ["bogus#^X"]}',
    b'{"ids": [], "pins": [], "scores": {"a": "high"}}',
    None,
])
def test_ranker_failure_asks_haiku_about_hubs_with_the_named_hub_first(env, capsys, monkeypatch, reply):
    plugin, data, bin_dir = env
    monkeypatch.setattr(lr, "RANK_TIMEOUT", 0.3)
    make_ready(plugin, data, "exit 1")
    script(bin_dir / "claude", 'cat > %s/stdin.txt; echo \'{"need": 3, "picks": [{"id": "entities/service-example-service", "s": 3}]}\'' % bin_dir)
    if reply is not None:
        fake_daemon(reply)
    out = run(capsys, monkeypatch)
    sent = (bin_dir / "stdin.txt").read_text()
    assert "### [3] entities/service-example-service" in out and "Claude Haiku rated" in out
    assert "entities/service-example-service" in sent and RULE_ID not in sent
    assert session(data)["ranker"] == {"fallback": 1, "haiku": 1}


def test_ranker_and_haiku_failure_inject_named_hubs_only(env, capsys, monkeypatch):
    plugin, data, bin_dir = env
    make_ready(plugin, data, "exit 1")
    script(bin_dir / "claude", "exit 1")
    out = run(capsys, monkeypatch)
    assert "### [3] entities/service-example-service" in out and RULE_ID not in out
    assert session(data)["ranker"] == {"fallback": 1, "haiku": 1} and session(data)["select_fail"] == 1


def test_missing_stamp_falls_back(env, capsys, monkeypatch):
    plugin, data, bin_dir = env
    make_ready(plugin, data, "echo '%s'" % HIGH)
    (data / "runtime.stamp").unlink()
    run(capsys, monkeypatch)
    assert session(data)["ranker"] == {"fallback": 1, "haiku": 1}


def test_changed_vault_needs_new_vectors(env, capsys, monkeypatch):
    plugin, data, _ = env
    make_ready(plugin, data, "echo '%s'" % HIGH)
    (plugin / "vault" / "api" / "rules.md").write_text(RULE + "\nmore\n")
    assert not lr.ready(str(plugin / "vault"))
    run(capsys, monkeypatch)
    assert session(data)["ranker"] == {"fallback": 1, "haiku": 1}


def test_opt_out_env_forces_fallback(env, capsys, monkeypatch):
    plugin, data, _ = env
    make_ready(plugin, data, "echo '%s'" % HIGH)
    monkeypatch.setenv(lr.OPT_OUT_ENV, "0")
    run(capsys, monkeypatch)
    assert session(data)["ranker"] == {"fallback": 1, "haiku": 1}


@pytest.fixture
def popen(monkeypatch):
    calls = []
    monkeypatch.setattr(lr.subprocess, "Popen", lambda cmd, **kw: calls.append((cmd, kw)))
    return calls


def test_ensure_spawns_detached_installer_when_missing(env, popen):
    plugin, _data, _ = env
    lr.ensure(str(plugin / "vault"))
    assert len(popen) == 1
    cmd, kw = popen[0]
    assert cmd[-1].endswith(os.path.join("runtime", "install.py")) and cmd[1:3] == ["-X", "utf8"]
    assert all(kw[k] == v for k, v in plugin_state.detached().items())


def test_ensure_skips_when_ready_opted_out_or_failed_recently(env, popen, monkeypatch):
    plugin, data, _ = env
    vault = str(plugin / "vault")
    monkeypatch.setenv(lr.OPT_OUT_ENV, "off")
    lr.ensure(vault)
    monkeypatch.delenv(lr.OPT_OUT_ENV)
    (data / "install.failed").write_text(lr.stamp())
    lr.ensure(vault)
    (data / "install.failed").write_text("old stamp")  # a failure of another version does not block
    lr.ensure(vault)
    assert len(popen) == 1
    popen.clear()
    (data / "install.failed").unlink()
    make_ready(plugin, data, "true")
    lr.ensure(vault)
    assert popen == []


def test_unsupported_platform_is_disabled(monkeypatch):
    monkeypatch.setattr(lr.sys, "platform", "linux")
    monkeypatch.setenv(lr.OPT_OUT_ENV, "1")
    assert not lr.enabled()


def test_installer_exits_when_lock_is_held(env, monkeypatch):
    _plugin, data, _ = env
    monkeypatch.setattr(install.lr, "enabled", lambda: True)
    monkeypatch.setattr(install, "install", lambda: pytest.fail("second installer ran"))
    with open(data / "install.lock", "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert install.main() == 0


def test_installer_failure_leaves_no_stamp_and_blocks_retry(env, popen, monkeypatch):
    plugin, data, _ = env
    monkeypatch.setattr(install.lr, "enabled", lambda: True)

    def boom():
        raise RuntimeError("pip failed")
    monkeypatch.setattr(install, "install", boom)
    assert install.main() == 0
    assert not (data / "runtime.stamp").exists() and (data / "install.failed").exists()
    assert "FAILED" in (data / "install.log").read_text()
    lr.ensure(str(plugin / "vault"))
    assert popen == []


def test_installer_checks_models_with_sha256(env, monkeypatch):
    plugin, data, _ = env
    good = b"model-bytes"
    import hashlib
    (plugin / "runtime" / "manifest.json").write_text(json.dumps({"files": [
        {"dest": "e5/model.onnx", "url": "https://example.invalid/m", "size": len(good), "sha256": hashlib.sha256(good).hexdigest()}]}))

    def fake_run(cmd, timeout=0):
        with open(cmd[cmd.index("-o") + 1], "wb") as fh:
            fh.write(b"tampered")
    monkeypatch.setattr(install, "run", fake_run)
    with pytest.raises(RuntimeError, match="sha256 mismatch"):
        install.fetch_models()
    assert not (data / "models" / "e5" / "model.onnx").exists()


def test_shipped_requirements_are_hash_locked():
    lines = [ln for ln in open(os.path.join(PLUGIN, "runtime", "requirements.txt")) if ln.strip() and not ln.startswith("#")]
    pins = [ln for ln in lines if "==" in ln]
    assert len(pins) == 3 and "".join(lines).count("--hash=sha256:") >= 3
    manifest = json.load(open(os.path.join(PLUGIN, "runtime", "manifest.json")))
    assert all(f["sha256"] and "/resolve/" in f["url"] and len(f["url"].split("/resolve/")[1].split("/")[0]) == 40 for f in manifest["files"])
    compile(open(os.path.join(PLUGIN, "runtime", "rank.py")).read(), "rank.py", "exec")


def fake_daemon(reply):
    """Listen on the daemon socket and answer one request with `reply` bytes."""
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(lr.sock_path())
    srv.listen(1)
    seen = []

    def serve():
        conn, _ = srv.accept()
        with conn:
            seen.append(conn.recv(65536))
            conn.sendall(reply)
        srv.close()
    threading.Thread(target=serve, daemon=True).start()
    return seen


def test_warm_daemon_answers(env, capsys, monkeypatch):
    plugin, data, _ = env
    make_ready(plugin, data, "exit 1")
    seen = fake_daemon(HIGH.encode())
    run(capsys, monkeypatch)
    assert json.loads(seen[0])["prompt"] == PROMPT
    assert session(data)["ranker"] == {"local": 1, "haiku": 1, "warm": 1} and SPAWNED == []


def test_absent_daemon_spawns_one_and_never_loads_models_in_process(env, capsys, monkeypatch):
    plugin, data, _ = env
    make_ready(plugin, data, "exit 1")

    def no_run(*a, **kw):
        raise AssertionError("subprocess.run must not be called")
    monkeypatch.setattr(lr.subprocess, "run", no_run)
    assert lr.rank(PROMPT, set()) is None
    assert SPAWNED == [1]
    run(capsys, monkeypatch)
    assert SPAWNED == [1, 1] and session(data)["ranker"] == {"fallback": 1, "haiku": 1}


def test_stale_socket_file_spawns_and_falls_back(env, capsys, monkeypatch):
    plugin, data, _ = env
    make_ready(plugin, data, "exit 1")
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(lr.sock_path())
    stale.close()  # the file stays, nobody listens
    run(capsys, monkeypatch)
    assert SPAWNED == [1] and session(data)["ranker"] == {"fallback": 1, "haiku": 1}


@pytest.mark.parametrize("reply", [b"not json", b'{"ids": [1]}', b'{"ids": []}'])
def test_garbage_daemon_reply_falls_back_without_spawn(env, capsys, monkeypatch, reply):
    plugin, data, _ = env
    make_ready(plugin, data, "cat > /dev/null; echo '%s'" % HIGH)
    fake_daemon(reply)
    run(capsys, monkeypatch)
    assert SPAWNED == [] and session(data)["ranker"] == {"fallback": 1, "haiku": 1}


def test_daemon_error_reply_leaves_cleanup_to_daemon(env, capsys, monkeypatch):
    plugin, data, _ = env
    make_ready(plugin, data, "exit 1")
    fake_daemon(b'{"error": "boom"}')
    run(capsys, monkeypatch)
    assert SPAWNED == [1] and os.path.exists(lr.sock_path()) and session(data)["ranker"] == {"fallback": 1, "haiku": 1}


def test_connect_refused_preserves_live_daemon_socket(env, monkeypatch):
    def refused(_request):
        raise ConnectionRefusedError(61, "backlog full")

    monkeypatch.setattr(lr, "ask_daemon", refused)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as srv:
        srv.bind(lr.sock_path())
        srv.listen(4)
        assert lr.rank("prompt", set()) is None
        assert os.path.exists(lr.sock_path())
        assert SPAWNED == [1]


def test_socket_name_ignores_plugin_root(env, monkeypatch):
    monkeypatch.undo()  # real sock_path
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", "/tmp/d")
    monkeypatch.setattr(lr, "stamp", lambda: "s")
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "/nonexistent-a")
    first = lr.sock_path()
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "/nonexistent-b")
    assert lr.sock_path() == first
    monkeypatch.setattr(lr, "stamp", lambda: "t")
    assert lr.sock_path() != first


def test_install_state(env, monkeypatch):
    plugin, data, _ = env
    vault = str(plugin / "vault")
    monkeypatch.setattr(lr.sys, "platform", "darwin")
    monkeypatch.setattr(lr.platform, "machine", lambda: "arm64")
    assert lr.install_state(vault) == "off"  # opt-in
    monkeypatch.setenv(lr.OPT_OUT_ENV, "1")
    assert lr.install_state(vault) == "installing"
    (data / "install.failed").write_text("old stamp")  # a failure of another version is not a failure now
    assert lr.install_state(vault) == "installing"
    (data / "install.failed").write_text(lr.stamp())
    assert lr.install_state(vault) == "failed"
    make_ready(plugin, data, "true")
    assert lr.install_state(vault) == "ready"
    monkeypatch.setattr(lr.platform, "machine", lambda: "x86_64")
    assert lr.install_state(vault) == "unsupported"
    monkeypatch.setattr(lr.platform, "machine", lambda: "arm64")
    monkeypatch.setenv(lr.OPT_OUT_ENV, "off")
    assert lr.install_state(vault) == "off"


def test_prewarm_spawns_once(env):
    plugin, data, _ = env
    vault = str(plugin / "vault")
    lr.prewarm(vault)
    assert SPAWNED == []  # runtime not ready
    make_ready(plugin, data, "true")
    lr.prewarm(vault)
    assert SPAWNED == [1]
    open(lr.sock_path(), "w").close()
    lr.prewarm(vault)
    assert SPAWNED == [1]  # a socket file exists


def test_prewarm_never_raises(env, monkeypatch):
    plugin, data, _ = env
    make_ready(plugin, data, "true")

    def boom():
        raise OSError("no exec")

    monkeypatch.setattr(lr, "spawn_daemon", boom)
    lr.prewarm(str(plugin / "vault"))


def test_prune_vectors_keeps_current_and_two_newest(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    for i, name in enumerate(["vectors-a.npz", "vectors-b.npz", "vectors-c.npz", "vectors-cur.npz"]):
        (tmp_path / name).write_text("x")
        os.utime(tmp_path / name, (1000 + i, 1000 + i))
    (tmp_path / "other.txt").write_text("x")
    install.prune_vectors("vectors-cur.npz")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["other.txt", "vectors-b.npz", "vectors-c.npz", "vectors-cur.npz"]


def test_prune_vectors_ignores_tmp_files(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    for i, name in enumerate(["vectors-a.npz", "vectors-b.npz", "vectors-c.npz", "vectors-cur.npz", "vectors-x.npz.tmp.npz"]):
        (tmp_path / name).write_text("x")
        os.utime(tmp_path / name, (1000 + i, 1000 + i))
    install.prune_vectors("vectors-cur.npz")
    assert (tmp_path / "vectors-x.npz.tmp.npz").exists()
    assert not (tmp_path / "vectors-a.npz").exists()


@pytest.fixture
def rank_mod(env, monkeypatch):
    """rank.py with stub numpy/onnxruntime/tokenizers and no models, so serve() runs without the runtime venv."""
    for name in ("numpy", "onnxruntime", "tokenizers"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    sys.modules["tokenizers"].Tokenizer = object
    mod = load("rank_under_test", os.path.join(PLUGIN, "runtime", "rank.py"))
    for name in ("vault_state", "model"):
        monkeypatch.setattr(mod, name, lambda *a, **kw: None)
    monkeypatch.setattr(mod, "STAMP_CHECK_SECONDS", 0.05)
    return mod


def start_daemon(mod, plugin):
    result = []
    thread = threading.Thread(target=lambda: result.append(mod.serve(str(plugin / "vault"))), daemon=True)
    thread.start()
    for _ in range(200):
        if os.path.exists(lr.sock_path()):
            break
        time.sleep(0.01)
    return thread, result


def ask(payload=b'{"prompt": "x"}\n'):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as cli:
        cli.settimeout(5)
        for _ in range(200):  # the socket file appears at bind(), a moment before listen()
            try:
                cli.connect(lr.sock_path())
                break
            except ConnectionRefusedError:
                time.sleep(0.01)
        else:
            cli.connect(lr.sock_path())
        cli.sendall(payload)
        return json.loads(cli.recv(65536) or b"null")


def test_daemon_exits_and_frees_lock_after_failed_request(env, rank_mod, monkeypatch):
    plugin, data, _ = env
    make_ready(plugin, data, "exit 1")
    monkeypatch.setattr(rank_mod, "answer", lambda *a: 1 / 0)
    thread, result = start_daemon(rank_mod, plugin)
    assert "error" in ask()
    thread.join(5)
    assert result == [0] and not os.path.exists(lr.sock_path())
    with open(lr.sock_path() + ".lock", "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)  # raises if the dead daemon still holds the lock


def test_daemon_exits_when_runtime_stamp_changes(env, rank_mod, monkeypatch):
    plugin, data, _ = env
    make_ready(plugin, data, "exit 1")
    monkeypatch.setattr(rank_mod, "IDLE_SECONDS", 3600)
    thread, result = start_daemon(rank_mod, plugin)
    time.sleep(0.2)
    assert thread.is_alive()
    (data / "runtime.stamp").write_text("newer-generation")
    thread.join(5)
    assert result == [0] and not os.path.exists(lr.sock_path())


def test_daemon_idle_exit_tolerates_missing_socket_file(env, rank_mod, monkeypatch):
    plugin, data, _ = env
    make_ready(plugin, data, "exit 1")
    monkeypatch.setattr(rank_mod, "IDLE_SECONDS", 0.1)
    thread, result = start_daemon(rank_mod, plugin)
    os.unlink(lr.sock_path())
    thread.join(5)
    assert result == [0]


def test_daemon_skips_expired_deadline_and_answers_the_rest(env, rank_mod, monkeypatch):
    plugin, data, _ = env
    make_ready(plugin, data, "exit 1")
    monkeypatch.setattr(rank_mod, "IDLE_SECONDS", 3600)
    calls = []
    monkeypatch.setattr(rank_mod, "answer", lambda vault, raw: calls.append(raw) or {"ids": []})
    thread, _ = start_daemon(rank_mod, plugin)
    try:
        assert ask(json.dumps({"prompt": "x", "deadline": time.time() - 1}).encode() + b"\n") is None
        assert calls == []
        assert ask(json.dumps({"prompt": "x", "deadline": time.time() + 60}).encode() + b"\n") == {"ids": []}
        assert ask() == {"ids": []}  # old client: no deadline
        assert len(calls) == 2
    finally:
        # Stop the daemon before env teardown: a leaked thread would unlink the next test's socket path.
        (data / "runtime.stamp").unlink(missing_ok=True)
        thread.join(5)
        assert not thread.is_alive()


def test_client_request_carries_a_deadline(env, monkeypatch):
    sent = []
    monkeypatch.setattr(lr, "ask_daemon", lambda request: sent.append(json.loads(request)) or None)
    before = time.time()
    lr.rank("p", set())
    assert before + lr.RANK_TIMEOUT <= sent[0]["deadline"] <= time.time() + lr.RANK_TIMEOUT
