#!/usr/bin/env python3
"""Check vault records against their live sources and write a verdict report.

Reads verify_with and verify_query from every hub (frontmatter) and every entry
(inline meta fields). The grammar is in vault/_template.md. This script runs the
queries it can: github (gh CLI) and directory (urllib, one cached users fetch).
Confluence and slack queries get the verdict "manual" for the cron agent.

Usage: knowledge_verify.py [--today YYYY-MM-DD] [--root vault_dir] [--oldest N]
                           [--only ID_PREFIX] [--apply [--confirm FILE]] [--out FILE] [--env-file FILE]
The report is JSON on stdout or in --out. Exit code 0 always, 2 when the run itself failed.
--apply sets last_verified (entries) or verified (hubs) to --today on an ok verdict only.
It also merges the stamped ids into <vault>/_verify/<today>.json. The gate needs that log.
--confirm FILE (with --apply only) is a JSON object {record_id: evidence}. The cron agent checks a
"manual" record (confluence, slack) itself and lists it here. Evidence starts with "confluence:" or
"slack:". Confirmed ids are stamped like ok ids. The day log also keeps the evidence under "agent".
The directory returns a JSON list of users. Each user has string fields github,
name, title and department. github is the GitHub login. The configured department
regex selects users in your organization. The script never prints the directory key.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_knowledge_graph as ckg  # noqa: E402
from repo_catalog import WINDOW_DAYS, first_sentence, purpose_of  # noqa: E402

HTTP_TIMEOUT = 30
with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "plugins", "knowledge", "knowledge.json"), encoding="utf-8") as fh:
    CONFIG = json.load(fh)
SLACK_ID = re.compile(r"^[UW][A-Z0-9]{6,}$")
UNSAFE_PATH = re.compile(r"\.\.|[?#%\\]")
# When several queries join one record, the worst verdict wins.
ORDER = ("gone", "changed", "error", "manual", "ok")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: it would re-send the bearer key to another host."""

    def redirect_request(self, *args, **kwargs):
        return None


class Context(object):
    """Run settings plus the caches that live for one run."""

    def __init__(self, today, env):
        self.today = today
        self.env = env
        self.gh_cache = {}
        self.users = None


def load_env_file(path):
    out = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            key, sep, val = line.partition("=")
            if sep and not line.startswith("#"):
                out[key.strip()] = val.strip().strip("'\"")
    return out


def records(vault):
    """Yield one dict per hub, repo file or entry that has a verify_query other than none."""
    for path in sorted(ckg.md_files(vault)):
        rel = os.path.relpath(path, vault)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        meta = ckg.frontmatter(text) or {}
        if meta.get("type") in ("entity", "repo"):
            query = (meta.get("verify_query") or "none").strip()
            purpose = first_sentence(purpose_of(text)) if meta["type"] == "repo" else None
            yield {"id": meta.get("id", rel[:-3]), "file": rel, "kind": "hub", "query": query,
                   "seen": meta.get("verified") or "", "bid": None, "meta": meta, "purpose": purpose}
        elif meta.get("type") in ckg.ID_SHAPE:
            for _num, _title, group, bid in ckg.entries(text):
                if not bid:
                    continue
                fields = dict(ckg.FIELD.findall(" ".join(ln for _n, ln in group)))
                seen = fields.get("last_verified") or fields.get("added")
                query = (fields.get("verify_query") or "none").strip()
                yield {"id": "%s#^%s" % (meta.get("id", rel[:-3]), bid), "file": rel, "kind": "entry",
                       "query": query, "seen": seen or "", "bid": bid, "meta": {}, "purpose": None}


def select(recs, oldest, only):
    recs = [r for r in recs if r["query"] != "none"]
    if only:
        recs = [r for r in recs if r["id"].startswith(only)]
    recs.sort(key=lambda r: (r["seen"], r["id"]))
    return recs[:oldest] if oldest else recs


