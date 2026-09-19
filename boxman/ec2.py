"""Manage an EC2 development box through a CloudFormation stack."""

from __future__ import annotations

import argparse
import base64
import contextlib
import importlib.resources
import json
import os
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import tomllib
from pathlib import Path


PARAMETERS = {
    "vpc_id": "VpcId",
    "subnet_id": "SubnetId",
    "instance_type": "InstanceType",
    "volume_size_gb": "VolumeSizeGb",
    "instance_name": "InstanceName",
    "ami_id": "LatestAmiId",
}
USERNAME = re.compile(r"[a-z_][a-z0-9_-]{0,31}\Z")
ALIAS = re.compile(r"[A-Za-z0-9_.-]+\Z")
MACHINE = re.compile(r"[a-z][a-z0-9-]{0,31}\Z")


def required(value: str | None, name: str) -> str:
    if not value:
        raise SystemExit(f"Missing {name}; provide it by option or --config")
    return value


def username(value: str) -> str:
    if not USERNAME.fullmatch(value):
        raise SystemExit(f"Invalid Unix username: {value!r}")
    return value


def machine_name(value: str) -> str:
    if not MACHINE.fullmatch(value):
        raise SystemExit(f"Invalid machine name: {value!r} (use names such as red, blue, or green)")
    return value


def config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "boxman"


def stack_name(machine: str) -> str:
    return f"boxman-{machine_name(machine)}"


def stack_template(machine: str) -> Path:
    return config_dir() / "stacks" / f"{machine_name(machine)}.yaml"


def init_stack(name: str, values: dict) -> Path:
    target = stack_template(name)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    body = importlib.resources.files("boxman").joinpath("data/ec2-template.yaml").read_text()
    for key, parameter in PARAMETERS.items():
        value = required(values.get(key), "--" + key.replace("_", "-"))
        marker = f"  {parameter}:\n    Type: "
        match = re.search(re.escape(marker) + r"[^\n]+\n", body)
        if not match:
            raise SystemExit(f"Template is missing parameter {parameter}")
        body = body[:match.end()] + f"    Default: {json.dumps(str(value))}\n" + body[match.end():]
    try:
        with target.open("x") as output:
            output.write(body)
    except FileExistsError:
        raise SystemExit(f"Stack template already exists: {target}") from None
    return target


def settings(args: argparse.Namespace) -> dict:
    document = {}
    config_path = args.config or config_dir() / "ec2.toml"
    if config_path.is_file():
        with config_path.open("rb") as source:
            document = tomllib.load(source)
    elif args.config:
        raise SystemExit(f"Config file does not exist: {config_path}")
    if set(document) - {"ec2", "machines"}:
        raise SystemExit("Config may contain only [ec2] and [machines.<name>] tables")
    ec2_config = document.get("ec2", {})
    machines = document.get("machines", {})
    if not isinstance(ec2_config, dict) or not isinstance(machines, dict):
        raise SystemExit("Config must contain [ec2] and [machines.<name>] tables")
    if set(ec2_config) - {"default_machine"}:
        raise SystemExit("[ec2] supports only default_machine")
    selected = getattr(args, "machine", None) or ec2_config.get("default_machine")
    if not selected and len(machines) == 1:
        selected = next(iter(machines))
    selected = machine_name(required(selected, "--machine or [ec2].default_machine"))
    machine_config = machines.get(selected, {})
    if not isinstance(machine_config, dict):
        raise SystemExit(f"[machines.{selected}] must be a table")
    if set(machine_config) - {"profile", "region", "tags"}:
        raise SystemExit(f"Unknown [machines.{selected}] settings")
    result = {
        "machine": selected,
        "stack_name": stack_name(selected),
        "profile": getattr(args, "profile", None) or machine_config.get("profile"),
        "region": getattr(args, "region", None) or machine_config.get("region"),
    }
    if args.action != "init":
        result["profile"] = required(result["profile"], "--profile")
        result["region"] = required(result["region"], "--region")
        if selected not in machines:
            raise SystemExit(f"No configuration for machine {selected!r}; add [machines.{selected}]")
    tags = machine_config.get("tags", {}).copy()
    if not isinstance(tags, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in tags.items()):
        raise SystemExit("[ec2.tags] must contain string keys and values")
    for item in getattr(args, "tag", None) or []:
        if "=" not in item or not item.split("=", 1)[0]:
            raise SystemExit("--tag must be KEY=VALUE")
        key, value = item.split("=", 1)
        tags[key] = value
    result["tags"] = tags
    return result


