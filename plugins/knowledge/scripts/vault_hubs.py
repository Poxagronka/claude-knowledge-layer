"""Vault note parsing shared by the selector and the tool-call hook: hub records, ids, the entry fence.

Stdlib only, Python 3.9. Lives apart from the two entry points so hook.py and knowledge_select.py both import it.
"""
import re

SELECT_OPT_OUT_ENV = "KNOWLEDGE_SELECT"
PREVIEW = 200
# Store and bundle ids a hub names. Only these shapes count: a bare long number elsewhere may be a wiki page id.
TOP_APP_RE = re.compile(r"(\d{6,12}) / ([A-Za-z]\w*(?:\.\w+)+)")
APP_STORE_RE = re.compile(r"App Store id (\d{6,12})")
BUNDLE_RE = re.compile(r"\b[a-z][a-z0-9_]*(?:\.[a-z0-9_]+){2,}\b")
URL_RE = re.compile(r"https?://\S+")
NOT_BUNDLE = (".com", ".org", ".net", ".io", ".md", ".json", ".py")


def whole_word(term, text):
    return re.search(r"(?<!\w)%s(?!\w)" % re.escape(term.lower()), text) is not None


def clean(text):
    return " ".join(re.sub(r"[#*`>]|^\s*-\s", " ", text).split())


def split_frontmatter(text):
    """Return (frontmatter lines, body) of a note."""
    if text.startswith("---\n") and "\n---" in text[4:]:
        head, _, body = text[4:].partition("\n---")
        return head.splitlines(), body.lstrip("-").lstrip("\n")
    return [], text


def record(ident, title, text):
    body = text.split("\n", 1)[1] if "\n" in text else ""
    return {
        "id": ident, "title": title, "text": text,
        "preview": clean(body)[:PREVIEW],
    }


def hub_ids(body):
    """Store ids and bundle ids named in a hub body, in order of appearance."""
    found = []
    for line in body.splitlines():
        if line.startswith("- Top apps:"):
            for num, bundle in TOP_APP_RE.findall(line):
                found += [num, bundle]
        found += APP_STORE_RE.findall(line)
        low = line.lower()
        if "package" in low or "bundle" in low:
            found += [t for t in BUNDLE_RE.findall(URL_RE.sub(" ", line)) if not t.endswith(NOT_BUNDLE)]
    return list(dict.fromkeys(found))


def hub_record(rel, front, body):
    slug = rel.split("/", 1)[1]
    first = next((ln for ln in body.splitlines() if ln.startswith("# ")), slug)
    title = clean(first)
    rest = body.split("\n", 1)[1] if "\n" in body else ""
    rec = record(rel, title, body)
    rec["preview"] = clean(rest)[:PREVIEW]
    # A hub slug starts with its kind (service-x). A repo slug is the repo name, so it keeps every word.
    short = re.sub(r"^[a-z]+-", "", slug) if rel.startswith("entities/") else slug
    names = [title, short.replace("-", " ")]
    aliases = []
    for ln in front:
        if ln.startswith("aliases:"):
            aliases = [a.strip().strip("'\"") for a in ln.split(":", 1)[1].strip(" []").split(",")]
    rec["names"] = [n for n in names + aliases if len(n) >= 3]
    rec["aliases"] = ", ".join(rec["names"][1:])
    rec["explicit_aliases"] = [a for a in aliases if a]
    rec["ids"] = hub_ids(body)
    rec["slug"] = slug
    return rec


FENCE_RE = re.compile(r"<\s*/?\s*vault-entry\b[^>\n]*>?", re.I)


def fence(ident, text, limit=None, pointer=""):
    """Wrap entry text in a <vault-entry> block. Fence tags inside the text are removed, so vault text cannot close the fence."""
    safe_id = re.sub(r'["<>\s]', "", ident)
    body = text
    while True:  # strip until stable: removing a tag must not rebuild one from the pieces around it
        stripped = FENCE_RE.sub("", body)
        if stripped == body:
            break
        body = stripped
    body = body.strip()
    if limit is not None:
        body = body[: max(0, limit - len(safe_id) - 40 - len(pointer))] + pointer
    return '<vault-entry id="%s">\n%s\n</vault-entry>' % (safe_id, body)
