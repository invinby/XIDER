"""Signature and metadata gates use temporary bundles and a codesign boundary double."""
import importlib.util
import pathlib
import plistlib
import subprocess
import tempfile
import unittest

class BundleVerifierTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = pathlib.Path(self.tmp.name) / "XIDER.app"
        (self.app / "Contents/MacOS").mkdir(parents=True)
        (self.app / "Contents/MacOS/XGENT-MCS").write_bytes(b"fixture")
        self.info = {"CFBundleIdentifier": "com.xider.agent", "CFBundleExecutable": "XGENT-MCS",
                     "NSLocationUsageDescription": "One explicit request after local permission."}
        self.identity = "Identifier=com.xider.agent\nAuthority=Apple Development: Test (TEAM)\nTeamIdentifier=TEAM\n"
        self.verify_code = 0
        self.notary_code = 0
        self.calls = []
    def verify(self, distribution=False):
        with (self.app / "Contents/Info.plist").open("wb") as stream:
            plistlib.dump(self.info, stream)
        path = pathlib.Path(__file__).with_name("verify_macos_bundle.py")
        self.assertTrue(path.exists(), "macOS bundle verifier is missing")
        spec = importlib.util.spec_from_file_location("bundle_verifier_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        def run(args, **kwargs):
            self.calls.append(args)
            if "--verify" in args:
                return subprocess.CompletedProcess(args, self.verify_code, "", "invalid" if self.verify_code else "")
            if args[0] == "xcrun":
                return subprocess.CompletedProcess(args, self.notary_code, "", "")
            return subprocess.CompletedProcess(args, 0, "", self.identity)
        return module.verify_bundle(self.app, distribution=distribution, run=run)
    def test_proper_development_identity_is_valid_only_for_local_acceptance(self):
        self.assertEqual(self.verify(), [])
        self.assertTrue(any("--deep" in call and "--strict" in call for call in self.calls))
    def test_missing_identifier_fails_closed(self):
        self.info.pop("CFBundleIdentifier")
        self.assertTrue(self.verify())
    def test_wrong_identifier_fails_closed(self):
        self.info["CFBundleIdentifier"] = "com.other.agent"
        self.assertTrue(self.verify())
    def test_missing_usage_description_fails_closed(self):
        self.info.pop("NSLocationUsageDescription")
        self.assertTrue(self.verify())
    def test_blank_usage_description_fails_closed(self):
        self.info["NSLocationUsageDescription"] = " "
        self.assertTrue(self.verify())
    def test_invalid_deep_strict_signature_fails_closed(self):
        self.verify_code = 1
        self.assertTrue(self.verify())
    def test_ad_hoc_signature_is_rejected(self):
        self.identity = "Identifier=com.xider.agent\nSignature=adhoc\nTeamIdentifier=not set"
        self.assertTrue(self.verify())
    def test_codesign_identity_must_match_plist(self):
        self.identity = self.identity.replace("Identifier=com.xider.agent", "Identifier=com.other.agent")
        self.assertTrue(self.verify())
    def test_development_identity_cannot_be_distributed(self):
        self.assertTrue(self.verify(distribution=True))
    def test_distribution_requires_stapled_developer_id(self):
        self.identity = "Identifier=com.xider.agent\nAuthority=Developer ID Application: Test (TEAM)\nTeamIdentifier=TEAM"
        self.assertEqual(self.verify(distribution=True), [])
        self.assertTrue(any(call[:3] == ["xcrun", "stapler", "validate"] for call in self.calls))

    def test_release_credentials_missing_or_wrong_identity_blocks_gate(self):
        path = pathlib.Path(__file__).with_name("verify_macos_bundle.py")
        spec = importlib.util.spec_from_file_location("credential_gate", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        gate = getattr(module, "verify_release_credentials", None)
        self.assertTrue(callable(gate), "Missing release credential gate")
        self.assertTrue(gate({}))
        values = {key: "fixture" for key in ("APPLE_CERTIFICATE_B64", "APPLE_CERTIFICATE_PASSWORD",
                  "APPLE_KEYCHAIN_PASSWORD", "APPLE_ID", "APPLE_TEAM_ID", "APPLE_APP_PASSWORD")}
        values["XIDER_MACOS_SIGNING_IDENTITY"] = "Developer ID Application: Fixture (TEAM)"
        self.assertEqual(gate(values), [])
        values["XIDER_MACOS_SIGNING_IDENTITY"] = "-"
        self.assertTrue(gate(values))
        values["XIDER_MACOS_SIGNING_IDENTITY"] = "Apple Development: Fixture (TEAM)"
        self.assertTrue(gate(values))

    def test_distribution_rejects_invalid_notarization_ticket(self):
        self.identity = "Identifier=com.xider.agent\nAuthority=Developer ID Application: Test (TEAM)\nTeamIdentifier=TEAM"
        self.notary_code = 1
        self.assertTrue(self.verify(distribution=True))

if __name__ == "__main__":
    unittest.main()
