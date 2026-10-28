import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from helpers import load

send = load("duo-send.py", "duo_send")


class Sessions(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.duo = Path(self.folder.name) / ".claude-duo"
        (self.duo / "sessions").mkdir(parents=True)
        self.patch = mock.patch.object(send.loop.duo_config, "read_config", return_value={"codeConfigDir": str(self.duo)})
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.folder.cleanup()

    def register(self, pid, name, pipe):
        (self.duo / "sessions" / f"{pid}.json").write_text(json.dumps(
            {"pid": pid, "name": name, "status": "idle", "messagingSocketPath": pipe, "sessionId": "s"}), encoding="utf-8")

    def test_live_session_gets_pipe_address(self):
        self.register(os.getpid(), "revisar PR", r"\\.\pipe\LOCAL\cc-msg-bf0fb951477325656defada0b27f95fc")
        found = send.open_duo_sessions("revisar")
        self.assertEqual(found[0]["address"], r"uds:\\.\pipe\LOCAL\cc-msg-bf0fb951477325656defada0b27f95fc")

    def test_dead_or_odd_sessions_are_hidden(self):
        self.register(2 ** 31 - 2, "morta", r"\\.\pipe\LOCAL\cc-msg-bf0fb951477325656defada0b27f95fc")
        self.register(os.getpid(), "estranha", r"\\evil\share\pipe")
        self.assertEqual(send.open_duo_sessions(), [])
        self.assertEqual({item["name"] for item in send.open_duo_sessions(include_stale=True)}, {"morta", "estranha"})

    def test_missing_duo_folder_is_refused(self):
        with mock.patch.object(send.loop.duo_config, "read_config", return_value={"codeConfigDir": "C:\\nao\\existe"}):
            with self.assertRaises(send.Refused):
                send.open_duo_sessions()


class Options(unittest.TestCase):
    def test_checks(self):
        for kwargs in ({"model": "bad model!"}, {"name": "x" * 61}, {"session": "../etc"}, {"cost_max": 0}):
            options = {"model": None, "name": None, "session": None, "cost_max": None, **kwargs}
            with self.assertRaises(send.Refused):
                send.check_options("task", **options)
        with self.assertRaises(send.Refused):
            send.check_options("  ", model=None, name=None, session=None, cost_max=None)
        send.check_options("task", model="claude-opus-5-5", name="nome curto", session=None, cost_max=1.5)

    def test_follow_up_uses_the_wrapper(self):
        self.assertEqual(send.follow_up("agents"), "claude-duo agents")
        self.assertEqual(send.follow_up("logs", "abc123"), 'claude-duo logs "abc123"')

    def test_bypass_is_not_offered(self):
        self.assertNotIn("bypassPermissions", send.PERMISSION_MODES)


if __name__ == "__main__":
    unittest.main()
