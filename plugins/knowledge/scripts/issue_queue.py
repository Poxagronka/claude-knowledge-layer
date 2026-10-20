"""The GitHub issue queue: file queued issues with gh."""
import contextlib
import json
import os
import subprocess
import time

from plugin_state import DROP_SECONDS, queue_dir, repo

SENDING_ORPHAN_SECONDS = 600
QUEUE_SETTLE_SECONDS = 5
BROKEN_JSON_SECONDS = 60
GH_TIMEOUT = 20


def gh_ready():
    """False when gh is missing or logged out: no item can succeed."""
    try:
        gh("auth", "status")
    except Exception:
        return False
    return True


def requeue_orphans(qdir, now):
    names = sorted(os.listdir(qdir))
    # A flush that died mid-send leaves a .sending file. Put it back in the queue.
    for name in names:
        if name.endswith(".json.sending"):
            path = os.path.join(qdir, name)
            try:
                if now - os.path.getmtime(path) > SENDING_ORPHAN_SECONDS:
                    os.rename(path, path[: -len(".sending")])
            except OSError:
                pass


def flush_queue(settle=QUEUE_SETTLE_SECONDS):
    """File each queued issue with gh. A file stays queued until gh succeeds. No issues repo: do nothing."""
    target = repo()
    if not target:
        return
    qdir = queue_dir()
    now = time.time()
    requeue_orphans(qdir, now)
    for name in sorted(os.listdir(qdir)):
        path = os.path.join(qdir, name)
        if not name.endswith(".json"):
            continue
        try:
            age = now - os.path.getmtime(path)
            if age > DROP_SECONDS:
                os.remove(path)
                continue
            if age < settle:
                continue  # the writer may still be writing it
            sending = path + ".sending"
            os.rename(path, sending)
            os.utime(sending)  # rename keeps the old mtime: mark it fresh so a parallel flush skips it
        except OSError:
            continue
        try:
            try:
                with open(sending) as fh:
                    item = json.load(fh)
            except ValueError:
                # Half-written or broken JSON: wait, then drop.
                if age > BROKEN_JSON_SECONDS:
                    os.remove(sending)
                else:
                    os.rename(sending, path)
                continue
            if not (isinstance(item, dict) and item.get("title") and item.get("body")):
                os.remove(sending)
                continue
            labels = item.get("labels") or []
            if isinstance(labels, str):
                labels = [labels]
            args = ["issue", "create", "-R", target, "--title", str(item["title"]), "--body", str(item["body"])]
            for label in labels:
                args += ["--label", str(label)]
            gh(*args)
            os.remove(sending)
        except Exception as exc:
            with contextlib.suppress(OSError):
                os.rename(sending, path)
            if isinstance(exc, FileNotFoundError) or not gh_ready():
                return


def gh(*args):
    out = subprocess.run(
        ["gh"] + list(args), capture_output=True, text=True, timeout=GH_TIMEOUT, check=True
    )
    return out.stdout.strip()
