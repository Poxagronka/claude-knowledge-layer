#!/usr/bin/env python3
"""Write the repo catalog of the knowledge vault (repos/*.md) from the gh CLI.

Lists the organizations in scripts/repo_catalog.json. Keeps only
repos that are not archived and have a push in the last 183 days.
A hand-written purpose line in the existing file always wins over the
GitHub description. The output holds no push dates.
Usage: repo_catalog.py --today YYYY-MM-DD [--root vault_dir] [--check]
--check writes nothing. It exits 1 when a file would change.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys

WINDOW_DAYS = 183
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "repo_catalog.json"), encoding="utf-8") as fh:
    CONFIG = json.load(fh)
NAME_RE = re.compile(CONFIG["name_filter"], re.I)
SKIP = set(CONFIG["skip"])
ORGS = CONFIG["orgs"]
DEFAULT_HUB = CONFIG["default_hub"]
M = {name: (value["type"], " ".join(value["hubs"])) for name, value in CONFIG["repos"].items()}
SECTIONS = [
    ("product", "Products"), ("tool", "Tools"), ("package", "Packages"),
    ("service", "Services and bots"), ("qa", "QA and CI"),
    ("data", "Data and models"), ("other", "Other"),
]

PURPOSE_RE = re.compile(r"^- `([^`]+)`: (.*?)(?: See \[\[.*)?$")
ADDED_RE = re.compile(r"\[added:: (\d{4}-\d{2}-\d{2})\]")
VERIFIED_RE = re.compile(r"\[last_verified:: (\d{4}-\d{2}-\d{2})\]")


# A run may drop at most this many entries. More means a short listing, not real archiving.
MAX_DROP = 10


def gh_list(org):
    try:
        out = subprocess.run(
            ["gh", "repo", "list", org, "--limit", "1000", "--json",
             "name,description,isArchived,pushedAt"],
            check=True, capture_output=True, text=True).stdout
        return json.loads(out)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise SystemExit("gh repo list %s failed: %s" % (org, exc))


def active(repos, today):
    """Repos that are not archived and pushed within the window."""
    cutoff = today - datetime.timedelta(days=WINDOW_DAYS)
    keep = []
    for r in repos:
        if r.get("isArchived") or not r.get("pushedAt"):
            continue
        pushed = datetime.date.fromisoformat(r["pushedAt"][:10])
        if pushed >= cutoff:
            keep.append(r)
    return sorted(keep, key=lambda r: r["name"].lower())


def pick(org, repos):
    return [r for r in repos if NAME_RE.search(r["name"]) and r["name"] not in SKIP]


def read_existing(path):
    """Map repo name to (hand-written purpose, added date, last_verified date)."""
    old = {}
    if not os.path.exists(path):
        return old
    lines = open(path, encoding="utf-8").read().split("\n")
    for i, line in enumerate(lines):
        m = PURPOSE_RE.match(line)
        if not m:
            continue
        meta = lines[i + 1] if i + 1 < len(lines) else ""
        added, seen = ADDED_RE.search(meta), VERIFIED_RE.search(meta)
        old[m.group(1)] = (m.group(2), added.group(1) if added else None,
                           seen.group(1) if seen else None)
    return old


def purpose_for(repo, old):
    if repo["name"] in old and old[repo["name"]][0]:
        return old[repo["name"]][0]
    desc = " ".join((repo.get("description") or "").split()).rstrip(".")
    return (desc or "Repository. Purpose not described yet") + "."


def entry(repo, old, today, org, bid):
    name = repo["name"]
    hubs = M.get(name, ("other", DEFAULT_HUB))[1].split()
    purpose = purpose_for(repo, old)
    added = old.get(name, (None, None, None))[1] or today.isoformat()
    seen = old.get(name, (None, None, None))[2]
    links = ", ".join("[[%s]]" % h for h in hubs)
    return ["- `%s`: %s See %s." % (name, purpose, links),
            "  [added:: %s] [verify_with:: github] [verify_query:: github:repo:%s/%s]%s ^%s"
            % (added, org, name, " [last_verified:: %s]" % seen if seen else "", bid)]


def block_ids(repos):
    """Unique block id per repo. A name that collides gets a numeric suffix, in name order."""
    ids, used = {}, set()
    for r in sorted(repos, key=lambda r: r["name"]):
        base = bid = "repo-" + re.sub(r"[^A-Za-z0-9-]", "-", r["name"])
        n = 1
        while bid in used:
            n += 1
            bid = "%s-%d" % (base, n)
        used.add(bid)
        ids[r["name"]] = bid
    return ids


def render(org, repos, old, today):
    title = ORGS[org]
    out = ["---", "id: repos/%s" % org, "type: catalog", "tags: [domain/repos]", "---",
           "# Active repos: %s" % title, "",
           "Active means not archived and with a push in the last 6 months.",
           "Repos outside that window are left out.", ""]
    ids = block_ids(repos)
    for key, label in SECTIONS:
        rows = [r for r in repos if M.get(r["name"], ("other",))[0] == key]
        if not rows:
            continue
        out += ["## " + label, ""]
        for r in rows:
            out += entry(r, old, today, org, ids[r["name"]])
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


def build(org, repos, root, today):
    path = os.path.join(root, "repos", org + ".md")
    return path, render(org, active(pick(org, repos), today), read_existing(path), today)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--today", required=True)
    ap.add_argument("--root", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "plugins", "knowledge", "vault"))
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    today = datetime.date.fromisoformat(args.today)
    stale = False
    pending = []
    for org in ORGS:
        path, text = build(org, gh_list(org), args.root, today)
        dropped = set(read_existing(path)) - set(re.findall(r"^- `([^`]+)`:", text, re.M))
        if len(dropped) > MAX_DROP:
            print("refusing to drop %d entries from %s (limit %d)" % (len(dropped), path, MAX_DROP))
            return 2
        current = open(path, encoding="utf-8").read() if os.path.exists(path) else None
        if current == text:
            continue
        stale = True
        if args.check:
            print("out of date: " + path)
        else:
            pending.append((path, text))
    for path, text in pending:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w", encoding="utf-8").write(text)
        print("wrote " + path)
    return 1 if (stale and args.check) else 0


if __name__ == "__main__":
    sys.exit(main())
