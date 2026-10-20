#!/usr/bin/env python3
"""Gate for the knowledge vault: frontmatter, links and block ids.

Checks per file type: block id prefix, lesson meta fields, the orphan rule
(every entry links at least one hub) and the verify fields (verify_with and
verify_query on every hub and every entry). Hubs and map files are exempt from the orphan rule.
A verified or last_verified date needs the record id in vault/_verify/<date>.json, which
scripts/knowledge_verify.py --apply writes. A Slack-sourced lesson has added = the newest Slack date.
Each hub kind has a size cap.

Usage: check_knowledge_graph.py [--root vault_dir]
Prints errors as file:line: message and exits 1 on any error.
"""
import argparse
import datetime
import json
import os
import re
import sys

DEFAULT_VAULT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins", "knowledge", "vault"
)
LINK = re.compile(r"\[\[([^\]\|#]+)(?:#\^([A-Za-z0-9-]+)|#[^\]\|]*)?(?:\|[^\]]*)?\]\]")
BLOCK_ID = re.compile(r"\s\^([A-Za-z0-9-]+)\s*$")
INLINE_CODE = re.compile(r"`[^`]*`")
HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
FIELD = re.compile(r"\[([A-Za-z_]+):: ([^\[\]]*)\]")
# Block id shape per file type. Types not listed here (entity, map) have no entries.
ID_SHAPE = {
    "rule": re.compile(r"R\d+$"),
    "fact": re.compile(r"K-[a-z0-9-]+$"),
    "hypothesis": re.compile(r"H-[a-z0-9-]+$"),
    "lesson": re.compile(r"L-[a-z0-9-]+$"),
    "catalog": re.compile(r"repo-[A-Za-z0-9-]+$"),
    "card": re.compile(r"page-\d+$"),
}
CARD_MAX_BYTES = 900
TABLE_NUMBER_ROW = re.compile(r"^\s*\|.*\d")
FILE_TYPES = tuple(ID_SHAPE) + ("entity", "map")
with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "plugins", "knowledge", "knowledge.json"), encoding="utf-8") as fh:
    CONFIG = json.load(fh)
HUB_MAX_BYTES = CONFIG["hub_kinds"]
HUB_KINDS = tuple(HUB_MAX_BYTES)
KIND_TAG = re.compile(r"kind/([a-z]+)")
# Contact data is banned (_template.md rule 4): emails and phone numbers.
CONTACT = re.compile(
    r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}"
    r"|(?<![\w.:/-])\+\d[\d ()-]{7,}\d"
    r"|\(?\b\d{3}\)?[ -]\d{3}[ -]\d{4}\b"
)
VERIFY_WITH = ("github", "directory", "confluence", "slack", "none")
NAME = r"[A-Za-z0-9_.-]+"
# verify_query grammar: (regex, verify_with family). The table is in _template.md.
QUERY_FORMS = (
    (re.compile(r"github:repo:(%s)/(%s)$" % (NAME, NAME)), "github"),
    (re.compile(r"github:path:(%s)/(%s):([^\s;]+)$" % (NAME, NAME)), "github"),
    (re.compile(r"directory:user:([A-Za-z0-9-]+)$"), "directory"),
    (re.compile(r"directory:user-name:(\S.*\S|\S)$"), "directory"),
    (re.compile(r"confluence:(\d+)$"), "confluence"),
    (re.compile(r"slack:([A-Z0-9]+):(\d+\.\d+)$"), "slack"),
    (re.compile(r"none$"), "none"),
)
PERSON_REQUIRED = tuple(CONFIG["person_required"])
LESSON_REQUIRED = ("added", "source", "origin", "status")
LESSON_ORIGIN = ("slack", "confluence", "agent", "issue", "jira")
LESSON_STATUS = ("active", "hypothesis", "stale")
VERIFY_LOG = "_verify"
SLACK_TS = re.compile(r"slack:(\d+)\.\d+")


def bad_date(value):
    """True when value is not a real YYYY-MM-DD date."""
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return True
    return not re.match(r"\d{4}-\d{2}-\d{2}$", value)


def split_queries(value):
    return [part.strip() for part in value.split(";")]


def query_family(query):
    """Return the verify_with family a single query belongs to, or None when it is invalid."""
    for pattern, family in QUERY_FORMS:
        if pattern.match(query):
            return family
    return None


