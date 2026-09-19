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
import subprocess
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


def required(value: str | None, name: str) -> str:
    if not value:
        raise SystemExit(f"Missing {name}; provide it by option or --config")
    return value


def username(value: str) -> str:
    if not USERNAME.fullmatch(value):
        raise SystemExit(f"Invalid Unix username: {value!r}")
    return value


def config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "boxman"


def stack_template(name: str) -> Path:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,127}", name):
        raise SystemExit("Stack name must be a valid CloudFormation stack name")
    return config_dir() / "stacks" / f"{name}.yaml"


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
    config = {}
    config_path = args.config or config_dir() / "ec2.toml"
    if config_path.is_file():
        with config_path.open("rb") as source:
            config = tomllib.load(source)
    elif args.config:
        raise SystemExit(f"Config file does not exist: {config_path}")
    if config:
        if set(config) - {"ec2"} or not isinstance(config.get("ec2"), dict):
            raise SystemExit("Config must contain an [ec2] table")
        config = config["ec2"]
    keys = {"profile", "region", "stack_name", "tags"}
    if set(config) - keys:
        raise SystemExit(f"Unknown [ec2] settings: {', '.join(sorted(set(config) - keys))}")
    result = {key: getattr(args, key, None) or config.get(key) for key in keys}
    result["stack_name"] = required(result["stack_name"], "--stack-name")
    stack_template(result["stack_name"])
    if args.action != "init":
        result["profile"] = required(result["profile"], "--profile")
        result["region"] = required(result["region"], "--region")
    tags = config.get("tags", {}).copy()
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


def keypair(path: Path) -> None:
    if path.exists():
        if not Path(str(path) + ".pub").is_file():
            raise SystemExit(f"Missing public key for {path}")
        return
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(path)], check=True)


def proxy(settings_: dict) -> str:
    return " ".join(shlex.quote(part) for part in ("aws", "ec2-instance-connect", "open-tunnel", "--profile", settings_["profile"], "--region", settings_["region"], "--instance-id")) + " %h"


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


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="boxman ec2")
    parser.add_argument("--config", type=Path, help="TOML file (default: $XDG_CONFIG_HOME/boxman/ec2.toml)")
    parser.add_argument("--profile")
    parser.add_argument("--region")
    parser.add_argument("--stack-name")
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
    ssh_config.add_argument("--alias", required=True)
    ssh_config.add_argument("--key-path", type=Path)
    run = sub.add_parser("run")
    run.add_argument("command")
    run.add_argument("-u", "--user")
    args = parser.parse_args(argv)
    conf = settings(args)
    if args.action == "init":
        values = {key: getattr(args, key) for key in PARAMETERS}
        try:
            if int(required(values["volume_size_gb"], "--volume-size-gb")) < 8:
                raise SystemExit("--volume-size-gb must be at least 8")
        except ValueError as exc:
            raise SystemExit("--volume-size-gb must be an integer") from exc
        print(f"Created {init_stack(conf['stack_name'], values)}")
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
            template = stack_template(name)
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
            key = args.key_path or Path.home() / ".ssh" / f"boxman-{name}-ed25519"
            keypair(key)
            add_key(session.client("ssm"), instance, user, Path(str(key) + ".pub"))
            tunnel = proxy(conf)
            if args.action == "ssh":
                cmd = ["ssh", "-t", "-o", f"ProxyCommand={tunnel}", "-o", "StrictHostKeyChecking=accept-new", "-i", str(key), f"{user}@{instance}"]
                if args.container:
                    cmd.append(f"podman exec -it {shlex.quote(args.container)} bash")
                subprocess.run(cmd, check=True)
            else:
                write_ssh_config(args.alias, f"Host {args.alias}\n    HostName {instance}\n    User {user}\n    IdentityFile {key}\n    ProxyCommand {tunnel}")
                print(f"SSH host {args.alias} configured for {user}@{instance}")
        elif args.action == "run":
            execute(session.client("ssm"), instance, args.command, args.user)
    except botocore.exceptions.BotoCoreError as exc:
        raise SystemExit(f"AWS error: {exc}") from exc
    except botocore.exceptions.ClientError as exc:
        raise SystemExit(f"AWS error: {exc.response['Error'].get('Message', exc)}") from exc
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"Command failed ({exc.returncode}): {exc.cmd}") from exc
