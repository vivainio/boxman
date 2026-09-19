# Set up the host

Boxman supports Ubuntu 24.04. Run the system step as root, and the user step separately from each login account. The first command works with Ubuntu's system Python before uv is installed:

```bash
sudo boxman system
boxman user
boxman verify
```

Run these commands from a Boxman checkout. `system` installs packages from the bundled `linux-tools.toml` recipe through zipget, configures Git LFS, allocates subordinate UID/GID ranges, and enables lingering for rootless Podman. Pass explicit usernames to `system` to limit user configuration, or omit them to select normal login accounts. It checks that the host is Ubuntu 24.04.

`user` installs the recipe's per-user tools, Node.js 22, Claude Code, Copilot CLI, uv, and AWS CLI. Run it as each target account, without sudo. Tool authentication is an interactive follow-up; Boxman does not copy credentials. `verify` reports installed tools and checks that Podman runs rootless.

The system step needs a zipget release supporting `recipe --system-only`; it downloads zipget when a suitable one is not already installed.

## Container variant

```bash
podman build -f container/Dockerfile -t boxman-dev .
```

The container runs the package-only system step and user tool installation. It does not configure host login accounts or systemd lingering. The vault's FUSE mount may need extra container privileges, so use the vault commands on a native host.
