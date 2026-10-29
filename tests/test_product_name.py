"""Production productName change for the managed copy; synthetic archives only."""
import json
import plistlib
import resource
import signal
import struct
import subprocess
import unittest

import test_hardening as hardening


def unpack(blob):
    _, size, _, raw = struct.unpack_from("<4I", blob)
    header = json.loads(blob[16:16 + raw])
    payload = blob[8 + size:]
    entry = header["files"]["package.json"]
    start = int(entry["offset"])
    return header, payload[start:start + entry["size"]], hardening.sha256(blob[16:16 + raw])


def repack(header, payload):
    raw = json.dumps(header, separators=(",", ":")).encode()
    padding = bytes((-len(raw)) % 4)
    size = 8 + len(raw) + len(padding)
    return struct.pack("<4I", 4, size, size - 4, len(raw)) + raw + padding + payload, hardening.sha256(raw)


def split(archive):
    _, size, _, raw = struct.unpack_from("<4I", archive)
    return json.loads(archive[16:16 + raw]), archive[8 + size:]


class ProductNameTests(unittest.TestCase):
    def setUp(self):
        self.fixture = hardening.HardeningTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.set_mode()
        self.archive = self.fixture.source / "Contents/Resources/app.asar"

    def app_archive(self):
        return self.fixture.app / "Contents/Resources/app.asar"

    def plist(self, app):
        return plistlib.loads((app / "Contents/Info.plist").read_bytes())

    def write_source(self, archive, header):
        self.archive.write_bytes(archive)
        info = self.plist(self.fixture.source)
        info["ElectronAsarIntegrity"] = {"Resources/app.asar": {"algorithm": "SHA256", "hash": header}}
        (self.fixture.source / "Contents/Info.plist").write_bytes(plistlib.dumps(info))

    def set_source(self, package=None, members=None, header_hash=None, block_size=4194304):
        archive, header = hardening.synthetic_asar(package, members, block_size)
        self.archive.write_bytes(archive)
        info = self.plist(self.fixture.source)
        info["ElectronAsarIntegrity"] = {"Resources/app.asar": {"algorithm": "SHA256", "hash": header_hash or header}}
        (self.fixture.source / "Contents/Info.plist").write_bytes(plistlib.dumps(info))

    def refused(self, message):
        result = self.fixture.run_script()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(message, result.stderr)
        self.assertFalse(self.fixture.app.exists())
        return result

    def test_copy_gets_own_product_name_with_same_size_and_sealed_digests(self):
        before = self.archive.read_bytes()
        self.fixture.build()
        self.assertEqual(self.fixture.run_script("--verify").returncode, 0)
        after = self.app_archive().read_bytes()
        self.assertEqual(len(after), len(before))
        self.assertEqual(self.archive.read_bytes(), before)  # Source untouched.
        header, package, header_hash = unpack(after)
        self.assertEqual(json.loads(package)["productName"], "One Claude")
        self.assertEqual(json.loads(package), {**json.loads(unpack(before)[1]), "productName": "One Claude"})
        integrity = header["files"]["package.json"]["integrity"]
        self.assertEqual(integrity["hash"], hardening.sha256(package))
        self.assertEqual(integrity["blocks"], [hardening.sha256(package)])
        self.assertEqual(self.plist(self.fixture.app)["ElectronAsarIntegrity"]["Resources/app.asar"]["hash"], header_hash)
        old_header, _, _ = unpack(before)
        self.assertEqual(old_header["files"]["index.js"], header["files"]["index.js"])
        self.assertEqual(old_header["files"]["node_modules"], header["files"]["node_modules"])
        self.assertFalse((self.fixture.app / "Contents/Resources/.app.asar.mcd-new").exists())

    def test_second_sealed_archive_is_copied_with_its_integrity_entry_unchanged(self):
        # Claude 4.29 seals Resources/ion-dist.asar next to app.asar.
        extra = {"algorithm": "SHA256", "hash": "ab" * 32}
        info = self.plist(self.fixture.source)
        info["ElectronAsarIntegrity"]["Resources/ion-dist.asar"] = extra
        (self.fixture.source / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
        self.fixture.build()
        self.assertEqual(self.fixture.run_script("--verify").returncode, 0)
        self.assertEqual(self.plist(self.fixture.app)["ElectronAsarIntegrity"]["Resources/ion-dist.asar"], extra)

    def test_unreviewed_integrity_entries_are_refused(self):
        for key, value in [("Frameworks/x.asar", {"algorithm": "SHA256", "hash": "ab" * 32}),
                           ("Resources/ion-dist.asar", {"algorithm": "SHA512", "hash": "ab" * 32})]:
            with self.subTest(key=key):
                info = self.plist(self.fixture.source)
                info["ElectronAsarIntegrity"] = {"Resources/app.asar": info["ElectronAsarIntegrity"]["Resources/app.asar"], key: value}
                (self.fixture.source / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
                self.refused("unreviewed ElectronAsarIntegrity")

    def test_product_name_keeps_a_desktop_user_agent_token(self):
        # Electron 44 builds "<app.name without spaces>/<version>" into the user
        # agent and names the Keychain item "<app.name> Safe Storage". claude.ai
        # enables its desktop UI only for /claude(nest|gov)?\/([^ ]+)/i and
        # indexOf("claude/"); "Claude One" -> "ClaudeOne/" fails both.
        import re
        self.fixture.build()
        name = json.loads(unpack(self.app_archive().read_bytes())[1])["productName"]
        token = name.replace(" ", "") + "/1.0.0"
        agent = "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 (KHTML, like Gecko) " + token + " Chrome/1 Electron/44.7.2 Safari/537.36"
        self.assertRegex(agent, re.compile(r"claude(nest|gov)?\/([^ ]+)", re.I))
        self.assertIn("claude/", agent.lower())
        self.assertNotEqual(name + " Safe Storage", "Claude Safe Storage")
        self.assertEqual(name.strip(), name)  # Electron trims productName.

    def test_legacy_copies_keep_the_archive_byte_identical(self):
        self.fixture.write_config()
        self.fixture.build()
        self.assertEqual(self.app_archive().read_bytes(), self.archive.read_bytes())
        self.assertEqual(self.fixture.run_script("--verify").returncode, 0)

    def test_post_build_archive_or_plist_digest_tampering_is_detected(self):
        self.fixture.build()
        original = self.app_archive().read_bytes()
        javascript = original.index(b"synthetic vendor code")
        for label, tampered in [("javascript", original[:javascript] + b"S" + original[javascript + 1:]),
                                ("product", original.replace(b'"One Claude"', b'"Two Claude"'))]:
            with self.subTest(label=label):
                self.app_archive().write_bytes(tampered)
                rejected = self.fixture.run_script("--verify")
                self.assertNotEqual(rejected.returncode, 0)
                self.assertIn("drift", rejected.stderr)
                self.app_archive().write_bytes(original)
                self.assertEqual(self.fixture.run_script("--verify").returncode, 0)
        plist = self.fixture.app / "Contents/Info.plist"
        info = self.plist(self.fixture.app)
        info["ElectronAsarIntegrity"]["Resources/app.asar"]["hash"] = "0" * 64
        plist.write_bytes(plistlib.dumps(info))
        self.assertNotEqual(self.fixture.run_script("--verify").returncode, 0)

    def test_tampering_between_writer_and_record_is_refused_before_publication(self):
        # The record regenerates the archive from the source; it never trusts
        # what the writer left in staging, nor a substituted header digest.
        pb = self.fixture.bin / "pb"
        pb.write_text('''#!/bin/bash
if [[ "$2" == "Set :ElectronAsarIntegrity:"* ]]; then
 archive="${3%/Contents/Info.plist}/Contents/Resources/app.asar"
 case "${MOCK_ASAR_TAMPER:-}" in
  js) /usr/bin/perl -0pi -e 's/synthetic vendor code/synthetic vendor CODE/' "$archive" ;;
  digest) exec /usr/libexec/PlistBuddy -c "Set :ElectronAsarIntegrity:Resources/app.asar:hash 0000000000000000000000000000000000000000000000000000000000000000" "$3" ;;
 esac
fi
exec /usr/libexec/PlistBuddy "$@"
''')
        for mode, message in [("js", "differs beyond the planned productName change"), ("digest", "Info.plist differs beyond planned changes")]:
            with self.subTest(mode=mode):
                self.fixture.env["MOCK_ASAR_TAMPER"] = mode
                self.refused(message)

    def test_unreviewed_source_packages_are_refused_without_publication(self):
        cases = [
            ("other name", b'{\n  "productName": "Claude Beta",\n  "name": "x"\n}\n', "unreviewed source productName"),
            ("duplicate", b'{\n  "productName": "Claude",\n  "productName": "Claude",\n  "name": "x"\n}\n', "exactly one productName"),
            ("escaped key", b'{\n  "productName": "Claude",\n  "product\\u004eame": "Claude"\n}\n', "escaped package.json keys"),
            ("nested only", b'{\n  "build": {\n    "productName": "Claude"\n  }\n}\n', "exactly one productName"),
            ("no whitespace", b'{"name":"x","productName":"Claude"}', "not enough insignificant whitespace"),
            ("value type", b'{\n  "productName": ["Claude"],\n  "name": "x"\n}\n', "unreviewed source productName"),
        ]
        for label, package, message in cases:
            with self.subTest(label=label):
                self.set_source(package)
                self.refused(message)

    def test_unreviewed_archive_layouts_are_refused_without_publication(self):
        self.set_source(header_hash="1" * 64)
        self.refused("does not match its archive header")
        archive, _ = hardening.synthetic_asar()
        header, payload = split(archive)
        digest = header["files"]["package.json"]["integrity"]
        digest["hash"] = digest["blocks"][0] = "2" * 64
        with self.subTest(label="source digest"):
            self.write_source(*repack(header, payload))
            self.refused("integrity mismatch")
        header, payload = split(archive)
        header["files"]["package.json"] = {"unpacked": True, **header["files"]["package.json"]}
        with self.subTest(label="unpacked"):
            self.write_source(*repack(header, payload))
            self.refused("unsupported ASAR package.json entry layout")
        self.archive.unlink()
        self.refused("app.asar")

    def test_package_payload_shared_with_another_member_is_refused(self):
        package = b'{\n  "productName": "Claude",\n  "name": "x"\n}\n'
        archive, _ = hardening.synthetic_asar(package, {"alias.json": package})
        header, payload = split(archive)
        header["files"]["package.json"]["offset"] = header["files"]["alias.json"]["offset"]
        self.write_source(*repack(header, payload))
        self.refused("cannot share its payload region")

    def test_multi_block_package_digests_are_all_resealed(self):
        package = b'{\n  "productName": "Claude",\n' + b'  "pad": "' + b"x" * 90 + b'"\n}\n'
        self.set_source(package, block_size=32)
        self.fixture.build()
        header, data, _ = unpack(self.app_archive().read_bytes())
        blocks = header["files"]["package.json"]["integrity"]["blocks"]
        self.assertEqual(blocks, [hardening.sha256(data[i:i + 32]) for i in range(0, len(data), 32)])
        self.assertEqual(self.fixture.run_script("--verify").returncode, 0)

    def test_stale_staged_archive_from_an_interrupted_writer_is_refused(self):
        (self.fixture.source / "Contents/Resources/.app.asar.mcd-new").write_bytes(b"partial")
        self.refused("stale staged archive present")
        self.fixture.write_config()  # Legacy record also refuses the leftover.
        self.refused("unexpected staged archive")

    def compile_helper(self):
        helper = self.fixture.root / "integrity-direct"
        subprocess.run(["/usr/bin/clang", "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2", "-mmacosx-version-min=12.0",
                        "-framework", "CoreFoundation", str(hardening.REPO / "bin/claude-integrity.c"), "-o", str(helper)], check=True)
        stage = self.fixture.root / "stage.app"
        (stage / "Contents/Resources").mkdir(parents=True)
        (stage / "Contents/Resources/app.asar").write_bytes(self.archive.read_bytes())
        (stage / "Contents/Resources/mcd-build-id").write_text("01234567-89ab-cdef-0123-456789abcdef\n")
        return helper, stage

    def test_interrupted_write_leaves_the_staged_archive_and_no_partial_file(self):
        helper, stage = self.compile_helper()
        original = (stage / "Contents/Resources/app.asar").read_bytes()

        def limit():
            signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
            resource.setrlimit(resource.RLIMIT_FSIZE, (len(original) // 2, len(original) // 2))

        failed = subprocess.run([str(helper), "product-name", str(stage), "One"], preexec_fn=limit,
                                capture_output=True, text=True, timeout=20)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("staged archive write failed", failed.stderr)
        self.assertEqual((stage / "Contents/Resources/app.asar").read_bytes(), original)
        self.assertFalse((stage / "Contents/Resources/.app.asar.mcd-new").exists())
        (stage / "Contents/Resources").chmod(0o500)
        self.addCleanup((stage / "Contents/Resources").chmod, 0o700)
        denied = subprocess.run([str(helper), "product-name", str(stage), "One"], capture_output=True, text=True, timeout=20)
        self.assertNotEqual(denied.returncode, 0)
        self.assertEqual((stage / "Contents/Resources/app.asar").read_bytes(), original)

    def test_writer_refuses_unvalidated_names(self):
        helper, stage = self.compile_helper()
        for name in ['One"', "One\\", "Ünï", "a b"]:
            with self.subTest(name=name):
                result = subprocess.run([str(helper), "product-name", str(stage), name], capture_output=True, text=True, timeout=20)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("validated instance name", result.stderr)

    def test_source_update_requires_and_supports_a_reviewed_rebuild(self):
        self.fixture.build()
        self.set_source(members={"index.js": b"console.log('updated vendor code');\n"})
        info = self.plist(self.fixture.source)
        info["CFBundleShortVersionString"] = "2.0.fixture"
        (self.fixture.source / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
        self.assertIn("rebuild required", self.fixture.run_script("--status").stdout)
        self.fixture.build()
        self.assertEqual(self.fixture.run_script("--verify").returncode, 0)
        rebuilt = self.app_archive().read_bytes()
        self.assertIn(b"updated vendor code", rebuilt)
        self.assertEqual(json.loads(unpack(rebuilt)[1])["productName"], "One Claude")


if __name__ == "__main__":
    unittest.main()
