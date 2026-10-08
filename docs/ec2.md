# EC2 boxes

## Local files

The default config is `${XDG_CONFIG_HOME:-~/.config}/boxman/ec2.toml`. It has `[machines.red]`, `[machines.blue]`, or `[machines.green]` tables with `profile`, `region`, and optional `tags`. Set `[ec2].default_machine` to choose the default. Use `--machine NAME` to override it, and `--config PATH` to select another machine collection. Instead of the TOML file, `--config FILE` can name a [layout file](commands.md) (any file not ending in `.toml`) with an `ec2` map, so one file holds the whole box:

```yaml
ec2:
  machine: red
  user: alice
  profile: your-aws-profile
  region: your-region
  vpc_id: vpc-...
  subnet_id: subnet-...
  instance_type: m7i.xlarge
  volume_size_gb: 200
  instance_name: red
  ami_id: ami-...
  tags:
    Owner: your-owner
repos:
  dir: /srv/boxman
  include:
    - your-org/*
```

One file describes one machine. Settings that several machines share, such as the account and network, go in a file of their own that each machine file pulls in with `#+include`, which splices the file's text in at the indent of the `#`. For example, `git-machine.yaml` can hold the network of a machine that can reach GitHub:

```yaml
# git-machine.yaml
profile: your-aws-profile
region: your-region
vpc_id: vpc-...
subnet_id: subnet-...
ami_id: ami-...
tags:
  Owner: your-owner
```

```yaml
# red.yaml
ec2:
  machine: red
  user: alice
  instance_type: m7i.xlarge
  volume_size_gb: 200
  instance_name: red
  #+include git-machine.yaml
repos:
  include:
    - your-org/*
```

Run it as `boxman ec2 --config red.yaml ...`. Duplicate keys are an error, so a key set in the included file cannot also be set in the machine file; keep only the shared settings in the included file.

`machine` is the machine name (`--machine` overrides it). `user` is the Unix account used by `setup`, `ssh`, `ssh-config`, `secrets send`, `connect` and `run`; `-u` overrides it, and the commands that need a user fail with a clear message when neither is given. `user` is the Unix account used by `setup`, `ssh`, `ssh-config`, `secrets send`, `connect` and `run`; `-u` overrides it, and the commands that need a user fail with a clear message when neither is given. `init` takes `vpc_id`, `subnet_id`, `instance_type`, `volume_size_gb`, `instance_name` and `ami_id` from the file unless you pass the matching option. `boxman layout apply` ignores the `ec2` map. Unknown `ec2` keys are rejected. Global options such as `--config`, `--machine`, `--profile`, and `--region` go **before** the action.

`init --machine red` creates `${XDG_CONFIG_HOME:-~/.config}/boxman/stacks/red.yaml` and uses the derived stack name `boxman-red`. It refuses to overwrite the file. The generated template includes all six required instance values as CloudFormation parameter defaults. To change a box, edit those defaults or the resource definitions, then run `deploy`. `deploy` reads the defaults from the YAML and passes them as stack parameters. Keep the `Parameters` entries in the generated form so Boxman can read them.

The template creates an Ubuntu EC2 instance, SSM role, security groups, and Instance Connect Endpoint. The AMI value can be a Systems Manager parameter path. Tags in `[machines.red.tags]` become stack tags; `boxman ec2 deploy --tag KEY=VALUE` adds or overrides a tag for that invocation.

## Discovering account values

`boxman ec2 --profile PROFILE discover` prints read-only JSON about the account: VPCs with their subnets (public or private, free IPs), existing instances with their types, volume sizes and tags, existing `boxman-*` stacks, tag keys in use, and suggested `init` values. `--region` is optional when the profile defines one. Sections the profile cannot read are listed under `warnings` instead of failing the command. It is meant for scripts and AI agents; `boxman skill` prints a getting-started skill describing the whole workflow.

## Day-to-day operations

