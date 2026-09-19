# boxman

[Read the Boxman book](https://vivainio.github.io/boxman/) for the concepts, setup steps, and command reference.

Set up and manage an Ubuntu 24.04 development box. `boxman` is a Python command
with system, user, and vault operations.

## Install a host

From this checkout, run the system step as root, then the user step from each
login account. The first command works with the Ubuntu system Python before
`uv` is installed:

```bash
sudo boxman system
boxman user
```

Install the published command with uv:

```bash
uv tool install --upgrade boxman
boxman vault status
```

For local development, use `uv tool install --editable .` from this checkout.

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
boxman vault init
boxman vault unlock
boxman vault status
boxman claude  # starts Claude with its config in the mounted vault
boxman vault lock  # after stopping processes that use the mount
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

## EC2 host

Install the optional AWS dependency, then create
`${XDG_CONFIG_HOME:-~/.config}/boxman/ec2.toml`:

```bash
uv tool install --upgrade 'boxman[ec2]'
mkdir -p "${XDG_CONFIG_HOME:-$HOME/.config}/boxman"
```

```toml
[ec2]
profile = "your-aws-profile"
region = "your-region"
stack_name = "your-stack-name"

[ec2.tags]
Owner = "your-owner"
Environment = "your-environment"
```

The config contains no credentials; AWS uses the named profile. Supply the tags
required by your account. The TOML file selects the AWS profile, region, stack,
and tags. Command-line options override the file;
`--tag KEY=VALUE` adds or overrides a tag. `--config PATH` selects another TOML
file. Put global options before the action:

```bash
boxman ec2 init --vpc-id vpc-... --subnet-id subnet-... \
  --instance-type t3.xlarge --volume-size-gb 100 \
  --instance-name mybox \
  --ami-id /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id
boxman ec2 deploy
boxman ec2 status
boxman ec2 start
boxman ec2 stop
boxman ec2 connect -u myuser
boxman ec2 ssh -u myuser
boxman ec2 ssh-config -u myuser --alias mybox
boxman ec2 run -u myuser 'uname -a'
```

`init` writes `${XDG_CONFIG_HOME:-~/.config}/boxman/stacks/<stack_name>.yaml`
with the instance settings as parameter defaults and refuses to overwrite an
existing file. Edit that YAML to customize the stack. `deploy` reads the YAML
for the configured stack name and sends its defaults as CloudFormation
parameters. Tags from `[ec2.tags]` and repeatable
`--tag KEY=VALUE` options become CloudFormation stack tags. A single config
file selects one stack; use `--config PATH` for another box.

Deployment creates or updates a CloudFormation stack containing an Ubuntu EC2
instance, an SSM role, and an EC2 Instance Connect Endpoint. SSH uses that
endpoint and installs a generated public key through SSM. `ssh-config` writes a
marked host entry to `~/.ssh/config`. The stack name selects the instance for
all subsequent commands. Starting, stopping, and deploying incur AWS charges.

## Release

Build the Zensical book locally with `zensical build --clean`. The docs workflow
publishes it to GitHub Pages when documentation changes on `main`.

A published GitHub release triggers the PyPI workflow. Use a `vX.Y.Z` tag;
the workflow sets the package version from the release tag, builds the wheel
and source distribution, and publishes with PyPI trusted publishing. Configure
a PyPI trusted publisher for repository `vivainio/boxman`, workflow
`publish.yml`, environment `pypi` before the first release.
