import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

from helpers import load

share = load("share-code-profile.py", "share_code_profile")


class ShareProfile(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        base = Path(self.folder.name)
        self.source, self.duo = base / ".claude", base / ".claude-duo"
        (self.source / "skills" / "minha-skill").mkdir(parents=True)
        (self.source / "skills" / "minha-skill" / "SKILL.md").write_text("skill\n", encoding="utf-8")
        (self.source / "projects" / "C--repo" / "memory").mkdir(parents=True)
        (self.source / "projects" / "C--repo" / "memory" / "MEMORY.md").write_text("memo\n", encoding="utf-8")
        (self.source / "CLAUDE.md").write_text("# regras\n", encoding="utf-8")
        (self.source / "settings.json").write_text(json.dumps({
            "theme": "dark", "apiKeyHelper": "secret.cmd",
            "env": {"ANTHROPIC_API_KEY": "x", "MY_VAR": "1"}}), encoding="utf-8")
        self.state = base / ".claude.json"
        self.state.write_text(json.dumps({"mcpServers": {"slack": {"url": "https://x"}, "local": {"command": "a"}},
                                          "oauthAccount": {"email": "never@copied"}}), encoding="utf-8")
        self.duo.mkdir()

    def tearDown(self):
        self.folder.cleanup()

    def run_tool(self, *extra, running=False):
        out, err = io.StringIO(), io.StringIO()
        argv = ["--source", str(self.source), "--destination", str(self.duo), "--source-state", str(self.state), *extra]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = share.main(argv, duo_running=lambda: running)
        return code, out.getvalue() + err.getvalue()

    def test_dry_run_changes_nothing(self):
        code, output = self.run_tool()
        self.assertEqual(code, 0)
        self.assertIn("Simulação", output)
        self.assertEqual(list(self.duo.iterdir()), [])

    def test_apply_links_and_filters_settings(self):
        code, output = self.run_tool("--apply", "--mcp-servers")
        self.assertEqual(code, 0, output)
        self.assertTrue(share.is_link(self.duo / "skills"))
        self.assertTrue((self.duo / "skills" / "minha-skill" / "SKILL.md").is_file())
        self.assertTrue(share.is_link(self.duo / "projects" / "C--repo" / "memory"))
        self.assertEqual((self.duo / "CLAUDE.md").read_text(encoding="utf-8"), "# regras\n")
        settings = json.loads((self.duo / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(settings, {"theme": "dark", "env": {"MY_VAR": "1"}})
        state = json.loads((self.duo / ".claude.json").read_text(encoding="utf-8"))
        self.assertEqual(set(state), {"mcpServers"})
        self.assertEqual(set(state["mcpServers"]), {"slack", "local"})
        # The original is untouched.
        self.assertFalse(share.is_link(self.source / "skills"))

    def test_second_run_is_a_no_op_and_keeps_duo_edits(self):
        self.run_tool("--apply")
        (self.duo / "settings.json").write_text(json.dumps({"theme": "light"}), encoding="utf-8")
        code, output = self.run_tool("--apply")
        self.assertEqual(code, 0, output)
        self.assertNotIn("conflict", output)
        self.assertEqual(json.loads((self.duo / "settings.json").read_text(encoding="utf-8")), {"theme": "light"})

    def test_existing_duo_item_needs_backup_flag(self):
        (self.duo / "skills").mkdir()
        (self.duo / "skills" / "only-duo.md").write_text("x", encoding="utf-8")
        code, output = self.run_tool("--apply")
        self.assertEqual(code, 1)
        self.assertIn("--backup-existing", output)
        code, output = self.run_tool("--apply", "--backup-existing")
        self.assertEqual(code, 0, output)
        backups = list(self.duo.glob(".share-backup-*/skills/only-duo.md"))
        self.assertEqual(len(backups), 1)

    def test_duo_running_is_refused(self):
        code, output = self.run_tool("--apply", running=True)
        self.assertEqual(code, 1)
        self.assertIn("aberto", output)

    def test_mcp_server_the_duo_has_is_kept(self):
        (self.duo / ".claude.json").write_text(json.dumps({"mcpServers": {"slack": {"url": "https://duo"}},
                                                           "userID": "duo"}), encoding="utf-8")
        code, output = self.run_tool("--apply", "--mcp-servers")
        self.assertEqual(code, 0, output)
        state = json.loads((self.duo / ".claude.json").read_text(encoding="utf-8"))
        self.assertEqual(state["mcpServers"]["slack"], {"url": "https://duo"})
        self.assertEqual(state["userID"], "duo")
        self.assertIn("local", state["mcpServers"])

    def test_source_link_is_refused(self):
        os.rename(self.source / "skills", self.source.parent / "real-skills")
        share._winapi.CreateJunction(str(self.source.parent / "real-skills"), str(self.source / "skills"))
        code, output = self.run_tool()
        self.assertEqual(code, 1)
        self.assertIn("link", output)


if __name__ == "__main__":
    unittest.main()
