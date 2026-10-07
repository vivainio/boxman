---
name: boxman
description: Set up and manage an Ubuntu development box on AWS EC2 with boxman. Use when the user wants to create, deploy, connect to, or configure a boxman EC2 machine, or write boxman's ec2.toml.
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

## 2. Write `${XDG_CONFIG_HOME:-~/.config}/boxman/ec2.toml`

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
