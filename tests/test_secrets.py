from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from boxman import ec2, secrets, user


class SecretCommandTests(unittest.TestCase):
    def test_receive_validates_before_loading(self) -> None:
        with patch.object(secrets.os, "geteuid", return_value=1000), patch.object(secrets, "tempkeys") as command:
            with patch.object(secrets.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b'{"TOKEN":"one"}'))):
                secrets.receive()
            command.assert_called_once_with("load", data=b'{"TOKEN":"one"}')
            with patch.object(secrets.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b'{"bad-name":"two"}'))):
                with self.assertRaisesRegex(SystemExit, "environment-style"):
                    secrets.receive()
            command.assert_called_once()

    def test_read_uses_default_keyset(self) -> None:
        with patch.object(secrets.os, "geteuid", return_value=1000), patch.object(secrets, "tempkeys") as command:
            secrets.read("TOKEN")
        command.assert_called_once_with("get", "TOKEN")

    def test_user_setup_configures_git_credential_helper(self) -> None:
        with patch.object(user, "run") as run:
            user.configure_git_credentials()
        setting = "credential.https://github.com.helper"
        self.assertEqual(run.call_args_list[0].args, ("git", "config", "--global", "--replace-all", setting, ""))
        self.assertEqual(run.call_args_list[1].args[:5], ("git", "config", "--global", "--add", setting))
        self.assertIn("--user git-credential", run.call_args_list[1].args[5])

    def test_ec2_send_streams_over_ssh_stdin(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "secrets.json"
            source.write_text('{"TOKEN":"one"}')
            source.chmod(0o600)
            conf = {"machine": "red", "profile": "aws", "region": "eu-west-1"}
            with patch.object(ec2, "keypair") as keypair, patch.object(ec2.subprocess, "run") as run:
                ec2.send_secrets(conf, "i-123", "alice", source)
            keypair.assert_called_once()
            command = run.call_args.args[0]
            self.assertEqual(command[0:2], ["ssh", "-T"])
            self.assertEqual(command[-2:], ["alice@i-123", '"$HOME/.local/bin/boxman" secrets receive'])
            self.assertEqual(run.call_args.kwargs["input"], b'{"TOKEN":"one"}')


if __name__ == "__main__":
    unittest.main()
