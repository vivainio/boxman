#!/usr/bin/env python3
"""Verify a user's AI development tool installation."""

from __future__ import annotations

import shutil
import subprocess

COMMANDS = {
    "git": ("--version",),
    "gocryptfs": ("-version",),
    "gh": ("--version",),
    "podman": ("--version",),
    "node": ("--version",),
    "npm": ("--version",),
    "zipget": ("--version",),
    "aws": ("--version",),
    "herdr": ("--version",),
    "cship": ("--version",),
    "starship": ("--version",),
    "fd": ("--version",),
    "bat": ("--version",),
    "zoxide": ("--version",),
    "delta": ("--version",),
    "uv": ("--version",),
    "claude": ("--version",),
    "copilot": ("version",),
}


def main() -> None:
    failures = 0
    for command, args in COMMANDS.items():
        if not shutil.which(command):
            print(f"MISSING  {command}")
            failures += 1
            continue
        result = subprocess.run(
            (command, *args), capture_output=True, text=True, timeout=20, check=False
        )
        output = (result.stdout or result.stderr).splitlines()
        if result.returncode == 0:
            print(f"OK       {command:<10} {output[0] if output else ''}")
        else:
            print(f"FAILED   {command:<10} {output[0] if output else ''}")
            failures += 1

    if shutil.which("podman"):
        result = subprocess.run(
            ("podman", "info", "--format", "{{.Host.Security.Rootless}}"),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip() == "true":
            print("OK       rootless Podman")
        else:
            print("FAILED   Podman is not running rootless")
            failures += 1

    raise SystemExit(failures)


if __name__ == "__main__":
    main()
