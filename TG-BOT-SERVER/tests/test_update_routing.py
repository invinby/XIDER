import asyncio
from types import SimpleNamespace

import pytest
import bot
from update_coordinator import Coordinator
from update_store import UpdateStore
from transport import MQTTTransport


@pytest.fixture
def coordinator(monkeypatch, tmp_path):
    c = Coordinator(UpdateStore(tmp_path / 'updates.json'))
    monkeypatch.setattr(bot, 'update_coordinator', c, raising=False)
    monkeypatch.setattr(bot.time, 'time', lambda: 1010)
    monkeypatch.setattr(bot, 'verify_message_detailed', lambda payload: (payload, 'ok'))
    monkeypatch.setattr(bot, 'ENCRYPT_PAYLOAD', False)
    monkeypatch.setattr(bot.bot_settings, 'is_blocked', lambda device: False)
    state = {'mac1': {'os': 'macOS 27', 'online': True, 'last_seen': 1010}}
    monkeypatch.setattr(bot, 'devices', SimpleNamespace(get=lambda device: state.get(device), all=lambda: state,
                     upsert=lambda device, info: state.setdefault(device, {}).update(info)))
    op = c.start_batch(['mac1'], 'v4.2.0', 'mac_agent', manifest_digest='a' * 64, now=1000)[0]
    c.dispatch_ready(op.batch_id, lambda op: True, now=1000)
    return c, op


def test_transport_subscribes_to_reliable_update_progress():
    subscriptions = []
    client = SimpleNamespace(subscribe=lambda topic, qos: subscriptions.append((topic, qos)))
    transport = MQTTTransport(lambda topic, data: None)
    transport._on_connect(client, None, None, 0)
    assert (f'{bot.MQTT_PREFIX}/+/update_progress', 1) in subscriptions


def test_real_paho_disconnect_after_precheck_is_queued_and_ambiguous():
    transport = MQTTTransport(lambda topic, data: None)
    # Real installed Paho client, no socket or network thread: simulate losing
    # the socket after the transport's connected precheck.
    transport.connected.set()
    assert transport.publish_command('mac1', 'agent_update', id='race-op') is None
    assert len(transport._client._out_messages) == 1
    assert next(iter(transport._client._out_messages.values())).qos == 1


def test_transport_disconnected_false_then_retry_same_operation(coordinator):
    c, _ = coordinator
    op = c.start_batch(['mac2'], 'v4.2.0', 'mac_agent', manifest_digest='a' * 64, now=1000)[0]
    transport = MQTTTransport(lambda topic, data: None)
    publish = lambda current: transport.publish_command(current.device_id, 'agent_update', id=current.operation_id)
    assert c.dispatch_ready(op.batch_id, publish, now=1000) == []
    assert not transport._client._out_messages
    assert not c.store.is_published(op.operation_id)
    transport.connected.set()
    sent = []
    transport._client = SimpleNamespace(publish=lambda topic, body, qos: sent.append(body) or SimpleNamespace(rc=0))
    assert c.dispatch_ready(op.batch_id, publish, now=1001) == [op]
    assert len(sent) == 1


def test_real_paho_queue_full_is_definitely_not_enqueued():
    transport = MQTTTransport(lambda topic, data: None)
    transport.connected.set()
    transport._client.max_queued_messages_set(1)
    assert transport.publish_command('mac1', 'agent_update', id='first-op') is None
    assert transport.publish_command('mac1', 'agent_update', id='refused-op') is False
    assert len(transport._client._out_messages) == 1


def test_authenticated_progress_routes_to_coordinator(coordinator):
    c, op = coordinator
    bot.on_mqtt_message(f'{bot.MQTT_PREFIX}/mac1/update_progress', {'type': 'agent_update_progress', 'device_id': 'mac1', 'operation_id': op.operation_id, 'update_state': 'downloading'})
    assert c.store.get(op.operation_id).state == 'downloading'


def test_final_update_failure_routes_to_operation_without_claiming_success(coordinator, monkeypatch):
    c, op = coordinator
    monkeypatch.setattr(bot.fun_text_collector, 'submit', lambda *args: None)
    monkeypatch.setattr(bot, '_schedule_lifecycle_failure_notice', lambda *args: None)
    bot.on_mqtt_message(f'{bot.MQTT_PREFIX}/mac1/output', {'type': 'agent_update', 'operation_id': op.operation_id, 'update_state': 'failed', 'ok': False})
    assert c.store.get(op.operation_id).state == 'failed'


