"""scripts/apply_verdict.sh and scripts/pr_diff.sh, run against a fake `gh`."""
import json
import os
import subprocess
from pathlib import Path

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="bash CI scripts: POSIX only")

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
SHA = "abc123"

# The fake gh logs every call, one line each, and answers from env fixtures.
FAKE_GH = r"""#!/bin/bash
echo "$*" >> "$GH_LOG"
case "$*" in
  "pr view"*headRefOid*) echo "$FAKE_HEAD" ;;
  "pr view"*labels*) printf '%s' "$FAKE_LABELS" ;;
  "api repos/o/r/pulls/1/reviews --paginate"*) printf '%s' "$FAKE_REVIEWS" ;;
  "api --paginate repos/o/r/pulls/1/files") cat "$FAKE_FILES" ;;
esac
exit 0
"""


def setup(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gh = bindir / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(0o755)
    work = tmp_path / "work"
    (work / ".review-ctx").mkdir(parents=True)
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "GH_TOKEN": "t",
           "REPO": "o/r", "PR": "1", "AUTHOR_OK": "true", "FAKE_HEAD": SHA,
           "FAKE_LABELS": "", "FAKE_REVIEWS": "", "GH_LOG": str(tmp_path / "gh.log"),
           "HAS_KNOWLEDGE": "false", "DOCS_ONLY": "true"}
    return work, env


def run(tmp_path, mode, verdict="approved", sha=SHA, gate_rc=None, **env_over):
    work, env = setup(tmp_path)
    env.update(env_over)
    if verdict is not None:
        (work / ".review-ctx/verdict.json").write_text(
            json.dumps({"verdict": verdict, "head_sha": sha}))
    if gate_rc is not None:
        (work / ".review-ctx/gate.rc").write_text(str(gate_rc))
    res = subprocess.run(["bash", str(SCRIPTS / "apply_verdict.sh"), mode], cwd=work,
                         env=env, capture_output=True, text=True)
    log = (tmp_path / "gh.log").read_text().splitlines() if (tmp_path / "gh.log").exists() else []
    return res.returncode, log


def posts(log):
    return [c for c in log if "-X POST" in c]


def edits(log):
    return [c for c in log if c.startswith("pr edit")]


def test_no_verdict_file_exits_1_without_edit(tmp_path):
    rc, log = run(tmp_path, "code", verdict=None)
    assert rc == 1 and not edits(log)


def test_sha_mismatch_exits_1(tmp_path):
    rc, log = run(tmp_path, "code", sha="other")
    assert rc == 1 and not edits(log)


def test_unknown_mode_exits_2(tmp_path):
    assert run(tmp_path, "bogus")[0] == 2


@pytest.mark.parametrize("mode,mine", [("code", "claude"), ("knowledge", "knowledge")])
def test_changes_needed_labels_without_post(tmp_path, mode, mine):
    rc, log = run(tmp_path, mode, verdict="changes_needed", gate_rc=0)
    assert rc == 0 and not posts(log)
    assert any(f"--add-label {mine}: changes needed" in c for c in edits(log))


def test_approved_but_author_not_ok_labels_only(tmp_path):
    rc, log = run(tmp_path, "code", AUTHOR_OK="false")
    assert rc == 0 and not posts(log)
    assert any("--add-label claude: approved" in c for c in edits(log))


def test_code_needs_knowledge_label_when_vault_touched(tmp_path):
    rc, log = run(tmp_path, "code", HAS_KNOWLEDGE="true", FAKE_LABELS="claude: approved\n")
    assert rc == 0 and not posts(log)


def test_code_empty_has_knowledge_fails_closed(tmp_path):
    rc, log = run(tmp_path, "code", HAS_KNOWLEDGE="", FAKE_LABELS="claude: approved\n")
    assert rc == 0 and not posts(log)


