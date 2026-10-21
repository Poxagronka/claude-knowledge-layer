#!/usr/bin/env python3
"""Local ranker: e5 top-40, mmarco rerank, top-20 plus alias-pinned hubs.

Runs under the runtime venv. `--warm` builds the vault vectors and exits.
`--serve` keeps the models in memory and answers one JSON request per connection on a Unix
socket (local_rank.sock_path()), or on Windows on a loopback TCP port written to that file.
It exits after IDLE_SECONDS with no request, when its runtime stamp
is no longer the installed one, and after a failed request (the client then respawns a fresh daemon).
Env: CLAUDE_PLUGIN_ROOT and CLAUDE_PLUGIN_DATA (set by Claude Code).
"""
import contextlib
import json
import os
import socket
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
import numpy as np  # noqa: E402
import onnxruntime as ort  # noqa: E402
from tokenizers import Tokenizer  # noqa: E402

import knowledge_select as ks  # noqa: E402
import local_rank as lr  # noqa: E402
import plugin_state  # noqa: E402

MAXLEN = 256
RETRIEVE = 40
KEEP = 20
IDLE_SECONDS = 10 * 60
STAMP_CHECK_SECONDS = 60
_models = {}
_vault = {}


class Model:
    """ONNX transformer: mean-pooled embeddings (e5) or a relevance logit (mmarco cross-encoder)."""

    def __init__(self, folder):
        self.tok = Tokenizer.from_file(lr.data_path("models", folder, "tokenizer.json"))
        self.tok.enable_truncation(MAXLEN)
        self.tok.enable_padding()
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = min(4, os.cpu_count() or 1)
        self.sess = ort.InferenceSession(lr.data_path("models", folder, "model.onnx"), opts, providers=["CPUExecutionProvider"])
        self.names = [i.name for i in self.sess.get_inputs()]

    def run(self, encodings):
        ids = np.array([e.ids for e in encodings], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self.names:
            feed["token_type_ids"] = np.zeros_like(ids)
        return self.sess.run(None, feed)[0], mask

    def embed(self, texts, batch=32):
        out = []
        for i in range(0, len(texts), batch):
            hidden, mask = self.run(self.tok.encode_batch(texts[i:i + batch]))
            vec = (hidden * mask[..., None]).sum(1) / mask.sum(1, keepdims=True)
            out.append(vec / np.linalg.norm(vec, axis=1, keepdims=True))
        return np.vstack(out)

    def cross(self, query, docs):
        logits, _ = self.run(self.tok.encode_batch([(query, d) for d in docs]))
        return 1 / (1 + np.exp(-logits.reshape(-1)))


def vault_docs(vault_dir):
    entries, hubs = ks.load_candidates(vault_dir)
    cands = hubs + entries
    docs = ["%s | %s | %s" % (c["id"], c["title"], c.get("aliases") or c["preview"]) for c in cands]
    return hubs, [c["id"] for c in cands], docs


def warm(vault_dir):
    _hubs, ids, docs = vault_docs(vault_dir)
    vectors = model("e5").embed(["passage: " + d for d in docs])
    path = lr.vectors_path(vault_dir)
    tmp = path + ".tmp.npz"
    np.savez(tmp, D=vectors, ids=np.array(ids))
    os.replace(tmp, path)


def model(folder):
    if folder not in _models:
        _models[folder] = Model(folder)
    return _models[folder]


def vault_state(vault_dir):
    """Docs and vault vectors, reloaded when the vault or the vectors file changes (the path holds the vault hash)."""
    path = lr.vectors_path(vault_dir)
    if _vault.get("path") != path:
        hubs, ids, docs = vault_docs(vault_dir)
        cache = np.load(path)
        if [str(i) for i in cache["ids"]] != ids:
            raise RuntimeError("stale vectors")
        _vault.update(path=path, hubs=hubs, ids=ids, docs=docs, D=cache["D"])
    return _vault["hubs"], _vault["ids"], _vault["docs"], _vault["D"]


def rank(vault_dir, prompt, seen):
    hubs, ids, docs, vectors = vault_state(vault_dir)
    keep = [i for i, ident in enumerate(ids) if ident not in seen]
    query = model("e5").embed(["query: " + prompt])[0]
    score = vectors[keep] @ query
    top = [keep[j] for j in np.argsort(-score)[:RETRIEVE]]
    probs = model("rerank").cross(prompt, [docs[i] for i in top])
    order = [top[j] for j in np.argsort(-probs)]
    low = prompt.lower()
    pins = [h["id"] for h in hubs if h["id"] not in seen and any(ks.whole_word(n, low) for n in h["names"])]
    picked = pins + [ids[i] for i in order if ids[i] not in pins][:KEEP]
    scores = {ids[i]: round(float(p), 4) for i, p in zip(top, probs)}
    return {"ids": picked, "pins": pins, "scores": scores}


def answer(vault_dir, raw):
    event = json.loads(raw)
    return rank(vault_dir, event["prompt"][:ks.PROMPT_CHARS], set(event.get("seen", [])))


def remove_orphan_locks():
    """Delete lock files of other runtime stamps when no daemon holds them."""
    run_dir = os.path.dirname(lr.sock_path())
    for name in os.listdir(run_dir):
        path = os.path.join(run_dir, name)
        if not name.endswith(".sock.lock") or path == lr.sock_path() + ".lock":
            continue
        try:
            with open(path, "a") as fh:
                plugin_state.flock(fh, wait=False)
                os.unlink(path)
        except OSError:
            pass


def stamp_stale():
    """True when the installed runtime stamp is missing or differs from this daemon's stamp."""
    try:
        with open(lr.data_path("runtime.stamp")) as fh:
            return fh.read().strip() != lr.stamp()
    except OSError:
        return True


def drop_socket():
    """Remove the socket file and return the exit code. The lock is released when the process ends."""
    with contextlib.suppress(OSError):
        os.unlink(lr.sock_path())
    return 0


def serve(vault_dir):
    """Daemon loop. The lock file keeps one daemon per runtime version, so a stale socket file is safe to remove."""
    os.makedirs(os.path.dirname(lr.sock_path()), mode=0o700, exist_ok=True)
    if sys.platform != "win32":
        os.chmod(os.path.dirname(lr.sock_path()), 0o700)
    lock = open(lr.sock_path() + ".lock", "a")
    try:
        plugin_state.flock(lock, wait=False)
    except OSError:
        return 0
    remove_orphan_locks()
    vault_state(vault_dir)
    model("e5")
    model("rerank")
    if os.path.exists(lr.sock_path()):
        os.unlink(lr.sock_path())
    if sys.platform == "win32":  # no AF_UNIX: listen on loopback, publish the port in the socket file
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        tmp = lr.sock_path() + ".tmp"
        with open(tmp, "w") as fh:
            fh.write(str(srv.getsockname()[1]))
        os.replace(tmp, lr.sock_path())
    else:
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(lr.sock_path())
        os.chmod(lr.sock_path(), 0o600)
    srv.listen(16)
    srv.settimeout(STAMP_CHECK_SECONDS)
    idle = 0
    while True:
        try:
            conn, _ = srv.accept()
        except socket.timeout:
            idle += STAMP_CHECK_SECONDS
            if idle >= IDLE_SECONDS or stamp_stale():
                return drop_socket()
            continue
        idle = 0
        with conn:
            if stamp_stale():
                return drop_socket()
            failed = False
            try:
                conn.settimeout(5)
                raw = b""
                while not raw.endswith(b"\n"):
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    raw += chunk
                deadline = json.loads(raw).get("deadline")
                if deadline is not None and deadline < time.time():
                    continue  # the client already gave up: skip the work so a burst cannot queue
                reply = answer(vault_dir, raw)
            except Exception as exc:
                reply = {"error": str(exc)[:200]}
                failed = True
            try:
                conn.sendall(json.dumps(reply).encode("utf-8"))
            except OSError:
                pass
        if failed:
            return drop_socket()


def main():
    vault_dir = os.path.join(lr.plugin_root(), "vault")
    if "--warm" in sys.argv:
        warm(vault_dir)
        return 0
    if "--serve" in sys.argv:
        return serve(vault_dir)
    return 2  # no one-shot mode: only the flocked daemon loads the models


if __name__ == "__main__":
    sys.exit(main())
