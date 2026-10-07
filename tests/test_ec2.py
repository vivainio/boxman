from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from boxman import ec2


class Ec2ConfigTests(unittest.TestCase):
    def test_uses_xdg_config_and_cli_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "boxman"
            path.mkdir()
            (path / "ec2.toml").write_text('''[ec2]
default_machine = "red"

[machines.red]
profile = "example"
region = "eu-west-1"
[machines.red.tags]
Owner = "someone"
''')
            parser = __import__("argparse").Namespace(config=None, action="deploy", profile=None, region=None, machine=None, tag=["Owner=other"], **{key: None for key in ec2.PARAMETERS})
            with patch.dict(ec2.os.environ, {"XDG_CONFIG_HOME": directory}):
                result = ec2.settings(parser)
            self.assertEqual(result["profile"], "example")
            self.assertEqual(result["machine"], "red")
            self.assertEqual(result["stack_name"], "boxman-red")
            self.assertEqual(result["tags"], {"Owner": "other"})

    def test_init_materializes_stack_beneath_xdg_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(ec2.os.environ, {"XDG_CONFIG_HOME": directory}):
            values = {"vpc_id": "vpc-1", "subnet_id": "subnet-1", "instance_type": "t3.small", "volume_size_gb": "30", "instance_name": "box", "ami_id": "ami-1"}
            path = ec2.init_stack("my-box", values)
            self.assertEqual(path, Path(directory) / "boxman" / "stacks" / "my-box.yaml")
            self.assertIn('Default: "vpc-1"', path.read_text())
            with self.assertRaisesRegex(SystemExit, "already exists"):
                ec2.init_stack("my-box", values)

    def test_init_cli_creates_stack_without_aws_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(ec2.os.environ, {"XDG_CONFIG_HOME": directory}):
            ec2.main(["--machine", "red", "init", "--vpc-id", "vpc-1", "--subnet-id", "subnet-1", "--instance-type", "t3.small", "--volume-size-gb", "30", "--instance-name", "demo", "--ami-id", "ami-1"])
            self.assertIn('Default: "ami-1"', (Path(directory) / "boxman" / "stacks" / "red.yaml").read_text())

    def test_init_requires_every_stack_value(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(ec2.os.environ, {"XDG_CONFIG_HOME": directory}):
            with self.assertRaisesRegex(SystemExit, "vpc-id"):
                ec2.init_stack("sample", {})

    def test_remove_local_files_deletes_only_this_machines_files(self) -> None:
        with tempfile.TemporaryDirectory() as home, patch.dict(ec2.os.environ, {"XDG_CONFIG_HOME": home + "/cfg", "HOME": home}):
            ssh = Path(home) / ".ssh"
            ssh.mkdir()
            ec2.write_ssh_config("other", "Host other\n    User x")
            ec2.write_ssh_config("blue", "Host blue\n    User a")
            ec2.write_ssh_config("blue-bootstrap", "Host blue-bootstrap\n    User b")
            for name in ("boxman-blue-ed25519", "boxman-blue-ed25519.pub", "boxman-other-ed25519"):
                (ssh / name).write_text("key")
            ec2.init_stack("blue", {key: "1" for key in ec2.PARAMETERS})
            ec2.init_stack("other", {key: "1" for key in ec2.PARAMETERS})
            removed = ec2.remove_local_files("blue", "boxman-blue")
            config = (ssh / "config").read_text()
            self.assertIn("Host other", config)
            self.assertNotIn("Host blue", config)
            self.assertTrue((ssh / "boxman-other-ed25519").exists())
            self.assertFalse((ssh / "boxman-blue-ed25519").exists())
            self.assertFalse(ec2.stack_template("blue").exists())
            self.assertTrue(ec2.stack_template("other").exists())
            self.assertEqual(len(removed), 5)

    def test_register_herdr_prepares_named_machine(self) -> None:
        with patch.object(ec2.shutil, "which", return_value="/usr/bin/herdr"), patch.object(ec2.subprocess, "run") as run:
            ec2.register_herdr("my-box", "alice")
        run.assert_called_once_with(
            ["herdr", "machine", "add", "my-box", "--label", "Boxman my-box (alice)"],
            check=True,
        )

    def test_eic_proxy_sends_key_before_opening_tunnel(self) -> None:
        with patch.object(ec2.subprocess, "run") as run, patch.object(ec2.os, "execvp") as execvp:
            ec2.eic_proxy("profile", "region", "i-123", "alice", Path("/tmp/boxman-key"))
        run.assert_called_once_with(
            [
                "aws", "ec2-instance-connect", "send-ssh-public-key",
                "--profile", "profile", "--region", "region",
                "--instance-id", "i-123", "--instance-os-user", "alice",
                "--ssh-public-key", "file:///tmp/boxman-key.pub",
            ],
            check=True,
            stdout=ec2.subprocess.DEVNULL,
        )
        execvp.assert_called_once_with(
            "aws",
            ["aws", "ec2-instance-connect", "open-tunnel", "--profile", "profile", "--region", "region", "--instance-id", "i-123"],
        )

    def test_setup_host_creates_user_and_runs_both_setup_steps(self) -> None:
        with patch.object(ec2, "stage_package", return_value=(Path("/tmp/boxman-setup-x"), "/tmp/boxman-setup-x")), patch.object(ec2, "remote_ssh") as remote:
            ec2.setup_host("red-bootstrap", "red", "alice")
        commands = [call.args for call in remote.call_args_list]
        self.assertIn(("red-bootstrap", "sudo env PYTHONPATH=/tmp/boxman-setup-x python3 -m boxman.cli system --packages-only"), commands)
        self.assertIn(("red-bootstrap", "sudo env PYTHONPATH=/tmp/boxman-setup-x python3 -m boxman.cli system alice"), commands)
        self.assertIn(("red", "env PYTHONPATH=/tmp/boxman-setup-x python3 -m boxman.cli user"), commands)
        self.assertIn(("red", ec2.INSTALL_BOXMAN), commands)
        self.assertIn(("red", "env PYTHONPATH=/tmp/boxman-setup-x python3 -m boxman.cli verify"), commands)


if __name__ == "__main__":
    unittest.main()


class FakeClient:
    def __init__(self, pages: dict) -> None:
        self.pages = pages

    def get_paginator(self, operation: str):
        pages = self.pages

        class Paginator:
            def paginate(self, **kwargs):
                if isinstance(pages[operation], Exception):
                    raise pages[operation]
                return [pages[operation]]

        return Paginator()


class FakeSession:
    region_name = "eu-west-1"
    profile_name = "p"

    def __init__(self, ec2: dict, cfn: dict) -> None:
        self.clients = {"ec2": FakeClient(ec2), "cloudformation": FakeClient(cfn)}

    def client(self, name: str):
        return self.clients[name]


class DiscoverTests(unittest.TestCase):
    def test_summarizes_account(self) -> None:
        ec2_pages = {
            "describe_vpcs": {"Vpcs": [{"VpcId": "vpc-1", "CidrBlock": "10.0.0.0/16", "IsDefault": True}]},
            "describe_subnets": {"Subnets": [
                {"SubnetId": "s-priv", "VpcId": "vpc-1", "AvailabilityZone": "a", "CidrBlock": "10.0.1.0/24", "AvailableIpAddressCount": 200},
                {"SubnetId": "s-pub", "VpcId": "vpc-1", "AvailabilityZone": "b", "CidrBlock": "10.0.2.0/24", "AvailableIpAddressCount": 50},
            ]},
            "describe_route_tables": {"RouteTables": [
                {"VpcId": "vpc-1", "Associations": [{"Main": True}], "Routes": []},
                {"VpcId": "vpc-1", "Associations": [{"SubnetId": "s-pub"}], "Routes": [{"GatewayId": "igw-1"}]},
            ]},
            "describe_instances": {"Reservations": [{"Instances": [
                {"InstanceId": "i-1", "State": {"Name": "running"}, "InstanceType": "t3.small", "SubnetId": "s-pub", "VpcId": "vpc-1",
                 "ImageId": "ami-1", "Tags": [{"Key": "Name", "Value": "box"}, {"Key": "Owner", "Value": "me"}],
                 "BlockDeviceMappings": [{"Ebs": {"VolumeId": "vol-1"}}]},
                {"InstanceId": "i-2", "State": {"Name": "terminated"}, "InstanceType": "t3.small"},
            ]}]},
            "describe_volumes": {"Volumes": [{"VolumeId": "vol-1", "Size": 40}]},
        }
        cfn_pages = {"list_stacks": {"StackSummaries": [
            {"StackName": "boxman-red", "StackStatus": "CREATE_COMPLETE"},
            {"StackName": "other", "StackStatus": "CREATE_COMPLETE"},
        ]}}
        result = ec2.discover(FakeSession(ec2_pages, cfn_pages))
        subnets = {s["id"]: s for s in result["vpcs"][0]["subnets"]}
        self.assertTrue(subnets["s-pub"]["public"])
        self.assertFalse(subnets["s-priv"]["public"])
        self.assertEqual([i["id"] for i in result["instances"]], ["i-1"])
        self.assertEqual(result["instances"][0]["volume_gb"], 40)
        self.assertEqual(result["boxman_stacks"], [{"name": "boxman-red", "status": "CREATE_COMPLETE"}])
        self.assertEqual(result["tag_keys"]["Owner"], ["me"])
        self.assertEqual(result["suggested_init_values"]["subnet_id"], "s-pub")

    def test_unreadable_section_becomes_warning(self) -> None:
        empty = {k: {v: []} for k, v in [
            ("describe_vpcs", "Vpcs"), ("describe_subnets", "Subnets"), ("describe_route_tables", "RouteTables"),
            ("describe_instances", "Reservations"), ("describe_volumes", "Volumes"),
        ]}
        result = ec2.discover(FakeSession(empty, {"list_stacks": RuntimeError("denied")}))
        self.assertEqual(result["warnings"], ["stacks: denied"])
        self.assertIsNone(result["suggested_init_values"])


class SkillTests(unittest.TestCase):
    def test_skill_command_prints_skill(self) -> None:
        import io
        from contextlib import redirect_stdout
        from boxman import cli

        out = io.StringIO()
        with patch.object(cli.sys, "argv", ["boxman", "skill"]), redirect_stdout(out):
            cli.main()
        self.assertTrue(out.getvalue().startswith("---\nname: boxman"))
