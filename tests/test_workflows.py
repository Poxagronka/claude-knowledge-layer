"""Text-level invariants of the CI workflows. Stdlib only: PyYAML is not in CI."""
import glob
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WF_DIR = os.path.join(ROOT, ".github", "workflows")
REVIEW = "claude-review.yml"
APPROVE = "auto-approve.yml"
CHECK = "check-pr.yml"


def read(name):
    with open(os.path.join(WF_DIR, name), encoding="utf-8") as fh:
        return fh.read()


def all_workflows():
    return {os.path.basename(p): read(os.path.basename(p)) for p in sorted(glob.glob(os.path.join(WF_DIR, "*.yml")))}


def checkout_blocks(text):
    """Return the text of each `actions/checkout` step, up to the next step or job key."""
    lines = text.splitlines()
    blocks = []
    for i, line in enumerate(lines):
        if "uses: actions/checkout@" not in line:
            continue
        indent = len(line) - len(line.lstrip())
        out = [line]
        for nxt in lines[i + 1:]:
            stripped = nxt.strip()
            cur = len(nxt) - len(nxt.lstrip())
            if stripped and (cur < indent or (cur == indent and stripped.startswith("- "))):
                break
            out.append(nxt)
        blocks.append("\n".join(out))
    return blocks


def test_uses_are_pinned_to_a_commit():
    pin = re.compile(r"^[\w.-]+/[\w.-]+(/[\w./-]+)?@[0-9a-f]{40}$")
    bad = []
    for name, text in all_workflows().items():
        for m in re.finditer(r"^\s*(?:-\s+)?uses:\s*(\S+)", text, re.M):
            ref = m.group(1)
            if not pin.match(ref):
                bad.append("%s: %s" % (name, ref))
    assert not bad, bad


def test_docker_images_are_pinned_by_digest():
    for name, text in all_workflows().items():
        for line in text.splitlines():
            if "docker run" in line:
                assert "@sha256:" in line, "%s: %s" % (name, line.strip())


def test_review_checkouts_drop_credentials():
    for name in (REVIEW, APPROVE):
        blocks = checkout_blocks(read(name))
        assert blocks, name
        for block in blocks:
            assert "persist-credentials: false" in block, "%s:\n%s" % (name, block)


def test_head_checkout_goes_to_review_ctx():
    found = 0
    for name in (REVIEW, APPROVE):
        for block in checkout_blocks(read(name)):
            if "github.event.pull_request.head.sha" in block:
                found += 1
                assert "path: .review-ctx/head" in block, "%s:\n%s" % (name, block)
    assert found > 0


def test_author_lists_use_repository_variable_with_closed_default():
    for name in (REVIEW, APPROVE):
        text = read(name)
        assert "fromJson(vars.REVIEW_AUTHORS || '[]')" in text, name
        assert not re.search(r"fromJson\('\[", text), name


def test_privileged_reviews_use_target_trigger_and_trusted_base():
    for name in (REVIEW, APPROVE):
        text = read(name)
        assert re.search(r"^  pull_request_target:", text, re.M), name
        for block in checkout_blocks(text):
            if "path: .review-ctx/head" not in block:
                assert "github.event.pull_request.base.sha" in block, block


def test_review_prompts_load_org_and_scope_from_trusted_config():
    text = read(REVIEW)
    jobs = re.split(r"^  [\w-]+:\n", text, flags=re.M)
    review_jobs = [job for job in jobs if "steps.config.outputs.org" in job]
    assert len(review_jobs) == 2
    for job in review_jobs:
        config_start = job.index("- name: Read trusted organization config")
        head_start = job.index("path: .review-ctx/head")
        assert config_start < head_start
        step = job[config_start:head_start]
        assert "id: config" in step
        assert "python3 -I -" in step
        assert "plugins/knowledge/knowledge.json" in step
        assert "json.load(" in step
        assert "json.dumps(" in step
        assert "GITHUB_OUTPUT" in step
        assert "steps.config.outputs.scope" in job


def test_workflow_headers_list_setup_requirements():
    for name, text in all_workflows().items():
        header = text.split("name:", 1)[0].lower()
        for requirement in ("secrets:", "variables:", "labels:"):
            assert requirement in header, (name, requirement)


def test_secret_scan_is_self_contained():
    text = read(CHECK)
    job = text.split("  gitleaks:", 1)[1].split("\n  knowledge:", 1)[0]
    assert "runs-on:" in job
    assert "gitleaks" in job
    assert "uses:" not in job or "uses: actions/checkout@" in job
    assert "@sha256:" in job or ("sha256sum" in job and "gitleaks_" in job)


def test_review_tools_cannot_change_prs():
    banned = ("gh pr edit", "gh pr review", "gh pr merge", "gh api", "gh label")
    seen = 0
    for name, text in all_workflows().items():
        for m in re.finditer(r"--allowedTools\s+\"([^\"]*)\"", text):
            seen += 1
            for item in banned:
                assert item not in m.group(1), "%s allows %s" % (name, item)
    assert seen > 0


def test_pr_code_workflow_has_no_api_key():
    assert "ANTHROPIC_API_KEY" not in read(CHECK)


def test_check_pr_is_read_only_by_default():
    text = read(CHECK)
    top = re.search(r"^permissions:\n((?:[ \t]+.*\n)+)", text, re.M)
    assert top, "no top-level permissions block"
    assert re.search(r"^\s+contents:\s*read\s*$", top.group(1), re.M)
