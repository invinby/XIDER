#!/usr/bin/env python3
"""Standard-library tests for the bounded SSH stream receiver."""

from __future__ import annotations

import io
import os
import signal
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

try:
    import fcntl
except ImportError:
    import pytest

    pytest.skip("bounded X-VAULT transport uses Linux flock and open flags", allow_module_level=True)


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ops"))
import x_vault_transport as transport  # noqa: E402
from x_vault_transport import MAGIC, UploadError, receive_upload, send_archive  # noqa: E402


class VaultTransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="xider-vault-transport-")
        self.root = Path(self.tmp.name)
        self.incoming = self.root / "incoming"
        self.incoming.mkdir(mode=0o700)
        os.chmod(self.incoming, 0o700)
        self.lock = self.root / "upload.lock"
        self.lock.touch(mode=0o660)
        os.chmod(self.lock, 0o660)

    def tearDown(self):
        self.tmp.cleanup()

    def frame(self, payload: bytes) -> io.BytesIO:
        return io.BytesIO(len(payload).to_bytes(8, "big") + payload)

    def receive(self, payload: bytes, *, limit=1024, queue=2048) -> str:
        return receive_upload(
            self.frame(payload),
            self.incoming,
            self.lock,
            max_archive_bytes=limit,
            max_incoming_bytes=queue,
        )

    def test_accepts_complete_archive_and_generates_safe_name(self):
        payload = MAGIC + b"encrypted-test-payload"
        name = self.receive(payload)

        self.assertRegex(name, r"^xvault-[0-9]{8}T[0-9]{6}Z-[a-f0-9]{6}\.xvlt$")
        self.assertEqual((self.incoming / name).read_bytes(), payload)
        self.assertEqual(os.stat(self.incoming / name).st_mode & 0o777, 0o400)
        self.assertEqual(len(list(self.incoming.iterdir())), 1)

    def test_rejects_oversized_declaration_before_creating_a_file(self):
        stream = io.BytesIO((1025).to_bytes(8, "big"))
        with self.assertRaisesRegex(UploadError, "outside the receiver limit"):
            receive_upload(stream, self.incoming, self.lock, max_archive_bytes=1024, max_incoming_bytes=2048)
        self.assertEqual(list(self.incoming.iterdir()), [])

    def test_rejects_queue_overflow_before_creating_a_file(self):
        existing = self.incoming / "xvault-20261001T000000Z-abcdef.xvlt"
        existing.write_bytes(b"x" * 150)
        os.chmod(existing, 0o400)
        with self.assertRaisesRegex(UploadError, "inbox is full"):
            self.receive(MAGIC + b"x" * 50, limit=100, queue=200)
        self.assertEqual({p.name for p in self.incoming.iterdir()}, {existing.name})

    def test_truncated_stream_removes_partial_file(self):
        stream = io.BytesIO((len(MAGIC) + 10).to_bytes(8, "big") + MAGIC + b"short")
        with self.assertRaisesRegex(UploadError, "truncated"):
            receive_upload(stream, self.incoming, self.lock, max_archive_bytes=100, max_incoming_bytes=200)
        self.assertEqual(list(self.incoming.iterdir()), [])

    def test_rejects_bad_magic_and_trailing_bytes_without_publication(self):
        with self.assertRaisesRegex(UploadError, "header"):
            self.receive(b"NOTVAULT" + b"payload")
        with self.assertRaisesRegex(UploadError, "extra bytes"):
            receive_upload(
                io.BytesIO((len(MAGIC) + 1).to_bytes(8, "big") + MAGIC + b"xextra"),
                self.incoming,
                self.lock,
                max_archive_bytes=100,
                max_incoming_bytes=200,
            )
        self.assertEqual(list(self.incoming.iterdir()), [])

    def test_waiting_receiver_refuses_to_race_with_promoter(self):
        lock_fd = os.open(self.lock, os.O_RDWR)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(UploadError, "still running"):
                receive_upload(
                    self.frame(MAGIC + b"x"),
                    self.incoming,
                    self.lock,
                    max_archive_bytes=100,
                    max_incoming_bytes=200,
                    lock_wait_seconds=0,
                )
        finally:
            os.close(lock_fd)
        self.assertEqual(list(self.incoming.iterdir()), [])

    def test_sender_writes_length_prefix_and_exact_file_bytes(self):
        archive = self.root / "source.xvlt"
        payload = MAGIC + b"ciphertext"
        archive.write_bytes(payload)
        output = io.BytesIO()

        send_archive(archive, output)

        self.assertEqual(output.getvalue(), len(payload).to_bytes(8, "big") + payload)

    @unittest.skipUnless(hasattr(signal, "SIGALRM"), "POSIX transfer deadline")
    def test_receiver_deadline_rejects_an_idle_authenticated_stream(self):
        class BlockingInput:
            def read(self, _count):
                time.sleep(2)

        class FakeStdin:
            buffer = BlockingInput()

        errors = io.StringIO()
        with (
            mock.patch.object(transport, "MAX_TRANSFER_SECONDS", 1),
            mock.patch.object(transport.sys, "stdin", FakeStdin()),
            mock.patch.object(transport.sys, "argv", ["x_vault_transport.py", "receive"]),
            redirect_stderr(errors),
        ):
            self.assertEqual(transport.main(), 1)
        self.assertIn("one-hour deadline", errors.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
