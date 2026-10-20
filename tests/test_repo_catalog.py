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

    def test_hub_mapping_and_fallback(self):
        text = rc.render("example-org", rc.active(FAKE, TODAY), {}, TODAY)
        self.assertIn("[[product-example-app]], [[team-example]]", text)
        self.assertIn("- `on-the-edge`: Repository.", text)
        self.assertIn("[[org-example]]", text.split("on-the-edge")[1])
        self.assertIn("^repo-example-app", text)
        self.assertIn("## Products", text)
        self.assertIn("## Packages", text)
        self.assertNotIn("2026-10-16", text)

    def test_verify_query_and_last_verified_kept(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "repos"))
            path, first = rc.build("example-org", FAKE, root, TODAY)
            self.assertIn("[verify_with:: github] [verify_query:: github:repo:example-org/example-app] ^repo-example-app", first)
            seen = first.replace("example-app] ^repo-example-app", "example-app] [last_verified:: 2026-10-01] ^repo-example-app")
            open(path, "w").write(seen)
            _, again = rc.build("example-org", FAKE, root, TODAY)
            self.assertEqual(again, seen)

    def test_colliding_names_get_unique_block_ids(self):
        rows = [repo("a_b", "2026-10-16"), repo("a-b", "2026-10-16"), repo("a.b", "2026-10-16")]
        text = rc.render("example-org", rc.active(rows, TODAY), {}, TODAY)
        ids = [ln.rsplit("^", 1)[1] for ln in text.splitlines() if ln.rstrip().endswith(tuple("^" + i for i in ("repo-a-b", "repo-a-b-2", "repo-a-b-3")))]
        self.assertEqual(sorted(ids), ["repo-a-b", "repo-a-b-2", "repo-a-b-3"])

    def test_description_used_without_hand_line(self):
        text = rc.render("example-org", rc.active(FAKE, TODAY), {}, TODAY)
        self.assertIn("- `example-app`: From GitHub.", text)

    def test_hand_written_purpose_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "repos"))
            path, first = rc.build("example-org", FAKE, root, TODAY)
            hand = first.replace("From GitHub.", "Hand written purpose.")
            open(path, "w").write(hand)
            _, again = rc.build("example-org", FAKE, root, TODAY)
            self.assertEqual(again, hand)

    def test_archived_repo_leaves_the_file(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "repos"))
            path, text = rc.build("example-org", FAKE, root, TODAY)
            self.assertNotIn("archived-pkg", text)
            self.assertNotIn("too-old", text)

    def test_short_listing_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "repos"))
            many = [repo("example-%d" % i, "2026-10-14") for i in range(rc.MAX_DROP + 2)]
            path, text = rc.build("example-org", many, root, TODAY)
            open(path, "w").write(text)
            orig = rc.gh_list
            rc.gh_list = lambda org: []
            try:
                self.assertEqual(rc.main(["--today", "2026-10-17", "--root", root]), 2)
            finally:
                rc.gh_list = orig
            self.assertEqual(open(path).read(), text)

    def test_second_org_drop_refuses_before_first_write(self):
        org1, org2 = list(rc.ORGS)[:2]
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "repos"))
            many = [repo("example-%d" % i, "2026-10-14") for i in range(rc.MAX_DROP + 2)]
            path1, old1 = rc.build(org1, [repo("old-a", "2026-10-14")], root, TODAY)
            open(path1, "w").write(old1)
            path2, old2 = rc.build(org2, many, root, TODAY)
            open(path2, "w").write(old2)
            listing = {org1: [repo("new-a", "2026-10-15")], org2: []}
            orig = rc.gh_list
            rc.gh_list = lambda org: listing.get(org, [])
            try:
                self.assertEqual(rc.main(["--today", "2026-10-17", "--root", root]), 2)
            finally:
                rc.gh_list = orig
            self.assertEqual(open(path1).read(), old1)
            self.assertEqual(open(path2).read(), old2)


if __name__ == "__main__":
    unittest.main()
