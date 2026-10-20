import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugins", "knowledge", "scripts"))
import autoupdate as au  # noqa: E402


class Spy:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))


def setup(monkeypatch, tmp_path, claude="/bin/claude"):
    spy = Spy()
    monkeypatch.setattr(au.subprocess, "Popen", spy)
    monkeypatch.setattr(au.shutil, "which", lambda n: claude)
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path / "plugins" / "cache" / "knowledge-layer" / "knowledge" / "1"))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "data"))
    for k in ("KNOWLEDGE_AUTOUPDATE", "KNOWLEDGE_SELECT_NESTED"):
        monkeypatch.delenv(k, raising=False)
    return spy


def test_spawns_detached_child(monkeypatch, tmp_path):
    spy = setup(monkeypatch, tmp_path)
    au.maybe_update()
    argv, kw = spy.calls[0]
    assert argv == [sys.executable, os.path.abspath(au.__file__), "--run", "/bin/claude"]
    assert kw["start_new_session"] is True


def test_throttled_within_an_hour(monkeypatch, tmp_path):
    spy = setup(monkeypatch, tmp_path)
    au.maybe_update()
    stamp = tmp_path / "data" / "autoupdate.stamp"
    stamp.parent.mkdir(parents=True)
    stamp.write_text("")
    au.maybe_update()
    assert len(spy.calls) == 1
    old = time.time() - 3700
    os.utime(stamp, (old, old))
    au.maybe_update()
    assert len(spy.calls) == 2


def test_skips(monkeypatch, tmp_path):
    for env in ({"KNOWLEDGE_AUTOUPDATE": "off"}, {"KNOWLEDGE_SELECT_NESTED": "1"},
                {"CLAUDE_PLUGIN_ROOT": str(tmp_path / "dev")}):
        spy = setup(monkeypatch, tmp_path)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        au.maybe_update()
        assert spy.calls == []
    spy = setup(monkeypatch, tmp_path, claude=None)
    au.maybe_update()
    assert spy.calls == []


MKT = ["plugin", "marketplace", "update", "knowledge-layer"]
UPDATE = [MKT, ["plugin", "update", "knowledge@knowledge-layer"]]
HEAL = [MKT, ["plugin", "uninstall", "knowledge@knowledge-layer", "--keep-data"], ["plugin", "install", "knowledge@knowledge-layer"]]


def make_root(tmp_path, files):
    root = tmp_path / "plugin"
    root.mkdir()
    for n, text in files.items():
        (root / n).write_text(text)
    digests = {n: hashlib.sha256(t.encode()).hexdigest() for n, t in files.items()}
    (root / "integrity.json").write_text(json.dumps(digests))
    return root


@pytest.fixture(autouse=True)
def child_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "data"))
    monkeypatch.setattr(au.time, "sleep", lambda s: SLEPT.append(s))
    SLEPT.clear()


SLEPT = []


def child_calls(monkeypatch, root, results=None):
    """Run the child with scripted exit codes (default 0). Returns the argv tails."""
    calls = []
    codes = iter(results or [])

    def fake(argv, **kw):
        assert kw["timeout"] == au.TIMEOUT
        calls.append(argv[1:])
        return next(codes, 0)
    monkeypatch.setattr(au, "ROOT", str(root))
    monkeypatch.setattr(au.subprocess, "call", fake)
    au.run("/bin/claude")
    return calls


def test_intact_updates(monkeypatch, tmp_path):
    assert child_calls(monkeypatch, make_root(tmp_path, {"a.py": "x"})) == UPDATE


def test_edited_file_heals(monkeypatch, tmp_path):
    root = make_root(tmp_path, {"a.py": "x"})
    (root / "a.py").write_text("edited")
    assert child_calls(monkeypatch, root) == HEAL


def test_missing_file_heals(monkeypatch, tmp_path):
    root = make_root(tmp_path, {"a.py": "x"})
    (root / "a.py").unlink()
    assert child_calls(monkeypatch, root) == HEAL


