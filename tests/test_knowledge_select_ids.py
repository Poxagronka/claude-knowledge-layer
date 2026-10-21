import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "knowledge_select", os.path.join(ROOT, "plugins", "knowledge", "scripts", "knowledge_select.py"))
sel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sel)


def test_load_candidates_ids_use_forward_slashes(tmp_path, monkeypatch):
    (tmp_path / "entities").mkdir()
    (tmp_path / "entities" / "service-example-service.md").write_text(
        "---\nid: service-example-service\ntype: entity\n---\n# Example Service\n\nA daily digest.\n", encoding="utf-8")
    real = os.path.relpath
    monkeypatch.setattr(sel.os.path, "relpath", lambda p, s: real(p, s).replace("/", "\\"))
    monkeypatch.setattr(sel.os, "sep", "\\")
    _entries, hubs = sel.load_candidates(str(tmp_path))
    assert [h["id"] for h in hubs] == ["entities/service-example-service"]
