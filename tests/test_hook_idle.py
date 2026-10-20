import fcntl
import os
import subprocess
import sys
import time

import pytest
from test_hook import ROOT, SID, fake_gh, hook, read_event, run
from test_hook import env as hook_env  # noqa: F401


BUMPS = (
    "import importlib.util, sys\n"
    "spec = importlib.util.spec_from_file_location('hook', sys.argv[1])\n"
    "hook = importlib.util.module_from_spec(spec)\n"
    "sys.modules['hook'] = hook\n"
    "spec.loader.exec_module(hook)\n"
    "for _ in range(20):\n"
    "    hook.bump_idle()\n"
)


@pytest.fixture
def env(request):
    return request.getfixturevalue("hook_env")


def test_send_reset_failure_restores_session(env, monkeypatch):
    plugin, data, _ = env
    run("track", read_event(plugin / "vault" / "a.md"), monkeypatch)
    monkeypatch.setattr(hook.subprocess, "run", fake_gh([]))
    real = hook.write_json

    def flaky(path, obj):
        if path.endswith("idle.json"):
            raise OSError("disk full")
        real(path, obj)

    monkeypatch.setattr(hook, "write_json", flaky)
    path = str(data / "sessions" / (SID + ".json"))
    hook.send(path)
    assert os.path.exists(path) and not os.path.exists(path + ".sending")


def test_concurrent_idle_bumps_preserve_all_counts(env):
    _, data, _ = env
    data.mkdir(exist_ok=True)
    script = os.path.join(ROOT, "plugins", "knowledge", "scripts", "hook.py")
    procs = [subprocess.Popen([sys.executable, "-c", BUMPS, script]) for _ in range(4)]
    assert all(p.wait() == 0 for p in procs)
    assert hook.read_json(str(data / "idle.json"), 0) == 80


def test_idle_lock_contention_skips_promptly(env):
    _, data, _ = env
    data.mkdir(exist_ok=True)
    with open(str(data / "idle.json.lock"), "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        start = time.time()
        hook.bump_idle()
        assert time.time() - start < 1
    assert not os.path.exists(str(data / "idle.json"))


def test_send_keeps_idle_bumps_made_during_the_network_call(env, monkeypatch):
    plugin, data, _ = env
    run("track", read_event(plugin / "vault" / "a.md"), monkeypatch)
    (data / "idle.json").write_text("3")
    inner = fake_gh([])

    def during(cmd, **kw):
        if cmd[1:3] == ["issue", "comment"]:
            hook.bump_idle()
        return inner(cmd, **kw)

    monkeypatch.setattr(hook.subprocess, "run", during)
    hook.send(str(data / "sessions" / (SID + ".json")))
    assert hook.read_json(str(data / "idle.json"), -1) == 1
