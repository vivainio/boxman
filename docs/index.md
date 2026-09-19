# Boxman

Boxman prepares an Ubuntu 24.04 development host and helps operate it on EC2. It installs shared packages, prepares each login user, checks the resulting tools, and provides an encrypted per-user directory. The optional EC2 commands create and operate the host's CloudFormation stack.

[Get started](getting-started.md){ .md-button .md-button--primary }
[Understand the pieces](concepts.md){ .md-button }

## Typical path

1. [Create an EC2 box](ec2.md) or start with an existing Ubuntu 24.04 host.
2. [Prepare the host](host-setup.md): run the system step, then the user step for each account.
3. [Set up the private vault](vault.md) for credentials you want encrypted at rest.
4. Use `boxman verify` to check the tools and rootless Podman.

Boxman can also build a development container from the same package recipe. The container covers packages and user tools; the vault and host login configuration are intended for a native host.
