"""Selector telemetry helpers: per-turn recall tracking and send-time fields.

Stdlib only, Python 3.9. Works on the session dict. Only vault ids, counts and timings leave the machine.
A turn runs from one UserPromptSubmit to the next, and `sess["turn"]` holds its state.
"""
import math

import local_rank

CAP = 50
MAX_RAW = 500


def base(ident):
    return ident.split("#")[0]


def begin_turn(sess):
    sess["turn"] = {"ran": False, "inj": [], "none": False, "missed": [], "reread": [], "nm": False}


def note_result(sess, shown, scores):
    """The selector decided: `shown` ids were injected, `scores` maps each id to its s rating."""
    turn = sess.setdefault("turn", {})
    for key, empty in (("missed", []), ("reread", []), ("nm", False)):
        turn.setdefault(key, empty)
    turn.update(ran=True, none=not shown, inj=sorted({base(i) for i in shown}))
    injected = sess.setdefault("injected", {})
    for ident in shown:
        counts = injected.setdefault(ident, {"s2": 0, "s3": 0})
        key = "s%d" % scores.get(ident, 2)
        counts[key] = counts.get(key, 0) + 1


def note_read(sess, rel, full):
    """A vault file was read. Count a recall miss or a re-read of an injected entry."""
    turn = sess.get("turn") or {}
    ident = rel[:-3] if rel.endswith(".md") else rel
    if not turn.get("ran") or ident.rsplit("/", 1)[-1].startswith("_"):
        return
    if ident in turn["inj"]:
        if full and ident not in turn["reread"]:
            turn["reread"].append(ident)
            reread = sess.setdefault("reread_after_inject", {})
            reread[ident] = reread.get(ident, 0) + 1
    elif ident not in turn["missed"] and ident not in {base(i) for i in sess.get("shown", [])}:
        turn["missed"].append(ident)
        missed = sess.setdefault("missed", {})
        missed[ident] = missed.get(ident, 0) + 1
        if turn["none"] and not turn["nm"]:
            turn["nm"] = True
            sess["missed_after_none"] = sess.get("missed_after_none", 0) + 1


def note_time(sess, ms, timed_out):
    raw = sess.setdefault("select_ms_raw", [])
    raw.append(int(ms))
    del raw[:-MAX_RAW]
    if timed_out:
        sess["select_timeout"] = sess.get("select_timeout", 0) + 1


def top(counts, weight=lambda v: v):
    """The CAP entries with the highest weight."""
    return dict(sorted(counts.items(), key=lambda kv: -weight(kv[1]))[:CAP])


def percentiles(raw):
    ordered = sorted(raw)
    n = len(ordered)

    def pick(p):
        return ordered[max(0, math.ceil(p * n) - 1)] if n else 0
    return {"n": n, "p50": pick(0.5), "p95": pick(0.95), "max": ordered[-1] if n else 0}


def fields(sess, vault_dir):
    """Selector telemetry fields built from the session."""
    return {
        "injected": top(sess.get("injected", {}), lambda v: v.get("s2", 0) + v.get("s3", 0) + v.get("tool", 0)),
        "missed": top(sess.get("missed", {})),
        "missed_after_none": sess.get("missed_after_none", 0),
        "reread_after_inject": top(sess.get("reread_after_inject", {})),
        "select_ms": percentiles(sess.get("select_ms_raw", [])),
        "haiku_ms": percentiles(sess.get("haiku_ms_raw", [])),
        "select_timeout": sess.get("select_timeout", 0),
        "select_paused": sess.get("select_paused", 0),
        "ranker_install": local_rank.install_state(vault_dir),
    }
