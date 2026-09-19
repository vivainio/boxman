# Get started

## Install the local command

Install the published Boxman command with uv. Add the `ec2` extra when you
want the AWS commands:

```bash
uv tool install --upgrade boxman
uv tool install --upgrade 'boxman[ec2]'
boxman ec2 --help
```

For local development, use `uv tool install --editable .` from a checkout.

You need an AWS CLI profile with credentials for the target account, plus AWS CLI v2 and OpenSSH for the access commands. `connect` also needs the Session Manager plugin. Boxman itself uses boto3 for AWS API calls.

## Choose a box

Create `${XDG_CONFIG_HOME:-$HOME/.config}/boxman/ec2.toml`:

```toml
[ec2]
profile = "my-profile"
region = "eu-west-1"
stack_name = "my-dev-box"

[ec2.tags]
Owner = "my-team"
Environment = "development"
```

These names are examples. Use your account's required tags. Boxman does not choose a profile, network, owner, or instance settings for you.

## Materialize and deploy

Run `init` once with your actual VPC, subnet, instance size, volume size, name, and AMI or SSM AMI parameter:

```bash
boxman ec2 init \
  --vpc-id vpc-example --subnet-id subnet-example \
  --instance-type t3.xlarge --volume-size-gb 100 \
  --instance-name my-dev-box \
  --ami-id /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id
boxman ec2 deploy
boxman ec2 status
```

Inspect and edit `~/.config/boxman/stacks/my-dev-box.yaml` before deployment if needed. `deploy` creates or updates the stack and waits for CloudFormation to finish. Give the instance time to register with SSM, then connect:

```bash
boxman ec2 connect
```

Next, follow [Set up the host](host-setup.md) inside the instance. To operate an existing Ubuntu 24.04 host without EC2, start there directly.
