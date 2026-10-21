#!/usr/bin/env python3
"""Keep one vault file per active repo (repos/<name>.md) and the map repos/_index.md.

Lists the organizations in scripts/repo_catalog.json. Keeps only
repos that are not archived and have a push in the last 183 days.
A new repo gets a file. A repo that left the window loses its file.
The body of an existing file is never rewritten. Only the frontmatter org
and section are fixed. The output holds no push dates.
Usage: repo_catalog.py --today YYYY-MM-DD [--root vault_dir] [--check]
--check writes nothing. It exits 1 when a file or the map would change.
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

# A run may drop at most this many files. More means a short listing, not real archiving.
MAX_DROP = 10
MAP_FILE = "_index.md"
NO_PURPOSE = "Purpose not described yet."


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


def section_of(name):
    return M.get(name, ("other",))[0]


def description(repo):
    desc = " ".join((repo.get("description") or "").split()).rstrip(".")
    return desc + "." if desc else NO_PURPOSE


def render_repo(repo, org, today, purpose=None, added=None):
    """Text of a new repos/<name>.md file."""
    name = repo["name"]
    hubs = M.get(name, ("other", DEFAULT_HUB))[1].split()
    head = ["---", "id: repos/" + name, "type: repo", "tags: [domain/repos]", "org: " + org,
            "section: " + section_of(name), "added: " + (added or today.isoformat()),
            "verify_with: github", "verify_query: github:repo:%s/%s" % (org, name)]
    body = ["---", "# " + name, "", purpose or description(repo), "",
            "- GitHub: `%s/%s`" % (org, name), "- Status: active", "",
            "Related: " + ", ".join("[[%s]]" % h for h in hubs)]
    return "\n".join(head + body) + "\n"


def repo_meta(text):
    """Frontmatter org and section of a repo file."""
    return {k: v.strip() for k, v in re.findall(r"^(org|section): *(.*)$", text.split("\n---", 2)[0], re.M)}


def purpose_of(text):
    """Purpose paragraph of a repo file: the lines after the H1 up to the first blank line."""
    lines = text.split("\n")
    start = next((i for i, ln in enumerate(lines) if ln.startswith("# ")), None)
    para = []
    for ln in lines[start + 1:] if start is not None else []:
        if not ln.strip():
            if para:
                break
            continue
        para.append(ln.strip())
    return " ".join(para)


def first_sentence(text):
    return re.split(r"(?<=[.!?])\s", text, 1)[0]


def fix_frontmatter(text, org, section):
    """Set org and section in the frontmatter of an existing repo file. The body stays."""
    head, sep, rest = text.partition("\n---\n")
    for key, val in (("org", org), ("section", section)):
        head = re.sub(r"^%s:.*$" % key, "%s: %s" % (key, val), head, flags=re.M)
    return head + sep + rest


def render_map(rows):
    """rows: list of (org, section, name, first sentence of purpose)."""
    out = ["---", "id: repos/_index", "type: map", "tags: [domain/repos]", "---",
           "# Active repos", "",
           "Active means not archived and with a push in the last 6 months. Each repo has one file."]
    for org, title in ORGS.items():
        out += ["", "## " + title]
        for key, label in SECTIONS:
            found = sorted((r for r in rows if r[0] == org and r[1] == key), key=lambda r: r[2].lower())
            if found:
                out += ["", "### " + label, ""]
                out += ["- [[repos/%s]]: %s" % (name, line) for _o, _s, name, line in found]
    return "\n".join(out) + "\n"


def read_file(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def plan(root, listing, today):
    """Return (writes {path: text}, deletes [path]) that bring root/repos in line with listing.

    listing maps repo name to (org, gh repo dict).
    """
    base = os.path.join(root, "repos")
    writes, rows = {}, []
    for name, (org, repo) in sorted(listing.items()):
        path = os.path.join(base, name + ".md")
        want = section_of(name)
        if os.path.exists(path):
            text = read_file(path)
            meta = repo_meta(text)
            if meta.get("org") != org or meta.get("section") != want:
                text = fix_frontmatter(text, org, want)
                writes[path] = text
        else:
            text = writes[path] = render_repo(repo, org, today)
        rows.append((org, want, name, first_sentence(purpose_of(text))))
    index = os.path.join(base, MAP_FILE)
    text = render_map(rows)
    if not os.path.exists(index) or read_file(index) != text:
        writes[index] = text
    have = [n for n in sorted(os.listdir(base)) if n.endswith(".md") and n != MAP_FILE] if os.path.isdir(base) else []
    deletes = [os.path.join(base, n) for n in have if n[:-3] not in listing]
    return writes, deletes


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--today", required=True)
    ap.add_argument("--root", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "plugins", "knowledge", "vault"))
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    today = datetime.date.fromisoformat(args.today)
    listing = {}
    for org in ORGS:
        for r in active(pick(org, gh_list(org)), today):
            listing.setdefault(r["name"], (org, r))
    writes, deletes = plan(args.root, listing, today)
    if len(deletes) > MAX_DROP:
        print("refusing to delete %d files (limit %d)" % (len(deletes), MAX_DROP))
        return 2
    for path in list(writes) + deletes:
        print(("out of date: " if args.check else "") + path)
    if args.check:
        return 1 if (writes or deletes) else 0
    os.makedirs(os.path.join(args.root, "repos"), exist_ok=True)
    for path, text in writes.items():
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    for path in deletes:
        os.remove(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
