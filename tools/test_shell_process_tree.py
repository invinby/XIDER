"""Real owned process-tree timeout probe, runnable with only the standard library."""
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


class ShellProcessTreeTest(unittest.TestCase):
    def test_timeout_terminates_owned_shell_and_descendants(self):
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
                result = module.execute_shell(command, 1)
                self.assertEqual(result["status"], "timeout")
                self.assertEqual(result["returncode"], -1)
                self.assertIn("ready", result["stdout"])
                self.assertLess(time.monotonic() - started, 7)
                self.assertTrue(pids.exists())
                owned = json.loads(pids.read_text())
                self.assertEqual(len(owned), 2)
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


if __name__ == "__main__":
    unittest.main()
