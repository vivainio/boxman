# EC2 boxes

## Local files

The default config is `${XDG_CONFIG_HOME:-~/.config}/boxman/ec2.toml`. It has `[machines.red]`, `[machines.blue]`, or `[machines.green]` tables with `profile`, `region`, and optional `tags`. Set `[ec2].default_machine` to choose the default. Use `--machine NAME` to override it, and `--config PATH` to select another machine collection. Global options such as `--config`, `--machine`, `--profile`, and `--region` go **before** the action.

`init --machine red` creates `${XDG_CONFIG_HOME:-~/.config}/boxman/stacks/red.yaml` and uses the derived stack name `boxman-red`. It refuses to overwrite the file. The generated template includes all six required instance values as CloudFormation parameter defaults. To change a box, edit those defaults or the resource definitions, then run `deploy`. `deploy` reads the defaults from the YAML and passes them as stack parameters. Keep the `Parameters` entries in the generated form so Boxman can read them.

The template creates an Ubuntu EC2 instance, SSM role, security groups, and Instance Connect Endpoint. The AMI value can be a Systems Manager parameter path. Tags in `[machines.red.tags]` become stack tags; `boxman ec2 deploy --tag KEY=VALUE` adds or overrides a tag for that invocation.

## Day-to-day operations

```bash
boxman ec2 status                         # default machine
boxman ec2 --machine blue status
boxman ec2 start
boxman ec2 stop
boxman ec2 connect
boxman ec2 connect -u myuser
boxman ec2 run 'uname -a'
boxman ec2 run -u myuser 'id'
```

`status` reports EC2 state and SSM registration. `start` and `stop` wait for the instance state change. `connect -u` starts a session as `ssm-user` and uses `sudo -iu` to enter the chosen account. `run` executes through SSM Run Command as root unless `-u` is supplied; it prints status and output and exits with an error when the remote command fails.

## SSH and editor access

```bash
boxman ec2 ssh -u myuser
boxman ec2 ssh -u myuser -c my-container
boxman ec2 ssh-config -u myuser
boxman ec2 --machine red ssh-config -u myuser --herdr
ssh mybox
```

Both SSH commands generate a dedicated local key pair when needed and install the public key in the remote user's `authorized_keys` using SSM. `ssh-config` writes a marked host block to `~/.ssh/config`, so OpenSSH tools and VS Code Remote SSH can use the alias. The alias defaults to the selected machine name; pass `--alias` when you want a different local name. `--herdr` then runs `herdr machine add` for that same alias, which prepares the remote Herdr server and saves the machine locally. You can pass `--key-path PATH` to choose the key pair. Re-run `ssh-config` after an instance replacement.

## More than one box

Add another `[machines.blue]` or `[machines.green]` table and run commands with `boxman ec2 --machine blue ...`. Each machine gets its own YAML file and derived stack in the boxman config directory. An alias passed to `ssh-config` is local to your SSH config; choose distinct aliases for boxes you want to keep available together.
