import json
from pathlib import Path

from conftest import load_script

ckg = load_script("ckg", "scripts/check_knowledge_graph.py")

FM = "---\nid: %s\ntype: %s\n---\n"
HUB = "---\nid: %s\ntype: entity\ntags: [kind/%s]\nverify_with: none\n---\n"

def hub(name):
    return HUB % (name, name.split("-")[0])


def write(vault, rel, text):
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def make_valid(vault):
    write(vault, "ci/rules.md", FM % ("ci/rules", "rule") + "## R1 — x [[tool-a]]\n[added:: 2026-10-17] [verify_with:: none] ^R1\n")
    write(vault, "entities/tool-a.md", hub("tool-a") + "See [[ci/rules#^R1]].\n")
    write(vault, "_index.md", FM % ("_index", "map") + "[[tool-a]] and `[[not-checked]]`\n")


def test_valid_vault_passes(tmp_path):
    make_valid(tmp_path)
    assert ckg.check(str(tmp_path)) == []
    assert ckg.main(["--root", str(tmp_path)]) == 0


def test_main_default_root_is_real_vault():
    assert ckg.main([]) == 0


def test_real_vault_passes():
    assert ckg.check(ckg.DEFAULT_VAULT) == []


def test_broken_link_fails(tmp_path):
    make_valid(tmp_path)
    write(tmp_path, "entities/tool-b.md", hub("tool-b") + "[[missing/file]] [[ci/rules#^R9]]\n")
    errors = ckg.check(str(tmp_path))
    assert any("broken link [[missing/file]]" in e for e in errors)
    assert any("^R9 not found" in e for e in errors)
    assert ckg.main(["--root", str(tmp_path)]) == 1


def test_missing_frontmatter_fails(tmp_path):
    make_valid(tmp_path)
    write(tmp_path, "ci/knowledge.md", "# no header\n")
    assert any("ci/knowledge.md:1" in e for e in ckg.check(str(tmp_path)))


def test_duplicate_block_id_fails(tmp_path):
    make_valid(tmp_path)
    write(tmp_path, "ci/rules.md", FM % ("ci/rules", "rule") + "## R1 — x\n[a:: b] ^R1\n## R2 — y\n[a:: b] ^R1\n")
    assert any("duplicate block id ^R1" in e for e in ckg.check(str(tmp_path)))


def test_duplicate_hub_basename_fails(tmp_path):
    make_valid(tmp_path)
    write(tmp_path, "entities/sub/tool-a.md", hub("tool-a") + "x\n")
    assert any("duplicate hub basename" in e for e in ckg.check(str(tmp_path)))


def test_unclosed_fence_fails(tmp_path):
    make_valid(tmp_path)
    write(tmp_path, "entities/tool-b.md", hub("tool-b") + "text\n```\ncode never closed\n")
    assert any("tool-b.md:8: unclosed code fence" in e for e in ckg.check(str(tmp_path)))


def test_duplicate_frontmatter_id_fails(tmp_path):
    make_valid(tmp_path)
    write(tmp_path, "entities/tool-b.md", hub("tool-a") + "body\n")
    assert any("duplicate frontmatter id tool-a" in e for e in ckg.check(str(tmp_path)))


LESSON_META = "[added:: 2024-04-05] [source:: slack:1712345678.123456] [origin:: slack] [status:: active] [verify_with:: none] ^L-x\n"


REPO_FM = ("---\nid: repos/r\ntype: repo\ntags: [domain/repos]\norg: example-org\nsection: product\n"
           "added: 2026-10-17\nverify_with: github\nverify_query: github:repo:example-org/r\n---\n")


def test_lesson_valid(tmp_path):
    make_valid(tmp_path)
    write(tmp_path, "lessons/ua.md", FM % ("lessons/ua", "lesson") + "## Lesson x [[tool-a]]\n" + LESSON_META)
    assert ckg.check(str(tmp_path)) == []


def test_repo_file_outside_repos_fails(tmp_path):
    make_valid(tmp_path)
    write(tmp_path, "lessons/r.md", REPO_FM + "[[tool-a]]\n")
    assert any("must live under repos/" in e for e in ckg.check(str(tmp_path)))


