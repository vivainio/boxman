#!/usr/bin/env python3
"""Install shared Ubuntu packages and prepare users for rootless Podman."""

from __future__ import annotations

import argparse
import os
import pwd
import re
import subprocess
import sys
import tomllib
from pathlib import Path

SUBID_START = 100_000
SUBID_COUNT = 65_536
USERNAME = re.compile(r"[a-z_][a-z0-9_-]{0,31}")

GROUP = "boxman"
SHARED_DIR = Path("/srv/boxman")

RECIPE = Path(__file__).resolve().parent / "data" / "linux-tools.toml"


def log(message: str) -> None:
    print(f"[system-setup] {message}", flush=True)


def run(*args: str, env: dict[str, str] | None = None) -> None:
    log(f"running: {' '.join(args)}")
    subprocess.run(args, check=True, env=env)


def apt_packages() -> list[str]:
    with RECIPE.open("rb") as handle:
        packages = tomllib.load(handle).get("system_packages", {}).get("apt", [])
    if not packages:
        sys.exit(f"no [system_packages] apt list in {RECIPE}")
    return packages


def os_release() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in Path("/etc/os-release").read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value.strip('"')
    return values


def allocated_ranges(path: Path) -> list[tuple[int, int]]:
    ranges = []
    for line in path.read_text().splitlines():
        try:
            _, start, count = line.split(":")
            ranges.append((int(start), int(count)))
        except ValueError:
            continue
    return ranges


def next_subid(path: Path) -> int:
    next_id = max(
        (start + count for start, count in allocated_ranges(path)),
        default=SUBID_START,
    )
    next_id = max(next_id, SUBID_START)
    remainder = (next_id - SUBID_START) % SUBID_COUNT
    return next_id if remainder == 0 else next_id + SUBID_COUNT - remainder


def has_subid(path: Path, username: str) -> bool:
    return any(
        line.partition(":")[0] == username for line in path.read_text().splitlines()
    )


def ensure_subid(username: str, path: Path, flag: str) -> None:
    if has_subid(path, username):
        return
    start = next_subid(path)
    end = start + SUBID_COUNT - 1
    run("usermod", flag, f"{start}-{end}", username)


def ensure_shared_dir(users: list[str]) -> None:
    """Create the boxman group and the setgid /srv/boxman directory it owns."""
    if subprocess.run(("getent", "group", GROUP), capture_output=True).returncode != 0:
        run("groupadd", "--system", GROUP)
    for username in users:
        run("usermod", "-aG", GROUP, username)
    SHARED_DIR.mkdir(parents=True, exist_ok=True)
    run("chgrp", GROUP, str(SHARED_DIR))
    run("chmod", "2775", str(SHARED_DIR))
    pattern = f"{SHARED_DIR}/*"
    known = subprocess.run(
        ("git", "config", "--system", "--get-all", "safe.directory"),
        capture_output=True,
        text=True,
    ).stdout.split()
    if pattern not in known:
        run("git", "config", "--system", "--add", "safe.directory", pattern)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--packages-only",
        action="store_true",
        help="install shared packages without configuring login users",
    )
    parser.add_argument("users", nargs="*", help="existing Unix users to configure")
    return parser.parse_args(argv)


def regular_users() -> list[str]:
    """Return normal login users, excluding nobody and system accounts."""
    return sorted(
        entry.pw_name
        for entry in pwd.getpwall()
        if 1_000 <= entry.pw_uid < 65_534
        and entry.pw_shell not in {"/usr/sbin/nologin", "/bin/false"}
    )


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if os.geteuid() != 0:
        sys.exit("boxman system must run as root")

    release = os_release()
    if release.get("ID") != "ubuntu" or release.get("VERSION_ID") != "24.04":
        sys.exit(
            "Ubuntu 24.04 is required "
            f"(found {release.get('ID', 'unknown')} {release.get('VERSION_ID', 'unknown')})"
        )

    if args.packages_only and args.users:
        sys.exit("--packages-only cannot be combined with usernames")

    users = [] if args.packages_only else (args.users or regular_users())
    if not args.packages_only and not args.users:
        log(f"auto-detected login users: {', '.join(users) if users else '(none)'}")

    for username in users:
        if not USERNAME.fullmatch(username):
            sys.exit(f"invalid Unix username: {username!r}")
        try:
            pwd.getpwnam(username)
        except KeyError:
            raise SystemExit(f"user does not exist: {username}") from None

    if not RECIPE.is_file():
        sys.exit(f"missing recipe: {RECIPE}")
    apt_env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
    run("apt-get", "update", env=apt_env)
    run("apt-get", "install", "-y", *apt_packages(), env=apt_env)
    run("git", "lfs", "install", "--system")

    if args.packages_only:
        log("package-only system setup complete")
        return

    ensure_shared_dir(users)

    for username in users:
        log(f"configuring rootless Podman prerequisites for {username}")
        ensure_subid(username, Path("/etc/subuid"), "--add-subuids")
        ensure_subid(username, Path("/etc/subgid"), "--add-subgids")
        run("loginctl", "enable-linger", username)

    if not users:
        log("no login users found; user Podman setup skipped")
    log("system setup complete")


if __name__ == "__main__":
    main()