def stack(cfn, name: str):
    try:
        return cfn.describe_stacks(StackName=name)["Stacks"][0]
    except Exception as exc:
        if getattr(exc, "response", {}).get("Error", {}).get("Code") == "ValidationError" and "does not exist" in str(exc):
            return None
        raise


def instance_id(cfn, name: str) -> str:
    current = stack(cfn, name)
    if current:
        for item in current.get("Outputs", []):
            if item["OutputKey"] == "InstanceId":
                return item["OutputValue"]
    raise SystemExit(f"Stack {name} has no InstanceId output; deploy it first")


def execute(ssm, instance: str, command: str, user: str | None = None) -> None:
    if user:
        user = username(user)
        payload = base64.b64encode(command.encode()).decode()
        command = (f"BOXMAN_TMP=$(mktemp)\ntrap 'rm -f \"$BOXMAN_TMP\"' EXIT\n"
                   f"echo {shlex.quote(payload)} | base64 -d > \"$BOXMAN_TMP\"\n"
                   f"chmod 755 \"$BOXMAN_TMP\"\nsudo -iu {user} bash \"$BOXMAN_TMP\"")
    response = ssm.send_command(InstanceIds=[instance], DocumentName="AWS-RunShellScript", Parameters={"commands": [command]})
    command_id = response["Command"]["CommandId"]
    with contextlib.suppress(Exception):
        ssm.get_waiter("command_executed").wait(CommandId=command_id, InstanceId=instance)
    result = ssm.get_command_invocation(CommandId=command_id, InstanceId=instance)
    print(f"Status: {result['Status']}")
    for key in ("StandardOutputContent", "StandardErrorContent"):
        if result.get(key):
            print(result[key], end="" if result[key].endswith("\n") else "\n")
    if result["Status"] != "Success":
        raise SystemExit(f"Remote command failed: {result['Status']}")


def add_key(ssm, instance: str, user: str, key_path: Path) -> None:
    user = username(user)
    key = key_path.read_text().strip()
    if "\n" in key or not key.startswith(("ssh-ed25519 ", "ssh-rsa ", "ecdsa-sha2-")):
        raise SystemExit(f"Invalid public key: {key_path}")
    payload = base64.b64encode(key.encode()).decode()
    script = ("set -e\nmkdir -p ~/.ssh\nchmod 700 ~/.ssh\n"
              "touch ~/.ssh/authorized_keys\nchmod 600 ~/.ssh/authorized_keys\n"
              f"KEY=$(echo {shlex.quote(payload)} | base64 -d)\n"
              "grep -qxF \"$KEY\" ~/.ssh/authorized_keys || printf '%s\\n' \"$KEY\" >> ~/.ssh/authorized_keys\n")
    execute(ssm, instance, script, user)


def eic_proxy(profile: str, region: str, instance: str, user: str, key_path: Path) -> None:
    """Send a short-lived key through EC2 Instance Connect, then relay SSH."""
    public_key = Path(str(key_path) + ".pub")
    subprocess.run(
        [
            "aws",
            "ec2-instance-connect",
            "send-ssh-public-key",
            "--profile",
            profile,
            "--region",
            region,
            "--instance-id",
            instance,
            "--instance-os-user",
            user,
            "--ssh-public-key",
            f"file://{public_key}",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    os.execvp(
        "aws",
        [
            "aws",
            "ec2-instance-connect",
            "open-tunnel",
            "--profile",
            profile,
            "--region",
            region,
            "--instance-id",
            instance,
        ],
    )


def keypair(path: Path) -> None:
    if path.exists():
        if not Path(str(path) + ".pub").is_file():
            raise SystemExit(f"Missing public key for {path}")
        return
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(path)], check=True)


def proxy(settings_: dict) -> str:
    return " ".join(
        shlex.quote(part)
        for part in (
            "boxman",
            "ec2",
            "--profile",
            settings_["profile"],
            "--region",
            settings_["region"],
            "proxy",
            "--instance-id",
            "%h",
            "--user",
            settings_["ssh_user"],
            "--key-path",
            str(settings_["key_path"]),
        )
    )


