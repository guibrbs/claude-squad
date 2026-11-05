"""The managed copy's claude-dev:// links (@links), in synthetic bundles only."""
import plistlib
import unittest

import test_hardening as hardening

EXPECTED = [{"CFBundleURLName": "Claude One links", "CFBundleURLSchemes": ["claude-dev"]}]


class LinksTests(unittest.TestCase):
    def setUp(self):
        self.fixture = hardening.HardeningTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.rows = self.fixture.config.read_text()
        # The stock app declares claude:// (and more); the copy must never inherit it.
        info = plistlib.loads((self.fixture.source / "Contents/Info.plist").read_bytes())
        info["CFBundleURLTypes"] = [{"CFBundleURLName": "Claude", "CFBundleURLSchemes": ["claude"]}]
        (self.fixture.source / "Contents/Info.plist").write_bytes(plistlib.dumps(info))

    def configure(self, *headers):
        self.fixture.config.write_text("".join(h + "\n" for h in headers) + self.rows)

    def info(self):
        return plistlib.loads((self.fixture.app / "Contents/Info.plist").read_bytes())

    def test_links_register_only_claude_dev_and_the_record_verifies_it(self):
        self.configure("@mode original-copy", "@links claude-dev")
        self.fixture.build()
        self.assertEqual(self.info()["CFBundleURLTypes"], EXPECTED)
        self.assertNotIn("NSUserActivityTypes", self.info())
        verified = self.fixture.run_script("--verify")
        self.assertEqual(verified.returncode, 0, verified.stderr)

    def test_without_links_the_copy_declares_no_scheme(self):
        for headers in (("@mode original-copy",), ("@mode original-copy", "@links none")):
            with self.subTest(headers=headers):
                self.configure(*headers)
                self.fixture.build()
                self.assertNotIn("CFBundleURLTypes", self.info())
                self.assertEqual(self.fixture.run_script("--verify").returncode, 0)

    def test_turning_links_on_or_off_requires_a_rebuild(self):
        self.configure("@mode original-copy")
        self.fixture.build()
        self.configure("@mode original-copy", "@links claude-dev")
        self.assertNotEqual(self.fixture.run_script("--verify").returncode, 0)
        self.fixture.build()
        self.assertEqual(self.fixture.run_script("--verify").returncode, 0)
        self.configure("@mode original-copy")
        self.assertNotEqual(self.fixture.run_script("--verify").returncode, 0)

    def test_a_scheme_other_than_the_planned_one_is_refused_before_publication(self):
        self.configure("@mode original-copy", "@links claude-dev")
        pb = self.fixture.bin / "pb"
        # A builder that wrote claude:// instead: the record regenerates the
        # expected plist from the source and refuses the difference.
        pb.write_text('#!/bin/bash\nargs=()\nfor a in "$@"; do args+=("${a/string claude-dev/string claude}"); done\n'
                      'exec /usr/libexec/PlistBuddy "${args[@]}"\n')
        result = self.fixture.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Info.plist differs", result.stderr)
        self.assertFalse(self.fixture.app.exists())

    def test_tampering_with_the_url_types_after_the_build_is_detected(self):
        self.configure("@mode original-copy", "@links claude-dev")
        self.fixture.build()
        info = self.info()
        info["CFBundleURLTypes"][0]["CFBundleURLSchemes"].append("claude")
        (self.fixture.app / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
        self.assertNotEqual(self.fixture.run_script("--verify").returncode, 0)

    def test_links_are_config_data_with_one_reviewed_value(self):
        for headers in (("@links claude-dev",),  # legacy copies: several apps would fight over the scheme
                        ("@mode original-copy", "@links claude"),
                        ("@mode original-copy", "@links claude-duo"),
                        ("@mode original-copy", "@links claude-dev extra"),
                        ("@mode original-copy", "@links $(touch marker)"),
                        ("@mode original-copy", "@links claude-dev", "@links claude-dev")):
            with self.subTest(headers=headers):
                self.configure(*headers)
                result = self.fixture.run_script("--list")
                self.assertNotEqual(result.returncode, 0)
        self.fixture.config.write_text("@mode original-copy\n" + self.rows + "@links claude-dev\n")
        self.assertNotEqual(self.fixture.run_script("--list").returncode, 0)
        self.assertFalse((self.fixture.root / "marker").exists())

    def two_copies(self, *headers):
        f = self.fixture
        rows = [("One", "com.audit.one", str(f.profile("one")), "142", "CLAUDE_CONFIG_DIR=" + str(f.code("one"))),
                ("Two", "com.audit.two", str(f.profile("two")), "212", "CLAUDE_CONFIG_DIR=" + str(f.code("two")))]
        f.config.write_text("".join(h + "\n" for h in headers) + "".join(" | ".join(r) + "\n" for r in rows))

    def info_of(self, name):
        return plistlib.loads((self.fixture.apps / f"Claude {name}.app/Contents/Info.plist").read_bytes())

    def test_with_several_copies_only_the_named_one_takes_the_links(self):
        self.two_copies("@mode original-copy", "@links claude-dev two")
        self.fixture.build()
        self.assertNotIn("CFBundleURLTypes", self.info_of("One"))
        self.assertEqual(self.info_of("Two")["CFBundleURLTypes"],
                         [{"CFBundleURLName": "Claude Two links", "CFBundleURLSchemes": ["claude-dev"]}])
        self.assertEqual(self.fixture.run_script("--verify").returncode, 0)

    def test_with_several_copies_the_links_must_name_a_configured_copy(self):
        for headers, message in ((("@mode original-copy", "@links claude-dev"), "name the one that takes links"),
                                 (("@mode original-copy", "@links claude-dev Three"), "not configured"),
                                 (("@mode original-copy", "@links claude-dev One Two"), "invalid instance name")):
            with self.subTest(headers=headers):
                self.two_copies(*headers)
                result = self.fixture.run_script("--list")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)

    def test_naming_the_only_copy_is_the_same_as_not_naming_it(self):
        self.configure("@mode original-copy", "@links claude-dev")
        self.fixture.build()
        self.configure("@mode original-copy", "@links claude-dev ONE")
        self.assertEqual(self.fixture.run_script("--verify").returncode, 0)

    def test_moving_the_links_to_another_copy_requires_rebuilding_both(self):
        self.two_copies("@mode original-copy", "@links claude-dev One")
        self.fixture.build()
        self.two_copies("@mode original-copy", "@links claude-dev Two")
        result = self.fixture.run_script("--verify")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Claude One: FAILED", result.stderr)
        self.assertIn("Claude Two: FAILED", result.stderr)
        self.fixture.build()
        self.assertEqual(self.fixture.run_script("--verify").returncode, 0)


if __name__ == "__main__":
    unittest.main()
