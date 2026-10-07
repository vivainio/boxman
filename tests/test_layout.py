from pathlib import Path

import pytest

from boxman import layout

LISTING = [
    {"name": "foo-a", "isArchived": False, "isFork": False},
    {"name": "foo-b", "isArchived": False, "isFork": False},
    {"name": "foo-old", "isArchived": False, "isFork": False},
    {"name": "foo-dead", "isArchived": True, "isFork": False},
    {"name": "foo-fork", "isArchived": False, "isFork": True},
    {"name": "bar", "isArchived": False, "isFork": False},
]


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "layout.yaml"
    path.write_text(text)
    return path


@pytest.fixture(autouse=True)
def fake_gh(monkeypatch):
    monkeypatch.setattr(layout, "github_repos", lambda owner: LISTING)


def test_literal_url_and_default_ref(tmp_path):
    path = write(
        tmp_path,
        "ref: main\nrepos:\n  - repo: me/boxman\n  - url: https://h/x/y.git\n    path: tools/y\n    ref: dev\n",
    )
    assert layout.load_layout(path) == [
        {"url": "https://github.com/me/boxman.git", "path": "boxman", "ref": "main", "depth": 0},
        {"url": "https://h/x/y.git", "path": "tools/y", "ref": "dev", "depth": 0},
    ]


def test_glob_skips_excluded_archived_and_forks(tmp_path):
    path = write(
        tmp_path, "repos:\n  - repo: co/foo-*\n    exclude: [foo-old]\n    into: services\n"
    )
    assert [r["path"] for r in layout.load_layout(path)] == ["services/foo-a", "services/foo-b"]


def test_glob_can_include_archived(tmp_path):
    path = write(tmp_path, "repos:\n  - repo: co/foo-*\n    include_archived: true\n")
    assert "foo-dead" in [r["path"] for r in layout.load_layout(path)]


@pytest.mark.parametrize(
    "entry",
    ["repo: co/x\n    path: ../x", "repo: co/x\n    path: /etc/x", "repo: co/foo-*\n    into: ..", "repo: co/foo-*\n    path: x", "repo: bare", "repo: '*/x'"],
)
def test_rejects_bad_entries(tmp_path, entry):
    with pytest.raises(SystemExit):
        layout.load_layout(write(tmp_path, f"repos:\n  - {entry}\n"))


def test_rejects_duplicates_and_empty(tmp_path):
    with pytest.raises(SystemExit):
        layout.load_layout(write(tmp_path, "repos:\n  - repo: a/x\n  - repo: b/x\n"))
    with pytest.raises(SystemExit):
        layout.load_layout(write(tmp_path, ""))


def test_depth_default_and_override(tmp_path):
    path = write(tmp_path, "depth: 1\nrepos:\n  - repo: a/x\n  - repo: a/y\n    depth: 0\n")
    assert [r["depth"] for r in layout.load_layout(path)] == [1, 0]


def test_rejects_partial_directory_name(tmp_path):
    with pytest.raises(SystemExit):
        layout.load_layout(write(tmp_path, "repos:\n  - repo: a/x\n    path: .partial/x\n"))


def test_clone_uses_partial_dir_then_renames(tmp_path, monkeypatch):
    monkeypatch.setattr(layout, "SHARED_DIR", tmp_path)
    calls = []

    def fake_run(cmd, check, env):
        calls.append(cmd)
        Path(cmd[-1]).mkdir()

    monkeypatch.setattr(layout.subprocess, "run", fake_run)
    layout.clone({"url": "u", "path": "svc/foo", "ref": "main", "depth": 1})
    partial = str(tmp_path / ".partial" / "svc__foo")
    assert calls[0] == ["git", "-c", "core.sharedRepository=group", "clone", "--branch", "main", "--depth", "1", "u", partial]
    assert (tmp_path / "svc" / "foo").is_dir() and not Path(partial).exists()


def test_clone_skips_finished_and_discards_stale_partial(tmp_path, monkeypatch):
    monkeypatch.setattr(layout, "SHARED_DIR", tmp_path)
    calls = []
    monkeypatch.setattr(layout.subprocess, "run", lambda cmd, check, env: (calls.append(cmd), Path(cmd[-1]).mkdir()))
    (tmp_path / "done").mkdir()
    layout.clone({"url": "u", "path": "done", "ref": "", "depth": 0})
    assert calls == []
    stale = tmp_path / ".partial" / "foo"
    stale.mkdir(parents=True)
    (stale / "junk").write_text("x")
    layout.clone({"url": "u", "path": "foo", "ref": "", "depth": 0})
    assert (tmp_path / "foo").is_dir() and not (tmp_path / "foo" / "junk").exists()