def verify_errors(fields):
    """Error messages for the verify_with and verify_query values of one hub or entry."""
    kind = fields.get("verify_with")
    if kind is None:
        return ["verify_with is missing, use none when nothing applies"]
    if kind not in VERIFY_WITH:
        return ["verify_with must be one of %s" % "|".join(VERIFY_WITH)]
    query = fields.get("verify_query")
    if kind == "none":
        return [] if query in (None, "none") else ["verify_with none takes no verify_query"]
    if not query:
        return ["verify_query is missing for verify_with %s" % kind]
    errors = []
    for part in split_queries(query):
        family = query_family(part)
        if family is None:
            errors.append("verify_query %r does not fit the grammar" % part)
        elif family != kind:
            errors.append("verify_query %r does not match verify_with %s" % (part, kind))
    return errors


def verify_log(vault):
    """Return ({date: set(ids)}, errors) from vault/_verify/*.json."""
    logs, errors = {}, []
    base = os.path.join(vault, VERIFY_LOG)
    for name in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        rel = "%s/%s" % (VERIFY_LOG, name)
        stem = name[:-5]
        try:
            if not name.endswith(".json") or bad_date(stem):
                raise ValueError("file name must be <YYYY-MM-DD>.json")
            with open(os.path.join(base, name), encoding="utf-8") as fh:
                ids = json.load(fh)["ok"]
            if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
                raise ValueError("ok must be a list of ids")
            logs[stem] = set(ids)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append("%s:1: bad verify log: %s" % (rel, exc))
    return logs, errors


def unlogged(logs, date, rid):
    """Error text when the date has no script verdict for rid, else None."""
    if rid in logs.get(date, ()):
        return None
    return ("verified date %s has no script verdict in _verify/%s.json: "
            "run scripts/knowledge_verify.py --apply" % (date, date))


def md_files(vault):
    for base, _dirs, names in os.walk(vault):
        for name in sorted(names):
            if name.endswith(".md"):
                yield os.path.join(base, name)


def prose_lines(text, strip=True):
    """Yield (line_number, line) outside fenced blocks, with inline code removed."""
    fenced = False
    for num, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            yield num, INLINE_CODE.sub("", line) if strip else line


def unclosed_fence(text):
    """Return the line number of a ``` fence that never closes, or None."""
    opened = None
    for num, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            opened = None if opened else num
    return opened


def _bullets(body):
    """Split body lines into top-level bullets, or None if the body is not only bullets."""
    items = []
    for num, line in body:
        if not line.strip():
            continue
        if line.startswith("- "):
            items.append([(num, line)])
        elif line[0] in " \t" and items:
            items[-1].append((num, line))
        else:
            return None
    return items or None


def entries(text):
    """Yield (line, title, lines, block_id) per entry, by the leaf-section rule."""
    lines = list(prose_lines(text, strip=False))
    heads = [(i, len(m.group(1)), m.group(2)) for i, (_n, ln) in enumerate(lines) for m in [HEADING.match(ln)] if m]
    for k, (i, level, title) in enumerate(heads):
        nxt = heads[k + 1] if k + 1 < len(heads) else None
        if nxt and nxt[1] > level:
            continue
        end = nxt[0] if nxt else len(lines)
        bullets = _bullets(lines[i + 1:end])
        groups = bullets if bullets else [lines[i:end]]
        for group in groups:
            first = group[0][1]
            name = title if not bullets else first[2:].replace("**", "")
            name = LINK.sub(lambda m: m.group(1).split("/")[-1], name)
            name = BLOCK_ID.sub("", name).replace("`", "").strip()[:80]
            bid = None
            for _n, ln in group:
                match = BLOCK_ID.search(ln)
                if match:
                    bid = match.group(1)
            yield group[0][0], name, group, bid


def card_errors(bid, fields, text):
    """Error messages for one Confluence reference card (heading, meta line and body as text)."""
    errors = []
    page = re.match(r"confluence:(\d+)$", fields.get("source", ""))
    if not page or bid != "page-" + page.group(1):
        errors.append("card block id ^%s must be page-<id> and match source confluence:<id>" % bid)
    if bad_date(fields.get("updated", "x")):
        errors.append("card needs meta field updated as a date YYYY-MM-DD")
    if fields.get("verify_with") != "confluence":
        errors.append("card verify_with must be confluence")
    if len(text.encode("utf-8")) > CARD_MAX_BYTES:
        errors.append("card is over the %d byte cap" % CARD_MAX_BYTES)
    if any(TABLE_NUMBER_ROW.match(ln) for ln in text.splitlines()):
        errors.append("card holds a table row with a number, a card never copies figures")
    return errors