@pytest.mark.parametrize('failure', ['signature', 'topic', 'device', 'component', 'blocked'])
def test_untrusted_progress_cannot_advance(coordinator, monkeypatch, failure):
    c, op = coordinator
    topic = f'{bot.MQTT_PREFIX}/mac1/update_progress'
    payload = {'type': 'agent_update_progress', 'device_id': 'mac1', 'operation_id': op.operation_id, 'update_state': 'downloading'}
    if failure == 'signature':
        monkeypatch.setattr(bot, 'verify_message_detailed', lambda payload: (None, 'bad'))
    elif failure == 'topic':
        topic = 'wrong/prefix/mac1/update_progress'
    elif failure == 'device':
        payload['device_id'] = 'mac2'
    elif failure == 'component':
        payload['component'] = 'windows_agent'
    elif failure == 'blocked':
        monkeypatch.setattr(bot.bot_settings, 'is_blocked', lambda device: True)
    bot.on_mqtt_message(topic, payload)
    assert c.store.get(op.operation_id).state == 'queued'


def test_matching_status_health_receipt_reconciles_update(coordinator, monkeypatch):
    c, op = coordinator
    monkeypatch.setattr(bot, 'audit', lambda *args, **kwargs: None)
    monkeypatch.setattr(bot.status_collector, 'submit', lambda *args: None)
    monkeypatch.setattr(bot.multi_status, 'submit', lambda *args: None)
    bot.on_mqtt_message(f'{bot.MQTT_PREFIX}/mac1/status', {'type': 'status', 'os': 'macOS 27', 'status': 'online', 'agent_update': {'operation_id': op.operation_id, 'release_tag': 'v4.2.0', 'manifest_digest': op.manifest_digest, 'update_state': 'succeeded', 'updated_at': 1010}})
    assert c.store.get(op.operation_id).state == 'succeeded'


def test_enqueue_uses_persisted_id_and_reports_unknown_without_receipt(coordinator, monkeypatch, signed_catalog_release):
    import release_catalog
    release = signed_catalog_release(release_catalog.Release('v4.2.0', (4, 2, 0), 'release', 'date', 'notes', frozenset({'XGENT-MCS-macos-bundle.zip', 'XIDER-source.zip', 'release-manifest.json'})))
    c, old = coordinator
    # A different device makes this independent of the routing fixture's active operation.
    monkeypatch.setattr(bot, 'SESSION', {'target': 'mac2'})
    monkeypatch.setattr(bot.devices, 'get', lambda device: {'os': 'macOS 27', 'online': True, 'last_seen': 1010, 'version': '4.2.0', 'update_mode': 'source', 'release_update_ready': True, 'update_manifest_binding_protocol': 1})
    monkeypatch.setattr(bot, '_simple_command_still_allowed', lambda *args: True)
    published = []
    def publish(action, **kwargs):
        op = c.store.get(kwargs['id'])
        assert op is not None and c.store.is_published(op.operation_id)
        published.append((action, kwargs))
        return True
    monkeypatch.setattr(bot, 'publish', publish)
    async def wait(operation_id, timeout, status_msg, user_id):
        return c.store.get(operation_id)
    monkeypatch.setattr(bot, '_wait_update_operation', wait, raising=False)
    rendered = []
    async def render(*args, **kwargs):
        rendered.append(args[1] if len(args) > 1 else '')
        return SimpleNamespace()
    monkeypatch.setattr(bot, '_replace_callback_message', render)
    monkeypatch.setattr(bot, '_replace_message_card', render)
    async def answer(*args, **kwargs):
        pass
    cq = SimpleNamespace(data='versions:install_confirm:v4.2.0', from_user=SimpleNamespace(id=1), answer=answer)
    result = asyncio.run(bot._simple_command_unlocked(cq, 'agent_update', '🔄', 'update', timeout=0, update=True, release_tag='v4.2.0', update_release=release))
    assert len(published) == 1
    assert result['update_state'] == 'queued'
    assert result['ok'] is False
    assert published[0][1]['release_tag'] == 'v4.2.0'
    asyncio.run(bot._simple_command_unlocked(cq, 'agent_update', '🔄', 'update', timeout=0, update=True, release_tag='v4.2.0', update_release=release))
    assert len(published) == 1


