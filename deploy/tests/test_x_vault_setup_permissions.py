"""Linux-only local fixtures; never touch system accounts, units, or SSH state.

The setup entrypoints are copied with their absolute installation paths mapped
into a temporary directory. Only getent's shell field and service commands are
mocked; install/chown/chmod/runuser and the kernel's UID/group/ACL checks are real.
Run with: sudo python3 deploy/tests/test_x_vault_setup_permissions.py
"""
from __future__ import annotations

import ctypes
import ctypes.util
import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

if sys.platform.startswith("linux"):
    import grp
    import pwd


ROOT = Path(__file__).resolve().parents[2]
LINUX_ROOT = sys.platform.startswith("linux") and hasattr(os, "geteuid") and os.geteuid() == 0


@unittest.skipUnless(LINUX_ROOT, "real SSH UID/group/ACL semantics require Linux root")
class VaultSetupPermissionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="xider-vault-permissions-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.root.chmod(0o755)
        self.user = next(
            (entry for entry in pwd.getpwall()
             if entry.pw_name in {"www-data", "daemon", "nobody"}
             and entry.pw_uid != 0
             and grp.getgrgid(entry.pw_gid).gr_name == entry.pw_name),
            None,
        )
        if self.user is None:
            self.skipTest("fixture requires an existing non-root user with its own primary group")
        for tool in ("runuser", "ssh-keygen", "bash", "install"):
            if not shutil.which(tool):
                self.skipTest(f"fixture requires {tool}")
        self.app = self.root / "source"
        self.bin = self.root / "bin"
        self.bin.mkdir(mode=0o755)
        self.log = self.root / "service-calls"
        self.env = dict(os.environ, XIDER_APP_DIR=str(self.app), PATH=f"{self.bin}:{os.environ['PATH']}")
        self.key = self.root / "upload-key"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(self.key)], check=True)
        self.authorized = self.root / "etc/ssh/authorized_keys" / self.user.pw_name
        self.receiver = self.root / "usr/local/libexec/xider-vault-upload/x_vault_transport.py"
        for relative in (
            "deploy/x-vault-promote.sh", "deploy/promote_x_vault.py", "ops/x_vault_transport.py",
            "deploy/xider-vault-promote.service", "deploy/xider-vault-promote.timer",
            "deploy/xider-vault-upload.sshd.conf", "ops/x_vault.py",
            "deploy/x-vault-backup.sh", "deploy/xider-vault-backup.service",
            "deploy/xider-vault-backup.timer",
        ):
            destination = self.app / relative
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            shutil.copyfile(ROOT / relative, destination)
        for relative in ("etc/ssh/sshd_config.d", "etc/systemd/system", "etc/tmpfiles.d", "usr/local/sbin", "run", "srv"):
            (self.root / relative).mkdir(parents=True, exist_ok=True, mode=0o755)

        # No user is created or modified. A real existing low-privilege account
        # supplies kernel access semantics; its irrelevant login shell is mocked.
        passwd_line = ":".join((self.user.pw_name, "x", str(self.user.pw_uid), str(self.user.pw_gid), "fixture", "/incoming", "/bin/sh"))
        self._tool("getent", f'if [[ "$1" == passwd && "$2" == {self.user.pw_name} ]]; then\n  printf \'%s\\n\' \'{passwd_line}\'\nelse\n  exec /usr/bin/getent "$@"\nfi\n')
        effective = f"forcecommand /usr/bin/python3 {self.receiver} receive\nauthorizedkeysfile {self.authorized}"
        self._tool("sshd", f'if [[ "$1" == -T ]]; then printf \'%s\\n\' \'{effective}\'; fi\n')
        for name in ("systemctl", "systemd-analyze", "systemd-tmpfiles"):
            self._tool(name, f'printf \'%s\\n\' \'{name}\' >>\'{self.log}\'\n')
        for name in ("useradd", "groupadd"):
            self._tool(name, 'echo "Fixture must not create system accounts" >&2\nexit 93\n')
        self.standby = self._mapped_script("xider-vault-standby-setup.sh")
        self.primary = self._mapped_script("xider-vault-primary-setup.sh")

    def _tool(self, name, body):
        path = self.bin / name
        path.write_text("#!/usr/bin/env bash\nset -Eeuo pipefail\n" + body)
        path.chmod(0o755)

    def _mapped_script(self, name):
        source = (ROOT / "deploy" / name).read_text()
        # Map every host installation prefix; no literal system path may remain.
        for prefix in ("/srv/xider-vault", "/etc/ssh", "/etc/xider", "/etc/tmpfiles.d", "/etc/systemd/system", "/usr/local/sbin", "/usr/local/libexec", "/run/xider-vault-upload.lock"):
            source = source.replace(prefix, str(self.root) + prefix)
        if name.startswith("xider-vault-standby"):
            source = source.replace('readonly account="xvault-upload"', f'readonly account="{self.user.pw_name}"')
        target = self.root / name
        target.write_text(source)
        return target

    def _standby(self, preflight=False):
        args = ["bash", str(self.standby)]
        if preflight:
            args.append("--preflight-only")
        args.append(str(self.key) + ".pub")
        return subprocess.run(args, env=self.env, capture_output=True, text=True)

    def _install(self):
        result = self._standby()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.log.write_text("")

    def _snapshot(self):
        result = {}
        for path in self.root.rglob("*"):
            metadata = path.lstat()
            result[str(path.relative_to(self.root))] = (
                metadata.st_uid, metadata.st_gid, stat.S_IMODE(metadata.st_mode), metadata.st_mtime_ns,
                hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None,
            )
        return result

    def _uploader_access(self, write=False):
        # Match sshd's temporarily_use_uid: initialize supplementary groups,
        # then adopt primary GID and UID before opening the root-owned key.
        code = (
            "import os,pwd,sys; p=pwd.getpwnam(sys.argv[1]); "
            "os.initgroups(p.pw_name,p.pw_gid); os.setgid(p.pw_gid); os.setuid(p.pw_uid); "
            "f=open(sys.argv[2],sys.argv[3]); f.close()"
        )
        return subprocess.run([sys.executable, "-c", code, self.user.pw_name, str(self.authorized), "a" if write else "r"], capture_output=True)

    def test_installed_key_root_owned_group_readable_and_not_uploader_writable(self):
        self._install()
        parent = self.receiver.parent.parent.stat()
        self.assertEqual((parent.st_uid, parent.st_gid, stat.S_IMODE(parent.st_mode)), (0, 0, 0o755))
        metadata = self.authorized.stat()
        self.assertEqual((metadata.st_uid, metadata.st_gid, stat.S_IMODE(metadata.st_mode)), (0, self.user.pw_gid, 0o640))
        self.assertEqual(self._uploader_access().returncode, 0)
        self.assertNotEqual(self._uploader_access(write=True).returncode, 0)
        before = self._snapshot()
        result = self._standby(preflight=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no SSH login was attempted", result.stdout)
        self.assertEqual(self._snapshot(), before)
        self.assertEqual(self.log.read_text(), "")

    def test_existing_receiver_parent_wrong_mode_fails_without_taking_it_over(self):
        self._install()
        self.receiver.parent.parent.chmod(0o700)
        before = self._snapshot()
        for preflight in (True, False):
            with self.subTest(preflight=preflight):
                result = self._standby(preflight=preflight)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("refusing to take it over", result.stderr)
                self.assertEqual(self._snapshot(), before)
                self.assertEqual(self.log.read_text(), "")

    def test_setup_shell_syntax(self):
        for script in (self.primary, self.standby):
            result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_root_600_preflight_fails_without_mutation_then_same_key_migrates(self):
        self._install()
        original = self.authorized.read_bytes()
        os.chown(self.authorized, 0, 0)
        self.authorized.chmod(0o600)
        before = self._snapshot()
        self.assertNotEqual(self._standby(preflight=True).returncode, 0)
        self.assertEqual(self._snapshot(), before)
        self.assertNotEqual(self._uploader_access().returncode, 0)
        result = self._standby()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.authorized.read_bytes(), original)
        self.assertEqual(self.authorized.stat().st_uid, 0)
        self.assertEqual(self._uploader_access().returncode, 0)
        self.assertNotEqual(self._uploader_access(write=True).returncode, 0)

    def test_user_owned_or_writable_key_is_never_adopted(self):
        self._install()
        for uid, gid, mode in ((self.user.pw_uid, self.user.pw_gid, 0o600), (0, self.user.pw_gid, 0o660), (0, 0, 0o644)):
            with self.subTest(uid=uid, mode=oct(mode)):
                os.chown(self.authorized, uid, gid)
                self.authorized.chmod(mode)
                before = self.authorized.stat()
                self.log.write_text("")
                result = self._standby(preflight=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("mode 0640", result.stderr)
                result = self._standby()
                self.assertNotEqual(result.returncode, 0)
                after = self.authorized.stat()
                self.assertEqual((after.st_uid, after.st_gid, after.st_mode), (before.st_uid, before.st_gid, before.st_mode))
                self.assertNotIn("systemctl", self.log.read_text())

    def test_legacy_key_mismatch_refuses_permission_migration(self):
        self._install()
        os.chown(self.authorized, 0, 0)
        self.authorized.chmod(0o600)
        self.authorized.write_text("ssh-ed25519 wrong-key\n")
        self.log.write_text("")
        result = self._standby()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing to rotate", result.stderr)
        self.assertEqual((self.authorized.stat().st_gid, stat.S_IMODE(self.authorized.stat().st_mode)), (0, 0o600))
        self.assertNotIn("systemctl", self.log.read_text())

    def test_read_denied_acl_fails_even_with_correct_owner_group_mode(self):
        self._install()
        library = ctypes.util.find_library("acl")
        if library is None:
            self.skipTest("libacl unavailable")
        acl = ctypes.CDLL(library, use_errno=True)
        acl.acl_from_text.argtypes = [ctypes.c_char_p]
        acl.acl_from_text.restype = ctypes.c_void_p
        acl.acl_set_file.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p]
        acl.acl_set_file.restype = ctypes.c_int
        acl.acl_free.argtypes = [ctypes.c_void_p]
        text = f"user::rw-,user:{self.user.pw_uid}:---,group::r--,mask::r--,other::---".encode()
        value = acl.acl_from_text(text)
        self.assertTrue(value)
        try:
            self.assertEqual(acl.acl_set_file(os.fsencode(self.authorized), 0x8000, value), 0, os.strerror(ctypes.get_errno()))
        finally:
            acl.acl_free(value)
        self.assertEqual(stat.S_IMODE(self.authorized.stat().st_mode), 0o640)
        self.assertNotEqual(self._uploader_access().returncode, 0)
        before = self._snapshot()
        result = self._standby(preflight=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("inspect path permissions and ACLs", result.stderr)
        self.assertEqual(self._snapshot(), before)
        result = self._standby()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("inspect path permissions and ACLs", result.stderr)
        self.assertNotIn("systemctl", self.log.read_text())

    def test_missing_standby_state_preflight_never_creates_files_or_accounts(self):
        before = self._snapshot()
        result = self._standby(preflight=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("required directory is missing", result.stderr)
        self.assertEqual(self._snapshot(), before)
        self.assertFalse(self.log.exists())

    def test_primary_preflight_keeps_private_identity_root_only(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

        config_dir = self.root / "etc/xider"
        config_dir.mkdir(mode=0o750)
        config = config_dir / "x-vault-backup.env"
        config.write_text("XIDER_VAULT_TARGET=xvault-upload@standby.example\nXIDER_VAULT_REMOTE_DIR=/incoming\n")
        config.chmod(0o600)
        identity = config_dir / "vault-upload-key"
        shutil.copyfile(self.key, identity)
        identity.chmod(0o600)
        (config_dir / "vault-known-hosts").write_text("standby.example ssh-ed25519 AAAA\n")
        recipient = X25519PrivateKey.generate().public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        (config_dir / "vault-recipient-public.pem").write_bytes(recipient)
        before = self._snapshot()
        result = subprocess.run(["bash", str(self.primary), "--preflight-only"], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SSH authentication are not verified", result.stdout)
        self.assertEqual(self._snapshot(), before)
        self.assertFalse(self.log.exists())
        identity.chmod(0o640)
        result = subprocess.run(["bash", str(self.primary), "--preflight-only"], env=self.env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must not be accessible by group or others", result.stderr)
        identity.chmod(0o600)
        config.write_text("XIDER_VAULT_TARGET=root@standby.example\nXIDER_VAULT_REMOTE_DIR=/incoming\n")
        result = subprocess.run(["bash", str(self.primary), "--preflight-only"], env=self.env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("restricted xvault-upload account", result.stderr)


if __name__ == "__main__":
    unittest.main()
