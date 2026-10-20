import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import build_integrity as bi  # noqa: E402


def test_check_fresh_and_stale(monkeypatch, tmp_path):
    plugin = tmp_path / "plugins" / "knowledge"
    plugin.mkdir(parents=True)
    (plugin / "a.py").write_text("x")
    (plugin / "vault").mkdir()
    (plugin / "vault" / "n.md").write_text("v")
    subprocess.check_call(["git", "init", "-q"], cwd=tmp_path)
    subprocess.check_call(["git", "add", "-A"], cwd=tmp_path)
    monkeypatch.setattr(bi, "PLUGIN", str(plugin))
    monkeypatch.setattr(bi, "OUT", str(plugin / "integrity.json"))
    assert bi.main(["--check"]) == 1
    assert bi.main([]) == 0
    assert bi.main(["--check"]) == 0
    assert '"a.py"' in (plugin / "integrity.json").read_text()
    (plugin / "a.py").write_text("changed")
    assert bi.main(["--check"]) == 1
    assert bi.main([]) == 0
    assert "vault" not in (plugin / "integrity.json").read_text()
    (plugin / "vault" / "n.md").write_text("edited")
    assert bi.main(["--check"]) == 0
