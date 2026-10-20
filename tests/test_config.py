"""config.py loader and the issues_repo-off path (no gh call anywhere)."""
import json
import os
import sys
import time

import pytest
from conftest import write_config
from test_hook import SID, hook, read_event, run
from test_hook import env as hook_env  # noqa: F401

import issue_queue  # noqa: E402  (hook put the scripts folder on sys.path)

config = sys.modules.get("config") or __import__("config")


@pytest.fixture
def env(request):
    return request.getfixturevalue("hook_env")


def test_load_reads_plugin_root_file(env):
    plugin, _, _ = env
    write_config(plugin, org="Globex", marketplace="globex-mkt")
    cfg = config.load()
    assert cfg["org"] == "Globex" and config.plugin_id() == "knowledge@globex-mkt"
    assert config.issues_repo() == "acme/knowledge-layer"


def test_load_falls_back_to_file_next_to_scripts(env):
    plugin, _, _ = env
    (plugin / "knowledge.json").unlink()
    fallback = os.path.join(os.path.dirname(os.path.dirname(config.__file__)), "knowledge.json")
    assert config.candidates()[-1] == fallback
    assert config.load()["marketplace"] == json.load(open(fallback))["marketplace"]


@pytest.mark.parametrize("content", ["{not json", "[1, 2]", ""])
def test_broken_file_gives_defaults(env, monkeypatch, content):
    plugin, _, _ = env
    (plugin / "knowledge.json").write_text(content)
    monkeypatch.setattr(config, "candidates", lambda: [str(plugin / "knowledge.json")])
    cfg = config.load()
    assert cfg == config.DEFAULTS and config.issues_repo() == ""
    assert config.plugin_id() == "knowledge@knowledge-layer"


def test_text_ignores_non_strings(env):
    plugin, _, _ = env
    write_config(plugin, issues_repo=None, org="  Spaced  ")
    assert config.issues_repo() == "" and config.text("org") == "Spaced"


def test_session_header_uses_config(env, capsys, monkeypatch):
    run("session-start", {"session_id": SID}, monkeypatch)
    out = capsys.readouterr().out
    assert out.startswith("# Acme knowledge vault") and "Acme questions" in out
    assert "`org-example` hub" in out and "knowledge-propose" in out and "filing is off" not in out


def test_filing_off_header_and_state(env, capsys, monkeypatch):
    plugin, _, _ = env
    write_config(plugin, issues_repo="")
    run("session-start", {"session_id": SID}, monkeypatch)
    out = capsys.readouterr().out
    assert "Issue filing is off: set issues_repo in knowledge.json." in out
    assert "knowledge-propose" not in out
    assert hook.opted_out() and hook.capture_off()


def test_filing_off_makes_no_gh_call(env, monkeypatch, tmp_path):
    plugin, data, spawned = env
    write_config(plugin, issues_repo="")
    calls = []
    monkeypatch.setattr(issue_queue, "gh", lambda *a: calls.append(a) or "url")
    monkeypatch.setattr(hook.subprocess, "run", lambda *a, **k: calls.append(a))
    queue = data / "queue"
    queue.mkdir(parents=True)
    item = queue / "1.json"
    item.write_text(json.dumps({"title": "capture: x", "body": "B", "labels": ["capture"]}))
    os.utime(item, (time.time() - 60,) * 2)
    run("track", read_event(plugin / "vault" / "a.md"), monkeypatch)
    hook.spawn_flush()
    issue_queue.flush_queue(settle=0)
    hook.main(["flush", str(queue)])
    hook.send(str(tmp_path / "missing.json"))
    import capture
    capture.capture_worker(SID, str(tmp_path / "t.jsonl"))
    capture.spawn_worker_if(SID, str(tmp_path / "t.jsonl"), "volume", 0)
    assert calls == [] and spawned == []
    assert item.exists()  # the queue file stays, nothing is lost
    assert not (data / "sessions" / (SID + ".json")).exists()


def test_filing_off_send_does_not_touch_gh_for_real_session(env, monkeypatch):
    plugin, data, _ = env
    run("track", read_event(plugin / "vault" / "a.md"), monkeypatch)  # filing on: session created
    write_config(plugin, issues_repo="")
    calls = []
    monkeypatch.setattr(issue_queue, "gh", lambda *a: calls.append(a) or "x")
    path = hook.session_path(SID)
    hook.send(path)
    assert calls == [] and os.path.exists(path)
