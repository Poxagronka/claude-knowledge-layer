"""Hubs named in tool calls (PostToolUse), the SubagentStart pointer, and hub id parsing."""
import importlib.util
import io
import json
import os

import pytest
from conftest import write_config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("hook", os.path.join(ROOT, "plugins", "knowledge", "scripts", "hook.py"))
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)
import tool_hubs  # noqa: E402  (hook put the scripts folder on sys.path)
import vault_hubs  # noqa: E402

SID = "s1"
COMPETITOR = """---
aliases: [Acme Studio, AB, Puzzle One]
---
# Acme Studio (Puzzle One) (competitor)

Makes Puzzle One!.

## Facts

- Top apps: Puzzle One! (0000000001 / com.acme.puzzleone), Puzzle Two! (1234567890 / com.acme.puzzletwo). (source: x)
"""
APP = """---
aliases: [Shop-App]
---
# Shop-App (app)

- iOS App Store id 0000000002 (store title "Shop-App"). In the table its app_id is id0000000002. (source: url:https://itunes.apple.com/lookup?id=0000000002)
- The Android package is `com.example.shopapp`, see https://play.google.com/store/apps/details?id=com.example.shopapp.
- Wiki page 9876543210 describes the launch plan.
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    plugin = tmp_path / "plugin-abc123"
    ents = plugin / "vault" / "entities"
    ents.mkdir(parents=True)
    write_config(plugin)
    (ents / "competitor-acme-studio.md").write_text(COMPETITOR)
    (ents / "app-shop-app.md").write_text(APP)
    data = tmp_path / "data"
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(data))
    monkeypatch.delenv(hook.OPT_OUT_ENV, raising=False)
    monkeypatch.delenv(vault_hubs.SELECT_OPT_OUT_ENV, raising=False)
    return plugin, data


def run(sub, event, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    assert hook.main([sub]) == 0
    return capsys.readouterr().out


def bash(cmd, **extra):
    return dict({"session_id": SID, "tool_name": "Bash", "tool_input": {"command": cmd}}, **extra)


def session(data):
    return json.loads((data / "sessions" / (SID + ".json")).read_text())


def injected(out):
    if not out:
        return None
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    return [ln[4:] for ln in ctx.splitlines() if ln.startswith("### ")]


def test_hub_ids_from_top_games_and_app_store_lines():
    ids = vault_hubs.hub_ids(COMPETITOR)
    assert ids == ["0000000001", "com.acme.puzzleone", "1234567890", "com.acme.puzzletwo"]
    app_ids = vault_hubs.hub_ids(APP)
    assert app_ids == ["0000000002", "com.example.shopapp"]
    assert "9876543210" not in app_ids  # a Wiki page id is no store id
    rec = vault_hubs.hub_record("entities/app-shop-app", [], APP)
    assert rec["ids"] == app_ids and "0000000002" not in rec["names"]


def test_bundle_id_in_bash_injects_once(env, monkeypatch, capsys):
    _, data = env
    out = run("track", bash("report --app com.acme.puzzleone"), monkeypatch, capsys)
    assert injected(out) == ["entities/competitor-acme-studio"]
    assert json.loads(out)["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "Acme Studio" in json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert run("track", bash("report --app com.acme.puzzleone"), monkeypatch, capsys) == ""
    sess = session(data)
    assert sess["injected"]["entities/competitor-acme-studio"]["tool"] == 1
    assert sess["injected"]["entities/competitor-acme-studio"]["s2"] == 0
    assert "entities/competitor-acme-studio" in sess["shown"]
    assert sess["shown_tool"]["main"] == ["entities/competitor-acme-studio"]


def test_other_agent_gets_the_hub_again(env, monkeypatch, capsys):
    run("track", bash("echo com.acme.puzzleone"), monkeypatch, capsys)
    out = run("track", bash("echo com.acme.puzzleone", agent_id="a1"), monkeypatch, capsys)
    assert injected(out) == ["entities/competitor-acme-studio"]
    assert run("track", bash("echo com.acme.puzzleone", agent_id="a1"), monkeypatch, capsys) == ""
    assert session(env[1])["shown_tool"] == {"main": ["entities/competitor-acme-studio"], "a1": ["entities/competitor-acme-studio"]}


def test_compact_lets_main_get_the_hub_again(env, monkeypatch, capsys):
    run("track", bash("echo com.acme.puzzleone"), monkeypatch, capsys)
    run("track", bash("echo com.acme.puzzleone", agent_id="a1"), monkeypatch, capsys)
    run("session-start", {"session_id": bash("")["session_id"], "source": "compact"}, monkeypatch, capsys)
    assert session(env[1])["shown_tool"] == {"a1": ["entities/competitor-acme-studio"]}
    assert injected(run("track", bash("echo com.acme.puzzleone"), monkeypatch, capsys)) == ["entities/competitor-acme-studio"]


def test_selector_shown_and_read_hubs_are_skipped(env, monkeypatch, capsys):
    plugin, data = env
    run("track", bash("echo nothing here"), monkeypatch, capsys)
    sess = session(data) if (data / "sessions" / (SID + ".json")).exists() else hook.new_session(SID)
    sess["shown"] = ["entities/competitor-acme-studio"]
    sess["read_files"] = ["entities/app-shop-app.md"]
    hook.write_json(hook.session_path(SID), sess)
    assert run("track", bash("echo com.acme.puzzleone 0000000002"), monkeypatch, capsys) == ""


def test_vault_reads_do_not_inject(env, monkeypatch, capsys):
    plugin, _ = env
    path = str(plugin / "vault" / "entities" / "app-shop-app.md")
    bash_path = path.replace("\\", "/")
    read = {"session_id": SID, "tool_name": "Read", "tool_input": {"file_path": path}}
    assert run("track", read, monkeypatch, capsys) == ""
    grep = {"session_id": SID, "tool_name": "Grep", "tool_input": {"pattern": "com.acme.puzzleone", "path": str(plugin / "vault")}}
    assert run("track", grep, monkeypatch, capsys) == ""
    assert run("track", bash("cat %s | grep com.acme.puzzleone" % bash_path), monkeypatch, capsys) == ""


def test_mcp_tool_name_matches_store_id(env, monkeypatch, capsys):
    event = {"session_id": SID, "tool_name": "mcp__store__app", "tool_input": {"app_ids": ["0000000002"]}}
    assert injected(run("track", event, monkeypatch, capsys)) == ["entities/app-shop-app"]


def test_wiki_id_and_digit_boundary_do_not_match(env, monkeypatch, capsys):
    assert run("track", bash("fetch 9876543210 and 00000000021"), monkeypatch, capsys) == ""


def test_alias_whole_word_and_short_alias_ignored(env, monkeypatch, capsys):
    assert run("track", bash("echo AB"), monkeypatch, capsys) == ""  # 2 chars: too short
    assert run("track", bash("grep -i 'acme studios' x"), monkeypatch, capsys) == ""  # not a whole word
    assert injected(run("track", bash("grep -i 'acme studio' x"), monkeypatch, capsys)) == ["entities/competitor-acme-studio"]


def test_title_and_slug_words_do_not_match(env, monkeypatch, capsys):
    assert run("track", bash("echo competitor hungry"), monkeypatch, capsys) == ""


def test_budget_caps_hubs_and_chars(env, monkeypatch, capsys):
    plugin, _ = env
    for n in range(5):
        text = "---\naliases: [bigalias%d]\n---\n# Big %d\n\n%s\n" % (n, n, "word " * 2000)
        (plugin / "vault" / "entities" / ("big-%d.md" % n)).write_text(text)
    out = run("track", bash("echo bigalias0 bigalias1 bigalias2 bigalias3 bigalias4"), monkeypatch, capsys)
    assert len(injected(out)) == tool_hubs.MAX_HUBS
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert len(ctx) <= tool_hubs.MAX_CHARS
    assert ctx.count("[cut: read the rest in %s" % os.path.join(str(plugin / "vault"), "entities/big-")) == 3


def test_switches_turn_injection_off(env, monkeypatch, capsys):
    monkeypatch.setenv(vault_hubs.SELECT_OPT_OUT_ENV, "off")
    assert run("track", bash("echo com.acme.puzzleone"), monkeypatch, capsys) == ""
    monkeypatch.delenv(vault_hubs.SELECT_OPT_OUT_ENV)
    # The telemetry opt-out (or an empty issues_repo) does not silence the local hubs.
    monkeypatch.setenv(hook.OPT_OUT_ENV, "off")
    assert injected(run("track", bash("echo com.acme.puzzleone"), monkeypatch, capsys)) == ["entities/competitor-acme-studio"]


def test_hub_index_cache_follows_hub_changes(env, monkeypatch, capsys):
    plugin, _ = env
    run("track", bash("echo com.acme.puzzleone"), monkeypatch, capsys)
    assert os.path.exists(os.path.join(hook.data_dir(), "tool_hubs.json"))
    (plugin / "vault" / "entities" / "competitor-new.md").write_text("# New\n\n- Top apps: X (1111111111 / com.new.app)\n")
    assert injected(run("track", bash("echo com.new.app"), monkeypatch, capsys)) == ["entities/competitor-new"]


def test_subagent_start_lists_shown_hubs(env, monkeypatch, capsys):
    _, data = env
    event = {"session_id": SID, "agent_id": "a1", "agent_type": "Explore"}
    assert run("subagent-start", event, monkeypatch, capsys) == ""  # no session file yet
    run("track", bash("echo com.acme.puzzleone 0000000002"), monkeypatch, capsys)
    sess = session(data)
    sess["shown"] = ["api/rules#^R1"] + sess["shown"]
    hook.write_json(hook.session_path(SID), sess)
    out = run("subagent-start", event, monkeypatch, capsys)
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert json.loads(out)["hookSpecificOutput"]["hookEventName"] == "SubagentStart"
    assert str(env[0] / "vault") in ctx and "Read the matching hub with the Read tool" in ctx
    assert "- entities/app-shop-app: Shop-App (app)" in ctx and "api/rules" not in ctx
    assert ctx.index("competitor-acme-studio") < ctx.index("app-shop-app")  # appended last, listed first
    assert "Makes Puzzle One" not in ctx and len(ctx) <= tool_hubs.SUBAGENT_CHARS
    monkeypatch.setenv(vault_hubs.SELECT_OPT_OUT_ENV, "off")
    assert run("subagent-start", event, monkeypatch, capsys) == ""


def test_bash_reads_ignores_paths_that_are_no_file(env):
    v = str(env[0] / "vault").replace("\\", "/")
    ent = "%s/entities/app-shop-app.md" % v
    loop = "for f in a b; do\n  cat \"%s/entities/$f.md\"\ndone" % v
    for cmd in (
        loop,
        "head -3 %s/entities/*.md" % v,
        "cat %s/entities/a?.md" % v,
        "cat %s/entities/{a,b}.md" % v,
        'cat "%s/entities/see notes about it.md"' % v,
        "cat %s/entities/missing.md" % v,
    ):
        assert hook.bash_reads(cmd) == [], cmd
    assert hook.bash_reads("cat %s" % ent) == [("entities/app-shop-app.md", True)]


def test_subagent_read_is_not_recorded(env, monkeypatch, capsys):
    path = str(env[0] / "vault" / "entities" / "app-shop-app.md")
    for event in (
        {"session_id": SID, "tool_name": "Read", "tool_input": {"file_path": path}},
        bash("cat %s" % path),
    ):
        run("track", dict(event, agent_id="sub1"), monkeypatch, capsys)
    assert not hook.load_session(SID).get("hits")


def test_token_after_newline_matches(env, monkeypatch, capsys):
    out = run("track", bash("echo a\ncom.acme.puzzleone\n\tAcme Studio"), monkeypatch, capsys)
    assert injected(out) == ["entities/competitor-acme-studio"]









