"""Tests for the ipt_server CLI and startup wiring.

The startup contract of scripts/start-ipt.sh ("blocking prepare, then main")
is verified behaviorally: the real script runs against a fake ``python``
shim on PATH that records its invocations, so ordering and abort semantics
are executed, not grepped out of the script text.
"""

import stat
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from click.testing import CliRunner

_COMPONENT_ROOT = Path(__file__).resolve().parents[2]


class TestIptServerCli(unittest.TestCase):
    def test_ipdb_cli_entrypoint_is_runnable(self):
        """The ipdb CLI entry point (python -m ipt_server.cli.ipdb) is a
        runnable click command: --help exits 0 and advertises prepare."""
        from ipt_server.cli.ipdb import cli

        result = CliRunner().invoke(cli, ["--help"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("prepare", result.output)

    def test_cli_prepare_click_command_returns_zero_on_success(self):
        """``prepare`` exits 0 and prints the chosen source/reason."""
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
        """A failing prepare exits non-zero with the failure reason shown."""
        from ipt_server.cli.ipdb import cli

        runner = CliRunner()
        with patch(
            "ipt_server.cli.ipdb.IptDbManager.prepare",
            side_effect=RuntimeError("prepare_failed_no_valid_source"),
        ):
            result = runner.invoke(cli, ["prepare"])

        self.assertEqual(result.exit_code, 1)
        self.assertIn("prepare_failed_no_valid_source", result.output)

    def _run_start_script(self, prepare_rc: int):
        """Run scripts/start-ipt.sh with a fake python recording invocations."""
        import os
        import tempfile

        script = _COMPONENT_ROOT / "scripts" / "start-ipt.sh"
        with tempfile.TemporaryDirectory() as td:
            record = Path(td) / "invocations"
            shim_dir = Path(td) / "bin"
            shim_dir.mkdir()
            shim = shim_dir / "python"
            shim.write_text(
                "#!/bin/sh\n"
                'printf \'%s\\n\' "$*" >> '
                f"{record}\n"
                f"if [ \"$1\" = \"-m\" ] && [ \"$2\" = \"ipt_server.cli.ipdb\" ]; then exit {prepare_rc}; fi\n"
                "exit 0\n"
            )
            shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
            env = dict(os.environ)
            env["PATH"] = f"{shim_dir}:{env['PATH']}"
            completed = subprocess.run(
                ["sh", str(script)],
                capture_output=True,
                text=True,
                env=env,
                timeout=10,
            )
            lines = (
                record.read_text().splitlines() if record.exists() else []
            )
        return completed, lines

    def test_startup_script_runs_blocking_prepare_then_main(self):
        """start-ipt.sh executes ``python -m ipt_server.cli.ipdb prepare``
        and only then ``python -m ipt_server.main`` — the blocking prepare
        contract (a missing/broken DB must abort startup, not race it)."""
        completed, invocations = self._run_start_script(prepare_rc=0)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(len(invocations), 2)
        self.assertIn("ipt_server.cli.ipdb prepare", invocations[0])
        self.assertIn("ipt_server.main", invocations[1])

    def test_startup_script_aborts_when_prepare_fails(self):
        """A failing prepare (set -eu) aborts the script before main runs —
        the service never starts on a broken IP database."""
        completed, invocations = self._run_start_script(prepare_rc=1)

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(len(invocations), 1)
        self.assertIn("ipt_server.cli.ipdb prepare", invocations[0])
        self.assertNotIn(
            "ipt_server.main",
            " ".join(invocations),
            "main must not start when prepare failed",
        )


if __name__ == "__main__":
    unittest.main()
