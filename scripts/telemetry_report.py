#!/usr/bin/env python3
"""Weekly plugin-health report from the plugin telemetry comments.

Usage: python3 scripts/telemetry_report.py [--days 7] [--now ISO] [--input FILE] [--json FILE] [--repo OWNER/REPO]
--input is a JSON list of {"user": login, "body": comment body}. Without it the script
fetches the comments with the `gh` CLI. Stdout is a short Markdown report. --json FILE
writes the machine result, including the anomalies with their stable keys.
"""
import argparse
import datetime as dt
import json
import re
import statistics
import subprocess
import sys
from collections import Counter
from pathlib import Path

with (Path(__file__).resolve().parent.parent / "plugins/knowledge/knowledge.json").open(encoding="utf-8") as fh:
    REPO = json.load(fh)["issues_repo"]
FAIL_MIN = 3  # select-fail-rate: minimum failures
FAIL_RATE = 0.05  # select-fail-rate: failures per prompt
P95_MEDIAN_MS = 8000  # select-latency: median of session p95
STALE_HOURS = 24  # stale-version: age of the newest version at the user's last session
FENCE = re.compile(r"```json\s*(.*?)```", re.S)
TS = "%Y-%m-%dT%H:%M:%SZ"


def ts(s):
    return dt.datetime.strptime(s, TS).replace(tzinfo=dt.timezone.utc)


def gh(*args):
    out = subprocess.run(["gh", *args], capture_output=True, text=True, check=True).stdout.strip()
    items, dec, pos = [], json.JSONDecoder(), 0
    while pos < len(out):  # --paginate prints one JSON array per page
        obj, pos = dec.raw_decode(out, pos)
        items.extend(obj if isinstance(obj, list) else [obj])
        while pos < len(out) and out[pos].isspace():
            pos += 1
    return items


def fetch(repo):
    rows = []
    for i in gh("issue", "list", "--repo", repo, "--label", "telemetry", "--state", "all", "--json", "number,title", "--limit", "100"):
        user = i["title"].replace("telemetry:", "", 1).strip() or "unknown"
        for c in gh("api", "repos/%s/issues/%s/comments" % (repo, i["number"]), "--paginate"):
            rows.append({"user": user, "body": c.get("body", "")})
    return rows


def load(rows):
    """Parse all json blocks, dedupe by (session_id, started_at). Return (records, bad_blocks)."""
    best, bad = {}, 0
    for row in rows:
        for block in FENCE.findall(row.get("body", "")):
            try:
                r = json.loads(block)
                r["_user"], r["_end"] = row["user"], ts(r["ended_at"])
                r["_start"] = ts(r["started_at"]) if r.get("started_at") else r["_end"]
                sid = (r["session_id"], r.get("started_at"))
            except (ValueError, KeyError, TypeError, AttributeError):
                bad += 1
                continue
            old = best.get(sid)
            if old is None or (r.get("final", True), r["_end"]) > (old.get("final", True), old["_end"]):
                best[sid] = r
    return list(best.values()), bad


def summarize(records):
    """Per-user stats over the records of one window."""
    users = {}
    for r in sorted(records, key=lambda r: r["_end"]):
        s = users.setdefault(r["_user"], {"sessions": 0, "prompts": 0, "select_fail": 0, "select_timeout": 0,
                                          "select_none": 0, "injected": 0, "idle_sessions": 0,
                                          "errors": Counter(), "p95s": [], "p95_max": 0, "version": None,
                                          "last_end": None})
        sm = r.get("select_ms") or {}
        s["sessions"] += 1
        s["prompts"] += sm.get("n", 0)
        for k in ("select_fail", "select_timeout", "select_none"):
            s[k] += r.get(k, 0)
        s["injected"] += len(r.get("injected") or [])
        s["idle_sessions"] += r.get("idle_sessions_since_last", 0)
        s["errors"].update(r.get("errors") or {})
        if sm.get("n", 0) > 0:
            s["p95s"].append(sm.get("p95", 0))
            s["p95_max"] = max(s["p95_max"], sm.get("p95", 0))
        s["version"], s["last_end"] = r.get("plugin_version"), r["_end"]
    return users


