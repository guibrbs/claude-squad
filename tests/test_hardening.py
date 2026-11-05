"""Regression tests in fixtures, never against real apps/accounts.

Uses macOS Bash/PlistBuddy/plutil; signatures and process observations are mocked.
No change to the caller's HOME, no network, no registration of fixture apps.
"""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import select
import shutil
import struct
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def synthetic_asar(package=None, members=None, block_size=4194304):
    """Small Electron-style archive: compact header, packed members, digests."""
    if package is None:
        package = b'{\n  "name": "@fixture/desktop",\n  "productName": "Claude",\n  "version": "1.0.0",\n  "main": "index.js"\n}\n'
    if members is None:
        members = {"index.js": b"console.log('synthetic vendor code');\n",
                   "node_modules/dep/package.json": b'{"productName": "Claude", "name": "dep"}\n'}
    files, payload = {}, b""

    def entry(data):
        nonlocal payload
        blocks = [sha256(data[i:i + block_size]) for i in range(0, len(data), block_size)]
        result = {"size": len(data), "offset": str(len(payload)),
                  "integrity": {"algorithm": "SHA256", "hash": sha256(data), "blockSize": block_size, "blocks": blocks}}
        payload += data
        return result

    for path, data in members.items():
        node = files
        parts = path.split("/")
        for part in parts[:-1]:
            node = node.setdefault(part, {"files": {}})["files"]
        node[parts[-1]] = entry(data)
    files["package.json"] = entry(package)
    raw = json.dumps({"files": files}, separators=(",", ":")).encode()
    padding = bytes((-len(raw)) % 4)
    size = 8 + len(raw) + len(padding)
    return struct.pack("<4I", 4, size, size - 4, len(raw)) + raw + padding + payload, sha256(raw)


class HardeningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="multi-claude-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.apps = self.root / "apps"
        self.home = self.root / "home"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.home.mkdir()
        (self.home / "Library/Application Support").mkdir(parents=True)
        self.source = self.apps / "Claude.app"
        (self.source / "Contents/MacOS").mkdir(parents=True)
        (self.source / "Contents/Resources").mkdir()
        (self.source / "Contents/_CodeSignature").mkdir()
        (self.source / "Contents/_CodeSignature/CodeResources").write_text("synthetic signature resources")
        self.info = {
            "CFBundleIdentifier": "com.anthropic.claudefordesktop",
            "CFBundleShortVersionString": "1.0.fixture",
            "CFBundleDisplayName": "Claude",
            "CFBundleExecutable": "Claude",
            "CFBundleIconFile": "electron.icns",
            "CFBundleIconName": "electron",
            "CFBundleURLTypes": [], "NSUserActivityTypes": [],
        }
        archive, header = synthetic_asar()
        (self.source / "Contents/Resources/app.asar").write_bytes(archive)
        self.info["ElectronAsarIntegrity"] = {"Resources/app.asar": {"algorithm": "SHA256", "hash": header}}
        (self.source / "Contents/Info.plist").write_bytes(plistlib.dumps(self.info))
        binary = self.source / "Contents/MacOS/Claude"
        binary.write_text('#!/bin/bash\nprintf "%s\\n" "$@"\nprintf "CONFIG=%s VALUE=%s\\n" "${CLAUDE_CONFIG_DIR:-}" "${ANTHROPIC_API_KEY:-}"\n')
        binary.chmod(0o755)
        self.config = self.home / "instances.conf"
        self.env = os.environ.copy()
        self.env.update({
            "PATH": str(self.bin) + ":" + self.env["PATH"],
            "CLAUDE_INSTANCES_CONF": str(self.config),
            "MULTI_CLAUDE_TEST_HOME": str(self.home),
        })
        # Host proxy settings are irrelevant to offline fixture children. The
        # explicit inherited-proxy regression below supplies synthetic values.
        for key in ["HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"]:
            self.env.pop(key, None)
        for src in (REPO / "bin").glob("*.sh"):
            code = src.read_text().replace("$HOME", "$MULTI_CLAUDE_TEST_HOME")
            code = code.replace("PATH=/usr/bin:/bin:/usr/sbin:/sbin\n", "PATH=" + str(self.bin) + ":/usr/bin:/bin:/usr/sbin:/sbin\n")
            code = code.replace('"/Applications', '"' + str(self.apps))
            code = code.replace("CI_PB=/usr/libexec/PlistBuddy", "CI_PB=" + str(self.bin / "pb"))
            code = code.replace("/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister", "/usr/bin/true")
            code = code.replace("/usr/bin/osascript", str(self.bin / "osascript"))
            code = code.replace("/usr/bin/id", str(self.bin / "id"))
            code = code.replace('"$CI_TX_HELPER" publish', '"' + str(self.bin / "tx-publish") + '" "$CI_TX_HELPER" publish')
            (self.bin / src.name).write_text(code)
        (self.bin / "claude-transaction.c").write_text("#define MCD_PROCESS_FIXTURE\n" + (REPO / "bin/claude-transaction.c").read_text())
        shutil.copyfile(REPO / "bin/claude-maintenance.h", self.bin / "claude-maintenance.h")
        shutil.copyfile(REPO / "bin/claude-integrity.c", self.bin / "claude-integrity.c")
        mocks = {
            "id": '#!/bin/bash\n[ "${MOCK_ID_FAIL:-}" != 1 ] || exit 99\nif [[ "$1" == -u && "${MOCK_ROOT:-}" == 1 ]]; then echo 0; else exec /usr/bin/id "$@"; fi\n',
            "ps": '''#!/bin/bash
[ "${MOCK_PS_FAIL:-}" != 1 ] || exit 99
if [ "$1" = -Aww ]; then
 [ -z "${MOCK_RUNNING_APP:-}" ] || printf '101 %s/Contents/MacOS/Claude\n' "$MOCK_RUNNING_APP"
else
 printf 'Claude --user-data-dir=%s\n' "${MOCK_PROFILE:-}"
fi
exit 0
''',
            "codesign": '''#!/bin/bash
previous=""
for arg in "$@"; do
 if [[ "$previous" == -R && "$arg" != =anchor* ]]; then exit 98; fi
 previous="$arg"
done
if [[ "$*" == *"-R "* && "${MOCK_SOURCE_BAD:-}" == 1 ]]; then exit 1; fi
if [[ "$1" == --force && "${MOCK_SIGN_BAD:-}" == 1 ]]; then exit 1; fi
if [[ "$*" == *"--deep "* && "${MOCK_NESTED_BAD:-}" == 1 ]]; then exit 1; fi
exit 0
''',
            "spctl": '#!/bin/bash\n[ "${MOCK_GATEKEEPER_BAD:-}" != 1 ]\n',
            "pb": '''#!/bin/bash
if [[ "${MOCK_PLIST_FAIL:-}" == 1 && "$2" == "Set :CFBundleIdentifier "* ]]; then exit 99; fi
exec /usr/libexec/PlistBuddy "$@"
''',
            "tx-publish": '''#!/bin/bash
[ "${MOCK_PUBLISH_FAIL:-}" != 1 ] || exit 1
exec "$@"
''',
            "cp": '#!/bin/bash\n[ "${MOCK_COPY_FAIL:-}" != 1 ] || exit 1\nexec /bin/cp "$@"\n',
            "open": '#!/bin/bash\nexit 0\n',
            "osascript": '#!/bin/bash\nexit 0\n',
            "claude-recolor-icon.sh": '#!/bin/bash\nprintf fixture-icon > "$2"\n',
        }
        for name, code in mocks.items():
            p = self.bin / name
            p.write_text(code)
            p.chmod(0o755)
        self.write_config()

    def profile(self, label):
        return self.home / "Library/Application Support" / ("Claude-" + label)

    def code(self, label):
        return self.home / (".claude-" + label)

    def write_config(self, rows=None):
        if rows is None:
            rows = [("One", "com.audit.one", str(self.profile('profile one')), "142", "CLAUDE_CONFIG_DIR=" + str(self.code('code one')))]
        self.config.write_text("\n".join(" | ".join(row) for row in rows) + "\n")

    def run_script(self, *args, script="claude-apps-refresh.sh"):
        return subprocess.run(["/bin/bash", str(self.bin / script), *args], env=self.env,
                              text=True, capture_output=True, timeout=20)

    def launch(self, *args):
        return subprocess.run([str(self.app / "Contents/MacOS/launcher"), *args],
                              env=self.env, cwd=self.root, text=True, capture_output=True, timeout=10)

    @property
    def app(self):
        return self.apps / "Claude One.app"

    def build(self):
        p = self.run_script()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def set_mode(self, mode="original-copy"):
        self.config.write_text("@mode " + mode + "\n" + self.config.read_text())

    def test_original_copy_mode_builds_only_the_copy_and_preserves_stock(self):
        self.set_mode()
        before = {str(p.relative_to(self.source)): p.read_bytes() for p in self.source.rglob("*") if p.is_file()}
        self.build()
        self.assertEqual(self.run_script("--verify").returncode, 0)
        after = {str(p.relative_to(self.source)): p.read_bytes() for p in self.source.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertIn("profile policy: original-copy", (self.app / "Contents/MacOS/launcher").read_text())

    def test_original_copy_mode_reserves_both_default_profiles(self):
        for desktop, code in [(str(self.home / "Library/Application Support/Claude"), str(self.code("copy"))),
                              (str(self.profile("copy")), str(self.home / ".claude")),
                              (str(self.profile("copy")), "")]:
            self.write_config([("One", "com.audit.one", desktop, "142", "CLAUDE_CONFIG_DIR=" + code if code else "")])
            self.set_mode()
            result = self.run_script()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("reserved for the original", result.stderr)
            self.assertFalse(self.app.exists())

    def test_original_copy_mode_rejects_case_and_physical_aliases_of_defaults(self):
        default = self.home / ".claude"
        default.mkdir()
        alias = self.code("alias")
        alias.symlink_to(default, target_is_directory=True)
        for value in [str(alias), str(default).replace(".claude", ".CLAUDE")]:
            self.write_config([("One", "com.audit.one", str(self.profile("copy")), "142", "CLAUDE_CONFIG_DIR=" + value)])
            self.set_mode()
            self.assertNotEqual(self.run_script("--list").returncode, 0)

    def two_copies(self, two_desktop=None, two_code=None):
        self.write_config([("One", "com.audit.one", str(self.profile("one")), "142", "CLAUDE_CONFIG_DIR=" + str(self.code("one"))),
                           ("Two", "com.audit.two", two_desktop or str(self.profile("two")), "212",
                            "CLAUDE_CONFIG_DIR=" + (two_code or str(self.code("two"))))])
        self.set_mode()

    def test_original_copy_mode_builds_several_copies_each_with_its_own_identity(self):
        self.two_copies()
        self.build()
        self.assertEqual(self.run_script("--verify").returncode, 0)
        two = self.apps / "Claude Two.app"
        ids = {plistlib.loads((app / "Contents/Info.plist").read_bytes())["CFBundleIdentifier"] for app in (self.app, two)}
        self.assertEqual(ids, {"com.audit.one", "com.audit.two"})
        for app in (self.app, two):
            self.assertIn("profile policy: original-copy", (app / "Contents/MacOS/launcher").read_text())

    def test_adding_a_copy_keeps_the_existing_copy_verified(self):
        self.write_config([("One", "com.audit.one", str(self.profile("one")), "142", "CLAUDE_CONFIG_DIR=" + str(self.code("one")))])
        self.set_mode()
        self.build()
        build_id = (self.app / "Contents/Resources/mcd-build-id").read_text()
        self.two_copies()
        built = self.run_script("Two")
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        self.assertEqual((self.app / "Contents/Resources/mcd-build-id").read_text(), build_id)
        self.assertEqual(self.run_script("--verify").returncode, 0)

    def test_every_copy_keeps_off_the_default_profiles(self):
        for desktop, code in [(str(self.home / "Library/Application Support/Claude"), None), (None, str(self.home / ".claude"))]:
            self.two_copies(desktop, code)
            result = self.run_script("--list")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("reserved for the original", result.stderr)

    def test_copies_cannot_share_a_profile(self):
        self.two_copies(two_code=str(self.code("one")))
        result = self.run_script("--list")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("overlap", result.stderr)

    def test_mode_is_data_and_cannot_be_late_duplicate_or_environment_override(self):
        original = self.config.read_text()
        for value in ["@mode unknown\n" + original, "@mode original-copy\n@mode legacy-copies\n" + original,
                      original + "@mode original-copy\n", "@mode $(touch marker)\n" + original]:
            self.config.write_text(value)
            self.assertNotEqual(self.run_script("--list").returncode, 0)
        self.write_config([("One", "com.audit.one", str(self.profile("one")), "142", "")])
        self.set_mode()
        self.env["CI_MODE"] = "legacy-copies"
        self.assertNotEqual(self.run_script("--list").returncode, 0)

    def test_switching_profile_policy_requires_a_reviewed_rebuild(self):
        self.build()
        self.set_mode()
        self.assertNotEqual(self.run_script("--verify").returncode, 0)
        self.build()
        self.assertEqual(self.run_script("--verify").returncode, 0)

    def test_user_apps_build_verification_and_recovery_preserve_system_apps(self):
        private = self.home / "Applications"
        private.mkdir(mode=0o700)
        self.set_mode()
        self.config.write_text("@apps user\n" + self.config.read_text())
        before = {str(p.relative_to(self.source)): p.read_bytes() for p in self.source.rglob("*") if p.is_file()}
        self.build()
        copy = private / "Claude One.app"
        self.assertTrue(copy.is_dir())
        self.assertFalse(self.app.exists())
        self.assertFalse((self.apps / ".claude-instances.lock").exists())
        verified = self.run_script("--verify")
        self.assertEqual(verified.returncode, 0, verified.stderr)
        launched = subprocess.run([str(copy / "Contents/MacOS/launcher")], env=self.env,
                                  text=True, capture_output=True, timeout=10)
        self.assertEqual(launched.returncode, 0, launched.stderr)
        self.assertIn("--user-data-dir=" + str(self.profile("profile one")), launched.stdout)
        self.assertIn("CONFIG=" + str(self.code("code one")), launched.stdout)
        self.build()
        retained = [p for p in private.glob(".claude-txn.*") if (p / "instance.app").exists()]
        self.assertEqual(len(retained), 1)
        observed = self.run_script("--recover", retained[0].name)
        self.assertEqual(observed.returncode, 0, observed.stderr)
        self.assertIn("COMMITTED", observed.stdout)
        after = {str(p.relative_to(self.source)): p.read_bytes() for p in self.source.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_user_apps_cache_cleanup_preserves_account_data_and_system_directory(self):
        private = self.home / "Applications"
        private.mkdir(mode=0o700)
        self.config.write_text("@apps user\n" + self.config.read_text())
        self.build()
        profile = self.profile("profile one")
        (profile / "Cache").mkdir()
        (profile / "Cache/bytes").write_text("regenerable fixture")
        (profile / "Local Storage").mkdir()
        data = profile / "Local Storage/keep"
        data.write_text("invented account state")
        result = self.run_script(script="claude-clear-caches.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((profile / "Cache").exists())
        self.assertEqual(data.read_text(), "invented account state")
        self.assertFalse((self.apps / ".claude-instances.lock").exists())

    def test_user_apps_maintenance_uses_one_private_kernel_lock(self):
        private = self.home / "Applications"
        private.mkdir(mode=0o700)
        self.config.write_text("@apps user\n" + self.config.read_text())
        shutil.copyfile(REPO / "bin/claude-checkpoint.c", self.bin / "claude-checkpoint.c")
        tx = self.bin / "transaction"
        subprocess.run(["/usr/bin/clang", "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2",
                        "-mmacosx-version-min=12.0", str(REPO / "bin/claude-transaction.c"), "-o", str(tx)], check=True)
        worker = self.bin / "hold.sh"
        worker.write_text('"$1" locked "$2" || exit 1\nprintf "LOCKED\\n"\nexec /bin/cat >/dev/null\n')
        process = subprocess.Popen([str(tx), "run", str(private), str(worker), str(private)],
                                   env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: process.communicate(timeout=5))
        self.addCleanup(lambda: process.kill() if process.poll() is None else None)
        self.assertTrue(select.select([process.stdout], [], [], 5)[0])
        self.assertEqual(process.stdout.readline().strip(), "LOCKED")
        for script, args in [("claude-apps-refresh.sh", ()), ("claude-clear-caches.sh", ()),
                             ("claude-profile-checkpoint.sh", ("create", "One"))]:
            result = self.run_script(*args, script=script)
            self.assertNotEqual(result.returncode, 0, script)
            self.assertIn("holds the lock", result.stderr)
        self.assertFalse(self.app.exists())
        self.assertFalse((self.apps / ".claude-instances.lock").exists())

    def test_apps_directive_is_data_and_rejects_late_duplicate_or_arbitrary_paths(self):
        original = self.config.read_text()
        marker = self.home / "unexpected"
        for directive in ["@apps /tmp", "@apps ~/Applications", "@apps USER", "@apps user extra",
                          "@apps $(touch " + str(marker) + ")", "@apps user\n@apps system"]:
            self.config.write_text(directive + "\n" + original)
            self.assertNotEqual(self.run_script("--list").returncode, 0, directive)
        self.config.write_text(original + "@apps user\n")
        self.assertNotEqual(self.run_script("--list").returncode, 0)
        self.assertFalse(marker.exists())
        self.assertFalse((self.home / "Applications").exists())
        self.assertFalse(self.app.exists())

    def test_user_apps_missing_file_alias_or_nonprivate_mode_is_refused_without_changes(self):
        private = self.home / "Applications"
        self.config.write_text("@apps user\n" + self.config.read_text())
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assertFalse(private.exists())
        private.write_text("keep")
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assertEqual(private.read_text(), "keep")
        private.unlink()
        private.symlink_to(self.apps, target_is_directory=True)
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assertFalse(self.app.exists())
        private.unlink()
        private.mkdir(mode=0o755)
        for mode in [0o755, 0o775, 0o1700]:
            private.chmod(mode)
            self.assertNotEqual(self.run_script().returncode, 0)
            self.assertEqual(private.stat().st_mode & 0o7777, mode)
        self.assertFalse(list(private.iterdir()))

    def test_user_apps_rechecks_acl_and_ancestor_permissions_before_maintenance(self):
        private = self.home / "Applications"
        private.mkdir(mode=0o700)
        self.config.write_text("@apps user\n" + self.config.read_text())
        subprocess.run(["/bin/chmod", "+a", "everyone deny delete", str(private)], check=True)
        self.assertEqual(self.run_script("--list").returncode, 0)
        subprocess.run(["/bin/chmod", "+a", "everyone allow add_file", str(private)], check=True)
        for script, args in [("claude-apps-refresh.sh", ()), ("claude-clear-caches.sh", ()),
                             ("claude-profile-checkpoint.sh", ("create", "One"))]:
            self.assertNotEqual(self.run_script(*args, script=script).returncode, 0)
        subprocess.run(["/bin/chmod", "-N", str(private)], check=True)
        self.home.chmod(0o777)
        self.assertNotEqual(self.run_script("--list").returncode, 0)
        self.assertFalse(list(private.iterdir()))

    def test_apps_location_ignores_inherited_overrides_and_preserves_default_binding(self):
        self.env.update({"CI_APP_SCOPE": "user", "CI_APPS": str(self.home / "unexpected"),
                         "CI_SYSTEM_APPS": str(self.home / "unexpected")})
        self.build()
        launcher = (self.app / "Contents/MacOS/launcher").read_bytes()
        self.config.write_text("@apps system\n" + self.config.read_text())
        self.assertEqual(self.run_script("--verify").returncode, 0)
        self.assertEqual((self.app / "Contents/MacOS/launcher").read_bytes(), launcher)
        self.assertFalse((self.home / "unexpected").exists())

    def test_moving_a_system_copy_to_user_apps_requires_a_reviewed_rebuild(self):
        self.set_mode()
        self.build()
        private = self.home / "Applications"
        private.mkdir(mode=0o700)
        moved = private / self.app.name
        self.app.rename(moved)
        self.config.write_text("@apps user\n" + self.config.read_text())
        self.assertNotEqual(self.run_script("--verify").returncode, 0)
        # Even replacing the expected launcher cannot reuse the old content
        # record: application location participates in its binding.
        script = 'source "$1"; ci_load_config && ci_render_launcher 0'
        rendered = subprocess.run(["/bin/bash", "-c", script, "test", str(self.bin / "claude-instances-common.sh")],
                                  env=self.env, capture_output=True, check=True)
        (moved / "Contents/MacOS/launcher").write_bytes(rendered.stdout)
        refused = self.run_script("--verify")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("binding", refused.stderr)

    def test_build_and_gui_launcher_environment(self):
        self.build()
        p = self.launch()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("--user-data-dir=" + str(self.profile('profile one')), p.stdout)
        self.assertIn("CONFIG=" + str(self.code('code one')), p.stdout)
        self.assertEqual(self.run_script("--verify").returncode, 0)
        for path in [self.profile('profile one'), self.code('code one')]:
            self.assertEqual(path.stat().st_mode & 0o777, 0o700)

    def test_maintenance_resets_inherited_path_before_resolving_commands(self):
        hostile = self.root / "hostile-bin"
        hostile.mkdir()
        marker = self.root / "unexpected-tool-executed"
        for name in ("dirname", "codesign", "cp", "clang", "ps"):
            tool = hostile / name
            tool.write_text('#!/bin/bash\n: > "' + str(marker) + '"\nexit 99\n')
            tool.chmod(0o755)
        self.env["PATH"] = str(hostile) + ":" + self.env["PATH"]
        self.build()
        self.assertEqual(self.run_script("--verify").returncode, 0)
        self.assertFalse(marker.exists())

    def test_invalid_names_and_traversal_rejected_before_mutation(self):
        for name in ["X/../../victim", "bad name", "../Claude", "A$(touch marker)", "-bad"]:
            with self.subTest(name=name):
                self.write_config([(name, "com.audit.one", str(self.profile('p')), "142", "")])
                self.assertNotEqual(self.run_script().returncode, 0)
                self.assertFalse(self.app.exists())

    def test_elevated_invocation_is_refused(self):
        self.env["MOCK_ROOT"] = "1"
        p = self.run_script()
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("root", p.stderr)
        self.assertFalse(self.app.exists())

    def test_unknown_user_identity_is_refused(self):
        self.env["MOCK_ID_FAIL"] = "1"
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assertFalse(self.app.exists())

    def test_invalid_environment_names_rejected(self):
        marker = self.root / "marker"
        for env in ["X$(touch " + str(marker) + ")=ok", "BASH_ENV=bad", "DYLD_INSERT_LIBRARIES=bad", "PATH=/bad", "HOME=/bad", "NODE_OPTIONS=bad", "A=one;A=two"]:
            with self.subTest(env=env):
                self.write_config([("One", "com.audit.one", str(self.profile('p')), "142", env)])
                self.assertNotEqual(self.run_script().returncode, 0)
                self.assertFalse(marker.exists())

    def test_provider_credentials_and_runtime_overrides_are_refused(self):
        for key in ["ELECTRON_RUN_AS_NODE", "ELECTRON_ENABLE_LOGGING", "ANTHROPIC_API_KEY",
                    "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "HTTPS_PROXY", "NODE_EXTRA_CA_CERTS"]:
            with self.subTest(key=key):
                self.write_config([("One", "com.audit.one", str(self.profile('p')), "142", key + "=synthetic-canary")])
                self.assertNotEqual(self.run_script().returncode, 0)
                self.assertFalse(self.app.exists())

    def test_launcher_does_not_inherit_provider_or_runtime_environment(self):
        self.build()
        (self.app / "Contents/MacOS/Claude").write_text('''#!/bin/bash
printf '%s|%s|%s|%s|%s|%s\\n' "${ANTHROPIC_API_KEY:-}" "${ANTHROPIC_BASE_URL:-}" "${ELECTRON_RUN_AS_NODE:-}" "${NODE_OPTIONS:-}" "${HTTPS_PROXY:-}" "$PATH"
''')
        for key in ["ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ELECTRON_RUN_AS_NODE", "NODE_OPTIONS"]:
            self.env[key] = "synthetic-canary"
        p = self.launch()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.strip(), "|||||/usr/bin:/bin:/usr/sbin:/sbin")

    def test_inherited_proxy_causes_refusal_instead_of_silent_bypass(self):
        self.build()
        for key in ["HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"]:
            self.env[key] = "http://synthetic.invalid:8080"
            p = self.launch()
            self.assertNotEqual(p.returncode, 0)
            self.assertIn("proxy environment detected", p.stderr)
            self.assertNotIn("synthetic.invalid", p.stderr)
            self.assertEqual(p.stdout, "")
            del self.env[key]

    def test_launcher_ignores_inherited_bash_startup_file(self):
        self.build()
        marker = self.root / "startup-executed"
        startup = self.root / "startup.sh"
        startup.write_text('touch "' + str(marker) + '"\n')
        self.env["BASH_ENV"] = str(startup)
        self.assertEqual(self.launch().returncode, 0)
        self.assertFalse(marker.exists())

    def test_all_caller_arguments_are_refused(self):
        self.build()
        for arg in ["--no-sandbox", "--inspect=9229", "--remote-debugging-port=9222",
                    "--proxy-server=example.invalid", "--user-data-dir=/other", "claude://callback"]:
            with self.subTest(arg=arg):
                self.assertNotEqual(self.launch(arg).returncode, 0)

    def test_environment_values_are_data_and_list_redacts_them(self):
        marker = self.root / "marker"
        value = str(self.code("$(touch marker)"))
        self.write_config([("One", "com.audit.one", str(self.profile('p')), "142", "CLAUDE_CONFIG_DIR=" + value)])
        self.build()
        p = self.launch()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn(value, p.stdout)
        self.assertFalse(marker.exists())
        listed = self.run_script("--list")
        self.assertIn("CLAUDE_CONFIG_DIR", listed.stdout)
        self.assertNotIn(value, listed.stdout)

    def test_verification_never_executes_tampered_launcher(self):
        self.build()
        marker = self.root / "marker"
        (self.app / "Contents/MacOS/launcher").write_text("#!/bin/bash\nDATA_DIR=$(touch " + str(marker) + ")\n")
        self.assertNotEqual(self.run_script("--verify").returncode, 0)
        self.assertFalse(marker.exists())

    def test_verification_compares_environment_values(self):
        self.build()
        p = self.app / "Contents/MacOS/launcher"
        p.write_text(p.read_text().replace("code\\ one", "code\\ other"))
        self.assertNotEqual(self.run_script("--verify").returncode, 0)

    def test_missing_apps_are_a_failure(self):
        self.assertNotEqual(self.run_script("--verify").returncode, 0)

    def test_duplicates_and_alias_profiles_rejected(self):
        real = self.profile('real')
        real.mkdir()
        alias = self.profile('alias')
        alias.symlink_to(real, target_is_directory=True)
        variants = [
            ("Two", "com.audit.one", str(self.profile('other')), str(self.code('c2'))),
            ("one", "com.audit.two", str(self.profile('other')), str(self.code('c2'))),
            ("Two", "com.audit.two", str(real), str(self.code('c2'))),
            ("Two", "com.audit.two", str(alias), str(self.code('c2'))),
            ("Two", "com.audit.two", str(real) + "/.", str(self.code('c2'))),
            ("Two", "com.audit.two", str(self.profile('other')), str(self.code('c1'))),
        ]
        for name, bid, profile, code in variants:
            self.write_config([("One", "com.audit.one", str(real), "142", "CLAUDE_CONFIG_DIR=" + str(self.code('c1'))),
                               (name, bid, profile, "212", "CLAUDE_CONFIG_DIR=" + code)])
            self.assertNotEqual(self.run_script().returncode, 0)

    def test_two_default_code_directories_rejected(self):
        self.write_config([("One", "com.audit.one", str(self.profile('p1')), "142", ""),
                           ("Two", "com.audit.two", str(self.profile('p2')), "212", "")])
        self.assertNotEqual(self.run_script().returncode, 0)

    def test_system_arbitrary_and_nested_directories_are_refused(self):
        for path in ["/etc", "/private/tmp", str(self.home / "Documents"),
                     str(self.profile('p') / 'nested'), str(self.code('p')),
                     str(self.profile('bundle.app'))]:
            with self.subTest(path=path):
                self.write_config([("One", "com.audit.one", path, "142", "")])
                self.assertNotEqual(self.run_script("--list").returncode, 0)
                self.assertFalse(self.app.exists())
        for path in [str(self.profile('p')), str(self.code('p') / 'nested'), "/etc"]:
            with self.subTest(code=path):
                self.write_config([("One", "com.audit.one", str(self.profile('p')), "142", "CLAUDE_CONFIG_DIR=" + path)])
                self.assertNotEqual(self.run_script("--list").returncode, 0)

    def test_managed_uuid_locations_and_cross_type_policy(self):
        base = self.home / "Library/Application Support/Multi Claude Desktop/Profiles"
        ids = ["439b7ef2-faf3-47fb-bc65-366c30a406cb", "edc1f5bd-1b3f-4c1c-beba-d677d0fb3a21"]
        rows = [(name, "com.audit." + name, str(base / uid / "desktop"), "142", "CLAUDE_CONFIG_DIR=" + str(base / uid / "code"))
                for name, uid in zip(["One", "Two"], ids)]
        self.write_config(rows)
        self.build()
        self.assertEqual(self.run_script("--verify").returncode, 0)
        for suffix in ["code", "desktop/nested", "../desktop", "desktop.app"]:
            bad = list(rows[0])
            bad[2] = str(base / ids[0] / suffix)
            self.write_config([bad])
            self.assertNotEqual(self.run_script("--list").returncode, 0, suffix)

    def test_writable_config_and_profile_ancestors_are_refused(self):
        self.config.chmod(0o666)
        self.assertNotEqual(self.run_script("--list").returncode, 0)
        self.config.chmod(0o600)
        self.profile('profile one').mkdir()
        self.profile('profile one').chmod(0o777)
        self.assertNotEqual(self.run_script().returncode, 0)
        self.profile('profile one').chmod(0o700)
        self.home.chmod(0o777)
        self.assertNotEqual(self.run_script("--list").returncode, 0)

    def test_custom_allow_acls_are_refused_and_default_deny_acl_is_allowed(self):
        profile = self.profile('profile one')
        profile.mkdir(mode=0o700)
        subprocess.run(["/bin/chmod", "+a", "everyone deny delete", str(profile)], check=True)
        self.assertEqual(self.run_script("--list").returncode, 0)
        subprocess.run(["/bin/chmod", "+a", "everyone allow add_file", str(profile)], check=True)
        self.assertNotEqual(self.run_script("--list").returncode, 0)
        subprocess.run(["/bin/chmod", "-N", str(profile)], check=True)
        subprocess.run(["/bin/chmod", "+a", "everyone allow write", str(self.config)], check=True)
        self.assertNotEqual(self.run_script("--list").returncode, 0)

    def test_unicode_legacy_aliases_are_rejected_before_creation(self):
        for suffix in ["caf\u00e9", "cafe\u0301"]:
            self.write_config([("One", "com.audit.one", str(self.profile(suffix)), "142", "")])
            self.assertNotEqual(self.run_script("--list").returncode, 0)
            self.assertFalse(self.profile(suffix).exists())

    def test_launch_refuses_replaced_profile_or_writable_ancestor(self):
        self.build()
        profile = self.profile('profile one')
        profile.rmdir()
        replacement = self.profile('other')
        replacement.mkdir()
        profile.symlink_to(replacement, target_is_directory=True)
        self.assertNotEqual(self.launch().returncode, 0)
        profile.unlink()
        profile.mkdir(mode=0o700)
        profile.parent.chmod(0o777)
        self.assertNotEqual(self.launch().returncode, 0)

    def test_overlap_check_includes_cross_type_nesting_and_case(self):
        # Directly exercise the graph guard even though the location policy
        # already rejects most nested/cross-type paths before this point.
        common = str(self.bin / "claude-instances-common.sh")
        for left, right, expected in [("/a/P", "/a/p/child", 0), ("/a/p", "/a/prefix", 1),
                                      ("/a/p/code", "/a/p", 0), ("/a/one", "/a/two", 1)]:
            p = subprocess.run(["/bin/bash", "-c", 'source "$1"; ci_paths_overlap "$2" "$3"',
                                "test", common, left, right], capture_output=True, env=self.env)
            self.assertEqual(p.returncode, expected)

    def test_source_and_gatekeeper_failure_leave_apps_untouched(self):
        self.build()
        sentinel = self.app / "sentinel"
        sentinel.write_text("keep")
        for flag in ["MOCK_SOURCE_BAD", "MOCK_GATEKEEPER_BAD"]:
            self.env[flag] = "1"
            self.assertNotEqual(self.run_script().returncode, 0)
            self.assertEqual(sentinel.read_text(), "keep")
            del self.env[flag]

    def test_failed_copy_plist_or_signing_preserves_old_bundle(self):
        self.build()
        sentinel = self.app / "sentinel"
        sentinel.write_text("keep")
        retained = set(self.apps.glob(".claude-txn.*"))
        for flag in ["MOCK_COPY_FAIL", "MOCK_PLIST_FAIL", "MOCK_SIGN_BAD"]:
            with self.subTest(flag=flag):
                self.env[flag] = "1"
                self.assertNotEqual(self.run_script().returncode, 0)
                self.assertEqual(sentinel.read_text(), "keep")
                self.assertEqual(set(self.apps.glob(".claude-txn.*")), retained)
                del self.env[flag]

    def test_failed_publication_restores_previous_app(self):
        self.build()
        sentinel = self.app / "sentinel"
        sentinel.write_text("keep")
        self.env["MOCK_PUBLISH_FAIL"] = "1"
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assertEqual(sentinel.read_text(), "keep")

    def test_rebuild_retains_previous_bundle_and_recovery_only_observes(self):
        self.build()
        old_inode = self.app.stat().st_ino
        (self.app / "retained-marker").write_text("previous")
        self.build()
        transactions = [p for p in self.apps.glob(".claude-txn.*")
                        if (p / "instance.app/retained-marker").exists()]
        self.assertEqual(len(transactions), 1)
        previous = transactions[0] / "instance.app"
        self.assertEqual(previous.stat().st_ino, old_inode)
        self.assertNotEqual(self.app.stat().st_ino, old_inode)
        before = (self.app.stat().st_ino, previous.stat().st_ino,
                  (transactions[0] / "transaction.txt").read_bytes())
        recovered = self.run_script("--recover", transactions[0].name)
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertIn("COMMITTED", recovered.stdout)
        self.assertIn("No bundle/profile was restored", recovered.stdout)
        self.assertEqual(before, (self.app.stat().st_ino, previous.stat().st_ino,
                                  (transactions[0] / "transaction.txt").read_bytes()))

    def test_failed_atomic_operation_retains_journal_for_prepared_recovery(self):
        self.build()
        old_inode = self.app.stat().st_ino
        retained = set(self.apps.glob(".claude-txn.*"))
        source = self.bin / "claude-transaction.c"
        source.write_text("#define MCD_TRANSACTION_TESTING\n" + source.read_text())
        self.env["MCD_TEST_RENAME_UNSUPPORTED"] = "1"
        failed = self.run_script()
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(self.app.stat().st_ino, old_inode)
        transactions = set(self.apps.glob(".claude-txn.*")) - retained
        self.assertEqual(len(transactions), 1)
        transaction = transactions.pop()
        self.assertTrue((transaction / "transaction.txt").exists())
        recovered = self.run_script("--recover", transaction.name)
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertIn("PREPARED", recovered.stdout)
        self.assertEqual(self.app.stat().st_ino, old_inode)

    def test_recovery_refuses_tampered_published_launcher(self):
        self.build()
        transaction = next(self.apps.glob(".claude-txn.*"))
        launcher = self.app / "Contents/MacOS/launcher"
        launcher.write_text(launcher.read_text() + "\necho tampered\n")
        self.assertNotEqual(self.run_script("--recover", transaction.name).returncode, 0)
        self.assertTrue(launcher.exists())
        self.assertTrue(transaction.exists())

    def test_compiler_failure_leaves_existing_bundle_untouched(self):
        self.build()
        inode = self.app.stat().st_ino
        entry = self.bin / "claude-apps-refresh.sh"
        entry.write_text(entry.read_text().replace("/usr/bin/clang", "/usr/bin/false"))
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assertEqual(self.app.stat().st_ino, inode)

    def test_production_build_has_no_environment_fault_injection(self):
        self.env["MCD_TEST_RENAME_UNSUPPORTED"] = "1"
        self.env["MCD_TEST_STOP"] = "journal-synced"
        self.build()

    def test_unknown_process_state_blocks_rebuild_and_health_check(self):
        self.env["MOCK_PS_FAIL"] = "1"
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assertFalse(self.app.exists())
        self.assertEqual(self.run_script(script="claude-instance-check.sh").returncode, 2)

    def test_running_app_blocks_rebuild(self):
        self.env["MOCK_RUNNING_APP"] = str(self.app)
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assertFalse(self.app.exists())

    def test_symlink_destination_rejected(self):
        victim = self.root / "victim"
        victim.mkdir()
        self.app.symlink_to(victim, target_is_directory=True)
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assertTrue(victim.is_dir())

    def test_lock_does_not_get_removed_by_another_run(self):
        lock = self.home / ".local/share/claude-instances/.refresh-lock"
        lock.mkdir(parents=True)
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assertTrue(lock.is_dir())

    def test_symlinked_icon_directory_and_icon_master_are_refused(self):
        icons = self.home / ".local/share/claude-instances"
        icons.parent.mkdir(parents=True)
        victim = self.root / "victim"
        victim.mkdir()
        icons.symlink_to(victim, target_is_directory=True)
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assertEqual(list(victim.iterdir()), [])
        icons.unlink()
        icons.mkdir(mode=0o700)
        marker = victim / "marker"
        (icons / "one.icns").symlink_to(marker)
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assertFalse(marker.exists())

    def test_writable_icon_ancestor_is_refused_before_publication(self):
        shared = self.home / ".local/share"
        shared.mkdir(parents=True)
        shared.chmod(0o777)
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assertFalse(self.app.exists())

    def test_launcher_rejects_profile_override(self):
        self.build()
        p = self.launch("--user-data-dir=/other")
        self.assertNotEqual(p.returncode, 0)

    def test_cache_preserves_data_and_refuses_a_shared_running_profile(self):
        self.build()
        profile = self.profile('profile one')
        (profile / "Cache").mkdir()
        (profile / "Cache/sentinel").write_text("cache")
        (profile / "Local Storage").mkdir()
        (profile / "Local Storage/sentinel").write_text("keep")
        self.env["MOCK_RUNNING_APP"] = str(self.source)
        self.env["MOCK_PROFILE"] = str(profile)
        self.assertNotEqual(self.run_script(script="claude-clear-caches.sh").returncode, 0)
        self.assertTrue((profile / "Cache/sentinel").exists())
        del self.env["MOCK_RUNNING_APP"]
        self.assertEqual(self.run_script(script="claude-clear-caches.sh").returncode, 0)
        self.assertFalse((profile / "Cache").exists())
        self.assertEqual((profile / "Local Storage/sentinel").read_text(), "keep")

    def test_watchdog_detects_tampering(self):
        self.build()
        self.env["CLAUDE_STALE_CHECK_DELAY"] = "0"
        self.assertEqual(self.run_script(script="claude-stale-check.sh").returncode, 0)
        p = self.app / "Contents/MacOS/launcher"
        p.write_text(p.read_text() + "\necho altered\n")
        self.assertNotEqual(self.run_script(script="claude-stale-check.sh").returncode, 0)

    def test_watchdog_does_not_append_to_symlinked_or_hardlinked_logs(self):
        self.build()
        self.env["CLAUDE_STALE_CHECK_DELAY"] = "0"
        folder = self.home / "Library/Logs/claude-instances"
        folder.mkdir(parents=True)
        victim = self.root / "keep.txt"
        victim.write_text("keep")
        log = folder / "watchdog.log"
        log.symlink_to(victim)
        self.assertNotEqual(self.run_script(script="claude-stale-check.sh").returncode, 0)
        self.assertEqual(victim.read_text(), "keep")
        log.unlink()
        os.link(victim, log)
        self.assertNotEqual(self.run_script(script="claude-stale-check.sh").returncode, 0)
        self.assertEqual(victim.read_text(), "keep")

    def test_status_reports_its_limits(self):
        self.build()
        self.assertIn("Version comparison only", self.run_script("--status").stdout)
        self.assertIn("account isolation not established", self.run_script("--verify").stdout)

    def test_source_is_never_modified(self):
        before = {str(p.relative_to(self.source)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in self.source.rglob("*") if p.is_file()}
        self.build()
        after = {str(p.relative_to(self.source)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in self.source.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_numeric_hues_and_malformed_config(self):
        for hue in ["0", "000", "359"]:
            self.write_config([("One", "com.audit.one", str(self.profile('p')), hue, "")])
            self.assertEqual(self.run_script("--list").returncode, 0, hue)
        for hue in ["-1", "360", "NaN", "1.5", "1000"]:
            self.write_config([("One", "com.audit.one", str(self.profile('p')), hue, "")])
            self.assertNotEqual(self.run_script("--list").returncode, 0, hue)
        self.config.write_text("One | com.audit.one | /tmp/p | 142 | A=b | extra\n")
        self.assertNotEqual(self.run_script("--list").returncode, 0)

    def test_root_home_application_and_relative_profiles_rejected(self):
        for path in ["/", str(self.home), str(self.source), "relative/path"]:
            self.write_config([("One", "com.audit.one", path, "142", "")])
            self.assertNotEqual(self.run_script("--list").returncode, 0, path)

    def test_config_without_final_newline_and_apostrophe_path(self):
        profile = str(self.profile("user's profile"))
        self.write_config([("One", "com.audit.one", profile, "142", "")])
        self.config.write_text(self.config.read_text().rstrip("\n"))
        self.build()
        self.assertEqual(self.run_script("--verify").returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
