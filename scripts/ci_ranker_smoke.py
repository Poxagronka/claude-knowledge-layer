#!/usr/bin/env python3
"""CI smoke test of the local ranker on a real OS (the Windows job runs it).

Needs CLAUDE_PLUGIN_ROOT and CLAUDE_PLUGIN_DATA, and an installed runtime (runtime/install.py).
Checks: the runtime is ready, the daemon starts and answers, the ids are POSIX-style vault ids.
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugins", "knowledge", "scripts"))
import local_rank as lr  # noqa: E402

PROMPT = "Example App for small shops"
EXPECTED = "entities/product-example-app"
WAIT_SECONDS = 120


def main():
    vault = os.path.join(lr.plugin_root(), "vault")
    assert lr.supported(), "platform is not supported by the ranker"
    assert lr.ready(vault), "runtime is not ready: see install.log"
    lr.spawn_daemon()
    request = json.dumps({"prompt": PROMPT, "seen": []})
    deadline, reply, last = time.time() + WAIT_SECONDS, None, None
    while reply is None and time.time() < deadline:
        try:
            reply = lr.ask_daemon(request)
        except OSError as exc:  # the daemon is still loading the models
            last = exc
            time.sleep(2)
    assert reply, "no daemon answer in %d s: %r" % (WAIT_SECONDS, last)
    print("ids:", reply["ids"][:5], "pins:", reply["pins"])
    assert EXPECTED in reply["ids"], "%s missing from ids" % EXPECTED
    assert reply["scores"], "no rerank scores: the reranker did not run"
    print("top rerank score: %.3f" % max(reply["scores"].values()))
    assert not any("\\" in i for i in reply["ids"] + reply["pins"]), "backslash in an id"
    print("ranker smoke OK in %.0f s" % (WAIT_SECONDS - (deadline - time.time())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
