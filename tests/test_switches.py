import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugins", "knowledge", "scripts"))
import switches  # noqa: E402


@pytest.mark.parametrize("value,expected", [("0", True), ("false", True), (" OFF ", True), ("No", True), ("", False), ("1", False), ("yes", False)])
def test_env_off(monkeypatch, value, expected):
    monkeypatch.setenv("X_SWITCH", value)
    assert switches.env_off("X_SWITCH") is expected


def test_env_off_unset(monkeypatch):
    monkeypatch.delenv("X_SWITCH", raising=False)
    assert switches.env_off("X_SWITCH") is False