def _lesson_errors(rel, num, fields, errors):
    for key in LESSON_REQUIRED:
        if key not in fields:
            errors.append("%s:%d: lesson meta field %s is missing" % (rel, num, key))
    if fields.get("origin", LESSON_ORIGIN[0]) not in LESSON_ORIGIN:
        errors.append("%s:%d: lesson origin must be one of %s" % (rel, num, "|".join(LESSON_ORIGIN)))
    if fields.get("status", LESSON_STATUS[0]) not in LESSON_STATUS:
        errors.append("%s:%d: lesson status must be one of %s" % (rel, num, "|".join(LESSON_STATUS)))


def entry_checks(rel, ftype, hubs, files, logs, file_id):
    """Id shape, lesson meta and orphan errors for the entries of one file."""
    errors = []
    shape = ID_SHAPE[ftype]
    if any(BLOCK_ID.search(ln) for _n, ln in prose_lines(files[rel])) and not list(entries(files[rel])):
        errors.append("%s:1: block ids appear but there is no heading, so no entry is checked" % rel)
    for num, _title, group, bid in entries(files[rel]):
        if bid and not shape.match(bid):
            errors.append("%s:%d: block id ^%s does not fit type %s" % (rel, num, bid, ftype))
        if not bid and ftype in ("lesson", "catalog"):
            errors.append("%s:%d: entry has no block id" % (rel, num))
        fields = dict(FIELD.findall(" ".join(ln for _n, ln in group)))
        for msg in verify_errors(fields):
            errors.append("%s:%d: %s" % (rel, num, msg))
        if "added" not in fields:
            errors.append("%s:%d: meta field added is missing" % (rel, num))
        for key in ("added", "last_verified"):
            if key in fields and bad_date(fields[key]):
                errors.append("%s:%d: %s must be a date YYYY-MM-DD, got %r" % (rel, num, key, fields[key]))
        if bid and not bad_date(fields.get("last_verified", "x")):
            msg = unlogged(logs, fields["last_verified"], "%s#^%s" % (file_id, bid))
            if msg:
                errors.append("%s:%d: %s" % (rel, num, msg))
        stamps = [int(t) for t in SLACK_TS.findall(fields.get("source", ""))]
        if stamps and "added" in fields and not bad_date(fields["added"]):
            newest = datetime.datetime.fromtimestamp(max(stamps), datetime.timezone.utc).date().isoformat()
            if fields["added"] != newest:
                errors.append("%s:%d: added %s must equal the newest Slack date %s" % (rel, num, fields["added"], newest))
        if ftype == "card" and bid:
            errors.extend("%s:%d: %s" % (rel, num, m) for m in card_errors(bid, fields, "\n".join(ln for _n, ln in group)))
        if ftype == "lesson":
            _lesson_errors(rel, num, fields, errors)
        body = INLINE_CODE.sub("", "\n".join(ln for _n, ln in group))
        if not any(t.strip() in hubs for t, _b in LINK.findall(body)):
            errors.append("%s:%d: orphan entry, link at least one [[hub]]" % (rel, num))
    return errors


def frontmatter(text):
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return None
    meta = {}
    for line in lines[1:]:
        if line == "---":
            return meta
        key, sep, val = line.partition(":")
        if sep:
            meta[key.strip()] = val.strip()
    return None


def hub_checks(rel, meta, logs, size):
    """Verify fields in the frontmatter of one hub, plus the person hub fields."""
    errors = ["%s:1: %s" % (rel, msg) for msg in verify_errors(meta)]
    if "verified" in meta and bad_date(meta["verified"]):
        errors.append("%s:1: verified must be a date YYYY-MM-DD, got %r" % (rel, meta["verified"]))
    elif "verified" in meta:
        msg = unlogged(logs, meta["verified"], meta.get("id"))
        if msg:
            errors.append("%s:1: %s" % (rel, msg))
    kind = os.path.basename(rel).split("-", 1)[0]
    if size > HUB_MAX_BYTES.get(kind, size):
        errors.append("%s:1: %s hub is %d bytes, over the %d byte cap" % (rel, kind, size, HUB_MAX_BYTES[kind]))
    kinds = KIND_TAG.findall(meta.get("tags", ""))
    if kinds != [kind] or kind not in HUB_KINDS:
        errors.append("%s:1: hub needs one tag kind/<kind> equal to the file prefix (one of %s)" % (rel, "|".join(HUB_KINDS)))
    if os.path.basename(rel).startswith("person-"):
        for key in PERSON_REQUIRED:
            if not meta.get(key):
                errors.append("%s:1: person hub frontmatter field %s is missing" % (rel, key))
        if meta.get("slack") and not re.match(r"^U[A-Z0-9]+$", meta["slack"]):
            errors.append("%s:1: person hub slack must be a member ID like UEXAMPLE" % rel)
    return errors


