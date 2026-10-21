import datetime
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import repo_catalog as rc  # noqa: E402

TODAY = datetime.date(2026, 10, 17)


def repo(name, pushed, archived=False, desc=None):
    return {"name": name, "pushedAt": pushed + "T10:00:00Z",
            "isArchived": archived, "description": desc}


FAKE = [
    repo("example-app", "2026-10-16", desc="From GitHub"),
    repo("on-the-edge", "2026-04-17"),
    repo("too-old", "2026-04-16"),
    repo("archived-pkg", "2026-10-01", archived=True),
    repo("example-package", "2026-10-14", desc="Complete ads solution"),
]


class RepoCatalogTest(unittest.TestCase):
    def test_window_and_archive_filter(self):
        names = [r["name"] for r in rc.active(FAKE, TODAY)]
        self.assertEqual(names, ["example-app", "example-package", "on-the-edge"])

    def test_repo_without_push_date_is_skipped(self):
        empty = {"name": "empty", "pushedAt": None, "isArchived": False, "description": None}
        self.assertEqual([r["name"] for r in rc.active(FAKE + [empty], TODAY)],
                         ["example-app", "example-package", "on-the-edge"])

    def test_gh_list_failure_exits(self):
        import subprocess
        orig = rc.subprocess.run
        rc.subprocess.run = lambda *a, **k: (_ for _ in ()).throw(subprocess.CalledProcessError(1, "gh"))
        try:
            with self.assertRaises(SystemExit):
                rc.gh_list("o")
        finally:
            rc.subprocess.run = orig

    def test_configured_name_filter_and_skip(self):
        import re
        from unittest.mock import patch
        repos = [repo("example-app", "2026-10-01"), repo("unrelated", "2026-10-01"),
                 repo("example-skip", "2026-10-01")]
        with patch.object(rc, "NAME_RE", re.compile("^example-")):
            self.assertEqual([r["name"] for r in rc.pick("example-org", repos)], ["example-app"])

    def test_new_file_layout_hubs_and_description(self):
        text = rc.render_repo(FAKE[0], "example-org", TODAY)
        self.assertIn("id: repos/example-app\ntype: repo\ntags: [domain/repos]\norg: example-org\nsection: product\nadded: 2026-10-17\n", text)
        self.assertIn("verify_query: github:repo:example-org/example-app\n---\n# example-app\n\nFrom GitHub.\n", text)
        self.assertIn("Related: [[product-example-app]], [[team-example]]\n", text)
        self.assertNotIn("2026-10-16", text)
        self.assertNotIn("bu:", text)

    def test_unmapped_repo_gets_fallback_purpose_and_bu_hub(self):
        text = rc.render_repo(repo("on-the-edge", "2026-10-16"), "example-partners", TODAY)
        self.assertIn("section: other", text)
        self.assertIn("\nPurpose not described yet.\n", text)
        self.assertIn("Related: [[org-example]]", text)

    def test_purpose_and_first_sentence(self):
        text = rc.render_repo(repo("x", "2026-10-16", desc="Does a. Then b."), "example-partners", TODAY)
        self.assertEqual(rc.purpose_of(text), "Does a. Then b.")
        self.assertEqual(rc.first_sentence(rc.purpose_of(text)), "Does a.")

    def run_plan(self, root, listing):
        return rc.plan(root, {r["name"]: ("example-org", r) for r in listing}, TODAY)

    def test_plan_creates_missing_files_and_map(self):
        with tempfile.TemporaryDirectory() as root:
            writes, deletes = self.run_plan(root, rc.active(FAKE, TODAY))
            names = sorted(os.path.basename(p) for p in writes)
            self.assertEqual(names, ["_index.md", "example-app.md", "example-package.md", "on-the-edge.md"])
            self.assertEqual(deletes, [])
            index = writes[os.path.join(root, "repos", "_index.md")]
            self.assertIn("id: repos/_index\ntype: map", index)
            self.assertIn("### Products\n\n- [[repos/example-app]]: From GitHub.", index)
            self.assertIn("### Packages\n\n- [[repos/example-package]]: Complete ads solution.", index)

    def apply(self, writes):
        for path, text in writes.items():
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "w").write(text)

    def test_existing_body_is_never_rewritten(self):
        with tempfile.TemporaryDirectory() as root:
            writes, _ = self.run_plan(root, rc.active(FAKE, TODAY))
            self.apply(writes)
            path = os.path.join(root, "repos", "example-app.md")
            hand = open(path).read().replace("From GitHub.", "Hand written purpose. More.")
            open(path, "w").write(hand)
            again, deletes = self.run_plan(root, rc.active(FAKE, TODAY))
            self.assertIn("Hand written purpose.", again[os.path.join(root, "repos", "_index.md")])
            self.assertNotIn(path, again)
            self.assertEqual(deletes, [])
            self.assertEqual(open(path).read(), hand)

    def test_only_org_and_section_are_fixed(self):
        with tempfile.TemporaryDirectory() as root:
            writes, _ = self.run_plan(root, rc.active(FAKE, TODAY))
            self.apply(writes)
            path = os.path.join(root, "repos", "example-app.md")
            hand = open(path).read().replace("section: product", "section: tool").replace("From GitHub.", "Mine.")
            open(path, "w").write(hand)
            again, _ = self.run_plan(root, rc.active(FAKE, TODAY))
            self.assertEqual(again[path], hand.replace("section: tool", "section: product"))

    def test_repo_that_left_the_window_loses_its_file(self):
        with tempfile.TemporaryDirectory() as root:
            writes, _ = self.run_plan(root, rc.active(FAKE, TODAY))
            self.apply(writes)
            _, deletes = self.run_plan(root, [FAKE[0]])
            self.assertEqual(sorted(os.path.basename(p) for p in deletes), ["example-package.md", "on-the-edge.md"])

    def test_short_listing_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            many = [repo("example-%d" % i, "2026-10-14") for i in range(rc.MAX_DROP + 2)]
            writes, _ = self.run_plan(root, many)
            self.apply(writes)
            orig = rc.gh_list
            rc.gh_list = lambda org: []
            try:
                self.assertEqual(rc.main(["--today", "2026-10-17", "--root", root]), 2)
            finally:
                rc.gh_list = orig
            self.assertEqual(len(os.listdir(os.path.join(root, "repos"))), len(many) + 1)

    def test_check_mode_writes_nothing_and_exits_1(self):
        with tempfile.TemporaryDirectory() as root:
            orig = rc.gh_list
            rc.gh_list = lambda org: FAKE if org == "example-org" else []
            try:
                self.assertEqual(rc.main(["--today", "2026-10-17", "--root", root, "--check"]), 1)
                self.assertFalse(os.path.exists(os.path.join(root, "repos")))
                self.assertEqual(rc.main(["--today", "2026-10-17", "--root", root]), 0)
                self.assertEqual(rc.main(["--today", "2026-10-17", "--root", root, "--check"]), 0)
                os.remove(os.path.join(root, "repos", "example-app.md"))
                self.assertEqual(rc.main(["--today", "2026-10-17", "--root", root, "--check"]), 1)
            finally:
                rc.gh_list = orig


if __name__ == "__main__":
    unittest.main()
