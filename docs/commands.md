# Command reference

| Command | Purpose |
| --- | --- |
| `boxman system [USER ...]` | Install shared packages and prepare login users; run as root |
| `boxman system --packages-only` | Install packages without user or systemd setup |
| `boxman user` | Install tools for the current login user |
| `boxman verify` | Check installed tools and rootless Podman |
| `boxman vault init` | Initialize the current user's encrypted directory |
| `boxman vault unlock` / `lock` / `status` | Mount, unmount, or check that directory |
| `boxman claude [ARGS ...]` | Launch Claude Code with config in the unlocked vault |
| `boxman secrets read NAME` | Print one value from the default tempkeys keyset |
| `boxman secrets receive` | Replace that keyset from JSON on stdin, as the target Unix user |
| `boxman ec2 --machine NAME init` | Create a per-machine YAML template from explicit instance settings |
| `boxman ec2 deploy` | Create or update the CloudFormation stack |
| `boxman ec2 status` | Show instance state and SSM registration |
| `boxman ec2 start` / `stop` | Start or stop the instance and wait |
| `boxman ec2 destroy [--yes] [--keep-local]` | Delete the stack with its instance and volume, then the local files |
| `boxman ec2 connect [-u USER]` | Start an interactive SSM session |
| `boxman ec2 run [-u USER] COMMAND` | Run a command through SSM without a terminal |
| `boxman ec2 setup --user USER [--bootstrap-user BOOTSTRAP]` | Bootstrap the remote host and create/configure USER through EIC SSH |
| `boxman ec2 ssh -u USER [-c CONTAINER]` | SSH through the Instance Connect Endpoint |
| `boxman ec2 ssh-config -u USER [--alias NAME] [--herdr]` | Install a key and write a local SSH/optional Herdr machine entry |
| `boxman ec2 secrets send FILE -u USER` | Upload a local JSON secrets document over SSH |

Use `boxman ec2 --help` and `boxman ec2 ACTION --help` for option details. EC2 global options go before `ACTION`.

## boxman layout

`boxman system` creates the `boxman` group, adds the login users to it, and
makes `/srv/boxman` group-owned with mode `2775` (setgid) so shared checkouts
stay writable by every user in the group.

`boxman layout FILE` clones the repositories described by a layout file
into `/srv/boxman`. Clones run in parallel (`-j N`, default 4) into `/srv/boxman/.partial` and are
renamed into place when finished, so an interrupted run can simply be repeated:
finished repositories are skipped and stale partial clones are discarded. If the group was added after the current login began, the command
re-runs itself under `sg boxman`. Patterns need an authenticated `gh`.

The file is [miniformat](https://github.com/vivainio/miniformat): YAML syntax with
every scalar a string (so `depth: 1` and `include_archived: true` are converted by
Boxman, and `no` or `1.10` stay as written). It is a map with `repos` plus the
optional key `ec2` (machine settings used by `boxman ec2 --config FILE`; see
[EC2](ec2.md)) and the defaults `ref` (branch or tag), `depth` (clone depth,
0 = full) and `include_archived`. `repos` is itself a map with an `include` list, an
optional `exclude` list and an optional `dir`, where the repositories are cloned (an
absolute path or one starting with `~`, default `/srv/boxman`):

```yaml
repos:
  dir: /srv/boxman
  include:
    - your-org/*
    - vivainio/boxman
  exclude:
    - your-org/*-docs
```

Each `exclude` entry is a glob matched against `owner/name`. It removes matches from
the whole `include` list, wherever it is written, so ordering never matters.

An include entry is a string:

- `owner/name` — one GitHub repository
- `owner/pattern` — every repository of that owner matching the glob
- `https://…` or `git@…` — any Git URL

Plain scalars cannot start with `*`, so quote those (`"*/x"`). A string that needs
options is a map instead, with `repo` or `url` and any of `into` (subdirectory of the
clone directory, for patterns), `path` (explicit path, for literal entries), `ref`,
`depth`, `include_archived`:

```yaml
ref: main
depth: 1
repos:
  include:
    - your-org/foo-*
    - repo: your-org/svc-*
      into: services
      include_archived: true
    - url: https://git.example.com/x/y.git
      path: tools/y
      ref: dev
```

With the default `dir`, run `sudo boxman system` first. A custom `dir` is created if it
does not exist and must be writable by the current user.

A line `#+include other.yaml` is replaced by that file's text, so a layout can be split up
or shared; see the miniformat README for the details. Errors name the line.

`boxman ec2 layout [FILE]` copies the layout to a host that is already set up (with
includes expanded and the `ec2` map removed) and starts `boxman layout` there
with `nohup`, logging to `~/.local/state/boxman/layout.log`; it returns without
waiting for the clones. Without `FILE` it uses the `--config` layout file. Send the
GitHub token first (`boxman ec2 secrets send`), or private repositories and patterns
will fail.

Patterns skip forks and archived repositories. Each clone gets
`core.sharedRepository=group`. Paths that escape the clone directory are rejected.
