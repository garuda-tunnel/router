"""Tests for ipt_server namespace, CLI, and startup wiring."""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from click.testing import CliRunner


class TestIptServerNamespaceAndCli(unittest.TestCase):
    def test_runtime_package_namespace_exists(self):
        import importlib
        import ipt_server

        main_mod = importlib.import_module("ipt_server.main")
        self.assertTrue(hasattr(main_mod, "main"))
        self.assertTrue(hasattr(ipt_server, "__path__"))

    def test_cli_prepare_click_command_returns_zero_on_success(self):
        from ipt_server.cli.ipdb import cli

        runner = CliRunner()
        with patch("ipt_server.cli.ipdb.IptDbManager.prepare") as mock_prepare:
            mock_prepare.return_value = SimpleNamespace(
                source="existing_fresh", reason="fresh_runtime_db"
            )
            result = runner.invoke(cli, ["prepare"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn(
            "source=existing_fresh reason=fresh_runtime_db",
            result.output,
        )

    def test_cli_prepare_click_command_returns_non_zero_on_failure(self):
        from ipt_server.cli.ipdb import cli

        runner = CliRunner()
        with patch(
            "ipt_server.cli.ipdb.IptDbManager.prepare",
            side_effect=RuntimeError("prepare_failed_no_valid_source"),
        ):
            result = runner.invoke(cli, ["prepare"])

        self.assertEqual(result.exit_code, 1)
        self.assertIn("prepare_failed_no_valid_source", result.output)

    def test_startup_wiring_uses_entrypoint_script_with_blocking_prepare(self):
        dockerfile = Path(__file__).parent.parent.parent / "Dockerfile"
        contents = dockerfile.read_text(encoding="utf-8")
        start_script = Path(__file__).parent.parent.parent / "scripts" / "start-ipt.sh"
        start_contents = start_script.read_text(encoding="utf-8")

        self.assertIn('ENTRYPOINT [ "./scripts/start-ipt.sh" ]', contents)
        self.assertIn("python -m ipt_server.cli.ipdb prepare", start_contents)
        self.assertIn("exec python -m ipt_server.main", start_contents)
        self.assertLess(
            start_contents.index("python -m ipt_server.cli.ipdb prepare"),
            start_contents.index("exec python -m ipt_server.main"),
        )
        self.assertNotIn("/ipt_server.py", contents)


if __name__ == "__main__":
    unittest.main()
