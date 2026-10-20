"""The gate and the selector must read the same entries from every vault file."""
import os

from conftest import load_script

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VAULT = os.path.join(ROOT, "plugins", "knowledge", "vault")


ckg = load_script("check_knowledge_graph", "scripts/check_knowledge_graph.py")
sel = load_script("knowledge_select", "plugins/knowledge/scripts/knowledge_select.py")


def test_selector_and_gate_agree_on_block_ids():
    checked = 0
    for base, _dirs, names in os.walk(VAULT):
        for name in sorted(names):
            if not name.endswith(".md") or name in sel.SKIP_FILES:
                continue
            path = os.path.join(base, name)
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            meta = ckg.frontmatter(text)
            if not meta or meta.get("type") not in ckg.ID_SHAPE:
                continue
            checked += 1
            rel = os.path.relpath(path, VAULT)[:-3]
            _front, body = sel.split_frontmatter(text)
            selector_ids = {e[0].split("#^", 1)[1] for e in sel.parse_entries(rel, body.strip().splitlines())}
            gate_ids = {bid for _l, _t, _g, bid in ckg.entries(text) if bid}
            assert selector_ids == gate_ids, "%s: selector-only %s, gate-only %s" % (
                rel, sorted(selector_ids - gate_ids), sorted(gate_ids - selector_ids))
    assert checked > 0
