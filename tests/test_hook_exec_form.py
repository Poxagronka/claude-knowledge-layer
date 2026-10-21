"""hooks.json exec form, PowerShell read tracking and Windows path helper."""
import importlib.util
import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(ROOT, "plugins", "knowledge")
spec = importlib.util.spec_from_file_location("hook", os.path.join(PLUGIN, "scripts", "hook.py"))
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


def all_hooks():
    with open(os.path.join(PLUGIN, "hooks", "hooks.json"), encoding="utf-8") as f:
        data = json.load(f)
    return [(ev, h) for ev, groups in data["hooks"].items() for g in groups for h in g["hooks"]], data


def test_every_hook_is_exec_form():
    hooks, _ = all_hooks()
    assert hooks
    for ev, h in hooks:
        assert h["command"] == "python3", ev
        assert h["args"][:2] == ["-X", "utf8"], ev
        assert h["args"][2].startswith("${CLAUDE_PLUGIN_ROOT}/scripts/"), ev


def test_post_tool_use_matches_powershell():
    _, data = all_hooks()
    assert "PowerShell" in data["hooks"]["PostToolUse"][0]["matcher"].split("|")


def test_user_prompt_submit_keeps_timeout():
    _, data = all_hooks()
    h = data["hooks"]["UserPromptSubmit"][0]["hooks"][0]
    assert h["timeout"] == 30 and h["statusMessage"]


@pytest.fixture
def vault_file(tmp_path, monkeypatch):
    plugin = tmp_path / "plugin-abc"
    (plugin / "vault").mkdir(parents=True)
    f = plugin / "vault" / "a.md"
    f.write_text("x")
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "data"))
    return f


def test_powershell_get_content_is_a_full_read(vault_file):
    assert hook.powershell_reads(f'Get-Content "{vault_file}"') == [("a.md", True)]
    assert hook.powershell_reads(f"gc -Path '{vault_file}'") == [("a.md", True)]
    assert hook.powershell_reads(f"type {vault_file}") == [("a.md", True)]


def test_powershell_partial_and_piped_reads(vault_file):
    assert hook.powershell_reads(f"Get-Content {vault_file} -TotalCount 5") == [("a.md", False)]
    assert hook.powershell_reads(f"Get-Content {vault_file} | Select-String x") == [("a.md", False)]


def test_powershell_ignores_other_commands_and_files(vault_file, tmp_path):
    other = tmp_path / "o.md"
    other.write_text("x")
    assert hook.powershell_reads(f"Remove-Item {vault_file}") == []
    assert hook.powershell_reads(f"Get-Content {other}") == []
    assert hook.powershell_reads("Get-Content missing.md") == []


def test_touches_vault_powershell(vault_file):
    assert hook.touches_vault("PowerShell", {"command": f"cat {vault_file}"})
    assert not hook.touches_vault("PowerShell", {"command": "Get-Date"})


@pytest.mark.skipif(os.name == "nt", reason="patching os.name to posix breaks pathlib on Windows")
def test_absolute_path_posix_unchanged(monkeypatch):
    monkeypatch.setattr(hook.os, "name", "posix")
    assert hook.absolute_path("/c/x.md") == "/c/x.md"
    assert hook.absolute_path("C:\\x.md") is None
    assert hook.absolute_path("rel/x.md") is None


@pytest.mark.parametrize("raw", ["C:\\v\\x.md", "C:/v/x.md", "/c/v/x.md"])
def test_absolute_path_windows(monkeypatch, raw):
    monkeypatch.setattr(hook.os, "name", "nt")
    assert hook.absolute_path(raw).replace("\\", "/") == "C:/" + raw[3:].replace("\\", "/")
    assert hook.absolute_path("rel\\x.md") is None
