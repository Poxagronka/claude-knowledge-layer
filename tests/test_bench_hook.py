"""Pure scoring functions of scripts/bench_hook.py."""
import os
import shutil
from pathlib import Path

from conftest import load_script
import sys
import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX dev bench: POSIX only")

ROOT = Path(__file__).resolve().parent.parent
bench = load_script("bench_hook", "scripts/bench_hook.py")

CASES = [
    {"prompt": "a", "lang": "en", "must": ["x"], "ok": ["y"], "none": False},
    {"prompt": "b", "lang": "ru", "must": ["x", "z"], "ok": [], "none": False},
    {"prompt": "c", "lang": "de", "must": ["w"], "ok": [], "none": False, "split": "holdout"},
    {"prompt": "d", "lang": "en", "must": [], "ok": [], "none": True},
]


def test_score_four_cases_with_one_negative():
    out = bench.score(CASES, [["x", "y"], ["x", "q"], [], ["junk"]])
    assert out["n"] == 4
    assert out["precision"] == (1.0 + 0.5 + 0.0) / 3
    assert out["recall"] == (1.0 + 0.5 + 0.0) / 3
    assert out["neg_clean"] == 0.0
    assert out["recall_by_lang"] == {"de": 0.0, "en": 1.0, "ru": 0.5}


def test_score_clean_negative():
    assert bench.score(CASES[3:], [[]])["neg_clean"] == 1.0


def test_en_ru_recall_skips_other_languages_and_negatives():
    assert bench.en_ru_recall(CASES, [["x"], ["x", "z"], [], []]) == 1.0


def test_build_report_splits_dev_and_holdout():
    rep = bench.build_report(CASES, [["x"], ["x", "z"], ["w"], []], [1.0, 2.0, 3.0, 4.0], {}, {"pipeline": "t"})
    assert rep["holdout"]["n"] == 1 and rep["dev"]["n"] == 3 and rep["all"]["n"] == 4
    assert rep["holdout"]["recall"] == 1.0
    assert rep["code_hash"] == bench.code_hash()


def test_check_floor():
    rep = {"all": {"precision": 0.8, "recall": 0.9, "neg_clean": 1.0}, "en_ru_recall": 0.9}
    floor = {"all": {"precision": 0.75, "recall": 0.9, "neg_clean": 0.98}, "en_ru_recall": 0.91}
    assert [name for name, _, _ in bench.check_floor(rep, floor)] == ["en_ru_recall"]
    floor["en_ru_recall"] = 0.9
    assert bench.check_floor(rep, floor) == []


def test_code_hash_changes_with_one_selector_file(tmp_path):
    for rel in bench.SELECTOR_FILES:
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, dest)
    before = bench.code_hash(str(tmp_path))
    assert before == bench.code_hash(str(ROOT))
    with open(tmp_path / bench.SELECTOR_FILES[1], "ab") as fh:
        fh.write(b"\n# change\n")
    assert bench.code_hash(str(tmp_path)) != before


def test_main_starts_the_daemon_before_the_first_case(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    monkeypatch.setattr(bench.sys, "argv", ["bench_hook.py", "--ranker"])
    calls, seen = [], {}

    class Daemon:
        def __init__(self, cmd, env, **_kw):
            calls.append(cmd[-1])
            run_dir = os.path.join(env["CLAUDE_PLUGIN_DATA"], "run")
            os.makedirs(run_dir)
            open(os.path.join(run_dir, "rank-test.sock"), "w").close()

        def terminate(self):
            calls.append("terminate")

    monkeypatch.setattr(bench.subprocess, "run", lambda cmd, **_kw: calls.append(cmd[-1]))
    monkeypatch.setattr(bench.subprocess, "Popen", Daemon)
    monkeypatch.setattr(bench, "run_bench", lambda _args, data: seen.update(
        sock=os.path.exists(os.path.join(data, "run", "rank-test.sock"))))
    bench.main()
    assert calls == ["--warm", "--serve", "terminate"]
    assert seen == {"sock": True}


def test_no_ranker_mode_needs_no_runtime(monkeypatch):
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    monkeypatch.setattr(bench.sys, "argv", ["bench_hook.py"])
    seen = {}
    monkeypatch.setattr(bench.subprocess, "Popen", lambda *a, **k: seen.update(daemon=True))
    monkeypatch.setattr(bench, "run_bench", lambda args, data: seen.update(data=data, ranker=args.ranker))
    bench.main()
    assert seen == {"data": seen["data"], "ranker": False}
    assert not os.path.exists(seen["data"])


def fake_selector(monkeypatch, result):
    def run(cmd, **kw):
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(bench.subprocess, "run", run)


def test_crashed_run_is_a_failure_not_a_clean_negative(tmp_path, monkeypatch):
    class Done:
        returncode, stdout = 1, ""
    fake_selector(monkeypatch, Done())
    ids, _elapsed, _sess, ok = bench.run_case("p", str(tmp_path))
    assert not ok and ids == [bench.CRASH]
    assert bench.score(CASES[3:], [ids])["neg_clean"] == 0.0
    assert bench.score(CASES[:1], [ids])["recall"] == 0.0


def test_timeout_is_a_failure(tmp_path, monkeypatch):
    fake_selector(monkeypatch, bench.subprocess.TimeoutExpired("x", 60))
    ids, _elapsed, _sess, ok = bench.run_case("p", str(tmp_path))
    assert not ok and ids == [bench.CRASH]


def test_ranker_flag_sets_the_opt_in_env(tmp_path, monkeypatch):
    seen = []

    class Done:
        returncode, stdout = 0, ""
    monkeypatch.setattr(bench.subprocess, "run", lambda cmd, **kw: seen.append(kw["env"].get("KNOWLEDGE_LOCAL")) or Done())
    monkeypatch.setenv("KNOWLEDGE_LOCAL", "1")
    bench.run_case("p", str(tmp_path))
    bench.run_case("p", str(tmp_path), ranker=True)
    assert seen == [None, "1"]


def test_per_run_scores_and_pooled_score_is_their_mean():
    pos = CASES[:2]
    cases = [pos[0], pos[0], pos[1], pos[1]]  # case-major, 2 repeats
    outputs = [["x"], [], ["x", "z"], ["x"]]
    runs = bench.per_run_scores(cases, outputs, 2)
    assert [r["recall"] for r in runs] == [1.0, 0.25]
    assert bench.score(cases, outputs)["recall"] == pytest.approx(sum(r["recall"] for r in runs) / 2)


def run_main(monkeypatch, argv):
    seen = {}
    monkeypatch.setattr(bench.sys, "argv", ["bench_hook.py"] + argv)
    monkeypatch.setattr(bench, "run_bench", lambda args, data: seen.update(repeats=args.repeats))
    bench.main()
    return seen["repeats"]


def test_repeats_default_is_three_with_floor_else_one(monkeypatch):
    assert run_main(monkeypatch, []) == 1
    assert run_main(monkeypatch, ["--floor", "f.json"]) == 3
    assert run_main(monkeypatch, ["--floor", "f.json", "--repeats", "5"]) == 5


def test_floor_passes_on_mean_when_one_run_dips():
    precisions = (0.76, 0.78, 0.77)
    floor = {"all": {"precision": 0.77}, "en_ru_recall": 0.9}
    assert bench.check_floor({"all": {"precision": precisions[0]}, "en_ru_recall": 0.95}, floor)
    assert not bench.check_floor({"all": {"precision": sum(precisions) / 3}, "en_ru_recall": 0.95}, floor)
