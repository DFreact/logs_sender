"""Resource guard checks without starting Docker or a browser."""

import fcntl
import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "verification_guard", Path(__file__).resolve().parents[1] / "verification_guard.py"
)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class VerificationGuardTests(unittest.TestCase):
    def test_exclusive_lock_and_release(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(guard, "ROOT", Path(directory)),
        ):
            local = Path(directory) / ".local"
            local.mkdir()
            called = []
            function = guard.exclusive(lambda: called.append(True))
            with (local / "verification.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaises(SystemExit):
                    function()
            self.assertEqual(called, [])
            with (
                patch.object(
                    Path, "read_text", return_value="MemAvailable: 8388608 kB"
                ),
                patch.object(
                    guard.subprocess,
                    "run",
                    return_value=SimpleNamespace(stdout="eventhub-local\n"),
                ),
            ):
                function()
            self.assertEqual(called, [True])

    def test_low_memory_blocks_before_docker(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(guard, "ROOT", Path(directory)),
            patch.object(Path, "read_text", return_value="MemAvailable: 1024 kB"),
            patch.object(guard.subprocess, "run") as docker,
        ):
            with self.assertRaises(SystemExit):
                guard.exclusive(lambda: self.fail("started"))()
            docker.assert_not_called()

    def test_orphaned_test_containers_block_new_stack(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(guard, "ROOT", Path(directory)),
            patch.object(Path, "read_text", return_value="MemAvailable: 8388608 kB"),
            patch.object(
                guard.subprocess,
                "run",
                return_value=SimpleNamespace(stdout="eventhub-check-orphan\n"),
            ),
        ):
            with self.assertRaises(SystemExit):
                guard.exclusive(lambda: self.fail("started"))()
