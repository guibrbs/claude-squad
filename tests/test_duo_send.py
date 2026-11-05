"""duo-send with a fake claude in a temporary HOME.

The fake claude answers like `claude -p --output-format stream-json`, like
`claude --bg` and like `claude agents|logs|stop`; FAKE_MODE picks a failure.
Nothing reaches a real account.
"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import urllib.parse

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "duo/duo-send.py"

FAKE_CLAUDE = r'''
import json, os, sys

args = sys.argv[1:]
if args[:2] == ["auth", "status"]:
    print(json.dumps({"loggedIn": os.environ.get("FAKE_AUTH") != "out", "authMethod": "claude.ai"}))
    sys.exit(0 if os.environ.get("FAKE_AUTH") != "out" else 1)
request = "" if "--bg" in args or args[:1] in (["agents"], ["logs"], ["stop"]) else sys.stdin.read()
leaked = sorted(k for k in os.environ if k.startswith(("CLAUDE", "ANTHROPIC_")) and k != "CLAUDE_CONFIG_DIR")
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps({"args": args, "request": request, "cwd": os.getcwd(),
                          "folder": os.environ.get("CLAUDE_CONFIG_DIR"), "leaked": leaked}, ensure_ascii=False) + "\n")
mode = os.environ.get("FAKE_MODE", "ok")

if args[:1] == ["agents"]:
    print(json.dumps([{"id": "4a79120f", "status": "busy", "title": "duo task"}]))
elif args[:1] in (["logs"], ["stop"]):
    print(f"{args[0]} {args[1]}")
elif "--bg" in args:
    if mode == "fail":
        print("error: not logged in", file=sys.stderr)
        sys.exit(1)
    print("backgrounded · 4a79120f\n  claude agents             list sessions")
else:
    session = args[args.index("--resume") + 1] if "--resume" in args else "sess-duo-0001"
    ok = mode != "fail"
    text = "Done: read the README." if ok else "Exceeded USD budget"
    for event in [
        {"type": "system", "subtype": "init", "session_id": session},
        {"type": "assistant", "message": {"id": "m1", "content": [
            {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "README.md"}},
            {"type": "text", "text": text}]}},
        {"type": "result", "subtype": "success" if ok else "error_max_budget_usd", "is_error": not ok,
         "result": text, "session_id": session, "total_cost_usd": 0.02},
    ]:
        print(json.dumps(event, ensure_ascii=False), flush=True)
'''


class DuoSendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="duo-send-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        self.home, self.bin, self.log = root / "home", root / "bin", root / "fake.jsonl"
        for folder in (self.home / ".claude", self.home / ".claude-duo", self.bin):
            folder.mkdir(parents=True)
        (self.bin / "claude").write_text(f"#!{sys.executable}\n{FAKE_CLAUDE}")
        (self.bin / "claude").chmod(0o755)
        self.repo = self.home / "project"
        self.repo.mkdir()
        self.env = {"HOME": str(self.home), "PATH": f"{self.bin}:/usr/bin:/bin", "FAKE_LOG": str(self.log),
                    # What a Desktop session of the original leaves in the environment and must not leak:
                    "CLAUDECODE": "1", "CLAUDE_CODE_ACCOUNT_UUID": "leaked", "ANTHROPIC_API_KEY": "leaked",
                    "CLAUDE_CONFIG_DIR": str(self.home / ".claude")}

    def send(self, *args, stdin=None, **env):
        return subprocess.run([sys.executable, "-I", str(SCRIPT), *args], capture_output=True, text=True,
                              input=stdin, env={**self.env, **env}, cwd=self.home, timeout=60)

    def send_json(self, *args, **kwargs):
        process = self.send("send", *args, "--json", **kwargs)
        return process, json.loads(process.stdout)

    def calls(self):
        lines = self.log.read_text().splitlines() if self.log.exists() else []
        return [json.loads(line) for line in lines]

    def test_sync_runs_on_the_duo_account_in_the_repo_and_returns_the_answer(self):
        process, end = self.send_json("read the README", "--repo", str(self.repo))
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual((end["ok"], end["mode"], end["answer"], end["session"]),
                         (True, "sync", "Done: read the README.", "sess-duo-0001"))
        self.assertAlmostEqual(end["cost_usd"], 0.02)
        [call] = self.calls()
        self.assertEqual(call["folder"], str(self.home / ".claude-duo"))
        self.assertEqual(call["leaked"], [])
        self.assertEqual(call["cwd"], str(self.repo))
        self.assertEqual(call["request"], "read the README")
        self.assertEqual(call["args"][:1], ["-p"])
        self.assertEqual(call["args"][call["args"].index("--permission-mode") + 1], "auto")
        self.assertIn("Claude Duo account", call["args"][call["args"].index("--append-system-prompt") + 1])
        self.assertNotIn("--disallowedTools", call["args"])

    def test_sync_plain_output_is_the_answer_and_stdin_carries_the_task(self):
        process = self.send("send", "-", "--repo", str(self.repo), stdin="a task with `backticks` and \"quotes\"")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stdout.strip(), "Done: read the README.")
        self.assertIn("duo Read: README.md", process.stderr)
        self.assertEqual(self.calls()[0]["request"], "a task with `backticks` and \"quotes\"")

    def test_sync_cost_ceiling_model_and_resume_reach_claude(self):
        process, end = self.send_json("go on", "--repo", str(self.repo), "--max-cost", "1.5", "--model", "sonnet",
                                      "--resume", "sess-duo-0042")
        self.assertEqual(process.returncode, 0, process.stderr)
        args = self.calls()[0]["args"]
        self.assertEqual(args[args.index("--max-budget-usd") + 1], "1.50")
        self.assertEqual(args[args.index("--model") + 1], "sonnet")
        self.assertEqual(args[args.index("--resume") + 1], "sess-duo-0042")
        self.assertEqual(end["session"], "sess-duo-0042")

    def test_sync_failure_is_reported_with_a_nonzero_exit(self):
        process, end = self.send_json("spend a lot", "--repo", str(self.repo), FAKE_MODE="fail")
        self.assertEqual(process.returncode, 1)
        self.assertFalse(end["ok"])
        self.assertIn("budget", end["error"])

    def test_bg_starts_a_background_session_on_the_duo_account(self):
        process, end = self.send_json("refactor the parser", "--repo", str(self.repo), "--bg", "--name", "parser")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual((end["ok"], end["mode"], end["id"]), (True, "bg", "4a79120f"))
        [call] = self.calls()
        self.assertEqual(call["folder"], str(self.home / ".claude-duo"))
        self.assertEqual(call["leaked"], [])
        self.assertEqual(call["cwd"], str(self.repo))
        self.assertEqual(call["args"][0], "--bg")
        self.assertEqual(call["args"][-1], "refactor the parser")
        self.assertEqual(call["args"][call["args"].index("--name") + 1], "parser")
        self.assertNotIn("-p", call["args"])
        # The follow-up commands point at the Duo's session list, not the original's.
        self.assertEqual(end["commands"]["attach"],
                         f"CLAUDE_CONFIG_DIR={self.home / '.claude-duo'} claude attach 4a79120f")

    def test_bg_failure_is_reported(self):
        process, end = self.send_json("task", "--repo", str(self.repo), "--bg", FAKE_MODE="fail")
        self.assertEqual(process.returncode, 1)
        self.assertEqual((end["ok"], end["id"]), (False, None))
        self.assertIn("not logged in", end["error"])

    def test_follow_up_commands_run_on_the_duo_account(self):
        listed = self.send("agents")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertEqual(json.loads(listed.stdout)[0]["id"], "4a79120f")
        self.assertEqual(self.send("logs", "4a79120f").stdout.strip(), "logs 4a79120f")
        calls = self.calls()
        self.assertEqual([c["args"] for c in calls], [["agents", "--json"], ["logs", "4a79120f"]])
        self.assertTrue(all(c["folder"] == str(self.home / ".claude-duo") and c["leaked"] == [] for c in calls))

    def test_refusals_never_reach_claude(self):
        missing = self.home / "nowhere"
        cases = [
            (("send", "task", "--repo", str(missing), "--json"), "does not exist"),
            (("send", "   ", "--repo", str(self.repo), "--json"), "task is empty"),
            (("send", "task", "--repo", str(self.repo), "--bg", "--max-cost", "2", "--json"), "synchronous mode"),
            (("send", "task", "--repo", str(self.repo), "--name", "x", "--json"), "with --bg"),
            (("send", "task", "--repo", str(self.repo), "--max-cost", "0", "--json"), "above zero"),
            (("send", "task", "--repo", str(self.repo), "--model", "bad model;rm", "--json"), "invalid model"),
            (("send", "task", "--repo", str(self.repo), "--resume", "../x", "--json"), "invalid session id"),
        ]
        for args, message in cases:
            with self.subTest(args=args):
                process = self.send(*args)
                self.assertEqual(process.returncode, 1)
                self.assertIn(message, json.loads(process.stdout)["error"])
        self.assertIn("invalid choice", self.send("send", "t", "--permission", "bypassPermissions").stderr)
        self.assertEqual(self.calls(), [])

    def test_a_signed_out_duo_cli_is_refused_with_the_login_command(self):
        for args in (("send", "task", "--repo", str(self.repo)), ("send", "task", "--repo", str(self.repo), "--bg")):
            with self.subTest(args=args):
                process = self.send(*args, FAKE_AUTH="out")
                self.assertEqual(process.returncode, 1)
                self.assertIn("not signed in", process.stderr)
                self.assertIn(f"CLAUDE_CONFIG_DIR={self.home / '.claude-duo'} claude auth login", process.stderr)
        self.assertEqual(self.calls(), [])

    def test_refuses_when_the_duo_account_has_no_code_folder(self):
        (self.home / ".claude-duo").rmdir()
        process = self.send("send", "task", "--repo", str(self.repo))
        self.assertEqual(process.returncode, 1)
        self.assertIn("no Code folder yet", process.stderr)
        self.assertEqual(self.calls(), [])

    def test_the_duo_folder_comes_from_the_installed_config(self):
        custom = self.home / "code-duo"
        custom.mkdir()
        config = self.home / ".config/claude-instances/claude-duo.conf"
        config.parent.mkdir(parents=True)
        config.write_text("Duo | io.github.x.claude-duo | ~/Library/Application Support/Claude-Duo | 212 | "
                          "CLAUDE_CONFIG_DIR=~/code-duo\n")
        pointer = self.home / ".local/share/claude-duo/config-path"
        pointer.parent.mkdir(parents=True)
        pointer.write_text(str(config))
        process, end = self.send_json("task", "--repo", str(self.repo))
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(self.calls()[0]["folder"], str(custom))


    def install_trio(self):
        (self.home / ".claude-trio").mkdir()
        config = self.home / ".config/claude-instances/claude-duo.conf"
        config.parent.mkdir(parents=True)
        config.write_text("@mode original-copy\n@apps user\n"
                          "Duo | io.github.x.claude-duo | ~/Library/Application Support/Claude-Duo | 212 | CLAUDE_CONFIG_DIR=~/.claude-duo\n"
                          "Trio | io.github.x.claude-trio | ~/Library/Application Support/Claude-Trio | 275 | CLAUDE_CONFIG_DIR=~/.claude-trio\n")
        pointer = self.home / ".local/share/claude-duo/config-path"
        pointer.parent.mkdir(parents=True)
        pointer.write_text(str(config))

    def test_to_sends_to_another_copy_of_the_config(self):
        self.install_trio()
        process, end = self.send_json("read the README", "--repo", str(self.repo), "--to", "Trio")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(end["account"], "trio")
        [call] = self.calls()
        self.assertEqual(call["folder"], str(self.home / ".claude-trio"))
        self.assertIn("Claude Trio account", call["args"][call["args"].index("--append-system-prompt") + 1])
        background, started = self.send_json("task", "--repo", str(self.repo), "--bg", "--to", "trio")
        self.assertEqual(background.returncode, 0, background.stderr)
        self.assertEqual(started["commands"]["attach"], f"CLAUDE_CONFIG_DIR={self.home / '.claude-trio'} claude attach 4a79120f")
        self.assertEqual(self.send("logs", "4a79120f", "--to", "trio").returncode, 0)
        self.assertEqual(self.calls()[-1]["folder"], str(self.home / ".claude-trio"))

    def test_to_refuses_the_original_and_unknown_accounts(self):
        self.install_trio()
        for name, message in (("original", "names a copy"), ("quatro", "this Mac has: original, duo, trio")):
            with self.subTest(name=name):
                process = self.send("send", "task", "--repo", str(self.repo), "--to", name, "--json")
                self.assertEqual(process.returncode, 1)
                self.assertIn(message, json.loads(process.stdout)["error"])
        self.assertEqual(self.calls(), [])

class DuoOpenTests(unittest.TestCase):
    """`open` and `send --open`, in process, with `open` and `claude -p` replaced."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="duo-open-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        (self.home / ".claude-duo").mkdir()
        self.repo = self.home / "project folder"
        self.repo.mkdir()
        self.config = self.home / ".config/claude-instances/claude-duo.conf"
        self.config.parent.mkdir(parents=True)
        pointer = self.home / ".local/share/claude-duo/config-path"
        pointer.parent.mkdir(parents=True)
        pointer.write_text(str(self.config))
        self.write_config("@links claude-dev")
        # For the whole test: the config's ~/.claude-duo expands at call time.
        home = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        home.start()
        self.addCleanup(home.stop)
        spec = importlib.util.spec_from_file_location("duo_send_under_test", SCRIPT)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.opened = []

        def fake_run(argv, **kwargs):
            if argv[1:3] == ["auth", "status"]:  # The sign-in check: signed in.
                return subprocess.CompletedProcess(argv, 0, json.dumps({"loggedIn": True}), "")
            self.opened.append(argv)
            return subprocess.CompletedProcess(argv, 0, "", "")
        for patcher in (mock.patch.object(self.module.subprocess, "run", side_effect=fake_run),
                        # No real claude is needed, or present on CI.
                        mock.patch.object(self.module.loop, "claude_bin", return_value="/fake/bin/claude")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def write_config(self, links=""):
        self.config.write_text(f"@mode original-copy\n@apps user\n{links}\n"
                               "Duo | io.github.tester.claude-duo | ~/Library/Application Support/Claude-Duo | 212 | "
                               "CLAUDE_CONFIG_DIR=~/.claude-duo\n")

    def main(self, *args):
        out, err = io.StringIO(), io.StringIO()
        umask = os.umask(0o022)  # main() sets 0o077 for its process; keep the test runner's
        os.umask(umask)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = self.module.main(list(args))
        finally:
            os.umask(umask)
        return code, out.getvalue(), err.getvalue()

    def link(self, index=-1):
        argv = self.opened[index]
        self.assertEqual(argv[:3], ["/usr/bin/open", "-b", "io.github.tester.claude-duo"])
        parsed = urllib.parse.urlsplit(argv[3])
        return parsed.scheme, parsed.netloc + parsed.path, dict(urllib.parse.parse_qsl(parsed.query))

    def test_open_starts_a_new_session_in_the_duo_window_with_folder_and_prompt(self):
        code, out, _ = self.main("open", "--repo", str(self.repo), "--prompt", "fix the tests & push? no")
        self.assertEqual(code, 0)
        self.assertIn("opened in Claude Duo", out)
        scheme, where, query = self.link()
        self.assertEqual((scheme, where), ("claude-dev", "code/new"))
        self.assertEqual(query, {"folder": str(self.repo), "q": "fix the tests & push? no", "source": "duo-send"})

    def test_open_reads_a_brief_with_quotes_and_backticks_from_stdin(self):
        brief = "Fix `make test` in the \"api\" folder.\nDone when CI is green; don't push to main."
        with mock.patch.object(self.module.sys, "stdin", io.StringIO(brief)):
            code, _, _ = self.main("open", "--repo", str(self.repo), "--prompt", "-")
        self.assertEqual(code, 0)
        self.assertEqual(self.link()[2]["q"], brief)

    def test_inbox_open_fills_in_duo_inbox_in_a_new_duo_session(self):
        code, out, _ = self.main("inbox", "--open", "--repo", str(self.repo))
        self.assertEqual(code, 0)
        self.assertIn("press Enter there to send /duo-inbox", out)
        self.assertEqual(self.link(), ("claude-dev", "code/new",
                                       {"folder": str(self.repo), "q": "/duo-inbox", "source": "duo-send"}))

    def test_open_imports_a_session_by_its_uuid(self):
        session = "0f8fad5b-d9cb-469f-a165-70867728950e"
        self.assertEqual(self.main("open", "--session", session)[0], 0)
        self.assertEqual(self.link(), ("claude-dev", "resume", {"session": session, "source": "duo-send"}))

    def test_send_open_imports_the_finished_session(self):
        session = "0f8fad5b-d9cb-469f-a165-70867728950e"
        finished = {"ok": True, "session": session, "cost_usd": 0.01, "answer": "done", "error": None}
        with mock.patch.object(self.module.loop, "converse", return_value=finished):
            code, out, err = self.main("send", "task", "--repo", str(self.repo), "--open", "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["opened"].split("?")[0], "claude-dev://resume")
        self.assertEqual(self.link()[2]["session"], session)

    def add_trio(self, links):
        self.write_config(links)
        (self.home / ".claude-trio").mkdir()
        self.config.write_text(self.config.read_text() + "Trio | io.github.tester.claude-trio | "
                               "~/Library/Application Support/Claude-Trio | 275 | CLAUDE_CONFIG_DIR=~/.claude-trio\n")

    def test_with_several_copies_only_the_one_the_links_name_opens_sessions(self):
        self.add_trio("@links claude-dev Trio")
        code, out, _ = self.main("open", "--repo", str(self.repo), "--to", "trio")
        self.assertEqual(code, 0)
        self.assertIn("opened in Claude Trio", out)
        self.assertEqual(self.opened[-1][:3], ["/usr/bin/open", "-b", "io.github.tester.claude-trio"])
        count = len(self.opened)
        code, _, err = self.main("open", "--repo", str(self.repo))  # The Duo, which has no links now.
        self.assertEqual(code, 1)
        self.assertIn("the config gives claude-dev:// to Claude Trio", err)
        self.assertEqual(len(self.opened), count)

    def test_without_links_in_the_config_nothing_runs_and_the_fix_is_named(self):
        self.write_config()
        with mock.patch.object(self.module.loop, "converse") as converse:
            for args in (("open", "--repo", str(self.repo)), ("send", "task", "--repo", str(self.repo), "--open")):
                with self.subTest(args=args):
                    code, _, err = self.main(*args)
                    self.assertEqual(code, 1)
                    self.assertIn("@links claude-dev", err)
            converse.assert_not_called()
        self.assertEqual(self.opened, [])

    def test_refusals(self):
        cases = [(("open", "--session", "sess-duo-0001"), "full UUID"),
                 (("open", "--session", "0f8fad5b-d9cb-469f-a165-70867728950e", "--prompt", "x"), "leave out"),
                 (("open", "--repo", str(self.home / "nowhere")), "does not exist"),
                 (("open", "--prompt", "x" * 4001), "longer than"),
                 (("send", "task", "--repo", str(self.repo), "--bg", "--open"), "open --session")]
        for args, message in cases:
            with self.subTest(args=args[:2]):
                code, _, err = self.main(*args)
                self.assertEqual(code, 1)
                self.assertIn(message, err)
        self.assertEqual(self.opened, [])


class DuoSessionsTests(unittest.TestCase):
    """`sessions` reads the Duo's registry; synthetic entries and real sockets under /tmp/cc-socks only."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="duo-sessions-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.registry = self.home / ".claude-duo/sessions"
        self.registry.mkdir(parents=True)
        (self.home / ".claude/sessions").mkdir(parents=True)
        self.sockets = []
        self.env = {"HOME": str(self.home), "PATH": "/usr/bin:/bin"}

    def live_socket(self):
        """A listening socket in /tmp/cc-socks with a pid number nothing else uses there."""
        folder = Path("/tmp/cc-socks")
        folder.mkdir(mode=0o700, exist_ok=True)
        number = 900000000 + os.getpid() % 1000 * 1000 + len(self.sockets)
        path = folder / f"{number}.sock"
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(path))
        self.addCleanup(lambda: (server.close(), path.unlink(missing_ok=True)))
        self.sockets.append(path)
        return str(path)

    def entry(self, name, *, pid=None, sock=None, status="idle", file=None, cwd="/x", registry=None):
        data = {"pid": pid or os.getpid(), "name": name, "status": status, "kind": "interactive",
                "sessionId": "0f8fad5b-d9cb-469f-a165-70867728950e", "cwd": cwd,
                "messagingSocketPath": sock if sock is not None else self.live_socket()}
        folder = registry or self.registry
        (folder / (file or f"{len(list(folder.iterdir()))}.json")).write_text(json.dumps(data))
        return data

    def run_tool(self, *args):
        return subprocess.run([sys.executable, "-I", str(SCRIPT), *args], capture_output=True, text=True,
                              env=self.env, timeout=60)

    def test_inbox_is_the_one_duo_inbox_session_in_that_folder(self):
        repo, other = self.home / "repo", self.home / "other"
        repo.mkdir(), other.mkdir()
        inbox = self.entry("[repo] Duo inbox", cwd=str(repo))
        self.entry("[other] Duo inbox", cwd=str(other))
        self.entry("[repo] Something else", cwd=str(repo))
        found = self.run_tool("inbox", "--repo", str(repo))
        self.assertEqual((found.returncode, found.stdout.strip()), (0, "uds:" + inbox["messagingSocketPath"]))
        self.entry("[repo] Duo inbox (2)", cwd=str(repo))
        two = self.run_tool("inbox", "--repo", str(repo))
        self.assertEqual(two.returncode, 1)
        self.assertIn("more than one Duo inbox", two.stderr)

    def test_no_inbox_names_the_command_that_opens_one(self):
        repo = self.home / "repo"
        repo.mkdir()
        self.entry("[repo] Duo inbox", cwd=str(repo), pid=2 ** 22 + 7, sock="/tmp/cc-socks/1.sock")  # gone
        none = self.run_tool("inbox", "--repo", str(repo))
        self.assertEqual(none.returncode, 1)
        self.assertIn(f"duo-send.py inbox --open --repo {repo}", none.stderr)

    def test_whois_tells_which_account_answers_on_an_address(self):
        duo = self.entry("[repo] Duo inbox")
        original = self.entry("Valete V2 - Orquestrador", registry=self.home / ".claude/sessions")
        for data, account in ((duo, "duo"), (original, "original")):
            with self.subTest(account=account):
                found = self.run_tool("whois", "uds:" + data["messagingSocketPath"], "--json")
                self.assertEqual(found.returncode, 0, found.stderr)
                self.assertEqual((json.loads(found.stdout)["account"], json.loads(found.stdout)["name"]),
                                 (account, data["name"]))
        for address, error in (("uds:/tmp/cc-socks/999999998.sock", "no live session"),
                               ("uds:/etc/passwd", "not a session address")):
            with self.subTest(address=address):
                process = self.run_tool("whois", address)
                self.assertEqual(process.returncode, 1)
                self.assertIn(error, process.stderr)

    def add_trio(self):
        registry = self.home / ".claude-trio/sessions"
        registry.mkdir(parents=True)
        config = self.home / ".config/claude-instances/claude-duo.conf"
        config.parent.mkdir(parents=True)
        config.write_text("@mode original-copy\n@apps user\n"
                          "Duo | io.github.x.claude-duo | ~/Library/Application Support/Claude-Duo | 212 | CLAUDE_CONFIG_DIR=~/.claude-duo\n"
                          "Trio | io.github.x.claude-trio | ~/Library/Application Support/Claude-Trio | 275 | CLAUDE_CONFIG_DIR=~/.claude-trio\n")
        pointer = self.home / ".local/share/claude-duo/config-path"
        pointer.parent.mkdir(parents=True)
        pointer.write_text(str(config))
        return registry

    def test_inbox_and_whois_reach_every_copy(self):
        registry = self.add_trio()
        repo = self.home / "repo"
        repo.mkdir()
        trio = self.entry("[repo] Duo inbox", cwd=str(repo), registry=registry)
        found = self.run_tool("inbox", "--repo", str(repo), "--to", "trio")
        self.assertEqual((found.returncode, found.stdout.strip()), (0, "uds:" + trio["messagingSocketPath"]))
        none = self.run_tool("inbox", "--repo", str(repo))  # The Duo has none there.
        self.assertEqual(none.returncode, 1)
        self.assertIn("no Duo inbox is open", none.stderr)
        trio_none = self.run_tool("inbox", "--repo", str(self.home), "--to", "trio")
        self.assertIn(f"duo-send.py inbox --open --to trio --repo {self.home}", trio_none.stderr)
        who = self.run_tool("whois", "uds:" + trio["messagingSocketPath"], "--json")
        self.assertEqual(who.returncode, 0, who.stderr)
        self.assertEqual(json.loads(who.stdout)["account"], "trio")

    def run_sessions(self, *args):
        return self.run_tool("sessions", *args)

    def test_lists_live_duo_sessions_with_their_address(self):
        live = self.entry("[proj] Valete · telas", status="busy")
        self.entry("Gone session", pid=2 ** 22 + 7, sock="/tmp/cc-socks/1.sock")  # process gone
        self.entry("No socket", sock="/tmp/cc-socks/999999999.sock")
        self.entry("Odd path", sock=str(self.home / "x.sock"))
        # The original's registry is never read.
        (self.home / ".claude/sessions/1.json").write_text(json.dumps({"pid": os.getpid(), "name": "Original one",
                                                                         "messagingSocketPath": live["messagingSocketPath"]}))
        listed = json.loads(self.run_sessions("--json").stdout)["sessions"]
        self.assertEqual([(s["name"], s["address"]) for s in listed],
                         [("[proj] Valete · telas", "uds:" + live["messagingSocketPath"])])
        with_stale = json.loads(self.run_sessions("--all", "--json").stdout)["sessions"]
        self.assertEqual(sorted(s["name"] for s in with_stale if not s["live"]), ["Gone session", "No socket", "Odd path"])
        self.assertTrue(all(s["address"] is None for s in with_stale if not s["live"]))
        plain = self.run_sessions().stdout
        self.assertIn("busy   uds:" + live["messagingSocketPath"], plain)

    def test_address_needs_exactly_one_match(self):
        one = self.entry("[proj] Valete · telas")
        self.entry("[proj] Valete · conferências")
        found = self.run_sessions("telas", "--address")
        self.assertEqual((found.returncode, found.stdout.strip()), (0, "uds:" + one["messagingSocketPath"]))
        for args, error in ((("valete", "--address"), "2 open Duo sessions match"),
                            (("nobody", "--address"), "0 open Duo sessions match"),
                            (("--address",), "needs a name")):
            with self.subTest(args=args):
                process = self.run_sessions(*args)
                self.assertEqual(process.returncode, 1)
                self.assertIn(error, process.stderr)

    def test_symlinked_or_broken_entries_are_skipped(self):
        self.entry("Real")
        (self.registry / "bad.json").write_text("not json")
        (self.registry / "link.json").symlink_to(self.registry / "0.json")
        listed = json.loads(self.run_sessions("--json").stdout)["sessions"]
        self.assertEqual([s["name"] for s in listed], ["Real"])


if __name__ == "__main__":
    unittest.main()
