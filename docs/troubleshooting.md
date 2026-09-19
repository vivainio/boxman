# Troubleshooting

## `init` says the template already exists

`init` deliberately preserves existing edits. Open `${XDG_CONFIG_HOME:-~/.config}/boxman/stacks/red.yaml` (or the selected machine's file) and change the parameter defaults there. Use a new machine alias such as `blue` to create another box.

## `deploy` cannot find a template or default

Run `boxman ec2 --machine red init` for the selected machine. If you edited the YAML, each of the six `Parameters` entries must still have a `Default:` directly after its `Type:`. `deploy` reads those defaults and sends them to CloudFormation.

## SSM has not registered

After deployment or start, allow the instance time to boot and run its SSM agent. Check `boxman ec2 status`. The instance needs network access to the SSM endpoints and a working instance profile; the generated stack attaches the SSM managed policy.

## SSH alias reaches an old instance

`ssh-config` writes the current instance ID into `~/.ssh/config`. Run `boxman ec2 ssh-config -u USER --alias NAME` again after CloudFormation replaces the instance.

## Vault will not lock

Close shells, editors, and agents using files below `~/private`, then retry `boxman vault lock`.

## A host setup command is missing

Run `boxman verify` as the affected login user. `system` and `user` install different parts of the toolset; repeat the appropriate step after fixing any installer or network error. Ensure `~/.local/bin` is on that user's `PATH`.