def expected_id(rel):
    """A hub id is the file basename. Any other id is the path without .md."""
    if os.path.dirname(rel) == "entities":
        return os.path.basename(rel)[:-3]
    return rel[:-3].replace(os.sep, "/")


def _file_errors(rel, text, errors, doc_ids, hubs, block_ids):
    meta = frontmatter(text)
    if meta is None or not meta.get("id") or not meta.get("type"):
        errors.append("%s:1: frontmatter with id and type is required" % rel)
    else:
        if meta["type"] not in FILE_TYPES:
            errors.append("%s:1: type %s must be one of %s" % (rel, meta["type"], "|".join(FILE_TYPES)))
        if meta["id"] != expected_id(rel):
            errors.append("%s:1: id %s must equal %s" % (rel, meta["id"], expected_id(rel)))
        if meta["id"] in doc_ids:
            errors.append("%s:1: duplicate frontmatter id %s (also %s)" % (rel, meta["id"], doc_ids[meta["id"]]))
        else:
            doc_ids[meta["id"]] = rel
    for num, line in enumerate(text.splitlines(), 1):
        if CONTACT.search(line):
            errors.append("%s:%d: contact data (email or phone) is not allowed" % (rel, num))
    fence = unclosed_fence(text)
    if fence:
        errors.append("%s:%d: unclosed code fence" % (rel, fence))
    ids = {}
    for num, line in prose_lines(text):
        match = BLOCK_ID.search(line)
        if match:
            bid = match.group(1)
            if bid in ids:
                errors.append("%s:%d: duplicate block id ^%s (first at line %d)" % (rel, num, bid, ids[bid]))
            else:
                ids[bid] = num
    block_ids[rel] = ids
    if rel.startswith("entities" + os.sep):
        base = os.path.basename(rel)[:-3]
        if base in hubs:
            errors.append("%s:1: duplicate hub basename %s (also %s)" % (rel, base, hubs[base]))
        hubs[base] = rel


def _link_errors(files, hubs, block_ids, errors):
    graveyard = files.get("_graveyard.md", "")
    for rel, text in files.items():
        for num, line in prose_lines(text):
            for target, bid in LINK.findall(line):
                target = target.strip()
                dest = target + ".md"
                if dest not in files:
                    dest = hubs.get(target)
                if dest is None:
                    errors.append("%s:%d: broken link [[%s]]" % (rel, num, target))
                elif bid and bid not in block_ids[dest] and not re.search(r"\^%s(?![A-Za-z0-9-])" % re.escape(bid), graveyard):
                    errors.append("%s:%d: block id ^%s not found in %s" % (rel, num, bid, dest))


def check(vault):
    logs, errors = verify_log(vault)
    block_ids = {}
    hubs = {}
    files = {}
    doc_ids = {}
    for path in md_files(vault):
        rel = os.path.relpath(path, vault)
        with open(path, encoding="utf-8") as fh:
            files[rel] = fh.read()
    for rel, text in files.items():
        _file_errors(rel, text, errors, doc_ids, hubs, block_ids)
    for rel, text in files.items():
        meta = frontmatter(text)
        if meta and meta.get("type") in ID_SHAPE:
            errors.extend(entry_checks(rel, meta["type"], hubs, files, logs, meta.get("id")))
        elif meta and rel.startswith("entities" + os.sep):
            errors.extend(hub_checks(rel, meta, logs, len(text.encode("utf-8"))))
    _link_errors(files, hubs, block_ids, errors)
    return errors


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=DEFAULT_VAULT, help="vault directory to check")
    errors = check(parser.parse_args(argv).root)
    for err in errors:
        print(err)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
