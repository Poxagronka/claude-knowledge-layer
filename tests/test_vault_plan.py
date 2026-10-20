import copy
import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("vault_plan", os.path.join(ROOT, "scripts", "vault_plan.py"))
vp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vp)
ckg = vp.ckg

HUB = "---\nid: tool-a\ntype: entity\ntags: [kind/tool]\nverify_with: none\n---\nA hub.\n"
CARD = (
    "## Planning guide\n"
    "[added:: 2026-10-18] [source:: confluence:123] [updated:: 2026-10-18] [verify_with:: confluence] "
    "[verify_query:: confluence:123] ^page-123\n"
    "https://example.atlassian.net/wiki/spaces/CP/pages/123\n"
    "Sections: Goals, Owners\n"
    "- Plans the quarter. See [[tool-a]].\n"
)
LESSON = (
    "## L-x-one — A lesson [[tool-a]]\n"
    "[added:: 2026-10-18] [source:: slack:C1:1.0] [origin:: agent] [verify_with:: none] [status:: active] ^L-x-one\n\n"
    "Outcome: text.\n"
)


OTHER = CARD.replace("123", "456").replace("Planning guide", "Other")


def make_vault(tmp_path):
    for rel, text in {
        "entities/tool-a.md": HUB,
        "confluence/CP.md": "---\nid: confluence/CP\ntype: card\n---\n# CP cards\n\n" + OTHER,
        "lessons/x.md": "---\nid: lessons/x\ntype: lesson\n---\n# X\n\n" + LESSON.replace("x-one", "x-zero"),
    }.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return str(tmp_path)


def card_plan(text=CARD):
    return {"job": "cards", "rejects": [], "ops": [
        {"op": "upsert_card", "file": "confluence/CP.md", "block_id": "page-123", "text": text, "closes": []}]}


def tree(vault):
    return {os.path.relpath(os.path.join(d, f), vault): open(os.path.join(d, f)).read()
            for d, _s, fs in os.walk(vault) for f in fs}


def test_card_plan_applies_twice_with_same_tree_and_passes_gate(tmp_path):
    vault = make_vault(tmp_path)
    plan = card_plan()
    assert vp.validate(plan, vault) == []
    assert vp.apply(plan, vault) == ["confluence/CP.md"]
    once = tree(vault)
    assert vp.apply(plan, vault) == []
    assert tree(vault) == once
    assert ckg.check(vault) == []


def test_replace_keeps_neighbours(tmp_path):
    vault = make_vault(tmp_path)
    vp.apply(card_plan(), vault)
    vp.apply(card_plan(CARD.replace("the quarter", "the year")), vault)
    body = (tmp_path / "confluence/CP.md").read_text()
    assert body.count("^page-123") == 1 and "Plans the year" in body and "## Other" in body
    assert ckg.check(vault) == []


def test_numeric_table_card_fails_validate_and_gate(tmp_path):
    vault = make_vault(tmp_path)
    bad = CARD + "| ROAS | 120% |\n"
    assert any("table row" in e for e in vp.validate(card_plan(bad), vault))
    (tmp_path / "confluence/CP.md").write_text("---\nid: confluence/CP\ntype: card\n---\n" + bad)
    assert any("table row" in e for e in ckg.check(vault))


def test_gate_rejects_card_with_wrong_id_or_verify_with(tmp_path):
    vault = make_vault(tmp_path)
    bad = CARD.replace("^page-123", "^page-999").replace("[verify_with:: confluence]", "[verify_with:: none]")
    (tmp_path / "confluence/CP.md").write_text("---\nid: confluence/CP\ntype: card\n---\n" + bad)
    errors = " ".join(ckg.check(vault))
    assert "must be page-<id>" in errors and "verify_with must be confluence" in errors


def test_validate_rejects_bad_ops(tmp_path):
    vault = make_vault(tmp_path)
    good = {"op": "add", "file": "lessons/x.md", "block_id": "L-x-one", "text": LESSON, "closes": [7]}
    base = {"job": "distil", "ops": [good], "rejects": []}
    assert vp.validate(base, vault, issues=[7]) == []

    def errors(**change):
        plan = copy.deepcopy(base)
        plan["ops"][0].update(change)
        return vp.validate(plan, vault, issues=[7])

    assert errors(file="../x.md") and errors(file="entities/tool-a.md") and errors(file="lessons/_index.md")
    assert errors(file="confluence/CP.md")
    assert errors(block_id="R1")
    assert errors(closes=[8])
    assert errors(text=LESSON + "token ghp_" + "a" * 30 + "\n")
    assert errors(text=LESSON.replace("[status:: active]", "[status:: active] [last_verified:: 2026-10-18]"))
    assert errors(text=LESSON + "x" * 1300)
    assert errors(op="replace")
    assert len(vp.validate({"job": "distil", "ops": [good] * 16}, vault, issues=[7])) >= 1
    assert vp.validate({"job": "distil", "ops": [], "rejects": [{"issue": 9, "reason": "nope"}]}, vault)


def test_add_lesson_then_gate(tmp_path):
    vault = make_vault(tmp_path)
    plan = {"job": "distil", "rejects": [], "ops": [
        {"op": "add", "file": "lessons/x.md", "block_id": "L-x-one", "text": LESSON, "closes": []}]}
    assert vp.validate(plan, vault) == []
    vp.apply(plan, vault)
    assert ckg.check(vault) == []
    assert any("add finds" in e for e in vp.validate(plan, vault))


def test_bullet_section_entry_is_not_writable(tmp_path):
    vault = make_vault(tmp_path)
    path = tmp_path / "api"
    path.mkdir()
    body = "---\nid: api/knowledge\ntype: fact\n---\n# API\n\n## Facts\n- One ^F-one\n- Two ^F-two\n"
    (path / "knowledge.md").write_text(body)
    plan = {"job": "distil", "rejects": [], "ops": [{
        "op": "replace", "file": "api/knowledge.md", "block_id": "F-one",
        "text": "## Facts\n[added:: 2026-10-18] ^F-one\n", "closes": []}]}
    errors = vp.validate(plan, vault)
    assert any("bullet section" in e for e in errors)
    assert (path / "knowledge.md").read_text() == body


def test_verified_field_without_space_is_rejected(tmp_path):
    vault = make_vault(tmp_path)
    plan = card_plan(CARD.replace("[verify_with:: confluence]", "[verified::2026-10-18] [verify_with:: confluence]"))
    assert any("verified" in e for e in vp.validate(plan, vault))
