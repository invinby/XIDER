import importlib
from dataclasses import replace

import pytest


def api():
    module = importlib.import_module('update_coordinator')
    return module


def operation(state='queued'):
    return api().UpdateOperation('operation1', 'batch1', 'mac1', 'v4.2.0', 'macos', state, 1000, '')


def test_full_lifecycle_and_duplicates():
    module = api()
    current = operation()
    for index, stage in enumerate(('downloading', 'verifying', 'installing', 'reconnecting', 'health_check', 'succeeded')):
        current = module.transition(current, stage, 1001 + index)
        assert current.state == stage
        assert module.transition(current, stage, 2000) == current
    assert module.transition(current, 'failed', 2010) == current


@pytest.mark.parametrize('state,event,want', [('installing', 'rolling_back', 'rolling_back'), ('rolling_back', 'rolled_back', 'rolled_back'), ('verifying', 'failed', 'failed'), ('queued', 'succeeded', 'queued'), ('queued', 'garbage', 'queued'), ('installing', 'timeout', 'unknown'), ('unknown', 'succeeded', 'unknown')])
def test_invalid_rollback_and_timeout_transitions(state, event, want):
    assert api().transition(operation(state), event, 1001).state == want


def setup(tmp_path):
    from update_store import UpdateStore
    module = api()
    return module.Coordinator(UpdateStore(tmp_path / 'updates.json'))


def receipt(op, **changes):
    return {'operation_id': op.operation_id, 'release_tag': op.release_tag, 'component': op.component,
            'update_state': 'succeeded', 'updated_at': 1010, **changes}


def test_canary_is_only_publish_until_fresh_health_then_batch(tmp_path):
    c = setup(tmp_path)
    ops = c.start_batch(['mac1', 'mac2'], 'v4.2.0', 'macos', now=1000)
    sent = []
    publish = lambda op: sent.append(op.device_id) or True
    c.dispatch_ready(ops[0].batch_id, publish, now=1000)
    c.dispatch_ready(ops[0].batch_id, publish, now=1001)
    assert sent == ['mac1']
    assert c.store.get(ops[0].operation_id).state == 'queued'
    assert not c.reconcile('mac1', receipt(ops[0]), received_at=1010, heartbeat_at=800)
    c.dispatch_ready(ops[0].batch_id, publish, now=1010)
    assert sent == ['mac1']
    assert c.reconcile('mac1', receipt(ops[0]), received_at=1010, heartbeat_at=1010)
    c.dispatch_ready(ops[0].batch_id, publish, now=1011)
    c.dispatch_ready(ops[0].batch_id, publish, now=1012)
    assert sent == ['mac1', 'mac2']


@pytest.mark.parametrize('change', [{'operation_id': 'other'}, {'release_tag': 'v4.1.0'}, {'component': 'windows'}, {'device_id': 'other'}, {'updated_at': 800}, {'updated_at': 1100}, {'update_state': 'queued'}])
def test_mismatched_or_stale_receipt_never_unlocks(tmp_path, change):
    c = setup(tmp_path)
    op = c.start_batch(['mac1', 'mac2'], 'v4.2.0', 'macos', now=1000)[0]
    c.dispatch_ready(op.batch_id, lambda op: True, now=1000)
    assert not c.reconcile('mac1', receipt(op, **change), received_at=1010, heartbeat_at=1010)
    assert c.dispatch_ready(op.batch_id, lambda op: pytest.fail('mass rollout'), now=1011) == []


def test_restart_requires_new_matching_receipt(tmp_path):
    c = setup(tmp_path)
    ops = c.start_batch(['mac1', 'mac2'], 'v4.2.0', 'macos', now=1000)
    c.dispatch_ready(ops[0].batch_id, lambda op: True, now=1000)
    c.reconcile('mac1', receipt(ops[0]), received_at=1010, heartbeat_at=1010)
    restarted = setup(tmp_path)
    assert restarted.dispatch_ready(ops[0].batch_id, lambda op: pytest.fail('unconfirmed canary'), now=1011) == []
    assert restarted.reconcile('mac1', receipt(ops[0]), received_at=1011, heartbeat_at=1011)
    sent = []
    restarted.dispatch_ready(ops[0].batch_id, lambda op: sent.append(op.device_id) or True, now=1012)
    assert sent == ['mac2']


