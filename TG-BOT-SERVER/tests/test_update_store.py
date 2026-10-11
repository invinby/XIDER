import importlib
import json

import pytest


def test_atomic_reload_and_sanitized_bounded_details(tmp_path):
    from update_coordinator import UpdateOperation
    from update_store import UpdateStore
    path = tmp_path / 'updates.json'
    store = UpdateStore(path)
    op = UpdateOperation('op1', 'batch1', 'mac1', 'v4.2.0', 'macos', 'queued', 1000, 'token=secret ' + 'a' * 5000, manifest_digest='a' * 64)
    store.save(op)
    saved = UpdateStore(path).get('op1')
    assert saved.device_id == 'mac1'
    assert len(saved.detail) <= 512
    assert 'secret' not in path.read_text()
    assert not list(tmp_path.glob('*.tmp'))
    assert UpdateStore(path).list_active('batch1')[0].operation_id == 'op1'


def test_legacy_journal_without_manifest_digest_requires_explicit_recovery(tmp_path):
    from dataclasses import asdict
    from update_coordinator import UpdateOperation, Coordinator
    from update_store import UpdateStore
    path = tmp_path / 'updates.json'
    record = asdict(UpdateOperation('op1', 'batch1', 'mac1', 'v4.2.0', 'macos', 'succeeded', 1000, '', 'a' * 64))
    record.pop('manifest_digest')
    path.write_text(json.dumps({'version': 1, 'operations': [record], 'published': ['op1'], 'started': {'op1': 1000}}))
    original = path.read_bytes()
    store = UpdateStore(path)
    assert not store.trusted
    assert Coordinator(store).dispatch_ready('batch1', lambda op: pytest.fail('legacy rollout'), now=1001) == []
    with pytest.raises(ValueError):
        Coordinator(store).start_batch(['mac1'], 'v4.2.0', 'macos', now=1001, manifest_digest='a' * 64)
    assert path.read_bytes() == original


def test_failure_to_clear_unsent_intent_keeps_durable_no_retry_gate(tmp_path, monkeypatch):
    from pathlib import Path
    from update_coordinator import Coordinator
    from update_store import UpdateStore
    store = UpdateStore(tmp_path / 'updates.json')
    c = Coordinator(store)
    op = c.start_batch(['mac1'], 'v4.2.0', 'macos', now=1000, manifest_digest='a' * 64)[0]
    store.mark_published(op.operation_id)
    def full_disk(*args, **kwargs):
        raise OSError('intent release failed')
    monkeypatch.setattr(Path, 'replace', full_disk)
    with pytest.raises(OSError):
        store.clear_unqueued_intent(op.operation_id)
    assert store.is_published(op.operation_id)
    assert UpdateStore(store.path).is_published(op.operation_id)
    assert c.dispatch_ready(op.batch_id, lambda op: pytest.fail('undurable retry'), now=1001) == []


@pytest.mark.parametrize('content', [None, '{', '{}', '{"version":1,"operations":[{"state":"succeeded"}]}'])
def test_missing_corrupt_store_blocks_old_batch(tmp_path, content):
    from update_store import UpdateStore
    from update_coordinator import Coordinator
    path = tmp_path / 'updates.json'
    if content is not None:
        path.write_text(content)
    store = UpdateStore(path)
    assert not store.trusted
    c = Coordinator(store)
    assert c.batch_state('old-batch') == 'unknown'
    assert c.dispatch_ready('old-batch', lambda op: pytest.fail('corrupt rollout'), now=1000) == []


def test_atomic_failure_keeps_previous_state(tmp_path, monkeypatch):
    from update_coordinator import UpdateOperation
    from update_store import UpdateStore
    from pathlib import Path
    store = UpdateStore(tmp_path / 'updates.json')
    first = UpdateOperation('op1', 'batch1', 'mac1', 'v4.2.0', 'macos', 'queued', 1000, '', manifest_digest='a' * 64)
    store.save(first)
    def fail(*args, **kwargs):
        raise OSError('disk full')
    monkeypatch.setattr(Path, 'replace', fail)
    with pytest.raises(OSError):
        store.save(UpdateOperation('op1', 'batch1', 'mac1', 'v4.2.0', 'macos', 'downloading', 1001, '', manifest_digest='a' * 64))
    assert store.get('op1').state == 'queued'
    assert UpdateStore(store.path).get('op1').state == 'queued'


@pytest.mark.parametrize('change', ['delete', 'corrupt'])
def test_journal_loss_after_load_blocks_rollout_without_recreating_it(tmp_path, change):
    from update_coordinator import Coordinator
    from update_store import UpdateStore
    path = tmp_path / 'updates.json'
    c = Coordinator(UpdateStore(path))
    ops = c.start_batch(['mac1', 'mac2'], 'v4.2.0', 'macos', manifest_digest='a' * 64, now=1000)
    c.dispatch_ready(ops[0].batch_id, lambda op: True, now=1000)
    c.reconcile('mac1', {'operation_id': ops[0].operation_id, 'release_tag': 'v4.2.0', 'component': 'macos', 'manifest_digest': ops[0].manifest_digest, 'update_state': 'succeeded', 'updated_at': 1010}, received_at=1010, heartbeat_at=1010)
    if change == 'delete':
        path.unlink()
    else:
        path.write_text('{')
    assert c.batch_state(ops[0].batch_id) == 'unknown'
    assert c.dispatch_ready(ops[0].batch_id, lambda op: pytest.fail('lost journal rollout'), now=1011) == []
    assert not c.store.trusted


def test_save_cannot_rebind_existing_operation_or_store_unbounded_tag(tmp_path):
    from dataclasses import replace
    from update_coordinator import UpdateOperation
    from update_store import UpdateStore
    store = UpdateStore(tmp_path / 'updates.json')
    op = UpdateOperation('op1', 'batch1', 'mac1', 'v4.2.0', 'macos', 'queued', 1000, '', manifest_digest='a' * 64)
    store.save(op)
    with pytest.raises(ValueError):
        store.save(replace(op, device_id='other'))
    with pytest.raises(ValueError):
        store.save(replace(op, operation_id='other', release_tag='v' + '1' * 1000 + '.2.0'))
    assert store.get('op1').device_id == 'mac1'
