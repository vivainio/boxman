---
name: boxman
description: Set up and manage an Ubuntu development box on AWS EC2 with boxman. Use when the user wants to create, deploy, connect to, or configure a boxman EC2 machine, or write boxman's layout file or ec2.toml.
---

# boxman: getting started

boxman manages a development box as a CloudFormation stack (`boxman-<machine>`),
reached through SSM / EC2 Instance Connect (no inbound ports). Machines are named
`red`, `blue`, `green`, etc. Install with `uv tool install 'boxman[ec2]'`.

## 1. Discover the account (read-only, JSON)

    boxman ec2 --profile PROFILE discover

`--region` is optional; the profile's region is used when omitted. Output keys:
`vpcs` (each with `subnets`: id, az, cidr, `public`, `free_ips`), `instances`
(type, subnet, ami, `volume_gb`, tags), `boxman_stacks` (machine names already
taken), `tag_keys` (tag keys in use with their values), `suggested_init_values`
(vpc, subnet, AMI) and `warnings` (sections that failed, e.g. missing
permissions). Copy tags the account requires from `tag_keys`/`instances`.

## 2. Write the layout file (preferred)

One YAML file can hold the whole box: the machine settings and the repositories to
clone. Pass it as
`boxman ec2 --config layout.yaml ...` and `boxman ec2 --config layout.yaml setup -u USER --layout layout.yaml`.
All scalars are strings; quote values starting with `*` (`"*/x"`).

    ec2:                          # read by `boxman ec2`; ignored by `layout apply`
      machine: red                # --machine overrides
      profile: PROFILE            # no credentials here
      region: REGION
      vpc_id: vpc-...             # init values; a matching init option overrides
      subnet_id: subnet-...
      instance_type: t3.xlarge
      volume_size_gb: 100         # at least 8
      instance_name: mybox
      ami_id: /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id
      tags:
        Owner: someone
    ref: main                     # defaults for every repo: ref, depth (0 = full), include_archived
    depth: 1
    repos:
      dir: /srv/boxman            # clone root: absolute or ~; default /srv/boxman
      include:                    # required
        - owner/name              # one GitHub repo
        - owner/prefix-*          # glob: every repo of the owner, skipping forks and archived
        - https://host/x/y.git    # any git URL
        - repo: owner/svc-*       # map form: repo or url, plus into (subdir, for globs),
          into: services          #   path (for literal entries), ref, depth, include_archived
      exclude:                    # globs on owner/name, removed from the whole include list
        - owner/prefix-docs

No other keys are valid, and unknown ones are rejected. One file describes one machine.
Settings several machines share (profile, region, vpc_id, subnet_id, ami_id, tags) go in
a file of their own, e.g. `git-machine.yaml` for a network that can reach GitHub, and each
machine file pulls it in with `#+include git-machine.yaml` placed inside `ec2:` at the
indent of its keys. Included text is pasted in, and duplicate keys are an error, so keep
only shared keys in the included file. Apply the repos alone with `boxman layout apply layout.yaml`.

## 2b. Or write `${XDG_CONFIG_HOME:-~/.config}/boxman/ec2.toml`

    [ec2]
    default_machine = "red"

    [machines.red]
    profile = "PROFILE"
    region = "REGION"

    [machines.red.tags]
    Owner = "someone"

Only `[ec2]` (`default_machine`) and `[machines.<name>]` (`profile`, `region`,
`tags`) are valid. No credentials go in this file.

## 3. Create and deploy the stack

With the layout file: `boxman ec2 --config layout.yaml init`, then `boxman ec2 --config layout.yaml deploy`.
With the TOML file, pass the values as options:

    boxman ec2 --machine red init --vpc-id vpc-... --subnet-id subnet-... \
      --instance-type t3.xlarge --volume-size-gb 100 --instance-name mybox \
      --ami-id /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id
    boxman ec2 deploy

`init` writes `~/.config/boxman/stacks/red.yaml` (edit it before `deploy` if
needed; it refuses to overwrite). Global options (`--config`, `--machine`,
`--profile`, `--region`) go before the action.

## 4. Use the box

    boxman ec2 status | start | stop
    boxman ec2 connect -u USER          # SSM session
    boxman ec2 setup --user USER        # create the account, install boxman, tools; writes SSH config
    boxman ec2 ssh-config -u USER [--herdr]   # (re)write the SSH config entry on its own
    boxman ec2 run -u USER 'uname -a'
    boxman ec2 secrets send ./secrets.json -u USER
    boxman ec2 destroy                  # deletes instance AND volume (asks to confirm; --yes skips)

After `setup` (or `ssh-config`) there is an entry in `~/.ssh/config` named after
the machine, so day-to-day use is plain `ssh red` (also `scp`, `rsync`, VS Code
Remote). The entry tunnels through EC2 Instance Connect via boxman, so no
`boxman ec2 ssh` is needed.

## On the box itself

    sudo boxman system    # apt packages, rootless containers
    boxman user           # user tools
    boxman verify         # check the result
    boxman vault init|unlock|lock|status   # encrypted ~/private

Run `boxman <command> --help` for details. Full docs: https://vivainio.github.io/boxman/
