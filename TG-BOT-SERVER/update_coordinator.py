"""Measured update stages and a fresh-health gate for canary rollout."""
from dataclasses import dataclass, replace
import threading
import uuid

ACTIVE = frozenset({'queued', 'downloading', 'verifying', 'installing', 'reconnecting', 'health_check', 'rolling_back', 'unknown'})
TERMINAL = frozenset({'succeeded', 'failed', 'rolled_back'})
STATES = ACTIVE | TERMINAL
NEXT = {'queued': 'downloading', 'downloading': 'verifying', 'verifying': 'installing',
        'installing': 'reconnecting', 'reconnecting': 'health_check', 'health_check': 'succeeded',
        'rolling_back': 'rolled_back'}
FRESH_SECONDS = 120


@dataclass(frozen=True)
class UpdateOperation:
    operation_id: str
    batch_id: str
    device_id: str
    release_tag: str
    component: str
    state: str
    changed_at: int
    detail: str


def transition(operation: UpdateOperation, event: str, now: int) -> UpdateOperation:
    if event == operation.state or operation.state in TERMINAL or now < operation.changed_at:
        return operation
    allowed = event == NEXT.get(operation.state)
    allowed |= event == 'timeout' and operation.state in ACTIVE
    allowed |= event == 'failed' and operation.state in ACTIVE - {'unknown'}
    allowed |= event == 'rolling_back' and operation.state in {'installing', 'reconnecting', 'health_check'}
    return replace(operation, state='unknown' if event == 'timeout' else event, changed_at=now) if allowed else operation


class Coordinator:
    def __init__(self, store):
        self.store = store
        self._lock = threading.RLock()
        # Persisted success alone is never authority to unlock a rollout.
        self._health = {}

    def start_batch(self, device_ids, release_tag, component, *, now):
        with self._lock:
            ids = list(dict.fromkeys(device_ids))
            if not ids or len(ids) > 100:
                raise ValueError('invalid batch size')
            for op in self.store.all():
                batch = self.store.batch(op.batch_id)
                if ([item.device_id for item in batch] == ids and op.release_tag == release_tag and op.component == component
                        and (any(item.state in ACTIVE for item in batch) or now - self.store.started_at(batch[0].operation_id) < 180)):
                    return batch
            if any(op.device_id in ids and op.state in ACTIVE for op in self.store.all()):
                raise ValueError('device update already active')
            batch_id = uuid.uuid4().hex
            operations = [UpdateOperation(uuid.uuid4().hex, batch_id, device, release_tag, component, 'queued', int(now), '') for device in ids]
            self.store.save_many(operations)
            return operations

    def batch_state(self, batch_id):
        batch = self.store.batch(batch_id)
        if not self.store.trusted or not batch:
            return 'unknown'
        if any(op.state in {'failed', 'rolled_back', 'unknown'} for op in batch):
            return 'blocked'
        if all(op.state == 'succeeded' for op in batch):
            return 'succeeded' if all(op.operation_id in self._health for op in batch) else 'unknown'
        return 'active'

    def observed(self, operation_id, *, now):
        op = self.store.get(operation_id)
        if op and op.state == 'succeeded':
            stamp = self._health.get(operation_id)
            if stamp is None or not 0 <= now - stamp <= FRESH_SECONDS:
                return replace(op, state='unknown')
        return op

    def dispatch_ready(self, batch_id, publish, *, now):
        with self._lock:
            batch = self.store.batch(batch_id)
            if not self.store.trusted or not batch or self.batch_state(batch_id) == 'blocked':
                return []
            canary = batch[0]
            fresh_health = self._health.get(canary.operation_id)
            unlocked = canary.state == 'succeeded' and fresh_health is not None and 0 <= now - fresh_health <= FRESH_SECONDS
            candidates = batch if unlocked else [canary]
            sent = []
            for op in candidates:
                if op.state != 'queued' or self.store.is_published(op.operation_id):
                    continue
                # Persist intent before side effect. Ambiguous send cannot retry.
                self.store.mark_published(op.operation_id)
                try:
                    ok = publish(op)
                except Exception:
                    self.store.save(transition(op, 'timeout', int(now)))
                    raise
                if not ok:
                    self.store.save(transition(op, 'timeout', int(now)))
                    break
                sent.append(op)
            return sent

    def _bound(self, device_id, payload):
        if not isinstance(payload, dict):
            return None
        op = self.store.get(payload.get('operation_id'))
        if not self.store.trusted or not op or not self.store.is_published(op.operation_id) or op.device_id != device_id:
            return None
        for name, expected in (('device_id', device_id), ('release_tag', op.release_tag), ('component', op.component)):
            if name in payload and payload[name] != expected:
                return None
        return op

    def handle_event(self, device_id, payload, *, received_at):
        with self._lock:
            op = self._bound(device_id, payload)
            if not op:
                return False
            event = payload.get('update_state')
            # Success requires the durable health receipt, never a progress event.
            if event == 'succeeded':
                return False
            updated = transition(op, event, int(received_at))
            if updated == op:
                return event == op.state
            self.store.save(updated)
            return True

    def reconcile(self, device_id, payload, *, received_at, heartbeat_at):
        with self._lock:
            op = self._bound(device_id, payload)
            if not op or payload.get('release_tag') != op.release_tag or payload.get('component') != op.component:
                return False
            updated_at = payload.get('updated_at')
            if type(updated_at) not in {int, float} or type(heartbeat_at) not in {int, float}:
                return False
            if updated_at < self.store.started_at(op.operation_id):
                return False
            if not (0 <= received_at - updated_at <= FRESH_SECONDS and 0 <= received_at - heartbeat_at <= FRESH_SECONDS):
                return False
            event = payload.get('update_state')
            if event not in {'health_check', 'succeeded', 'failed', 'rolled_back', 'rolling_back', 'reconnecting'}:
                return False
            if op.state in {'failed', 'rolled_back'} or (op.state == 'succeeded' and event != 'succeeded'):
                return False
            # Exact operation receipt is the post-restart reconciliation proof.
            self.store.save(replace(op, state=event, changed_at=int(received_at), detail=''))
            if event == 'succeeded':
                self._health[op.operation_id] = received_at
            return True

    def heartbeat_lost(self, device_id, *, now):
        with self._lock:
            for op in self.store.all():
                if op.device_id == device_id:
                    self._health.pop(op.operation_id, None)
                    if self.store.is_published(op.operation_id) and op.state in ACTIVE:
                        self.store.save(transition(op, 'timeout', int(now)))

    def timeout(self, operation_id, *, now):
        with self._lock:
            op = self.store.get(operation_id)
            if op and self.store.is_published(operation_id):
                self.store.save(transition(op, 'timeout', int(now)))

    def expire(self, *, now, timeout=300):
        with self._lock:
            for op in self.store.all():
                if self.store.is_published(op.operation_id) and op.state in ACTIVE - {'unknown'} and now - op.changed_at >= timeout:
                    self.store.save(transition(op, 'timeout', int(now)))
