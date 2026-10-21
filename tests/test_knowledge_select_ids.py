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


def test_repo_files_are_whole_file_records_named_by_the_full_repo_name(tmp_path):
    (tmp_path / "repos").mkdir()
    (tmp_path / "repos" / "example-app.md").write_text(
        "---\nid: repos/example-app\ntype: repo\norg: o\n---\n# example-app\n\nSample app.\n\n"
        "Related: [[product-example-app]]\n", encoding="utf-8")
    (tmp_path / "repos" / "_index.md").write_text("---\nid: repos/_index\ntype: map\n---\n# Active repos\n", encoding="utf-8")
    entries, hubs = sel.load_candidates(str(tmp_path))
    assert entries == []
    assert [h["id"] for h in hubs] == ["repos/example-app"]
    assert hubs[0]["names"] == ["example-app", "example app"]
    assert [h["id"] for h in sel.named_hubs(hubs, "fix the example-app build", set())] == ["repos/example-app"]
    assert sel.named_hubs(hubs, "a sample puzzle", set()) == []
