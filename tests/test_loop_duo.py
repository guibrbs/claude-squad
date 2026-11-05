"""loop-duo with a fake claude and gh in a temporary HOME and repository.

The fake claude answers like `claude -p --output-format stream-json`; its
script per role comes from FAKE_LOOP. Nothing reaches a real account.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "duo/loop-duo.py"

FAKE_CLAUDE = r'''
import json, os, subprocess, sys
from pathlib import Path

args = sys.argv[1:]
if args[:2] == ["auth", "status"]:
    out = os.environ.get("FAKE_AUTH_OUT")
    signed_out = out == ("duo" if os.environ.get("CLAUDE_CONFIG_DIR") else "original")
    print(json.dumps({"loggedIn": not signed_out}))
    sys.exit(1 if signed_out else 0)
request = sys.stdin.read()
prompt = args[args.index("--append-system-prompt") + 1]
role = "dev" if "DEVELOPER in a loop-duo" in prompt else "reviewer"
counter = Path(os.environ["FAKE_LOG"]).with_name(f"turn-{role}")
turn = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(turn))
leaked = sorted(k for k in os.environ if k.startswith(("CLAUDE", "ANTHROPIC_")) and k != "CLAUDE_CONFIG_DIR")
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps({"loop": role, "turn": turn, "request": request, "args": args, "cwd": os.getcwd(),
                          "folder": os.environ.get("CLAUDE_CONFIG_DIR"), "leaked": leaked}, ensure_ascii=False) + "\n")
script = os.environ.get("FAKE_LOOP", "complete")

def git(*command):
    subprocess.run(["/usr/bin/git", *command], check=True, capture_output=True)

def block(data):
    return "Done.\n\n```loop-duo\n" + json.dumps(data, ensure_ascii=False) + "\n```"

if script == "no_block":
    text = "I forgot the block."
elif role == "dev" and script == "blocked":
    text = block({"state": "blocked", "reason": "the test environment credentials are missing"})
elif role == "dev":
    if turn == 1 and script == "complete":
        text = block({"state": "question", "questions": [{"question": "Python or shell?", "options": ["Python", "shell"]}]})
    elif "The reviewer asked" in request:
        text = block({"state": "answer", "answers": ["because the project is already Python"]})
    elif "not tracked by git" in request:
        # Commits only the tests it meant to add; a .env stays untracked.
        for test in sorted(Path(".").glob("test_add_*.py")):
            git("add", str(test))
        git("commit", "-q", "-m", "test: add")
        text = block({"state": "review", "summary": "committed the test"})
    elif not Path("add.py").exists():
        Path("add.py").write_text("def add(a, b):\n    return a + b\n")
        git("add", "add.py")
        git("commit", "-q", "-m", "feat: add")
        text = block({"state": "review", "summary": "created add.py"})
    else:
        # Left uncommitted on purpose: the loop commits the tracked change and
        # sends the untracked files back before the review.
        with open("add.py", "a") as source:
            source.write("# checked by the test\n")
        Path(f"test_add_{turn}.py").write_text("from add import add\n\nassert add(1, 2) == 3\n")
        if script == "leaves_secret":
            Path(".env").write_text("TOKEN=not-for-git\n")
        text = block({"state": "review", "summary": "added the test"})
else:
    has_test = bool(list(Path(".").glob("test_add_*.py")))
    if "The developer asked" in request:
        text = block({"state": "answer", "answers": ["Python"]})
    elif script == "reviewer_edits":
        Path("add.py").write_text("def add(a, b):\n    return b + a\n")
        text = block({"state": "approved", "summary": "fixed it myself"})
    elif script == "reviewer_commits":
        Path("add.py").write_text("def add(a, b):\n    return b + a\n")
        git("commit", "-qam", "review: fix it myself")
        text = block({"state": "approved", "summary": "fixed and committed it myself"})
    elif script == "reviewer_asks" and turn == 1:
        text = block({"state": "question", "questions": ["Why add.py?"]})
    elif script == "never_approves" or not has_test:
        text = block({"state": "changes", "comments": ["add.py:1 — missing test"]})
    else:
        text = block({"state": "approved", "summary": "add.py with a test; ok"})

session = args[args.index("--resume") + 1] if "--resume" in args else f"sess-{role}-0001"
for event in [
    {"type": "system", "subtype": "init", "session_id": session, "model": "claude-sonnet-5-5"},
    {"type": "assistant", "message": {"id": f"m{turn}", "content": [
        {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "git status"}},
        {"type": "text", "text": text}]}},
    {"type": "result", "subtype": "success", "is_error": False, "result": text, "session_id": session,
     "duration_ms": 500, "total_cost_usd": 0.01},
]:
    print(json.dumps(event, ensure_ascii=False), flush=True)
'''

FAKE_GH = '#!/bin/sh\nprintf \'%s\\n\' "$@" > "$HOME/gh.args"\necho https://github.com/x/y/pull/7\n'


class LoopDuoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="loop-duo-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        self.home, self.bin, self.log = root / "home", root / "bin", root / "fake.jsonl"
        for folder in (self.home / ".claude", self.home / ".claude-duo", self.bin):
            folder.mkdir(parents=True)
        (self.bin / "claude").write_text(f"#!{sys.executable}\n{FAKE_CLAUDE}")
        (self.bin / "gh").write_text(FAKE_GH)
        for name in ("claude", "gh"):
            (self.bin / name).chmod(0o755)
        self.repo = self.home / "project"
        self.repo.mkdir()
        self.env = {"HOME": str(self.home), "PATH": f"{self.bin}:/usr/bin:/bin", "FAKE_LOG": str(self.log),
                    "GIT_CONFIG_NOSYSTEM": "1",
                    # What a Desktop session leaves in the environment and must not leak:
                    "CLAUDECODE": "1", "CLAUDE_CODE_ACCOUNT_UUID": "leaked", "ANTHROPIC_API_KEY": "leaked",
                    "CLAUDE_CONFIG_DIR": str(root / "other")}
        if "DEVELOPER_DIR" in os.environ:  # Lets /usr/bin/git work where only the Command Line Tools are set up.
            self.env["DEVELOPER_DIR"] = os.environ["DEVELOPER_DIR"]
        for command in (["init", "-q", "-b", "main"], ["config", "user.email", "loop@example.com"],
                        ["config", "user.name", "Loop"], ["commit", "-q", "--allow-empty", "-m", "start"]):
            self.git(*command)
        self.base = self.git("rev-parse", "HEAD")

    def git(self, *args, folder=None):
        return subprocess.run(["/usr/bin/git", "-C", str(folder or self.repo), *args], check=True, capture_output=True,
                              text=True, env=self.env).stdout.strip()

    def run_script(self, *args, **env):
        return subprocess.run([sys.executable, "-I", str(SCRIPT), *args], capture_output=True, text=True,
                              env={**self.env, **env}, cwd=self.home, timeout=60)

    def loop(self, *args, **env):
        process = self.run_script("run", "write the add function", "--repo", str(self.repo), *args, "--json", **env)
        return process, json.loads(process.stdout)

    def calls(self, role=None):
        lines = self.log.read_text().splitlines() if self.log.exists() else []
        return [c for c in map(json.loads, lines) if role is None or c["loop"] == role]

    def test_question_goes_to_the_reviewer_and_the_loop_approves_after_changes(self):
        process, end = self.loop()
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual((end["status"], end["round"]), ("approved", 2))
        # The developer's question went to the reviewer, and the answer came back.
        reviewer = self.calls("reviewer")
        self.assertIn("The developer asked", reviewer[0]["request"])
        self.assertIn("Python or shell?", reviewer[0]["request"])
        self.assertIn("→ Python", self.calls("dev")[1]["request"])
        # The requested changes reach the developer in the next round.
        self.assertTrue(any("add.py:1 — missing test" in c["request"] for c in self.calls("dev")))
        # The worktree holds the loop branch; the user's folder stays where it was.
        tree = Path(end["area"]["tree"])
        self.assertEqual(tree, self.home / f".local/share/claude-duo/loops/{end['id']}/tree")
        self.assertTrue(end["area"]["branch"].startswith("loop-duo/write-the-add-function-"))
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "HEAD", folder=tree), end["area"]["branch"])
        self.assertEqual(self.git("rev-parse", "HEAD"), self.base)
        # The untracked test went back to the developer, who committed it; the
        # tracked leftover was committed by the loop.
        self.assertTrue(any("not tracked by git" in c["request"] and "test_add_" in c["request"]
                            for c in self.calls("dev")))
        self.assertEqual(len(end["commits"]), 3)
        self.assertTrue(any("left uncommitted" in c for c in end["commits"]))
        self.assertEqual(self.git("status", "--porcelain", folder=tree), "")
        # The conversation stays readable, the state saved, both private.
        conversation = Path(end["conversation"])
        self.assertIn("## Goal", conversation.read_text())
        self.assertIn("Python or shell?", conversation.read_text())
        state = conversation.parent / "loop.json"
        self.assertEqual(json.loads(state.read_text())["status"], "approved")
        self.assertEqual([p.stat().st_mode & 0o777 for p in (conversation.parent, conversation, state)],
                         [0o700, 0o600, 0o600])
        self.assertAlmostEqual(end["cost_usd"], 0.01 * len(self.calls()))

    def test_each_account_in_its_role_and_the_reviewer_without_edit_tools(self):
        self.loop()
        dev, reviewer = self.calls("dev"), self.calls("reviewer")
        self.assertTrue(dev and reviewer)
        self.assertTrue(all(c["folder"] is None for c in dev))  # original: ~/.claude, no CLAUDE_CONFIG_DIR
        self.assertTrue(all(c["folder"] == str(self.home / ".claude-duo") for c in reviewer))
        self.assertTrue(all(c["args"][c["args"].index("--disallowedTools") + 1:][:3] == ["Edit", "Write", "NotebookEdit"]
                            for c in reviewer))
        self.assertTrue(all("--disallowedTools" not in c["args"] for c in dev))
        self.assertTrue(all(c["args"][c["args"].index("--permission-mode") + 1] == "auto" for c in dev + reviewer))
        self.assertTrue(all(c["leaked"] == [] for c in dev + reviewer))
        self.assertTrue(all(Path(c["cwd"]).name == "tree" for c in dev + reviewer))
        # From the second turn on, each one continues its own session.
        self.assertTrue(all("--resume" in c["args"] for c in dev[1:] + reviewer[1:]))

    def test_roles_can_swap_and_the_duo_folder_comes_from_the_installed_config(self):
        custom = self.home / "code-duo"
        custom.mkdir()
        config = self.home / ".config/claude-instances/claude-duo.conf"
        config.parent.mkdir(parents=True)
        config.write_text("@mode original-copy\n@apps user\n"
                          "Duo | io.github.x.claude-duo | ~/Library/Application Support/Claude-Duo | 212 | CLAUDE_CONFIG_DIR=~/code-duo\n")
        pointer = self.home / ".local/share/claude-duo/config-path"
        pointer.parent.mkdir(parents=True)
        pointer.write_text(f"{config}\n")
        process, end = self.loop("--dev", "duo", "--reviewer", "original")
        self.assertEqual(end["status"], "approved", process.stderr)
        self.assertTrue(all(c["folder"] == str(custom) for c in self.calls("dev")))
        self.assertTrue(all(c["folder"] is None for c in self.calls("reviewer")))

    def install_trio(self):
        for folder in ("code-trio",):
            (self.home / folder).mkdir()
        config = self.home / ".config/claude-instances/claude-duo.conf"
        config.parent.mkdir(parents=True)
        config.write_text("@mode original-copy\n@apps user\n@links claude-dev Duo\n"
                          "Duo | io.github.x.claude-duo | ~/Library/Application Support/Claude-Duo | 212 | CLAUDE_CONFIG_DIR=~/.claude-duo\n"
                          "Trio | io.github.x.claude-trio | ~/Library/Application Support/Claude-Trio | 275 | CLAUDE_CONFIG_DIR=~/code-trio\n")
        pointer = self.home / ".local/share/claude-duo/config-path"
        pointer.parent.mkdir(parents=True)
        pointer.write_text(f"{config}\n")

    def test_any_copy_in_the_config_can_take_a_role(self):
        self.install_trio()
        process, end = self.loop("--dev", "Trio", "--reviewer", "duo")
        self.assertEqual(end["status"], "approved", process.stderr)
        self.assertEqual((end["dev"], end["reviewer"]), ("trio", "duo"))
        self.assertTrue(all(c["folder"] == str(self.home / "code-trio") for c in self.calls("dev")))
        self.assertTrue(all(c["folder"] == str(self.home / ".claude-duo") for c in self.calls("reviewer")))

    def test_an_account_the_config_does_not_have_is_refused_with_the_known_ones(self):
        self.install_trio()
        for name in ("quatro", "../x", "Trio;rm"):
            with self.subTest(name=name):
                refused = self.run_script("run", "x", "--repo", str(self.repo), "--reviewer", name, "--json")
                self.assertEqual(refused.returncode, 1)
                self.assertIn("this Mac has: original, duo, trio", json.loads(refused.stdout)["error"])
        same = self.run_script("run", "x", "--repo", str(self.repo), "--dev", "TRIO", "--reviewer", "trio", "--json")
        self.assertIn("different accounts", json.loads(same.stdout)["error"])
        self.assertEqual(self.calls(), [])

    def test_the_reviewer_also_asks_the_developer(self):
        process, end = self.loop(FAKE_LOOP="reviewer_asks")
        self.assertEqual(end["status"], "approved", process.stderr)
        self.assertTrue(any("The reviewer asked" in c["request"] and "Why add.py?" in c["request"]
                            for c in self.calls("dev")))
        self.assertTrue(any("because the project is already Python" in c["request"] for c in self.calls("reviewer")))

    def test_blocked_stops_without_publishing(self):
        process, end = self.loop("--until", "pr", FAKE_LOOP="blocked")
        self.assertEqual((process.returncode, end["status"]), (1, "blocked"))
        self.assertIn("credentials", end["reason"])
        self.assertNotIn("pr", end)
        self.assertEqual(self.calls("reviewer"), [])
        self.assertFalse((self.home / "gh.args").exists())

    def test_a_missing_block_is_asked_for_once_then_the_loop_stops(self):
        process, end = self.loop(FAKE_LOOP="no_block")
        self.assertEqual((process.returncode, end["status"]), (1, "failed"))
        self.assertIn("loop-duo block", end["reason"])
        self.assertEqual(len(self.calls("dev")), 2)
        self.assertIn("did not end with the agreed", self.calls("dev")[1]["request"])

    def test_round_ceiling(self):
        process, end = self.loop("--rounds", "2", FAKE_LOOP="never_approves")
        self.assertEqual((end["status"], end["round"]), ("round_ceiling", 2))
        self.assertEqual(len(self.calls("reviewer")), 2)

    def test_cost_ceiling(self):
        process, end = self.loop("--max-cost", "0.01", FAKE_LOOP="never_approves")
        self.assertEqual((end["status"], end["round"]), ("cost_ceiling", 1))

    def test_until_pr_pushes_and_opens_a_draft_pr(self):
        origin = self.home / "origin.git"
        subprocess.run(["/usr/bin/git", "init", "-q", "--bare", str(origin)], check=True, env=self.env)
        self.git("remote", "add", "origin", str(origin))
        process, end = self.loop("--until", "pr")
        self.assertEqual(end["pr"], "https://github.com/x/y/pull/7", process.stderr)
        args = (self.home / "gh.args").read_text().splitlines()
        self.assertEqual(args[:3], ["pr", "create", "--draft"])
        self.assertEqual(args[args.index("--base") + 1], "main")
        self.assertIn("A person still has to review it before merging.", "\n".join(args))
        self.assertNotIn("merge", [a for a in args if a.startswith("--")])
        branch = end["area"]["branch"]
        self.assertEqual(subprocess.run(["/usr/bin/git", "-C", str(origin), "rev-parse", "--verify", branch],
                                        capture_output=True, env=self.env).returncode, 0)

    def test_untracked_files_go_back_to_the_developer_and_never_reach_a_push(self):
        origin = self.home / "origin.git"
        subprocess.run(["/usr/bin/git", "init", "-q", "--bare", str(origin)], check=True, env=self.env)
        self.git("remote", "add", "origin", str(origin))
        process, end = self.loop("--until", "pr", FAKE_LOOP="leaves_secret")
        self.assertEqual(end["status"], "approved", process.stderr)
        asked = [c for c in self.calls("dev") if "not tracked by git" in c["request"]]
        self.assertEqual(len(asked), 1)
        self.assertIn(".env", asked[0]["request"])
        self.assertIn("left out of every commit:\n- .env", self.calls("reviewer")[-1]["request"])
        pushed = subprocess.run(["/usr/bin/git", "-C", str(origin), "ls-tree", "-r", "--name-only", end["area"]["branch"]],
                                check=True, capture_output=True, text=True, env=self.env).stdout.split()
        self.assertNotIn(".env", pushed)
        self.assertTrue(any(name.startswith("test_add_") for name in pushed))
        self.assertTrue((Path(end["area"]["tree"]) / ".env").exists())  # Left in place, never committed.

    def test_an_approval_does_not_count_when_the_reviewer_changed_the_branch(self):
        for script, change in (("reviewer_edits", "tracked files changed: add.py"), ("reviewer_commits", "HEAD moved")):
            with self.subTest(script=script):
                process, end = self.loop("--until", "pr", FAKE_LOOP=script)
                self.assertEqual((process.returncode, end["status"]), (1, "reviewer_edited"), process.stderr)
                self.assertIn(change, end["reason"])
                self.assertIn("does not count", end["reason"])
                self.assertNotIn("pr", end)
                self.assertFalse((self.home / "gh.args").exists())

    def test_no_automatic_merge_and_no_bypass(self):
        merge = self.run_script("run", "x", "--repo", str(self.repo), "--until", "merge")
        self.assertEqual(merge.returncode, 2)
        self.assertIn("invalid choice", merge.stderr)
        bypass = self.run_script("run", "x", "--repo", str(self.repo), "--permission", "bypassPermissions")
        self.assertEqual(bypass.returncode, 2)
        self.assertEqual(self.calls(), [])

    def test_refusals_leave_no_loop_behind(self):
        same = self.run_script("run", "x", "--repo", str(self.repo), "--dev", "duo", "--reviewer", "duo", "--json")
        self.assertIn("different accounts", json.loads(same.stdout)["error"])
        outside = self.run_script("run", "x", "--repo", str(self.home), "--json")
        self.assertIn("is not a git repository", json.loads(outside.stdout)["error"])
        (self.repo / "dirty.txt").write_text("x")
        dirty = self.run_script("run", "x", "--repo", str(self.repo), "--no-worktree", "--json")
        self.assertIn("uncommitted changes", json.loads(dirty.stdout)["error"])
        (self.home / ".claude-duo").rmdir()
        missing = self.run_script("run", "x", "--repo", str(self.repo), "--json")
        self.assertIn("has no Code folder yet", json.loads(missing.stdout)["error"])
        loops = self.home / ".local/share/claude-duo/loops"
        self.assertEqual(list(loops.iterdir()) if loops.exists() else [], [])
        self.assertEqual(self.calls(), [])

    def test_a_signed_out_cli_stops_the_loop_before_it_starts(self):
        for account, command in (("duo", f"CLAUDE_CONFIG_DIR={self.home / '.claude-duo'} claude auth login"),
                                 ("original", "run  claude auth login")):
            with self.subTest(account=account):
                process, end = self.loop(FAKE_AUTH_OUT=account)
                self.assertEqual(process.returncode, 1)
                self.assertIn(f"the {account} account's Claude Code CLI is not signed in", end["error"])
                self.assertIn(command, end["error"])
        loops = self.home / ".local/share/claude-duo/loops"
        self.assertEqual(list(loops.iterdir()) if loops.exists() else [], [])
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.git("branch", "--list", "loop-duo/*"), "")

    def test_list_shows_what_ran(self):
        self.loop()
        listed = json.loads(self.run_script("list", "--json").stdout)["loops"]
        self.assertEqual(len(listed), 1)
        self.assertEqual((listed[0]["status"], listed[0]["dev"], listed[0]["reviewer"]), ("approved", "original", "duo"))
        self.assertTrue(listed[0]["branch"].startswith("loop-duo/"))
        readable = self.run_script("run", "write something else", "--repo", str(self.repo), FAKE_LOOP="blocked")
        self.assertIn("stopped (blocked)", readable.stdout)
        self.assertIn("· original Bash: git status", readable.stdout)
        table = self.run_script("list").stdout
        self.assertIn("blocked", table)
        self.assertIn("approved", table)


if __name__ == "__main__":
    unittest.main()
