#!/usr/bin/env python3
"""Map the files changed in a PR to CI units and print a GitHub matrix.

A unit is a tool folder that holds a Makefile with a `check` target.
Output on stdout: {"include":[{"unit":"x","shard":1,"shards":2}, ...]}.
Contract errors go to stderr and the exit code is 1.
Stdlib only, Python 3.9+.
"""
import argparse
import json
import os
import re
import subprocess
import sys

# Top-level paths that belong to the repo itself, never to a tool.
# Any top-level folder that starts with a dot is reserved too.
RESERVED = ("plugins", "scripts", "tests", "docs", "evals")
MAX_SHARDS = 16
MAX_MATRIX = 256  # GitHub rejects a matrix with more jobs than this
CONTRACT_HINT = "no Makefile with a 'check' target; see README \"Tool contract\""


def changed_files(base, head, cwd):
    out = subprocess.run(
        ["git", "diff", "-z", "--name-only", "%s...%s" % (base, head)],
        cwd=cwd, check=True, capture_output=True,
    ).stdout.decode("utf-8")
    return [name for name in out.split("\0") if name]


def find_unit(root, rel_file):
    """Nearest ancestor folder with a Makefile, never above the top-level folder."""
    top = rel_file.split("/", 1)[0]
    folder = os.path.dirname(rel_file)
    while True:
        if os.path.isfile(os.path.join(root, folder, "Makefile")):
            return folder
        if folder == top:
            return None
        folder = os.path.dirname(folder)


def read_shards(makefile):
    m = re.search(r"^CI_SHARDS\s*[:?]?=\s*(\d+)", makefile, re.M)
    return max(1, min(MAX_SHARDS, int(m.group(1)))) if m else 1


def plan(files, root):
    units, errors, loose = set(), [], {}
    for f in files:
        parts = f.split("/")
        # Root-level files have one part. They are repo files, not tools.
        if len(parts) < 2 or parts[0] in RESERVED or parts[0].startswith("."):
            continue
        # A deleted top-level folder has nothing left to check.
        if not os.path.isdir(os.path.join(root, parts[0])):
            continue
        unit = find_unit(root, f)
        if unit:
            units.add(unit)
        else:
            loose.setdefault(parts[0], []).append(f)
    for top, fs in sorted(loose.items()):
        # Markdown-only folders (skills, docs) need no build.
        if not all(f.endswith(".md") for f in fs):
            errors.append("%s: %s" % (top, CONTRACT_HINT))
    include = []
    for unit in sorted(units):
        with open(os.path.join(root, unit, "Makefile")) as fh:
            text = fh.read()
        if not re.search(r"^check\s*:", text, re.M):
            errors.append("%s: %s" % (unit, CONTRACT_HINT))
            continue
        n = read_shards(text)
        include += [{"unit": unit, "shard": i, "shards": n} for i in range(1, n + 1)]
    if len(include) > MAX_MATRIX:
        errors.append("matrix has %d jobs, more than the limit of %d" % (len(include), MAX_MATRIX))
    return {"include": include}, errors


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--root", default=".")
    args = ap.parse_args(argv)
    matrix, errors = plan(changed_files(args.base, args.head, args.root), args.root)
    print(json.dumps(matrix, separators=(",", ":")))
    for e in errors:
        print("error: " + e, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
