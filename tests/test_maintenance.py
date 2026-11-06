"""Real descriptor cleanup and native process metadata on disposable fixtures."""
import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

REPO = Path(__file__).resolve().parents[1]


class MaintenanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compilation = tempfile.TemporaryDirectory(prefix="claude-maintenance-native-")
        cls.addClassCleanup(cls.compilation.cleanup)
        cls.helper = Path(cls.compilation.name) / "helper"
        cls.production = Path(cls.compilation.name) / "production"
        command = ["/usr/bin/clang", "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2", "-mmacosx-version-min=12.0",
                   str(REPO / "bin/claude-transaction.c")]
        subprocess.run(command + ["-DMCD_TRANSACTION_TESTING", "-DMCD_PROCESS_FIXTURE", "-o", str(cls.helper)], check=True)
        subprocess.run(command + ["-o", str(cls.production)], check=True)

    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="claude-maintenance-")
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name).resolve()
        self.apps = self.root / "apps"
        self.apps.mkdir(mode=0o700)
        self.home = self.root / "home"
        self.profile = self.home / "Library/Application Support/Claude-Fixture"
        self.profile.mkdir(parents=True, mode=0o700)
        self.cache = self.profile / "Cache"
        (self.cache / "nested").mkdir(parents=True)
        (self.cache / "nested/bytes").write_text("regenerable synthetic bytes")
        (self.profile / "Local Storage").mkdir()
        (self.profile / "Local Storage/keep").write_text("invented account data; preserve")
        self.worker = self.root / "worker.sh"
        self.worker.write_text('exec "$1" clean "$2" "$3"\n')
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("MCD_", "MOCK_"))}
        self.env["MULTI_CLAUDE_TEST_HOME"] = str(self.home)

    def command(self):
        return list(map(str, [self.helper, "run", self.apps, self.worker, self.apps, self.profile]))

    def clean(self):
        return subprocess.run(self.command(), env=self.env, text=True, capture_output=True, timeout=10)

    @staticmethod
    def kill(process):
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)

    def stop(self, point):
        self.env["MCD_CACHE_STOP"] = point
        process = subprocess.Popen(self.command(), env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: self.kill(process))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            pid, status = os.waitpid(process.pid, os.WNOHANG | os.WUNTRACED)
            if pid:
                if os.WIFSTOPPED(status):
                    return process
                self.fail("cleanup exited before injection point")
            time.sleep(0.01)
        self.fail("cleanup did not reach injection point")

    def test_only_fixed_caches_are_removed_with_no_quarantine_left(self):
        (self.profile / "GPUCache").write_text("synthetic cache file")
        (self.profile / "Unknown Cache").mkdir()
        (self.profile / "Unknown Cache/keep").write_text("not an allowed name")
        result = self.clean()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.cache.exists())
        self.assertFalse((self.profile / "GPUCache").exists())
        self.assertEqual((self.profile / "Local Storage/keep").read_text(), "invented account data; preserve")
        self.assertTrue((self.profile / "Unknown Cache/keep").exists())
        self.assertFalse(list(self.profile.glob(".claude-cache.*")))

    def test_links_hardlinks_and_unsafe_modes_fail_before_quarantine(self):
        victim = self.root / "victim"
        victim.write_text("unrelated")
        bad = self.cache / "bad"
        for kind in ("symlink", "hardlink", "writable", "fifo"):
            with self.subTest(kind=kind):
                if kind == "symlink":
                    bad.symlink_to(victim)
                elif kind == "hardlink":
                    os.link(victim, bad)
                elif kind == "writable":
                    bad.write_text("cache")
                    bad.chmod(0o666)
                else:
                    os.mkfifo(bad)
                self.assertNotEqual(self.clean().returncode, 0)
                self.assertTrue((self.cache / "nested/bytes").exists())
                self.assertFalse(list(self.profile.glob(".claude-cache.*")))
                bad.unlink()
        self.assertEqual(victim.read_text(), "unrelated")

    def test_running_or_unknown_native_observation_blocks_mutation(self):
        for key, value in (("MOCK_PS_FAIL", "1"), ("MOCK_RUNNING_APP", str(self.apps / "Claude Other.app"))):
            self.env[key] = value
            self.assertNotEqual(self.clean().returncode, 0)
            self.assertTrue((self.cache / "nested/bytes").exists())
            self.assertFalse(list(self.profile.glob(".claude-cache.*")))
            self.env.pop(key)

    def test_unknown_pass_is_retried_and_a_settled_pass_counts(self):
        # Right after a build a process can be caught mid-exec: the helper
        # repeats the pass instead of refusing at once.
        self.env["MOCK_PS_FAIL_PASSES"] = "2"
        result = subprocess.run([str(self.helper), "desktop-idle"], env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stderr, "")

    def test_unknown_on_every_pass_stays_unknown_and_names_the_process(self):
        self.env["MOCK_PS_FAIL_PASSES"] = "3"
        result = subprocess.run([str(self.helper), "desktop-idle"], env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stderr, "process 4242 (fixture) changed while observed\n")
        self.env["MOCK_PS_FAIL_PASSES"] = "2"
        self.assertEqual(self.clean().returncode, 0)

    def test_native_clean_requires_inherited_lock_and_restricts_profile_paths(self):
        result = subprocess.run([str(self.helper), "clean", str(self.apps), str(self.profile)],
                                env=self.env, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("inherited transaction lock", result.stderr)
        self.profile = self.root  # Owned, but not a permitted Desktop profile.
        self.assertNotEqual(self.clean().returncode, 0)
        self.assertTrue((self.cache / "nested/bytes").exists())

    def test_profile_replaced_by_link_after_observation_is_not_followed(self):
        process = self.stop("checked")
        original = self.profile.with_name("original")
        self.profile.rename(original)
        victim = self.root / "victim"
        (victim / "Cache").mkdir(parents=True)
        (victim / "Cache/keep").write_text("outside")
        self.profile.symlink_to(victim, target_is_directory=True)
        process.send_signal(signal.SIGCONT)
        process.communicate(timeout=5)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual((victim / "Cache/keep").read_text(), "outside")
        self.assertTrue((original / "Cache/nested/bytes").exists())

    def test_new_live_cache_symlink_does_not_redirect_quarantined_deletion(self):
        process = self.stop("quarantined")
        victim = self.root / "victim"
        victim.mkdir()
        (victim / "keep").write_text("outside")
        self.cache.symlink_to(victim, target_is_directory=True)
        process.send_signal(signal.SIGCONT)
        _, errors = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0, errors)
        self.assertTrue(self.cache.is_symlink())
        self.assertEqual((victim / "keep").read_text(), "outside")

    def test_replaced_quarantined_child_is_refused_without_following_link(self):
        process = self.stop("quarantined")
        quarantine = next(self.profile.glob(".claude-cache.*"))
        moved = self.root / "moved"
        (quarantine / "Cache/nested").rename(moved)
        victim = self.root / "victim"
        victim.mkdir()
        (victim / "keep").write_text("outside")
        (quarantine / "Cache/nested").symlink_to(victim, target_is_directory=True)
        process.send_signal(signal.SIGCONT)
        process.communicate(timeout=5)
        self.assertNotEqual(process.returncode, 0)
        self.assertTrue(quarantine.exists())
        self.assertEqual((victim / "keep").read_text(), "outside")
        self.assertTrue((moved / "bytes").exists())

    def test_sigkill_after_quarantine_retains_cache_and_releases_lock(self):
        process = self.stop("quarantined")
        self.kill(process)
        quarantine = next(self.profile.glob(".claude-cache.*"))
        self.assertTrue((quarantine / "Cache/nested/bytes").exists())
        result = subprocess.run([str(self.helper), "run", str(self.apps), "/dev/null"], env=self.env,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_process_running_a_deleted_executable_is_unknown_and_named(self):
        # Claude's updater moves the old bundle away and deletes it; a helper it
        # had started (Chrome's native host) keeps running the deleted file.
        old = self.root / "Old.app"
        executable = old / "Contents/Helpers/duo-stale-probe"
        executable.parent.mkdir(parents=True)
        source = self.root / "wait.c"
        source.write_text('#include <unistd.h>\nint main(void) { char c; write(1,"READY\\n",6); return read(0,&c,1)<0; }\n')
        subprocess.run(["/usr/bin/clang", str(source), "-o", str(executable)], check=True)
        process = subprocess.Popen([str(executable)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: self.kill(process))
        self.assertTrue(select.select([process.stdout], [], [], 5)[0])
        self.assertEqual(process.stdout.readline(), b"READY\n")
        (self.root / "staged").mkdir()
        old.rename(self.root / "staged/Old.app")
        shutil.rmtree(self.root / "staged/Old.app")
        result = subprocess.run([str(self.production), "desktop-idle"], env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn(f"process {process.pid} (duo-stale-probe) runs a deleted executable; quit it and retry\n", result.stderr)
        process.communicate(b"x", timeout=5)
        result = subprocess.run([str(self.production), "desktop-idle"], env=self.env, text=True, capture_output=True)
        self.assertNotIn("duo-stale-probe", result.stderr)

    def test_zombie_process_does_not_make_observation_unknown(self):
        # A child that exited and was never reaped, like the helpers Chrome
        # leaves behind: it runs no executable, so it cannot be the app. Newer
        # macOS answers proc_pidinfo for it with ESRCH while kill(pid, 0) works.
        process = subprocess.Popen(["/usr/bin/true"])
        self.addCleanup(process.wait)
        state = ""
        for _ in range(100):
            state = subprocess.run(["/bin/ps", "-o", "stat=", "-p", str(process.pid)],
                                   capture_output=True, text=True).stdout.strip()
            if state.startswith("Z"):
                break
            time.sleep(0.05)
        self.assertTrue(state.startswith("Z"), state)
        for command in (["desktop-idle"], ["app-state", str(self.apps / "Claude Synthetic.app")]):
            result = subprocess.run([str(self.production), *command], env=self.env, text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 2, result.stderr)
            self.assertNotIn(f"process {process.pid} ", result.stderr)

    def test_real_native_pid_path_observation_detects_only_selected_app_without_env_reads(self):
        app = self.apps / "Claude Synthetic.app"
        executable = app / "Contents/MacOS/Claude"
        executable.parent.mkdir(parents=True)
        source = self.root / "wait.c"
        source.write_text('#include <unistd.h>\nint main(void) { char c; write(1,"READY\\n",6); return read(0,&c,1)<0; }\n')
        subprocess.run(["/usr/bin/clang", str(source), "-o", str(executable)], check=True)
        process = subprocess.Popen([str(executable)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: self.kill(process))
        self.assertTrue(select.select([process.stdout], [], [], 5)[0])
        self.assertEqual(process.stdout.readline(), b"READY\n")
        self.env.update({"MOCK_PS_FAIL": "1", "MOCK_RUNNING_APP": "ignored in production"})
        for target, expected in ((app, 0), (self.apps / "Claude Syntheti.app", 1)):
            result = subprocess.run([str(self.production), "app-state", str(target)], env=self.env,
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, expected, result.stderr)
        process.communicate(b"x", timeout=5)
        result = subprocess.run([str(self.production), "app-state", str(app)], env=self.env,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 1, result.stderr)
