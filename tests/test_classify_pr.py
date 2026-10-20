import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "classify_pr.sh")


def classify(tmp_path, paths):
    f = tmp_path / "paths.txt"
    f.write_text("".join(p + "\n" for p in paths))
    out = subprocess.run(["bash", SCRIPT, "--paths-file", str(f)],
                         check=True, capture_output=True, text=True).stdout
    return dict(line.split("=") for line in out.split())


V = "plugins/knowledge/vault/a.md"


def test_code_only(tmp_path):
    assert classify(tmp_path, ["scripts/x.py"]) == {"docs_only": "false", "has_knowledge": "false"}


def test_docs_only(tmp_path):
    r = classify(tmp_path, ["README.md", "docs/a.txt", "knowledge/x/rules.md"])
    assert r == {"docs_only": "true", "has_knowledge": "false"}


def test_vault_only(tmp_path):
    assert classify(tmp_path, [V]) == {"docs_only": "true", "has_knowledge": "true"}


def test_code_and_vault(tmp_path):
    assert classify(tmp_path, [V, "scripts/x.py"]) == {"docs_only": "false", "has_knowledge": "true"}


def test_skill_markdown_is_code(tmp_path):
    r = classify(tmp_path, ["plugins/knowledge/skills/x/SKILL.md"])
    assert r == {"docs_only": "false", "has_knowledge": "false"}


def test_rename_vault_to_docs(tmp_path):
    r = classify(tmp_path, ["docs/moved.md", V])
    assert r == {"docs_only": "true", "has_knowledge": "true"}


def test_empty_list_fails_closed(tmp_path):
    assert classify(tmp_path, []) == {"docs_only": "false", "has_knowledge": "true"}


def test_verify_log_json_in_vault_is_docs(tmp_path):
    r = classify(tmp_path, [V, "plugins/knowledge/vault/_verify/2026-10-18.json"])
    assert r == {"docs_only": "true", "has_knowledge": "true"}


def test_other_vault_json_is_code(tmp_path):
    r = classify(tmp_path, [V, "plugins/knowledge/vault/.obsidian/x.json"])
    assert r == {"docs_only": "false", "has_knowledge": "true"}
