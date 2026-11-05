"""Claude Duo installer/updater/update check in a temporary HOME.

Dialogs, notifications, process lists, quitting and the toolset dispatcher
are mocked; plist handling and the shell logic are real. Nothing touches the
caller's apps, profiles, launch agents or toolset.
"""
import os
from pathlib import Path
import re
import plistlib
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]

OSASCRIPT = '''#!/bin/bash
printf '%s\\n' "$*" >> "$MOCK_DIR/osascript.log"
case "$*" in
 *"display alert"*) printf '%s\\n' "${MOCK_ANSWER:-}" ;;
 *" to quit"*) [ "${MOCK_STUCK:-}" = 1 ] || {
    id="$*"; id="${id#*application id \\\"}"; id="${id%%\\\"*}"
    app="$(awk -v id="$id" '$1 == id { sub(/^[^ ]+ /, ""); print }' "$MOCK_DIR/ids")"
    [ -z "$app" ] || { grep -vF "$app/" "$MOCK_DIR/running" > "$MOCK_DIR/r"; mv "$MOCK_DIR/r" "$MOCK_DIR/running"; }; } ;;
esac
exit 0
'''
# ps -o ppid=,comm= -p PID answers MOCK_PARENT for any PID: one ancestor, then launchd.
PS = '''#!/bin/bash
case "$*" in *ppid=*) printf '%s\\n' "${MOCK_PARENT:-1 /sbin/launchd}" ;;
 *) cat "$MOCK_DIR/running" 2>/dev/null
    # A process that outlives the app's quit for a few ps calls: "<calls left> <pid> <executable>".
    if [ -s "$MOCK_DIR/linger" ]; then
     read -r left pid exe < "$MOCK_DIR/linger"
     if [ "$left" -gt 0 ]; then printf '%5d %s\\n' "$pid" "$exe"; printf '%s %s %s\\n' "$((left - 1))" "$pid" "$exe" > "$MOCK_DIR/linger"; fi
    fi ;;
esac
exit 0
'''
OPEN = '#!/bin/bash\nprintf "%s\\n" "$*" >> "$MOCK_DIR/open.log"\n'
DISPATCHER = '''#!/bin/bash
printf '%s|%s\\n' "$CLAUDE_INSTANCES_CONF" "$*" >> "$MOCK_DIR/dispatcher.log"
# What a lingering process still had left when the build started (see the ps mock).
[ ! -s "$MOCK_DIR/linger" ] || cut -d' ' -f1 "$MOCK_DIR/linger" >> "$MOCK_DIR/linger-at-build"
[ "${MOCK_DISPATCH_FAIL:-}" != 1 ] || { echo "synthetic build failure" >&2; exit 1; }
# A build that publishes the copy named in dispatch-partial, then fails.
[ ! -e "$MOCK_DIR/dispatch-partial" ] || { cp "$MOCK_DIR/partial.plist" "$(cat "$MOCK_DIR/dispatch-partial")"; echo "synthetic failure after one copy" >&2; exit 1; }
[ ! -e "$MOCK_DIR/dispatch-stale" ] || { echo "process 4242 (chrome-native-host) runs a deleted executable; quit it and retry" >&2; exit 1; }
exit 0
'''


class DuoToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="duo-tools-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "home"
        self.mock = self.root / "mock"
        self.mock.mkdir()
        self.language = "en"
        (self.home / "Applications").mkdir(parents=True, mode=0o700)
        self.source_app = self.root / "Applications/Claude.app"
        self.duo_app = self.home / "Applications/Claude Duo.app"
        self.make_app(self.source_app, "2.0.0", "com.anthropic.claudefordesktop")
        self.make_app(self.duo_app, "1.0.0", "io.github.test.claude-duo")
        self.tools = self.home / ".local/share/claude-duo"
        self.tools.mkdir(parents=True)
        self.dispatcher = self.home / ".local/share/claude-instances/toolsets/dispatcher"
        self.dispatcher.parent.mkdir(parents=True)
        self.write_mock(self.dispatcher, DISPATCHER.replace("$MOCK_DIR", str(self.mock))
                        .replace('"${MOCK_DISPATCH_FAIL:-}" != 1', f'! -e "{self.mock}/dispatch-fail"'))
        for name, code in (("osascript", OSASCRIPT), ("ps", PS), ("open", OPEN), ("sleep", "#!/bin/bash\nexit 0\n")):
            self.write_mock(self.mock / name, code)
        self.config = self.home / ".config/claude-instances/claude-duo.conf"
        self.config.parent.mkdir(parents=True)
        self.config.write_text("@mode original-copy\n@apps user\n" + self.copy_line("Duo", 212))
        (self.tools / "config-path").write_text(str(self.config) + "\n")
        for name in ("duo-common.sh", "update-duo.sh", "check-update.sh"):
            self.install_patched(REPO / "duo" / name, self.tools / name)
        self.env = {"HOME": str(self.home), "USER": "tester", "PATH": "/usr/bin:/bin", "MOCK_DIR": str(self.mock)}

    @staticmethod
    def copy_line(name, hue):
        lower = name.lower()
        return (f"{name} | io.github.test.claude-{lower} | ~/Library/Application Support/Claude-{name} | {hue} | "
                f"CLAUDE_CONFIG_DIR=~/.claude-{lower}\n")

    def add_trio(self, version="1.0.0"):
        self.trio_app = self.home / "Applications/Claude Trio.app"
        self.make_app(self.trio_app, version, "io.github.test.claude-trio")
        self.config.write_text(self.config.read_text() + self.copy_line("Trio", 275))

    def builds(self):
        return [line for line in self.log("dispatcher.log").splitlines() if line.endswith("|run claude-apps-refresh.sh")]

    def make_app(self, app, version, identifier):
        (app / "Contents/MacOS").mkdir(parents=True)
        with open(self.mock / "ids", "a") as ids:
            ids.write(f"{identifier} {app}\n")
        (app / "Contents/Info.plist").write_bytes(plistlib.dumps(
            {"CFBundleShortVersionString": version, "CFBundleIdentifier": identifier}))

    @staticmethod
    def write_mock(path, code):
        path.write_text(code)
        path.chmod(0o755)

    def patch(self, code):
        for old, new in (("OSASCRIPT=/usr/bin/osascript", f"OSASCRIPT={self.mock}/osascript"),
                         ("OPEN=/usr/bin/open", f"OPEN={self.mock}/open"),
                         ("PS=/bin/ps", f"PS={self.mock}/ps"),
                         ("SLEEP=/bin/sleep", f"SLEEP={self.mock}/sleep"),
                         ("DUO_QUIT_TIMEOUT=60", "DUO_QUIT_TIMEOUT=3"),
                         ('DUO_SOURCE_APP="/Applications/Claude.app"', f'DUO_SOURCE_APP="{self.source_app}"')):
            code = code.replace(old, new)
        return re.sub(r"^DUO_LANGUAGE=.*$", "DUO_LANGUAGE=" + self.language, code, flags=re.M)

    def install_patched(self, source, target):
        target.write_text(self.patch(source.read_text()))
        target.chmod(0o700)

    def run_tool(self, name, *args):
        return subprocess.run(["/bin/bash", "-p", str(self.tools / name), *args], env=self.env,
                              capture_output=True, text=True, timeout=60)

    def log(self, name):
        path = self.mock / name
        return path.read_text() if path.exists() else ""

    def running(self, *apps):
        self.processes(*(f"{app}/Contents/MacOS/Claude" for app in apps))

    def processes(self, *executables):
        # Same shape as ps -axo pid=,comm=: right-aligned PID, then the executable.
        (self.mock / "running").write_text("".join(f"{pid:5d} {exe}\n" for pid, exe in enumerate(executables, 501)))

    def updater_log(self):
        return "".join(p.read_text() for p in (self.home / "Library/Logs/Claude Duo Updater").glob("update-*.log"))

    # Update check (launch agent)
    def test_check_is_silent_when_versions_match(self):
        info = plistlib.loads((self.duo_app / "Contents/Info.plist").read_bytes())
        info["CFBundleShortVersionString"] = "2.0.0"
        (self.duo_app / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
        self.assertEqual(self.run_tool("check-update.sh").returncode, 0)
        self.assertEqual(self.log("osascript.log"), "")

    def test_check_asks_once_a_day_per_version_and_later_does_nothing(self):
        self.env["MOCK_ANSWER"] = "Later"
        self.assertEqual(self.run_tool("check-update.sh").returncode, 0)
        self.assertIn("2.0.0", self.log("osascript.log"))
        self.assertIn("1.0.0", self.log("osascript.log"))
        self.assertEqual(self.log("dispatcher.log"), "")
        before = self.log("osascript.log")
        self.run_tool("check-update.sh")
        self.assertEqual(self.log("osascript.log"), before)  # Already asked today.

    def test_check_accepting_rebuilds_without_asking_again(self):
        self.env["MOCK_ANSWER"] = "Update now"
        result = self.run_tool("check-update.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log("osascript.log").count("display alert"), 1)
        self.assertEqual(self.log("dispatcher.log").splitlines(),
                         [f"{self.config}|run claude-apps-refresh.sh", f"{self.config}|run claude-apps-refresh.sh --verify"])

    def test_check_does_nothing_when_not_installed(self):
        (self.tools / "config-path").unlink()
        self.assertEqual(self.run_tool("check-update.sh").returncode, 0)
        self.assertEqual(self.log("osascript.log"), "")

    # Updater
    def test_cancel_changes_nothing(self):
        self.running(self.source_app)
        self.env["MOCK_ANSWER"] = "Cancel"
        self.assertEqual(self.run_tool("update-duo.sh").returncode, 0)
        self.assertNotIn(" to quit", self.log("osascript.log"))
        self.assertEqual(self.log("dispatcher.log"), "")

    def test_update_quits_rebuilds_and_reopens_what_was_open(self):
        self.running(self.source_app, self.duo_app)
        self.env["MOCK_ANSWER"] = "Update now"
        result = self.run_tool("update-duo.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log("osascript.log").count(" to quit"), 2)
        self.assertEqual(len(self.builds()), 1)
        self.assertEqual(self.log("open.log").splitlines(), [f"-a {self.source_app}", f"-a {self.duo_app}"])
        self.assertIn("updated to", self.log("osascript.log"))

    def test_update_reopens_only_apps_that_were_open(self):
        self.running(self.source_app)
        self.assertEqual(self.run_tool("update-duo.sh", "--yes").returncode, 0)
        self.assertEqual(self.log("open.log").splitlines(), [f"-a {self.source_app}"])

    def test_apps_that_do_not_quit_abort_before_building(self):
        self.running(self.source_app)
        self.env["MOCK_STUCK"] = "1"
        result = self.run_tool("update-duo.sh", "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.log("dispatcher.log"), "")
        self.assertIn("Could not quit", self.log("osascript.log"))
        self.assertIn(f"still running (PID executable):\n  501 {self.source_app}/Contents/MacOS/Claude\n", self.updater_log())

    def test_build_failure_is_reported_and_claude_reopened(self):
        self.running(self.source_app)
        (self.mock / "dispatch-fail").write_text("1")
        result = self.run_tool("update-duo.sh", "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("update failed", self.log("osascript.log"))
        self.assertEqual(self.log("open.log").splitlines(), [f"-a {self.source_app}"])
        self.assertIn("synthetic build failure", self.updater_log())

    def test_process_running_a_deleted_executable_is_named_in_the_notification(self):
        (self.mock / "dispatch-stale").write_text("1")
        result = self.run_tool("update-duo.sh", "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("chrome-native-host (PID 4242) still runs a file from the previous Claude", self.log("osascript.log"))
        self.assertNotIn("update failed", self.log("osascript.log"))

    def test_bundle_helpers_started_by_other_programs_do_not_block_the_update(self):
        # Chrome starts chrome-native-host and the claude CLI starts disclaimer;
        # both run from Contents/Helpers and survive quitting the app.
        self.processes(f"{self.source_app}/Contents/Helpers/chrome-native-host",
                       f"{self.source_app}/Contents/Helpers/disclaimer")
        self.env["MOCK_STUCK"] = "1"
        result = self.run_tool("update-duo.sh", "--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.builds()), 1)
        self.assertNotIn(" to quit", self.log("osascript.log"))
        self.assertEqual(self.log("open.log"), "")

    def test_electron_helpers_still_count_as_running(self):
        helper = "Claude Helper (Renderer)"
        self.processes(f"{self.source_app}/Contents/Frameworks/{helper}.app/Contents/MacOS/{helper}")
        self.env["MOCK_STUCK"] = "1"
        result = self.run_tool("update-duo.sh", "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.log("dispatcher.log"), "")

    def crashpad(self, app):
        return f"{app}/Contents/Frameworks/Electron Framework.framework/Helpers/chrome_crashpad_handler"

    def test_update_waits_for_duo_executables_that_outlive_its_quit(self):
        # Electron's crash handler detaches and lives on a few seconds; the
        # builder refuses while anything from the Duo's bundle runs.
        self.running(self.source_app, self.duo_app)
        (self.mock / "linger").write_text(f"8 777 {self.crashpad(self.duo_app)}\n")
        result = self.run_tool("update-duo.sh", "--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.builds()), 1)
        # Every build step started only once the crash handler was gone.
        self.assertEqual(set(self.log("linger-at-build").split()), {"0"})

    def test_a_duo_executable_that_never_exits_is_named_and_nothing_is_built(self):
        self.running(self.duo_app)
        (self.mock / "linger").write_text(f"100000 777 {self.crashpad(self.duo_app)}\n")
        result = self.run_tool("update-duo.sh", "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.log("dispatcher.log"), "")
        self.assertIn(f"  777 {self.crashpad(self.duo_app)}\n", self.updater_log())

    def test_an_executable_of_another_copy_that_never_exits_also_blocks_the_build(self):
        self.add_trio()
        (self.mock / "linger").write_text(f"100000 778 {self.crashpad(self.trio_app)}\n")
        result = self.run_tool("update-duo.sh", "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.log("dispatcher.log"), "")
        self.assertIn(f"  778 {self.crashpad(self.trio_app)}\n", self.updater_log())

    def test_leftovers_of_the_original_app_still_do_not_block(self):
        self.processes(self.crashpad(self.source_app))
        self.env["MOCK_STUCK"] = "1"
        result = self.run_tool("update-duo.sh", "--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log("open.log"), "")

    def test_a_lingering_duo_helper_does_not_count_as_the_duo_being_open(self):
        (self.mock / "linger").write_text(f"3 777 {self.crashpad(self.duo_app)}\n")
        result = self.run_tool("update-duo.sh", "--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log("open.log"), "")  # Not reopened: it was not open.

    def test_config_pointer_outside_the_config_folder_is_refused(self):
        outside = self.root / "evil.conf"
        outside.write_text("@mode original-copy\n")
        (self.tools / "config-path").write_text(str(outside) + "\n")
        self.assertNotEqual(self.run_tool("update-duo.sh", "--yes").returncode, 0)
        self.assertEqual(self.log("dispatcher.log"), "")

    def test_dialog_text_cannot_break_out_of_applescript_strings(self):
        self.env["MOCK_ANSWER"] = "Later"
        info = plistlib.loads((self.source_app / "Contents/Info.plist").read_bytes())
        info["CFBundleShortVersionString"] = '2" & (do shell script "touch PWNED") & "'
        (self.source_app / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
        self.run_tool("check-update.sh")
        self.assertIn('2\\" & (do shell script \\"touch PWNED\\") & \\"', self.log("osascript.log"))

    def test_dialogs_follow_a_portuguese_macos_language(self):
        self.language = "pt-BR"
        for name in ("duo-common.sh", "update-duo.sh", "check-update.sh"):
            self.install_patched(REPO / "duo" / name, self.tools / name)
        self.env["MOCK_ANSWER"] = "Atualizar agora"  # The Portuguese button label.
        result = self.run_tool("check-update.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        log = self.log("osascript.log")
        self.assertIn("O Claude foi atualizado para 2.0.0", log)
        self.assertIn('"Depois"', log)
        self.assertEqual(len(self.builds()), 1)
        self.assertIn("Claude Duo atualizado para", self.log("osascript.log"))

    def test_installer_removes_the_updaters_of_earlier_releases(self):
        installer = self.installer_fixture()
        legacy = [self.home / "Applications/Atualizar Claude Duo.app", self.home / "Applications/Update Claude Duo.app"]
        for app in legacy:
            (app / "Contents").mkdir(parents=True)
        self.tools.mkdir(parents=True)
        for name in ("atualizar-duo.sh", "verificar-atualizacao.sh"):
            (self.tools / name).write_text("old\n")
        result = self.run_installer(installer, "--no-build", "--no-auto-check")
        self.assertEqual(result.returncode, 0, result.stderr)
        for app in legacy:
            self.assertFalse(app.exists(), app)
        self.assertFalse((self.tools / "atualizar-duo.sh").exists())
        self.assertFalse((self.tools / "verificar-atualizacao.sh").exists())
        self.assertTrue((self.home / "Applications/Update Claude Squad.app").exists())

    # Installer
    def installer_fixture(self):
        checkout = self.root / "checkout"
        (checkout / "duo").mkdir(parents=True)
        (checkout / "scripts").mkdir()
        shutil.copyfile(REPO / "scripts/share-code-profile.py", checkout / "scripts/share-code-profile.py")
        for name in ("duo-send.py", "loop-duo.py"):
            shutil.copyfile(REPO / "duo" / name, checkout / "duo" / name)
        for skill in ("duo-send", "duo-inbox"):
            (checkout / "duo/skills" / skill).mkdir(parents=True)
            shutil.copyfile(REPO / "duo/skills" / skill / "SKILL.md", checkout / "duo/skills" / skill / "SKILL.md")
        for name in ("duo-common.sh", "update-duo.sh", "check-update.sh"):
            self.install_patched(REPO / "duo" / name, checkout / "duo" / name)
        code = self.patch((REPO / "duo/install-duo.sh").read_text())
        code = code.replace("LAUNCHCTL=/bin/launchctl", f"LAUNCHCTL={self.mock}/launchctl")
        code = code.replace('/usr/bin/python3 -I "$REPO/scripts/prepare-toolset.py" "$work/package"', f"{self.mock}/prepare")
        code = code.replace('/bin/bash -p "$REPO/bin/claude-toolset.sh" install', f"{self.mock}/toolset install")
        (checkout / "duo/install-duo.sh").write_text(code)
        self.write_mock(self.mock / "launchctl", '#!/bin/bash\nprintf "%s\\n" "$*" >> "$MOCK_DIR/launchctl.log"\n')
        self.write_mock(self.mock / "prepare", '#!/bin/bash\necho ' + "a" * 64 + '\n')
        self.write_mock(self.mock / "toolset", f'#!/bin/bash\nprintf "%s\\n" "$*" >> "{self.mock}/toolset.log"\n')
        shutil.rmtree(self.tools)
        self.config.unlink()
        return checkout / "duo/install-duo.sh"

    def run_installer(self, installer, *args):
        return subprocess.run(["/bin/bash", "-p", str(installer), *args], env=self.env,
                              capture_output=True, text=True, timeout=120)

    def test_installer_sets_up_config_tools_app_and_agent(self):
        installer = self.installer_fixture()
        result = self.run_installer(installer, "--no-build")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        config = self.home / ".config/claude-instances/claude-duo.conf"
        self.assertEqual(config.read_text().splitlines()[:2], ["@mode original-copy", "@apps user"])
        self.assertIn("Duo | io.github.tester.claude-duo |", config.read_text())
        self.assertEqual(config.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.tools / "config-path").read_text().strip(), str(config))
        for name in ("duo-common.sh", "update-duo.sh", "check-update.sh", "share-code-profile.py"):
            self.assertEqual((self.tools / name).stat().st_mode & 0o777, 0o700, name)
        self.assertIn("install", self.log("toolset.log"))
        applet = self.home / "Applications/Update Claude Squad.app"
        self.assertTrue((applet / "Contents/Info.plist").exists())
        agent = self.home / "Library/LaunchAgents/io.github.guibrbs.claude-duo.update-check.plist"
        data = plistlib.loads(agent.read_bytes())
        self.assertEqual(data["ProgramArguments"], ["/bin/bash", "-p", str(self.tools / "check-update.sh")])
        self.assertEqual(data["StartInterval"], 3600)
        self.assertIn("bootstrap", self.log("launchctl.log"))
        self.assertEqual(len(self.builds()), 0)  # --no-build

    def test_installer_rerun_keeps_an_existing_config_and_can_skip_the_agent(self):
        installer = self.installer_fixture()
        self.assertEqual(self.run_installer(installer, "--no-build", "--no-auto-check").returncode, 0)
        config = self.home / ".config/claude-instances/claude-duo.conf"
        config.write_text(config.read_text().replace("| 212 |", "| 100 |"))
        again = self.run_installer(installer, "--no-build", "--no-auto-check")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("| 100 |", config.read_text())
        self.assertFalse((self.home / "Library/LaunchAgents").exists())
        self.assertEqual(self.log("launchctl.log"), "")

    def test_installer_links_adds_one_links_line_after_apps(self):
        installer = self.installer_fixture()
        self.assertEqual(self.run_installer(installer, "--no-build", "--no-auto-check").returncode, 0)
        config = self.home / ".config/claude-instances/claude-duo.conf"
        config.write_text(config.read_text().replace("@apps user\n", "@apps user\n@links none\n"))
        for _ in range(2):  # Running it again changes nothing.
            result = self.run_installer(installer, "--no-build", "--no-auto-check", "--links")
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = config.read_text().splitlines()
            self.assertEqual(lines[:3], ["@mode original-copy", "@apps user", "@links claude-dev"])
            self.assertEqual(sum(line.startswith("@links") for line in lines), 1)
            self.assertIn("Duo | io.github.tester.claude-duo |", lines[3])
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
        self.assertFalse(config.with_name(config.name + ".links.tmp").exists())

    def test_installer_gives_the_original_claude_the_duo_send_skill_and_script(self):
        installer = self.installer_fixture()
        (self.home / ".claude").mkdir()
        result = self.run_installer(installer, "--no-build", "--no-auto-check")
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in ("duo-send.py", "loop-duo.py"):
            self.assertEqual((self.tools / name).read_bytes(), (REPO / "duo" / name).read_bytes())
            self.assertEqual((self.tools / name).stat().st_mode & 0o777, 0o700, name)
        skill = self.home / ".claude/skills/duo-send/SKILL.md"
        self.assertEqual(skill.read_bytes(), (REPO / "duo/skills/duo-send/SKILL.md").read_bytes())
        self.assertIn("~/.local/share/claude-duo/duo-send.py", skill.read_text())
        # Running it again refreshes the copy it installed.
        skill.write_text(skill.read_text().replace("Always run", "Older text. Always run"))
        self.assertEqual(self.run_installer(installer, "--no-build", "--no-auto-check").returncode, 0)
        self.assertEqual(skill.read_bytes(), (REPO / "duo/skills/duo-send/SKILL.md").read_bytes())

    def test_installer_gives_the_duo_the_duo_inbox_skill(self):
        installer = self.installer_fixture()
        result = self.run_installer(installer, "--no-build", "--no-auto-check")
        self.assertIn("Skipped duo-inbox", result.stdout)  # The Duo never opened Code yet.
        (self.home / ".claude-duo").mkdir()
        result = self.run_installer(installer, "--no-build", "--no-auto-check")
        self.assertEqual(result.returncode, 0, result.stderr)
        inbox = self.home / ".claude-duo/skills/duo-inbox/SKILL.md"
        self.assertEqual(inbox.read_bytes(), (REPO / "duo/skills/duo-inbox/SKILL.md").read_bytes())
        self.assertFalse((self.home / ".claude/skills/duo-inbox").exists())

    def test_installer_gives_every_copy_the_duo_inbox_skill(self):
        installer = self.installer_fixture()
        (self.home / ".claude-duo").mkdir()
        (self.home / ".claude-trio").mkdir()
        result = self.run_installer(installer, "--no-build", "--no-auto-check", "--add", "Trio")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for code in (".claude-duo", ".claude-trio"):
            inbox = self.home / code / "skills/duo-inbox/SKILL.md"
            self.assertEqual(inbox.read_bytes(), (REPO / "duo/skills/duo-inbox/SKILL.md").read_bytes(), code)
        self.assertIn("a Claude Trio session", result.stdout)

    def test_installer_follows_the_duo_code_folder_of_the_config(self):
        installer = self.installer_fixture()
        self.assertEqual(self.run_installer(installer, "--no-build", "--no-auto-check").returncode, 0)
        config = self.home / ".config/claude-instances/claude-duo.conf"
        config.write_text(config.read_text().replace("CLAUDE_CONFIG_DIR=~/.claude-duo", "CLAUDE_CONFIG_DIR=~/code-duo"))
        (self.home / "code-duo").mkdir()
        self.assertEqual(self.run_installer(installer, "--no-build", "--no-auto-check").returncode, 0)
        self.assertTrue((self.home / "code-duo/skills/duo-inbox/SKILL.md").exists())

    def test_installer_keeps_a_skill_of_the_user_and_honors_no_skill(self):
        installer = self.installer_fixture()
        skills = self.home / ".claude/skills"
        own = skills / "duo-send"
        own.mkdir(parents=True)
        (own / "SKILL.md").write_text("my own duo-send\n")
        result = self.run_installer(installer, "--no-build", "--no-auto-check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("not installed by Claude Squad", result.stdout)
        self.assertEqual((own / "SKILL.md").read_text(), "my own duo-send\n")
        (own / "SKILL.md").unlink()
        own.rmdir()
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        own.symlink_to(elsewhere, target_is_directory=True)
        self.assertEqual(self.run_installer(installer, "--no-build", "--no-auto-check").returncode, 0)
        self.assertEqual(list(elsewhere.iterdir()), [])
        own.unlink()
        self.assertEqual(self.run_installer(installer, "--no-build", "--no-auto-check", "--no-skill").returncode, 0)
        self.assertFalse(own.exists())

    def test_installer_skips_the_skill_without_a_code_folder(self):
        installer = self.installer_fixture()
        result = self.run_installer(installer, "--no-build", "--no-auto-check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"Skipped duo-send: {self.home / '.claude'} does not exist yet", result.stdout)
        self.assertFalse((self.home / ".claude").exists())

    def test_installer_reuses_an_earlier_manual_duo_config(self):
        installer = self.installer_fixture()
        manual = self.home / ".config/claude-instances/duo-validation.conf"
        manual.write_text("@mode original-copy\n@apps user\nDuo | com.example.duo | ~/x | 212 |\n")
        self.assertEqual(self.run_installer(installer, "--no-build", "--no-auto-check").returncode, 0)
        self.assertEqual((self.tools / "config-path").read_text().strip(), str(manual))
        self.assertFalse((self.home / ".config/claude-instances/claude-duo.conf").exists())

    def test_installer_inside_claude_hands_the_build_to_the_updater_app(self):
        # Run from Claude's Code tab: building would quit the caller's own app.
        installer = self.installer_fixture()
        self.env["MOCK_PARENT"] = f"1 {self.source_app}/Contents/MacOS/Claude"
        result = self.run_installer(installer, "--no-auto-check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.builds()), 0)
        self.assertEqual(self.log("open.log").splitlines(), [f"-a {self.home}/Applications/Update Claude Squad.app"])
        self.assertIn("runs inside Claude", result.stdout)
        self.assertIn('choose "Update now"', result.stdout)

    def test_installer_from_a_terminal_claude_cli_builds_now(self):
        # The CLI only puts Contents/Helpers/disclaimer in the chain.
        installer = self.installer_fixture()
        self.env["MOCK_PARENT"] = f"1 {self.source_app}/Contents/Helpers/disclaimer"
        self.env["MOCK_ANSWER"] = "Update now"
        result = self.run_installer(installer, "--no-auto-check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.builds()), 1)
        self.assertNotIn("Update Claude Squad.app", self.log("open.log"))

    # Several copies (Duo, Trio)
    def test_update_with_several_copies_quits_builds_and_reopens_all(self):
        self.add_trio()
        self.running(self.source_app, self.duo_app, self.trio_app)
        result = self.run_tool("update-duo.sh", "--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log("osascript.log").count(" to quit"), 3)
        self.assertEqual(len(self.builds()), 1)  # One build covers every copy.
        self.assertEqual(self.log("open.log").splitlines(),
                         [f"-a {self.source_app}", f"-a {self.duo_app}", f"-a {self.trio_app}"])
        self.assertIn("Claude Duo and Claude Trio updated to", self.log("osascript.log"))
        self.assertIn("source 2.0.0 duo 1.0.0 trio 1.0.0", self.updater_log())

    def partial_build(self, app):
        shutil.copyfile(self.source_app / "Contents/Info.plist", self.mock / "partial.plist")
        (self.mock / "dispatch-partial").write_text(str(app / "Contents/Info.plist"))

    def test_failure_after_one_copy_names_the_updated_and_the_pending_copy(self):
        self.add_trio()
        self.partial_build(self.trio_app)
        result = self.run_tool("update-duo.sh", "--yes")
        self.assertNotEqual(result.returncode, 0)
        notices = self.log("osascript.log")
        self.assertIn("Claude Trio updated to 2.0.0; Claude Duo kept the previous version. Run the update again.", notices)
        self.assertNotIn("previous copies were kept", notices)

    def test_failure_after_the_last_copy_says_the_copies_are_updated(self):
        # The copy left behind by an earlier failed run is already current.
        self.add_trio("2.0.0")
        self.partial_build(self.duo_app)
        self.assertNotEqual(self.run_tool("update-duo.sh", "--yes").returncode, 0)
        self.assertIn("Claude Duo updated to 2.0.0, but the update stopped before its last check.", self.log("osascript.log"))

    def test_copy_that_lost_its_bundle_is_not_reported_as_updated(self):
        self.add_trio()
        (self.mock / "partial.plist").write_text("")
        (self.mock / "dispatch-partial").write_text(str(self.trio_app / "Contents/Info.plist"))
        self.assertNotEqual(self.run_tool("update-duo.sh", "--yes").returncode, 0)
        notices = self.log("osascript.log")
        self.assertIn("previous copies were kept", notices)
        self.assertNotIn("updated to", notices)

    def test_partial_failure_in_portuguese_agrees_in_number(self):
        self.language = "pt-BR"
        for name in ("duo-common.sh", "update-duo.sh"):
            self.install_patched(REPO / "duo" / name, self.tools / name)
        self.add_trio()
        self.partial_build(self.trio_app)
        self.assertNotEqual(self.run_tool("update-duo.sh", "--yes").returncode, 0)
        self.assertIn("o Claude Trio foi atualizado para 2.0.0; o Claude Duo ficou na versão anterior.", self.log("osascript.log"))

    def test_update_logs_a_copy_that_is_not_built_yet_as_none(self):
        self.add_trio()
        shutil.rmtree(self.trio_app)
        self.assertEqual(self.run_tool("update-duo.sh", "--yes").returncode, 0)
        self.assertIn("source 2.0.0 duo 1.0.0 trio none\n", self.updater_log())

    def test_update_waits_for_every_copy_to_quit(self):
        self.add_trio()
        self.processes(f"{self.trio_app}/Contents/MacOS/Claude")
        (self.mock / "ids").write_text("")  # No app leaves when asked to quit.
        result = self.run_tool("update-duo.sh", "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.builds(), [])
        self.assertIn(f"{self.trio_app}/Contents/MacOS/Claude", self.updater_log())

    def test_update_relinks_shared_code_files_of_each_copy_that_shares_them(self):
        self.add_trio()
        self.write_mock(self.tools / "share-code-profile.py",
                        f"import sys\nopen({str(self.mock / 'share.log')!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n")
        (self.home / ".claude-trio").mkdir()
        (self.home / ".claude-trio/.shared-from-original.json").write_text("{}")
        self.assertEqual(self.run_tool("update-duo.sh", "--yes").returncode, 0)
        self.assertEqual(self.log("share.log").splitlines(),
                         [f"--apply --destination {self.home}/.claude-trio --app {self.trio_app}"])

    def test_check_asks_when_only_one_copy_is_behind_and_names_it(self):
        self.add_trio("1.5.0")
        info = plistlib.loads((self.duo_app / "Contents/Info.plist").read_bytes())
        info["CFBundleShortVersionString"] = "2.0.0"
        (self.duo_app / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
        self.env["MOCK_ANSWER"] = "Later"
        self.assertEqual(self.run_tool("check-update.sh").returncode, 0)
        alert = self.log("osascript.log")
        self.assertIn("Claude Trio is on 1.5.0", alert)
        self.assertNotIn("Claude Duo is on", alert)

    def test_check_ignores_a_copy_that_is_not_built_yet(self):
        self.add_trio()
        shutil.rmtree(self.trio_app)
        info = plistlib.loads((self.duo_app / "Contents/Info.plist").read_bytes())
        info["CFBundleShortVersionString"] = "2.0.0"
        (self.duo_app / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
        self.assertEqual(self.run_tool("check-update.sh").returncode, 0)
        self.assertEqual(self.log("osascript.log"), "")

    def test_installer_add_appends_a_copy_once_with_its_own_folders_and_color(self):
        installer = self.installer_fixture()
        for _ in range(2):  # Running it again adds nothing.
            result = self.run_installer(installer, "--no-build", "--no-auto-check", "--add", "Trio")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lines = (self.home / ".config/claude-instances/claude-duo.conf").read_text().splitlines()
        self.assertEqual([line.split(" | ")[0] for line in lines[2:]], ["Duo", "Trio"])
        self.assertEqual(lines[3], "Trio | io.github.tester.claude-trio | ~/Library/Application Support/Claude-Trio"
                                   " | 275 | CLAUDE_CONFIG_DIR=~/.claude-trio")
        self.assertIn("already in the config", result.stdout)
        self.assertIn("CLAUDE_CONFIG_DIR=~/.claude-trio claude auth login", result.stdout)
        self.assertIn("Claude Trio", result.stdout)

    def test_installer_add_names_the_duo_in_an_existing_links_line(self):
        installer = self.installer_fixture()
        self.assertEqual(self.run_installer(installer, "--no-build", "--no-auto-check", "--links").returncode, 0)
        config = self.home / ".config/claude-instances/claude-duo.conf"
        result = self.run_installer(installer, "--no-build", "--no-auto-check", "--add", "Trio")
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = config.read_text().splitlines()
        self.assertEqual(lines[:3], ["@mode original-copy", "@apps user", "@links claude-dev Duo"])
        self.assertEqual(sum(line.startswith("@links") for line in lines), 1)
        self.assertEqual(config.stat().st_mode & 0o777, 0o600)
        # --links with several copies keeps the one already named.
        self.assertEqual(self.run_installer(installer, "--no-build", "--no-auto-check", "--links").returncode, 0)
        self.assertEqual(config.read_text().splitlines()[2], "@links claude-dev Duo")

    def test_installer_add_refuses_unusable_names(self):
        installer = self.installer_fixture()
        for name in ("original", "Original", "3rd", "two words", "a/b", "$(touch x)"):
            with self.subTest(name=name):
                result = self.run_installer(installer, "--no-build", "--no-auto-check", "--add", name)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.tools.exists())
        self.assertNotEqual(self.run_installer(installer, "--add").returncode, 0)

    def test_installer_builds_when_any_copy_is_missing_or_behind(self):
        installer = self.installer_fixture()
        info = plistlib.loads((self.duo_app / "Contents/Info.plist").read_bytes())
        info["CFBundleShortVersionString"] = "2.0.0"
        (self.duo_app / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
        self.env["MOCK_ANSWER"] = "Update now"
        result = self.run_installer(installer, "--no-auto-check", "--no-skill", "--add", "Trio")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.builds()), 1)  # The Duo was current; the Trio did not exist.

    # Uninstaller
    def uninstall_fixture(self):
        installer = self.installer_fixture()
        self.assertEqual(self.run_installer(installer, "--no-build").returncode, 0)
        code = self.patch((REPO / "duo/uninstall-duo.sh").read_text())
        uninstaller = installer.parent / "uninstall-duo.sh"
        uninstaller.write_text(code.replace("LAUNCHCTL=/bin/launchctl", f"LAUNCHCTL={self.mock}/launchctl"))
        (self.home / "Library/Logs/Claude Duo Updater").mkdir(parents=True)
        (self.home / "Library/Logs/Claude Duo Updater/update-1.log").write_text("OK\n")
        (self.home / "Applications/.claude-txn.ABCDEFGH").mkdir()
        self.account = [self.home / "Library/Application Support/Claude-Duo", self.home / ".claude-duo"]
        for path in self.account:
            path.mkdir(parents=True)
            (path / "invented-account-data").write_text("keep")
        return uninstaller

    def uninstall(self, uninstaller, *args):
        return subprocess.run(["/bin/bash", "-p", str(uninstaller), *args], env=self.env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=60)

    def trashed(self):
        folders = list((self.home / ".Trash").glob("Claude Squad uninstalled *"))
        self.assertEqual(len(folders), 1)
        return folders[0]

    def test_uninstaller_moves_the_duo_to_the_trash_and_keeps_account_data(self):
        uninstaller = self.uninstall_fixture()
        for earlier in ("Atualizar Claude Duo.app", "Update Claude Duo.app"):  # From earlier releases.
            (self.home / "Applications" / earlier / "Contents").mkdir(parents=True)
        result = self.uninstall(uninstaller, "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        moved = ("Applications/Claude Duo.app", "Applications/Update Claude Squad.app",
                 "Applications/Update Claude Duo.app", "Applications/Atualizar Claude Duo.app",
                 "Library/LaunchAgents/io.github.guibrbs.claude-duo.update-check.plist", ".local/share/claude-duo",
                 ".config/claude-instances/claude-duo.conf", "Library/Logs/Claude Duo Updater",
                 ".local/share/claude-instances", "Applications/.claude-txn.ABCDEFGH")
        for relative in moved:
            self.assertFalse((self.home / relative).exists(), relative)
            self.assertTrue((self.trashed() / relative).exists(), relative)
        self.assertFalse((self.home / ".config/claude-instances").exists())
        for path in self.account:
            self.assertEqual((path / "invented-account-data").read_text(), "keep")
        self.assertIn("bootout", self.log("launchctl.log"))

    def test_uninstaller_moves_the_installed_skill_but_not_one_of_the_user(self):
        (self.home / ".claude").mkdir()
        uninstaller = self.uninstall_fixture()
        ours = self.home / ".claude/skills/duo-send"
        theirs = self.home / ".claude/skills/other"
        theirs.mkdir()
        (theirs / "SKILL.md").write_text("mine\n")
        self.assertTrue((ours / "SKILL.md").exists())
        result = self.uninstall(uninstaller, "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(ours.exists())
        self.assertTrue((self.trashed() / ".claude/skills/duo-send/SKILL.md").exists())
        self.assertEqual((theirs / "SKILL.md").read_text(), "mine\n")

    def test_uninstaller_moves_the_duo_inbox_skill_once_when_skills_are_shared(self):
        (self.home / ".claude").mkdir()
        (self.home / ".claude/skills").mkdir()
        uninstaller = self.uninstall_fixture()  # creates ~/.claude-duo (account data)
        shared = self.home / ".claude-duo/skills"
        shared.symlink_to(self.home / ".claude/skills", target_is_directory=True)
        self.assertEqual(self.run_installer(self.root / "checkout/duo/install-duo.sh", "--no-build").returncode, 0)
        self.assertTrue((self.home / ".claude/skills/duo-inbox/SKILL.md").exists())  # through the link
        result = self.uninstall(uninstaller, "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for name in ("duo-send", "duo-inbox"):
            self.assertFalse((self.home / ".claude/skills" / name).exists(), name)
            self.assertTrue((self.trashed() / ".claude/skills" / name / "SKILL.md").exists(), name)

    def test_uninstaller_keeps_a_duo_send_skill_it_did_not_install(self):
        uninstaller = self.uninstall_fixture()
        own = self.home / ".claude/skills/duo-send"
        own.mkdir(parents=True)
        (own / "SKILL.md").write_text("my own duo-send\n")
        result = self.uninstall(uninstaller, "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((own / "SKILL.md").read_text(), "my own duo-send\n")

    def test_uninstaller_moves_account_data_only_when_asked(self):
        uninstaller = self.uninstall_fixture()
        result = self.uninstall(uninstaller, "--yes", "--include-account-data")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for path in self.account:
            self.assertFalse(path.exists())
            self.assertTrue((self.trashed() / path.relative_to(self.home) / "invented-account-data").exists())

    def duo_line(self, desktop, env):
        config = self.home / ".config/claude-instances/claude-duo.conf"
        config.write_text(f"@mode original-copy\n@apps user\nDuo | io.github.tester.claude-duo | {desktop} | 212 | {env}\n")

    def test_uninstaller_moves_the_account_folders_the_config_names(self):
        uninstaller = self.uninstall_fixture()
        managed = "Library/Application Support/Multi Claude Desktop/Profiles/0123abcd-4567-89ef-0123-456789abcdef/desktop"
        self.duo_line(f"~/{managed}", "CLAUDE_CONFIG_DIR=~/.claude-work")
        configured = [self.home / managed, self.home / ".claude-work"]
        for path in configured:
            path.mkdir(parents=True)
            (path / "invented-account-data").write_text("duo")
        result = self.uninstall(uninstaller, "--yes", "--include-account-data")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for path in configured:
            self.assertFalse(path.exists())
            self.assertTrue((self.trashed() / path.relative_to(self.home) / "invented-account-data").exists())
        for path in self.account:  # The defaults are not this Duo's data any more.
            self.assertEqual((path / "invented-account-data").read_text(), "keep")

    def test_uninstaller_never_moves_folders_outside_duo_profile_locations(self):
        uninstaller = self.uninstall_fixture()
        self.duo_line("~/Library/Application Support/Claude", "CLAUDE_CONFIG_DIR=~/.claude")
        originals = [self.home / "Library/Application Support/Claude", self.home / ".claude"]
        for path in originals:
            path.mkdir(parents=True)
            (path / "original-account-data").write_text("keep")
        result = self.uninstall(uninstaller, "--yes", "--include-account-data")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for path in originals:
            self.assertEqual((path / "original-account-data").read_text(), "keep")
        self.assertIn("not copy profile locations", result.stdout)

    def test_uninstaller_keeps_the_shared_toolset_while_another_config_uses_it(self):
        uninstaller = self.uninstall_fixture()
        (self.home / ".config/claude-instances/other.conf").write_text("@mode original-copy\n")
        result = self.uninstall(uninstaller, "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.home / ".config/claude-instances/claude-duo.conf").exists())
        for kept in (".local/share/claude-instances", "Applications/.claude-txn.ABCDEFGH", ".config/claude-instances/other.conf"):
            self.assertTrue((self.home / kept).exists(), kept)

    def test_uninstaller_changes_nothing_while_the_duo_runs_or_without_confirmation(self):
        uninstaller = self.uninstall_fixture()
        self.running(self.duo_app)
        self.assertNotEqual(self.uninstall(uninstaller, "--yes").returncode, 0)
        (self.mock / "running").unlink()
        result = self.uninstall(uninstaller)  # no terminal to confirm and no --yes
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--yes", result.stderr)
        self.assertTrue(self.duo_app.exists())
        self.assertFalse((self.home / ".Trash").exists())

    def two_copy_uninstall_fixture(self):
        uninstaller = self.uninstall_fixture()
        config = self.home / ".config/claude-instances/claude-duo.conf"
        config.write_text(config.read_text().replace("@apps user\n", "@apps user\n@links claude-dev Trio\n")
                          + "Trio | io.github.tester.claude-trio | ~/Library/Application Support/Claude-Trio | 275 | "
                            "CLAUDE_CONFIG_DIR=~/.claude-trio\n")
        self.trio_app = self.home / "Applications/Claude Trio.app"
        self.make_app(self.trio_app, "1.0.0", "io.github.tester.claude-trio")
        self.trio_account = [self.home / "Library/Application Support/Claude-Trio", self.home / ".claude-trio"]
        for path in self.trio_account:
            path.mkdir(parents=True)
            (path / "invented-account-data").write_text("trio")
        return uninstaller, config

    def test_uninstaller_removes_every_copy(self):
        uninstaller, _ = self.two_copy_uninstall_fixture()
        result = self.uninstall(uninstaller, "--yes", "--include-account-data")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for path in (self.duo_app, self.trio_app, *self.account, *self.trio_account):
            self.assertFalse(path.exists(), path)
            self.assertTrue((self.trashed() / path.relative_to(self.home)).exists(), path)
        self.assertIn('"Trio Claude Safe Storage"', result.stdout)

    def test_uninstaller_only_removes_one_copy_and_its_line(self):
        uninstaller, config = self.two_copy_uninstall_fixture()
        (self.mock / "launchctl.log").unlink()
        result = self.uninstall(uninstaller, "--yes", "--only", "trio", "--include-account-data")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        folder = list((self.home / ".Trash").glob("Claude Trio uninstalled *"))
        self.assertEqual(len(folder), 1)
        for path in (self.trio_app, *self.trio_account):
            self.assertFalse(path.exists(), path)
            self.assertTrue((folder[0] / path.relative_to(self.home)).exists(), path)
        for kept in (self.duo_app, *self.account, self.tools, self.home / "Applications/Update Claude Squad.app",
                     self.home / ".local/share/claude-instances"):
            self.assertTrue(kept.exists(), kept)
        lines = config.read_text().splitlines()
        self.assertEqual(lines[:2], ["@mode original-copy", "@apps user"])
        self.assertEqual([line.split(" | ")[0] for line in lines[2:]], ["Duo"])  # The @links line named the Trio.
        self.assertEqual(config.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("bootout", self.log("launchctl.log"))

    def test_uninstaller_only_removes_just_that_copy_s_inbox_skill(self):
        uninstaller, _ = self.two_copy_uninstall_fixture()
        skill = (REPO / "duo/skills/duo-inbox/SKILL.md").read_bytes()
        for code, name in ((".claude-duo", "duo-inbox"), (".claude-trio", "duo-inbox"), (".claude", "duo-send")):
            (self.home / code / "skills" / name).mkdir(parents=True)
            (self.home / code / "skills" / name / "SKILL.md").write_bytes(skill)
        result = self.uninstall(uninstaller, "--yes", "--only", "Trio")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.home / ".claude-trio/skills/duo-inbox").exists())
        for kept in (".claude-duo/skills/duo-inbox", ".claude/skills/duo-send"):
            self.assertTrue((self.home / kept / "SKILL.md").exists(), kept)

    def test_uninstaller_only_keeps_links_that_name_another_copy(self):
        uninstaller, config = self.two_copy_uninstall_fixture()
        config.write_text(config.read_text().replace("@links claude-dev Trio", "@links claude-dev Duo"))
        self.assertEqual(self.uninstall(uninstaller, "--yes", "--only", "Trio").returncode, 0)
        self.assertIn("@links claude-dev Duo", config.read_text().splitlines())
        for path in self.trio_account:
            self.assertEqual((path / "invented-account-data").read_text(), "trio")

    def test_uninstaller_only_refuses_the_last_copy_an_unknown_one_or_a_running_one(self):
        uninstaller, config = self.two_copy_uninstall_fixture()
        before = config.read_text()
        self.running(self.trio_app)
        running = self.uninstall(uninstaller, "--yes", "--only", "Trio")
        self.assertNotEqual(running.returncode, 0)
        self.assertIn("quit Claude Trio", running.stderr)
        self.assertNotEqual(self.uninstall(uninstaller, "--yes", "--only", "Quatro").returncode, 0)
        self.assertEqual(config.read_text(), before)
        (self.mock / "running").unlink()
        self.assertEqual(self.uninstall(uninstaller, "--yes", "--only", "Trio").returncode, 0)
        last = self.uninstall(uninstaller, "--yes", "--only", "Duo")
        self.assertNotEqual(last.returncode, 0)
        self.assertIn("only copy", last.stderr)
        self.assertTrue(self.duo_app.exists())

    def test_installer_refuses_without_prerequisites(self):
        installer = self.installer_fixture()
        (self.home / "Applications").chmod(0o755)
        result = self.run_installer(installer, "--no-build", "--no-auto-check")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("chmod 700 ~/Applications", result.stderr)
        (self.home / "Applications").chmod(0o700)
        shutil.rmtree(self.source_app)
        missing = self.run_installer(installer, "--no-build", "--no-auto-check")
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("official Claude", missing.stderr)
        self.assertEqual(self.log("toolset.log"), "")


if __name__ == "__main__":
    unittest.main()
