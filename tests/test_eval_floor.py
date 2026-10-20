"""The committed eval baseline must match the selector code and meet the floor."""
import json
import sys

import pytest
from pathlib import Path

from conftest import load_script

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "plugins" / "knowledge" / "scripts"))
import knowledge_select  # noqa: E402

bench = load_script("bench_hook", "scripts/bench_hook.py")

BASELINE = json.loads((ROOT / "evals" / "baseline.json").read_text())
FLOOR = json.loads((ROOT / "evals" / "floor.json").read_text())
CASES = [json.loads(line) for line in (ROOT / "evals" / "select_cases.jsonl").read_text(encoding="utf-8").splitlines()]


def test_baseline_matches_selector_code():
    assert BASELINE["code_hash"] == bench.code_hash(str(ROOT)), (
        "selector code changed: re-run scripts/bench_hook.py --json evals/baseline.json")


@pytest.mark.skipif(BASELINE.get("measured") is False, reason="template baseline is unmeasured")
def test_baseline_meets_floor():
    assert bench.check_floor(BASELINE, FLOOR) == []


def test_case_ids_exist_in_vault():
    entries, hubs = knowledge_select.load_candidates(str(ROOT / "plugins" / "knowledge" / "vault"))
    known = {e["id"] for e in entries} | {h["id"] for h in hubs}
    for case in CASES:
        for ident in case["must"] + case["ok"]:
            assert ident in known, "%s: unknown id %s" % (case["prompt"], ident)


def test_holdout_share():
    share = sum(c.get("split") == "holdout" for c in CASES) / len(CASES)
    assert 0.15 <= share <= 0.25
