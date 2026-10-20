#!/usr/bin/env python3
"""Knowledge plugin hooks: index injection, usage tracking, telemetry sender.

Every subcommand fails open: any error prints nothing and exits 0.
"""
import contextlib
import fcntl
import json
import os
import re
import shlex
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402
import local_rank  # noqa: E402
import issue_queue  # noqa: E402
import select_stats  # noqa: E402
from privacy import SENSITIVE  # noqa: E402
from plugin_state import (  # noqa: E402,F401
    CAPTURE_ENV,
    DROP_SECONDS,
    OPT_OUT_ENV,
    WORKER_ENV,
    active,
    capture_off,
    data_dir,
    in_vault,
    load_session,
    locked,
    new_session,
    note_error,
    now_iso,
    opted_out,
    queue_dir,
    read_json,
    repo,
    remove_session,
    root,
    session_path,
    vault,
    vault_rel,
    write_json,
)

MAX_STDOUT = 8900
STALE_SECONDS = 2 * 3600
MAX_SPAWNS = 2
MAX_STALE = 20
MAX_MISSES = 50
MAX_BODY_BYTES = 20000
BODY_FILE_RE = re.compile(r"(?:--body-file|-F)[ =]+['\"]?([^\s'\"]+)")
CHANGE_TYPE_RE = re.compile(r"### Change type\s*\n+\s*(\w+)")
TARGET_RE = re.compile(r"### Target\s*\n+\s*(\S+)")
READ_CMDS = ("cat", "head", "sed", "less", "bat")


@contextlib.contextmanager
def idle_locked():
    """Yield True when idle.json.lock is held, False after ~100 ms of contention: skip, never block."""
    with open(os.path.join(data_dir(), "idle.json.lock"), "a") as lock:
        held = False
        for _ in range(10):
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                held = True
                break
            except OSError:
                time.sleep(0.01)
        yield held


ASSIGN_RE = re.compile(r"[A-Za-z_]\w*=")
VAR_RE = re.compile(r"\$(?:(\w+)|\{(\w+)\})")


def _segments(cmd):
    """Split a shell command into (words, piped) simple segments, or None if unparseable."""
    try:
        lex = shlex.shlex(cmd.replace("\n", " ; "), posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:
        return None
    segs, words = [], []
    for tok in tokens:
        if tok.strip(";&|") == "":
            segs.append((words, tok in ("|", "|&")))
            words = []
        else:
            words.append(tok)
    segs.append((words, False))
    return segs


def bash_reads(cmd):
    """(vault-relative file, fully read) pairs for cat/head/sed/less/bat segments of a command."""
    segs = _segments(cmd)
    env, found = {}, []
    for words, piped in segs or []:
        if words and words[0] == "export":
            words = words[1:]
        if words and all(ASSIGN_RE.match(w) for w in words):
            for w in words:
                name, _, val = w.partition("=")
                env[name] = VAR_RE.sub(lambda m: env.get(m.group(1) or m.group(2), m.group(0)), val)
            continue
        if not words or words[0] not in READ_CMDS:
            continue
        redirected = any(re.fullmatch(r"[<>&]+", w) and re.search(r"[<>]", w) for w in words)
        full = words[0] in ("cat", "less", "bat") and not piped and not redirected
        for w in words[1:]:
            if w.endswith(".md"):
                path = os.path.expanduser(VAR_RE.sub(lambda m: env.get(m.group(1) or m.group(2), m.group(0)), w))
                if os.path.isabs(path) and in_vault(path):
                    found.append((vault_rel(path), full))
    return found


def stale_target(cmd):
    """Vault id named by a fix or remove proposal in a `gh issue create` command, or None."""
    text = cmd
    match = BODY_FILE_RE.search(cmd)
    if match:
        with contextlib.suppress(OSError), open(os.path.expanduser(match.group(1))) as fh:
            text += "\n" + fh.read(MAX_BODY_BYTES)
    kind, target = CHANGE_TYPE_RE.search(text), TARGET_RE.search(text)
    if not target or (kind and kind.group(1).lower() not in ("fix", "remove")):
        return None
    ident = target.group(1).strip("`[]")
    if ident.startswith("new:"):
        return None
    ident = re.sub(r"^(?:plugins/knowledge/)?vault/", "", ident)
    ident = re.sub(r"\.md(?=#|$)", "", ident)
    return ident.rsplit("/", 1)[-1] if ident.startswith("entities/") else ident


def zero_matches(resp):
    # The one shape seen live: {"mode": "content", "numFiles": 0, "filenames": [], ...}
    return isinstance(resp, dict) and resp.get("numFiles") == 0


def spawn_sender(path, mode="send"):
    subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), mode, path],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def sweep_sessions(sid):
    sdir = os.path.join(data_dir(), "sessions")
    now = time.time()
    spawns = 0
    for name in sorted(os.listdir(sdir)):
        path = os.path.join(sdir, name)
        if not name.endswith((".json", ".sending")):
            continue
        try:
            age = now - os.path.getmtime(path)
        except OSError:
            continue
        if age > DROP_SECONDS:
            remove_session(path)
        elif name.endswith(".json") and name != "%s.json" % sid and age > STALE_SECONDS and not opted_out():
            if spawns < MAX_SPAWNS:
                spawn_sender(path)
                spawns += 1


