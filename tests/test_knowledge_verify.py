import io
import json
import os
import subprocess
import sys
import types
import urllib.error

import pytest
from conftest import load_script

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
kv = load_script("kv", "scripts/knowledge_verify.py")

TODAY = "2026-10-17"
USERS = [
    {"github": "JaneDoe", "name": "Jane Doe", "title": "UA Lead", "department": "Engineering"},
    {"github": "", "name": "No Github", "title": "QA", "department": "Engineering"},
    {"github": "outside", "name": "Outside Org", "title": "Dev", "department": "Platform"},
]

REPOS = {
    "repos/o/live": {"archived": False, "pushed_at": "2026-09-01T10:00:00Z", "description": "Live repo."},
    "repos/o/diff": {"archived": False, "pushed_at": "2026-09-01T10:00:00Z", "description": "Other words"},
    "repos/o/old": {"archived": False, "pushed_at": "2025-01-01T10:00:00Z", "description": "x"},
    "repos/o/arch": {"archived": True, "pushed_at": "2026-09-01T10:00:00Z", "description": "x"},
    "repos/o/r/contents/a/b.md": {"name": "b.md"},
}


class FakeGh(object):
    def __init__(self, fail=False, stdout=None, stderr=None):
        self.calls = []
        self.fail = fail
        self.stdout, self.stderr = stdout, stderr

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd[2])
        if self.stdout is not None:
            return subprocess.CompletedProcess(cmd, 0, self.stdout, "")
        if self.stderr is not None:
            return subprocess.CompletedProcess(cmd, 1, "", self.stderr)
        if self.fail:
            return subprocess.CompletedProcess(cmd, 1, "", "network down")
        if cmd[2] in REPOS:
            return subprocess.CompletedProcess(cmd, 0, json.dumps(REPOS[cmd[2]]), "")
        return subprocess.CompletedProcess(cmd, 1, "", "gh: Not Found (HTTP 404)")


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeHttp(object):
    def __init__(self, rows=USERS, boom=False):
        self.rows, self.boom, self.requests = rows, boom, []

    def __call__(self, req, timeout=0):
        self.requests.append(req)
        if self.boom:
            raise urllib.error.URLError("down")
        return FakeResp(json.dumps(self.rows).encode())


@pytest.fixture
def fakes(monkeypatch):
    gh, http = FakeGh(), FakeHttp()
    monkeypatch.setattr(kv.subprocess, "run", gh)
    monkeypatch.setattr(kv.urllib.request, "build_opener", lambda *handlers: types.SimpleNamespace(open=http))
    return gh, http


def ctx(env=None):
    base = {"DIRECTORY_API_KEY": "directory_secretkey", "DIRECTORY_API_BASE": "https://directory.test/"}
    return kv.Context(kv.datetime.date.fromisoformat(TODAY), base if env is None else env)


def rec(query, purpose=None, meta=None):
    return {"id": "x", "query": query, "purpose": purpose, "meta": meta or {}}


def verdict(query, **kw):
    return kv.verify(kw.pop("context", None) or ctx(), rec(query, **kw))


@pytest.mark.parametrize("query,expected", [
    ("github:repo:o/live", "ok"),
    ("github:repo:o/missing", "gone"),
    ("github:repo:o/arch", "gone"),
    ("github:repo:o/old", "gone"),
    ("github:path:o/r:a/b.md", "ok"),
    ("github:path:o/r:nope.md", "gone"),
    ("directory:user:janedoe", "ok"),
    ("directory:user:ghost", "gone"),
    ("directory:user:outside", "ok"),
    ("directory:user-name:no  github", "ok"),
    ("directory:user-name:Nobody", "gone"),
    ("confluence:123", "manual"),
    ("slack:C1:1712345678.123456", "manual"),
])
def test_every_query_type_and_verdict(fakes, query, expected):
    assert verdict(query)["verdict"] == expected


