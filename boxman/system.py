#!/usr/bin/env python3
"""Install shared Ubuntu packages and prepare users for rootless Podman."""

from __future__ import annotations

import argparse
import os
import pwd
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

SUBID_START = 100_000
SUBID_COUNT = 65_536
USERNAME = re.compile(r"[a-z_][a-z0-9_-]{0,31}")

RECIPE = Path(__file__).resolve().parent / "data" / "linux-tools.toml"
ZIPGET_URL = (
    "https://github.com/vivainio/zipget-rs/releases/latest/download/"
    "zipget-linux-x64-musl"
)


def log(message: str) -> None:
    print(f"[system-setup] {message}", flush=True)


def run(*args: str, env: dict[str, str] | None = None) -> None:
    log(f"running: {' '.join(args)}")
    subprocess.run(args, check=True, env=env)


def supports_system_packages(zipget: Path) -> bool:
    result = subprocess.run(
        (str(zipget), "recipe", "--help"),
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and "--system-only" in result.stdout


def install_zipget(temp: Path) -> Path:
    installed = shutil.which("zipget")
    if installed and supports_system_packages(Path(installed)):
        return Path(installed)

    zipget = temp / "zipget"
    log(f"downloading {ZIPGET_URL}")
    request = urllib.request.Request(ZIPGET_URL, headers={"User-Agent": "boxman"})
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
        zipget.write_bytes(response.read())
    zipget.chmod(0o755)
    if not supports_system_packages(zipget):
        sys.exit("zipget release does not support recipe --system-only yet")
    return zipget


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
        sys.exit(f"missing zipget recipe: {RECIPE}")
    with tempfile.TemporaryDirectory(prefix="linux-system-") as temp_name:
        zipget = install_zipget(Path(temp_name))
        apt_env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
        run(str(zipget), "recipe", str(RECIPE), "--system-only", env=apt_env)
    run("git", "lfs", "install", "--system")

    if args.packages_only:
        log("package-only system setup complete")
        return

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