def session_start(event):
    org = config.text("org") or config.DEFAULTS["org"]
    root_hub = config.text("root_hub")
    header = (
        "# %s knowledge vault\n"
        "Vault path: %s\n"
        "Queue path: %s\n"
        "Use the `knowledge` skill for %s questions. "
        "Search the vault with the Grep tool with `path` set to the vault. "
        "%s%s\n\n" % (
            org, vault(), queue_dir(), org,
            ("Start at the `%s` hub. " % root_hub) if root_hub else "",
            "Issues repo: %s. Propose edits with the `knowledge-propose` skill." % repo() if repo()
            else "Issue filing is off: set issues_repo in knowledge.json.",
        )
    )
    try:
        with open(os.path.join(vault(), "_index.md")) as fh:
            index = fh.read()
    except OSError:
        index = ""
    print((header + index)[:MAX_STDOUT])
    local_rank.ensure(vault())
    local_rank.prewarm(vault())
    try:
        import autoupdate
        autoupdate.maybe_update()
    except Exception as exc:
        note_error("autoupdate", exc)
    spawn_flush()
    sid = event.get("session_id", "")
    if not sid:
        return
    if not os.path.exists(session_path(sid)):
        write_json(session_path(sid), new_session(sid))
    elif event.get("source") in ("compact", "clear"):
        # The context is gone, so the selector may show its entries again.
        with locked(sid):
            sess = load_session(sid)
            sess["before_compact"] = sess.get("shown", [])
            sess["shown"] = []
            sess["read_files"] = []
            write_json(session_path(sid), sess)
    sweep_sessions(sid)


def note_reads(sess, reads):
    for rel, full in reads:
        select_stats.note_read(sess, rel, full)
        sess["hits"][rel] = sess["hits"].get(rel, 0) + 1
        if full:
            files = sess.setdefault("read_files", [])
            if rel not in files:
                files.append(rel)
            if any(i.split("#")[0] + ".md" == rel for i in sess.get("before_compact", [])):
                sess["reread_after_compact"] = sess.get("reread_after_compact", 0) + 1


def track(event):
    if opted_out():
        return
    sid = event["session_id"]
    with contextlib.suppress(OSError):
        # A live session must never look stale to the sweep in session_start.
        os.utime(session_path(sid))
    tool, inp = event.get("tool_name"), event.get("tool_input") or {}
    reads = []
    pat = None
    proposal = capture = comment = False
    stale = None
    if tool == "Read":
        target = inp.get("file_path") or ""
        if not (target and in_vault(target)):
            return
        reads = [(vault_rel(target), "offset" not in inp and "limit" not in inp)]
    elif tool == "Grep":
        if not (inp.get("path") and in_vault(inp["path"])):
            return
        if not zero_matches(event.get("tool_response")):
            return
        pat = str(inp.get("pattern", ""))[:100]
        if not pat or SENSITIVE.search(pat):
            return
    elif tool == "Bash":
        cmd = str(inp.get("command", ""))
        reads = bash_reads(cmd)
        if "gh issue create" in cmd:
            proposal = "knowledge-proposal" in cmd
            capture = re.search(r"--label[ =]capture\b", cmd) is not None
            stale = stale_target(cmd) if proposal else None
        comment = "gh issue comment" in cmd and bool(repo()) and repo() in cmd
        if comment:
            stale = stale_target(cmd)
        if not (reads or proposal or capture or comment):
            return
    else:
        return
    with locked(sid):
        sess = load_session(sid)
        note_reads(sess, reads)
        if pat and pat not in sess["misses"]:
            sess["misses"] = (sess["misses"] + [pat])[-MAX_MISSES:]
        if proposal:
            sess["proposals"] += 1
        if capture:
            sess["captures"] = sess.get("captures", 0) + 1
        if comment:
            sess["comments"] = sess.get("comments", 0) + 1
        reports = sess.setdefault("stale_reports", [])
        if stale and stale not in reports and len(reports) < MAX_STALE:
            reports.append(stale)
        write_json(session_path(sid), sess)


def bump_idle():
    idle = os.path.join(data_dir(), "idle.json")
    with idle_locked() as held:
        if held:
            write_json(idle, read_json(idle, 0) + 1)


def session_end(event):
    spawn_flush()
    sid = event.get("session_id", "")
    import capture
    capture.spawn_worker_if(sid, event.get("transcript_path", ""), "session_end", capture.FLUSH_MIN_BYTES)
    path = session_path(sid)
    with locked(sid):
        sess = read_json(path, None)
        if sess is None:
            return
        if opted_out():
            remove_session(path)
            return
        if not active(sess):
            remove_session(path)
            bump_idle()
            return
    spawn_sender(path)


