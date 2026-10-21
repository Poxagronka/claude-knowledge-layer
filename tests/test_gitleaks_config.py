"""The gitleaks config must not hide real secrets behind a global allowlist."""
import re
from pathlib import Path

import pytest

tomllib = pytest.importorskip("tomllib")  # Python 3.11+

CONFIG = Path(__file__).resolve().parent.parent / ".github" / "config" / "gitleaks.toml"


def _canaries():
    return [
        "AKIA" + "Q" * 16,
        "ASIA" + "Z" * 16,
        "ghp_" + "a1" * 18,
        "ghp_test" + "c" * 32,
        "github_pat_" + "x" * 40,
        "xoxb-" + "1" * 12 + "-" + "a" * 24,
        "sk-ant-" + "b" * 32,
        "-----BEGIN " + "RSA PRIVATE KEY-----",
    ]


def _tables():
    cfg = tomllib.loads(CONFIG.read_text())
    tables = [cfg.get("allowlist", {})] + list(cfg.get("allowlists", []))
    return cfg, tables


def test_extends_default_rules():
    cfg, _ = _tables()
    assert cfg["extend"]["useDefault"] is True


def test_no_allowlist_regex_matches_a_canary():
    _, tables = _tables()
    for table in tables:
        for rx in table.get("regexes", []):
            for canary in _canaries():
                assert not re.search(rx, canary), (rx, canary[:8])


def test_allowlist_paths_are_tests_or_known_data():
    _, tables = _tables()
    for table in tables:
        for path in table.get("paths", []):
            assert path.startswith("^tests/"), path