def test_knowledge_gate_failure_forces_changes_needed(tmp_path):
    rc, log = run(tmp_path, "knowledge", gate_rc=1)
    assert rc == 0 and not posts(log)
    assert any("--add-label knowledge: changes needed" in c for c in edits(log))


def test_knowledge_missing_gate_rc_counts_as_failure(tmp_path):
    rc, log = run(tmp_path, "knowledge")
    assert rc == 0 and not posts(log)


def test_knowledge_needs_claude_label_when_not_docs_only(tmp_path):
    rc, log = run(tmp_path, "knowledge", gate_rc=0, DOCS_ONLY="false",
                  FAKE_LABELS="knowledge: approved\n")
    assert rc == 0 and not posts(log)


def test_all_labels_present_makes_one_pinned_post(tmp_path):
    rc, log = run(tmp_path, "code", HAS_KNOWLEDGE="true",
                  FAKE_LABELS="claude: approved\nknowledge: approved\n")
    assert rc == 0 and len(posts(log)) == 1
    assert f"commit_id={SHA}" in posts(log)[0]


def test_docs_only_knowledge_posts_without_claude_label(tmp_path):
    rc, log = run(tmp_path, "knowledge", gate_rc=0, FAKE_LABELS="knowledge: approved\n")
    assert rc == 0 and len(posts(log)) == 1


def test_already_approved_commit_gets_no_second_post(tmp_path):
    rc, log = run(tmp_path, "code", FAKE_LABELS="claude: approved\n", FAKE_REVIEWS=f"{SHA}\n")
    assert rc == 0 and not posts(log)


@pytest.mark.parametrize("mode,other", [("code", "knowledge"), ("knowledge", "claude")])
def test_other_changes_needed_blocks_post(tmp_path, mode, other):
    mine = "claude" if mode == "code" else "knowledge"
    rc, log = run(tmp_path, mode, gate_rc=0,
                  FAKE_LABELS=f"{mine}: approved\n{other}: changes needed\n")
    assert rc == 0 and not posts(log)


def test_pr_diff_builds_unified_diff_from_rest_files(tmp_path):
    work, env = setup(tmp_path)
    files = [
        {"filename": "a.py", "status": "modified", "patch": "@@ -1 +1 @@\n-x\n+y"},
        {"filename": "new.md", "status": "added", "patch": "@@ -0,0 +1 @@\n+n"},
        {"filename": "b.bin", "status": "modified"},
        {"filename": "new/name.py", "previous_filename": "old/name.py",
         "status": "renamed", "patch": ""},
    ]
    # --paginate prints one JSON array per page.
    fixture = tmp_path / "files.json"
    fixture.write_text(json.dumps(files[:2]) + json.dumps(files[2:]))
    env["FAKE_FILES"] = str(fixture)
    res = subprocess.run(["bash", str(SCRIPTS / "pr_diff.sh")], cwd=work, env=env,
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    out = res.stdout
    assert "api --paginate repos/o/r/pulls/1/files" in (tmp_path / "gh.log").read_text()
    assert out.count("diff --git ") == 4
    assert "diff --git a/old/name.py b/new/name.py" in out
    assert "--- /dev/null\n+++ b/new.md\n@@ -0,0 +1 @@\n+n\n" in out
    assert "diff --git a/b.bin b/b.bin\n--- a/b.bin\n+++ b/b.bin\nBinary files differ\n" in out


def test_pr_diff_handles_more_than_300_files(tmp_path):
    work, env = setup(tmp_path)
    files = [{"filename": f"f{i}.txt", "status": "modified", "patch": "@@ -1 +1 @@\n-a\n+b"}
             for i in range(350)]
    fixture = tmp_path / "files.json"
    fixture.write_text(json.dumps(files[:100]) + json.dumps(files[100:]))
    env["FAKE_FILES"] = str(fixture)
    res = subprocess.run(["bash", str(SCRIPTS / "pr_diff.sh")], cwd=work, env=env,
                         capture_output=True, text=True)
    assert res.stdout.count("diff --git ") == 350