def test_repo_description_change_is_reported(fakes):
    out = verdict("github:repo:o/diff", purpose="Recorded purpose")
    assert out["verdict"] == "ok"
    assert out["diff"] == {"description": ["Recorded purpose", "Other words"]}
    assert verdict("github:repo:o/live", purpose="Live repo")["verdict"] == "ok"


def test_user_title_and_department_change(fakes):
    out = verdict("directory:user:janedoe", meta={"title": "UA Manager", "department": "Engineering"})
    assert out["verdict"] == "changed"
    assert out["diff"] == {"title": ["UA Manager", "UA Lead"]}


def test_users_fetched_once_and_key_sent_not_printed(fakes):
    _, http = fakes
    c = ctx()
    for q in ("directory:user:janedoe", "directory:user-name:Jane Doe", "directory:user:ghost"):
        out = kv.verify(c, rec(q))
        assert "directory_secretkey" not in json.dumps(out)
    assert len(http.requests) == 1
    assert http.requests[0].full_url == "https://directory.test/api/v1/users"
    assert http.requests[0].get_header("Authorization") == "Bearer directory_secretkey"


def test_missing_key_and_http_failure_are_errors(fakes, monkeypatch):
    assert verdict("directory:user:janedoe", context=ctx({}))["verdict"] == "error"
    monkeypatch.setattr(kv.urllib.request, "build_opener",
                        lambda *h: types.SimpleNamespace(open=FakeHttp(boom=True)))
    assert verdict("directory:user:janedoe")["verdict"] == "error"


def test_gh_failure_other_than_404_is_error(monkeypatch):
    monkeypatch.setattr(kv.subprocess, "run", FakeGh(fail=True))
    assert verdict("github:repo:o/live")["verdict"] == "error"


@pytest.mark.parametrize("rows", [[], {"error": "outage"}, {"data": []}, {"data": USERS, "next": "c2"}, None])
def test_bad_users_reply_is_error_not_gone(monkeypatch, rows):
    monkeypatch.setattr(kv.urllib.request, "build_opener", lambda *h: types.SimpleNamespace(open=FakeHttp(rows=rows)))
    assert verdict("directory:user:janedoe")["verdict"] == "error"


def test_http_base_is_refused(fakes):
    _, http = fakes
    c = ctx({"DIRECTORY_API_KEY": "k", "DIRECTORY_API_BASE": "http://directory.test"})
    assert kv.verify(c, rec("directory:user:janedoe"))["verdict"] == "error"
    assert http.requests == []


def test_redirect_handler_never_follows():
    assert kv.NoRedirect().redirect_request(None, None, 302, "", {}, "http://evil.test") is None


@pytest.mark.parametrize("kwargs", [{"stderr": "gh: forbidden, request id 404abc"}, {"stderr": "gh: Not Found"}])
def test_404_text_outside_http_404_is_error(monkeypatch, kwargs):
    monkeypatch.setattr(kv.subprocess, "run", FakeGh(**kwargs))
    assert verdict("github:repo:o/live")["verdict"] == "error"


def test_gh_null_reply_is_error(monkeypatch):
    monkeypatch.setattr(kv.subprocess, "run", FakeGh(stdout="null"))
    assert verdict("github:repo:o/live")["verdict"] == "error"
    monkeypatch.setattr(kv.subprocess, "run", FakeGh(stdout="{}"))
    assert verdict("github:repo:o/live")["verdict"] == "error"


def test_stale_push_boundary(monkeypatch):
    def at(day):
        monkeypatch.setitem(REPOS, "repos/o/edge", {"archived": False, "pushed_at": day + "T00:00:00Z"})
        monkeypatch.setattr(kv.subprocess, "run", FakeGh())
        return verdict("github:repo:o/edge")["verdict"]
    assert at("2026-04-17") == "ok"
    assert at("2026-04-16") == "gone"