def write_ssh_config(alias: str, body: str) -> None:
    if not ALIAS.fullmatch(alias):
        raise SystemExit("Alias may contain letters, numbers, dot, underscore and hyphen")
    path = Path.home() / ".ssh" / "config"
    path.parent.mkdir(mode=0o700, exist_ok=True)
    content = path.read_text() if path.exists() else ""
    begin, end = f"# boxman:{alias} begin", f"# boxman:{alias} end"
    block = f"{begin}\n{body}\n{end}\n"
    pattern = re.compile(re.escape(begin) + r".*?" + re.escape(end) + r"\n?", re.DOTALL)
    content = pattern.sub(lambda _: block, content) if pattern.search(content) else content.rstrip("\n") + ("\n\n" if content else "") + block
    path.write_text(content)
    path.chmod(0o600)


def register_herdr(alias: str, user: str) -> None:
    """Prepare the remote Herdr server and save the SSH machine locally."""
    if shutil.which("herdr") is None:
        raise SystemExit("--herdr requires the herdr command on the local machine")
    subprocess.run(
        ["herdr", "machine", "add", alias, "--label", f"Boxman {alias} ({user})"],
        check=True,
    )


def remote_ssh(alias: str, command: str) -> None:
    subprocess.run(["ssh", "-t", alias, command], check=True)


def send_secrets(conf: dict, instance: str, user: str, source: Path) -> None:
    """Stream a local JSON document through SSH to the remote receiver."""
    if source.is_symlink() or not source.is_file():
        raise SystemExit(f"Secrets source must be a regular file: {source}")
    if source.stat().st_mode & 0o077:
        raise SystemExit(f"Secrets source must be readable only by its owner (chmod 600): {source}")
    from boxman.secrets import MAX_DOCUMENT, parse_document

    with source.open("rb") as input_file:
        data = input_file.read(MAX_DOCUMENT + 1)
    parse_document(data)
    key = Path.home() / ".ssh" / f"boxman-{conf['machine']}-ed25519"
    keypair(key)
    tunnel = proxy({**conf, "ssh_user": user, "key_path": key})
    subprocess.run(
        ["ssh", "-T", "-o", f"ProxyCommand={tunnel}", "-o", "StrictHostKeyChecking=accept-new",
         "-i", str(key), f"{user}@{instance}", '"$HOME/.local/bin/boxman" secrets receive'],
        input=data,
        check=True,
    )


def stage_package(alias: str) -> tuple[Path, str]:
    """Copy this installed Boxman package to a temporary remote directory."""
    package_dir = Path(__file__).resolve().parent
    token = next(tempfile._get_candidate_names())
    remote_dir = f"/tmp/boxman-setup-{token}"
    descriptor, archive_name = tempfile.mkstemp(prefix="boxman-setup-", suffix=".tar.gz")
    os.close(descriptor)
    archive = Path(archive_name)
    try:
        with tarfile.open(archive, "w:gz") as bundle:
            bundle.add(package_dir, arcname="boxman")
        remote_ssh(alias, f"mkdir -p {shlex.quote(remote_dir)}")
        subprocess.run(["scp", str(archive), f"{alias}:{remote_dir}/package.tar.gz"], check=True)
        remote_ssh(
            alias,
            f"tar -xzf {shlex.quote(remote_dir)}/package.tar.gz -C {shlex.quote(remote_dir)}",
        )
    finally:
        archive.unlink(missing_ok=True)
    return Path(remote_dir), remote_dir