def test_repo_file_valid(tmp_path):
    make_valid(tmp_path)
    write(tmp_path, "repos/r.md", REPO_FM + "# r\n\nA game.\n\nRelated: [[tool-a]]\n")
    assert ckg.check(str(tmp_path)) == []


def test_repo_file_under_entities_fails(tmp_path):
    make_valid(tmp_path)
    write(tmp_path, "entities/tool-b.md", REPO_FM + "[[tool-a]]\n")
    assert any("must live under repos/" in e for e in ckg.check(str(tmp_path)))


def test_repo_file_errors(tmp_path):
    make_valid(tmp_path)
    bad = REPO_FM.replace("org: example-org", "org: nobody").replace("section: product", "section: x")
    write(tmp_path, "repos/r.md", bad + "# r\n\nNo hub. " + "x" * 2500 + "\n")
    errors = "\n".join(ckg.check(str(tmp_path)))
    assert "repo org must be one of" in errors
    assert "repo section must be one of" in errors
    assert "over the 2500 byte cap" in errors
    assert "orphan repo" in errors


def test_repo_file_verify_fields_checked(tmp_path):
    make_valid(tmp_path)
    write(tmp_path, "repos/r.md", REPO_FM.replace("verify_query: github:repo:example-org/r\n", "") + "[[tool-a]]\n")
    assert any("verify_query is missing" in e for e in ckg.check(str(tmp_path)))


def test_jira_origin_experiment_lesson_valid(tmp_path):
    make_valid(tmp_path)
    meta = "[added:: 2026-10-17] [source:: jira:EXAMPLE-1] [origin:: jira] [status:: active] [verify_with:: none] ^L-exp-example-1\n"
    write(tmp_path, "experiments/app.md", FM % ("experiments/app", "lesson") + "## EXAMPLE-1 — Title [[tool-a]]\n" + meta)
    assert ckg.check(str(tmp_path)) == []


def test_lesson_meta_and_id_errors(tmp_path):
    make_valid(tmp_path)
    bad = "## Lesson [[tool-a]]\n[added:: 2026-10-17] [origin:: web] [status:: done] ^X1\n"
    write(tmp_path, "lessons/ua.md", FM % ("lessons/ua", "lesson") + bad)
    errors = "\n".join(ckg.check(str(tmp_path)))
    assert "does not fit type lesson" in errors
    assert "meta field source is missing" in errors
    assert "origin must be one of" in errors
    assert "status must be one of" in errors


def test_missing_block_id_fails_for_lesson(tmp_path):
    make_valid(tmp_path)
    write(tmp_path, "lessons/ua.md", FM % ("lessons/ua", "lesson") + "## Lesson [[tool-a]]\n")
    assert any("entry has no block id" in e for e in ckg.check(str(tmp_path)))


def test_orphan_entries_fail(tmp_path):
    make_valid(tmp_path)
    write(tmp_path, "ci/rules.md", FM % ("ci/rules", "rule") + "## R1 — x\n[added:: 2026-10-17] [verify_with:: none] ^R1\n")
    write(tmp_path, "ci/knowledge.md", FM % ("ci/knowledge", "fact") + "# K\n- a fact\n  ^K-a\n- linked [[tool-a]]\n  ^K-b\n")
    errors = ckg.check(str(tmp_path))
    assert any("ci/rules.md:5" in e for e in errors if "orphan" in e)
    assert sum("orphan" in e for e in errors) == 2
    assert not any("tool-a.md" in e or "_index.md" in e for e in errors)


def test_link_to_non_hub_is_still_orphan(tmp_path):
    make_valid(tmp_path)
    write(tmp_path, "ci/rules.md", FM % ("ci/rules", "rule") + "## R1 — x [[ci/knowledge]]\n^R1\n")
    write(tmp_path, "ci/knowledge.md", FM % ("ci/knowledge", "fact") + "# K\n- a [[tool-a]]\n  ^K-a\n")
    assert any("orphan" in e for e in ckg.check(str(tmp_path)))


def test_query_grammar_accepts_every_form():
    good = [
        "github:repo:example-org/example-tools",
        "github:path:example-org/directory:app/api/v1/users/route.ts",
        "directory:user:janedoe",
        "directory:user-name:Jane Q. Doe",
        "confluence:100001",
        "slack:CEXAMPLE:1712345678.123456",
        "none",
    ]
    for query in good:
        assert ckg.query_family(query) is not None, query


