import json

import pytest
from test_hook import SID, hook, read_event, run
from test_hook import env as hook_env  # noqa: F401


@pytest.fixture
def env(request):
    return request.getfixturevalue("hook_env")


def test_failed_send_puts_the_errors_back(env, monkeypatch):
    plugin, data, _ = env
    run("track", read_event(plugin / "vault" / "a.md"), monkeypatch)
    (data / "errors.json").write_text('{"select:OSError": 2}')

    def boom(*a, **k):
        raise OSError("no network")
    monkeypatch.setattr(hook.subprocess, "run", boom)
    hook.main(["send-interim", str(data / "sessions" / (SID + ".json"))])
    assert json.loads((data / "errors.json").read_text()) == {"select:OSError": 2}
