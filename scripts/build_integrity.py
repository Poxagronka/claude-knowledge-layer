#!/usr/bin/env python3
"""Write plugins/knowledge/integrity.json: {relative path: sha256} of every tracked plugin code file.

The vault (vault/) is excluded: it changes in many PRs and in the cron.

The installed plugin checks its own files against this list and reinstalls when one differs.
Usage: build_integrity.py [--check]   (--check exits 1 when the file is stale)
"""
import hashlib
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(REPO, "plugins", "knowledge")
OUT = os.path.join(PLUGIN, "integrity.json")


def build():
    names = subprocess.check_output(["git", "ls-files", "-co", "--exclude-standard", "-z", "--", "."], cwd=PLUGIN).decode().split("\0")
    hashes = {}
    for n in sorted(n for n in names if n and n != "integrity.json" and not n.startswith("vault/")):
        with open(os.path.join(PLUGIN, n), "rb") as f:
            hashes[n] = hashlib.sha256(f.read()).hexdigest()
    return json.dumps(hashes, indent=1, sort_keys=True) + "\n"


def main(argv):
    text = build()
    if "--check" in argv:
        try:
            with open(OUT) as f:
                fresh = f.read() == text
        except OSError:
            fresh = False
        if not fresh:
            print("integrity.json is stale. Run: python3.11 scripts/build_integrity.py")
            return 1
        return 0
    with open(OUT, "w") as f:
        f.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
