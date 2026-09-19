"""Boxman commands backed by the default tempkeys keyset."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

MAX_DOCUMENT = 1024 * 1024


def fail(message: str) -> None:
    raise SystemExit(message)


def parse_document(data: bytes) -> dict[str, str]:
    if len(data) > MAX_DOCUMENT:
        fail("Secrets document exceeds 1 MiB")
    try:
        document = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        fail(f"Secrets input must be a JSON object: {exc}")
    if not isinstance(document, dict) or not document or any(
        not isinstance(name, str) or not name.isidentifier() or not name.isascii()
        or name[0].isdigit() or not isinstance(value, str) or not value
        for name, value in document.items()
    ):
        fail("Secrets input must be a nonempty JSON object with environment-style names and nonempty string values")
    return document


def tempkeys(*args: str, data: bytes | None = None) -> None:
    executable = Path.home() / ".local" / "bin" / "tempkeys"
    if not executable.is_file():
        fail("tempkeys is missing; run boxman user to install it")
    try:
        subprocess.run([str(executable), "--user", *args], input=data, check=True)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode) from exc


def receive() -> None:
    if os.geteuid() == 0:
        fail("Run secrets commands as a normal user")
    data = sys.stdin.buffer.read(MAX_DOCUMENT + 1)
    parse_document(data)
    tempkeys("load", data=data)


def read(name: str) -> None:
    if os.geteuid() == 0:
        fail("Run secrets commands as a normal user")
    tempkeys("get", name)


def main(argv: list[str]) -> None:
    if argv == ["receive"]:
        receive()
    elif len(argv) == 2 and argv[0] == "read":
        read(argv[1])
    else:
        fail("Usage: boxman secrets {receive|read NAME}")
