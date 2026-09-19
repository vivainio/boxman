#!/usr/bin/env python3
"""Set up and manage a Linux development box."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def fail(message: str) -> None:
    raise SystemExit(message)


def run(*args: str) -> None:
    result = subprocess.run(args, check=False)
    if result.returncode:
        raise SystemExit(result.returncode)


def mounted(path: Path) -> bool:
    return subprocess.run(
        ("mountpoint", "-q", str(path)),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


def vault_paths() -> tuple[Path, Path]:
    home = Path.home()
    cipher, plain = home / ".private.cipher", home / "private"
    if cipher.is_symlink() or plain.is_symlink():
        fail("Private vault paths must not be symlinks")
    return cipher, plain


def private_dir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)


def vault(action: str) -> None:
    if os.geteuid() == 0:
        fail("Run vault commands as your login user, not root")
    os.umask(0o077)
    cipher, plain = vault_paths()

    if action == "status":
        print("Private vault is unlocked" if mounted(plain) else "Private vault is locked")
    elif action == "init":
        if mounted(plain):
            fail("Private vault is already unlocked")
        if (cipher / "gocryptfs.conf").exists():
            fail("Private vault is already initialized")
        private_dir(cipher)
        private_dir(plain)
        if any(cipher.iterdir()):
            fail("Encrypted backing directory is not empty")
        if any(plain.iterdir()):
            fail("Plaintext mount directory is not empty")
        run("gocryptfs", "-init", str(cipher))
        print("Keep the password and recovery key outside this host.")
    elif action == "unlock":
        if not (cipher / "gocryptfs.conf").is_file():
            fail("Private vault is not initialized; run boxman vault init")
        private_dir(cipher)
        private_dir(plain)
        if mounted(plain):
            print("Private vault is already unlocked")
            return
        if any(plain.iterdir()):
            fail("Plaintext mount directory is not empty; refusing to hide its files")
        run("gocryptfs", str(cipher), str(plain))
    elif action == "lock":
        if not mounted(plain):
            print("Private vault is already locked")
            return
        run("fusermount3", "-u", str(plain))
    else:
        fail("Usage: boxman vault {init|unlock|lock|status}")


def claude(args: list[str]) -> None:
    if os.geteuid() == 0:
        fail("Run Claude as your login user, not root")
    _, plain = vault_paths()
    if not mounted(plain):
        fail("Private vault is locked; run boxman vault unlock first")
    config = plain / "claude"
    if config.is_symlink():
        fail("Claude config directory must not be a symlink")
    os.umask(0o077)
    private_dir(config)
    os.environ["CLAUDE_CONFIG_DIR"] = str(config)
    os.execvp("claude", ["claude", *args])


def main() -> None:
    if len(sys.argv) < 2:
        fail("Usage: boxman {system|user|vault|claude|secrets|verify|ec2} ...")
    command, *args = sys.argv[1:]
    if command == "system":
        from boxman import system

        system.main(args)
    elif command == "user":
        if args:
            fail("Usage: boxman user")
        from boxman import user

        user.main()
    elif command == "vault":
        if len(args) != 1:
            fail("Usage: boxman vault {init|unlock|lock|status}")
        vault(args[0])
    elif command == "claude":
        claude(args)
    elif command == "ec2":
        from boxman import ec2

        ec2.main(args)
    elif command == "secrets":
        from boxman import secrets

        secrets.main(args)
    elif command == "verify":
        if args:
            fail("Usage: boxman verify")
        from boxman import verify

        verify.main()
    else:
        fail("Usage: boxman {system|user|vault|claude|secrets|verify|ec2} ...")


if __name__ == "__main__":
    main()
