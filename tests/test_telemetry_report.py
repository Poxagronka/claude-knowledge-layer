"""Pure functions of scripts/telemetry_report.py."""
import datetime as dt
import json

from conftest import load_script

tr = load_script("telemetry_report", "scripts/telemetry_report.py")
NOW = dt.datetime(2026, 10, 20, tzinfo=dt.timezone.utc)


def row(user, sid, ended="2026-10-18T10:00:00Z", **kw):
    d = {"schema": 10, "session_id": sid, "started_at": ended, "ended_at": ended, "final": True,
         "plugin_version": "v1", "select_ms": {"n": 10, "p50": 100, "p95": 500, "max": 900}}
    d.update(kw)
    return {"user": user, "body": "```json\n%s\n```" % json.dumps(d)}


def keys(rows):
    return {a["key"] for a in tr.analyze(rows, 7, NOW)["anomalies"]}


def test_fail_rate_fires_and_not():
    assert "select-fail-rate:a" in keys([row("a", "1", select_fail=3)])
    assert not keys([row("a", "1", select_fail=2)])
    assert not keys([row("a", "1", select_fail=3, select_ms={"n": 100})])


def test_new_error_vs_previous_window():
    old = row("a", "0", ended="2026-10-10T10:00:00Z", errors={"x:E": 1})
    new = row("a", "1", errors={"x:E": 2, "y:F": 1})
    assert keys([old, new]) == {"error:y:F"}


def test_latency_median():
    slow = [row("a", str(i), select_ms={"n": 5, "p95": 9000}) for i in range(3)]
    assert "select-latency:a" in keys(slow)
    fast = [row("a", str(i), select_ms={"n": 5, "p95": 100}) for i in range(2)]
    assert not keys(slow[:1] + fast)


def test_stale_version():
    new = row("b", "2", ended="2026-10-18T10:00:00Z", started_at="2026-10-16T10:00:00Z", plugin_version="v2")
    old = row("a", "1", ended="2026-10-18T08:00:00Z")
    assert "stale-version:a" in keys([old, new, row("a", "0", ended="2026-10-10T08:00:00Z")])
    fresh = row("a", "3", ended="2026-10-18T09:00:00Z", plugin_version="v2")
    assert not keys([new, fresh])
    young = row("b", "2", ended="2026-10-18T20:00:00Z", plugin_version="v2")
    assert not keys([row("a", "1", ended="2026-10-18T10:00:00Z"), young])


def test_dedupe_bad_block_and_window():
    interim = row("a", "1", final=False, select_fail=0, ended="2026-10-18T09:00:00Z")
    final = row("a", "1", select_fail=1, started_at="2026-10-18T09:00:00Z")
    bad = {"user": "a", "body": "```json\n{oops\n```"}
    old = row("a", "2", ended="2026-09-01T00:00:00Z")
    res = tr.analyze([interim, final, bad, old], 7, NOW)
    assert res["bad_blocks"] == 1
    assert res["users"]["a"]["sessions"] == 1
    assert res["users"]["a"]["select_fail"] == 1


def test_resumed_session_keeps_each_start_and_dedupes_payloads():
    final = row("a", "1", select_fail=1, started_at="2026-10-18T09:00:00Z")
    interim = row("a", "1", ended="2026-10-18T11:00:00Z", final=False,
                  select_fail=10, started_at="2026-10-18T09:00:00Z")
    resumed = row("a", "1", ended="2026-10-18T13:00:00Z", select_fail=2,
                  started_at="2026-10-18T12:00:00Z")
    res = tr.analyze([final, interim, resumed], 7, NOW)
    assert res["users"]["a"]["sessions"] == 2
    assert res["users"]["a"]["select_fail"] == 3