def test_non_dict_user_row_is_skipped(monkeypatch):
    monkeypatch.setattr(kv.urllib.request, "build_opener",
                        lambda *h: types.SimpleNamespace(open=FakeHttp(rows=["x"] + USERS)))
    assert verdict("directory:user:janedoe")["verdict"] == "ok"


def test_gh_results_are_cached(fakes):
    gh, _ = fakes
    c = ctx()
    kv.verify(c, rec("github:repo:o/live"))
    kv.verify(c, rec("github:repo:o/live"))
    assert gh.calls == ["repos/o/live"]


def test_joined_queries_take_the_worst_verdict(fakes):
    out = verdict("github:repo:o/live; github:repo:o/arch; confluence:1")
    assert out["verdict"] == "gone"
    assert verdict("github:repo:o/live; confluence:1")["verdict"] == "manual"
    assert verdict("github:repo:o/live; github:path:o/r:a/b.md")["verdict"] == "ok"


VAULT_HUB = ("---\nid: service-x\ntype: entity\ntags: [kind/service]\nverify_with: github\n"
             "verify_query: github:repo:o/live\nverified: 2026-01-01\n---\n# X\n")
VAULT_RULES = ("---\nid: api/rules\ntype: rule\n---\n# Rules\n\n"
               "## R1 — a [[service-x]]\n[added:: 2026-02-01] [verify_with:: github] [verify_query:: github:repo:o/live] ^R1\nbody\n\n"
               "## R2 — b [[service-x]]\n[added:: 2026-03-01] [verify_with:: none] ^R2\n\n"
               "## R3 — c [[service-x]]\n[added:: 2026-01-01] [verify_with:: github] [verify_query:: github:repo:o/arch] "
               "[last_verified:: 2026-01-05] ^R3\n")
VAULT_REPO = ("---\nid: repos/live\ntype: repo\ntags: [domain/repos]\norg: example-org\nsection: other\nadded: 2026-04-01\n"
              "verify_with: github\nverify_query: github:repo:o/live\nverified: 2026-04-01\n---\n# live\n\n"
              "Live repo. It does a thing.\n\n- GitHub: `o/live`\n\nRelated: [[service-x]]\n")


@pytest.fixture
def vault(tmp_path):
    (tmp_path / "entities").mkdir()
    (tmp_path / "api").mkdir()
    (tmp_path / "repos").mkdir()
    (tmp_path / "entities" / "service-x.md").write_text(VAULT_HUB)
    (tmp_path / "api" / "rules.md").write_text(VAULT_RULES)
    (tmp_path / "repos" / "live.md").write_text(VAULT_REPO)
    return tmp_path


def run_main(vault, capsys, *extra):
    code = kv.main(["--today", TODAY, "--root", str(vault)] + list(extra))
    return code, capsys.readouterr().out


def test_repo_file_is_a_hub_record_with_first_sentence_purpose(vault):
    rec = next(r for r in kv.records(str(vault)) if r["id"] == "repos/live")
    assert rec["kind"] == "hub" and rec["bid"] is None
    assert rec["purpose"] == "Live repo."
    assert rec["seen"] == "2026-04-01"


def test_report_shape_and_exit_zero(fakes, vault, capsys):
    code, out = run_main(vault, capsys)
    report = json.loads(out)
    assert code == 0 and report["checked_at"] == TODAY
    got = {r["id"]: r["verdict"] for r in report["results"]}
    assert got == {"service-x": "ok", "api/rules#^R1": "ok", "api/rules#^R3": "gone", "repos/live": "ok"}


def test_oldest_and_only_filters(fakes, vault, capsys):
    _, out = run_main(vault, capsys, "--oldest", "2")
    assert [r["id"] for r in json.loads(out)["results"]] == ["service-x", "api/rules#^R3"]
    _, out = run_main(vault, capsys, "--only", "api/")
    assert [r["id"] for r in json.loads(out)["results"]] == ["api/rules#^R3", "api/rules#^R1"]


