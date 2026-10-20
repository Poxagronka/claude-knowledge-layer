"""scripts/carry_forward.sh: carry the last bot approval when the diff is unchanged."""
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "carry_forward.sh"


def git(cwd, *args):
    email = "@".join(("test", "example.invalid"))
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": email,
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": email}
    out = subprocess.run(["git", *args], cwd=cwd, env=env, check=True,
                         capture_output=True, text=True)
    return out.stdout.strip()


def write(repo, files):
    for name, data in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode())


def commit(repo, files, msg="c"):
    write(repo, files)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", msg)
    return git(repo, "rev-parse", "HEAD")


def run_carry(tmp_path, change, merge_main=False, old_change=None):
    """Approve commit O, then push N = O + change. Return (carried, log)."""
    src = tmp_path / "src"
    src.mkdir()
    git(src, "init", "-q", "-b", "main")
    commit(src, {"app.py": "x = 1\n", "plugins/p/SKILL.md": "a\n",
                 "bin.dat": b"\x00\x01", "README.md": "r\n"})
    git(src, "checkout", "-q", "-b", "pr")
    old = commit(src, old_change or {"app.py": "x = 1\ny = 2\n"})
    if merge_main:
        git(src, "checkout", "-q", "main")
        commit(src, {"other.txt": "moved\n"})
        git(src, "checkout", "-q", "pr")
        git(src, "merge", "-q", "--no-edit", "main")
    if change:
        new = commit(src, change)
    elif merge_main:
        new = git(src, "rev-parse", "HEAD")
    else:
        git(src, "commit", "-q", "--allow-empty", "-m", "e")
        new = git(src, "rev-parse", "HEAD")
    remote = tmp_path / "remote.git"
    git(tmp_path, "clone", "-q", "--bare", str(src), str(remote))
    git(remote, "config", "uploadpack.allowAnySHA1InWant", "true")
    git(remote, "update-ref", "refs/pull/1/head", new)
    work = tmp_path / "work"
    work.mkdir()
    git(work, "init", "-q")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "gh"
    stub.write_text(f"#!/bin/bash\necho {old}\n")
    stub.chmod(0o755)
    out = tmp_path / "out.txt"
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "GH_TOKEN": "t",
           "REPO": "o/r", "PR": "1", "BASE_REF": "main", "HEAD_SHA": new,
           "GITHUB_OUTPUT": str(out), "CARRY_REMOTE": str(remote)}
    res = subprocess.run(["bash", str(SCRIPT), "code"], cwd=work, env=env,
                         capture_output=True, text=True)
    return "carried=true" in out.read_text(), res.stdout + res.stderr


@pytest.mark.parametrize("change,merge,expected", [
    ({"docs/a.md": "d\n", "README.md": "r2\n"}, False, True),
    ({"app.py": "x = 1\n\n\ny = 2\n"}, False, False),
    ({"plugins/p/SKILL.md": "b\n"}, False, False),
    ({"bin.dat": b"\x00\x02"}, False, False),
    ({}, False, False),
    ({}, True, True),
], ids=["docs-only", "whitespace", "plugin-md", "binary", "empty-diff", "merge-main"])
def test_carry(tmp_path, change, merge, expected):
    # Empty diff: the approved commit changed docs only, so no code was reviewed.
    old = {"docs/o.md": "o\n"} if change == {} and not merge else None
    carried, log = run_carry(tmp_path, change, merge, old)
    assert carried is expected, log


def test_no_argument_prints_usage_and_exits_zero():
    r = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0
    assert r.stdout == "usage: carry_forward.sh code|vault\n"
    assert r.stderr == ""
