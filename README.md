# boxman

Set up and manage an Ubuntu 24.04 development box. `boxman` is a Python command
with system, user, and vault operations.

## Install a host

From this checkout, run the system step as root, then the user step from each
login account. The first command works with the Ubuntu system Python before
`uv` is installed:

```bash
sudo python3 -m boxman.cli system
python3 -m boxman.cli user
```

With `uv` available, run it without installing the package:

```bash
uvx --from . boxman vault status
```

After publishing the repository, `uvx --from
git+https://github.com/vivainio/boxman boxman ...` can run the same console
command. For a persistent command, use `uv tool install .` from this checkout.

`boxman system` uses zipget to install the apt packages declared in
`linux-tools.toml`, sets up Git LFS, and configures rootless Podman for normal
login users. It accepts explicit usernames, or `--packages-only` for a
container build. It downloads zipget if no version supporting
`recipe --system-only` is on root's PATH. A fresh install needs a released
zipget with that option.

`boxman user` installs the tools in the recipe, Node.js 22, Claude Code,
Copilot CLI, and uv. Run the user step for each account.
Run `boxman verify` afterward to check the installed commands and rootless
Podman.

The same package set can be used in a dev container:

```bash
podman build -f container/Dockerfile -t boxman-dev .
```

The container build skips host login-user and systemd configuration. FUSE
mounting may require additional container privileges, so the vault commands
are intended for the native host.

## Private directory

The recipe installs `gocryptfs` and FUSE 3. Each user initializes their own
vault once and unlocks it after a reboot or unmount:

```bash
uvx --from . boxman vault init
uvx --from . boxman vault unlock
uvx --from . boxman vault status
uvx --from . boxman claude  # starts Claude with its config in the mounted vault
uvx --from . boxman vault lock  # after stopping processes that use the mount
```

Encrypted files live in `~/.private.cipher`; the plaintext mount is
`~/private`. Initialization and unlocking prompt for the password. Keep the
password and gocryptfs recovery key outside the host. Back up the encrypted
directory, including `gocryptfs.conf`, while keeping the plaintext mount out
of backups.

`boxman claude` refuses to run unless the vault is mounted. Use it before the
first Claude login. It sets `CLAUDE_CONFIG_DIR` to `~/private/claude` and does
not move existing credentials from `~/.claude`. Configure other tools'
credential locations separately if they should use the vault. Persistent
agents need the vault mounted while they use credentials. Locking fails while
a process holds files in the mount open.

The vault protects its backing files and snapshots while locked. It does not
hide credentials from the user's running processes or a host administrator
while unlocked. Keep home directory permissions private to each Unix owner.
