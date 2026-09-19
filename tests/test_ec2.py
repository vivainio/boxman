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
        self.assertIn(("red", "\"$HOME/.local/bin/uv\" tool install --upgrade 'boxman[ec2]'"), commands)
        self.assertIn(("red", "env PYTHONPATH=/tmp/boxman-setup-x python3 -m boxman.cli verify"), commands)


if __name__ == "__main__":
    unittest.main()
