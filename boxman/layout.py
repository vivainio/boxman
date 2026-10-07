#!/usr/bin/env python3
"""Clone the repositories listed in a layout file into /srv/boxman."""

from __future__ import annotations

import argparse
import fnmatch
import grp
import json
import os
import pwd
import re
import shlex
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

import yaml

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
    exclude = set(entry.get("exclude", []))
    into = safe_path(entry["into"]) if entry.get("into") else ""
    repos = []
    for found in github_repos(owner):
        name = found["name"]
        if not fnmatch.fnmatchcase(name, pattern) or name in exclude:
            continue
        if found["isFork"] or (found["isArchived"] and not entry.get("include_archived")):
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


def load_layout(path: Path) -> list[dict]:
    """Parse a layout file into a flat, de-duplicated list of repos to clone."""
    document = yaml.safe_load(path.read_text()) or {}
    entries = document.get("repos")
    if not entries:
        sys.exit(f"no repos in {path}")
    repos: list[dict] = []
    seen: set[str] = set()
    for entry in entries:
        for repo in expand(entry, document):
            if repo["path"] in seen:
                sys.exit(f"duplicate repo path: {repo['path']}")
            seen.add(repo["path"])
            repos.append(repo)
    return repos


def ensure_group_active() -> None:
    """Re-run under `sg boxman` when the group was added after this login began."""
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


def clone(repo: dict) -> None:
    """Clone into a partial directory and rename on success, so reruns can resume."""
    target = SHARED_DIR / repo["path"]
    if target.exists():
        log(f"skipping {repo['path']}: already cloned")
        return
    partial = SHARED_DIR / PARTIAL / repo["path"].replace("/", "__")
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
    parser.add_argument("file", type=Path, help="layout YAML file")
    parser.add_argument("-j", "--jobs", type=int, default=4, help="parallel clones (default: 4)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    repos = load_layout(args.file)
    ensure_group_active()
    if not SHARED_DIR.is_dir() or not os.access(SHARED_DIR, os.W_OK):
        sys.exit(f"{SHARED_DIR} is not writable; run `sudo boxman system` first")
    os.umask(0o002)
    done = 0
    failures = 0
    lock = threading.Lock()

    def work(repo: dict) -> None:
        nonlocal done, failures
        try:
            clone(repo)
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