def test_apply_stamps_only_ok_records(fakes, vault, capsys):
    code, _ = run_main(vault, capsys, "--apply")
    assert code == 0
    assert "verified: %s\n" % TODAY in (vault / "entities" / "service-x.md").read_text()
    rules = (vault / "api" / "rules.md").read_text()
    assert "github:repo:o/live] [last_verified:: %s] ^R1" % TODAY in rules
    assert "[last_verified:: 2026-01-05] ^R3" in rules
    assert "^R2" in rules and "[verify_with:: none] ^R2" in rules
    assert "verified: %s\n" % TODAY in (vault / "repos" / "live.md").read_text()
    import check_knowledge_graph as ckg
    # R3 keeps its old date, so it needs its own log entry for the gate.
    (vault / "_verify" / "2026-01-05.json").write_text(json.dumps({"ok": ["api/rules#^R3"]}))
    assert ckg.check(str(vault)) == []


def read_log(vault, day=TODAY):
    return json.loads((vault / "_verify" / (day + ".json")).read_text())


def test_apply_logs_exactly_the_stamped_ids(fakes, vault, capsys):
    run_main(vault, capsys, "--apply")
    log = read_log(vault)
    assert log["checked_at"] == TODAY
    assert log["ok"] == ["api/rules#^R1", "repos/live", "service-x"]


def test_second_same_day_run_merges_the_log(fakes, vault, capsys):
    (vault / "_verify").mkdir()
    (vault / "_verify" / (TODAY + ".json")).write_text(json.dumps({"checked_at": TODAY, "ok": ["earlier#^id"]}))
    run_main(vault, capsys, "--apply", "--only", "service-x")
    assert read_log(vault)["ok"] == ["earlier#^id", "service-x"]


def test_apply_without_ok_records_writes_no_log(fakes, vault, capsys):
    run_main(vault, capsys, "--apply", "--only", "api/rules#^R3")
    assert not (vault / "_verify").exists()


def test_apply_replaces_hub_missing_field_and_existing_date(fakes, vault, capsys):
    hub = vault / "entities" / "service-x.md"
    hub.write_text(VAULT_HUB.replace("verified: 2026-01-01\n", ""))
    run_main(vault, capsys, "--apply")
    assert hub.read_text().count("verified: %s" % TODAY) == 1
    run_main(vault, capsys, "--apply")
    assert hub.read_text().count("verified:") == 1


def test_no_apply_leaves_files_alone(fakes, vault, capsys):
    before = (vault / "api" / "rules.md").read_text()
    run_main(vault, capsys)
    assert (vault / "api" / "rules.md").read_text() == before


def test_out_file_and_env_file(fakes, vault, tmp_path, capsys):
    env = tmp_path / "e.env"
    env.write_text("# c\nexport DIRECTORY_API_KEY='k1'\nDIRECTORY_API_BASE=https://x.test\n")
    assert kv.load_env_file(str(env)) == {"DIRECTORY_API_KEY": "k1", "DIRECTORY_API_BASE": "https://x.test"}
    out = tmp_path / "report.json"
    code, printed = run_main(vault, capsys, "--out", str(out), "--env-file", str(env))
    assert code == 0 and printed == ""
    assert json.loads(out.read_text())["checked_at"] == TODAY


def test_run_failure_exits_two(tmp_path, capsys):
    assert kv.main(["--root", str(tmp_path / "missing")]) == 2
    assert kv.main(["--today", "not-a-date", "--root", str(tmp_path)]) == 2


def test_real_vault_parses(monkeypatch):
    monkeypatch.setattr(kv.subprocess, "run", FakeGh())
    recs = list(kv.records(kv.ckg.DEFAULT_VAULT))
    assert all(r["query"] for r in recs)


