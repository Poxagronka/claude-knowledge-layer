#!/usr/bin/env python3
"""Run evals/select_cases.jsonl through the real UserPromptSubmit hook, sequentially.

Needs CLAUDE_PLUGIN_DATA (with the local runtime installed for the local path) and a working `claude`.
Each case uses a fresh session id, so read dedup never hides a pick.
Usage: bench_hook.py [--repeats 1] [--limit N] [--out FILE] [--json FILE] [--floor FILE]
--json writes the committed baseline (evals/baseline.json). --floor exits 1 when a metric falls below the floor file.
"""
import argparse
import datetime
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
                  "plugins/knowledge/runtime/rank.py", "plugins/knowledge/runtime/manifest.json")
HEADER_RE = re.compile(r"^### \[\d\] (\S+)", re.M)
OFF = ["Привет, как дела", "capital of Australia", "tiempo hará mañana", "Käsespätzle", "погода в Минске", "книгу, которую",
       "17 умножить", "vegan curry", "French Revolution", "capital de Argentina", "consejos para dormir", "Wal und einem Hai",
       "Schaltjahr", "Geburtstagswunsch"]


def run_case(prompt, data):
    sid = "bench-" + uuid.uuid4().hex[:8]
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=PLUGIN, CLAUDE_PLUGIN_DATA=data)
    start = time.time()
    done = subprocess.run([sys.executable, os.path.join(PLUGIN, "scripts", "knowledge_select.py")], env=env, capture_output=True,
                          text=True, input=json.dumps({"session_id": sid, "prompt": prompt}), timeout=60)
    elapsed = time.time() - start
    path = os.path.join(data, "sessions", sid + ".json")
    sess = json.load(open(path)) if os.path.exists(path) else {}
    for name in (path, path + ".lock"):
        if os.path.exists(name):
            os.remove(name)
    return HEADER_RE.findall(done.stdout), elapsed, sess


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


def check_floor(report, floor):
    """Return the floored metrics the report misses. Floor shape: {"all": {metric: min}, "en_ru_recall": min}."""
    bad = [(k, report["all"][k], v) for k, v in floor["all"].items() if not report["all"][k] >= v]
    if not report["en_ru_recall"] >= floor["en_ru_recall"]:
        bad.append(("en_ru_recall", report["en_ru_recall"], floor["en_ru_recall"]))
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default="bench-hook.md")
    ap.add_argument("--json", default="")
    ap.add_argument("--floor", default="")
    args = ap.parse_args()
    real = os.environ["CLAUDE_PLUGIN_DATA"]
    data = tempfile.mkdtemp(prefix="ckl-bench-", dir="/tmp")  # private data dir: the bench never touches live vectors
    try:
        for name in ("venv", "models", "runtime.stamp"):
            if os.path.exists(os.path.join(real, name)):
                os.symlink(os.path.join(real, name), os.path.join(data, name))
        env = dict(os.environ, CLAUDE_PLUGIN_ROOT=PLUGIN, CLAUDE_PLUGIN_DATA=data)
        subprocess.run([os.path.join(data, "venv", "bin", "python"), "-I", os.path.join(PLUGIN, "runtime", "rank.py"), "--warm"], env=env, check=True)
        run_bench(args, data)
    finally:
        shutil.rmtree(data, ignore_errors=True)


def run_bench(args, data):
    base = [json.loads(line) for line in open(CASES, encoding="utf-8")][: args.limit or None]
    cases, outputs, times, fails, paths, misses = [], [], [], 0, {}, []
    for case in base:
        for _ in range(args.repeats):
            ids, elapsed, sess = run_case(case["prompt"], data)
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
    report = build_report(cases, outputs, times, paths, {"pipeline": "ranker+haiku"})
    al, ts = report["all"], sorted(times)
    lines = [
        "# Hook bench: %d cases x %d repeats, sequential" % (len(base), args.repeats), "",
        "ranker paths: %s, select_fail: %d" % (paths, fails),
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
        for name, got, need in bad:
            print("BELOW FLOOR: %s %.3f < %.3f" % (name, got, need), file=sys.stderr)
        sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
