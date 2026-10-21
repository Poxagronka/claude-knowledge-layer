#!/usr/bin/env python3
"""Run evals/select_cases.jsonl through the real UserPromptSubmit hook, sequentially.

Default mode is the shipped path: Haiku rates the full catalog, no ranker. It needs a working `claude` only.
--ranker adds the opt-in local ranker: CLAUDE_PLUGIN_DATA must hold the installed runtime (POSIX venv, AF_UNIX socket).
A selector run that crashes or times out counts as a failure: negatives are not clean and positives score 0.
Each case uses a fresh session id, so read dedup never hides a pick.
Usage: bench_hook.py [--ranker] [--repeats 1] [--limit N] [--out FILE] [--json FILE] [--floor FILE]
--json writes the committed baseline (evals/baseline.json). --floor exits 1 when a metric falls below the floor file.
"""
import argparse
import datetime
import glob
import hashlib
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(ROOT, "plugins", "knowledge")
CASES = os.path.join(ROOT, "evals", "select_cases.jsonl")
SELECTOR_FILES = ("plugins/knowledge/scripts/knowledge_select.py", "plugins/knowledge/scripts/local_rank.py",
                  "plugins/knowledge/runtime/rank.py", "plugins/knowledge/runtime/manifest.json",
                  "plugins/knowledge/scripts/vault_hubs.py")
HEADER_RE = re.compile(r"^### \[\d\] (\S+)", re.M)
OFF = ["Привет, как дела", "capital of Australia", "tiempo hará mañana", "Käsespätzle", "погода в Минске", "книгу, которую",
       "17 умножить", "vegan curry", "French Revolution", "capital de Argentina", "consejos para dormir", "Wal und einem Hai",
       "Schaltjahr", "Geburtstagswunsch"]


CRASH = "<crash>"  # stands in for the ids of a run that crashed or timed out


def run_case(prompt, data, ranker=False):
    """Return (ids, elapsed, session, ok). `ok` is False when the selector crashed or timed out; ids is then [CRASH]."""
    sid = "bench-" + uuid.uuid4().hex[:8]
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=PLUGIN, CLAUDE_PLUGIN_DATA=data)
    if ranker:
        env["KNOWLEDGE_LOCAL"] = "1"
    else:
        env.pop("KNOWLEDGE_LOCAL", None)
    start = time.time()
    try:
        done = subprocess.run([sys.executable, os.path.join(PLUGIN, "scripts", "knowledge_select.py")], env=env, capture_output=True,
                              encoding="utf-8", errors="replace", input=json.dumps({"session_id": sid, "prompt": prompt}), timeout=60)
        ok = done.returncode == 0
        ids = HEADER_RE.findall(done.stdout) if ok else [CRASH]
    except subprocess.TimeoutExpired:
        ok, ids = False, [CRASH]
    elapsed = time.time() - start
    path = os.path.join(data, "sessions", sid + ".json")
    sess = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
    for name in (path, path + ".lock"):
        if os.path.exists(name):
            os.remove(name)
    return ids, elapsed, sess, ok


def mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def code_hash(root=ROOT):
    """sha256 over the bytes of the selector code files, so a baseline names the code it measured."""
    digest = hashlib.sha256()
    for rel in sorted(SELECTOR_FILES):
        with open(os.path.join(root, rel), "rb") as fh:
            digest.update(fh.read())
    return digest.hexdigest()


def case_scores(case, got):
    """Return (kind, value) for one case: ('neg', is_clean) or ('pos', (precision, recall))."""
    got = set(got)
    if case["none"]:
        return "neg", not got
    must = set(case["must"])
    prec = len(got & (must | set(case["ok"]))) / len(got) if got else 0.0
    return "pos", (prec, len(got & must) / len(must))


def score(cases, outputs):
    """cases and outputs are parallel lists. An output is the list of ids the hook injected."""
    prec, rec, neg, by_lang = [], [], [], {}
    for case, got in zip(cases, outputs):
        kind, val = case_scores(case, got)
        if kind == "neg":
            neg.append(val)
        else:
            prec.append(val[0])
            rec.append(val[1])
            by_lang.setdefault(case["lang"], []).append(val[1])
    return {"n": len(cases), "precision": mean(prec), "recall": mean(rec), "neg_clean": mean(neg),
            "recall_by_lang": {k: mean(v) for k, v in sorted(by_lang.items())}}


def en_ru_recall(cases, outputs):
    recs = [case_scores(c, g)[1][1] for c, g in zip(cases, outputs) if not c["none"] and c["lang"] in ("en", "ru")]
    return mean(recs)


def git_sha():
    done = subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"], capture_output=True, text=True)
    return done.stdout.strip()


def build_report(cases, outputs, times, paths, params):
    """The dict that --json writes: all, dev and holdout scores, the en+ru recall and the code identity."""
    def part(name):
        idx = [i for i, c in enumerate(cases) if name is None or c.get("split", "dev") == name]
        return [cases[i] for i in idx], [outputs[i] for i in idx]
    times = sorted(times)
    return {
        "date": datetime.date.today().isoformat(), "git_sha": git_sha(), "code_hash": code_hash(), "params": params,
        "all": score(*part(None)), "dev": score(*part("dev")), "holdout": score(*part("holdout")),
        "en_ru_recall": en_ru_recall(cases, outputs),
        "latency_s": {"p50": statistics.median(times), "p95": times[int(0.95 * len(times))]}, "ranker_paths": paths,
    }


def per_run_scores(cases, outputs, repeats):
    """Score each repeat alone. Cases are stored case-major, so run r holds items r, r+repeats, ..."""
    return [score(cases[r::repeats], outputs[r::repeats]) for r in range(repeats)]


