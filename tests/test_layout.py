import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from boxman import layout

LISTING = [
    {"name": "foo-a", "isArchived": False, "isFork": False},
    {"name": "foo-b", "isArchived": False, "isFork": False},
    {"name": "foo-old", "isArchived": False, "isFork": False},
    {"name": "foo-dead", "isArchived": True, "isFork": False},
    {"name": "foo-fork", "isArchived": False, "isFork": True},
    {"name": "bar", "isArchived": False, "isFork": False},
]


class LayoutTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp = Path(directory.name)
        patcher = patch.object(layout, "github_repos", lambda owner: LISTING)
        patcher.start()
        self.addCleanup(patcher.stop)

    def load(self, text: str) -> list[dict]:
        path = self.tmp / "layout.yaml"
        path.write_text(text)
        return layout.load_layout(path)

    def test_literal_url_and_default_ref(self) -> None:
        repos = self.load(
            "ref: main\nrepos:\n  - me/boxman\n  - url: https://h/x/y.git\n    path: tools/y\n    ref: dev\n"
        )
        self.assertEqual(
            repos,
            [
                {"url": "https://github.com/me/boxman.git", "path": "boxman", "ref": "main", "depth": 0},
                {"url": "https://h/x/y.git", "path": "tools/y", "ref": "dev", "depth": 0},
            ],
        )

    def test_bare_list_of_strings(self) -> None:
        repos = self.load("- co/foo-*\n- me/boxman\n- https://h/x/y.git\n")
        self.assertEqual([r["path"] for r in repos], ["foo-a", "foo-b", "foo-old", "boxman", "y"])

    def test_negation_removes_earlier_matches(self) -> None:
        repos = self.load('- co/foo-*\n- "!co/foo-old"\n- "!*/foo-b"\n- me/boxman\n')
        self.assertEqual([r["path"] for r in repos], ["foo-a", "boxman"])

    def test_glob_skips_archived_and_forks(self) -> None:
        repos = self.load("- repo: co/foo-*\n  into: services\n")
        self.assertEqual([r["path"] for r in repos], ["services/foo-a", "services/foo-b", "services/foo-old"])

    def test_glob_can_include_archived(self) -> None:
        self.assertIn("foo-dead", [r["path"] for r in self.load("- repo: co/foo-*\n  include_archived: true\n")])
        self.assertIn("foo-dead", [r["path"] for r in self.load("include_archived: true\nrepos:\n  - co/foo-*\n")])

    def test_depth_default_and_override(self) -> None:
        repos = self.load("depth: 1\nrepos:\n  - a/x\n  - repo: a/y\n    depth: 0\n")
        self.assertEqual([r["depth"] for r in repos], [1, 0])

    def test_include_pulls_in_another_file(self) -> None:
        (self.tmp / "more.yaml").write_text("- co/foo-*\n")
        self.assertEqual(len(self.load("- me/boxman\n#+include more.yaml\n")), 4)

    def test_rejects_bad_entries(self) -> None:
        for entry in (
            "- repo: co/x\n  path: ../x",
            "- repo: co/x\n  path: /etc/x",
            "- repo: co/x\n  path: .partial/x",
            "- repo: co/foo-*\n  into: ..",
            "- repo: co/foo-*\n  path: x",
            "- repo: co/x\n  depth: many",
            "- repo: co/x\n  include_archived: maybe",
            "- repo: co/x\n  bogus: 1",
            "- bare",
            '- "*/x"',
            "bogus: 1\nrepos:\n  - a/x",
        ):
            with self.subTest(entry=entry), self.assertRaises(SystemExit):
                self.load(entry + "\n")

    def test_rejects_duplicates_empty_and_bad_syntax(self) -> None:
        for text in ("- a/x\n- b/x\n", "repos:\n", "", "- a/x: b\n", "- *a/x\n"):
            with self.subTest(text=text), self.assertRaises(SystemExit):
                self.load(text)

    def test_clone_uses_partial_dir_then_renames(self) -> None:
        calls = []

        def fake_run(cmd, check, env):
            calls.append(cmd)
            Path(cmd[-1]).mkdir()

        with patch.object(layout, "SHARED_DIR", self.tmp), patch.object(layout.subprocess, "run", fake_run):
            layout.clone({"url": "u", "path": "svc/foo", "ref": "main", "depth": 1})
        partial = str(self.tmp / ".partial" / "svc__foo")
        self.assertEqual(
            calls[0],
            ["git", "-c", "core.sharedRepository=group", "clone", "--branch", "main", "--depth", "1", "u", partial],
        )
        self.assertTrue((self.tmp / "svc" / "foo").is_dir())
        self.assertFalse(Path(partial).exists())

    def test_clone_skips_finished_and_discards_stale_partial(self) -> None:
        calls = []

        def fake_run(cmd, check, env):
            calls.append(cmd)
            Path(cmd[-1]).mkdir()

        with patch.object(layout, "SHARED_DIR", self.tmp), patch.object(layout.subprocess, "run", fake_run):
            (self.tmp / "done").mkdir()
            layout.clone({"url": "u", "path": "done", "ref": "", "depth": 0})
            self.assertEqual(calls, [])
            stale = self.tmp / ".partial" / "foo"
            stale.mkdir(parents=True)
            (stale / "junk").write_text("x")
            layout.clone({"url": "u", "path": "foo", "ref": "", "depth": 0})
        self.assertTrue((self.tmp / "foo").is_dir())
        self.assertFalse((self.tmp / "foo" / "junk").exists())


if __name__ == "__main__":
    unittest.main()
