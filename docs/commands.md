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
| `boxman ec2 --machine NAME init` | Create a per-machine YAML template from explicit instance settings |
| `boxman ec2 deploy` | Create or update the CloudFormation stack |
| `boxman ec2 status` | Show instance state and SSM registration |
| `boxman ec2 start` / `stop` | Start or stop the instance and wait |
| `boxman ec2 connect [-u USER]` | Start an interactive SSM session |
| `boxman ec2 run [-u USER] COMMAND` | Run a command through SSM without a terminal |
| `boxman ec2 ssh -u USER [-c CONTAINER]` | SSH through the Instance Connect Endpoint |
| `boxman ec2 ssh-config -u USER [--alias NAME] [--herdr]` | Install a key and write a local SSH/optional Herdr machine entry |

Use `boxman ec2 --help` and `boxman ec2 ACTION --help` for option details. EC2 global options go before `ACTION`.
