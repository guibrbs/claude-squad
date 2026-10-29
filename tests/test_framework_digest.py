"""Electron 44.9 integrity-dictionary digest in the framework; synthetic Mach-O only."""
import hashlib
import plistlib
import struct
import unittest

import test_hardening as hardening

SENTINEL = b"AGbevlPCksUGKNL8TSn7wGmJEuJsXb2A"
FRAMEWORK = "Contents/Frameworks/Electron Framework.framework/Versions/A/Electron Framework"
HELPER = "Contents/Frameworks/Claude Helper (GPU).app/Contents/MacOS/Claude Helper (GPU)"


def dictionary_digest(integrity):
    h = hashlib.sha256()
    for key in sorted(integrity):
        h.update(key.encode() + integrity[key]["algorithm"].encode() + integrity[key]["hash"].encode())
    return h.digest()


def macho(code, slot=None, signature=b"vendor-signature"):
    """Thin 64-bit Mach-O: [__DATA_CONST,__asar_integrity], __LINKEDIT, LC_CODE_SIGNATURE."""
    def segment(name, sections):
        body = b"".join(sections)
        return struct.pack("<II16sQQQQIIII", 0x19, 72 + len(body), name, 0, 0, 0, 0, 3, 3, len(sections), 0) + body
    slot_bytes = slot or b""
    commands_size = 72 + (80 if slot else 0) + 72 + 16
    slot_at = 32 + commands_size + len(code)
    data_end = slot_at + len(slot_bytes)
    section = struct.pack("<16s16sQQIIIIIIII", b"__asar_integrity", b"__DATA_CONST", 0, len(slot_bytes), slot_at, 0, 0, 0, 0, 0, 0, 0)
    commands = (segment(b"__DATA_CONST", [section] if slot else [])
                + segment(b"__LINKEDIT", [])
                + struct.pack("<IIII", 0x1D, 16, data_end, len(signature)))
    header = struct.pack("<IiiIIIII", 0xFEEDFACF, 0x0100000C, 0, 6, 3, len(commands), 0, 0)
    return header + commands + code + slot_bytes + signature


def slot(used=1, version=1, digest=bytes(32)):
    return SENTINEL + bytes([used, version]) + digest + bytes(6)


# Simulates `codesign --force -s -`: replaces only the trailing signature and the
# two size fields that describe it, as the system signer does. With
# MOCK_RESIGN_TAMPER the signer also changes a code byte, which must be refused.
RESIGN = r'''
import os, struct, sys
path = sys.argv[1]
if path.endswith(".framework"):
    path += "/Versions/A/Electron Framework"
elif path.endswith(".app") and "/Contents/Frameworks/" in path:
    name = os.path.basename(path)[:-4]
    path += "/Contents/MacOS/" + name
else:
    sys.exit(0)
with open(path, "r+b") as f:
    b = bytearray(f.read())
    ncmds, at = struct.unpack_from("<I", b, 16)[0], 32
    for _ in range(ncmds):
        cmd, size = struct.unpack_from("<II", b, at)
        if cmd == 0x19 and b[at + 8:at + 18] == b"__LINKEDIT":
            linkedit = at
        if cmd == 0x1D:
            signature = at
        at += size
    offset = struct.unpack_from("<I", b, signature + 8)[0]
    new = b"adhoc-local-signature"
    b = b[:offset] + new
    struct.pack_into("<I", b, signature + 12, len(new))
    struct.pack_into("<QQ", b, linkedit + 32 + 0, len(new), 0)
    struct.pack_into("<Q", b, linkedit + 48, len(new))
    if os.environ.get("MOCK_RESIGN_TAMPER") == os.path.basename(path):
        b[40] ^= 1
    f.seek(0); f.truncate(); f.write(b)
with open(os.environ["MOCK_SIGN_LOG"], "a") as log:
    log.write(os.path.basename(path) + "\n")
'''