@pytest.mark.parametrize('outcome', [False, True, None, 'exception', 'already_published', 'already_succeeded'])
def test_coordinated_publish_unsent_returns_immediately_but_ambiguous_waits(coordinator, monkeypatch, signed_catalog_release, outcome):
    import release_catalog
    c, _ = coordinator
    release = signed_catalog_release(release_catalog.Release('v4.2.0', (4, 2, 0), 'release', 'date', 'notes', frozenset({'XGENT-MCS-macos-bundle.zip', 'XIDER-source.zip', 'release-manifest.json'})))
    monkeypatch.setattr(bot, 'SESSION', {'target': 'mac2'})
    monkeypatch.setattr(bot.devices, 'get', lambda device: {'os': 'macOS 27', 'online': True, 'last_seen': 1010, 'version': '4.2.0', 'update_mode': 'source', 'release_update_ready': True, 'update_manifest_binding_protocol': 1})
    monkeypatch.setattr(bot, '_simple_command_still_allowed', lambda *args: True)
    if outcome in {'already_published', 'already_succeeded'}:
        digest = bot.hashlib.sha256(release_catalog.manifest_signing_bytes(release.manifest)).hexdigest()
        old = c.start_batch(['mac2'], 'v4.2.0', 'mac_agent', now=1000, manifest_digest=digest)[0]
        c.store.mark_published(old.operation_id)
        if outcome == 'already_succeeded':
            assert c.reconcile('mac2', {'operation_id': old.operation_id, 'release_tag': old.release_tag,
                'component': old.component, 'manifest_digest': digest, 'update_state': 'succeeded', 'updated_at': 1010},
                received_at=1010, heartbeat_at=1010)
    def publish(*args, **kwargs):
        if outcome in {'already_published', 'already_succeeded'}:
            pytest.fail('existing operation must not be republished')
        if outcome == 'exception':
            raise RuntimeError('ambiguous transport exception')
        return outcome
    monkeypatch.setattr(bot, 'publish', publish)
    waited = []
    async def wait(operation_id, timeout, *args):
        if outcome is False:
            pytest.fail('definitely unsent update must not wait 110 seconds')
        waited.append((operation_id, timeout))
        return c.store.get(operation_id)
    monkeypatch.setattr(bot, '_wait_update_operation', wait)
    rendered = []
    async def render(*args, **kwargs):
        rendered.append(args[1])
        return SimpleNamespace()
    monkeypatch.setattr(bot, '_replace_callback_message', render)
    monkeypatch.setattr(bot, '_replace_message_card', render)
    async def answer(*args, **kwargs):
        pass
    cq = SimpleNamespace(data='versions:install_confirm:v4.2.0', from_user=SimpleNamespace(id=1), answer=answer)
    result = asyncio.run(bot._coordinated_agent_update(cq, 'update', 110, 'v4.2.0', update_release=release))
    if outcome is False:
        assert result['sent'] is False and result['retryable'] is True
        assert result['update_state'] == 'queued'
        assert 'не отправлена' in rendered[-1]
        assert not c.store.is_published(result['operation_id'])
        assert c.store.get(result['operation_id']).state == 'queued'
    else:
        assert len(waited) == 1 and waited[0][1] == 110
        assert c.store.is_published(result['operation_id'])
        expected = 'succeeded' if outcome == 'already_succeeded' else 'queued' if outcome is True or outcome == 'already_published' else 'unknown'
        assert result['update_state'] == expected
        assert result.get('sent') is not False


