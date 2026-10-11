"""Synthetic temporary-path provisioning regressions; run POSIX cases as root."""
import base64
import hashlib
import importlib.util
import json
import os
if os.name == "posix":
    import grp
    import pwd
from pathlib import Path
import stat
import shlex
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy/provision_command_signing_key.py"
NAME = "XIDER_COMMAND_VERIFY_KEYSET_B64"


def b64(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def public_entry(key):
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return {hashlib.sha256(raw).hexdigest()[:16]: b64(raw)}


def keyset(entries):
    return b64(json.dumps(entries, sort_keys=True, separators=(",", ":")).encode())


@unittest.skipUnless(os.name == "posix" and os.geteuid() == 0, "requires POSIX root temporary fixture")
class ProvisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="xider-command-key-test-")
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.key = self.directory / "command-signing-private.pem"
        self.env = self.directory / "bot.env"
        self.env.write_bytes(b"# keep\r\nSHARED_KEY=synthetic-secret\r\nDEVICE_ID=untouched\r\n")
        self.env.chmod(0o640)
        self.gid = pwd.getpwnam("nobody").pw_gid
        self.group = grp.getgrgid(self.gid).gr_name

    def run_install(self, succeeds=True):
        result = subprocess.run([sys.executable, str(SCRIPT), "--key-path", str(self.key),
                                 "--env-file", str(self.env), "--group", self.group],
                                capture_output=True, text=True)
        if succeeds:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("BEGIN PRIVATE KEY", result.stdout + result.stderr)
        if self.key.is_file() and not self.key.is_symlink():
            self.assertNotIn(self.key.read_text(), result.stdout + result.stderr)
        return result

    def write_key(self, key=None):
        key = key or Ed25519PrivateKey.generate()
        self.key.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption()))
        return key

    def add_keyset(self, value):
        with self.env.open("a") as env:
            env.write(f"{NAME}={value}\n")

    def assert_preserved_failure(self):
        before_key = self.key.read_bytes() if self.key.is_file() else None
        before_env = self.env.read_bytes()
        self.run_install(False)
        self.assertEqual(self.env.read_bytes(), before_env)
        if before_key is not None:
            self.assertEqual(self.key.read_bytes(), before_key)
        else:
            self.assertFalse(self.key.exists())

    def test_first_install_permissions_public_only_and_repeat_preserves_key(self):
        self.run_install()
        private = self.key.read_bytes()
        key = serialization.load_pem_private_key(private, password=None)
        self.assertIsInstance(key, Ed25519PrivateKey)
        self.assertEqual(stat.S_IMODE(self.key.stat().st_mode), 0o640)
        self.assertEqual((self.key.stat().st_uid, self.key.stat().st_gid), (0, self.gid))
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o750)
        self.assertEqual(self.directory.stat().st_gid, self.gid)
        content = self.env.read_bytes()
        self.assertTrue(content.startswith(b"# keep\r\nSHARED_KEY=synthetic-secret\r\nDEVICE_ID=untouched\r\n"))
        self.assertNotIn(private, content)
        self.assertNotIn(b"PRIVATE KEY", content)
        self.assertEqual(stat.S_IMODE(self.env.stat().st_mode), 0o640)
        self.assertEqual(self.env.read_text().split(f"{NAME}=")[1].strip(), keyset(public_entry(key)))
        spec = importlib.util.spec_from_file_location("test_agent_key_contract", ROOT / "XGENT-MCS/command_signing.py")
        verifier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(verifier)
        with unittest.mock.patch.dict(os.environ, {NAME: keyset(public_entry(key))}):
            self.assertTrue(verifier.trusted_command_keys_ready())
        self.run_install()
        self.assertEqual(self.key.read_bytes(), private)
        self.assertEqual(self.env.read_bytes(), content)

    def test_existing_overlap_is_preserved(self):
        current = self.write_key()
        entries = public_entry(Ed25519PrivateKey.generate()) | public_entry(current)
        self.add_keyset(keyset(entries))
        before = self.key.read_bytes()
        self.run_install()
        self.assertEqual(self.key.read_bytes(), before)
        self.assertIn(f"{NAME}={keyset(entries)}", self.env.read_text())

    def test_add_current_public_key_without_discarding_previous(self):
        current = self.write_key()
        old = public_entry(Ed25519PrivateKey.generate())
        self.add_keyset(keyset(old))
        self.run_install()
        self.assertIn(f"{NAME}={keyset(old | public_entry(current))}", self.env.read_text())

    def test_invalid_existing_private_material_is_never_replaced(self):
        for material in (b"malformed synthetic key", generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())):
            with self.subTest(kind="malformed" if material.startswith(b"malformed") else "rsa"):
                self.key.write_bytes(material)
                self.assert_preserved_failure()

    def test_symlink_key_and_env_refused(self):
        target = self.directory / "target"
        target.write_text("do not change")
        self.key.symlink_to(target)
        self.run_install(False)
        self.assertEqual(target.read_text(), "do not change")
        self.key.unlink()
        self.env.unlink()
        self.env.symlink_to(target)
        self.run_install(False)
        self.assertEqual(target.read_text(), "do not change")
        self.assertFalse(self.key.exists())

    def test_malformed_mismatched_duplicate_noncanonical_overlimit_keysets_refused(self):
        self.write_key()
        entries = public_entry(Ed25519PrivateKey.generate())
        encoded_public = next(iter(entries.values()))
        invalid = ["broken==", "", keyset({"0" * 16: encoded_public}),
                   keyset(entries | public_entry(Ed25519PrivateKey.generate()) | public_entry(Ed25519PrivateKey.generate())),
                   b64(json.dumps(entries, indent=1).encode()),
                   b64((f'{{"{next(iter(entries))}":"{encoded_public}","{next(iter(entries))}":"{encoded_public}"}}').encode()),
                   keyset(entries | public_entry(Ed25519PrivateKey.generate()))]
        for value in invalid:
            with self.subTest(value_index=invalid.index(value)):
                self.env.write_text("UNRELATED=keep\n")
                self.add_keyset(value)
                self.assert_preserved_failure()
        self.env.write_text("UNRELATED=keep\n")
        self.add_keyset(keyset(public_entry(serialization.load_pem_private_key(self.key.read_bytes(), None))))
        self.add_keyset(keyset(entries))
        self.assert_preserved_failure()

    def test_invalid_keyset_on_first_install_creates_no_key(self):
        self.add_keyset("malformed")
        self.assert_preserved_failure()

    def test_atomic_env_failure_preserves_old_environment_and_repeat_recovers(self):
        spec = importlib.util.spec_from_file_location("provision_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        original = self.env.read_bytes()
        with unittest.mock.patch.object(module.os, "replace", side_effect=OSError("synthetic atomic failure")):
            with self.assertRaises(OSError):
                module.provision(self.key, self.env, group=self.group)
        self.assertEqual(self.env.read_bytes(), original)
        private = self.key.read_bytes()
        self.run_install()
        self.assertEqual(self.key.read_bytes(), private)
        self.assertFalse(list(self.directory.glob("*.tmp")))

    def test_same_key_id_different_public_bytes_refused_without_mutation(self):
        key = self.write_key()
        spec = importlib.util.spec_from_file_location("provision_collision_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        key_id = next(iter(public_entry(key)))
        self.add_keyset(keyset(public_entry(key)))
        before = self.env.read_bytes()
        with unittest.mock.patch.object(module, "_public_keyset", return_value={key_id: b64(b"x" * 32)}):
            with self.assertRaises(ValueError):
                module.provision(self.key, self.env, group=self.group)
        self.assertEqual(self.env.read_bytes(), before)

    def test_duplicate_export_env_and_unsafe_file_shapes_refused(self):
        key = self.write_key()
        value = keyset(public_entry(key))
        self.env.write_text(f"export {NAME}='{value}'\n  {NAME} = {value}\n")
        self.assert_preserved_failure()
        self.env.write_text("UNRELATED=keep\n")
        self.key.unlink()
        self.key.mkdir()
        self.run_install(False)
        self.assertTrue(self.key.is_dir())
        self.key.rmdir()
        os.mkfifo(self.key)
        self.run_install(False)
        self.key.unlink()
        key = self.write_key()
        os.link(self.key, self.directory / "hardlink")
        self.assert_preserved_failure()

    def test_permission_repair_preserves_private_bytes_and_env_permissions(self):
        self.write_key()
        private = self.key.read_bytes()
        self.key.chmod(0o666)
        os.chown(self.key, self.gid, self.gid)
        self.env.chmod(0o600)
        self.run_install()
        self.assertEqual(self.key.read_bytes(), private)
        self.assertEqual(stat.S_IMODE(self.key.stat().st_mode), 0o640)
        self.assertEqual(self.key.stat().st_uid, 0)
        self.assertEqual(stat.S_IMODE(self.env.stat().st_mode), 0o600)

    def test_bom_no_final_newline_preserves_unrelated_content(self):
        self.env.write_bytes(b"\xef\xbb\xbfUNRELATED=keep")
        self.run_install()
        self.assertTrue(self.env.read_bytes().startswith(b"\xef\xbb\xbfUNRELATED=keep\n"))

    def test_concurrent_installers_preserve_one_key_and_one_public_entry(self):
        processes = [subprocess.Popen([sys.executable, str(SCRIPT), "--key-path", str(self.key),
                                     "--env-file", str(self.env), "--group", self.group],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(5)]
        for process in processes:
            stdout, stderr = process.communicate(timeout=20)
            self.assertEqual(process.returncode, 0, stderr.decode())
            self.assertNotIn(b"PRIVATE KEY", stdout + stderr)
        self.assertEqual(self.env.read_text().count(NAME + "="), 1)
        key = serialization.load_pem_private_key(self.key.read_bytes(), None)
        self.assertIn(keyset(public_entry(key)), self.env.read_text())

    def test_server_install_provisions_before_service_restart(self):
        # Execute the real installer tail only: package management, sudoers,
        # account creation, and production directories are outside this fixture.
        source = Path(os.environ.get("XIDER_TEST_SERVER_INSTALL_SOURCE", ROOT / "deploy/server-install.sh")).read_text()
        tail = source[source.index('chmod 0640 "${ENV_FILE}"'):]
        app = self.directory / "app"
        python = app / "TG-BOT-SERVER/venv/bin/python"
        python.parent.mkdir(parents=True)
        python.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + " " + shlex.quote(str(SCRIPT)) +
                          " --key-path " + shlex.quote(str(self.key)) + " --group " + shlex.quote(self.group) +
                          ' --env-file "$ENV_FILE"\n')
        python.chmod(0o755)
        binary = self.directory / "bin"
        binary.mkdir()
        systemctl = binary / "systemctl"
        systemctl.write_text('#!/bin/sh\nif [ "$1" = restart ]; then\n' +
                              '  test -f "$KEY_PATH" && grep -q "^XIDER_COMMAND_VERIFY_KEYSET_B64=" "$ENV_FILE" || exit 77\n' +
                              'fi\n')
        systemctl.chmod(0o755)
        environment = os.environ | {"APP_DIR": str(app), "ENV_FILE": str(self.env), "KEY_PATH": str(self.key),
                                   "PATH": str(binary) + ":" + os.environ["PATH"]}
        result = subprocess.run(["bash", "-Eeuo", "pipefail", "-c", tail], env=environment,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.key.is_file())

    def test_interrupted_key_publication_recovers_exact_inode_and_fsyncs_cleanup(self):
        key = self.write_key()
        self.key.chmod(0o640)
        os.chown(self.key, 0, self.gid)
        private_digest = hashlib.sha256(self.key.read_bytes()).digest()
        original_inode = self.key.stat().st_ino
        orphan = self.directory / f".{self.key.name}.abcdef12.tmp"
        os.link(self.key, orphan)
        self.assertEqual(self.key.stat().st_nlink, 2)
        spec = importlib.util.spec_from_file_location("provision_crash_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        real_sync = module._sync_directory
        cleanup_syncs = []

        def observe_sync(path):
            real_sync(path)
            if not orphan.exists() and self.key.stat().st_nlink == 1:
                cleanup_syncs.append(NAME not in self.env.read_text())

        with unittest.mock.patch.object(module, "_sync_directory", side_effect=observe_sync):
            actual_id = module.provision(self.key, self.env, group=self.group)
        self.assertEqual(actual_id, next(iter(public_entry(key))))
        self.assertEqual(self.key.stat().st_ino, original_inode)
        self.assertEqual(hashlib.sha256(self.key.read_bytes()).digest(), private_digest)
        self.assertEqual(self.key.stat().st_nlink, 1)
        self.assertFalse(orphan.exists())
        self.assertIn(True, cleanup_syncs, "orphan cleanup was not fsynced before public env publication")
        self.assertIn(keyset(public_entry(key)), self.env.read_text())
        self.run_install()
        self.assertEqual(hashlib.sha256(self.key.read_bytes()).digest(), private_digest)

    def test_normal_key_publication_fsyncs_after_removing_temp_alias(self):
        spec = importlib.util.spec_from_file_location("provision_sync_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        key = Ed25519PrivateKey.generate()
        private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption())
        real_sync = module._sync_directory
        link_counts_at_sync = []

        def observe_sync(path):
            real_sync(path)
            link_counts_at_sync.append(self.key.stat().st_nlink)

        with unittest.mock.patch.object(module, "_sync_directory", side_effect=observe_sync):
            module._atomic_file(self.key, private, mode=0o640, uid=0, gid=self.gid, create_only=True)
        self.assertEqual(link_counts_at_sync[-1], 1, "last directory fsync preceded alias removal")
        self.assertFalse(list(self.directory.glob(f".{self.key.name}.*.tmp")))

    def test_unrelated_or_unsafe_hardlinks_are_not_recovered(self):
        self.write_key()
        self.key.chmod(0o640)
        os.chown(self.key, 0, self.gid)
        for suffix in ("unrelated", f".{self.key.name}.bad-name.tmp"):
            with self.subTest(shape=suffix):
                alias = self.directory / suffix
                os.link(self.key, alias)
                self.assert_preserved_failure()
                self.assertTrue(alias.is_file())
                alias.unlink()
        orphan = self.directory / f".{self.key.name}.abcdef12.tmp"
        os.link(self.key, orphan)
        self.key.chmod(0o666)
        self.assert_preserved_failure()
        self.assertTrue(orphan.exists())
        self.key.chmod(0o640)
        external = self.directory / "unrelated-third-link"
        os.link(self.key, external)
        self.assert_preserved_failure()
        self.assertTrue(orphan.exists())
        self.assertTrue(external.exists())


if __name__ == "__main__":
    import unittest.mock
    unittest.main(verbosity=2)
