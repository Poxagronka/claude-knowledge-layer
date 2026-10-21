import hashlib
import json
import os
import subprocess
import sys
import time

import pytest

fcntl = pytest.importorskip("fcntl")  # POSIX-only module
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugins", "knowledge", "scripts"))
import autoupdate as au  # noqa: E402
import config  # noqa: E402

MARKET = config.plugin_id().split("@", 1)[1]


class Spy:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))


def setup(monkeypatch, tmp_path, claude="/bin/claude"):
    spy = Spy()
    monkeypatch.setattr(au.subprocess, "Popen", spy)
    monkeypatch.setattr(au.shutil, "which", lambda n: claude)
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path / "plugins" / "cache" / MARKET / "knowledge" / "1"))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "data"))
    for k in ("KNOWLEDGE_AUTOUPDATE", "KNOWLEDGE_SELECT_NESTED"):
        monkeypatch.delenv(k, raising=False)
    return spy


def test_spawns_detached_child(monkeypatch, tmp_path):
    spy = setup(monkeypatch, tmp_path)
    au.maybe_update()
    argv, kw = spy.calls[0]
    assert argv == [sys.executable, "-X", "utf8", os.path.abspath(au.__file__), "--run", "/bin/claude"]
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


MKT = ["plugin", "marketplace", "update", MARKET]
UPDATE = [MKT, ["plugin", "update", "knowledge@" + MARKET]]
INSTALL = ["plugin", "install", "knowledge@" + MARKET]
HEAL = [MKT, INSTALL]


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



def child_calls(monkeypatch, root, results=None, restore=True):
    """Run the child with scripted exit codes (default 0). Returns the argv tails.

    A fake install recreates the version dir with intact files when `restore` is set, like the real CLI.
    """
    calls = []
    codes = iter(results or [])

    def fake(argv, **kw):
        assert kw["timeout"] == au.TIMEOUT
        calls.append(argv[1:])
        if argv[1:] == INSTALL and restore:
            root.mkdir(exist_ok=True)
            (root / "a.py").write_text("x")
            (root / "integrity.json").write_text(json.dumps({"a.py": hashlib.sha256(b"x").hexdigest()}))
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


def test_heal_installs_in_place_without_uninstall(monkeypatch, tmp_path):
    root = broken_root(tmp_path)
    assert child_calls(monkeypatch, root) == HEAL
    assert (root / "a.py").read_text() == "x"
    assert not os.path.exists(str(root) + ".heal-bak")


def test_failed_install_restores_old_dir(monkeypatch, tmp_path):
    root = broken_root(tmp_path)
    assert child_calls(monkeypatch, root, [0, 1], restore=False) == HEAL
    assert (root / "a.py").read_text() == "edited"
    assert not os.path.exists(str(root) + ".heal-bak")
    assert (tmp_path / "data" / "autoupdate.stamp").exists()


def test_noop_install_is_rolled_back(monkeypatch, tmp_path):
    root = broken_root(tmp_path)
    child_calls(monkeypatch, root, [0, 0], restore=False)  # install exits 0 but restores nothing
    assert (root / "a.py").read_text() == "edited"


def test_install_of_newer_version_dir_counts(monkeypatch, tmp_path):
    root = broken_root(tmp_path)
    new = tmp_path / "plugin2"
    new.mkdir()
    (new / "a.py").write_text("y")
    (new / "integrity.json").write_text(json.dumps({"a.py": hashlib.sha256(b"y").hexdigest()}))
    monkeypatch.setattr(au, "ROOT", str(root))
    assert au.installed_ok(str(root) + ".heal-bak")
    assert not au.installed_ok(str(new))  # only the broken ROOT remains


def test_cannot_move_dir_skips_install(monkeypatch, tmp_path):
    def deny(a, b):
        raise OSError("busy")
    monkeypatch.setattr(au.os, "rename", deny)
    assert child_calls(monkeypatch, broken_root(tmp_path)) == [MKT]
    assert "cannot move" in (tmp_path / "data" / "autoupdate.log").read_text()


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


def test_marketplace_failure_skips_heal(monkeypatch, tmp_path):
    assert child_calls(monkeypatch, broken_root(tmp_path), [1]) == [MKT]


def test_timeout_is_failure(monkeypatch, tmp_path):
    def boom(argv, **kw):
        raise subprocess.TimeoutExpired(argv, kw["timeout"])
    monkeypatch.setattr(au.subprocess, "call", boom)
    assert au.call("/bin/claude", ["plugin", "update"]) is False
    monkeypatch.setattr(au, "ROOT", str(broken_root(tmp_path)))
    au.run("/bin/claude")