def test_query_grammar_rejects_bad_forms():
    for query in ["github:repo:noslash", "github:path:a/b", "confluence:abc", "slack:C1:12",
                  "directory:user:", "jira:ABC-1", "github:repo:a/b c"]:
        assert ckg.query_family(query) is None, query


def test_verify_fields_rules():
    assert ckg.verify_errors({}) != []
    assert ckg.verify_errors({"verify_with": "docs"}) != []
    assert ckg.verify_errors({"verify_with": "none"}) == []
    assert ckg.verify_errors({"verify_with": "none", "verify_query": "none"}) == []
    assert ckg.verify_errors({"verify_with": "none", "verify_query": "confluence:1"}) != []
    assert ckg.verify_errors({"verify_with": "github"}) != []
    assert ckg.verify_errors({"verify_with": "github", "verify_query": "github:repo:a/b; github:repo:a/c"}) == []
    assert ckg.verify_errors({"verify_with": "github", "verify_query": "confluence:1"}) != []
    assert ckg.verify_errors({"verify_with": "slack", "verify_query": "bogus"}) != []


def test_hub_needs_verify_with(tmp_path):
    make_valid(tmp_path)
    write(tmp_path, "entities/tool-b.md", FM % ("tool-b", "entity") + "x\n")
    assert any("tool-b.md:1: verify_with is missing" in e for e in ckg.check(str(tmp_path)))


def test_hub_with_query_in_frontmatter(tmp_path):
    make_valid(tmp_path)
    head = "---\nid: tool-b\ntype: entity\ntags: [kind/tool]\nverify_with: github\nverify_query: github:repo:a/b\n---\n"
    write(tmp_path, "entities/tool-b.md", head + "x\n")
    assert ckg.check(str(tmp_path)) == []


def test_entry_needs_verify_with(tmp_path):
    make_valid(tmp_path)
    write(tmp_path, "ci/rules.md", FM % ("ci/rules", "rule") + "## R1 — x [[tool-a]]\n[added:: 2026-10-17] ^R1\n")
    assert any("ci/rules.md:5: verify_with is missing" in e for e in ckg.check(str(tmp_path)))


def test_entry_query_must_fit_verify_with(tmp_path):
    make_valid(tmp_path)
    meta = "[verify_with:: github] [verify_query:: confluence:12] ^R1\n"
    write(tmp_path, "ci/rules.md", FM % ("ci/rules", "rule") + "## R1 — x [[tool-a]]\n" + meta)
    assert any("does not match verify_with github" in e for e in ckg.check(str(tmp_path)))


def test_person_hub_needs_title_and_department(tmp_path):
    make_valid(tmp_path)
    write(tmp_path, "entities/person-jane-doe.md", hub("person-jane-doe") + "x\n")
    errors = "\n".join(ckg.check(str(tmp_path)))
    assert "field title is missing" in errors and "field department is missing" in errors
    full = "---\nid: person-jane-doe\ntype: entity\ntags: [kind/person]\nverify_with: directory\n" \
           "verify_query: directory:user:janedoe\ntitle: UA Lead\ndepartment: Engineering\ngithub: janedoe\nslack: UEXAMPLE\n---\nx\n"
    write(tmp_path, "entities/person-jane-doe.md", full)
    assert ckg.check(str(tmp_path)) == []


def test_heading_anchor_link_is_checked(tmp_path):
    make_valid(tmp_path)
    write(tmp_path, "entities/tool-b.md", hub("tool-b") + "[[nowhere#Head]] [[tool-a#Head]]\n")
    errors = ckg.check(str(tmp_path))
    assert any("broken link [[nowhere]]" in e for e in errors)
    assert not any("tool-a" in e for e in errors)


def test_graveyard_exemption_needs_exact_id(tmp_path):
    make_valid(tmp_path)
    write(tmp_path, "_graveyard.md", FM % ("_graveyard", "map") + "removed ^R12\n")
    write(tmp_path, "entities/tool-b.md", hub("tool-b") + "[[ci/rules#^R1]] [[ci/rules#^R2]]\n")
    errors = ckg.check(str(tmp_path))
    assert any("^R2 not found" in e for e in errors)
    write(tmp_path, "_graveyard.md", FM % ("_graveyard", "map") + "removed ^R2\n")
    assert ckg.check(str(tmp_path)) == []


