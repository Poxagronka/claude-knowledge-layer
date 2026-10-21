import pytest
from test_hook import SID, grep_event, hook, read_event, run
from test_hook import env as hook_env  # noqa: F401


@pytest.fixture
def env(request):
    return request.getfixturevalue("hook_env")


def test_grep_content_mode_hit_not_a_miss(env, monkeypatch):
    plugin, data, _ = env
    resp = {"mode": "content", "numFiles": 0, "filenames": [], "content": "a.md:1:foo", "numLines": 1}
    run("track", grep_event(plugin, resp), monkeypatch)
    assert not (data / "sessions" / (SID + ".json")).exists()


def test_send_skips_non_uuid_session(env, monkeypatch):
    plugin, data, _ = env
    ev = read_event(plugin / "vault" / "a.md")
    ev["session_id"] = "t2"
    run("track", ev, monkeypatch)
    calls = []
    monkeypatch.setattr(hook.subprocess, "run", lambda *a, **k: calls.append(a))
    hook.send(str(data / "sessions" / "t2.json"))
    assert calls == []
