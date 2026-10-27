import os

import pytest

from broker.x_dock_config import render_config, write_config


def _config(**overrides):
    values = {
        "plugin_path": "/usr/lib/mosquitto_dynamic_security.so",
        "security_file": "/var/lib/xider-dock/dynamic-security.json",
        "data_dir": "/var/lib/xider-dock/data",
        "cert_file": "/etc/xider/dock/bootstrap.crt",
        "key_file": "/etc/xider/dock/bootstrap.key",
        "port": 18884,
        "bind_address": "127.0.0.1",
        "password_init_file": "/etc/xider/dock/admin-password",
    }
    values.update(overrides)
    return render_config(**values)


def test_bootstrap_config_is_loopback_tls_dynamic_security_and_nonanonymous():
    text = _config()

    assert "allow_anonymous false\n" in text
    assert "listener 18884 127.0.0.1\n" in text
    assert "plugin /usr/lib/mosquitto_dynamic_security.so\n" in text
    assert "plugin_opt_config_file /var/lib/xider-dock/dynamic-security.json\n" in text
    assert "password_file " not in text
    assert "acl_file " not in text


def test_public_config_does_not_bind_to_a_specific_interface():
    text = _config(bind_address=None, password_init_file=None, port=8884)

    assert "listener 8884\n" in text
    assert "listener 8884 127.0.0.1\n" not in text
    assert "plugin_opt_password_init_file" not in text


@pytest.mark.parametrize("port", [0, -1, 65536, True])
def test_rejects_invalid_port(port):
    with pytest.raises(ValueError, match="port"):
        _config(port=port)


@pytest.mark.parametrize("address", ["0.0.0.0", "192.0.2.1", "localhost"])
def test_bootstrap_rejects_non_loopback_bind(address):
    with pytest.raises(ValueError, match="loopback"):
        _config(bind_address=address)


def test_rejects_relative_or_injected_paths():
    with pytest.raises(ValueError, match="absolute"):
        _config(plugin_path="relative/plugin.so")
    with pytest.raises(ValueError, match="invalid character"):
        _config(plugin_path="/tmp/plugin.so\nallow_anonymous true")


def test_write_config_replaces_atomically_and_sets_private_mode(tmp_path):
    path = tmp_path / "mosquitto.conf"
    write_config(path, "allow_anonymous false\n")

    assert path.read_text(encoding="utf-8") == "allow_anonymous false\n"
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o640
    assert list(tmp_path.glob(".mosquitto.conf.*.tmp")) == []


def test_write_config_refuses_symlink(tmp_path):
    target = tmp_path / "target"
    target.write_text("keep", encoding="utf-8")
    link = tmp_path / "mosquitto.conf"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation unavailable")

    with pytest.raises(RuntimeError, match="symlink"):
        write_config(link, "allow_anonymous true\n")
    assert target.read_text(encoding="utf-8") == "keep"