def test_block_ids_without_heading_fail(tmp_path):
    make_valid(tmp_path)
    write(tmp_path, "lessons/x.md", FM % ("lessons/x", "lesson") + "text [[tool-a]] ^L-x\n")
    assert any("no heading" in e for e in ckg.check(str(tmp_path)))


def test_person_hub_slack_optional_and_shaped(tmp_path):
    make_valid(tmp_path)
    base = "---\nid: person-jane-doe\ntype: entity\ntags: [kind/person]\nverify_with: directory\n" \
           "verify_query: directory:user:janedoe\ntitle: UA Lead\ndepartment: Engineering\n%s---\nx\n"
    write(tmp_path, "entities/person-jane-doe.md", base % "")
    assert ckg.check(str(tmp_path)) == []
    write(tmp_path, "entities/person-jane-doe.md", base % "slack: jane\n")
    assert "slack must be a member ID" in "\n".join(ckg.check(str(tmp_path)))
    write(tmp_path, "entities/person-jane-doe.md", base % "slack: UEXAMPLE\n")
    assert ckg.check(str(tmp_path)) == []


def errors_with(tmp_path, rel, text, needle):
    make_valid(tmp_path)
    write(tmp_path, rel, text)
    return [e for e in ckg.check(str(tmp_path)) if needle in e]


def test_unknown_file_type_fails(tmp_path):
    assert errors_with(tmp_path, "ci/x.md", FM % ("ci/x", "weird") + "x\n", "type weird must be")


def test_id_must_equal_path(tmp_path):
    assert errors_with(tmp_path, "ci/other.md", FM % ("ci/rules2", "map") + "x\n", "id ci/rules2 must equal ci/other")
    assert errors_with(tmp_path, "entities/tool-c.md", hub("tool-d") + "x\n", "id tool-d must equal tool-c")


def test_bad_entry_dates_fail(tmp_path):
    text = FM % ("ci/rules", "rule") + "## R1 — x [[tool-a]]\n[added:: bad-date] [last_verified:: 2026-13-40] [verify_with:: none] ^R1\n"
    found = errors_with(tmp_path, "ci/rules.md", text, "must be a date")
    assert len(found) == 2


def test_entry_without_added_fails(tmp_path):
    text = FM % ("ci/rules", "rule") + "## R1 — x [[tool-a]]\n[verify_with:: none] ^R1\n"
    assert errors_with(tmp_path, "ci/rules.md", text, "meta field added is missing")


def test_bad_hub_verified_date_fails(tmp_path):
    text = hub("tool-b")[:-4] + "verified: nope\n---\nx\n"
    assert errors_with(tmp_path, "entities/tool-b.md", text, "verified must be a date")


def test_hub_kind_tag_must_match_prefix(tmp_path):
    no_tag = "---\nid: tool-b\ntype: entity\ntags: []\nverify_with: none\n---\nx\n"
    assert errors_with(tmp_path, "entities/tool-b.md", no_tag, "hub needs one tag")
    wrong = no_tag.replace("[]", "[kind/product]")
    assert errors_with(tmp_path, "entities/tool-b.md", wrong, "hub needs one tag")
    assert errors_with(tmp_path, "entities/zzz-foo.md", hub("zzz-foo"), "hub needs one tag")


def test_contact_data_fails(tmp_path):
    email = "@".join(("example-user", "example.invalid"))
    for bad in (email, "call +1 555 123 4567", "call (555) 123-4567"):
        assert errors_with(tmp_path, "entities/tool-b.md", hub("tool-b") + bad + "\n", "contact data")


def test_ids_and_timestamps_are_not_contacts(tmp_path):
    body = "slack:CEXAMPLE:1712345678.123456 confluence:100001 2026-10-17\n"
    assert errors_with(tmp_path, "entities/tool-b.md", hub("tool-b") + body, "contact data") == []


def log(vault, day, ids):
    write(vault, "_verify/%s.json" % day, '{"checked_at": "%s", "ok": %s}' % (day, str(ids).replace("'", '"')))


