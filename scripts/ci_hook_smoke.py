#!/usr/bin/env python3
"""CI smoke test of the plugin hooks on a real OS (the plugin-matrix job runs it on every leg).

Runs each hook of plugins/knowledge/hooks/hooks.json as the host does, with a realistic stdin event.
Reads both hook forms: shell form ("command" string) and exec form ("command" + "args").
Checks: exit 0, no Traceback in stderr, the SessionStart banner, no swallowed errors in errors.json.
On Windows it also checks that the integrity check (autoupdate.broken) is False after the CRLF checkout.
"""
import json
import os
import shlex
import stat
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(ROOT, "plugins", "knowledge")
WIN = os.name == "nt"
STUB = '#!%s\nimport sys\nsys.stdin.read()\nprint(\'{"need":0,"picks":[]}\')\n'


def expand(text):
    return text.replace("${CLAUDE_PLUGIN_ROOT}", PLUGIN)


def argv_of(hook):
    """The argv of a hook: exec form is used as is, shell form is split. python3 becomes this interpreter."""
    if "args" in hook:
        argv = [expand(hook["command"])] + [expand(a) for a in hook["args"]]
    else:
        argv = [expand(a) for a in shlex.split(hook["command"], posix=not WIN)]
        argv = [a[1:-1] if len(a) > 1 and a[0] == a[-1] == '"' else a for a in argv]
    if argv[0] in ("python3", "python"):
        argv[0] = sys.executable
    return argv


def events(tmp):
    transcript = os.path.join(tmp, "transcript.jsonl")
    open(transcript, "w").close()
    vault_file = os.path.join(PLUGIN, "vault", "_index.md")
    base = {"session_id": "ci-smoke", "transcript_path": transcript, "cwd": tmp}
    prompt = "What do we know about our vendors and the metrics we track for them?"
    extra = {
        "SessionStart": {"source": "startup"},
        "UserPromptSubmit": {"prompt": prompt},
        "PostToolUse": {"tool_name": "Read", "tool_input": {"file_path": vault_file},
                        "tool_response": {"file": {"filePath": vault_file}}},
        "Stop": {"stop_hook_active": False},
        "PreCompact": {"trigger": "auto"},
        "SubagentStart": {"agent_id": "a1", "agent_type": "general-purpose"},
        "SessionEnd": {"reason": "other"},
    }
    return [(name, dict(base, hook_event_name=name, **extra[name])) for name in extra]


def hooks_by_event():
    with open(os.path.join(PLUGIN, "hooks", "hooks.json"), encoding="utf-8") as fh:
        spec = json.load(fh)["hooks"]
    return {ev: [h for group in groups for h in group["hooks"]] for ev, groups in spec.items()}


def make_env(tmp):
    data = os.path.join(tmp, "data")
    os.makedirs(data)
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=PLUGIN, CLAUDE_PLUGIN_DATA=data,
               KNOWLEDGE_AUTOUPDATE="0", KNOWLEDGE_LOCAL="0", PYTHONUTF8="1")
    if not WIN:
        # CreateProcess finds only claude.exe, so a stub works on POSIX only.
        bindir = os.path.join(tmp, "bin")
        os.makedirs(bindir)
        stub = os.path.join(bindir, "claude")
        with open(stub, "w") as fh:
            fh.write(STUB % sys.executable)
        os.chmod(stub, os.stat(stub).st_mode | stat.S_IXUSR)
        env["PATH"] = bindir + os.pathsep + env["PATH"]
    return env, data


def check_errors(data):
    path = os.path.join(data, "errors.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        counts = json.load(fh)
    # Windows has no claude stub: the selector may record a missing nested `claude`.
    return ["errors.json: %s=%s" % kv for kv in counts.items() if not (WIN and kv[0].startswith("haiku:"))]


def main():
    fails = []
    registered = hooks_by_event()
    tmp = tempfile.mkdtemp()
    try:
        env, data = make_env(tmp)
        sent = events(tmp)
        for name, event in sent:
            for hook in registered.get(name, []):
                done = subprocess.run(argv_of(hook), input=json.dumps(event), capture_output=True, text=True,
                                      encoding="utf-8", env=env, cwd=tmp, timeout=120)
                print("%-17s rc=%d out=%d bytes err=%d bytes" % (name, done.returncode, len(done.stdout), len(done.stderr)))
                if done.returncode != 0:
                    fails.append("%s: exit %d" % (name, done.returncode))
                if "Traceback" in done.stderr:
                    fails.append("%s: Traceback in stderr:\n%s" % (name, done.stderr))
                if name == "SessionStart" and "knowledge vault" not in done.stdout:
                    fails.append("SessionStart: banner missing, stdout=%r" % done.stdout[:200])
        fails += ["hooks.json has no %s hook" % n for n, _ in sent if n not in registered]
        fails += check_errors(data)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if WIN:
        sys.path.insert(0, os.path.join(PLUGIN, "scripts"))
        import autoupdate
        if autoupdate.broken():
            fails.append("autoupdate.broken() is True after the CRLF checkout: integrity.json hashes differ")
    for line in fails:
        print("FAIL", line)
    print("hook smoke %s" % ("FAILED" if fails else "OK"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
