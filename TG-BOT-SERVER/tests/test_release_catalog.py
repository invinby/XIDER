"""Signed catalogue boundaries; all publisher keys here are synthetic."""
import copy
import dataclasses
import sys
import time
import asyncio
from types import SimpleNamespace
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.append(str(Path(__file__).resolve().parents[2] / "XGENT-MCS"))
import release_signature
import release_catalog as ledger


@pytest.fixture
def signed_release(monkeypatch):
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    monkeypatch.setattr(release_signature, "TRUSTED_RELEASE_KEYS", {release_signature.release_key_id(public): public})
    names = {"XGENT-WDS.exe", "XIDER-source.zip", "release-manifest.json"}
    manifest = release_signature.sign_manifest({"schema": 1, "release": "v4.1.0", "assets": [
        {"name": "XGENT-WDS.exe", "component": "windows_agent", "platform": "windows", "size": 3, "sha256": "a" * 64},
        {"name": "XIDER-source.zip", "component": "source", "platform": "all", "size": 4, "sha256": "b" * 64},
    ]}, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    release = ledger.Release("v4.1.0", (4, 1, 0), "Prior", "2026-10-01", "Rollback notes", frozenset(names))
    return release, manifest


def test_valid_signed_manifest_and_checksum_inventory(signed_release):
    release, manifest = signed_release
    assert ledger.verify_release_metadata(release, manifest)


@pytest.mark.parametrize("mutation", ["hash", "signature", "missing", "tag", "platform", "component"])
def test_invalid_signed_catalogue_fails_closed(signed_release, mutation):
    release, original = signed_release
    manifest = copy.deepcopy(original)
    if mutation == "hash":
        manifest["assets"][0]["sha256"] = "c" * 64
    elif mutation == "signature":
        manifest["signature"]["value"] = "A" * 88
    elif mutation == "missing":
        release = dataclasses.replace(release, asset_names=frozenset({"release-manifest.json"}))
    elif mutation == "tag":
        release = dataclasses.replace(release, tag="v4.2.0")
    else:
        manifest["assets"][0][mutation] = "macos" if mutation == "platform" else "mac_agent"
    assert not ledger.verify_release_metadata(release, manifest)


def test_prereleases_never_displace_stable_order():
    releases = ledger.parse_releases([
        {"tag_name": "v5.0.0", "prerelease": True},
        {"tag_name": "v4.2.0-rc.1"},
        {"tag_name": "v4.1.9"}, {"tag_name": "v4.1.10"},
    ])
    assert [release.tag for release in releases] == ["v4.1.10", "v4.1.9"]


def test_unsigned_catalogue_cannot_offer_install():
    import bot
    release = ledger.Release("v4.1.0", (4, 1, 0), "", "", "", frozenset({"XGENT-WDS.exe", "XIDER-source.zip", "release-manifest.json"}))
    info = {"os": "Windows", "version": "4.1.0", "update_mode": "source", "release_update_ready": True, "update_manifest_binding_protocol": 1, "online": True, "last_seen": time.time()}
    assert bot._version_install_block_reason("agent", release, "windows_agent", info) is not None


@pytest.mark.parametrize('capability', [None, 0, True, '1'])
def test_old_agent_without_manifest_binding_fails_closed(signed_catalog_release, capability):
    import bot
    release = signed_catalog_release(ledger.Release('v4.2.0', (4, 2, 0), '', '', '', frozenset({'XGENT-WDS.exe', 'XIDER-source.zip', 'release-manifest.json'})))
    info = {'os': 'Windows', 'version': '4.2.0', 'update_mode': 'source', 'release_update_ready': True,
            'online': True, 'last_seen': time.time(), 'update_manifest_binding_protocol': capability}
    assert bot._version_install_block_reason('agent', release, 'windows_agent', info) == 'versions_install_requires_agent'


def test_explicit_version_state_keeps_component_versions_separate(signed_release):
    release, manifest = signed_release
    state = ledger.version_state("?", release, "mac_agent", manifest)
    assert state["installed"] is None
    assert state["available"] is None
    assert state["unsupported"] is True
    assert state["unknown"] is True
    assert state["signature"] == "verified"
    assert state["os"] == "macos"
    assert state["published_at"] == "2026-10-01"
    assert state["changelog"] == "Rollback notes"


def test_available_version_requires_package_inside_signed_inventory(signed_catalog_release):
    source = ledger.Release("v4.1.0", (4, 1, 0), "", "", "", frozenset({"XIDER-source.zip", "release-manifest.json"}))
    signed = signed_catalog_release(source)
    advertised = dataclasses.replace(signed, asset_names=signed.asset_names | {"XGENT-WDS.exe"})
    assert ledger.version_state("4.0.1", advertised, "windows_agent")["available"] is None


@pytest.mark.parametrize("change", ["none", "unknown", "os", "signature", "role", "grant", "display", "expires_during_refresh"])
def test_rollback_confirmation_revalidates_exact_signed_selection(monkeypatch, signed_release, change):
    import bot
    release, manifest = signed_release
    # An older signed tag is intentionally selected, even with a newer release present.
    session = bot.SessionRegistry()
    session["target"] = "pc"
    monkeypatch.setattr(bot, "SESSION", session)
    info = {"os": "Windows", "version": "4.2.0", "update_mode": "source", "release_update_ready": True, "update_manifest_binding_protocol": 1, "last_seen": time.time(), "online": True}
    monkeypatch.setattr(bot, "devices", SimpleNamespace(get=lambda target: info))
    release = dataclasses.replace(release, manifest=manifest)
    newer = dataclasses.replace(release, tag="v4.2.0", version=(4, 2, 0))
    available = [newer, release]
    monkeypatch.setattr(ledger.catalog, "list", lambda **kwargs: available)
    monkeypatch.setattr(bot, "get_user_role", lambda user: bot.Role.OWNER)
    monkeypatch.setattr(bot, "_simple_command_still_allowed", lambda *args: True)
    monkeypatch.setattr(bot, "_PENDING_VERSION_INSTALLS", {})
    monkeypatch.setattr(bot, "_DISPLAYED_VERSION_RELEASES", {}, raising=False)
    cards, commands = [], []
    async def replace(cq, text, reply_markup=None):
        cards.append((text, reply_markup))
    async def dispatch(cq, action, emoji, label, **kwargs):
        commands.append((action, kwargs))
    monkeypatch.setattr(bot, "_replace_callback_message", replace)
    monkeypatch.setattr(bot, "simple_command", dispatch)
    class Callback:
        from_user = SimpleNamespace(id=bot.ADMIN_ID)
        def __init__(self, data):
            self.data = data
        async def answer(self, *args, **kwargs):
            pass
    asyncio.run(bot.on_versions_detail(Callback("versions:detail:agent:v4.1.0")))
    assert "v4.1.0" in cards[-1][0]
    if change == "display":
        bot._DISPLAYED_VERSION_RELEASES.clear()
    asyncio.run(bot.on_versions_install_select(Callback("versions:install:agent:v4.1.0")))
    if change == "unknown":
        available[:] = [newer]
    elif change == "os":
        info["os"] = "macOS"
    elif change == "signature":
        available[-1] = dataclasses.replace(release, manifest={})
    elif change == "role":
        monkeypatch.setattr(bot, "get_user_role", lambda user: bot.Role.GUEST)
    elif change == "grant":
        monkeypatch.setattr(bot, "_simple_command_still_allowed", lambda *args: False)
    elif change == "expires_during_refresh":
        clock = [time.time()]
        monkeypatch.setattr(bot.time, "time", lambda: clock[0])
        def delayed_refresh(**kwargs):
            clock[0] += 181
            info["last_seen"] = clock[0]
            return available
        monkeypatch.setattr(ledger.catalog, "list", delayed_refresh)
    asyncio.run(bot.on_versions_install_confirm(Callback("versions:install_confirm:v4.1.0")))
    if change == "none":
        assert commands == [("agent_update", {"timeout": 110.0, "update": True, "release_tag": "v4.1.0", "update_release": release})]
        assert info["version"] == "4.2.0"
    else:
        assert commands == []
    if change == "expires_during_refresh":
        assert bot.ADMIN_ID not in bot._PENDING_VERSION_INSTALLS


@pytest.mark.parametrize("trust", ["unsigned", "tampered"])
def test_release_detail_never_announces_unverified_package(monkeypatch, signed_release, trust):
    import bot
    release, manifest = signed_release
    if trust == "unsigned":
        manifest = None
    else:
        manifest = copy.deepcopy(manifest)
        manifest["assets"][0]["sha256"] = "c" * 64
    release = dataclasses.replace(release, manifest=manifest)
    session = bot.SessionRegistry()
    session["target"] = "pc"
    monkeypatch.setattr(bot, "SESSION", session)
    info = {"os": "Windows", "version": "4.2.0", "update_mode": "source", "release_update_ready": True, "update_manifest_binding_protocol": 1, "last_seen": time.time(), "online": True}
    monkeypatch.setattr(bot, "devices", SimpleNamespace(get=lambda target: info))
    monkeypatch.setattr(ledger.catalog, "list", lambda: [release])
    cards = []
    async def replace(cq, text, reply_markup=None):
        cards.append((text, reply_markup))
    monkeypatch.setattr(bot, "_replace_callback_message", replace)
    class Callback:
        from_user = SimpleNamespace(id=bot.ADMIN_ID)
        data = "versions:detail:agent:v4.1.0"
        async def answer(self, *args, **kwargs):
            pass
    asyncio.run(bot.on_versions_detail(Callback()))
    text, keyboard = cards[-1]
    assert bot._lex("versions_package_found") not in text
    assert bot._lex("versions_package_missing") in text
    assert "Доступная версия: unknown" in text
    assert not any(button.callback_data.startswith("versions:install:") for row in keyboard.inline_keyboard for button in row)