```bash
boxman ec2 status                         # default machine
boxman ec2 --machine blue status
boxman ec2 start
boxman ec2 stop
boxman ec2 destroy
boxman ec2 connect
boxman ec2 connect -u myuser
boxman ec2 run 'uname -a'
boxman ec2 run -u myuser 'id'
boxman ec2 setup --user alice
```

`destroy` deletes the CloudFormation stack and waits for it: the instance, its root volume (which is deleted with the instance), the Instance Connect Endpoint, the security groups and the IAM role are all removed, and the data on the disk is lost. It asks you to type the machine name unless you pass `--yes`. It then removes the machine's local stack template, its `# boxman:` blocks in `~/.ssh/config` and its generated key pair; pass `--keep-local` to keep those. The `[machines.NAME]` entry in the TOML config stays. `stop` keeps the volume, which still costs storage.

`status` reports EC2 state and SSM registration. `start` and `stop` wait for the instance state change. `connect -u` starts a session as `ssm-user` and uses `sudo -iu` to enter the chosen account. `run` executes through SSM Run Command as root unless `-u` is supplied; it prints status and output and exits with an error when the remote command fails.

`setup --user USER` is the laptop-side host bootstrap. It uses the Ubuntu image's `ubuntu` account by default only for bootstrap; pass `--bootstrap-user ACCOUNT` for a custom image. `USER` must be a different account. Boxman writes temporary SSH entries, uses EC2 Instance Connect to send a short-lived key for the bootstrap account, copies the local Boxman package to a temporary directory on the host, installs system packages, creates `USER` if needed, configures its subordinate IDs and lingering, then reconnects as `USER` for `boxman user`, installs the `boxman[ec2]` command permanently from the wheel attached to the latest GitHub release (so the host does not need PyPI), and runs `boxman verify`. The bootstrap account must have passwordless sudo. The temporary package directory is removed afterward.

## SSH and editor access

```bash
boxman ec2 ssh -u myuser
boxman ec2 ssh -u myuser -c my-container
boxman ec2 ssh-config -u myuser
boxman ec2 --machine red ssh-config -u myuser --herdr
ssh mybox
```

Both SSH commands generate a dedicated local key pair when needed. Their ProxyCommand calls EC2 Instance Connect to send the public key for the selected Unix user immediately before opening the tunnel; no persistent `authorized_keys` change is required. `ssh-config` writes a marked host block to `~/.ssh/config`, so OpenSSH tools and VS Code Remote SSH can use the alias. The alias defaults to the selected machine name; pass `--alias` when you want a different local name. `--herdr` then runs `herdr machine add` for that same alias, which prepares the remote Herdr server and saves the machine locally. You can pass `--key-path PATH` to choose the key pair. Re-run `ssh-config` after an instance replacement.

## Sending secrets

Create a JSON file of string values on your laptop and restrict its access:

```json
{"GH_TOKEN":"github-token"}
```

```bash
chmod 600 secrets.json
boxman ec2 --machine red secrets send ./secrets.json -u alice
```

Boxman sends the document over SSH to `boxman secrets receive` running as
Alice. The host loads it into the default tempkeys keyset. Inside that Unix
account, `boxman secrets read NAME` prints one value, and
`tempkeys clear` removes the keyset. Names must be environment
variable names and values must be nonempty strings. Every process running as
Alice can potentially read these values. The laptop file remains the source
of truth: resend it after a host reboot, which clears kernel keyrings. An
upload replaces the whole keyset atomically. For HTTPS GitHub remotes,
`boxman user` configures Git to fetch `GH_TOKEN` from tempkeys when needed.
SSH Git remotes use SSH keys instead. Secrets stored by the earlier
Boxman format need to be resent after upgrading.

## More than one box

Add another `[machines.blue]` or `[machines.green]` table and run commands with `boxman ec2 --machine blue ...`. Each machine gets its own YAML file and derived stack in the boxman config directory. An alias passed to `ssh-config` is local to your SSH config; choose distinct aliases for boxes you want to keep available together.
