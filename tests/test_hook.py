import importlib.util
import io
import json
import os
import subprocess
import sys
import time

import pytest
from conftest import write_config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("hook", os.path.join(ROOT, "plugins", "knowledge", "scripts", "hook.py"))
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)
import issue_queue  # noqa: E402  (hook put the scripts folder on sys.path)

SID = "6f1c2d3e-4a5b-4c6d-8e7f-0a1b2c3d4e5f"
ENSURED = []


@pytest.fixture
def env(tmp_path, monkeypatch):
    plugin = tmp_path / "plugin-abc123"
    (plugin / "vault").mkdir(parents=True)
    write_config(plugin)
    (plugin / "vault" / "_index.md").write_text("INDEX BODY\n")
    (plugin / "vault" / "a.md").write_text("x")
    data = tmp_path / "data"
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(data))
    monkeypatch.delenv(hook.OPT_OUT_ENV, raising=False)
    spawned = []
    monkeypatch.setattr(hook, "spawn_sender", lambda p, mode="send": spawned.append(p if mode == "send" else (p, mode)))
    ENSURED.clear()
    monkeypatch.setattr(hook.local_rank, "ensure", ENSURED.append)
    return plugin, data, spawned


def run(sub, event, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    assert hook.main([sub]) == 0


def session(data):
    return json.loads((data / "sessions" / (SID + ".json")).read_text())


def read_event(path):
    return {"session_id": SID, "tool_name": "Read", "tool_input": {"file_path": str(path)}}


def test_read_vault_file_counts_hit(env, monkeypatch):
    plugin, data, _ = env
    run("track", read_event(plugin / "vault" / "a.md"), monkeypatch)
    run("track", read_event(plugin / "vault" / "a.md"), monkeypatch)
    sess = session(data)
    assert sess["hits"] == {"a.md": 2}
    assert sess["plugin_version"] == "plugin-abc123"


def test_read_outside_vault_ignored(env, monkeypatch, tmp_path):
    _, data, _ = env
    other = tmp_path / "other.md"
    other.write_text("x")
    run("track", read_event(other), monkeypatch)
    assert not (data / "sessions" / (SID + ".json")).exists()


LIVE_MISS = {"mode": "content", "numFiles": 0, "filenames": [], "content": "", "numLines": 0}


def grep_event(plugin, resp):
    return {"session_id": SID, "tool_name": "Grep", "tool_response": resp,
            "tool_input": {"pattern": "foo", "path": str(plugin / "vault")}}


def test_grep_miss_live_shape(env, monkeypatch):
    plugin, data, _ = env
    ev = grep_event(plugin, LIVE_MISS)
    run("track", ev, monkeypatch)
    run("track", ev, monkeypatch)
    assert session(data)["misses"] == ["foo"]


def test_grep_secret_pattern_not_recorded(env, monkeypatch):
    plugin, data, _ = env
    ev = grep_event(plugin, LIVE_MISS)
    ev["tool_input"]["pattern"] = "ghp_" + "a" * 36
    run("track", ev, monkeypatch)
    run("track", ev, monkeypatch)
    assert not (data / "sessions" / (SID + ".json")).exists()


@pytest.mark.parametrize("resp", ["No files found", {"filenames": [], "content": ""}, {"numFiles": 2}])
def test_grep_other_shapes_not_a_miss(env, monkeypatch, resp):
    plugin, data, _ = env
    run("track", grep_event(plugin, resp), monkeypatch)
    assert not (data / "sessions" / (SID + ".json")).exists()


def test_grep_hit_not_a_miss(env, monkeypatch):
    plugin, data, _ = env
    ev = {"session_id": SID, "tool_name": "Grep", "tool_response": {"numFiles": 1, "filenames": ["a.md"]},
          "tool_input": {"pattern": "foo", "path": str(plugin / "vault")}}
    run("track", ev, monkeypatch)
    assert not (data / "sessions" / (SID + ".json")).exists()


def test_capture_counted_separately(env, monkeypatch):
    _, data, _ = env
    cap = "gh issue create -R x --label capture --title t"
    run("track", {"session_id": SID, "tool_name": "Bash", "tool_input": {"command": cap}}, monkeypatch)
    sess = session(data)
    assert sess["captures"] == 1 and sess["proposals"] == 0
    cap2 = "gh issue create -R x --label capture --title t"
    run("track", {"session_id": SID, "tool_name": "Bash", "tool_input": {"command": cap2}}, monkeypatch)
    prop = "gh issue create -R x --label knowledge-proposal --title t"
    run("track", {"session_id": SID, "tool_name": "Bash", "tool_input": {"command": prop}}, monkeypatch)
    sess = session(data)
    assert sess["captures"] == 2 and sess["proposals"] == 1


def test_dedup_comment_counted(env, monkeypatch):
    _, data, _ = env
    for cmd in ("gh issue comment 5 -R acme/knowledge-layer --body x", "gh issue comment 5 -R other/repo --body x"):
        run("track", {"session_id": SID, "tool_name": "Bash", "tool_input": {"command": cmd}}, monkeypatch)
    assert session(data)["comments"] == 1


def test_proposal_detected(env, monkeypatch):
    _, data, _ = env
    cmd = "gh issue create -R x --label knowledge-proposal --title t"
    run("track", {"session_id": SID, "tool_name": "Bash", "tool_input": {"command": cmd}}, monkeypatch)
    run("track", {"session_id": SID, "tool_name": "Bash", "tool_input": {"command": "gh issue create"}}, monkeypatch)
    assert session(data)["proposals"] == 1


def bash_event(cmd):
    return {"session_id": SID, "tool_name": "Bash", "tool_input": {"command": cmd}}


def test_stale_report_from_body_file(env, monkeypatch, tmp_path):
    _, data, _ = env
    body = tmp_path / "b.md"
    body.write_text("### Change type\n\nremove\n\n### Target\n\napi/rules.md#^R3\n\n### Evidence\n\nx\n")
    cmd = "gh issue create -R x --label knowledge-proposal --body-file %s" % body
    run("track", bash_event(cmd), monkeypatch)
    run("track", bash_event(cmd), monkeypatch)
    assert session(data)["stale_reports"] == ["api/rules#^R3"]
    assert session(data)["proposals"] == 2


def test_stale_report_hub_and_inline_body(env, monkeypatch):
    _, data, _ = env
    cmd = ('gh issue create --label knowledge-proposal --body "### Change type\n\nfix\n\n'
           '### Target\n\nentities/person-jane-doe.md\n"')
    run("track", bash_event(cmd), monkeypatch)
    assert session(data)["stale_reports"] == ["person-jane-doe"]


def test_add_and_lesson_proposals_are_not_stale_reports(env, monkeypatch, tmp_path):
    _, data, _ = env
    for kind in ("add", "lesson"):
        body = tmp_path / (kind + ".md")
        body.write_text("### Change type\n\n%s\n\n### Target\n\nnew: lessons/tooling.md\n" % kind)
        run("track", bash_event("gh issue create --label knowledge-proposal --body-file %s" % body), monkeypatch)
    run("track", bash_event("gh issue create --label knowledge-proposal --body-file /nonexistent"), monkeypatch)
    sess = session(data)
    assert sess["stale_reports"] == [] and sess["proposals"] == 3


def test_stale_reports_capped_at_twenty(env, monkeypatch):
    _, data, _ = env
    for i in range(25):
        cmd = "gh issue create --label knowledge-proposal --body '### Change type\n\nfix\n\n### Target\n\nci/rules.md#^R%d\n'" % i
        run("track", bash_event(cmd), monkeypatch)
    assert len(session(data)["stale_reports"]) == hook.MAX_STALE


@pytest.mark.parametrize("value", ["0", "false", "OFF", "No", " off "])
def test_opt_out_makes_track_noop_and_end_deletes(env, monkeypatch, value):
    plugin, data, spawned = env
    run("session-start", {"session_id": SID}, monkeypatch)
    monkeypatch.setenv(hook.OPT_OUT_ENV, value)
    run("track", read_event(plugin / "vault" / "a.md"), monkeypatch)
    assert session(data)["hits"] == {}
    run("session-end", {"session_id": SID}, monkeypatch)
    assert not (data / "sessions" / (SID + ".json")).exists()
    assert spawned == []


def test_session_end_idle_path(env, monkeypatch):
    _, data, spawned = env
    run("session-start", {"session_id": SID}, monkeypatch)
    run("session-end", {"session_id": SID}, monkeypatch)
    assert not (data / "sessions" / (SID + ".json")).exists()
    assert json.loads((data / "idle.json").read_text()) == 1
    assert spawned == []


def test_session_end_active_spawns_sender(env, monkeypatch):
    plugin, data, spawned = env
    run("track", read_event(plugin / "vault" / "a.md"), monkeypatch)
    run("session-end", {"session_id": SID}, monkeypatch)
    assert spawned == [str(data / "sessions" / (SID + ".json"))]


def test_pre_compact_spawns_interim_and_keeps_session(env, monkeypatch):
    plugin, data, spawned = env
    run("track", read_event(plugin / "vault" / "a.md"), monkeypatch)
    run("pre-compact", {"session_id": SID}, monkeypatch)
    path = str(data / "sessions" / (SID + ".json"))
    assert spawned == [(path, "send-interim")]
    assert session(data)["hits"] == {"a.md": 1}


def test_pre_compact_idle_session_sends_nothing(env, monkeypatch):
    _, _, spawned = env
    run("session-start", {"session_id": SID}, monkeypatch)
    run("pre-compact", {"session_id": SID}, monkeypatch)
    assert spawned == []


def test_send_interim_keeps_file_and_idle(env, monkeypatch):
    plugin, data, _ = env
    run("track", read_event(plugin / "vault" / "a.md"), monkeypatch)
    (data / "idle.json").write_text("3")
    calls = []
    monkeypatch.setattr(hook.subprocess, "run", fake_gh(calls))
    path = str(data / "sessions" / (SID + ".json"))
    hook.main(["send-interim", path])
    assert os.path.exists(path)
    comment = [c for c in calls if c[1:3] == ["issue", "comment"]][0]
    body = comment[comment.index("--body") + 1]
    payload = json.loads(body.split("```json\n")[1].rsplit("\n```", 1)[0])
    assert payload["final"] is False and payload["hits"] == {"a.md": 1}
    assert json.loads((data / "idle.json").read_text()) == 3


def test_main_records_error_and_fails_open(env, monkeypatch):
    _plugin, data, _ = env

    def boom(event):
        raise ValueError("bad")

    monkeypatch.setattr(hook, "track", boom)
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
    assert hook.main(["track"]) == 0
    assert json.loads((data / "errors.json").read_text()) == {"track:ValueError": 1}
    assert len((data / "errors.log").read_text().splitlines()) == 1


def test_send_reports_and_resets_errors(env, monkeypatch):
    plugin, data, _ = env
    run("track", read_event(plugin / "vault" / "a.md"), monkeypatch)
    (data / "errors.json").write_text('{"select:OSError": 2}')
    calls = []
    monkeypatch.setattr(hook.subprocess, "run", fake_gh(calls))
    path = str(data / "sessions" / (SID + ".json"))

    def sent():
        comment = [c for c in calls if c[1:3] == ["issue", "comment"]][-1]
        body = comment[comment.index("--body") + 1]
        return json.loads(body.split("```json\n")[1].rsplit("\n```", 1)[0])

    hook.main(["send-interim", path])
    assert sent()["errors"] == {"select:OSError": 2}
    assert not (data / "errors.json").exists()  # taken by the interim send: the final one must not repeat it
    hook.main(["send", path])
    assert sent()["errors"] == {}


def test_session_start_ensures_local_runtime_once(env, monkeypatch):
    plugin, _data, _ = env
    run("session-start", {"session_id": SID}, monkeypatch)
    assert ENSURED == [str(plugin / "vault")]


def test_session_start_survives_missing_autoupdate(env, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "autoupdate", None)
    run("session-start", {"session_id": SID}, monkeypatch)
    assert "INDEX BODY" in capsys.readouterr().out


def test_session_start_output_and_stale_recovery(env, monkeypatch, capsys):
    plugin, data, spawned = env
    (plugin / "vault" / "_index.md").write_text("INDEX BODY\n" + "z" * 20000)
    sdir = data / "sessions"
    sdir.mkdir(parents=True)
    stale = sdir / "old.json"
    stale.write_text("{}")
    os.utime(str(stale), (time.time() - 3 * 3600,) * 2)
    run("session-start", {"session_id": SID}, monkeypatch)
    out = capsys.readouterr().out
    assert "INDEX BODY" in out
    assert str(plugin / "vault") in out
    assert len(out) < 9000
    assert spawned == [str(stale)]
    assert (sdir / (SID + ".json")).exists()


def fake_gh(calls, existing=None):
    def _run(cmd, **kw):
        calls.append(cmd)
        out = ""
        if cmd[1:3] == ["api", "user"]:
            out = "alice\n"
        elif cmd[1:3] == ["issue", "list"]:
            out = json.dumps(existing or [])
        elif cmd[1:3] == ["issue", "create"]:
            out = "https://github.com/acme/knowledge-layer/issues/42\n"
        return subprocess.CompletedProcess(cmd, 0, out, "")
    return _run


def test_send_payload_and_gh_calls(env, monkeypatch):
    plugin, data, _ = env
    run("track", read_event(plugin / "vault" / "a.md"), monkeypatch)
    (data / "idle.json").write_text("3")
    calls = []
    monkeypatch.setattr(hook.subprocess, "run", fake_gh(calls))
    path = str(data / "sessions" / (SID + ".json"))
    hook.main(["send", path])
    assert not os.path.exists(path) and not os.path.exists(path + ".sending")
    assert any(c[1:3] == ["issue", "create"] and "telemetry: alice" in c for c in calls)
    comment = [c for c in calls if c[1:3] == ["issue", "comment"]][0]
    assert comment[3] == "42"
    body = comment[comment.index("--body") + 1]
    payload = json.loads(body.split("```json\n")[1].rsplit("\n```", 1)[0])
    assert payload["schema"] == 11 and payload["final"] is True
    assert payload["captures"] == 0
    assert payload["ranker"] == {}
    assert payload["reread_after_compact"] == 0 and payload["reinjected_after_compact"] == 0
    assert payload["selected"] == {} and payload["select_none"] == 0 and payload["select_fail"] == 0
    assert payload["select_levels"] == {}
    assert payload["stale_reports"] == []
    assert payload["hits"] == {"a.md": 1}
    assert payload["idle_sessions_since_last"] == 3
    assert payload["injected"] == {} and payload["missed"] == {} and payload["reread_after_inject"] == {}
    assert payload["missed_after_none"] == 0 and payload["select_timeout"] == 0
    assert payload["select_ms"] == {"n": 0, "p50": 0, "p95": 0, "max": 0}
    assert payload["ranker_install"] in ("ready", "installing", "failed", "unsupported", "off")
    assert payload["errors"] == {}
    assert json.loads((data / "idle.json").read_text()) == 0
    assert json.loads((data / "issue.json").read_text()) == {"login": "alice", "number": 42}


def test_send_reuses_exact_title_match(env, monkeypatch):
    plugin, data, _ = env
    run("track", read_event(plugin / "vault" / "a.md"), monkeypatch)
    calls = []
    existing = [{"number": 7, "title": "telemetry: alice2"}, {"number": 9, "title": "telemetry: alice"}]
    monkeypatch.setattr(hook.subprocess, "run", fake_gh(calls, existing))
    hook.main(["send", str(data / "sessions" / (SID + ".json"))])
    assert not any(c[1:3] == ["issue", "create"] for c in calls)
    assert [c for c in calls if c[1:3] == ["issue", "comment"]][0][3] == "9"


def test_send_failure_renames_back(env, monkeypatch):
    plugin, data, _ = env
    run("track", read_event(plugin / "vault" / "a.md"), monkeypatch)

    def boom(cmd, **kw):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(hook.subprocess, "run", boom)
    path = str(data / "sessions" / (SID + ".json"))
    hook.main(["send", path])
    assert os.path.exists(path) and not os.path.exists(path + ".sending")
    assert os.path.getmtime(path) > time.time() - 60


WORKER = """
import importlib.util, io, json, sys
spec = importlib.util.spec_from_file_location("hook", sys.argv[1])
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)
ev = json.dumps({"session_id": "6f1c2d3e-4a5b-4c6d-8e7f-0a1b2c3d4e5f", "tool_name": "Read", "tool_input": {"file_path": sys.argv[2]}})
for _ in range(25):
    sys.stdin = io.StringIO(ev)
    hook.main(["track"])
"""


def test_concurrent_track_loses_no_hits(env):
    plugin, data, _ = env
    script = os.path.join(ROOT, "plugins", "knowledge", "scripts", "hook.py")
    procs = [subprocess.Popen([sys.executable, "-c", WORKER, script, str(plugin / "vault" / "a.md")])
             for _ in range(8)]
    assert all(p.wait() == 0 for p in procs)
    errors = data / "errors.log"
    assert session(data)["hits"] == {"a.md": 200}, errors.read_text() if errors.exists() else "no errors.log"


def test_session_start_spawns_at_most_two(env, monkeypatch):
    _, data, spawned = env
    sdir = data / "sessions"
    sdir.mkdir(parents=True)
    for i in range(5):
        f = sdir / ("old%d.json" % i)
        f.write_text("{}")
        os.utime(str(f), (time.time() - 3 * 3600,) * 2)
    run("session-start", {"session_id": SID}, monkeypatch)
    assert len(spawned) == 2


def test_session_start_drops_files_older_than_seven_days(env, monkeypatch):
    _, data, spawned = env
    sdir = data / "sessions"
    sdir.mkdir(parents=True)
    for name in ("dead.json", "dead.json.sending", "dead.json.lock"):
        (sdir / name).write_text("{}")
    for name in ("dead.json", "dead.json.sending"):
        os.utime(str(sdir / name), (1, 1))
    run("session-start", {"session_id": SID}, monkeypatch)
    assert spawned == []
    assert sorted(os.listdir(str(sdir))) == [SID + ".json"]


def test_track_touches_session_file(env, monkeypatch, tmp_path):
    plugin, data, _ = env
    run("track", read_event(plugin / "vault" / "a.md"), monkeypatch)
    path = data / "sessions" / (SID + ".json")
    os.utime(str(path), (1, 1))
    other = tmp_path / "other.md"
    other.write_text("x")
    run("track", read_event(other), monkeypatch)
    assert path.stat().st_mtime > time.time() - 60


def test_send_empty_session_counts_idle_without_gh(env, monkeypatch):
    _, data, _ = env
    run("session-start", {"session_id": SID}, monkeypatch)
    calls = []
    monkeypatch.setattr(hook.subprocess, "run", fake_gh(calls))
    path = str(data / "sessions" / (SID + ".json"))
    hook.main(["send", path])
    assert calls == []
    assert not os.path.exists(path) and not os.path.exists(path + ".sending")
    assert json.loads((data / "idle.json").read_text()) == 1


@pytest.mark.parametrize("unset", ["CLAUDE_PLUGIN_DATA", "CLAUDE_PLUGIN_ROOT"])
def test_missing_env_fails_open(env, monkeypatch, tmp_path, capsys, unset):
    plugin, _, _ = env
    monkeypatch.setenv(unset, "")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    for sub in ("session-start", "track", "session-end"):
        run(sub, read_event(plugin / "vault" / "a.md"), monkeypatch)
    assert capsys.readouterr().out == ""
    assert os.listdir(str(cwd)) == []


def test_opt_out_still_sweeps_week_old_files(env, monkeypatch):
    _, data, spawned = env
    monkeypatch.setenv(hook.OPT_OUT_ENV, "0")
    sdir = data / "sessions"
    sdir.mkdir(parents=True)
    (sdir / "dead.json").write_text("{}")
    os.utime(str(sdir / "dead.json"), (1, 1))
    run("session-start", {"session_id": SID}, monkeypatch)
    assert spawned == []
    assert sorted(os.listdir(str(sdir))) == [SID + ".json"]


def test_only_plain_cat_counts_as_full_read(env, monkeypatch):
    _, data, _ = env
    path = (env[0] / "vault" / "a.md").as_posix()
    for cmd in ("head -5 %s" % path, "sed -n 1,3p %s" % path, "cat >> %s <<EOF" % path, "cat %s | head" % path):
        run("track", bash_event(cmd), monkeypatch)
    sess = session(data)
    assert sess["hits"]["a.md"] == 4 and not sess.get("read_files")
    run("track", bash_event("cat %s" % path), monkeypatch)
    assert session(data)["read_files"] == ["a.md"]


def test_worker_env_makes_every_subcommand_noop(env, monkeypatch, capsys):
    monkeypatch.setenv(hook.WORKER_ENV, "1")
    run("session-start", {"session_id": SID, "source": "startup"}, monkeypatch)
    assert capsys.readouterr().out == ""


def queue_item(data, name, **item):
    qdir = data / "queue"
    qdir.mkdir(parents=True, exist_ok=True)
    (qdir / name).write_text(json.dumps(item))
    old = time.time() - 100
    os.utime(qdir / name, (old, old))
    return qdir / name


def test_flush_files_queued_issue_and_deletes_it(env, monkeypatch):
    _, data, _ = env
    path = queue_item(data, "a.json", title="capture: x", body="B", labels=["capture", "needs-check"])
    calls = []
    monkeypatch.setattr(issue_queue, "gh", lambda *a: calls.append(a) or "url")
    assert hook.main(["flush"]) == 0
    assert calls == [("issue", "create", "-R", hook.repo(), "--title", "capture: x", "--body", "B",
                      "--label", "capture", "--label", "needs-check")]
    assert not path.exists()


def test_flush_keeps_item_when_gh_fails(env, monkeypatch):
    _, data, _ = env
    path = queue_item(data, "a.json", title="t", body="b", labels=["capture"])

    def boom(*a):
        raise OSError("no gh")

    monkeypatch.setattr(issue_queue, "gh", boom)
    assert hook.main(["flush"]) == 0
    assert path.exists() and not (path.parent / "a.json.sending").exists()


def test_session_start_spawns_flush_only_when_queue_has_items(env, monkeypatch, capsys):
    _, data, spawned = env
    run("session-start", {"session_id": SID, "source": "startup"}, monkeypatch)
    assert spawned == []
    queue_item(data, "a.json", title="t", body="b")
    run("session-start", {"session_id": SID, "source": "startup"}, monkeypatch)
    assert any(isinstance(s, tuple) and s[1] == "flush" for s in spawned)


def test_flush_continues_past_a_failing_item(env, monkeypatch):
    _, data, _ = env
    bad = queue_item(data, "a.json", title="bad", body="b", labels="capture")
    good = queue_item(data, "b.json", title="good", body="b")
    calls = []

    def fake(*a):
        calls.append(a)
        if a[0] == "issue" and "bad" in a:
            raise subprocess.CalledProcessError(1, "gh")
        return "ok"

    monkeypatch.setattr(issue_queue, "gh", fake)
    hook.main(["flush"])
    assert bad.exists() and not good.exists()
    assert ("issue", "create", "-R", hook.repo(), "--title", "bad", "--body", "b", "--label", "capture") in calls


def test_flush_stops_when_gh_missing(env, monkeypatch):
    _, data, _ = env
    a = queue_item(data, "a.json", title="t", body="b")
    b = queue_item(data, "b.json", title="t", body="b")
    calls = []

    def fake(*args):
        calls.append(args)
        raise FileNotFoundError("gh")

    monkeypatch.setattr(issue_queue, "gh", fake)
    hook.main(["flush"])
    assert a.exists() and b.exists() and len(calls) == 1


def test_flush_stops_when_auth_fails(env, monkeypatch):
    _, data, _ = env
    queue_item(data, "a.json", title="t", body="b")
    queue_item(data, "b.json", title="t", body="b")
    calls = []

    def fake(*args):
        calls.append(args)
        raise subprocess.CalledProcessError(1, "gh")

    monkeypatch.setattr(issue_queue, "gh", fake)
    hook.main(["flush"])
    assert [c[0] for c in calls] == ["issue", "auth"]


def test_flush_recovers_old_orphan_sending(env, monkeypatch):
    _, data, _ = env
    path = queue_item(data, "a.json", title="t", body="b")
    orphan = path.with_name("a.json.sending")
    path.rename(orphan)
    old = time.time() - 700
    os.utime(orphan, (old, old))
    monkeypatch.setattr(issue_queue, "gh", lambda *a: "url")
    hook.main(["flush"])
    assert not orphan.exists() and not path.exists()


def test_flush_leaves_recent_orphan_and_fresh_files(env, monkeypatch):
    _, data, _ = env
    fresh = queue_item(data, "a.json", title="t", body="b")
    os.utime(fresh, None)
    orphan = fresh.with_name("b.json.sending")
    orphan.write_text("{}")
    calls = []
    monkeypatch.setattr(issue_queue, "gh", lambda *a: calls.append(a))
    hook.main(["flush"])
    assert fresh.exists() and orphan.exists() and calls == []


def test_flush_keeps_broken_json_until_sixty_seconds(env, monkeypatch):
    _, data, _ = env
    path = data / "queue" / "a.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"title": "t", "bo')
    monkeypatch.setattr(issue_queue, "gh", lambda *a: "url")
    old = time.time() - 30
    os.utime(path, (old, old))
    hook.main(["flush"])
    assert path.exists()
    old = time.time() - 90
    os.utime(path, (old, old))
    hook.main(["flush"])
    assert not path.exists()


def test_session_start_prints_queue_path(env, monkeypatch, capsys):
    _, data, _ = env
    run("session-start", {"session_id": SID, "source": "startup"}, monkeypatch)
    assert "Queue path: %s" % (data / "queue") in capsys.readouterr().out


def test_spawn_flush_never_raises(env, monkeypatch):
    def boom(*a, **k):
        raise OSError("x")

    monkeypatch.setattr(hook, "queue_dir", boom)
    hook.spawn_flush()


def test_flush_marks_sending_file_fresh(env, monkeypatch):
    _, data, _ = env
    path = queue_item(data, "a.json", title="t", body="b")
    seen = []

    def gh_stub(*a):
        seen.append(time.time() - os.path.getmtime(str(path) + ".sending"))

    monkeypatch.setattr(issue_queue, "gh", gh_stub)
    hook.main(["flush"])
    assert len(seen) == 1 and seen[0] < 30  # the queue file was 100 s old before the rename



# --- compound Bash reads and stale fixes sent as comments ---

def vault_str(plugin, *rels):
    for r in rels:
        (plugin / "vault" / r).parent.mkdir(parents=True, exist_ok=True)
        (plugin / "vault" / r).write_text("x")
    return (plugin / "vault").as_posix()  # Bash text: a backslash would be an escape


def test_bash_reads_compound_assignment_and_grep(env):
    v = vault_str(env[0], "entities/team-example.md")
    cmd = 'V=%s; cat $V/entities/team-example.md; grep -rn -i "example" $V/lessons' % v
    assert hook.bash_reads(cmd) == [("entities/team-example.md", True)]


def test_bash_reads_multiple_sed_and_cat(env):
    v = vault_str(env[0], "lessons/ua-networks.md", "entities/playbook-example-playbook.md")
    cmd = ("V=%s; sed -n 80,100p $V/lessons/ua-networks.md; sed -n 266,278p $V/lessons/ua-networks.md; "
           "cat $V/entities/playbook-example-playbook.md") % v
    assert hook.bash_reads(cmd) == [("lessons/ua-networks.md", False), ("lessons/ua-networks.md", False),
                                    ("entities/playbook-example-playbook.md", True)]


def test_bash_reads_piped_cat_is_not_full(env):
    v = vault_str(env[0])
    assert hook.bash_reads("V=%s\ncat $V/a.md | head" % v) == [("a.md", False)]


def test_bash_reads_export_and_braces(env):
    v = vault_str(env[0], "x.md", "y.md")
    assert hook.bash_reads('export V=%s && cat "$V/x.md"' % v) == [("x.md", True)]
    assert hook.bash_reads("V=%s; cat ${V}/y.md" % v) == [("y.md", True)]


def test_bash_reads_ignores_non_vault_and_bad_input(env, tmp_path):
    assert hook.bash_reads("V=%s; cat $V/a.md; cat $W/b.md" % tmp_path) == []
    assert hook.bash_reads('cat "unterminated') == []


def test_bash_reads_redirect_is_not_full(env):
    v = vault_str(env[0])
    assert hook.bash_reads("cat >> %s/a.md <<EOF" % v) == [("a.md", False)]


def test_track_compound_bash_read_counts_hits(env, monkeypatch):
    _, data, _ = env
    v = vault_str(env[0], "b.md")
    run("track", bash_event("V=%s; cat $V/a.md; sed -n 1,2p $V/b.md" % v), monkeypatch)
    sess = session(data)
    assert sess["hits"] == {"a.md": 1, "b.md": 1}
    assert sess["read_files"] == ["a.md"]


def test_stale_report_from_issue_comment(env, monkeypatch, tmp_path):
    _, data, _ = env
    body = tmp_path / "c.md"
    body.write_text("### Change type\nfix\n\n### Target\nentities/metric-example-metric\n\nnew evidence\n")
    cmd = "gh issue comment 129 -R acme/knowledge-layer --body-file %s" % body
    run("track", bash_event(cmd), monkeypatch)
    sess = session(data)
    assert sess["stale_reports"] == ["metric-example-metric"] and sess["comments"] == 1


def test_issue_comment_without_fields_is_not_stale_report(env, monkeypatch, tmp_path):
    _, data, _ = env
    body = tmp_path / "c.md"
    body.write_text("just more evidence\n")
    cmd = "gh issue comment 129 -R acme/knowledge-layer --body-file %s" % body
    run("track", bash_event(cmd), monkeypatch)
    assert session(data)["stale_reports"] == []
