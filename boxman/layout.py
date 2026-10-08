#!/usr/bin/env python3
"""Clone the repositories listed in a layout file into /srv/boxman."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

from boxman._vendor import mfloader
from boxman.system import GROUP, SHARED_DIR

REEXEC_FLAG = "BOXMAN_LAYOUT_SG"
PARTIAL = ".partial"


def log(message: str) -> None:
    print(f"[layout] {message}", flush=True)


GLOB = re.compile(r"[*?\[]")


def github_repos(owner: str) -> list[dict]:
    """List an owner's repositories through the authenticated gh CLI."""
    result = subprocess.run(
        ("gh", "repo", "list", owner, "--limit", "1000", "--json", "name,isArchived,isFork"),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        sys.exit(f"gh repo list {owner} failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def safe_path(name: str) -> str:
    relative = PurePosixPath(name)
    if not name or relative.is_absolute() or ".." in relative.parts or relative.parts[0] == PARTIAL:
        sys.exit(f"repo path must stay inside {SHARED_DIR}: {name!r}")
    return name


def expand(entry: dict, defaults: dict) -> list[dict]:
    """Turn one layout entry into concrete repos with paths relative to SHARED_DIR."""
    ref = entry.get("ref", defaults.get("ref", ""))
    depth = int(entry.get("depth", defaults.get("depth", 0)))
    if "url" in entry:
        url = entry["url"]
        name = entry.get("path") or PurePosixPath(url.rstrip("/")).name.removesuffix(".git")
        return [{"url": url, "path": safe_path(name), "ref": ref, "depth": depth}]
    spec = entry.get("repo", "")
    owner, _, pattern = spec.partition("/")
    if not owner or not pattern or "/" in pattern or GLOB.search(owner):
        sys.exit(f"repo must look like owner/name or owner/pattern: {spec!r}")
    if not GLOB.search(pattern):
        name = entry.get("path") or pattern
        return [{"url": f"https://github.com/{spec}.git", "path": safe_path(name), "ref": ref, "depth": depth}]
    if "path" in entry:
        sys.exit(f"{spec}: use 'into', not 'path', with a pattern")
    into = safe_path(entry["into"]) if entry.get("into") else ""
    repos = []
    for found in github_repos(owner):
        name = found["name"]
        if not fnmatch.fnmatchcase(name, pattern):
            continue
        if found["isFork"] or (found["isArchived"] and not entry.get("include_archived", defaults.get("include_archived"))):
            continue
        repos.append(
            {
                "url": f"https://github.com/{owner}/{name}.git",
                "path": f"{into}/{name}" if into else name,
                "ref": ref,
                "depth": depth,
            }
        )
    if not repos:
        log(f"warning: {spec} matched no repositories")
    return repos


def slug(repo: dict) -> str:
    """owner/name for GitHub repos (what exclude patterns match against), else the URL."""
    return repo["url"].removeprefix("https://github.com/").removesuffix(".git")


DEFAULTABLE = ("ref", "depth", "include_archived")


def convert(entry: dict) -> dict:
    """Give an entry typed options: miniformat reads every scalar as a string."""
    out = {}
    for key in ("repo", "url", "path", "into", "ref", "depth", "include_archived"):
        value = entry.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            sys.exit(f"{key} must be a string, got {value!r}")
        if key == "depth":
            if not value.isdigit():
                sys.exit(f"depth must be a number, got {value!r}")
            value = int(value)
        elif key == "include_archived":
            if value not in ("true", "false"):
                sys.exit(f"include_archived must be true or false, got {value!r}")
            value = value == "true"
        out[key] = value
    unknown = set(entry) - set(out) - {"repo", "url"}
    if unknown:
        sys.exit(f"unknown option(s) {sorted(unknown)} in {entry}")
    return out


def read_document(path: Path) -> dict:
    """Read a layout file (miniformat) into its top-level map."""
    try:
        with path.open() as handle:
            document = mfloader.load(handle)
    except mfloader.MiniFormatError as exc:
        sys.exit(f"{path}: {exc}")
    if not isinstance(document, dict):
        sys.exit(f"{path}: expected a map with a `repos` key")
    return document


def load_layout(path: Path) -> tuple[Path, list[dict]]:
    """Parse a layout file into the clone directory and a flat, de-duplicated list of repos.

    The file is miniformat (YAML syntax; every scalar is a string): a map with `repos`
    plus `ec2` (read by `boxman ec2`, ignored
    here) and the defaults `ref`, `depth` and
    `include_archived`. `repos` is a map
    `{dir: PATH, include: [...], exclude: [...]}` (`dir` is where to clone, default
    /srv/boxman); exclude patterns (globs on `owner/name`) remove
    matches from the whole include list. An include entry is a URL, `owner/name`,
    `owner/pattern` or a map with `repo`/`url` and options. `#+include FILE` works too.
    """
    document = read_document(path)
    if unknown := set(document) - set(DEFAULTABLE) - {"repos", "ec2"}:
        sys.exit(f"{path}: unknown key(s) {sorted(unknown)}")
    block = document.get("repos")
    if not isinstance(block, dict):
        sys.exit(f"{path}: repos must be a map with `include` and optional `exclude` lists")
    if unknown := set(block) - {"dir", "include", "exclude"}:
        sys.exit(f"{path}: unknown key(s) {sorted(unknown)} in repos")
    root = SHARED_DIR
    if "dir" in block:
        value = block["dir"]
        root = Path(value).expanduser() if isinstance(value, str) else None
        if root is None or not root.is_absolute():
            sys.exit(f"{path}: repos.dir must be an absolute path (or start with ~), got {value!r}")
    entries = block.get("include")
    excludes = block.get("exclude") or []
    if not entries:
        sys.exit(f"no repos in {path}")
    if not isinstance(entries, list):
        sys.exit(f"{path}: repos.include must be a list")
    if not isinstance(excludes, list) or not all(isinstance(p, str) for p in excludes):
        sys.exit(f"{path}: repos.exclude must be a list of patterns")
    defaults = convert({k: v for k, v in document.items() if k not in ("repos", "ec2")})
    repos: list[dict] = []
    for entry in entries:
        if isinstance(entry, str):
            entry = {"url": entry} if "://" in entry or entry.startswith("git@") else {"repo": entry}
        elif not isinstance(entry, dict):
            sys.exit(f"{path}: repos.include entries must be strings or maps, got {entry!r}")
        repos.extend(expand(convert(entry), defaults))
    for pattern in excludes:
        repos = [r for r in repos if not fnmatch.fnmatchcase(slug(r), pattern)]
    seen: set[str] = set()
    for repo in repos:
        if repo["path"] in seen:
            sys.exit(f"duplicate repo path: {repo['path']}")
        seen.add(repo["path"])
    return root, repos


def ensure_group_active() -> None:
    """Re-run under `sg boxman` when the group was added after this login began."""
    import grp
    import pwd

    try:
        group = grp.getgrnam(GROUP)
    except KeyError:
        sys.exit(f"group {GROUP!r} does not exist; run `sudo boxman system` first")
    if group.gr_gid in os.getgroups():
        return
    if pwd.getpwuid(os.getuid()).pw_name not in group.gr_mem or os.environ.get(REEXEC_FLAG):
        sys.exit(f"you are not in the {GROUP!r} group; run `sudo boxman system <user>` and log in again")
    command = shlex.join([os.path.abspath(sys.argv[0]), *sys.argv[1:]])
    os.environ[REEXEC_FLAG] = "1"
    os.execvp("sg", ["sg", GROUP, "-c", command])


def clone(repo: dict, root: Path) -> None:
    """Clone into a partial directory and rename on success, so reruns can resume."""
    target = root / repo["path"]
    if target.exists():
        log(f"skipping {repo['path']}: already cloned")
        return
    partial = root / PARTIAL / repo["path"].replace("/", "__")
    partial.parent.mkdir(exist_ok=True)
    shutil.rmtree(partial, ignore_errors=True)
    command = ["git", "-c", "core.sharedRepository=group", "clone"]
    if repo["ref"]:
        command += ["--branch", repo["ref"]]
    if repo["depth"]:
        command += ["--depth", str(repo["depth"])]
    command += [repo["url"], str(partial)]
    log(f"cloning {repo['url']} -> {target}")
    subprocess.run(command, check=True, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    target.parent.mkdir(parents=True, exist_ok=True)
    partial.rename(target)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["apply"])
    parser.add_argument("file", type=Path, help="layout file")
    parser.add_argument("-j", "--jobs", type=int, default=4, help="parallel clones (default: 4)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    root, repos = load_layout(args.file)
    if root == SHARED_DIR:
        ensure_group_active()
        if not root.is_dir() or not os.access(root, os.W_OK):
            sys.exit(f"{root} is not writable; run `sudo boxman system` first")
    else:
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            sys.exit(f"cannot create {root}: {error}")
        if not os.access(root, os.W_OK):
            sys.exit(f"{root} is not writable")
    os.umask(0o002)
    done = 0
    failures = 0
    lock = threading.Lock()

    def work(repo: dict) -> None:
        nonlocal done, failures
        try:
            clone(repo, root)
            outcome = "done"
        except (OSError, subprocess.CalledProcessError) as error:
            outcome = f"FAILED: {error}"
            with lock:
                failures += 1
        with lock:
            done += 1
            log(f"[{done}/{len(repos)}] {repo['path']} {outcome}")

    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        list(pool.map(work, repos))
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