def test_unknown_reconciles_only_bound_fresh_receipt(tmp_path):
    c = setup(tmp_path)
    op = c.start_batch(['mac1'], 'v4.2.0', 'macos', now=1000)[0]
    c.dispatch_ready(op.batch_id, lambda op: True, now=1000)
    c.expire(now=1400)
    assert c.store.get(op.operation_id).state == 'unknown'
    assert not c.reconcile('mac2', receipt(op, updated_at=1401), received_at=1401, heartbeat_at=1401)
    assert c.reconcile('mac1', receipt(op, updated_at=1401), received_at=1401, heartbeat_at=1401)
    assert c.store.get(op.operation_id).state == 'succeeded'


def test_canary_failure_blocks_rest_and_repeat_start_reuses_operation(tmp_path):
    c = setup(tmp_path)
    ops = c.start_batch(['mac1', 'mac2'], 'v4.2.0', 'macos', now=1000)
    assert c.start_batch(['mac1', 'mac2'], 'v4.2.0', 'macos', now=1001) == ops
    c.dispatch_ready(ops[0].batch_id, lambda op: True, now=1000)
    assert c.handle_event('mac1', {'operation_id': ops[0].operation_id, 'update_state': 'failed'}, received_at=1001)
    assert c.dispatch_ready(ops[0].batch_id, lambda op: pytest.fail('failed canary rollout'), now=1002) == []


def test_progress_binding_rejects_wrong_device_tag_and_component(tmp_path):
    c = setup(tmp_path)
    op = c.start_batch(['mac1'], 'v4.2.0', 'macos', now=1000)[0]
    c.dispatch_ready(op.batch_id, lambda op: True, now=1000)
    for changes in ({'device_id': 'other'}, {'release_tag': 'v4.1.0'}, {'component': 'windows'}):
        assert not c.handle_event('mac1', {'operation_id': op.operation_id, 'update_state': 'downloading', **changes}, received_at=1001)
    assert c.handle_event('mac1', {'operation_id': op.operation_id, 'update_state': 'downloading'}, received_at=1001)
    assert c.store.get(op.operation_id).state == 'downloading'


def test_receipt_predating_dispatch_and_success_after_offline_cannot_unlock(tmp_path):
    c = setup(tmp_path)
    op = c.start_batch(['mac1', 'mac2'], 'v4.2.0', 'macos', now=1000)[0]
    c.dispatch_ready(op.batch_id, lambda op: True, now=1000)
    assert not c.reconcile('mac1', receipt(op, updated_at=999), received_at=1010, heartbeat_at=1010)
    assert c.reconcile('mac1', receipt(op), received_at=1010, heartbeat_at=1010)
    c.heartbeat_lost('mac1', now=1011)
    assert c.dispatch_ready(op.batch_id, lambda op: pytest.fail('offline canary'), now=1012) == []


def test_single_timeout_does_not_expire_another_device(tmp_path):
    c = setup(tmp_path)
    first = c.start_batch(['mac1'], 'v4.2.0', 'macos', now=1000)[0]
    second = c.start_batch(['mac2'], 'v4.2.0', 'macos', now=1000)[0]
    c.dispatch_ready(first.batch_id, lambda op: True, now=1000)
    c.dispatch_ready(second.batch_id, lambda op: True, now=1000)
    c.timeout(first.operation_id, now=1010)
    assert c.store.get(first.operation_id).state == 'unknown'
    assert c.store.get(second.operation_id).state == 'queued'


def test_restart_cannot_report_persisted_success_as_current_and_repeat_reuses_id(tmp_path):
    c = setup(tmp_path)
    op = c.start_batch(['mac1'], 'v4.2.0', 'macos', now=1000)[0]
    c.dispatch_ready(op.batch_id, lambda op: True, now=1000)
    c.reconcile('mac1', receipt(op), received_at=1010, heartbeat_at=1010)
    assert c.start_batch(['mac1'], 'v4.2.0', 'macos', now=1011)[0].operation_id == op.operation_id
    restarted = setup(tmp_path)
    assert restarted.observed(op.operation_id, now=1011).state == 'unknown'
    assert restarted.batch_state(op.batch_id) == 'unknown'
    assert restarted.reconcile('mac1', receipt(op), received_at=1012, heartbeat_at=1012)
    assert restarted.observed(op.operation_id, now=1012).state == 'succeeded'
