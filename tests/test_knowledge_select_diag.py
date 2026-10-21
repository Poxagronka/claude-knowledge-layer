import json
import os

import pytest
from test_knowledge_select import RULE_ID, SID, no_ranker, reply, run, sel, session
from test_knowledge_select import env as select_env  # noqa: F401


@pytest.fixture
def env(request):
    return request.getfixturevalue("select_env")


def test_timeout_is_counted_in_errors(env, capsys, monkeypatch):
    _bin_dir, data = env

    def slow(cmd, **kw):
        raise sel.subprocess.TimeoutExpired(cmd, 1)
    monkeypatch.setattr(sel.subprocess, "run", slow)
    run(capsys, monkeypatch)
    with open(os.path.join(data, "errors.json")) as fh:
        assert json.load(fh) == {"haiku:TimeoutExpired": 1}


def test_paused_turn_counts_as_select_paused_not_select_none(env, capsys, monkeypatch):
    bin_dir, data = env
    stdin = no_ranker(bin_dir, reply([RULE_ID]))
    for _ in range(sel.MAX_FAILS):
        sel.record_result(SID, [], "select_fail", model=True)
    run(capsys, monkeypatch, prompt="How do I tune the bid floor for installs?")
    sess = session(data)
    assert sess["select_paused"] == 1 and sess["select_none"] == 0 and not stdin.exists()


@pytest.mark.parametrize("text", [
    'Sure {"need": 2, "picks": []}\nNote: use {like this}',
    '```json\n{"need": 2, "picks": []}\n```\n{"need": 0}',
    '{bad} {"need": 2, "picks": []}',
])
def test_first_json_takes_the_first_valid_object(text):
    assert sel.first_json(text) == {"need": 2, "picks": []}


def test_first_json_rejects_a_reply_without_an_object():
    with pytest.raises(ValueError):
        sel.first_json("no json {here")