def setup_host(bootstrap_alias: str, target_alias: str, user: str) -> None:
    remote_dir, remote_path = stage_package(bootstrap_alias)
    python_path = shlex.quote(str(remote_dir))
    try:
        remote_ssh(bootstrap_alias, f"sudo env PYTHONPATH={python_path} python3 -m boxman.cli system --packages-only")
        create_user = (
            f"if id -u {user} >/dev/null 2>&1; then echo 'user {user} already exists'; "
            f"else sudo useradd --create-home --shell /bin/bash {user}; fi"
        )
        remote_ssh(bootstrap_alias, create_user)
        remote_ssh(bootstrap_alias, f"sudo env PYTHONPATH={python_path} python3 -m boxman.cli system {user}")
        remote_ssh(target_alias, f"env PYTHONPATH={python_path} python3 -m boxman.cli user")
        remote_ssh(target_alias, "\"$HOME/.local/bin/uv\" tool install --upgrade 'boxman[ec2]'")
        remote_ssh(target_alias, f"env PYTHONPATH={python_path} python3 -m boxman.cli verify")
    finally:
        remote_ssh(bootstrap_alias, f"rm -rf {shlex.quote(remote_path)}")


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="boxman ec2")
    parser.add_argument("--config", type=Path, help="TOML file (default: $XDG_CONFIG_HOME/boxman/ec2.toml)")
    parser.add_argument("--profile")
    parser.add_argument("--region")
    parser.add_argument("--machine", help="machine alias such as red, blue, or green")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("init", help="create a local stack template under the boxman config directory")
    init = sub.choices["init"]
    for key in PARAMETERS:
        init.add_argument("--" + key.replace("_", "-"), dest=key)
    deploy = sub.add_parser("deploy")
    deploy.add_argument("--tag", action="append", help="stack tag as KEY=VALUE; repeatable")
    for action in ("status", "start", "stop"):
        sub.add_parser(action)
    connect = sub.add_parser("connect")
    connect.add_argument("-u", "--user")
    ssh = sub.add_parser("ssh")
    ssh.add_argument("-u", "--user", required=True)
    ssh.add_argument("--key-path", type=Path)
    ssh.add_argument("-c", "--container")
    ssh_config = sub.add_parser("ssh-config")
    ssh_config.add_argument("-u", "--user", required=True)
    ssh_config.add_argument("--alias", help="local SSH and Herdr name (default: stack name)")
    ssh_config.add_argument("--key-path", type=Path)
    ssh_config.add_argument("--herdr", action="store_true", help="prepare the remote Herdr server and save this SSH machine")
    setup = sub.add_parser("setup", help="set up the remote host through SSH")
    setup.add_argument("-u", "--user", required=True, help="Unix account to create or configure")
    setup.add_argument("--bootstrap-user", default="ubuntu", help="existing account used for the initial SSH connection (default: ubuntu)")
    proxy_command = sub.add_parser("proxy", help=argparse.SUPPRESS)
    proxy_command.add_argument("--instance-id", required=True)
    proxy_command.add_argument("--user", required=True)
    proxy_command.add_argument("--key-path", type=Path, required=True)
    run = sub.add_parser("run")
    run.add_argument("command")
    run.add_argument("-u", "--user")
    secrets_command = sub.add_parser("secrets", help="send a local JSON secrets document to the selected machine")
    secrets_command.add_argument("operation", choices=["send"])
    secrets_command.add_argument("source", type=Path)
    secrets_command.add_argument("-u", "--user", required=True)
    args = parser.parse_args(argv)
    if args.action == "proxy":
        eic_proxy(
            required(args.profile, "--profile"),
            required(args.region, "--region"),
            args.instance_id,
            username(args.user),
            args.key_path,
        )
        return
    conf = settings(args)
    if args.action == "init":
        values = {key: getattr(args, key) for key in PARAMETERS}
        try:
            if int(required(values["volume_size_gb"], "--volume-size-gb")) < 8:
                raise SystemExit("--volume-size-gb must be at least 8")
        except ValueError as exc:
            raise SystemExit("--volume-size-gb must be an integer") from exc
        print(f"Created {init_stack(conf['machine'], values)}")
        return
    try:
        import boto3
        import botocore.exceptions
    except ImportError as exc:
        raise SystemExit("EC2 commands require boto3; install boxman with the ec2 extra") from exc
    try:
        session = boto3.Session(profile_name=conf["profile"], region_name=conf["region"])
        cfn = session.client("cloudformation")
        name = conf["stack_name"]
        if args.action == "deploy":
            template = stack_template(conf["machine"])
            if not template.is_file():
                raise SystemExit(f"Stack template missing: {template}; run boxman ec2 init first")
            body = template.read_text()
            params = []
            for aws in PARAMETERS.values():
                match = re.search(rf"^  {re.escape(aws)}:\n    Type: [^\n]+\n    Default: (.+)$", body, re.MULTILINE)
                if not match:
                    raise SystemExit(f"Stack template lacks a default for {aws}: {template}")
                try:
                    value = json.loads(match.group(1))
                except json.JSONDecodeError as exc:
                    raise SystemExit(f"Invalid default for {aws}: {exc}") from exc
                params.append({"ParameterKey": aws, "ParameterValue": str(value)})
            tags = [{"Key": key, "Value": value} for key, value in conf["tags"].items()]
            current = stack(cfn, name)
            method = cfn.update_stack if current else cfn.create_stack
            try:
                method(StackName=name, TemplateBody=body, Parameters=params, Capabilities=["CAPABILITY_NAMED_IAM"], Tags=tags)
            except botocore.exceptions.ClientError as exc:
                if current and "No updates are to be performed" in str(exc):
                    print("No changes to apply.")
                    return
                raise
            cfn.get_waiter("stack_update_complete" if current else "stack_create_complete").wait(StackName=name)
            print(f"InstanceId: {instance_id(cfn, name)}")
            return
        if args.action == "setup":
            instance = instance_id(cfn, name)
            user = username(args.user)
            bootstrap_user = username(args.bootstrap_user)
            if user == bootstrap_user:
                raise SystemExit("setup USER must be different from --bootstrap-user; the bootstrap account is only for initial access")
            key = Path.home() / ".ssh" / f"boxman-{conf['machine']}-ed25519"
            keypair(key)
            alias = conf["machine"]
            bootstrap_alias = alias if bootstrap_user == user else f"{alias}-bootstrap"
            bootstrap_tunnel = proxy({**conf, "ssh_user": bootstrap_user, "key_path": key})
            write_ssh_config(
                bootstrap_alias,
                f"Host {bootstrap_alias}\n    HostName {instance}\n    User {bootstrap_user}\n    IdentityFile {key}\n    ProxyCommand {bootstrap_tunnel}",
            )
            target_tunnel = proxy({**conf, "ssh_user": user, "key_path": key})
            write_ssh_config(
                alias,
                f"Host {alias}\n    HostName {instance}\n    User {user}\n    IdentityFile {key}\n    ProxyCommand {target_tunnel}",
            )
            setup_host(bootstrap_alias, alias, user)
            return
        instance = instance_id(cfn, name)
        if args.action == "status":
            ec2 = session.client("ec2")
            info = ec2.describe_instances(InstanceIds=[instance])["Reservations"][0]["Instances"][0]
            print(f"Instance {instance}: {info['State']['Name']}, {info.get('PublicIpAddress', 'no public IP')}")
            ssm = session.client("ssm")
            records = ssm.describe_instance_information(Filters=[{"Key": "InstanceIds", "Values": [instance]}])["InstanceInformationList"]
            print(f"SSM: {records[0]['PingStatus'] if records else 'not registered'}")
        elif args.action in ("start", "stop"):
            ec2 = session.client("ec2")
            getattr(ec2, args.action + "_instances")(InstanceIds=[instance])
            ec2.get_waiter("instance_running" if args.action == "start" else "instance_stopped").wait(InstanceIds=[instance])
            print(f"Instance {instance}: {'running' if args.action == 'start' else 'stopped'}")
        elif args.action == "connect":
            cmd = ["aws", "--profile", conf["profile"], "--region", conf["region"], "ssm", "start-session", "--target", instance]
            if args.user:
                cmd += ["--document-name", "AWS-StartInteractiveCommand", "--parameters", f"command=sudo -iu {username(args.user)}"]
            subprocess.run(cmd, check=True)
        elif args.action in ("ssh", "ssh-config"):
            user = username(args.user)
            key = args.key_path or Path.home() / ".ssh" / f"boxman-{conf['machine']}-ed25519"
            keypair(key)
            tunnel = proxy({**conf, "ssh_user": user, "key_path": key})
            if args.action == "ssh":
                cmd = ["ssh", "-t", "-o", f"ProxyCommand={tunnel}", "-o", "StrictHostKeyChecking=accept-new", "-i", str(key), f"{user}@{instance}"]
                if args.container:
                    cmd.append(f"podman exec -it {shlex.quote(args.container)} bash")
                subprocess.run(cmd, check=True)
            else:
                alias = args.alias or name
                write_ssh_config(alias, f"Host {alias}\n    HostName {instance}\n    User {user}\n    IdentityFile {key}\n    ProxyCommand {tunnel}")
                print(f"SSH host {alias} configured for {user}@{instance}")
                if args.herdr:
                    register_herdr(alias, user)
        elif args.action == "run":
            execute(session.client("ssm"), instance, args.command, args.user)
        elif args.action == "secrets":
            send_secrets(conf, instance, username(args.user), args.source)
    except botocore.exceptions.BotoCoreError as exc:
        raise SystemExit(f"AWS error: {exc}") from exc
    except botocore.exceptions.ClientError as exc:
        raise SystemExit(f"AWS error: {exc.response['Error'].get('Message', exc)}") from exc
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"Command failed ({exc.returncode}): {exc.cmd}") from exc
