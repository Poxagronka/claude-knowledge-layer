#!/usr/bin/env python3
"""Set organization and repository values in a copied knowledge template."""

import argparse
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org", required=True)
    parser.add_argument("--issues-repo", required=True)
    parser.add_argument("--plugin-repo")
    parser.add_argument("--marketplace")
    parser.add_argument("--maintainer")
    parser.add_argument("--root-hub")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config_path = ROOT / "plugins/knowledge/knowledge.json"
    marketplace_path = ROOT / ".claude-plugin/marketplace.json"
    owners_path = ROOT / ".github/CODEOWNERS"
    originals = {path: path.read_text() for path in (
        config_path, marketplace_path, owners_path,
    )}
    config = json.loads(originals[config_path])
    config.update(org=args.org, issues_repo=args.issues_repo)
    if args.marketplace is not None:
        config["marketplace"] = args.marketplace
    if args.root_hub is not None:
        config["root_hub"] = args.root_hub

    marketplace = json.loads(originals[marketplace_path])
    marketplace["name"] = config["marketplace"]
    plugin_repo = args.plugin_repo or args.issues_repo
    for plugin in marketplace["plugins"]:
        if plugin["name"] == "knowledge":
            plugin["source"]["url"] = f"https://github.com/{plugin_repo}.git"

    owners = originals[owners_path]
    if args.maintainer is not None:
        lines = []
        for line in owners.splitlines(keepends=True):
            body, marker, comment = line.partition("#")
            match = re.match(r"^(\s*\S+)([ \t]+)(.*)$", body, re.DOTALL)
            if match:
                logins = re.sub(
                    r"(?<!\S)@[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?",
                    lambda _: f"@{args.maintainer}", match[3],
                )
                body = match[1] + match[2] + logins
            lines.append(body + marker + comment)
        owners = "".join(lines)

    updates = {
        config_path: json.dumps(config, indent=2) + "\n",
        marketplace_path: json.dumps(marketplace, indent=2) + "\n",
        owners_path: owners,
    }
    for path, content in updates.items():
        if content == originals[path]:
            continue
        print(f"{'Would update' if args.dry_run else 'Updated'} {path.relative_to(ROOT)}")
        if not args.dry_run:
            path.write_text(content)
    print("Next steps:")
    print("1. Fill the example vault with your organization's knowledge.")
    print("2. Review knowledge.json scope and optional directory settings.")
    print("3. Review CODEOWNERS and repository workflow settings.")
    print("4. Run the tests and publish your plugin repository.")


if __name__ == "__main__":
    main()
