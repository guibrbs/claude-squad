import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from helpers import git, load, make_repo

loop = load("loop-duo.py", "loop_duo")


def block(**data):
    return "text before\n```loop-duo\n" + json.dumps(data) + "\n```\n"


class Protocol(unittest.TestCase):
    def test_last_block_wins(self):
        text = block(state="question", questions=["a?"]) + block(state="review", summary="done")
        self.assertEqual(loop.loop_block(text), {"state": "review", "summary": "done"})

    def test_invalid_blocks(self):
        self.assertIsNone(loop.loop_block("no block"))
        self.assertIsNone(loop.loop_block("```loop-duo\nnot json\n```"))
        self.assertIsNone(loop.loop_block("```loop-duo\n{\"summary\": \"no state\"}\n```"))

    def test_crlf_block(self):
        self.assertEqual(loop.loop_block("```loop-duo\r\n{\"state\": \"approved\"}\r\n```")["state"], "approved")

    def test_questions_accept_strings_and_objects(self):
        found = loop.questions_of({"questions": ["one?", {"question": "two?", "options": ["a", "b"]}, {"x": 1}, " "]})
        self.assertEqual(found, [{"question": "one?", "options": []}, {"question": "two?", "options": ["a", "b"]}])

    def test_slug(self):
        self.assertEqual(loop.slug("Corrigir o cálculo de ICMS!"), "corrigir-o-calculo-de-icms")
        self.assertEqual(loop.slug("???"), "task")


class Accounts(unittest.TestCase):
    def test_environment_drops_other_sessions_account(self):
        inherited = {"CLAUDE_CODE_OAUTH_TOKEN": "x", "ANTHROPIC_API_KEY": "y", "claude_config_dir": "z", "PATH": "p"}
        with mock.patch.dict(os.environ, inherited, clear=True):
            duo = loop.environment({"id": "duo", "folder": Path("C:/u/.claude-duo")})
            original = loop.environment({"id": "original", "folder": Path("C:/u/.claude")})
        self.assertEqual(duo, {"PATH": "p", "CLAUDE_CONFIG_DIR": str(Path("C:/u/.claude-duo"))})
        self.assertEqual(original, {"PATH": "p"})

    def test_duo_folder_comes_from_installed_config(self):
        with mock.patch.object(loop.duo_config, "read_config", return_value={"codeConfigDir": "D:\\contas\\.duo"}):
            self.assertEqual(loop.account("duo")["folder"], Path("D:\\contas\\.duo"))
        self.assertEqual(loop.account("original")["folder"], Path.home() / ".claude")

    def test_login_command_uses_wrapper(self):
        self.assertEqual(loop.login_command({"id": "duo"}), "claude-duo auth login")


class FakeAgents:
    """Scripted turns: the developer commits a file, the reviewer asks once, then approves."""

    def __init__(self, tree_holder):
        self.tree_holder = tree_holder
        self.calls = []

    def __call__(self, acct, session, text, *, folder, role_prompt, reviewer, model, permission, on_tool, extra=()):
        self.calls.append((acct["id"], reviewer, text))
        if not reviewer:
            if "asked" in text:
                answer = block(state="answer", answers=["use UTF-8"])
            else:
                Path(folder, "feature.txt").write_text("feature\n", encoding="utf-8")
                git(folder, "add", "feature.txt")
                git(folder, "commit", "-q", "-m", "feature")
                answer = block(state="review", summary="added feature.txt")
        elif "Answers from the other agent" in text:
            answer = block(state="approved", summary="looks good")
        else:
            answer = block(state="question", questions=["Which encoding?"])
        return {"ok": True, "session": f"session-{acct['id']}-0001", "cost_usd": 0.01, "answer": answer, "error": None}


class RunLoop(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        base = Path(self.folder.name)
        self.repo = make_repo(base / "repo")
        for name in (".claude", ".claude-duo"):
            (base / name).mkdir()
        self.patches = [
            mock.patch.object(loop, "LOOPS", base / "loops"),
            mock.patch.object(loop, "HOME", base),
            mock.patch.object(loop.duo_config, "read_config", return_value={"codeConfigDir": str(base / ".claude-duo")}),
            mock.patch.object(loop, "require_signed_in", lambda acct: None),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        self.folder.cleanup()

    def test_question_goes_to_the_other_agent_and_loop_approves(self):
        agents = FakeAgents(None)
        with mock.patch.object(loop, "converse", agents):
            result = loop.run_loop("Add the feature", folder=self.repo, say=lambda text: None)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status"], "approved")
        self.assertEqual(len(result["commits"]), 1)
        self.assertTrue(result["area"]["branch"].startswith("loop-duo/add-the-feature-"))
        self.assertEqual([c[0] for c in agents.calls], ["original", "duo", "original", "duo"])
        self.assertTrue(Path(result["conversation"]).is_file())
        self.assertEqual(git(self.repo, "rev-parse", "--abbrev-ref", "HEAD"), "main")  # worktree: main untouched

    def test_reviewer_that_edits_stops_the_loop(self):
        def converse(acct, session, text, *, folder, reviewer, **_):
            if reviewer:
                Path(folder, "README.md").write_text("changed by reviewer\n", encoding="utf-8")
                answer = block(state="approved", summary="ok")
            else:
                answer = block(state="review", summary="nothing")
            return {"ok": True, "session": "session-x-0001", "cost_usd": 0, "answer": answer, "error": None}

        with mock.patch.object(loop, "converse", converse):
            result = loop.run_loop("Goal", folder=self.repo, say=lambda text: None)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "reviewer_edited")

    def test_same_account_twice_is_refused(self):
        with self.assertRaises(loop.Refused):
            loop.run_loop("Goal", dev="duo", reviewer="duo", folder=self.repo)

    def test_listing_marks_dead_loops_interrupted(self):
        home = loop.LOOPS / "20261023-101010-abcd"
        home.mkdir(parents=True)
        (home / "loop.json").write_text(json.dumps({"id": "20261023-101010-abcd", "status": "running",
                                                    "pid": 2 ** 31 - 2, "area": {"branch": "b"}}), encoding="utf-8")
        self.assertEqual(loop.recent_loops()[0]["status"], "interrupted")


if __name__ == "__main__":
    unittest.main()
