import os
import subprocess

from conftest import load_script

cu = load_script("ci_units", "scripts/ci_units.py")

MAKE_OK = "check:\n\ttrue\n"


def git(repo, *args):
    subprocess.run(["git", "-c", "user.email=" + "@".join(("test", "example.invalid")), "-c", "user.name=t", *args],
                   cwd=repo, check=True, capture_output=True)


def put(repo, rel, text="x\n"):
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def run(repo, changes, deletes=()):
    """Commit a base, then a head with `changes` (rel -> text) and `deletes`."""
    git(repo, "init", "-q")
    put(repo, "README.md")
    for rel in deletes:
        put(repo, rel)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    for rel, text in changes.items():
        put(repo, rel, text)
    for rel in deletes:
        os.remove(repo / rel)
        # Git does not track empty folders, so drop them like a real checkout.
        if not os.listdir(repo / os.path.dirname(rel)):
            os.rmdir(repo / os.path.dirname(rel))
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "head")
    files = cu.changed_files(base, "HEAD", str(repo))
    return cu.plan(files, str(repo))


def test_reserved_and_root_paths_are_not_units(tmp_path):
    changes = {p: "x\n" for p in (".github/a.yml", "plugins/k/a.py", "scripts/a.py",
                                  "tests/a.py", "docs/a.md", "docs/b.py", "evals/a.jsonl", "root.py")}
    assert run(tmp_path, changes) == ({"include": []}, [])


def test_markdown_only_folder_is_skipped(tmp_path):
    assert run(tmp_path, {"skills/alice/foo/SKILL.md": "x\n"}) == ({"include": []}, [])


def test_code_without_makefile_errors(tmp_path):
    matrix, errors = run(tmp_path, {"tool/a.py": "x\n", "tool/README.md": "x\n"})
    assert matrix == {"include": []}
    assert errors == ['tool: ' + cu.CONTRACT_HINT]


def test_makefile_without_check_errors(tmp_path):
    matrix, errors = run(tmp_path, {"tool/Makefile": "build:\n\ttrue\n", "tool/a.py": "x\n"})
    assert matrix == {"include": []}
    assert errors == ['tool: ' + cu.CONTRACT_HINT]


def test_nested_unit_and_ancestor_lookup(tmp_path):
    changes = {"skills/alice/foo/Makefile": MAKE_OK, "skills/alice/foo/src/a.py": "x\n"}
    matrix, errors = run(tmp_path, changes)
    assert errors == []
    assert matrix == {"include": [{"unit": "skills/alice/foo", "shard": 1, "shards": 1}]}


def test_makefile_above_top_level_is_ignored(tmp_path):
    # A root Makefile must not turn a loose folder into a unit.
    matrix, errors = run(tmp_path, {"Makefile": MAKE_OK, "tool/a.py": "x\n"})
    assert errors and matrix == {"include": []}


def test_shards_parsed_and_clamped(tmp_path):
    matrix, errors = run(tmp_path, {"a/Makefile": "CI_SHARDS = 3\n" + MAKE_OK,
                                    "b/Makefile": "CI_SHARDS ?= 99\n" + MAKE_OK,
                                    "c/Makefile": "CI_SHARDS := 0\n" + MAKE_OK})
    assert errors == []
    by_unit = {}
    for e in matrix["include"]:
        by_unit.setdefault(e["unit"], []).append((e["shard"], e["shards"]))
    assert by_unit["a"] == [(1, 3), (2, 3), (3, 3)]
    assert len(by_unit["b"]) == 16 and by_unit["b"][0] == (1, 16)
    assert by_unit["c"] == [(1, 1)]


def test_deleted_folder_is_skipped(tmp_path):
    matrix, errors = run(tmp_path, {}, deletes=["gone/Makefile", "gone/a.py"])
    assert (matrix, errors) == ({"include": []}, [])


def test_non_ascii_path_is_listed_unquoted(tmp_path):
    matrix, errors = run(tmp_path, {"tööl/Makefile": MAKE_OK, "tööl/a.py": "x\n"})
    assert errors == []
    assert matrix == {"include": [{"unit": "tööl", "shard": 1, "shards": 1}]}


def test_dot_folder_is_reserved(tmp_path):
    assert run(tmp_path, {".hidden/a.py": "x\n", ".claude-plugin/b.json": "{}\n"}) == ({"include": []}, [])


def test_matrix_over_256_errors(tmp_path):
    changes = {}
    for i in range(20):
        changes["t%d/Makefile" % i] = "CI_SHARDS = 16\ncheck:\n\ttrue\n"
        changes["t%d/a.py" % i] = "x\n"
    matrix, errors = run(tmp_path, changes)
    assert len(matrix["include"]) == 320
    assert any("limit of 256" in e for e in errors)