def test_extra_file_ignored(monkeypatch, tmp_path):
    root = make_root(tmp_path, {"a.py": "x"})
    (root / "extra.pyc").write_text("junk")
    assert child_calls(monkeypatch, root) == UPDATE


def test_no_integrity_file_updates_only(monkeypatch, tmp_path):
    root = make_root(tmp_path, {"a.py": "x"})
    (root / "integrity.json").unlink()
    assert child_calls(monkeypatch, root) == UPDATE


def broken_root(tmp_path):
    root = make_root(tmp_path, {"a.py": "x"})
    (root / "a.py").write_text("edited")
    return root


def test_install_retried_until_success(monkeypatch, tmp_path):
    calls = child_calls(monkeypatch, broken_root(tmp_path), [0, 0, 1, 1, 0])
    assert calls == HEAL[:2] + [HEAL[2]] * 3
    assert SLEPT == [20, 60]


def test_install_gives_up_after_three_retries(monkeypatch, tmp_path):
    calls = child_calls(monkeypatch, broken_root(tmp_path), [0, 0, 1, 1, 1, 1])
    assert len(calls) == 6 and calls[2:] == [HEAL[2]] * 4
    assert SLEPT == [20, 60, 180]


def test_lock_held_child_exits(monkeypatch, tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    with open(data / "autoupdate.lock", "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert child_calls(monkeypatch, make_root(tmp_path, {"a.py": "x"})) == []


def test_child_skips_fresh_stamp_and_touches_stamp(monkeypatch, tmp_path):
    root = make_root(tmp_path, {"a.py": "x"})
    assert child_calls(monkeypatch, root) == UPDATE
    assert (tmp_path / "data" / "autoupdate.stamp").exists()
    assert child_calls(monkeypatch, root) == []


def test_uninstall_failure_skips_install(monkeypatch, tmp_path):
    calls = child_calls(monkeypatch, broken_root(tmp_path), [0, 1])
    assert calls == HEAL[:2]
    assert SLEPT == []


def test_marketplace_failure_skips_heal(monkeypatch, tmp_path):
    assert child_calls(monkeypatch, broken_root(tmp_path), [1]) == [MKT]


def test_timeout_is_failure(monkeypatch, tmp_path):
    def boom(argv, **kw):
        raise subprocess.TimeoutExpired(argv, kw["timeout"])
    monkeypatch.setattr(au.subprocess, "call", boom)
    assert au.call("/bin/claude", ["plugin", "update"]) is False
    monkeypatch.setattr(au, "ROOT", str(broken_root(tmp_path)))
    au.run("/bin/claude")
    assert SLEPT == []


def test_prune_cache_keeps_current_and_previous(monkeypatch, tmp_path):
    base = tmp_path / "plugins" / "cache" / "knowledge-layer" / "knowledge"
    for i, name in enumerate(["1", "2", "3", "4"]):
        (base / name).mkdir(parents=True)
        os.utime(base / name, (1000 + i, 1000 + i))
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(base / "3"))
    au.prune_cache()
    assert sorted(p.name for p in base.iterdir()) == ["3", "4"]


def test_prune_cache_handles_trailing_slash(monkeypatch, tmp_path):
    base = tmp_path / "plugins" / "cache" / "knowledge-layer" / "knowledge"
    for i, name in enumerate(["1", "2", "3", "4"]):
        (base / name).mkdir(parents=True)
        os.utime(base / name, (1000 + i, 1000 + i))
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(base / "3") + "/")
    au.prune_cache()
    assert sorted(p.name for p in base.iterdir()) == ["3", "4"]


def test_prune_cache_ignores_non_cache_root(monkeypatch, tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "c").mkdir()
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path / "a"))
    au.prune_cache()
    assert len(list(tmp_path.iterdir())) == 3
