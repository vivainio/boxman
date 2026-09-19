from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from boxman import cli


class VaultTests(unittest.TestCase):
    def test_init_creates_private_directories_and_passes_no_password(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            with (
                patch.object(Path, "home", return_value=home),
                patch.object(cli.os, "geteuid", return_value=1000),
                patch.object(cli.os, "umask"),
                patch.object(cli, "mounted", return_value=False),
                patch.object(cli, "run") as run,
            ):
                cli.vault("init")
            self.assertEqual(
                run.call_args.args,
                ("gocryptfs", "-init", str(home / ".private.cipher")),
            )
            self.assertEqual((home / ".private.cipher").stat().st_mode & 0o777, 0o700)
            self.assertEqual((home / "private").stat().st_mode & 0o777, 0o700)

    def test_claude_refuses_locked_vault_without_creating_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            with (
                patch.object(Path, "home", return_value=home),
                patch.object(cli.os, "geteuid", return_value=1000),
                patch.object(cli, "mounted", return_value=False),
            ):
                with self.assertRaisesRegex(SystemExit, "locked"):
                    cli.claude([])
            self.assertFalse((home / "private" / "claude").exists())


if __name__ == "__main__":
    unittest.main()
