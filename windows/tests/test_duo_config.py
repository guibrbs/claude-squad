import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from helpers import load

config = load("duo_config.py")
config_module = config


class ReadConfig(unittest.TestCase):
    def test_missing_file_falls_back_to_defaults(self):
        empty = config.read_config(Path(tempfile.gettempdir()) / "no-such-duo.json")
        self.assertEqual(empty, {})
        self.assertEqual(config.code_config_dir(empty), Path.home() / ".claude-duo")
        self.assertEqual(config.user_data_dir(empty), config.LOCAL / "Claude-Duo")

    def test_reads_installed_paths_with_bom(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "duo.json"
            data = {"codeConfigDir": "C:\\x\\.claude-work", "userDataDir": "%LOCALAPPDATA%\\Claude-Work"}
            path.write_bytes(b"\xef\xbb\xbf" + json.dumps(data).encode())
            loaded = config.read_config(path)
            self.assertEqual(config.code_config_dir(loaded), Path("C:\\x\\.claude-work"))
            self.assertEqual(config.user_data_dir(loaded), Path(os.path.expandvars("%LOCALAPPDATA%\\Claude-Work")))

    def test_not_an_object_is_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "duo.json"
            path.write_text("[1, 2]", encoding="utf-8")
            self.assertEqual(config.read_config(path), {})


class DuoRunning(unittest.TestCase):
    def listing(self, config, lines):
        done = subprocess.CompletedProcess([], 0, stdout="\n".join(lines), stderr="")
        with mock.patch.object(config_module.subprocess, "run", return_value=done) as run:
            running = config_module.duo_running(config)
        return running, run.call_args.args[0][-1]

    def test_looks_for_the_renamed_executable_on_the_duo_profile(self):
        installed = {"exe": "C:\\u\\Programs\\ClaudeDuo\\app\\Claude Work.exe", "userDataDir": "C:\\u\\Claude-Work"}
        running, script = self.listing(installed, ['"C:\\x\\Claude Work.exe" --user-data-dir="C:\\u\\Claude-Work"'])
        self.assertTrue(running)
        self.assertIn("Name='Claude Work.exe'", script)
        running, _ = self.listing(installed, ['"C:\\x\\claude.exe"'])  # The official app.
        self.assertFalse(running)

    def test_odd_exe_falls_back_to_the_default_name(self):
        self.assertEqual(config_module.exe_name({"exe": "C:\\x\\a'b.exe"}), "Claude Duo.exe")
        self.assertEqual(config_module.exe_name({}), "Claude Duo.exe")

    def test_tool_state_is_not_the_desktop_profile(self):
        # NTFS ignores case: %LOCALAPPDATA%\\claude-duo is the Duo's Claude-Duo profile.
        self.assertNotEqual(str(config_module.DATA).lower(), str(config_module.DEFAULT_USER_DATA).lower())
        self.assertFalse(str(config_module.DATA).lower().startswith(str(config_module.DEFAULT_USER_DATA).lower() + os.sep))


class ProcessAlive(unittest.TestCase):
    def test_this_process(self):
        self.assertTrue(config.process_alive(os.getpid()))

    def test_bad_values(self):
        for value in (None, 0, -5, "123", 2 ** 31 - 2):
            self.assertFalse(config.process_alive(value), value)

    def test_does_not_kill_what_it_checks(self):
        # On Windows os.kill(pid, 0) terminates the process; the check must only ask.
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            self.assertTrue(config.process_alive(child.pid))
            self.assertIsNone(child.poll())
        finally:
            child.kill()
            child.wait()
        self.assertFalse(config.process_alive(child.pid))


if __name__ == "__main__":
    unittest.main()