@pytest.mark.parametrize("query", [
    "github:path:o/r:../x", "github:path:o/r:a?b=1", "github:path:o/r:a#b", "github:path:o/r:/etc",
    "github:path:o/r:%2e%2e/x", "github:repo:o/../x", "github:repo:o/x?y", "github:path:/r:a",
])
def test_unsafe_paths_are_errors_before_gh(fakes, query):
    gh, _ = fakes
    assert verdict(query)["verdict"] == "error"
    assert gh.calls == []


def test_window_constant_is_shared():
    import repo_catalog
    assert kv.WINDOW_DAYS is repo_catalog.WINDOW_DAYS


class FakeSlack(FakeHttp):
    def __init__(self, reply):
        super().__init__(rows=reply)


def slack_ctx(monkeypatch, reply, token="xoxb-secret"):
    http = FakeSlack(reply)
    monkeypatch.setattr(kv.urllib.request, "build_opener", lambda *h: types.SimpleNamespace(open=http))
    monkeypatch.setattr(kv.subprocess, "run", FakeGh())
    env = {"DIRECTORY_API_KEY": "k", "DIRECTORY_API_BASE": "https://directory.test"}
    if token:
        env["SLACK_BOT_TOKEN"] = token
    return kv.Context(kv.datetime.date.fromisoformat(TODAY), env), http


def hub_rec():
    return {"id": "person-x", "kind": "hub", "query": "github:repo:o/live", "purpose": None,
            "meta": {"slack": "UEXAMPLE"}}


@pytest.mark.parametrize("reply,expected", [
    ({"ok": True, "user": {"deleted": True}}, "gone"),
    ({"ok": True, "user": {"deleted": False}}, "ok"),
    ({"ok": False, "error": "user_not_found"}, "gone"),
    ({"ok": False, "error": "ratelimited"}, "error"),
])
def test_slack_deleted_user_is_gone(monkeypatch, reply, expected):
    c, http = slack_ctx(monkeypatch, reply)
    assert kv.verify(c, hub_rec())["verdict"] == expected
    assert "user=UEXAMPLE" in http.requests[0].full_url


def test_slack_without_token_changes_nothing(monkeypatch):
    c, http = slack_ctx(monkeypatch, {"ok": True}, token=None)
    assert kv.verify(c, hub_rec())["verdict"] == "ok"
    assert http.requests == []


def test_slack_token_not_in_report(monkeypatch):
    c, _ = slack_ctx(monkeypatch, {"ok": True, "user": {"deleted": True}})
    assert "xoxb-secret" not in json.dumps(kv.verify(c, hub_rec()))


def test_apply_writes_nothing_when_one_file_fails_to_render(fakes, vault, capsys, monkeypatch):
    def boom(lines, bid, today):
        raise ValueError("malformed")
    monkeypatch.setattr(kv, "stamp_entry", boom)
    hub = vault / "entities" / "service-x.md"
    before = hub.read_text()
    code, _ = run_main(vault, capsys, "--apply")
    assert code == 2
    assert hub.read_text() == before
    assert not list(vault.rglob("*.tmp"))


MANUAL = ("\n## R4 — d [[service-x]]\n[added:: 2026-01-01] [verify_with:: confluence] "
          "[verify_query:: confluence:page:1] ^R4\n")


def confirm_file(tmp_path, data):
    path = tmp_path / "confirm.json"
    path.write_text(json.dumps(data))
    return str(path)


def test_confirm_stamps_manual_record_and_logs_evidence(fakes, vault, tmp_path, capsys):
    rules = vault / "api" / "rules.md"
    rules.write_text(rules.read_text() + MANUAL)
    ev = "confluence: page 1 read, matches"
    code, _ = run_main(vault, capsys, "--apply", "--only", "api/rules#^R4",
                       "--confirm", confirm_file(tmp_path, {"api/rules#^R4": ev}))
    assert code == 0
    assert "[last_verified:: %s] ^R4" % TODAY in rules.read_text()
    log = read_log(vault)
    assert log["ok"] == ["api/rules#^R4"] and log["agent"] == {"api/rules#^R4": ev}