@pytest.mark.parametrize('healthy,changed_manifest,preflight', [(False, False, None), (True, False, None), (True, True, None), (True, False, 'offline'), (True, False, 'revoked')])
def test_owner_rollout_requires_fresh_canary_and_revalidates_signed_release(coordinator, monkeypatch, signed_catalog_release, healthy, changed_manifest, preflight):
    import release_catalog
    c, old = coordinator
    release = signed_catalog_release(release_catalog.Release('v4.2.0', (4, 2, 0), 'release', 'date', 'notes', frozenset({'XGENT-MCS-macos-bundle.zip', 'XIDER-source.zip', 'release-manifest.json'})))
    digest = bot.hashlib.sha256(release_catalog.manifest_signing_bytes(release.manifest)).hexdigest()
    ops = c.start_batch(['mac2', 'mac3'], 'v4.2.0', 'mac_agent', manifest_digest=digest, now=1000)
    if changed_manifest:
        from dataclasses import replace
        release = signed_catalog_release(replace(release, asset_names=release.asset_names | {'XGENT-WDS.exe'}))
        assert bot.release_catalog.verify_release_metadata(release, release.manifest)
    c.dispatch_ready(ops[0].batch_id, lambda op: True, now=1000)
    if healthy:
        assert c.reconcile('mac2', {'operation_id': ops[0].operation_id, 'release_tag': 'v4.2.0', 'component': 'mac_agent', 'manifest_digest': ops[0].manifest_digest, 'update_state': 'succeeded', 'updated_at': 1010}, received_at=1010, heartbeat_at=1010)
    info = {'os': 'macOS 27', 'version': '4.1.0', 'update_mode': 'source', 'release_update_ready': True, 'update_manifest_binding_protocol': 1, 'online': True, 'last_seen': 1010}
    monkeypatch.setattr(bot.devices, 'get', lambda device: info)
    monkeypatch.setattr(bot, 'get_user_role', lambda user: bot.Role.OWNER)
    monkeypatch.setattr(bot, '_simple_command_still_allowed', lambda *args: True)
    monkeypatch.setattr(bot.release_catalog.catalog, 'list', lambda **kwargs: [release])
    monkeypatch.setattr(bot, 'SESSION', {'target': 'mac2'})
    sent = []
    monkeypatch.setattr(bot, 'publish', lambda action, **kwargs: sent.append(kwargs) or True)
    async def render(*args, **kwargs):
        return None
    monkeypatch.setattr(bot, '_replace_callback_message', render)
    cq = SimpleNamespace(data=f'versions:rollout:{ops[0].batch_id}', from_user=SimpleNamespace(id=1), answer=render)
    if preflight:
        monkeypatch.setattr(bot.devices, 'get', lambda device: {**info, 'online': False} if device == 'mac3' and preflight == 'offline' else info)
        monkeypatch.setattr(bot, '_simple_command_still_allowed', lambda user, callback, device: not (device == 'mac3' and preflight == 'revoked'))
        asyncio.run(bot.on_versions_rollout(cq))
        assert not sent
        assert not c.store.is_published(ops[1].operation_id)
        assert c.store.get(ops[1].operation_id).state == 'queued'
        monkeypatch.setattr(bot.devices, 'get', lambda device: info)
        monkeypatch.setattr(bot, '_simple_command_still_allowed', lambda *args: True)
    asyncio.run(bot.on_versions_rollout(cq))
    assert [item['_target'] for item in sent] == (['mac3'] if healthy and not changed_manifest else [])
    asyncio.run(bot.on_versions_rollout(cq))
    assert len(sent) == (1 if healthy and not changed_manifest else 0)
    if sent:
        assert sent[0]['manifest_digest'] == digest


def test_device_component_change_during_telegram_ack_prevents_update(coordinator, monkeypatch, signed_catalog_release):
    import release_catalog
    c, op = coordinator
    monkeypatch.setattr(bot, 'SESSION', {'target': 'mac2'})
    info = {'os': 'macOS 27', 'version': '4.1.0', 'update_mode': 'source', 'release_update_ready': True, 'update_manifest_binding_protocol': 1, 'online': True, 'last_seen': 1010}
    monkeypatch.setattr(bot.devices, 'get', lambda device: info)
    monkeypatch.setattr(bot, '_simple_command_still_allowed', lambda *args: True)
    release = signed_catalog_release(release_catalog.Release('v4.2.0', (4, 2, 0), 'release', 'date', 'notes', frozenset({'XGENT-MCS-macos-bundle.zip', 'XIDER-source.zip', 'release-manifest.json'})))
    sent = []
    monkeypatch.setattr(bot, 'publish', lambda *args, **kwargs: sent.append(kwargs) or True)
    async def answer(*args, **kwargs):
        info['os'] = 'Windows 11'
    async def render(*args, **kwargs):
        return SimpleNamespace()
    async def wait(operation_id, *args):
        return c.store.get(operation_id)
    monkeypatch.setattr(bot, '_replace_callback_message', render)
    monkeypatch.setattr(bot, '_replace_message_card', render)
    monkeypatch.setattr(bot, '_wait_update_operation', wait)
    cq = SimpleNamespace(data='versions:install_confirm:v4.2.0', from_user=SimpleNamespace(id=1), answer=answer)
    asyncio.run(bot._simple_command_unlocked(cq, 'agent_update', '🔄', 'update', timeout=0, release_tag='v4.2.0', update_release=release))
    assert not sent
