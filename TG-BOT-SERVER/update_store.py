"""Atomic bounded update journal. Corrupt/missing journals cannot unlock batches."""
from dataclasses import asdict, replace
import json
import hashlib
import os
from pathlib import Path
import re
import tempfile
import threading

from update_coordinator import UpdateOperation, STATES, ACTIVE


class UpdateStore:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else Path(__file__).with_name('update_operations.json')
        self._lock = threading.RLock()
        self._operations = {}
        self._published = set()
        self._started = {}
        self._trusted = False
        self._digest = None
        self._corrupt = False
        try:
            if self.path.is_symlink() or self.path.stat().st_size > 8 * 1024 * 1024:
                raise ValueError('unsafe journal')
            raw = self.path.read_bytes()
            doc = json.loads(raw)
            if not isinstance(doc, dict) or doc.get('version') != 1 or not isinstance(doc.get('operations'), list) or not isinstance(doc.get('published'), list):
                raise ValueError('invalid journal')
            if len(doc['operations']) > 10000:
                raise ValueError('journal too large')
            for value in doc['operations']:
                op = self._validated(UpdateOperation(**value))
                if op.operation_id in self._operations:
                    raise ValueError('duplicate id')
                self._operations[op.operation_id] = op
            self._published = set(doc['published'])
            self._started = doc.get('started', {})
            if not isinstance(self._started, dict) or set(self._started) != set(self._operations) or any(type(stamp) is not int or stamp < 0 for stamp in self._started.values()):
                raise ValueError('invalid start times')
            if not self._published <= self._operations.keys():
                raise ValueError('unknown publish id')
            self._digest = hashlib.sha256(raw).digest()
            self._trusted = True
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, KeyError):
            self._operations = {}
            self._published = set()
            self._started = {}
            self._corrupt = True

    @property
    def trusted(self):
        with self._lock:
            if not self._trusted:
                return False
            try:
                return (not self.path.is_symlink() and self.path.stat().st_size <= 8 * 1024 * 1024
                        and hashlib.sha256(self.path.read_bytes()).digest() == self._digest)
            except OSError:
                return False

    @staticmethod
    def _validated(op):
        for value in (op.operation_id, op.batch_id, op.device_id, op.component):
            if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', value):
                raise ValueError('invalid update identity')
        if not isinstance(op.release_tag, str) or len(op.release_tag) > 96 or not re.fullmatch(r'v?\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?', op.release_tag):
            raise ValueError('invalid release tag')
        if op.state not in STATES or type(op.changed_at) is not int or not 0 <= op.changed_at <= 2**63 - 1:
            raise ValueError('invalid update state')
        # Details carry fixed state summaries only; agent output is never stored.
        return replace(op, detail='')

    def _write(self, operations, published):
        if self._corrupt or (self._trusted and not self.trusted):
            raise ValueError('corrupt update journal; recovery required')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        started = {key: self._started.get(key, op.changed_at) for key, op in operations.items()}
        doc = {'version': 1, 'operations': [asdict(op) for op in operations.values()], 'published': sorted(published), 'started': started}
        encoded = json.dumps(doc, ensure_ascii=False).encode('utf-8')
        if len(encoded) > 8 * 1024 * 1024:
            raise ValueError('journal full')
        fd, name = tempfile.mkstemp(prefix=self.path.name + '.', suffix='.tmp', dir=self.path.parent)
        tmp = Path(name)
        try:
            with os.fdopen(fd, 'wb') as output:
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())
            tmp.replace(self.path)
        finally:
            tmp.unlink(missing_ok=True)
        self._operations = operations
        self._published = published
        self._started = started
        self._digest = hashlib.sha256(self.path.read_bytes()).digest()
        self._trusted = True

    def save(self, operation):
        self.save_many([operation])

    def save_many(self, operations):
        with self._lock:
            candidate = dict(self._operations)
            for op in operations:
                old = candidate.get(op.operation_id)
                if old and any(getattr(old, field) != getattr(op, field) for field in ('batch_id', 'device_id', 'release_tag', 'component')):
                    raise ValueError('update identity is immutable')
                candidate[op.operation_id] = self._validated(op)
            if len(candidate) > 10000:
                raise ValueError('journal full')
            self._write(candidate, set(self._published))

    def mark_published(self, operation_id):
        with self._lock:
            if not self.trusted or operation_id not in self._operations:
                raise ValueError('unknown update')
            self._write(dict(self._operations), self._published | {operation_id})

    def is_published(self, operation_id):
        with self._lock:
            return self.trusted and operation_id in self._published

    def started_at(self, operation_id):
        with self._lock:
            return self._started.get(operation_id)

    def get(self, operation_id):
        with self._lock:
            return self._operations.get(operation_id) if self.trusted and isinstance(operation_id, str) else None

    def all(self):
        with self._lock:
            return list(self._operations.values())

    def batch(self, batch_id):
        return [op for op in self.all() if op.batch_id == batch_id]

    def list_active(self, batch_id):
        return [op for op in self.batch(batch_id) if op.state in ACTIVE]