class FrameworkDigestTests(unittest.TestCase):
    def setUp(self):
        self.fixture = hardening.HardeningTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.set_mode()
        self.log = self.fixture.root / "sign.log"
        self.fixture.env["MOCK_SIGN_LOG"] = str(self.log)
        (self.fixture.bin / "resign.py").write_text(RESIGN)
        mock = self.fixture.bin / "codesign"
        resign = '[ "$1" != --force ] || /usr/bin/python3 -I "' + str(self.fixture.bin / "resign.py") + '" "${@: -1}" || exit 1\n'
        mock.write_text(mock.read_text().rsplit("exit 0\n", 1)[0] + resign + "exit 0\n")

    def info(self, app):
        return plistlib.loads((app / "Contents/Info.plist").read_bytes())

    def add_framework(self, used=1, version=1, digest=None):
        source = self.fixture.source
        if digest is None:
            digest = dictionary_digest(self.info(source)["ElectronAsarIntegrity"])
        framework = source / FRAMEWORK
        framework.parent.mkdir(parents=True)
        framework.write_bytes(macho(b"synthetic framework code" * 4, slot(used, version, digest)))
        framework.chmod(0o755)
        (framework.parent / "_CodeSignature").mkdir()
        (framework.parent / "_CodeSignature/CodeResources").write_text("vendor framework resources")
        (framework.parents[1] / "Current").symlink_to("A")
        helper = source / HELPER
        helper.parent.mkdir(parents=True)
        helper.write_bytes(macho(b"synthetic helper code" * 4))
        helper.chmod(0o755)
        (helper.parents[1] / "_CodeSignature").mkdir()
        (helper.parents[1] / "_CodeSignature/CodeResources").write_text("vendor helper resources")

    def signed(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def refused(self, message):
        result = self.fixture.run_script()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(message, result.stderr)
        self.assertFalse(self.fixture.app.exists())

    def test_framework_digest_follows_the_copy_integrity_dictionary(self):
        self.add_framework()
        before = (self.fixture.source / FRAMEWORK).read_bytes()
        self.fixture.build()
        self.assertEqual(self.fixture.run_script("--verify").returncode, 0)
        self.assertEqual((self.fixture.source / FRAMEWORK).read_bytes(), before)  # Source untouched.
        app = self.fixture.app
        copy = (app / FRAMEWORK).read_bytes()
        at = copy.index(SENTINEL)
        expected = dictionary_digest(self.info(app)["ElectronAsarIntegrity"])
        self.assertNotEqual(expected, before[at + 34:at + 66])
        self.assertEqual(copy[at + 34:at + 66], expected)
        self.assertEqual(copy[at:at + 34], before[at:at + 34])
        self.assertEqual(copy[at + 66:at + 72], before[at + 66:at + 72])
        self.assertTrue(copy.endswith(b"adhoc-local-signature"))
        self.assertEqual(sorted(self.signed()), sorted(["Electron Framework", "Claude Helper (GPU)"]))

    def test_unused_slot_leaves_framework_and_helpers_byte_identical(self):
        self.add_framework(used=0, digest=bytes(32))
        self.fixture.build()
        self.assertEqual(self.fixture.run_script("--verify").returncode, 0)
        for path in (FRAMEWORK, HELPER):
            self.assertEqual((self.fixture.app / path).read_bytes(), (self.fixture.source / path).read_bytes())
        self.assertEqual(self.signed(), [])

    def test_legacy_copies_never_reseal_the_framework(self):
        self.add_framework()
        self.fixture.write_config()
        self.fixture.build()
        self.assertEqual((self.fixture.app / FRAMEWORK).read_bytes(), (self.fixture.source / FRAMEWORK).read_bytes())
        self.assertEqual(self.signed(), [])

    def test_code_changed_while_resigning_is_refused_before_publication(self):
        for target in ("Electron Framework", "Claude Helper (GPU)"):
            with self.subTest(target=target):
                if not (self.fixture.source / FRAMEWORK).exists():
                    self.add_framework()
                self.fixture.env["MOCK_RESIGN_TAMPER"] = target
                self.refused("re-signed code differs beyond its signature and integrity digest")

    def test_unreviewed_slot_version_is_refused(self):
        self.add_framework(version=2)
        self.refused("unreviewed integrity digest slot version")

    def test_post_build_slot_tampering_is_detected(self):
        self.add_framework()
        self.fixture.build()
        path = self.fixture.app / FRAMEWORK
        copy = bytearray(path.read_bytes())
        copy[copy.index(SENTINEL) + 40] ^= 1
        path.write_bytes(bytes(copy))
        rejected = self.fixture.run_script("--verify")
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("drift", rejected.stderr)


if __name__ == "__main__":
    unittest.main()