def test_prune_cache_keeps_current_and_previous(monkeypatch, tmp_path):
    base = tmp_path / "plugins" / "cache" / MARKET / "knowledge"
    for i, name in enumerate(["1", "2", "3", "4"]):
        (base / name).mkdir(parents=True)
        os.utime(base / name, (1000 + i, 1000 + i))
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(base / "3"))
    au.prune_cache()
    assert sorted(p.name for p in base.iterdir()) == ["3", "4"]


def test_prune_cache_handles_trailing_slash(monkeypatch, tmp_path):
    base = tmp_path / "plugins" / "cache" / MARKET / "knowledge"
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


def test_crlf_file_hashes_like_lf(monkeypatch, tmp_path):
    root = make_root(tmp_path, {"a.py": "x\ny\n"})
    (root / "a.py").write_bytes(b"x\r\ny\r\n")
    monkeypatch.setattr(au, "ROOT", str(root))
    assert au.broken() is False


def test_windows_root_with_forward_slashes_spawns(monkeypatch, tmp_path):
    spy = setup(monkeypatch, tmp_path)
    monkeypatch.setattr(au, "CACHE_MARK", "\\plugins\\cache\\")
    monkeypatch.setattr(au.os.path, "normpath", lambda p: p.replace("/", "\\"))
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "C:/Users/u/.claude/plugins/cache/" + MARKET + "/knowledge/1")
    au.maybe_update()
    assert len(spy.calls) == 1


def test_final_install_failure_is_logged(monkeypatch, tmp_path):
    child_calls(monkeypatch, broken_root(tmp_path), [0, 1], restore=False)
    assert "claude plugin install" in (tmp_path / "data" / "autoupdate.log").read_text()


def log_text(tmp_path):
    return (tmp_path / "data" / "autoupdate.log").read_text()


def test_marketplace_failure_is_logged_and_retried_after_the_backoff(monkeypatch, tmp_path):
    root = broken_root(tmp_path)
    assert child_calls(monkeypatch, root, [1]) == [MKT]
    assert "marketplace update failed" in log_text(tmp_path)
    stamp = str(tmp_path / "data" / "autoupdate.stamp")
    assert au.fresh(stamp)
    age = time.time() - os.path.getmtime(stamp)
    assert au.FAIL_BACKOFF - 5 <= au.INTERVAL - age <= au.FAIL_BACKOFF + 5  # stale again after FAIL_BACKOFF s
    assert child_calls(monkeypatch, root) == []  # still inside the backoff


def test_plugin_update_failure_is_logged(monkeypatch, tmp_path):
    assert child_calls(monkeypatch, make_root(tmp_path, {"a.py": "x"}), [0, 1]) == UPDATE
    assert "plugin update failed" in log_text(tmp_path)
    stamp = str(tmp_path / "data" / "autoupdate.stamp")
    assert time.time() - os.path.getmtime(stamp) > au.INTERVAL - au.FAIL_BACKOFF - 5


def test_success_keeps_the_full_hourly_stamp(monkeypatch, tmp_path):
    child_calls(monkeypatch, make_root(tmp_path, {"a.py": "x"}))
    assert time.time() - os.path.getmtime(str(tmp_path / "data" / "autoupdate.stamp")) < 5


def test_missing_claude_is_logged_once_per_backoff(monkeypatch, tmp_path):
    spy = setup(monkeypatch, tmp_path, claude=None)
    au.maybe_update()
    au.maybe_update()
    assert spy.calls == [] and log_text(tmp_path).count("claude is not on PATH") == 1


def test_prune_cache_keeps_versions_an_open_session_uses(monkeypatch, tmp_path):
    base = tmp_path / "plugins" / "cache" / MARKET / "knowledge"
    for i, name in enumerate(["1", "2", "3", "4"]):
        (base / name).mkdir(parents=True)
        os.utime(base / name, (1000 + i, 1000 + i))
    sessions = tmp_path / "data" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "live.json").write_text(json.dumps({"plugin_version": "1"}))
    (sessions / "sent.json.sending").write_text(json.dumps({"plugin_version": "2"}))
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(base / "3"))
    au.prune_cache()
    assert sorted(p.name for p in base.iterdir()) == ["1", "3", "4"]
