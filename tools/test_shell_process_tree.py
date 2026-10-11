"""Owned process-tree timeout probe; Windows uses the agents' psutil dependency."""
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


class ShellProcessTreeTest(unittest.TestCase):
    def test_timeout_terminates_owned_shell_and_descendants(self):
        self._assert_timeout_tree()

    @unittest.skipUnless(os.name == "nt", "taskkill fallback is Windows-specific")
    def test_taskkill_timeout_still_terminates_owned_tree(self):
        self._assert_timeout_tree(subprocess.TimeoutExpired("taskkill", 5))

    @unittest.skipUnless(os.name == "nt", "taskkill fallback is Windows-specific")
    def test_taskkill_failure_preserves_original_command_timeout(self):
        self._assert_timeout_tree(OSError("taskkill unavailable"))

    def _assert_timeout_tree(self, taskkill_error=None):
        path = Path(__file__).resolve().parents[1] / "XGENT-MCS" / "shell_execution.py"
        spec = importlib.util.spec_from_file_location("timeout_probe_shell_execution", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory(prefix="xider-shell-tree-test-") as directory:
            root = Path(directory)
            fixture, pids = root / "fixture.py", root / "pids.json"
            fixture.write_text(
                "import subprocess, sys, os, time, json\n"
                "from pathlib import Path\n"
                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)', 'xider-owned-timeout-probe'])\n"
                "Path(sys.argv[1]).write_text(json.dumps([os.getpid(), child.pid]))\n"
                "print('ready', flush=True)\n"
                "time.sleep(30)\n", encoding="utf-8")
            args = [sys.executable, str(fixture), str(pids)]
            command = subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)
            def alive(pid):
                if os.name == "nt":
                    import psutil
                    return psutil.pid_exists(pid)
                stat = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                                      capture_output=True, text=True, timeout=2).stdout.strip()
                return bool(stat) and not stat.startswith("Z")
            try:
                started = time.monotonic()
                real_run, real_popen = subprocess.run, subprocess.Popen
                shells = []
                def run(args, **kwargs):
                    if taskkill_error is not None and args[0] == "taskkill":
                        raise taskkill_error
                    return real_run(args, **kwargs)
                def popen(*args, **kwargs):
                    process = real_popen(*args, **kwargs)
                    if kwargs.get("shell"):
                        shells.append(process)
                    return process
                with patch.object(module.subprocess, "run", side_effect=run), patch.object(module.subprocess, "Popen", side_effect=popen):
                    result = module.execute_shell(command, 1)
                self.assertEqual(result["status"], "timeout")
                self.assertEqual(result["returncode"], -1)
                self.assertIn("[TIMEOUT after 1s]", result["output"])
                self.assertIn("ready", result["stdout"])
                self.assertLess(time.monotonic() - started, 7)
                self.assertTrue(pids.exists())
                owned = json.loads(pids.read_text())
                self.assertEqual(len(owned), 2)
                self.assertEqual(len(shells), 1)
                self.assertIsNotNone(shells[0].poll(), "owned shell survived cleanup failure")
                self.assertTrue(all(not alive(pid) for pid in owned), "owned child survived timeout")
            finally:
                # Keep the probe isolated even when an assertion fails. These
                # exact PIDs belong to this fresh fixture; inspect command line
                # before cleanup so PID reuse cannot kill unrelated processes.
                if pids.exists():
                    for pid in json.loads(pids.read_text()):
                        if not alive(pid):
                            continue
                        if os.name == "nt":
                            import psutil
                            process = psutil.Process(pid)
                            if str(fixture) in process.cmdline() or "xider-owned-timeout-probe" in process.cmdline():
                                process.kill()
                                process.wait(timeout=3)
                        else:
                            args = subprocess.run(["ps", "-o", "args=", "-p", str(pid)],
                                                  capture_output=True, text=True, timeout=2).stdout
                            if str(fixture) in args or "xider-owned-timeout-probe" in args:
                                os.kill(pid, 9)
                for process in shells:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=3)


if __name__ == "__main__":
    unittest.main()