def check_floor(report, floor):
    """Return the floored metrics the report misses. Floor shape: {"all": {metric: min}, "en_ru_recall": min}."""
    bad = [(k, report["all"][k], v) for k, v in floor["all"].items() if not report["all"][k] >= v]
    if not report["en_ru_recall"] >= floor["en_ru_recall"]:
        bad.append(("en_ru_recall", report["en_ru_recall"], floor["en_ru_recall"]))
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ranker", action="store_true", help="add the opt-in local ranker (needs the installed runtime)")
    ap.add_argument("--repeats", type=int, default=0, help="runs per case; default 3 with --floor, else 1")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default="bench-hook.md")
    ap.add_argument("--json", default="")
    ap.add_argument("--floor", default="")
    args = ap.parse_args()
    args.repeats = args.repeats or (3 if args.floor else 1)
    daemon = None
    data = tempfile.mkdtemp(prefix="knowledge-bench-")  # private data dir: the bench never touches live vectors
    try:
        if args.ranker:
            daemon = start_ranker(data)
        run_bench(args, data)
    finally:
        if daemon:
            daemon.terminate()
        shutil.rmtree(data, ignore_errors=True)


def start_ranker(data):
    """Link the installed runtime into `data`, warm the ranker and start its daemon. Return the daemon process."""
    real = os.environ["CLAUDE_PLUGIN_DATA"]
    for name in ("venv", "models", "runtime.stamp"):
        if os.path.exists(os.path.join(real, name)):
            os.symlink(os.path.join(real, name), os.path.join(data, name))
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=PLUGIN, CLAUDE_PLUGIN_DATA=data, KNOWLEDGE_LOCAL="1")
    rank = [os.path.join(data, "venv", "bin", "python"), "-I", os.path.join(PLUGIN, "runtime", "rank.py")]
    subprocess.run(rank + ["--warm"], env=env, check=True)
    # Real sessions start the daemon at SessionStart. A cold daemon makes the first prompts fall back.
    daemon = subprocess.Popen(rank + ["--serve"], env=env)
    deadline = time.time() + 60
    while not glob.glob(os.path.join(data, "run", "*.sock")):
        if time.time() > deadline:
            daemon.terminate()
            sys.exit("ranker daemon did not start")
        time.sleep(0.1)
    return daemon


def run_bench(args, data):
    base = [json.loads(line) for line in open(CASES, encoding="utf-8")][: args.limit or None]
    cases, outputs, times, fails, paths, misses, crashes = [], [], [], 0, {}, [], 0
    for case in base:
        for _ in range(args.repeats):
            ids, elapsed, sess, ok = run_case(case["prompt"], data, args.ranker)
            crashes += not ok
            times.append(elapsed)
            fails += bool(sess.get("select_fail"))
            for name, n in sess.get("ranker", {}).items():
                paths[name] = paths.get(name, 0) + n
            cases.append(case)
            outputs.append(ids)
            got = set(ids)
            if case["none"] and got:
                misses.append((case["prompt"], "FALSE POSITIVE", sorted(got)))
            elif set(case["must"]) - got:
                misses.append((case["prompt"], "missed", sorted(set(case["must"]) - got)))
    neg_code = [not o for c, o in zip(cases, outputs) if c["none"] and not any(x in c["prompt"] for x in OFF)]
    neg_off = [not o for c, o in zip(cases, outputs) if c["none"] and any(x in c["prompt"] for x in OFF)]
    report = build_report(cases, outputs, times, paths, {"pipeline": "ranker+haiku" if args.ranker else "haiku-full-catalog", "repeats": args.repeats})
    al, ts = report["all"], sorted(times)
    runs = per_run_scores(cases, outputs, args.repeats)
    run_lines = ["run %d: P %.3f R %.3f N %.3f" % (i + 1, r["precision"], r["recall"], r["neg_clean"]) for i, r in enumerate(runs)]
    lines = [
        "# Hook bench: %d cases x %d repeats, sequential" % (len(base), args.repeats), "",
        "Floor check uses the MEAN of %d runs (every run has the same cases, so the pooled score is the mean)." % args.repeats,
    ] + run_lines + [
        "mean: P %.3f R %.3f N %.3f" % (al["precision"], al["recall"], al["neg_clean"]),
        "ranker paths: %s, select_fail: %d, crashed or timed out: %d" % (paths, fails, crashes),
        "precision %.3f | recall %.3f | negatives clean %.3f (coding %.3f, off-domain %.3f)" % (
            al["precision"], al["recall"], al["neg_clean"], mean(neg_code), mean(neg_off)),
        "dev: P %.3f R %.3f N %.3f | holdout: P %.3f R %.3f N %.3f | en+ru recall %.3f" % (
            report["dev"]["precision"], report["dev"]["recall"], report["dev"]["neg_clean"],
            report["holdout"]["precision"], report["holdout"]["recall"], report["holdout"]["neg_clean"], report["en_ru_recall"]),
        "wall s: p50 %.2f, p95 %.2f, mean %.2f" % (statistics.median(ts), ts[int(0.95 * len(ts))], mean(ts)), "",
        "## Misses",
    ] + ["- %s | %s %s" % m for m in misses]
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, sort_keys=True)
            fh.write("\n")
    if args.floor:
        bad = check_floor(report, json.load(open(args.floor)))
        if crashes:
            print("CRASHED OR TIMED OUT RUNS: %d" % crashes, file=sys.stderr)
        for name, got, need in bad:
            print("BELOW FLOOR: %s %.3f < %.3f" % (name, got, need), file=sys.stderr)
        sys.exit(1 if bad or crashes else 0)


if __name__ == "__main__":
    main()
