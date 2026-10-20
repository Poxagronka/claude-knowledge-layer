"""Pure scoring functions of scripts/bench_hook.py."""
import shutil
from pathlib import Path

from conftest import load_script

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
