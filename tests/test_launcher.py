"""Checkout launcher behavior independent of native dependencies or network access."""

import io
import os
from contextlib import redirect_stderr
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import run


class LauncherTests(unittest.TestCase):
    def test_browser_uses_checkout_uri_without_installing_anything(self):
        with patch.object(run, "_pip_environment") as install, patch.object(run.webbrowser, "open") as browser:
            self.assertEqual(run.main(["--browser"]), 0)
            browser.assert_called_once_with((run.ROOT / "docs/demo.html").as_uri())
            browser.return_value = False
            with redirect_stderr(io.StringIO()) as message:
                self.assertEqual(run.main(["--browser"]), 1)
                self.assertEqual(run.main(["--browser", "--demo"]), 2)
            self.assertIn(str(run.ROOT / "docs/demo.html"), message.getvalue())
            install.assert_not_called()

    def test_native_cli_defaults_to_demo_and_preserves_args_and_exit_code(self):
        with patch.object(run, "_local_python", return_value="python"), patch.object(
            run.shutil, "which", return_value=None
        ), patch.object(run, "_pip_environment", return_value="python"), patch.object(
            subprocess, "call", return_value=2
        ) as invoke:
            self.assertEqual(run.main([]), 2)
            invoke.assert_called_with(["python", "-m", "scheduling_planner", "--demo"])
            self.assertEqual(run.main(["my schedule.json", "--brief"]), 2)
            invoke.assert_called_with(["python", "-m", "scheduling_planner", "my schedule.json", "--brief"])

    def test_failed_install_can_be_retried_and_ready_install_is_reused(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(run, "CACHE", Path(directory)):
            cache = Path(directory)
            failure = subprocess.CalledProcessError(1, "pip")
            with patch.object(subprocess, "run", side_effect=[None, failure]), redirect_stderr(
                io.StringIO()
            ), self.assertRaises(subprocess.CalledProcessError):
                run._pip_environment("python")
            self.assertEqual(list(cache.iterdir()), [])
            def prepare(command, **kwargs):
                if command[1:3] == ["-m", "venv"]:
                    python = command[3] / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
                    python.parent.mkdir()
                    python.touch()

            with patch.object(subprocess, "run", side_effect=prepare) as setup, redirect_stderr(io.StringIO()):
                installed = run._pip_environment("python")
                self.assertEqual(setup.call_count, 4)
                setup.reset_mock()
                self.assertEqual(run._pip_environment("python"), installed)
                setup.assert_not_called()

    def test_inspection_needs_no_installer(self):
        with patch.object(run, "_local_python", return_value=sys.executable), patch.object(
            run, "_pip_environment", side_effect=AssertionError("unexpected install")
        ), patch.object(subprocess, "call", return_value=0) as invoke:
            for flag in ["--help", "--version", "--schema", "--brief-schema"]:
                self.assertEqual(run.main([flag]), 0)
                invoke.assert_called_with([sys.executable, run.ROOT / "planner.py", flag])

    def test_invalid_explicit_python_emits_actionable_error(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, SCHEDULING_PLANNER_PYTHON=str(Path(directory) / "missing")
        ), patch.object(run, "CACHE", Path(directory)), redirect_stderr(io.StringIO()) as message:
            self.assertEqual(run.main(["--demo"]), 1)
            self.assertIn("SCHEDULING_PLANNER_PYTHON", message.getvalue())
