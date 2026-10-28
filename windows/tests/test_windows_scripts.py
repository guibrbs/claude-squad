"""The PowerShell scripts parse, and the double-click wrappers point at them."""
import subprocess
import unittest

from helpers import ROOT


class PowerShellScripts(unittest.TestCase):
    def test_scripts_parse_and_carry_a_bom(self):
        for script in sorted((ROOT / "scripts").glob("*.ps1")):
            with self.subTest(script=script.name):
                # Windows PowerShell 5.1 reads a file without BOM as ANSI and garbles the accents.
                self.assertTrue(script.read_bytes().startswith(b"\xef\xbb\xbf"))
                check = ("$errors = $null; [System.Management.Automation.Language.Parser]::ParseFile("
                         f"'{script}', [ref]$null, [ref]$errors) | Out-Null; "
                         "$errors | ForEach-Object { $_.Message }; exit $errors.Count")
                done = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", check],
                                      capture_output=True, text=True)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_double_click_wrappers(self):
        for name, script in (("Install Claude Duo.cmd", "install.ps1"), ("Uninstall Claude Duo.cmd", "uninstall.ps1")):
            with self.subTest(wrapper=name):
                text = (ROOT / name).read_bytes()
                self.assertNotIn(b"\n", text.replace(b"\r\n", b""))  # cmd.exe needs CRLF
                self.assertIn(f"%~dp0scripts\\{script}".encode(), text)


if __name__ == "__main__":
    unittest.main()
