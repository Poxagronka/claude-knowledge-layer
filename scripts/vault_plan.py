#!/usr/bin/env python3.11
"""Trusted writer for vault changes. An LLM proposes a plan JSON. This script checks and applies it.

Plan: {"job": "distil|cards", "ops": [{"op": "add|replace|upsert_card", "file": "<vault path>",
"block_id": "...", "text": "<heading, meta line and body>", "closes": [<issue>]}],
"rejects": [{"issue": <n>, "reason": "privacy|off-scope|duplicate|unverified"}]}

validate() is pure. apply() inserts or replaces one entry per op by block id. A second apply of the
same plan changes nothing. The caller runs scripts/check_knowledge_graph.py after apply.

Usage: vault_plan.py validate|apply PLAN.json [--vault DIR] [--issues 1,2,3]
"""
import argparse
import importlib.util
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_VAULT = os.path.join(ROOT, "plugins", "knowledge", "vault")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ckg = _load("check_knowledge_graph", os.path.join(HERE, "check_knowledge_graph.py"))
privacy = _load("knowledge_privacy", os.path.join(ROOT, "plugins", "knowledge", "scripts", "privacy.py"))

OPS = ("add", "replace", "upsert_card")
JOBS = ("distil", "cards")
REJECT_REASONS = ("privacy", "off-scope", "duplicate", "unverified")
MAX_OPS = 15
MAX_ENTRY_BYTES = 1200
DOMAIN_FILES = {"rules.md": "rule", "knowledge.md": "fact", "hypotheses.md": "hypothesis"}
SPACE_FILE = re.compile(r"confluence/[A-Za-z0-9]+\.md$")
LESSON_FILE = re.compile(r"lessons/[a-z0-9-]+\.md$")
DOMAIN_FILE = re.compile(r"[a-z0-9-]+/(rules|knowledge|hypotheses)\.md$")
VERIFY_FIELD = re.compile(r"\[(?:last_)?verified::")
TRAILING_ID = re.compile(r"\s\^([A-Za-z0-9-]+)\s*$")


def file_type(rel):
    """Return the vault file type a plan may write at rel, or None."""
    if rel.startswith(("/", ".")) or ".." in rel.split("/") or "\\" in rel:
        return None
    if LESSON_FILE.match(rel) and not rel.endswith("_index.md"):
        return "lesson"
    if DOMAIN_FILE.match(rel):
        return DOMAIN_FILES[os.path.basename(rel)]
    if SPACE_FILE.match(rel):
        return "card"
    return None


def _entry_errors(i, op, ftype):
    text, bid = op.get("text"), op.get("block_id")
    where = "op %d" % i
    if not isinstance(text, str) or not isinstance(bid, str):
        return ["%s: text and block_id must be strings" % where]
    errors = []
    shape = ckg.ID_SHAPE[ftype]
    if not shape.match(bid):
        errors.append("%s: block id ^%s does not fit type %s" % (where, bid, ftype))
    lines = text.strip("\n").splitlines()
    heads = [ln for ln in lines if ckg.HEADING.match(ln)]
    if len(heads) != 1 or not lines or not ckg.HEADING.match(lines[0]):
        errors.append("%s: text must be one entry that starts with one heading" % where)
    ids = [m.group(1) for ln in lines for m in [TRAILING_ID.search(ln)] if m]
    if ids != [bid]:
        errors.append("%s: text must carry exactly one block id and it must equal block_id" % where)
    limit = ckg.CARD_MAX_BYTES if ftype == "card" else MAX_ENTRY_BYTES
    if len(text.encode("utf-8")) > limit:
        errors.append("%s: entry is over the %d byte cap" % (where, limit))
    if privacy.SENSITIVE.search(text):
        errors.append("%s: text matches the sensitive-data pattern" % where)
    if VERIFY_FIELD.search(text):
        errors.append("%s: only knowledge_verify writes verified or last_verified" % where)
    if ftype == "card" and ids == [bid]:
        fields = dict(ckg.FIELD.findall(" ".join(lines)))
        errors.extend("%s: %s" % (where, m) for m in ckg.card_errors(bid, fields, text))
    return errors


