# How Boxman fits together

Boxman has two sides: a local operator command and a command run *inside* the development host. Both are provided by the same Python package.

| Piece | Where it runs | What it owns |
| --- | --- | --- |
| `boxman ec2` | Your local machine | CloudFormation stack and instance lifecycle, SSM and SSH access |
| `boxman system` | Ubuntu host, as root | Apt packages, Git LFS, rootless Podman prerequisites |
| `boxman user` | Ubuntu host, as each login user | User tools, Node.js, uv, Claude Code, Copilot CLI |
| `boxman vault` / `boxman claude` | Ubuntu host, as a login user | Encrypted private directory and Claude config location |
| `boxman verify` | Ubuntu host, as a login user | Tool and rootless Podman checks |

## One box, one stack

Each EC2 box has one CloudFormation stack. The stack creates an instance, security groups, an IAM role and instance profile for SSM, and an EC2 Instance Connect Endpoint. Its `InstanceId` output tells Boxman which machine to operate. A machine alias such as `red` derives the stack name `boxman-red`; its profile and region select where to look.

The editable stack template lives at `${XDG_CONFIG_HOME:-~/.config}/boxman/stacks/<machine>.yaml`. Machine aliases such as `red`, `blue`, and `green` are the primary Boxman identity. `boxman ec2 --machine red init` writes `red.yaml` once with the chosen instance settings and uses the derived CloudFormation stack name `boxman-red`. `deploy` reads that file and sends its parameter defaults to CloudFormation. The TOML keeps per-machine profile, region, and tags. Set `[ec2].default_machine` to choose the machine used when `--machine` is omitted.

## Access paths

`connect` opens an interactive SSM session. `run` uses SSM Run Command without a terminal. `ssh` and `ssh-config` use the EC2 Instance Connect Endpoint as an SSH tunnel. The template permits port 22 from that endpoint's security group, and has no general inbound rule. The machine still needs the AWS network access required for SSM and package installation.

`ssh-config` stores the current instance ID in `~/.ssh/config`. Its alias defaults to the machine alias; `--alias` is only a local SSH/Herdr name override. Pass `--herdr` to run `herdr machine add` after writing the SSH entry. If CloudFormation replaces the instance, run `ssh-config` again to refresh the entry.

## Private data boundary

The vault is a gocryptfs directory owned by each Unix user. Its encrypted backing files are in `~/.private.cipher`; decrypted files appear at `~/private` while unlocked. `boxman claude` sets `CLAUDE_CONFIG_DIR` inside that mount. The vault protects files while locked; processes and administrators on an unlocked host can access plaintext.
