"""Pure functions of scripts/fleet_health.py."""
from conftest import load_script

fh = load_script("fleet_health", "scripts/fleet_health.py")


def p(sid, user="a", ended="2026-10-17T10:00:00Z", **kw):
    d = {"schema": 10, "session_id": sid, "user": user, "ended_at": ended, "final": True}
    d.update(kw)
    return d


def test_parse_reads_fenced_json_and_rejects_junk():
    assert fh.parse('x\n```json\n{"a": 1}\n```') == {"a": 1}
    assert fh.parse("no block") is None
    assert fh.parse("```json\n{bad\n```") is None


def test_final_wins_over_later_interim():
    final = p("s1", ended="2026-10-17T10:00:00Z", hits={"x": 1})
    interim = p("s1", ended="2026-10-17T11:00:00Z", final=False)
    assert fh.sessions([interim, final], "2026-10-01T00:00:00Z") == [final]


def test_resumed_session_keeps_each_start_and_dedupes_payloads():
    final = p("s1", started_at="2026-10-17T09:00:00Z")
    interim = p("s1", ended="2026-10-17T11:00:00Z", final=False,
                started_at="2026-10-17T09:00:00Z")
    resumed = p("s1", ended="2026-10-17T13:00:00Z", started_at="2026-10-17T12:00:00Z")
    assert fh.sessions([final, interim, resumed], "2026-10-01T00:00:00Z") == [final, resumed]


def test_latest_interim_wins_without_final():
    a = p("s1", ended="2026-10-17T10:00:00Z", final=False)
    b = p("s1", ended="2026-10-17T11:00:00Z", final=False)
    assert fh.sessions([a, b], "2026-10-01T00:00:00Z") == [b]


def test_window_filter_and_old_schema_without_final():
    old = p("old", ended="2026-09-01T00:00:00Z")
    legacy = {"schema": 8, "session_id": "l", "user": "b", "ended_at": "2026-10-17T00:00:00Z"}
    assert fh.sessions([old, legacy], "2026-10-01T00:00:00Z") == [legacy]


def clean():
    return fh.summarize([p("s1", select_ms={"p95": 900}, selected={"x": 1}, ranker_install="ready",
                           ranker={"local": 3, "warm": 2}, plugin_version="abc")])


def test_clean_fleet_has_no_alerts():
    s = clean()
    assert s["sessions"] == 1 and s["with_injection"] == 1 and s["fallback_share"] == 0
    assert fh.alerts(s) == []


def test_no_sessions_alerts():
    assert fh.alerts(fh.summarize([]))


def test_ranker_failed_uses_latest_per_user():
    s = fh.summarize([p("s1", ended="2026-10-16T00:00:00Z", ranker_install="failed"),
                      p("s2", ended="2026-10-17T00:00:00Z", ranker_install="ready")])
    assert s["ranker_failed"] == []
    s = fh.summarize([p("s1", ended="2026-10-16T00:00:00Z", ranker_install="ready"),
                      p("s2", ended="2026-10-17T00:00:00Z", ranker_install="failed")])
    assert s["ranker_failed"] == ["a"] and fh.alerts(s)


def test_errors_and_p95_alert():
    s = fh.summarize([p("s1", errors={"select:OSError": 2}, select_ms={"p95": fh.P95_ALERT_MS + 1})])
    text = " ".join(fh.alerts(s))
    assert "select:OSError" in text and "p95" in text


def test_render_has_no_prompt_text():
    s = fh.summarize([p("s1", prompt="SECRET PROMPT", misses=["term"], missed={"api/rules": 2})])
    out = fh.render(s, fh.alerts(s))
    assert "SECRET PROMPT" not in out and "api/rules" in out and "term" in out


def test_main_uses_supported_utc_clock_and_preserves_cutoff(monkeypatch):
    import datetime as real
    import types

    class Frozen(real.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 20, 12, 30, 5, tzinfo=tz)

        @classmethod
        def utcnow(cls):
            raise DeprecationWarning("utcnow")

    seen = []
    monkeypatch.setattr(fh, "datetime", types.SimpleNamespace(
        datetime=Frozen, timedelta=real.timedelta, timezone=real.timezone))
    monkeypatch.setattr(fh, "fetch", lambda repo: [])
    monkeypatch.setattr(fh, "sessions", lambda payloads, since: seen.append(since) or [])
    fh.main(["--days", "7", "--repo", "example-org/example-repo"])
    assert seen == ["2026-10-13T12:30:05Z"]


def test_main_uses_configured_repo(monkeypatch):
    seen = []
    monkeypatch.setattr(fh, "REPO", "example-org/configured")
    monkeypatch.setattr(fh, "fetch", lambda repo: seen.append(repo) or [])
    fh.main([])
    assert seen == ["example-org/configured"]


def test_main_repo_override(monkeypatch):
    seen = []
    monkeypatch.setattr(fh, "fetch", lambda repo: seen.append(repo) or [])
    fh.main(["--repo", "example-org/override"])
    assert seen == ["example-org/override"]


def test_main_empty_repo_exits_before_fetch(monkeypatch):
    import pytest
    monkeypatch.setattr(fh, "REPO", "")
    monkeypatch.setattr(fh, "fetch", lambda repo: pytest.fail("must not fetch without a repo"))
    with pytest.raises(SystemExit) as exc:
        fh.main([])
    assert exc.value.code == 2
