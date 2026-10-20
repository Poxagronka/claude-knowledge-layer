#!/usr/bin/env python3
"""Fleet health report from the plugin telemetry issues.

Usage: python3 scripts/fleet_health.py [--days 7] [--repo OWNER/NAME]
Needs the `gh` CLI. Prints a markdown report. Exit 1 when an alert fires.
Users without a logged-in `gh` send nothing, so the report undercounts.
"""
import argparse
import datetime
import json
from pathlib import Path
import re
import subprocess
import sys
from collections import Counter

with (Path(__file__).resolve().parent.parent / "plugins/knowledge/knowledge.json").open(encoding="utf-8") as fh:
    REPO = json.load(fh)["issues_repo"]
P95_ALERT_MS = 8000  # selector option C-B: Haiku runs in the grey zone
FENCE = re.compile(r"```json\s*(.*?)```", re.S)


def gh_json(*args):
    out = subprocess.run(["gh", "api", "--paginate", *args], capture_output=True, text=True, check=True).stdout
    # --paginate prints one JSON array per page, back to back.
    items, dec, pos = [], json.JSONDecoder(), 0
    out = out.strip()
    while pos < len(out):
        obj, pos = dec.raw_decode(out, pos)
        items.extend(obj if isinstance(obj, list) else [obj])
        while pos < len(out) and out[pos].isspace():
            pos += 1
    return items


def fetch(repo):
    """Return payloads of all telemetry comments, each tagged with `user`."""
    payloads = []
    for issue in gh_json("repos/%s/issues?labels=telemetry&state=all&per_page=100" % repo):
        user = issue.get("title", "").replace("telemetry:", "").strip() or "unknown"
        for c in gh_json("repos/%s/issues/%s/comments?per_page=100" % (repo, issue["number"])):
            p = parse(c.get("body", ""))
            if p is not None:
                p["user"] = user
                payloads.append(p)
    return payloads


def parse(body):
    """Return the dict in the first fenced json block, or None."""
    m = FENCE.search(body or "")
    if not m:
        return None
    try:
        obj = json.loads(m.group(1))
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def sessions(payloads, since):
    """One payload per session_id (a final one wins, else the latest), ended since `since` (ISO string)."""
    best = {}
    for p in payloads:
        sid = p.get("session_id")
        if not sid:
            continue
        key = (p.get("final", True), p.get("ended_at") or "")
        old = best.get(sid)
        if old is None or key > (old.get("final", True), old.get("ended_at") or ""):
            best[sid] = p
    return [p for p in best.values() if (p.get("ended_at") or "") >= since]


def percentile(values, q):
    if not values:
        return 0
    values = sorted(values)
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))]


def summarize(sess):
    users = {p["user"] for p in sess if "user" in p}
    latest = {}
    for p in sess:
        u = p.get("user")
        if u and (u not in latest or (p.get("ended_at") or "") > (latest[u].get("ended_at") or "")):
            latest[u] = p
    errors, missed, misses = Counter(), Counter(), Counter()
    ranker = Counter()
    for p in sess:
        errors.update(p.get("errors") or {})
        missed.update(p.get("missed") or {})
        misses.update(p.get("misses") or [])
        ranker.update({k: v for k, v in (p.get("ranker") or {}).items() if k != "warm"})
    ranked = sum(ranker.values())
    return {
        "users": len(users),
        "sessions": len(sess),
        "with_injection": sum(1 for p in sess if p.get("selected")),
        "idle": sum(p.get("idle_sessions_since_last", 0) for p in sess),
        "fallback_share": (ranker.get("fallback", 0) / ranked) if ranked else 0.0,
        "select_p95": percentile([(p.get("select_ms") or {}).get("p95", 0) for p in sess if p.get("select_ms")], 0.95),
        "ranker_failed": sorted(u for u, p in latest.items() if p.get("ranker_install") == "failed"),
        "errors": dict(errors),
        "worker_captures": sum(p.get("worker_captures", 0) for p in sess),
        "versions": dict(Counter(p.get("plugin_version") or "?" for p in sess)),
        "missed": missed.most_common(10),
        "misses": misses.most_common(10),
    }


def alerts(s):
    out = []
    if s["sessions"] == 0:
        out.append("No sessions in the window. Check that the plugin still sends telemetry.")
    if s["ranker_failed"]:
        out.append("ranker_install is failed for: %s." % ", ".join(s["ranker_failed"]))
    if s["errors"]:
        out.append("Swallowed plugin errors: %s." % ", ".join("%s x%d" % kv for kv in sorted(s["errors"].items())))
    if s["select_p95"] > P95_ALERT_MS:
        out.append("Selector p95 is %d ms. The limit is %d ms." % (s["select_p95"], P95_ALERT_MS))
    return out


def render(s, al):
    lines = [
        "# Fleet health",
        "",
        "- Active users: %d. Sessions: %d." % (s["users"], s["sessions"]),
        "- Sessions with an injection: %d. Idle sessions: %d." % (s["with_injection"], s["idle"]),
        "- Fallback share of ranked prompts: %.0f%%." % (100 * s["fallback_share"]),
        "- Selector p95 of session p95: %d ms." % s["select_p95"],
        "- Worker captures: %d." % s["worker_captures"],
        "- Plugin versions: %s." % (", ".join("%s (%d)" % kv for kv in sorted(s["versions"].items())) or "none"),
        "- Errors: %s." % (", ".join("%s x%d" % kv for kv in sorted(s["errors"].items())) or "none"),
        "",
        "## Top missed ids",
        "",
    ]
    lines += ["- `%s`: %d" % kv for kv in s["missed"]] or ["- none"]
    lines += ["", "## Top miss terms", ""]
    lines += ["- `%s`: %d" % kv for kv in s["misses"]] or ["- none"]
    lines += ["", "## Alerts", ""]
    lines += ["- %s" % a for a in al] or ["- none"]
    lines += ["", "Users without a logged-in `gh` send nothing. The counts are a lower bound."]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--repo", default=REPO)
    args = ap.parse_args(argv)
    if not args.repo:
        ap.error("set issues_repo in knowledge.json or pass --repo OWNER/REPO")
    since = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=args.days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    s = summarize(sessions(fetch(args.repo), since))
    al = alerts(s)
    print(render(s, al))
    return 1 if al else 0


if __name__ == "__main__":
    sys.exit(main())