def _find(lines, bid):
    """Return (start, end) of the section whose line ends with ^bid, or None."""
    for n, ln in enumerate(lines):
        m = TRAILING_ID.search(ln)
        if m and m.group(1) == bid:
            start = n
            while start >= 0 and not ckg.HEADING.match(lines[start]):
                start -= 1
            end = n + 1
            while end < len(lines) and not ckg.HEADING.match(lines[end]):
                end += 1
            return max(start, 0), end
    return None


def validate(plan, vault, issues=None):
    """Return a list of error strings. An empty list means the plan may be applied."""
    if not isinstance(plan, dict):
        return ["plan is not an object"]
    errors = []
    if plan.get("job") not in JOBS:
        errors.append("job must be one of %s" % "|".join(JOBS))
    ops = plan.get("ops")
    if not isinstance(ops, list):
        return errors + ["ops must be a list"]
    if len(ops) > MAX_OPS:
        errors.append("plan has %d ops, the cap is %d" % (len(ops), MAX_OPS))
    for rej in plan.get("rejects") or []:
        if not (isinstance(rej, dict) and isinstance(rej.get("issue"), int) and rej.get("reason") in REJECT_REASONS):
            errors.append("reject needs an integer issue and a reason in %s" % "|".join(REJECT_REASONS))
        elif issues is not None and rej["issue"] not in issues:
            errors.append("reject names issue %s, which is not an input" % rej["issue"])
    for i, op in enumerate(ops):
        if not isinstance(op, dict) or op.get("op") not in OPS:
            errors.append("op %d: op must be one of %s" % (i, "|".join(OPS)))
            continue
        rel = op.get("file")
        ftype = file_type(rel) if isinstance(rel, str) else None
        if ftype is None:
            errors.append("op %d: file %r is not lessons/*.md, <domain>/{rules,knowledge,hypotheses}.md or confluence/<SPACE>.md" % (i, rel))
            continue
        if (op["op"] == "upsert_card") != (ftype == "card"):
            errors.append("op %d: upsert_card and confluence/<SPACE>.md go together" % i)
        errors.extend(_entry_errors(i, op, ftype))
        closes = op.get("closes", [])
        if not isinstance(closes, list) or not all(isinstance(c, int) for c in closes):
            errors.append("op %d: closes must be a list of issue numbers" % i)
        elif issues is not None and not set(closes) <= set(issues):
            errors.append("op %d: closes names an issue that is not an input" % i)
        path = os.path.join(vault, rel)
        if not os.path.isfile(path):
            errors.append("op %d: file %s does not exist (a plan never creates a vault file)" % (i, rel))
            continue
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        lines = text.splitlines()
        found = _find(lines, op.get("block_id", ""))
        if found and ckg._bullets(list(enumerate(lines[found[0] + 1:found[1]]))):
            errors.append("op %d: ^%s is in a bullet section, one entry per bullet, a plan cannot write it" % (i, op.get("block_id")))
            continue
        if op["op"] == "replace" and not found:
            errors.append("op %d: replace needs an existing ^%s" % (i, op.get("block_id")))
        if op["op"] == "add" and found:
            errors.append("op %d: add finds ^%s already in %s, use replace" % (i, op.get("block_id"), rel))
    return errors


def apply(plan, vault):
    """Write the ops into the vault files. Return the sorted list of changed relative paths."""
    changed = set()
    for op in plan["ops"]:
        path = os.path.join(vault, op["file"])
        with open(path, encoding="utf-8") as fh:
            old = fh.read()
        lines = old.rstrip("\n").splitlines()
        entry = op["text"].strip("\n").splitlines()
        found = _find(lines, op["block_id"])
        if found:
            lines[found[0]:found[1]] = entry + [""] if found[1] < len(lines) else entry
        else:
            lines += [""] + entry
        new = "\n".join(lines) + "\n"
        if new != old:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new)
            changed.add(op["file"])
    return sorted(changed)


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "apply"))
    parser.add_argument("plan")
    parser.add_argument("--vault", default=DEFAULT_VAULT)
    parser.add_argument("--issues", help="comma list of input issue numbers")
    args = parser.parse_args(argv)
    with open(args.plan, encoding="utf-8") as fh:
        plan = json.load(fh)
    issues = [int(x) for x in args.issues.split(",") if x] if args.issues is not None else None
    errors = validate(plan, args.vault, issues)
    for msg in errors:
        print(msg, file=sys.stderr)
    if errors:
        return 1
    if args.command == "apply":
        for rel in apply(plan, args.vault):
            print(rel)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