@pytest.mark.parametrize("data", [
    {"api/rules#^nope": "slack: x"},
    {"service-x": "slack: x"},
    {"api/rules#^R4": "looks fine"},
    {"api/rules#^R4": ""},
    {"api/rules#^R4": "confluence:"},
    {"api/rules#^R4": 5},
    ["api/rules#^R4"],
])
def test_bad_confirm_exits_two_and_stamps_nothing(fakes, vault, tmp_path, capsys, data):
    rules = vault / "api" / "rules.md"
    rules.write_text(rules.read_text() + MANUAL)
    before = (rules.read_text(), (vault / "entities" / "service-x.md").read_text())
    code, _ = run_main(vault, capsys, "--apply", "--confirm", confirm_file(tmp_path, data))
    assert code == 2
    assert before == (rules.read_text(), (vault / "entities" / "service-x.md").read_text())
    assert not (vault / "_verify").exists()


def test_confirm_without_apply_exits_two(fakes, vault, tmp_path, capsys):
    code, _ = run_main(vault, capsys, "--confirm", confirm_file(tmp_path, {}))
    assert code == 2


def test_confirm_of_unstampable_record_exits_two_and_writes_nothing(fakes, vault, tmp_path, capsys, monkeypatch):
    rules = vault / "api" / "rules.md"
    rules.write_text(rules.read_text() + MANUAL)
    before = rules.read_text()
    monkeypatch.setattr(kv, "stamp_entry", lambda lines, bid, today: 0)
    cf = confirm_file(tmp_path, {"api/rules#^R4": "slack: ok"})
    code = kv.main(["--today", TODAY, "--root", str(vault), "--apply", "--only", "api/rules#^R4", "--confirm", cf])
    assert code == 2
    assert "could not be stamped" in capsys.readouterr().err
    assert rules.read_text() == before and not (vault / "_verify").exists()


def test_second_temp_write_failure_preserves_all_originals(fakes, vault, monkeypatch):
    import builtins
    before = {p: p.read_text() for p in vault.rglob("*.md")}
    real_open = builtins.open
    temps = []

    def flaky(path, mode="r", *a, **kw):
        if str(path).endswith(".tmp") and "w" in mode:
            temps.append(str(path))
            if len(temps) == 2:
                raise OSError("disk full")
        return real_open(path, mode, *a, **kw)

    monkeypatch.setattr(builtins, "open", flaky)
    code = kv.main(["--today", TODAY, "--root", str(vault), "--apply"])
    monkeypatch.undo()
    assert code == 2
    assert {p: p.read_text() for p in vault.rglob("*.md")} == before
    assert not list(vault.rglob("*.tmp"))


def test_directory_config_controls_env_path_and_department(fakes, monkeypatch, tmp_path):
    monkeypatch.setitem(kv.CONFIG, "directory", {
        "key_env": "STAFF_KEY", "base_env": "STAFF_BASE",
        "users_path": "/staff/users", "department_regex": r"^Engineering$",
    })
    env_file = tmp_path / "credentials"
    env_file.write_text("STAFF_KEY=custom-key\nSTAFF_BASE=https://staff.test/\n")
    c = ctx(kv.load_env_file(str(env_file)))
    assert kv.verify(c, rec("directory:user:janedoe"))["verdict"] == "ok"
    assert kv.verify(c, rec("directory:user:outside"))["verdict"] == "gone"
    assert kv.verify(c, rec("directory:user-name:No Github"))["verdict"] == "ok"
    request = fakes[1].requests[0]
    assert request.full_url == "https://staff.test/staff/users"
    assert request.get_header("Authorization") == "Bearer custom-key"
    assert len(fakes[1].requests) == 1
    missing = kv.verify(ctx({}), rec("directory:user:janedoe"))
    assert "STAFF_KEY and STAFF_BASE are required" in missing["evidence"]
