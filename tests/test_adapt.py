"""Test template adaptation on an isolated template copy."""

import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/adapt.py"


@pytest.fixture
def template(tmp_path):
    script = tmp_path / "scripts/adapt.py"
    script.parent.mkdir()
    shutil.copyfile(SCRIPT, script)
    files = {
        "plugins/knowledge/knowledge.json": {
            "org": "Example Organization",
            "issues_repo": "",
            "marketplace": "knowledge-layer",
            "root_hub": "org-example",
            "scope": "Shared knowledge.",
            "directory": {"base_env": "DIRECTORY_BASE"},
        },
        ".claude-plugin/marketplace.json": {
            "name": "knowledge-layer",
            "plugins": [
                {
                    "name": "knowledge",
                    "source": {
                        "source": "git-subdir",
                        "url": "https://github.com/example/template.git",
                        "path": "plugins/knowledge",
                    },
                },
                {"name": "other", "source": {"url": "unchanged"}},
            ],
        },
    }
    for name, data in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n")
    owners = tmp_path / ".github/CODEOWNERS"
    owners.parent.mkdir()
    owners.write_text(
        "# Contact @example-owner\n"
        "* @example-owner # Preserve @example-comment\n"
        "/docs/  @example-team/docs\n"
        "\n"
    )
    return tmp_path


def run_adapt(template, *args):
    return subprocess.run(
        [sys.executable, str(template / "scripts/adapt.py"),
         "--org", "Sample Organization", "--issues-repo", "example/issues", *args],
        check=True, capture_output=True, text=True, cwd=template,
    )


def read_json(template, name):
    return json.loads((template / name).read_text())


def snapshot(template):
    return {str(path.relative_to(template)): path.read_bytes()
            for path in template.rglob("*") if path.is_file()}


def test_adapt_all_options(template):
    result = run_adapt(
        template, "--plugin-repo", "example/plugin", "--marketplace", "sample-market",
        "--maintainer", "sample-maintainer", "--root-hub", "team-example",
    )
    config = read_json(template, "plugins/knowledge/knowledge.json")
    assert config["org"] == "Sample Organization"
    assert config["issues_repo"] == "example/issues"
    assert config["marketplace"] == "sample-market"
    assert config["root_hub"] == "team-example"
    assert config["scope"] == "Shared knowledge."
    assert config["directory"] == {"base_env": "DIRECTORY_BASE"}
    marketplace = read_json(template, ".claude-plugin/marketplace.json")
    assert marketplace["name"] == "sample-market"
    assert marketplace["plugins"][0]["source"] == {
        "source": "git-subdir", "url": "https://github.com/example/plugin.git",
        "path": "plugins/knowledge",
    }
    assert marketplace["plugins"][1]["source"]["url"] == "unchanged"
    assert (template / ".github/CODEOWNERS").read_text() == (
        "# Contact @example-owner\n"
        "* @sample-maintainer # Preserve @example-comment\n"
        "/docs/  @sample-maintainer\n\n"
    )
    assert "Next steps:" in result.stdout


def test_plugin_repo_defaults_to_issues_repo(template):
    run_adapt(template)
    marketplace = read_json(template, ".claude-plugin/marketplace.json")
    assert marketplace["plugins"][0]["source"]["url"] == (
        "https://github.com/example/issues.git"
    )


def test_dry_run_preserves_all_bytes(template):
    before = snapshot(template)
    result = run_adapt(
        template, "--dry-run", "--marketplace", "sample-market",
        "--maintainer", "sample-maintainer", "--root-hub", "team-example",
    )
    assert snapshot(template) == before
    assert "Would update plugins/knowledge/knowledge.json" in result.stdout
    assert "Would update .claude-plugin/marketplace.json" in result.stdout
    assert "Would update .github/CODEOWNERS" in result.stdout
    assert "Next steps:" in result.stdout


def test_repeated_adaptation_is_idempotent(template):
    args = ("--marketplace", "sample-market", "--maintainer", "sample-maintainer")
    run_adapt(template, *args)
    before = snapshot(template)
    mtimes = {path: path.stat().st_mtime_ns for path in template.rglob("*")
              if path.is_file()}
    result = run_adapt(template, *args)
    assert snapshot(template) == before
    assert all(path.stat().st_mtime_ns == mtime for path, mtime in mtimes.items())
    assert "Updated" not in result.stdout


def test_omitted_optional_settings_are_preserved(template):
    owners = (template / ".github/CODEOWNERS").read_bytes()
    run_adapt(template)
    config = read_json(template, "plugins/knowledge/knowledge.json")
    assert config["marketplace"] == "knowledge-layer"
    assert config["root_hub"] == "org-example"
    assert (template / ".github/CODEOWNERS").read_bytes() == owners


def test_maintainer_replaces_multiple_owners_and_preserves_patterns(template):
    owners = template / ".github/CODEOWNERS"
    owners.write_text(
        "# Team @example-team/docs\n"
        "/packages/@example/ @example-owner\t@example-team/docs # @example-comment\n"
        "  /docs/  @example-owner\n"
    )
    run_adapt(template, "--maintainer", "sample-maintainer")
    assert owners.read_text() == (
        "# Team @example-team/docs\n"
        "/packages/@example/ @sample-maintainer\t@sample-maintainer # @example-comment\n"
        "  /docs/  @sample-maintainer\n"
    )