def anomalies(cur, prev, all_records, window_records):
    out = []
    for u, s in sorted(cur.items()):
        denom = s["prompts"] or (s["select_fail"] + s["select_none"])
        if s["select_fail"] >= FAIL_MIN and denom and s["select_fail"] / denom > FAIL_RATE:
            ek = ", ".join("%s=%d" % kv for kv in sorted(s["errors"].items())) or "none"
            out.append({"key": "select-fail-rate:" + u, "title": "Selector failures for %s" % u,
                        "detail": "select_fail=%d, select_timeout=%d, select_none=%d, prompts=%d. Errors: %s."
                        % (s["select_fail"], s["select_timeout"], s["select_none"], s["prompts"], ek)})
        if s["p95s"] and statistics.median(s["p95s"]) > P95_MEDIAN_MS:
            out.append({"key": "select-latency:" + u, "title": "Slow selector for %s" % u,
                        "detail": "Median session p95 select_ms is %d (limit %d) over %d sessions."
                        % (statistics.median(s["p95s"]), P95_MEDIAN_MS, len(s["p95s"]))})
    seen_prev = {k for s in prev.values() for k in s["errors"]}
    errs = {}
    for u, s in cur.items():
        for k, n in s["errors"].items():
            if k not in seen_prev:
                e = errs.setdefault(k, {"n": 0, "users": []})
                e["n"] += n
                e["users"].append(u)
    for k, e in sorted(errs.items()):
        out.append({"key": "error:" + k, "title": "New plugin error %s" % k,
                    "detail": "Seen %d times for users: %s. Absent in the previous window." % (e["n"], ", ".join(sorted(e["users"])))})
    if window_records:
        newest = max(window_records, key=lambda r: r["_end"]).get("plugin_version")
        def first_seen(v):
            return min((r["_start"] for r in all_records if r.get("plugin_version") == v), default=None)

        first = first_seen(newest)
        for u, s in sorted(cur.items()):
            older = first and s["version"] != newest and first_seen(s["version"]) < first
            if older and s["last_end"] - first >= dt.timedelta(hours=STALE_HOURS):
                out.append({"key": "stale-version:" + u, "title": "Stale plugin version for %s" % u,
                            "detail": "Last session %s used %s. Newest version %s first seen %s."
                            % (s["last_end"].strftime(TS), s["version"], newest, first.strftime(TS))})
    return out


def totals(users):
    t = Counter()
    for s in users.values():
        for k in ("prompts", "select_fail", "injected", "sessions"):
            t[k] += s[k]
    t["fail_rate"] = t["select_fail"] / t["prompts"] if t["prompts"] else 0
    t["injected_per_prompt"] = t["injected"] / t["prompts"] if t["prompts"] else 0
    return dict(t)


def analyze(rows, days=7, now=None):
    records, bad = load(rows)
    now = now or dt.datetime.now(dt.timezone.utc)
    span = dt.timedelta(days=days)

    def inwin(lo, hi):
        return [r for r in records if lo <= r["_end"] < hi]

    wcur, wprev = inwin(now - span, now), inwin(now - 2 * span, now - span)
    cur, prev = summarize(wcur), summarize(wprev)
    return {"window_end": now.strftime(TS), "days": days, "bad_blocks": bad, "totals": totals(cur),
            "previous_totals": totals(prev), "anomalies": anomalies(cur, prev, records, wcur),
            "users": {u: {**s, "errors": dict(s["errors"]), "last_end": s["last_end"].strftime(TS)}
                      for u, s in cur.items()}}


def render(res):
    t, p = res["totals"], res["previous_totals"]
    lines = ["# Plugin health, %d days to %s" % (res["days"], res["window_end"]), "",
             "| Metric | This window | Previous |", "|---|---|---|"]
    for name, k, f in (("Sessions", "sessions", "%d"), ("Prompts", "prompts", "%d"),
                       ("select_fail rate", "fail_rate", "%.3f"), ("Injected per prompt", "injected_per_prompt", "%.2f")):
        lines.append("| %s | %s | %s |" % (name, f % t.get(k, 0), f % p.get(k, 0)))
    lines += ["", "| User | Sessions | Prompts | fail | timeout | none | injected | idle | worst p95 ms | Version |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for u, s in sorted(res["users"].items()):
        lines.append("| %s | %d | %d | %d | %d | %d | %d | %d | %d | %s |" % (
            u, s["sessions"], s["prompts"], s["select_fail"], s["select_timeout"], s["select_none"],
            s["injected"], s["idle_sessions"], s["p95_max"], (s["version"] or "?")[:8]))
    lines += ["", "Bad json blocks: %d" % res["bad_blocks"], "", "## Anomalies"]
    lines += ["- `%s`: %s" % (a["key"], a["detail"]) for a in res["anomalies"]] or ["None."]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--now", help="window end, ISO UTC (default: current time)")
    ap.add_argument("--input", help="JSON list of {user, body}; skips gh")
    ap.add_argument("--json", help="write the machine result to this file")
    ap.add_argument("--repo", default=REPO, help="telemetry issues repo (default: issues_repo of knowledge.json)")
    a = ap.parse_args()
    if a.input:
        with open(a.input) as f:
            rows = json.load(f)
    else:
        if not a.repo:
            ap.error("set issues_repo in knowledge.json or pass --repo OWNER/REPO")
        rows = fetch(a.repo)
    now = None
    if a.now:
        now = dt.datetime.fromisoformat(a.now.replace("Z", "+00:00"))
        now = now if now.tzinfo else now.replace(tzinfo=dt.timezone.utc)
    res = analyze(rows, a.days, now)
    if a.json:
        with open(a.json, "w") as f:
            json.dump(res, f, indent=2)
    print(render(res))


if __name__ == "__main__":
    sys.exit(main())