def stop(event):
    """Print nothing and never block. A detached worker files the capture in the background."""
    sid = event.get("session_id")
    if capture_off() or not sid:
        return
    import capture
    capture.spawn_worker_if(sid, event.get("transcript_path", ""), "volume", capture.CAPTURE_MIN_BYTES)


def spawn_flush():
    try:
        if repo() and any(n.endswith(".json") for n in os.listdir(queue_dir())):
            spawn_sender(queue_dir(), "flush")
    except Exception as exc:
        note_error("flush", exc)


def pre_compact(event):
    # Interim snapshot: the session file stays, counters keep running.
    sid = event.get("session_id", "")
    import capture
    capture.spawn_worker_if(sid, event.get("transcript_path", ""), "precompact", capture.FLUSH_MIN_BYTES)
    with locked(sid):
        sess = read_json(session_path(sid), None)
        if sess is None or opted_out() or not active(sess):
            return
    spawn_sender(session_path(sid), "send-interim")


def find_issue(login, target):
    cache = os.path.join(data_dir(), "issue.json")
    cached = read_json(cache, {})
    if cached.get("login") == login and cached.get("number"):
        return cached["number"]
    title = "telemetry: %s" % login
    found = json.loads(issue_queue.gh(
        "issue", "list", "-R", target, "--label", "telemetry", "--state", "all",
        "--search", 'in:title "%s"' % title, "--json", "number,title",
    ) or "[]")
    exact = [i["number"] for i in found if i.get("title") == title]
    if exact:
        number = exact[0]
    else:
        url = issue_queue.gh(
            "issue", "create", "-R", target, "--title", title, "--label", "telemetry",
            "--body", "Automatic usage telemetry from the knowledge plugin. See README.",
        )
        number = int(url.rstrip("/").rsplit("/", 1)[-1])
    write_json(cache, {"login": login, "number": number})
    return number


def send(path, final=True):
    target = repo()
    if not target:
        return  # issue filing is off
    sending = path + ".sending" if final else path
    try:
        if final:
            os.rename(path, sending)
    except OSError:
        return
    try:
        sess = read_json(sending, {})
        if not active(sess):
            # An empty session left by a crash: count it as idle, send nothing.
            remove_session(sending)
            bump_idle()
            return
        idle_path = os.path.join(data_dir(), "idle.json")
        idle = read_json(idle_path, 0)
        errors_path = os.path.join(data_dir(), "errors.json")
        payload = {
            "schema": 10,
            "final": final,
            "session_id": sess.get("session_id"),
            "started_at": sess.get("started_at"),
            "ended_at": now_iso(),
            "plugin_version": sess.get("plugin_version"),
            "hits": sess.get("hits", {}),
            "misses": sess.get("misses", []),
            "proposals": sess.get("proposals", 0),
            "captures": sess.get("captures", 0),
            "worker_captures": sess.get("worker_captures", 0),
            "comments": sess.get("comments", 0),
            "stale_reports": sess.get("stale_reports", []),
            "selected": sess.get("selected", {}),
            "select_none": sess.get("select_none", 0),
            "select_fail": sess.get("select_fail", 0),
            "select_levels": sess.get("select_levels", {}),
            "ranker": sess.get("ranker", {}),
            "reread_after_compact": sess.get("reread_after_compact", 0),
            "reinjected_after_compact": sess.get("reinjected_after_compact", 0),
            "idle_sessions_since_last": idle,
            "errors": read_json(errors_path, {}),
        }
        payload.update(select_stats.fields(sess, vault()))
        login = issue_queue.gh("api", "user", "--jq", ".login")
        number = find_issue(login, target)
        body = "```json\n%s\n```" % json.dumps(payload, indent=2)
        issue_queue.gh("issue", "comment", str(number), "-R", target, "--body", body)
        if final:
            # Reset first: a failed write must still find `sending` to rename back.
            with idle_locked() as held:
                if held:
                    # Subtract the sent count: bumps made during the network call survive.
                    write_json(idle_path, max(read_json(idle_path, 0) - idle, 0))
            write_json(errors_path, {})
            remove_session(sending)
    except Exception:
        if final:
            os.rename(sending, path)
            # Wait another stale period before the next sweep retries.
            os.utime(path)


def main(argv):
    try:
        # The background worker session must not inject context, count or spawn.
        if os.environ.get(WORKER_ENV):
            return 0
        # Fail open when the host did not set the plugin env.
        if not (root() and os.environ.get("CLAUDE_PLUGIN_DATA", "")):
            return 0
        cmd = argv[0] if argv else ""
        if cmd == "capture-worker":
            import capture
            capture.capture_worker(argv[1], argv[2], argv[3] if len(argv) > 3 else "volume")
            return 0
        if cmd == "send":
            send(argv[1])
            return 0
        if cmd == "flush":
            issue_queue.flush_queue()
            return 0
        if cmd == "send-interim":
            send(argv[1], final=False)
            return 0
        event = json.load(sys.stdin)
        {"session-start": session_start, "session-end": session_end, "pre-compact": pre_compact, "track": track, "stop": stop}[cmd](event)
    except Exception as exc:
        note_error(argv[0] if argv else "main", exc)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