def test_hub_date_needs_log_entry(tmp_path):
    make_valid(tmp_path)
    write(tmp_path, "entities/tool-a.md", "---\nid: tool-a\ntype: entity\ntags: [kind/tool]\nverify_with: none\nverified: 2026-10-01\n---\nSee [[ci/rules#^R1]].\n")
    errors = ckg.check(str(tmp_path))
    assert any("no script verdict in _verify/2026-10-01.json" in e for e in errors)
    log(tmp_path, "2026-10-01", ["tool-a"])
    assert ckg.check(str(tmp_path)) == []
    log(tmp_path, "2026-10-01", ["other"])
    assert any("no script verdict" in e for e in ckg.check(str(tmp_path)))


def test_entry_date_needs_log_entry(tmp_path):
    make_valid(tmp_path)
    write(tmp_path, "ci/rules.md", FM % ("ci/rules", "rule") + "## R1 — x [[tool-a]]\n"
          "[added:: 2026-10-17] [verify_with:: none] [last_verified:: 2026-10-02] ^R1\n")
    assert any("_verify/2026-10-02.json" in e for e in ckg.check(str(tmp_path)))
    log(tmp_path, "2026-10-02", ["ci/rules#^R1"])
    assert ckg.check(str(tmp_path)) == []


def test_malformed_log_is_an_error_not_a_crash(tmp_path):
    make_valid(tmp_path)
    write(tmp_path, "_verify/2026-10-02.json", "{not json")
    write(tmp_path, "_verify/2026-10-03.json", '{"ok": "x"}')
    write(tmp_path, "_verify/bad-name.json", "{}")
    errors = ckg.check(str(tmp_path))
    assert len([e for e in errors if "bad verify log" in e]) == 3


def lesson_vault(tmp_path, added):
    make_valid(tmp_path)
    write(tmp_path, "lessons/ua.md", FM % ("lessons/ua", "lesson") + "## L-x — x [[tool-a]]\n"
          "[added:: %s] [source:: slack:1712345678.123456] [origin:: slack] [status:: active] [verify_with:: none] ^L-x\n" % added)


def test_slack_added_must_match_newest_slack_date(tmp_path):
    lesson_vault(tmp_path, "2024-04-05")
    assert ckg.check(str(tmp_path)) == []


def test_slack_added_mismatch_fails(tmp_path):
    lesson_vault(tmp_path, "2026-10-17")
    assert any("must equal the newest Slack date 2024-04-05" in e for e in ckg.check(str(tmp_path)))


def test_hub_over_its_cap_fails(tmp_path):
    make_valid(tmp_path)
    write(tmp_path, "entities/tool-a.md", hub("tool-a") + "See [[ci/rules#^R1]].\n" + "x" * ckg.HUB_MAX_BYTES["tool"])
    assert any("tool hub is" in e and "byte cap" in e for e in ckg.check(str(tmp_path)))


CARD = (
    "## Guide\n[added:: 2026-10-18] [source:: confluence:55] [updated:: 2026-10-18] [verify_with:: confluence] "
    "[verify_query:: confluence:55] ^page-55\nSections: Goals\n- Plans. [[tool-a]]\n"
)


def card_vault(tmp_path, body):
    make_valid(tmp_path)
    write(tmp_path, "confluence/CP.md", FM % ("confluence/CP", "card") + body)


def test_valid_card_passes(tmp_path):
    card_vault(tmp_path, CARD)
    assert ckg.check(str(tmp_path)) == []


def test_card_with_numeric_table_row_or_big_body_fails(tmp_path):
    card_vault(tmp_path, CARD + "| ROAS | 120 |\n")
    assert any("table row" in e for e in ckg.check(str(tmp_path)))
    card_vault(tmp_path, CARD + "- " + "x" * 900 + "\n")
    assert any("byte cap" in e for e in ckg.check(str(tmp_path)))


def test_config_sets_hub_caps_kinds_and_person_fields():
    with open(Path(ckg.__file__).resolve().parents[1] / "plugins/knowledge/knowledge.json") as fh:
        config = json.load(fh)
    assert ckg.HUB_MAX_BYTES == config["hub_kinds"]
    assert ckg.HUB_KINDS == tuple(config["hub_kinds"])
    assert ckg.PERSON_REQUIRED == tuple(config["person_required"])
    assert "org" in ckg.HUB_KINDS and "product" in ckg.HUB_KINDS
    assert ckg.query_family("directory:endpoint:users") is None