def gh_api(ctx, route):
    """Return ("ok", json), ("gone", None) or ("error", message) for one gh api GET."""
    if route not in ctx.gh_cache:
        try:
            out = subprocess.run(["gh", "api", route], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            ctx.gh_cache[route] = ("error", "gh failed: %s" % exc)
        else:
            blob = out.stdout + out.stderr
            if out.returncode == 0:
                try:
                    ctx.gh_cache[route] = ("ok", json.loads(out.stdout or "null"))
                except ValueError:
                    ctx.gh_cache[route] = ("error", "gh returned invalid JSON")
            elif "HTTP 404" in out.stderr:
                ctx.gh_cache[route] = ("gone", None)
            else:
                ctx.gh_cache[route] = ("error", "gh exit %d: %s" % (out.returncode, blob.strip()[:120]))
    return ctx.gh_cache[route]


def clean(text):
    return " ".join((text or "").split()).rstrip(".")


def check_repo(ctx, org, name, rec):
    state, data = gh_api(ctx, "repos/%s/%s" % (org, name))
    if state == "gone":
        return "gone", "repo %s/%s returns 404" % (org, name), None
    if state == "error":
        return "error", data, None
    if not isinstance(data, dict) or not data.get("pushed_at"):
        return "error", "repo reply has no pushed_at", None
    if data.get("archived"):
        return "gone", "repo is archived", None
    pushed = datetime.date.fromisoformat(data["pushed_at"][:10])
    if pushed < ctx.today - datetime.timedelta(days=WINDOW_DAYS):
        return "gone", "no push within %d days" % WINDOW_DAYS, None
    live, recorded = clean(data.get("description")), clean(rec.get("purpose"))
    if live and recorded and live != recorded:
        # Advisory only: curated purpose lines differ from the GitHub description on purpose.
        return "ok", "repo is live; description differs from the recorded purpose (advisory)", {"description": [recorded, live]}
    return "ok", "repo exists, not archived, pushed recently", None


def check_path(ctx, org, repo, path, label):
    state, data = gh_api(ctx, "repos/%s/%s/contents/%s" % (org, repo, path))
    if state == "ok":
        return "ok", "%s exists" % label, None
    return state, ("%s returns 404" % label) if state == "gone" else data, None


def fetch_users(ctx):
    """One GET of the configured users path per run. Return (rows, error message)."""
    if ctx.users is None:
        directory = CONFIG["directory"]
        key_env, base_env = directory["key_env"], directory["base_env"]
        key, base = ctx.env.get(key_env), (ctx.env.get(base_env) or "").rstrip("/")
        if not (key and base):
            ctx.users = (None, "%s and %s are required" % (key_env, base_env))
        elif not base.startswith("https://"):
            ctx.users = (None, "%s must start with https://" % base_env)
        else:
            req = urllib.request.Request(base + "/" + directory["users_path"].lstrip("/"), headers={"Authorization": "Bearer " + key})
            try:
                with urllib.request.build_opener(NoRedirect).open(req, timeout=HTTP_TIMEOUT) as resp:
                    rows = json.loads(resp.read().decode("utf-8"))
                if not isinstance(rows, list) or not rows:
                    ctx.users = (None, "users reply is empty or not a list")
                else:
                    ctx.users = ([u for u in rows if isinstance(u, dict)], None)
            except (OSError, ValueError, urllib.error.URLError) as exc:
                ctx.users = (None, "users fetch failed: %s" % type(exc).__name__)
    return ctx.users


def check_user(ctx, by, value, rec):
    rows, err = fetch_users(ctx)
    if rows is None:
        return "error", err, None
    norm = lambda s: clean(s).lower()  # noqa: E731
    if by == "login":
        found = [u for u in rows if norm(u.get("github")) == norm(value)]
    else:
        found = [u for u in rows if norm(u.get("name")) == norm(value)]
    if not found:
        return "gone", "user %s not found in directory" % value, None
    user = found[0]
    dept = user.get("department") or ""
    if not re.match(CONFIG["directory"]["department_regex"], dept):
        return "gone", "department %r is outside your organization" % dept, None
    diff = {}
    for field in ("title", "department"):
        old = (rec["meta"].get(field) or "").strip()
        if old and clean(old) != clean(user.get(field)):
            diff[field] = [old, user.get(field) or ""]
    if diff:
        return "changed", "title or department differs from the hub", diff
    return "ok", "user exists in your organization", None


def check_slack(ctx, uid):
    """Slack users.info for one person hub. Returns None when no token is set."""
    token = ctx.env.get("SLACK_BOT_TOKEN") or ctx.env.get("SLACK_TOKEN")
    if not token:
        return None
    if not SLACK_ID.match(uid):
        return "error", "slack id %r has a bad shape" % uid, None
    url = "https://slack.com/api/users.info?user=" + uid
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=HTTP_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if body.get("ok"):
            if (body.get("user") or {}).get("deleted"):
                return "gone", "slack account %s is deactivated" % uid, None
            return "ok", "slack account %s is active" % uid, None
        if body.get("error") == "user_not_found":
            return "gone", "slack account %s not found" % uid, None
        return "error", "slack users.info: %s" % str(body.get("error"))[:60], None
    except (OSError, ValueError, AttributeError, urllib.error.URLError) as exc:
        return "error", "slack fetch failed: %s" % type(exc).__name__, None


def run_query(ctx, query, rec):
    """Return (verdict, evidence, diff) for one query."""
    parts = query.split(":", 2)
    kind = parts[0]
    if kind == "github" and len(parts) == 3 and parts[1] in ("repo", "path"):
        tail = parts[2].split(":", 1)[-1] if parts[1] == "path" else ""
        if UNSAFE_PATH.search(parts[2]) or parts[2].startswith("/") or tail.startswith("/"):
            return "error", "query %r has an unsafe path" % query, None
    if kind in ("confluence", "slack"):
        return "manual", "check %s through the connector" % query, None
    if kind == "github" and parts[1] == "repo":
        org, name = parts[2].split("/", 1)
        return check_repo(ctx, org, name, rec)
    if kind == "github" and parts[1] == "path":
        org, rest = parts[2].split("/", 1)
        repo, path = rest.split(":", 1)
        return check_path(ctx, org, repo, path, "%s/%s:%s" % (org, repo, path))
    if kind == "directory" and parts[1] in ("user", "user-name"):
        return check_user(ctx, "login" if parts[1] == "user" else "name", parts[2], rec)
    return "error", "unsupported query", None


def verify(ctx, rec):
    results = []
    for part in ckg.split_queries(rec["query"]):
        try:
            results.append(run_query(ctx, part, rec))
        except (ValueError, IndexError):
            results.append(("error", "query %r does not fit the grammar" % part, None))
        except (AttributeError, TypeError, KeyError):
            results.append(("error", "query %r got an unexpected reply shape" % part, None))
    if rec.get("kind") == "hub" and SLACK_ID.match(str(rec["meta"].get("slack") or "")):
        slack = check_slack(ctx, rec["meta"]["slack"])
        if slack:
            results.append(slack)
    verdict = min((r[0] for r in results), key=ORDER.index)
    diff = {}
    for r in results:
        diff.update(r[2] or {})
    out = {"id": rec["id"], "query": rec["query"], "verdict": verdict,
           "evidence": "; ".join(r[1] for r in results)[:300]}
    if diff:
        out["diff"] = diff
    return out


def check_confirm(results, confirm):
    """Reject a confirmation unless its record was checked this run, got "manual", and has evidence."""
    if not isinstance(confirm, dict):
        raise ValueError("--confirm file must be a JSON object {record_id: evidence}")
    verdicts = {r["id"]: r["verdict"] for r in results}
    for rid, evidence in confirm.items():
        if rid not in verdicts:
            raise ValueError("--confirm id not selected in this run: %s" % rid)
        if verdicts[rid] != "manual":
            raise ValueError("--confirm id has verdict %s, not manual: %s" % (verdicts[rid], rid))
        if not (isinstance(evidence, str) and re.match(r"(confluence|slack):\s*\S", evidence)):
            raise ValueError("--confirm evidence must be a string starting with confluence: or slack: (%s)" % rid)


def apply_ok(vault, recs, results, today, confirm=None):
    """Stamp today on every ok or confirmed record. Returns the number stamped."""
    confirm = confirm or {}
    ok = {r["id"] for r in results if r["verdict"] == "ok"} | set(confirm)
    by_file = {}
    for rec in recs:
        if rec["id"] in ok:
            by_file.setdefault(rec["file"], []).append(rec)
    stamped, texts, done = 0, {}, set()
    for rel, items in by_file.items():
        with open(os.path.join(vault, rel), encoding="utf-8") as fh:
            lines = fh.read().split("\n")
        for rec in items:
            n = stamp_hub(lines, today) if rec["kind"] == "hub" else stamp_entry(lines, rec["bid"], today)
            if n:
                done.add(rec["id"])
            stamped += n
        texts[rel] = "\n".join(lines)
    missed = set(confirm) - done
    if missed:
        raise ValueError("--confirm id could not be stamped: %s" % ", ".join(sorted(missed)))
    # Every file rendered without error. Stage every temp file first, so a write failure leaves all originals.
    paths = [os.path.join(vault, rel) for rel in texts]
    try:
        for path, text in zip(paths, texts.values()):
            with open(path + ".tmp", "w", encoding="utf-8") as fh:
                fh.write(text)
    except BaseException:
        for path in paths:
            if os.path.exists(path + ".tmp"):
                os.remove(path + ".tmp")
        raise
    for path in paths:
        os.replace(path + ".tmp", path)
    if done:
        write_verify_log(vault, today, done, {k: v for k, v in confirm.items() if k in done})
    return stamped


def write_verify_log(vault, today, ids, agent=None):
    """Merge ids and agent evidence into <vault>/_verify/<today>.json. A same-day file is merged, not replaced."""
    path = os.path.join(vault, ckg.VERIFY_LOG, today + ".json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    old, notes = set(), {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            prev = json.load(fh)
        old, notes = set(prev.get("ok", [])), prev.get("agent", {})
    body = {"checked_at": today, "ok": sorted(old | set(ids))}
    notes.update(agent or {})
    if notes:
        body["agent"] = notes
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        fh.write(json.dumps(body, indent=1, ensure_ascii=False) + "\n")
    os.replace(path + ".tmp", path)


def stamp_hub(lines, today):
    end = lines.index("---", 1)
    for i in range(1, end):
        if lines[i].startswith("verified:"):
            lines[i] = "verified: " + today
            return 1
    lines.insert(end, "verified: " + today)
    return 1


def stamp_entry(lines, bid, today):
    tail = re.compile(r"\s\^%s\s*$" % re.escape(bid))
    fenced = False
    for i, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        elif not fenced and tail.search(line):
            if "[last_verified:: " in line:
                lines[i] = re.sub(r"\[last_verified:: [^\]]*\]", "[last_verified:: %s]" % today, line)
            else:
                lines[i] = tail.sub(" [last_verified:: %s] ^%s" % (today, bid), line)
            return 1
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--today", default=datetime.date.today().isoformat())
    ap.add_argument("--root", default=ckg.DEFAULT_VAULT)
    ap.add_argument("--oldest", type=int, default=0, help="check only the N records verified longest ago")
    ap.add_argument("--only", default="", help="check only ids that start with this prefix")
    ap.add_argument("--apply", action="store_true", help="stamp the verified date on ok records")
    ap.add_argument("--confirm", default="", help="JSON {id: evidence} of manual records the agent checked; needs --apply")
    ap.add_argument("--out", default="", help="write the report here instead of stdout")
    ap.add_argument("--env-file", default="", help="file with directory credentials named in knowledge.json")
    args = ap.parse_args(argv)
    try:
        today = datetime.date.fromisoformat(args.today)
        env = dict(os.environ)
        if args.env_file:
            env.update(load_env_file(args.env_file))
        if not os.path.isdir(args.root):
            raise OSError("vault not found: %s" % args.root)
        if args.confirm and not args.apply:
            raise ValueError("--confirm needs --apply")
        recs = select(list(records(args.root)), args.oldest, args.only)
        ctx = Context(today, env)
        results = [verify(ctx, rec) for rec in recs]
        if args.apply:
            confirm = None
            if args.confirm:
                with open(args.confirm, encoding="utf-8") as fh:
                    confirm = json.load(fh)
                check_confirm(results, confirm)
            apply_ok(args.root, recs, results, args.today, confirm)
    except (OSError, ValueError) as exc:
        print("knowledge_verify failed: %s" % exc, file=sys.stderr)
        return 2
    report = json.dumps({"checked_at": args.today, "results": results}, indent=2, ensure_ascii=False)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(report + "\n")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
