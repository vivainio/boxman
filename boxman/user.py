#!/usr/bin/env python3
"""Install per-user AI development tools without storing credentials."""

from __future__ import annotations

import hashlib
import os
import platform
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

NODE_MAJOR = 22
HOME = Path.home()
LOCAL_BIN = HOME / ".local" / "bin"
NODE_ROOT = HOME / ".local" / "share" / f"node-v{NODE_MAJOR}"
SCRIPT_DIR = Path(__file__).resolve().parent
TOOLS_RECIPE = SCRIPT_DIR / "data" / "linux-tools.toml"
ZIPGET_URL = (
    "https://github.com/vivainio/zipget-rs/releases/latest/download/"
    "zipget-linux-x64-musl"
)
CLAUDE_INSTALL_URL = "https://claude.ai/install.sh"
COPILOT_INSTALL_URL = "https://gh.io/copilot-install"
UV_INSTALL_URL = "https://astral.sh/uv/install.sh"


def log(message: str) -> None:
    print(f"[user-setup] {message}", flush=True)


def run(
    *args: str,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> None:
    log(f"running: {' '.join(args)}")
    subprocess.run(args, check=True, env=env, cwd=cwd)


def download(url: str, destination: Path) -> None:
    log(f"downloading {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "boxman"})
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
        destination.write_bytes(response.read())


def node_architecture() -> str:
    machine = platform.machine().lower()
    try:
        return {"x86_64": "x64", "aarch64": "arm64", "arm64": "arm64"}[machine]
    except KeyError:
        sys.exit(f"unsupported CPU architecture: {machine}")


def find_node_archive(checksums: str, architecture: str) -> tuple[str, str]:
    suffix = f"-linux-{architecture}.tar.xz"
    for line in checksums.splitlines():
        digest, _, filename = line.partition("  ")
        if filename.endswith(suffix):
            return filename, digest
    sys.exit(f"no Node.js {NODE_MAJOR} archive found for {architecture}")


def install_node(temp: Path) -> None:
    base_url = f"https://nodejs.org/dist/latest-v{NODE_MAJOR}.x"
    checksums_path = temp / "SHASUMS256.txt"
    download(f"{base_url}/SHASUMS256.txt", checksums_path)
    filename, expected_digest = find_node_archive(
        checksums_path.read_text(), node_architecture()
    )

    archive = temp / filename
    download(f"{base_url}/{filename}", archive)
    actual_digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if actual_digest != expected_digest:
        sys.exit("Node.js checksum verification failed")

    shutil.rmtree(NODE_ROOT, ignore_errors=True)
    NODE_ROOT.mkdir(parents=True)
    with tarfile.open(archive, "r:xz") as bundle:
        members = bundle.getmembers()
        top_directory = Path(members[0].name).parts[0]
        for member in members:
            member.name = str(Path(member.name).relative_to(top_directory))
            if member.name != ".":
                bundle.extract(member, NODE_ROOT, filter="data")

    for executable in ("node", "npm", "npx", "corepack"):
        link = LOCAL_BIN / executable
        link.unlink(missing_ok=True)
        link.symlink_to(NODE_ROOT / "bin" / executable)


def install_zipget() -> Path:
    zipget = LOCAL_BIN / "zipget"
    if not zipget.exists():
        download(ZIPGET_URL, zipget)
        zipget.chmod(0o755)
    return zipget


def install_recipe_tools(zipget: Path) -> None:
    if not TOOLS_RECIPE.is_file():
        sys.exit(f"missing zipget recipe: {TOOLS_RECIPE}")
    with tempfile.TemporaryDirectory(prefix="linux-tools-") as stage_name:
        stage = Path(stage_name)
        # The root setup handles apt packages. Keep this step compatible with
        # zipget releases predating [system_packages] support.
        lines = TOOLS_RECIPE.read_text().splitlines(keepends=True)
        user_lines = []
        in_system_packages = False
        for line in lines:
            if line.strip() == "[system_packages]":
                in_system_packages = True
            elif in_system_packages and line.lstrip().startswith("["):
                in_system_packages = False
            if not in_system_packages:
                user_lines.append(line)
        user_recipe = stage / "user-tools.toml"
        user_recipe.write_text("".join(user_lines))
        run(str(zipget), "recipe", str(user_recipe), cwd=stage)

        aws_installer = stage / "aws-cli-installer" / "aws" / "install"
        if not aws_installer.is_file():
            sys.exit(
                f"zipget did not produce the AWS CLI installer at {aws_installer}"
            )
        args = [
            str(aws_installer),
            "--install-dir",
            str(HOME / ".local" / "aws-cli"),
            "--bin-dir",
            str(LOCAL_BIN),
        ]
        if (HOME / ".local" / "aws-cli").exists():
            args.append("--update")
        run(*args)


def install_claude_code(temp: Path) -> None:
    installer = temp / "install-claude-code.sh"
    download(CLAUDE_INSTALL_URL, installer)
    run("bash", str(installer))


def install_copilot(temp: Path) -> None:
    installer = temp / "install-copilot.sh"
    download(COPILOT_INSTALL_URL, installer)
    env = {**os.environ, "PREFIX": str(HOME / ".local")}
    run("bash", str(installer), env=env)


def install_uv(temp: Path) -> None:
    installer = temp / "install-uv.sh"
    download(UV_INSTALL_URL, installer)
    env = {
        **os.environ,
        "UV_INSTALL_DIR": str(LOCAL_BIN),
        "UV_NO_MODIFY_PATH": "1",
    }
    run("sh", str(installer), env=env)


def configure_git_credentials() -> None:
    """Use tempkeys for HTTPS credentials from github.com only."""
    setting = "credential.https://github.com.helper"
    helper = f"!{shlex.quote(str(LOCAL_BIN / 'tempkeys'))} --user git-credential"
    run("git", "config", "--global", "--replace-all", setting, "")
    run("git", "config", "--global", "--add", setting, helper)


def main() -> None:
    if os.geteuid() == 0:
        sys.exit("boxman user must run as the target user, not root")

    LOCAL_BIN.mkdir(parents=True, exist_ok=True)
    NODE_ROOT.parent.mkdir(parents=True, exist_ok=True)

    zipget = install_zipget()
    install_recipe_tools(zipget)

    with tempfile.TemporaryDirectory(prefix="boxman-user-") as temp_name:
        temp = Path(temp_name)
        install_node(temp)
        install_claude_code(temp)
        install_copilot(temp)
        install_uv(temp)

    run(str(LOCAL_BIN / "uv"), "tool", "install", "--upgrade", "tempkeys")
    configure_git_credentials()

    log("user setup complete; authenticate claude, copilot, and gh interactively")


if __name__ == "__main__":
    main()
