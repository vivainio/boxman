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
        path = self.tmp / "layout.toml"
        path.write_text(text)
        return layout.load_layout(path)

    def test_literal_url_and_default_ref(self) -> None:
        repos = self.load(
            'ref = "main"\n[[repos]]\nrepo = "me/boxman"\n[[repos]]\nurl = "https://h/x/y.git"\npath = "tools/y"\nref = "dev"\n'
        )
        self.assertEqual(
            repos,
            [
                {"url": "https://github.com/me/boxman.git", "path": "boxman", "ref": "main", "depth": 0},
                {"url": "https://h/x/y.git", "path": "tools/y", "ref": "dev", "depth": 0},
            ],
        )

    def test_glob_skips_excluded_archived_and_forks(self) -> None:
        repos = self.load('[[repos]]\nrepo = "co/foo-*"\nexclude = ["foo-old"]\ninto = "services"\n')
        self.assertEqual([r["path"] for r in repos], ["services/foo-a", "services/foo-b"])

    def test_glob_can_include_archived(self) -> None:
        repos = self.load('[[repos]]\nrepo = "co/foo-*"\ninclude_archived = true\n')
        self.assertIn("foo-dead", [r["path"] for r in repos])

    def test_depth_default_and_override(self) -> None:
        repos = self.load('depth = 1\n[[repos]]\nrepo = "a/x"\n[[repos]]\nrepo = "a/y"\ndepth = 0\n')
        self.assertEqual([r["depth"] for r in repos], [1, 0])

    def test_rejects_bad_entries(self) -> None:
        for entry in (
            'repo = "co/x"\npath = "../x"',
            'repo = "co/x"\npath = "/etc/x"',
            'repo = "co/x"\npath = ".partial/x"',
            'repo = "co/foo-*"\ninto = ".."',
            'repo = "co/foo-*"\npath = "x"',
            'repo = "bare"',
            'repo = "*/x"',
        ):
            with self.subTest(entry=entry), self.assertRaises(SystemExit):
                self.load(f"[[repos]]\n{entry}\n")

    def test_rejects_duplicates_and_empty(self) -> None:
        with self.assertRaises(SystemExit):
            self.load('[[repos]]\nrepo = "a/x"\n[[repos]]\nrepo = "b/x"\n')
        with self.assertRaises(SystemExit):
            self.load("")
        with self.assertRaises(SystemExit):
            self.load("repos: [")

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
